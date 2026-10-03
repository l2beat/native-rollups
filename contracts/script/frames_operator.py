"""
Operator for a native rollup on an EIP-8141 chain without EIP-8288, such as
frames-devnet-0, with the preconfirmations customization (see
FramesSequencedRollup).

`preconfirm` anchors the next L2 block to an L1 block a few slots behind the
head, and has the L2 node (`l2_node.py`, through its RPC) build it on its
latest block from its mempool, validate it with the L1 stateless validation
program, sign the dependency, and preconfirm it with the sequencer's key.

`advance` posts the oldest preconfirmed block the rollup does not have yet,
in one frame transaction:

    frame 0  VERIFY   the operator's account approves execution and payment
    frame 1  DEFAULT  MockDependencyVerifier(scheme || data_hash || vk_hash || proof)
    frame 2  SENDER   rollup.advance(params, 1)

The transaction carries the block's EIP-8142 payload blobs, so it is sent in
the EIP-7594 network form with the blobs, their commitments and their cell
proofs. `--submit-rpc`, which can repeat, selects clients that accept blob-carrying frame
transactions: on frames-devnet-0, Nethermind and Reth do, while geth and
ethrex do not.

`claim-l2-message` claims an L2 to L1 message, which an L2 account sent with
`L2Messenger.sendMessage`, with a proof from the L2 node's `eth_getProof`
against the latest L2 block the rollup has.

`check-vectors` checks the Python root computation against the
consensus-specs vectors that the Solidity tests use.

Run with the execution-specs `devnets/frames/0` environment, which provides
the frame transaction types, and Foundry's `cast` on the PATH:

    uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py \
        preconfirm --rpc <url> --rollup <address> --verifier <address> \
        --sequencer-key <key> --prover-key <key> --l2-rpc <url>
    uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py \
        advance --rpc <url> --rollup <address> --verifier <address> --operator-key <key> --l2-rpc <url>
"""

import argparse
from dataclasses import replace
import json
import random
import subprocess
import threading
import time
import traceback
import urllib.request

from ethereum_types.bytes import Bytes0, Bytes20
from ethereum_types.numeric import U64, U256, Uint

from ethereum.forks.amsterdam.transactions import encode_transaction
from ethereum.forks.amsterdam.transactions.frame_transaction import (
    Frame,
    FrameFlag,
    FrameMode,
    FrameSignature,
    FrameSignatureScheme,
    FrameTransaction,
    GasLimits,
    TransactionFees,
    compute_frame_signature_hash,
)

from ethereum.crypto.hash import keccak256
from ethereum_rlp import rlp

import block_in_blobs as bib
from ssz_roots import check_vectors  # noqa: E402

ADVANCE_SIGNATURE = (
    "advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,"
    "bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)
CLAIM_L2_MESSAGE_SIGNATURE = "claimL2Message((address,address,uint256,uint256,uint256,bytes,uint256),uint256,bytes[],bytes[],address)"
DEPENDENCY_FRAME_INDEX = 1
# How many L1 blocks behind the head the operator anchors L2 blocks.
ANCHOR_DEPTH = 2
# How many preconfirmed blocks `sequence` posts at once, each in its own L1
# transaction with one blob. On the local devnet only Nethermind includes
# blob-carrying frame transactions, in about one L1 block out of four, so
# each of its blocks must take many, and it and Reth refuse more than 16
# pending ones from one sender.
MAX_POSTS = 16
L2_MESSENGER = "0x8079000000000000000000000000000000000001"
L2_MESSAGE_SENT = keccak256(b"L2MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")
SENT_SLOT = 2  # L2Messenger.sentMessages


# ---------------------------------------------------------------------------
# Chain access through Foundry's cast
# ---------------------------------------------------------------------------


def cast(*args: str) -> str:
    out = subprocess.run(["cast", *args], capture_output=True, text=True)
    if out.returncode != 0:
        # The arguments can hold a whole blob transaction, so only the error.
        raise SystemExit(f"cast {' '.join(a for a in args[:3] if len(a) < 80)} failed: {out.stderr.strip()[-500:]}")
    return out.stdout.strip()


def call(rpc: str, to: str, sig: str) -> str:
    return cast("call", "--rpc-url", rpc, to, sig)


def sign_hash(key: str, digest: bytes) -> bytes:
    """65-byte r || s || v signature with v in {27, 28}, from `cast`."""
    return bytes.fromhex(cast("wallet", "sign", "--no-hash", "--private-key", key, "0x" + digest.hex())[2:])


def hx(b: bytes) -> str:
    return "0x" + b.hex()


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


def l2_rpc(url: str, method: str, *params):
    request = urllib.request.Request(
        url,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        out = json.load(response)
    if "error" in out:
        raise SystemExit(f"L2 node: {method}: {out['error']['message']}")
    return out["result"]


def send_raw_transaction(url: str, raw: bytes) -> str:
    # Over JSON-RPC rather than with cast: a blob transaction is longer than
    # Linux allows one command-line argument to be.
    request = urllib.request.Request(
        url,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": [hx(raw)]}).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            out = json.load(response)
    except OSError as e:
        raise SystemExit(str(e))
    if "error" in out:
        raise SystemExit(out["error"]["message"])
    return out["result"]


def wait_for_receipt(rpc: str, tx_hash: str, blocks: int = 40) -> dict:
    """Polls for the receipt, giving up after `blocks` L1 blocks."""
    last = int(cast("block-number", "--rpc-url", rpc)) + blocks
    while int(cast("block-number", "--rpc-url", rpc)) <= last:
        receipt = json.loads(cast("rpc", "--rpc-url", rpc, "eth_getTransactionReceipt", tx_hash))
        if receipt:
            return receipt
        time.sleep(3)
    raise SystemExit(f"{tx_hash} not included within {blocks} blocks")


def preconfirm_block(args: argparse.Namespace, timestamp: int | None = None) -> dict:
    """Has the L2 node build the next block, at `timestamp` if given,
    validate it, sign the dependency and preconfirm it, and returns what
    happened."""
    rpc = args.rpc
    # The registry's current entry, under which the block must be posted.
    registry = call(rpc, args.rollup, "evmVkRegistry()(address)")
    entry = cast("call", "--rpc-url", rpc, registry, "0x" + "00" * 32)
    vk_hash, schema_id = "0x" + entry[2:66], int(entry[66:130], 16)

    # Anchor a few slots behind the L1 head, so that a short L1 reorg does
    # not remove the anchor and, with it, the block. The block must reach L1
    # while the anchor is in the BLOCKHASH window.
    l1_block = int(cast("block-number", "--rpc-url", rpc))
    anchor_number = l1_block - ANCHOR_DEPTH
    anchor_hash = cast("block", "--rpc-url", rpc, str(anchor_number), "--field", "hash")

    # The L2 node builds the next block on its latest one, validates it with
    # the stateless program, signs the dependency, and preconfirms it.
    preconfirmed = l2_rpc(args.l2_rpc, "nr_preconfirm", {
        "timestamp": timestamp,
        "anchorNumber": anchor_number,
        "anchorHash": anchor_hash,
        "schemaId": schema_id,
        "vkHash": vk_hash,
        "l1ChainId": int(cast("chain-id", "--rpc-url", rpc)),
        "verifier": args.verifier,
        "rollup": args.rollup,
        "proverKey": args.prover_key,
        "sequencerKey": args.sequencer_key,
    })
    print(
        f"preconfirmed L2 block {preconfirmed['number']} ({preconfirmed['transactions']} transactions), "
        f"hash {preconfirmed['blockHash']}, anchor L1 block {anchor_number}",
        flush=True,
    )
    return {
        "type": "preconfirm",
        "l2": {k: v for k, v in preconfirmed.items() if k not in ("proof", "triple", "preconfirmation")},
        "preconfirmation": preconfirmed["preconfirmation"],
        "l1Block": l1_block,
    }


def preconfirm(args: argparse.Namespace) -> None:
    entry = preconfirm_block(args)
    if args.record:
        record(args.record, entry)


def send_post(args: argparse.Namespace, bundle: dict, nonce: int) -> str:
    """Sends the frame transaction that posts a preconfirmed block, with
    `nonce`, and returns its hash."""
    rpc = args.rpc
    operator = cast("wallet", "address", "--private-key", args.operator_key)
    p = bundle["params"]
    triple = bytes.fromhex(bundle["triple"][2:])
    proof = bytes.fromhex(bundle["proof"][2:])
    blobs = [bytes.fromhex(blob[2:]) for blob in bundle["blobs"]]
    versioned_hashes = tuple(bytes.fromhex(h[2:]) for h in bundle["versionedHashes"])
    blob_base_fee = int(cast("rpc", "--rpc-url", rpc, "eth_blobBaseFee").strip('"'), 16)
    if getattr(args, "corrupt_proof", False):
        proof = bytes([proof[0] ^ 1]) + proof[1:]

    params = (
        f"({p['stateRoot']},{p['receiptsRoot']},{p['logsBloom']},{p['gasUsed']},{p['timestamp']},"
        f"{p['baseFeePerGas']},{p['blockHash']},{p['transactionsRoot']},{p['blockAccessListRoot']},"
        f"{p['payloadBlobCount']},{p['executionRequestsRoot']},{p['anchorBlockNumber']},"
        f"{p['feeRecipient']},{p['prevRandao']},{p['extraData']})"
    )
    calldata = bytes.fromhex(cast("calldata", ADVANCE_SIGNATURE, params, str(DEPENDENCY_FRAME_INDEX))[2:])

    base_fee = int(cast("base-fee", "--rpc-url", rpc))
    # A slightly different tip each time, so that sending a block again makes
    # a new transaction: a client that dropped the old one may still refuse
    # it as already known.
    tip = 10**9 + random.randrange(10**6)
    tx = FrameTransaction(
        chain_id=U64(int(cast("chain-id", "--rpc-url", rpc))),
        nonce=U256(nonce),
        sender=Bytes20(bytes.fromhex(operator[2:])),
        frames=(
            Frame(
                mode=FrameMode.VERIFY,
                flags=FrameFlag.APPROVE_PAYMENT | FrameFlag.APPROVE_EXECUTION,
                to=Bytes0(),
                gas_limits=GasLimits(execution=U64(30_000), state=U64(0)),
                value=U256(0),
                data=b"",
            ),
            Frame(
                mode=FrameMode.DEFAULT,
                flags=FrameFlag(0),
                to=Bytes20(bytes.fromhex(args.verifier[2:])),
                gas_limits=GasLimits(execution=U64(100_000), state=U64(0)),
                value=U256(0),
                data=triple + proof,
            ),
            Frame(
                mode=FrameMode.SENDER,
                flags=FrameFlag(0),
                to=Bytes20(bytes.fromhex(args.rollup[2:])),
                gas_limits=GasLimits(execution=U64(2_000_000), state=U64(1_000_000)),
                value=U256(0),
                data=calldata,
            ),
        ),
        signatures=(
            FrameSignature(
                scheme=FrameSignatureScheme.SECP256K1,
                signer=bytes.fromhex(operator[2:]),
                message=Bytes0(),
                signature=b"",
            ),
        ),
        fees=TransactionFees(
            max_priority_fee_per_gas=Uint(tip),
            max_fee_per_gas=Uint(2 * base_fee + tip),
            max_fee_per_blob_gas=U256(2 * blob_base_fee),
        ),
        blob_versioned_hashes=versioned_hashes,
    )
    # Sign the canonical hash, then insert the signature as v || r || s.
    sig = sign_hash(args.operator_key, compute_frame_signature_hash(tx))
    signature = FrameSignature(
        scheme=FrameSignatureScheme.SECP256K1,
        signer=bytes.fromhex(operator[2:]),
        message=Bytes0(),
        signature=bytes([sig[64] - 27]) + sig[:64],
    )
    tx = replace(tx, signatures=(signature,))
    raw = encode_transaction(tx)
    # EIP-7594 network form: [tx_payload_body, wrapper_version, blobs, commitments, cell_proofs].
    commitments = [bib.commitment(blob) for blob in blobs]
    wrapped = raw[:1] + rlp.encode(
        [rlp.decode(raw[1:]), b"\x01", blobs, commitments, [p for blob in blobs for p in bib.cell_proofs(blob)]]
    )

    print(
        f"posting L2 block {bundle['number']} ({bundle['transactions']} transactions, state root {bundle['stateRoot']}), "
        f"anchor L1 block {bundle['anchor']['number']}, data_hash 0x{triple[32:64].hex()}, "
        f"{bundle['payloadBytes']} payload bytes in {len(blobs)} blob(s), nonce {nonce}",
        flush=True,
    )
    for m in bundle["l2Messages"]:
        print(f"sends L2 message {m['index']}: {m['value']} wei from {m['sender']} to {m['to']} on L1", flush=True)
    # To every client given, any of which may include it.
    sent = []
    for url in args.submit_rpc or [rpc]:
        try:
            sent.append(send_raw_transaction(url, wrapped))
        except SystemExit as e:
            print(f"{url} refused it: {e}", flush=True)
    if not sent:
        raise SystemExit("no client took the transaction")
    return sent[0]


def post_record(args: argparse.Namespace, bundle: dict, tx_hash: str, receipt: dict) -> dict | None:
    """What posting a block did, once L1 included the transaction, or None
    if `advance` failed in it."""
    rpc = args.rpc
    block = json.loads(cast("rpc", "--rpc-url", rpc, "eth_getBlockByNumber", receipt["blockNumber"], "false"))
    builder = bytes.fromhex(block["extraData"][2:])
    frames = [
        {"status": int(f["status"], 16), "executionGas": int(f["executionGasUsed"], 16), "stateGas": int(f["stateGasUsed"], 16)}
        for f in receipt.get("frameReceipts", [])
    ]
    print(
        f"included {tx_hash} in L1 block {int(receipt['blockNumber'], 16)} (built by {builder.decode(errors='replace')}), "
        f"gas {int(receipt['gasUsed'], 16)}, blob gas {int(receipt.get('blobGasUsed', '0x0'), 16)} "
        f"at {int(receipt.get('blobGasPrice', '0x0'), 16)} wei, frame statuses {[f['status'] for f in frames]}",
        flush=True,
    )
    if not frames or frames[-1]["status"] != 1:
        return None
    return {
        "type": "advance",
        "l2": {k: v for k, v in bundle.items() if k not in ("blobs", "proof", "triple", "preconfirmation")},
        "preconfirmation": bundle["preconfirmation"],
        "proof": {"kind": "mock", "triple": bundle["triple"], "signature": bundle["proof"]},
        "l1": {
            "txHash": tx_hash,
            "block": int(receipt["blockNumber"], 16),
            "timestamp": int(block["timestamp"], 16),
            "builder": builder.decode(errors="replace"),
            "gasUsed": int(receipt["gasUsed"], 16),
            "blobGasUsed": int(receipt.get("blobGasUsed", "0x0"), 16),
            "blobGasPrice": int(receipt.get("blobGasPrice", "0x0"), 16),
            "frames": frames,
            "rollupHead": int(call(rpc, args.rollup, "blockNumber()(uint256)").split()[0]),
        },
    }


def advance(args: argparse.Namespace) -> None:
    """Posts the oldest preconfirmed block the rollup does not have yet."""
    bundles = l2_rpc(args.l2_rpc, "nr_waitingPosts", call(args.rpc, args.rollup, "blockHash()(bytes32)"), 1)
    if not bundles:
        print("no preconfirmed block to post")
        return
    operator = cast("wallet", "address", "--private-key", args.operator_key)
    tx_hash = send_post(args, bundles[0], int(cast("nonce", "--rpc-url", args.rpc, operator)))
    entry = post_record(args, bundles[0], tx_hash, wait_for_receipt(args.rpc, tx_hash))
    if entry is None:
        raise SystemExit("advance failed")
    if args.record:
        record(args.record, entry)


def sequence(args: argparse.Namespace) -> None:
    """Runs the sequencer: preconfirms a block every --block-time seconds,
    empty if the mempool is, and posts each once --proving-time has passed
    since its preconfirmation, standing in for proving, several per L1
    block. Appends what happened to --log, one JSON line per block
    preconfirmed or posted."""
    args.operator_key = args.sequencer_key
    log = open(args.log, "a")
    lock = threading.Lock()

    def emit(entry: dict) -> None:
        with lock:
            log.write(json.dumps(entry) + "\n")
            log.flush()

    def produce() -> None:
        while True:
            try:
                latest = l2_rpc(args.l2_rpc, "eth_getBlockByNumber", "latest", False)
                # The next slot, unless the sequencer fell more than a slot
                # behind, as after a restart: then now, skipping the slots it
                # missed instead of filling them.
                timestamp = int(latest["timestamp"], 16) + args.block_time
                if timestamp < time.time() - args.block_time:
                    timestamp = int(time.time())
                time.sleep(max(0.0, timestamp - time.time()))
                emit(preconfirm_block(args, timestamp))
            except BaseException:
                traceback.print_exc()
                time.sleep(args.block_time)

    def post() -> None:
        operator = cast("wallet", "address", "--private-key", args.operator_key)
        in_flight = {}  # L2 block number -> (bundle, transaction hash)
        while True:
            try:
                for number, (bundle, tx_hash) in sorted(in_flight.items()):
                    receipt = json.loads(cast("rpc", "--rpc-url", args.rpc, "eth_getTransactionReceipt", tx_hash))
                    if receipt:
                        entry = post_record(args, bundle, tx_hash, receipt)
                        if entry:
                            emit(entry)
                        del in_flight[number]
                # Posts not yet included, this process's or not, as the
                # nonces the submitting client has pending. With none, any
                # earlier post landed or was dropped, so the waiting blocks
                # are exactly those to post. Otherwise only those after this
                # process's posts, and none if it does not know which are in
                # flight, as after a restart.
                confirmed = int(cast("nonce", "--rpc-url", args.rpc, operator))
                pending = max(
                    int(cast("nonce", "--block", "pending", "--rpc-url", url, operator)) for url in args.submit_rpc or [args.rpc]
                )
                if pending == confirmed:
                    in_flight.clear()
                elif not in_flight:
                    time.sleep(2)
                    continue
                head = call(args.rpc, args.rollup, "blockHash()(bytes32)")
                after = max(in_flight, default=0)
                ready = [
                    b for b in l2_rpc(args.l2_rpc, "nr_waitingPosts", head, MAX_POSTS)
                    if b["number"] > after and time.time() - b["preconfirmation"]["time"] >= args.proving_time
                ][: MAX_POSTS - len(in_flight)]
                for b in ready:
                    in_flight[b["number"]] = (b, send_post(args, b, pending))
                    pending += 1
            except BaseException:
                traceback.print_exc()
            time.sleep(2)

    threading.Thread(target=post, daemon=True).start()
    produce()


def record(path: str, entry: dict) -> None:
    with open(path, "w") as f:
        json.dump(entry, f)


def claim_l2_message(args: argparse.Namespace) -> None:
    rpc = args.rpc
    head_hash = call(rpc, args.rollup, "blockHash()(bytes32)")
    latest = l2_rpc(args.l2_rpc, "eth_getBlockByNumber", "safe", False)
    if latest["hash"] != head_hash:
        raise SystemExit(f"the L2 node's latest posted block is {latest['hash']}, the rollup's {head_hash}")
    logs = l2_rpc(args.l2_rpc, "eth_getLogs", {
        "address": L2_MESSENGER, "fromBlock": "0x0", "toBlock": "safe",
        "topics": [hx(L2_MESSAGE_SENT), f"0x{args.index:064x}"],
    })
    if not logs:
        raise SystemExit(f"L2 to L1 message {args.index} is not in an L2 block the rollup has")
    topics, data = logs[0]["topics"], bytes.fromhex(logs[0]["data"][2:])
    offset = int.from_bytes(data[96:128], "big")
    m = {
        "index": args.index, "sender": "0x" + topics[2][-40:], "to": "0x" + topics[3][-40:],
        "value": int.from_bytes(data[0:32], "big"), "fee": int.from_bytes(data[32:64], "big"),
        "gasLimit": int.from_bytes(data[64:96], "big"),
        "data": hx(data[offset + 32 : offset + 32 + int.from_bytes(data[offset : offset + 32], "big")]),
    }
    # The claimer receives the message's fee, if any.
    claimer = cast("wallet", "address", "--private-key", args.key)
    slot = int.from_bytes(keccak256(SENT_SLOT.to_bytes(32, "big")), "big") + args.index
    proof = l2_rpc(args.l2_rpc, "eth_getProof", L2_MESSENGER, [f"0x{slot:064x}"], "safe")
    p = {
        "blockNumber": int(latest["number"], 16),
        "accountProof": proof["accountProof"],
        "storageProof": proof["storageProof"][0]["proof"],
    }
    before = int(cast("balance", "--rpc-url", rpc, m["to"]))
    receipt = json.loads(
        cast(
            "send", "--rpc-url", rpc, "--private-key", args.key, "--json", args.rollup, CLAIM_L2_MESSAGE_SIGNATURE,
            f"({m['sender']},{m['to']},{m['value']},{m['fee']},{m['gasLimit']},{m['data']},{m['index']})",
            str(p["blockNumber"]),
            "[" + ",".join(p["accountProof"]) + "]",
            "[" + ",".join(p["storageProof"]) + "]",
            claimer,
        )
    )
    print(
        f"L2 message {m['index']} proven against L2 block {p['blockNumber']} "
        f"({len(p['accountProof'])} + {len(p['storageProof'])} proof nodes): "
        f"status {int(receipt['status'], 16)}, gas {int(receipt['gasUsed'], 16)}, "
        f"{m['to']} received {int(cast('balance', '--rpc-url', rpc, m['to'])) - before} wei"
    )
    if args.record:
        record(args.record, {
            "type": "claimL2Message",
            "message": m,
            "claimer": claimer.lower(),
            "l2Block": p["blockNumber"],
            "proofNodes": len(p["accountProof"]) + len(p["storageProof"]),
            "l1": {
                "txHash": receipt["transactionHash"],
                "block": int(receipt["blockNumber"], 16),
                "status": int(receipt["status"], 16),
                "gasUsed": int(receipt["gasUsed"], 16),
            },
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-vectors")
    check.add_argument("--vectors", default="test/native_rollup_vectors.json")
    pre = sub.add_parser("preconfirm")
    pre.add_argument("--rpc", required=True)
    pre.add_argument("--rollup", required=True)
    pre.add_argument("--verifier", required=True)
    pre.add_argument("--sequencer-key", required=True)
    pre.add_argument("--prover-key", required=True)
    pre.add_argument("--l2-rpc", required=True, help="the L2 node's RPC")
    pre.add_argument("--record", help="write what happened to this JSON file")
    seq = sub.add_parser("sequence")
    seq.add_argument("--rpc", required=True)
    seq.add_argument("--submit-rpc", action="append", help="a client to send the blob-carrying transactions to, by default --rpc")
    seq.add_argument("--rollup", required=True)
    seq.add_argument("--verifier", required=True)
    seq.add_argument("--sequencer-key", required=True, help="signs the preconfirmations and posts the blocks")
    seq.add_argument("--prover-key", required=True)
    seq.add_argument("--l2-rpc", required=True, help="the L2 node's RPC")
    seq.add_argument("--block-time", type=int, default=4, help="seconds between L2 blocks")
    seq.add_argument("--proving-time", type=int, default=20, help="seconds a block waits before it is posted")
    seq.add_argument("--log", required=True, help="append what happened to this file, one JSON line per event")
    adv = sub.add_parser("advance")
    adv.add_argument("--rpc", required=True)
    adv.add_argument("--submit-rpc", action="append", help="a client to send the blob-carrying transaction to, by default --rpc")
    adv.add_argument("--rollup", required=True)
    adv.add_argument("--verifier", required=True)
    adv.add_argument("--operator-key", required=True)
    adv.add_argument("--l2-rpc", required=True, help="the L2 node's RPC")
    adv.add_argument("--corrupt-proof", action="store_true", help="send an invalid mock proof")
    adv.add_argument("--record", help="write what happened to this JSON file")
    claim = sub.add_parser("claim-l2-message")
    claim.add_argument("--rpc", required=True)
    claim.add_argument("--rollup", required=True)
    claim.add_argument("--key", required=True, help="the L1 account sending the claim")
    claim.add_argument("--index", type=int, required=True)
    claim.add_argument("--l2-rpc", required=True, help="the L2 node's RPC")
    claim.add_argument("--record", help="write what happened to this JSON file")
    args = parser.parse_args()
    if args.command == "check-vectors":
        check_vectors(args.vectors)
    elif args.command == "preconfirm":
        preconfirm(args)
    elif args.command == "sequence":
        sequence(args)
    elif args.command == "advance":
        advance(args)
    else:
        claim_l2_message(args)


if __name__ == "__main__":
    main()
