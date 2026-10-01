"""
Claims L1 to L2 messages as wallets do: from L1 data and the L2 RPC alone,
each claim signed with its claimer's own key.

A claim is an EIP-8141 frame transaction from the claimer:

    frame 0  DEFAULT  L2Messenger.proveL1MessageRoot(...)  when the messenger has no root with the message
    frame 1  DEFAULT  L2Messenger.claimL1Message(message, path)
    frame 2  VERIFY   the claimer approves execution and payment

The first frame proves the root of the rollup contract's message tree
against the L1 anchor of the latest L2 block, which the EIP-4788 contract
keeps on L2, so a wallet builds the claim between blocks. A message to the
claimer pays for its own claim: the fee is taken at the VERIFY frame, after
the claim delivered the message's value.

Each wallet claims the messages to itself. The relayer, if any, claims the
messages to every other address, which cannot claim themselves, such as
contracts. Messages carry no fee, so nothing pays the relayer back.

    uv run --project <execution-specs> python script/l2_claims.py --l1-rpc <url> --rollup <address> \
        --l2-rpc <url> --wallet <key> [--wallet <key> ...] [--relayer <key>]

Run with the environment of the execution-specs merge the L2 node uses,
which signs the frame transactions with EEST.
"""

import argparse
import json
import subprocess

from execution_testing import EOA, Frame, Transaction
from execution_testing import Address as TestAddress

from ethereum.crypto.hash import keccak256

from l2_node import L2_CHAIN_ID, L2_MESSENGER, PRIORITY_FEE, hx, json_rpc

PROVE_ROOT_SIGNATURE = "proveL1MessageRoot(uint256,bytes,bytes[],bytes[])"
CLAIM_SIGNATURE = "claimL1Message((address,address,uint256,bytes,uint256),bytes32[])"
CLAIM_GAS_LIMIT = 1_000_000
# EIP-8141 frame modes and approval scopes.
DEFAULT_MODE, VERIFY_MODE = 0, 1
APPROVE_EXECUTION_AND_PAYMENT = 3
L1_MESSAGE_SENT = keccak256(b"L1MessageSent(uint256,address,address,uint256,bytes)")
L1_MESSAGE_ROOT_SLOT = 3  # root of NativeRollup.l1Messages
CLAIMED_SLOT = 1  # L2Messenger.claimedBits, 256 flags per slot
PROVEN_ROOT_SLOT = 4  # L2Messenger.provenL1MessageRoot
TREE_DEPTH = 32
ZERO_HASHES = [bytes(32)]
for _ in range(TREE_DEPTH):
    ZERO_HASHES.append(keccak256(ZERO_HASHES[-1] * 2))


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True).stdout.strip()


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


def prefix_roots(leaves: list) -> list:
    """The tree's root after each message, as `MessageTree.insert` keeps it."""
    branch, roots = [bytes(32)] * TREE_DEPTH, []
    for count, leaf in enumerate(leaves, 1):
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


def l1_messages(l1_rpc: str, rollup: str, anchor: int) -> list:
    """The messages the rollup contract had sent by the anchor block."""
    logs = json_rpc(l1_rpc, "eth_getLogs", {
        "address": rollup, "fromBlock": "0x0", "toBlock": hex(anchor), "topics": [hx(L1_MESSAGE_SENT)],
    })
    messages = []
    for log in logs:
        data = bytes.fromhex(log["data"][2:])
        offset = int.from_bytes(data[32:64], "big")
        length = int.from_bytes(data[offset : offset + 32], "big")
        messages.append({
            "index": int(log["topics"][1], 16),
            "sender": "0x" + log["topics"][2][-40:],
            "to": "0x" + log["topics"][3][-40:],
            "value": int.from_bytes(data[0:32], "big"),
            "data": "0x" + data[offset + 32 : offset + 32 + length].hex(),
        })
    assert [m["index"] for m in messages] == list(range(len(messages))), "missing L1 messages"
    return messages


def leaf(m: dict) -> bytes:
    return keccak256(
        bytes.fromhex(m["sender"][2:]) + bytes.fromhex(m["to"][2:]) + m["value"].to_bytes(32, "big")
        + keccak256(bytes.fromhex(m["data"][2:])) + m["index"].to_bytes(32, "big")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l1-rpc", required=True)
    parser.add_argument("--rollup", required=True)
    parser.add_argument("--l2-rpc", required=True)
    parser.add_argument("--wallet", action="append", default=[], help="a key that claims the messages to its address")
    parser.add_argument("--relayer", help="a key that claims the messages to every other address")
    args = parser.parse_args()

    # The anchor of the latest L2 block, which the EIP-4788 contract keeps
    # on L2 under the block's timestamp.
    head = json_rpc(args.l2_rpc, "eth_getBlockByNumber", "latest", False)
    if int(head["number"], 16) == 0:
        return
    timestamp = int(head["timestamp"], 16)
    anchor = int(json_rpc(args.l1_rpc, "eth_getBlockByHash", head["parentBeaconBlockRoot"], False)["number"], 16)
    messages = l1_messages(args.l1_rpc, args.rollup, anchor)
    if not messages:
        return

    def storage(slot: bytes) -> int:
        return int(json_rpc(args.l2_rpc, "eth_getStorageAt", L2_MESSENGER, hx(slot), "latest"), 16)

    words = {}
    claimed = lambda i: words.setdefault(i >> 8, storage(keccak256((i >> 8).to_bytes(32, "big") + CLAIMED_SLOT.to_bytes(32, "big")))) >> (i & 0xFF) & 1  # noqa: E731

    wallets = {str(EOA(key=int(k, 16))).lower(): EOA(key=int(k, 16)) for k in args.wallet}
    relayer = EOA(key=int(args.relayer, 16)) if args.relayer else None
    claims = {}  # claimer -> messages
    for m in messages:
        claimer = wallets.get(m["to"].lower()) or relayer
        if claimer is not None and not claimed(m["index"]):
            claims.setdefault(str(claimer).lower(), (claimer, []))[1].append(m)
    if not claims:
        return

    # A root the messenger already has covers the first messages: claims of
    # those only need their path to it.
    leaves = [leaf(m) for m in messages]
    proven = storage(PROVEN_ROOT_SLOT.to_bytes(32, "big")).to_bytes(32, "big")
    covered = next((count for count, root in reversed(list(enumerate(prefix_roots(leaves), 1))) if root == proven), 0)
    trees = {}

    def path(m: dict, size: int) -> str:
        if size not in trees:
            trees[size] = message_tree(leaves[:size])
        height = (size - 1).bit_length()
        return "[" + ",".join(hx(p) for p in trees[size][1][m["index"]][:height]) + "]"

    prove = None
    for claimer, pending in claims.values():
        nonce = int(json_rpc(args.l2_rpc, "eth_getTransactionCount", str(claimer), "pending"), 16)
        for i, m in enumerate(pending):
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
                if i == 0:
                    calls.append(prove)
                size = len(leaves)
            else:
                size = covered
            calls.append(cast(
                "calldata", CLAIM_SIGNATURE, f"({m['sender']},{m['to']},{m['value']},{m['data']},{m['index']})", path(m, size),
            ))
            tx = Transaction(
                sender=claimer,
                nonce=nonce,
                frames=[
                    Frame(mode=DEFAULT_MODE, target=TestAddress(L2_MESSENGER), data=bytes.fromhex(c[2:]), gas_limit=CLAIM_GAS_LIMIT)
                    for c in calls
                ]
                + [Frame(mode=VERIFY_MODE, flags=APPROVE_EXECUTION_AND_PAYMENT)],
                chain_id=L2_CHAIN_ID,
                max_fee_per_gas=10**9,
                max_priority_fee_per_gas=PRIORITY_FEE,
            )
            nonce += 1
            tx_hash = json_rpc(args.l2_rpc, "eth_sendRawTransaction", hx(bytes(tx.rlp())))
            print(json.dumps({"index": m["index"], "to": m["to"], "value": m["value"], "claimer": str(claimer).lower(), "tx": tx_hash}), flush=True)


if __name__ == "__main__":
    main()
