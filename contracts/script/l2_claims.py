"""
Claims L1 to L2 messages as wallets do: from L1 data and the L2 RPC alone,
each claim signed with its claimer's own key.

A claim is an EIP-8141 frame transaction from the claimer:

    frame 0  DEFAULT  L2Messenger.proveL1MessageRoot(...)  when the messenger has no root with the message
    frame 1  DEFAULT  L2Messenger.claimL1Message(message, path, feeRecipient = claimer)
    frame 2  VERIFY   the claimer approves execution and payment

The first frame proves the root of the rollup contract's message tree
against the L1 anchor of the latest L2 block, which the EIP-4788 contract
keeps on L2, so a wallet builds the claim between blocks. The claim frame
pays the message's fee to the claimer, and a message to the claimer also
its value, before the VERIFY frame approves payment: a claim of a message to
the claimer pays for itself, and the claimer needs no funds. If the claim
fails, the claimer cannot pay, so the transaction is invalid and costs it
nothing. The claim frame has room for the message's gas limit, which the
message's call gets in both gas dimensions.

Each wallet claims the messages to itself. The relayer, if any, claims the
messages to every other address, such as contracts, whose fee covers the
claim's cost. Their calls may run code that anyone can change, so the L2
node only takes those claims from a claimer that can pay up front: the
relayer funds itself with a message to itself, which it claims as a wallet.

    uv run --project <execution-specs> python script/l2_claims.py --l1-rpc <url> --rollup <address> \
        --l2-rpc <url> --wallet <key> [--wallet <key> ...] [--relayer <key>] [--cache <path>]

`--cache` keeps what stays true across runs in a file: the messages, their
leaves and the tree's roots up to `CACHE_MARGIN` L1 blocks below the anchor,
and the claimed bits seen set, so a run only reads and hashes what is new.

Run with the environment of the execution-specs merge the L2 node uses,
which signs the frame transactions with EEST.
"""

import argparse
import json
import os
import subprocess

from execution_testing import EOA, Frame, Transaction
from execution_testing import Address as TestAddress

from ethereum.crypto.hash import keccak256

from l2_node import L2_CHAIN_ID, L2_MESSENGER, PRIORITY_FEE, get_logs, hx, json_rpc

PROVE_ROOT_SIGNATURE = "proveL1MessageRoot(uint256,bytes,bytes[],bytes[])"
CLAIM_SIGNATURE = "claimL1Message((address,address,uint256,uint256,uint256,bytes,uint256),bytes32[],address)"
CLAIM_GAS_LIMIT = 1_000_000
# EIP-8141 frame modes and approval scopes.
DEFAULT_MODE, VERIFY_MODE = 0, 1
APPROVE_EXECUTION_AND_PAYMENT = 3
CLAIM_STATE_GAS_LIMIT = 400_000
# What the message's call costs the messenger before it runs, as
# `Messages.CALL_OVERHEAD`.
CALL_OVERHEAD = 40_000
L1_MESSAGE_SENT = keccak256(b"L1MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")


def claim_gas(m: dict) -> tuple:
    """A claim frame's execution and state gas limits, with room for the
    message's call to get its gas limit in both (`Messages.deliver`)."""
    return (CLAIM_GAS_LIMIT + m["gasLimit"] * 64 // 63 + CALL_OVERHEAD, CLAIM_STATE_GAS_LIMIT + m["gasLimit"])


def max_claim_gas(m: dict) -> int:
    """A bound on a claim's gas: the frames' limits, their intrinsic cost,
    and the signature's. At the price per gas it is the claim's `max_fee`
    budget, the most the claim can cost."""
    return CLAIM_GAS_LIMIT + CLAIM_STATE_GAS_LIMIT + sum(claim_gas(m)) + 100_000 + 400_000 + 100_000
L1_MESSAGE_ROOT_SLOT = 3  # root of NativeRollup.l1Messages
CLAIMED_SLOT = 1  # L2Messenger.claimedBits, 256 flags per slot
PROVEN_ROOT_SLOT = 4  # L2Messenger.provenL1MessageRoot
TREE_DEPTH = 32
# L1 blocks below the anchor that a message must be for the cache to keep it.
CACHE_MARGIN = 64
ZERO_HASHES = [bytes(32)]
for _ in range(TREE_DEPTH):
    ZERO_HASHES.append(keccak256(ZERO_HASHES[-1] * 2))


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True, timeout=180).stdout.strip()


def message_tree(leaves: list) -> tuple:
    """Root of the message tree holding `leaves`, and each leaf's path, as
    `MessageTree` computes them."""
    level, positions = list(leaves), list(range(len(leaves)))
    paths = [[] for _ in leaves]
    for height in range(TREE_DEPTH):
        for path, position in zip(paths, positions):
            sibling = position ^ 1
            path.append(level[sibling] if sibling < len(level) else ZERO_HASHES[height])
        level = [
            keccak256(level[i] + (level[i + 1] if i + 1 < len(level) else ZERO_HASHES[height]))
            for i in range(0, len(level), 2)
        ]
        positions = [position >> 1 for position in positions]
    return (level[0] if level else ZERO_HASHES[TREE_DEPTH]), paths


def extend_roots(branch: list, count: int, leaves: list) -> list:
    """The tree's root after each of `leaves`, inserted into a tree of
    `count` messages with the frontier `branch`, as `MessageTree.insert`
    keeps it. Updates `branch`."""
    roots = []
    for count, leaf in enumerate(leaves, count + 1):
        node, size = leaf, count
        for height in range(TREE_DEPTH):
            if size & 1:
                branch[height] = node
                break
            node = keccak256(branch[height] + node)
            size >>= 1
        node, size = bytes(32), count
        for height in range(TREE_DEPTH):
            node = keccak256(branch[height] + node) if size & 1 else keccak256(node + ZERO_HASHES[height])
            size >>= 1
        roots.append(node)
    return roots


def l1_messages(l1_rpc: str, rollup: str, anchor: int, first_block: int = 0, first_index: int = 0) -> list:
    """The messages the rollup contract sent from `first_block` through the
    anchor block, the first of them with index `first_index`."""
    logs = get_logs(l1_rpc, {"address": rollup, "topics": [hx(L1_MESSAGE_SENT)]}, first_block, anchor)
    messages = []
    for log in logs:
        data = bytes.fromhex(log["data"][2:])
        offset = int.from_bytes(data[96:128], "big")
        length = int.from_bytes(data[offset : offset + 32], "big")
        messages.append({
            "index": int(log["topics"][1], 16),
            "sender": "0x" + log["topics"][2][-40:],
            "to": "0x" + log["topics"][3][-40:],
            "value": int.from_bytes(data[0:32], "big"),
            "fee": int.from_bytes(data[32:64], "big"),
            "gasLimit": int.from_bytes(data[64:96], "big"),
            "data": "0x" + data[offset + 32 : offset + 32 + length].hex(),
            "block": int(log["blockNumber"], 16),
        })
    assert [m["index"] for m in messages] == list(range(first_index, first_index + len(messages))), "missing L1 messages"
    return messages


class MessageCache:
    """The messages the rollup contract had sent by an anchor block, with
    their leaves and the tree's root after each, from the cache file and the
    logs after it, and the claimed bits seen set. The file keeps only
    messages `CACHE_MARGIN` blocks below the anchor, which an L1 reorg
    cannot change, and claimed bits, which never clear."""

    def __init__(self, path: str | None, l1_rpc: str, rollup: str, anchor: int):
        self.path, self.rollup = path, rollup
        stored = json.load(open(path)) if path and os.path.exists(path) else None
        if stored is None or stored["rollup"] != rollup or stored["through"] > anchor:
            stored = {"through": -1, "messages": [], "leaves": [], "roots": [], "branch": [hx(bytes(32))] * TREE_DEPTH, "claimed": {}}
        self.claimed_words = {int(i): int(w, 16) for i, w in stored["claimed"].items()}
        cached = stored["messages"]
        new = l1_messages(l1_rpc, rollup, anchor, stored["through"] + 1, len(cached))
        self.messages = cached + new
        self.leaves = [bytes.fromhex(x[2:]) for x in stored["leaves"]] + [leaf(m) for m in new]
        self.roots = [bytes.fromhex(x[2:]) for x in stored["roots"]]
        branch = [bytes.fromhex(x[2:]) for x in stored["branch"]]
        # The new messages that the file can keep, then the others.
        self.through = max(stored["through"], anchor - CACHE_MARGIN)
        settled = len(cached) + next((i for i, m in enumerate(new) if m["block"] > self.through), len(new))
        self.roots += extend_roots(branch, len(cached), self.leaves[len(cached) : settled])
        self.settled, self.branch = settled, list(branch)
        self.roots += extend_roots(branch, settled, self.leaves[settled:])

    def claimed(self, index: int, word) -> bool:
        """Whether message `index` is claimed, reading its word of claimed
        bits with `word` unless the bit was already seen set."""
        i = index >> 8
        if not self.claimed_words.get(i, 0) >> (index & 0xFF) & 1:
            self.claimed_words[i] = word(i)
        return bool(self.claimed_words[i] >> (index & 0xFF) & 1)

    def save(self) -> None:
        if not self.path:
            return
        n = self.settled
        stored = {
            "rollup": self.rollup, "through": self.through, "messages": self.messages[:n],
            "leaves": [hx(x) for x in self.leaves[:n]], "roots": [hx(x) for x in self.roots[:n]],
            "branch": [hx(x) for x in self.branch], "claimed": {str(i): hex(w) for i, w in self.claimed_words.items()},
        }
        with open(self.path + ".tmp", "w") as f:
            json.dump(stored, f)
        os.replace(self.path + ".tmp", self.path)


def leaf(m: dict) -> bytes:
    return keccak256(
        bytes.fromhex(m["sender"][2:]) + bytes.fromhex(m["to"][2:]) + m["value"].to_bytes(32, "big")
        + m["fee"].to_bytes(32, "big") + m["gasLimit"].to_bytes(32, "big") + keccak256(bytes.fromhex(m["data"][2:]))
        + m["index"].to_bytes(32, "big")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l1-rpc", required=True)
    parser.add_argument("--rollup", required=True)
    parser.add_argument("--l2-rpc", required=True)
    parser.add_argument("--wallet", action="append", default=[], help="a key that claims the messages to its address")
    parser.add_argument("--relayer", help="a key that claims the messages to every other address")
    parser.add_argument("--cache", help="a file that keeps the messages and claimed bits across runs")
    args = parser.parse_args()

    # The anchor of the latest L2 block, which the EIP-4788 contract keeps
    # on L2 under the block's timestamp.
    head = json_rpc(args.l2_rpc, "eth_getBlockByNumber", "latest", False)
    if int(head["number"], 16) == 0:
        return
    anchor = int(json_rpc(args.l1_rpc, "eth_getBlockByHash", head["parentBeaconBlockRoot"], False)["number"], 16)
    cache = MessageCache(args.cache, args.l1_rpc, args.rollup, anchor)
    try:
        claim_messages(args, head, anchor, cache)
    finally:
        cache.save()


def claim_messages(args: argparse.Namespace, head: dict, anchor: int, cache: MessageCache) -> None:
    messages = cache.messages
    if not messages:
        return
    timestamp = int(head["timestamp"], 16)

    def storage(slot: bytes) -> int:
        return int(json_rpc(args.l2_rpc, "eth_getStorageAt", L2_MESSENGER, hx(slot), "latest"), 16)

    words = {}

    def word(i: int) -> int:
        """A word of claimed bits, read once per run."""
        if i not in words:
            words[i] = storage(keccak256(i.to_bytes(32, "big") + CLAIMED_SLOT.to_bytes(32, "big")))
        return words[i]

    claimed = lambda i: cache.claimed(i, word)  # noqa: E731

    # Twice the base fee leaves room for it to rise before inclusion.
    price = 2 * int(head["baseFeePerGas"], 16) + PRIORITY_FEE
    wallets = {str(EOA(key=int(k, 16))).lower(): EOA(key=int(k, 16)) for k in args.wallet}
    relayer = EOA(key=int(args.relayer, 16)) if args.relayer else None
    if relayer is not None:
        wallets.setdefault(str(relayer).lower(), relayer)
    claims = {}  # claimer -> messages
    for m in messages:
        claimer = wallets.get(m["to"].lower())
        # The relayer only claims what pays: the fee, received before the
        # VERIFY frame, must cover the most the claim can cost.
        if claimer is None and relayer is not None and m["fee"] >= max_claim_gas(m) * price:
            claimer = relayer
        if claimer is not None and not claimed(m["index"]):
            claims.setdefault(str(claimer).lower(), (claimer, []))[1].append(m)
    if not claims:
        return

    # A root the messenger already has covers the first messages: claims of
    # those only need their path to it.
    leaves = cache.leaves
    proven = storage(PROVEN_ROOT_SLOT.to_bytes(32, "big")).to_bytes(32, "big")
    covered = next((count for count, root in reversed(list(enumerate(cache.roots, 1))) if root == proven), 0)
    trees = {}

    def path(m: dict, size: int) -> str:
        if size not in trees:
            trees[size] = message_tree(leaves[:size])
        height = (size - 1).bit_length()
        return "[" + ",".join(hx(p) for p in trees[size][1][m["index"]][:height]) + "]"

    prove = None
    for claimer, pending in claims.values():
        nonce = int(json_rpc(args.l2_rpc, "eth_getTransactionCount", str(claimer), "pending"), 16)
        proved = False
        for m in pending:
            calls = []
            if m["index"] >= covered:
                if prove is None:
                    proof = json_rpc(args.l1_rpc, "eth_getProof", args.rollup, [f"0x{L1_MESSAGE_ROOT_SLOT:064x}"], hex(anchor))
                    assert int(proof["storageProof"][0]["value"], 16) == int.from_bytes(message_tree(leaves)[0], "big"), "L1 message root"
                    prove = cast(
                        "calldata", PROVE_ROOT_SIGNATURE, str(timestamp), json_rpc(args.l1_rpc, "debug_getRawHeader", hex(anchor)),
                        "[" + ",".join(proof["accountProof"]) + "]", "[" + ",".join(proof["storageProof"][0]["proof"]) + "]",
                    )
                # Each claimer's first claim proves the root, which costs little
                # if another claim in the block proved it already.
                if not proved:
                    calls.append(prove)
                size = len(leaves)
            else:
                size = covered
            calls.append(cast(
                "calldata", CLAIM_SIGNATURE,
                f"({m['sender']},{m['to']},{m['value']},{m['fee']},{m['gasLimit']},{m['data']},{m['index']})",
                path(m, size), str(claimer),
            ))
            tx = Transaction(
                sender=claimer,
                nonce=nonce,
                frames=[
                    Frame(
                        mode=DEFAULT_MODE, target=TestAddress(L2_MESSENGER), data=bytes.fromhex(c[2:]),
                        gas_limit=gas, state_gas_limit=state_gas,
                    )
                    for c, (gas, state_gas) in zip(calls, [(CLAIM_GAS_LIMIT, CLAIM_STATE_GAS_LIMIT)] * (len(calls) - 1) + [claim_gas(m)])
                ]
                + [Frame(mode=VERIFY_MODE, flags=APPROVE_EXECUTION_AND_PAYMENT)],
                chain_id=L2_CHAIN_ID,
                max_fee=max_claim_gas(m) * price,
                max_priority_fee_per_gas=PRIORITY_FEE,
            )
            try:
                tx_hash = json_rpc(args.l2_rpc, "eth_sendRawTransaction", hx(bytes(tx.rlp())))
            except RuntimeError as e:
                # Another claim of it may be pending, which the node admitted
                # because it succeeds: skip the message and keep the nonce.
                print(json.dumps({"index": m["index"], "skipped": str(e)}), flush=True)
                continue
            nonce += 1
            proved = proved or prove in calls
            print(json.dumps({
                "index": m["index"], "to": m["to"], "value": m["value"], "fee": m["fee"], "claimer": str(claimer).lower(), "tx": tx_hash,
            }), flush=True)


if __name__ == "__main__":
    main()
