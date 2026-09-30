"""
Operator for a native rollup on an EIP-8141 chain without EIP-8288, such as
frames-devnet-0 (see FramesNativeRollup).

`advance` anchors the next L2 block to the latest L1 block, has the L2 node
(`l2_node.py`) build it on the rollup's head, claiming the L1 messages sent
up to the anchor, validate it with the L1 stateless validation program, and
sign the dependency, then sends one frame transaction:

    frame 0  VERIFY   the operator's account approves execution and payment
    frame 1  DEFAULT  MockDependencyVerifier(scheme || data_hash || vk_hash || proof)
    frame 2  SENDER   rollup.advance(params, 1)

The transaction carries the block's EIP-8142 payload blobs, so it is sent in
the EIP-7594 network form with the blobs, their commitments and their cell
proofs. `--submit-rpc` selects a client that accepts blob-carrying frame
transactions: on frames-devnet-0, Nethermind and Reth do, while geth and
ethrex do not.

`claim-l2-message` claims an L2 to L1 message sent with `advance --withdraw`,
with the L2 node's proofs against the rollup's latest L2 block.

`check-vectors` checks the Python root computation against the
consensus-specs vectors that the Solidity tests use.

Run with the execution-specs `devnets/frames/0` environment, which provides
the frame transaction types, and Foundry's `cast` on the PATH:

    uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py \
        advance --rpc <url> --rollup <address> --verifier <address> \
        --operator-key <key> --prover-key <key> \
        --l2-state <file> --zkevm-specs <execution-specs@projects/zkevm+eip-8141>
"""

import argparse
from dataclasses import replace
import json
import os
import subprocess
import time

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

from ethereum_rlp import rlp

import block_in_blobs as bib
from ssz_roots import check_vectors  # noqa: E402

ADVANCE_SIGNATURE = (
    "advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,"
    "bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)
CLAIM_L2_MESSAGE_SIGNATURE = "claimL2Message((address,address,uint256,bytes,uint256),uint256,bytes[],bytes[])"
DEPENDENCY_FRAME_INDEX = 1


# ---------------------------------------------------------------------------
# Chain access through Foundry's cast
# ---------------------------------------------------------------------------


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True).stdout.strip()


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


def l2_node(args: argparse.Namespace, *node_args: str) -> dict:
    node = subprocess.run(
        [
            "uv", "run", "--project", args.zkevm_specs, "python",
            os.path.join(os.path.dirname(__file__), "l2_node.py"), *node_args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(node.stdout.strip().splitlines()[-1])


def wait_for_receipt(rpc: str, tx_hash: str, blocks: int = 40) -> dict:
    """Polls for the receipt, giving up after `blocks` L1 blocks."""
    last = int(cast("block-number", "--rpc-url", rpc)) + blocks
    while int(cast("block-number", "--rpc-url", rpc)) <= last:
        receipt = json.loads(cast("rpc", "--rpc-url", rpc, "eth_getTransactionReceipt", tx_hash))
        if receipt:
            return receipt
        time.sleep(3)
    raise SystemExit(f"{tx_hash} not included within {blocks} blocks")


def advance(args: argparse.Namespace) -> None:
    rpc = args.rpc
    operator = cast("wallet", "address", "--private-key", args.operator_key)

    # Rollup head and the registry's current entry.
    head_hash = call(rpc, args.rollup, "blockHash()(bytes32)")
    registry = call(rpc, args.rollup, "evmVkRegistry()(address)")
    entry = cast("call", "--rpc-url", rpc, registry, "0x" + "00" * 32)
    vk_hash, schema_id = "0x" + entry[2:66], int(entry[66:130], 16)

    # Anchor to the latest L1 block, whose hash is available to `advance`
    # in any later block.
    anchor_number = int(cast("block-number", "--rpc-url", rpc))
    anchor_hash = cast("block", "--rpc-url", rpc, str(anchor_number), "--field", "hash")

    # The L2 node builds the next block on the rollup's head, validates it
    # with the stateless program, and signs the dependency.
    bundle = l2_node(
        args, "build",
        "--state", args.l2_state,
        "--head-hash", head_hash,
        "--anchor-number", str(anchor_number),
        "--anchor-hash", anchor_hash,
        "--schema-id", str(schema_id),
        "--vk-hash", vk_hash,
        "--l1-chain-id", cast("chain-id", "--rpc-url", rpc),
        "--verifier", args.verifier,
        "--prover-key", args.prover_key,
        "--l1-rpc", rpc,
        "--rollup", args.rollup,
        *[a for w in args.withdraw for a in ("--withdraw", w)],
        *[a for t in args.transfer for a in ("--transfer", t)],
    )
    p = bundle["params"]
    triple = bytes.fromhex(bundle["triple"][2:])
    proof = bytes.fromhex(bundle["proof"][2:])
    blobs = [bytes.fromhex(blob[2:]) for blob in bundle["blobs"]]
    versioned_hashes = tuple(bytes.fromhex(h[2:]) for h in bundle["versionedHashes"])
    blob_base_fee = int(cast("rpc", "--rpc-url", rpc, "eth_blobBaseFee").strip('"'), 16)
    if args.corrupt_proof:
        proof = bytes([proof[0] ^ 1]) + proof[1:]

    params = (
        f"({p['stateRoot']},{p['receiptsRoot']},{p['logsBloom']},{p['gasUsed']},{p['timestamp']},"
        f"{p['baseFeePerGas']},{p['blockHash']},{p['transactionsRoot']},{p['blockAccessListRoot']},"
        f"{p['payloadBlobCount']},{p['executionRequestsRoot']},{p['anchorBlockNumber']},"
        f"{p['feeRecipient']},{p['prevRandao']},{p['extraData']})"
    )
    calldata = bytes.fromhex(cast("calldata", ADVANCE_SIGNATURE, params, str(DEPENDENCY_FRAME_INDEX))[2:])

    base_fee = int(cast("base-fee", "--rpc-url", rpc))
    tip = 10**9
    tx = FrameTransaction(
        chain_id=U64(int(cast("chain-id", "--rpc-url", rpc))),
        nonce=U256(int(cast("nonce", "--rpc-url", rpc, operator))),
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
        f"L2 block {bundle['number']} ({bundle['transactions']} transactions, state root {bundle['stateRoot']}), "
        f"anchor L1 block {anchor_number}, data_hash 0x{triple[32:64].hex()}, "
        f"{bundle['payloadBytes']} payload bytes in {len(blobs)} blob(s)"
    )
    for c in bundle["claims"]:
        print(
            f"claims L1 message {c['index']}: {c['value']} wei from {c['sender']} to {c['to']}, "
            f"whose L2 balance becomes {c['l2Balance']}"
        )
    for m in bundle["l2Messages"]:
        print(f"sends L2 message {m['index']}: {m['value']} wei from {m['sender']} to {m['to']} on L1")
    tx_hash = json.loads(cast("rpc", "--rpc-url", args.submit_rpc or rpc, "eth_sendRawTransaction", hx(wrapped)))
    receipt = wait_for_receipt(rpc, tx_hash)
    builder = bytes.fromhex(
        json.loads(cast("rpc", "--rpc-url", rpc, "eth_getBlockByNumber", receipt["blockNumber"], "false"))["extraData"][2:]
    )
    print(
        f"included {tx_hash} in L1 block {int(receipt['blockNumber'], 16)} (built by {builder.decode(errors='replace')}), "
        f"gas {int(receipt['gasUsed'], 16)}, blob gas {int(receipt.get('blobGasUsed', '0x0'), 16)} "
        f"at {int(receipt.get('blobGasPrice', '0x0'), 16)} wei"
    )
    frames = []
    for i, frame in enumerate(receipt.get("frameReceipts", [])):
        execution, state = int(frame["executionGasUsed"], 16), int(frame["stateGasUsed"], 16)
        print(f"frame {i}: status {int(frame['status'], 16)}, {execution} execution, {state} state gas")
        frames.append({"status": int(frame["status"], 16), "executionGas": execution, "stateGas": state})
    head = int(call(rpc, args.rollup, "blockNumber()(uint256)").split()[0])
    print(f"rollup at L2 block {head}")
    if args.record:
        record(args.record, {
            "type": "advance",
            "l2": {k: v for k, v in bundle.items() if k not in ("blobs", "proof", "triple")},
            "proof": {"kind": "mock", "triple": bundle["triple"], "signature": bundle["proof"]},
            "l1": {
                "txHash": tx_hash,
                "block": int(receipt["blockNumber"], 16),
                "builder": builder.decode(errors="replace"),
                "gasUsed": int(receipt["gasUsed"], 16),
                "blobGasUsed": int(receipt.get("blobGasUsed", "0x0"), 16),
                "blobGasPrice": int(receipt.get("blobGasPrice", "0x0"), 16),
                "frames": frames,
                "rollupHead": head,
            },
        })


def record(path: str, entry: dict) -> None:
    with open(path, "w") as f:
        json.dump(entry, f)


def claim_l2_message(args: argparse.Namespace) -> None:
    rpc = args.rpc
    head_hash = call(rpc, args.rollup, "blockHash()(bytes32)")
    p = l2_node(args, "prove", "--state", args.l2_state, "--head-hash", head_hash, "--index", str(args.index))
    m = p["message"]
    before = int(cast("balance", "--rpc-url", rpc, m["to"]))
    receipt = json.loads(
        cast(
            "send", "--rpc-url", rpc, "--private-key", args.key, "--json", args.rollup, CLAIM_L2_MESSAGE_SIGNATURE,
            f"({m['sender']},{m['to']},{m['value']},{m['data']},{m['index']})",
            str(p["blockNumber"]),
            "[" + ",".join(p["accountProof"]) + "]",
            "[" + ",".join(p["storageProof"]) + "]",
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
    adv = sub.add_parser("advance")
    adv.add_argument("--rpc", required=True)
    adv.add_argument("--submit-rpc", help="the client to send the blob-carrying transaction to, by default --rpc")
    adv.add_argument("--rollup", required=True)
    adv.add_argument("--verifier", required=True)
    adv.add_argument("--operator-key", required=True)
    adv.add_argument("--prover-key", required=True)
    adv.add_argument("--l2-state", required=True, help="the L2 node's state file")
    adv.add_argument("--zkevm-specs", required=True, help="execution-specs projects/zkevm merged with eips/bogota/eip-8141")
    adv.add_argument("--corrupt-proof", action="store_true", help="send an invalid mock proof")
    adv.add_argument("--record", help="write what happened to this JSON file")
    adv.add_argument(
        "--withdraw", action="append", default=[], metavar="FROM:TO:WEI[:DATA]",
        help="send an L2 to L1 message from one of the L2 node's accounts",
    )
    adv.add_argument("--transfer", action="append", default=[], metavar="FROM:TO:WEI", help="send ETH on L2 from one of the L2 node's accounts")
    claim = sub.add_parser("claim-l2-message")
    claim.add_argument("--rpc", required=True)
    claim.add_argument("--rollup", required=True)
    claim.add_argument("--key", required=True, help="the L1 account sending the claim")
    claim.add_argument("--index", type=int, required=True)
    claim.add_argument("--l2-state", required=True, help="the L2 node's state file")
    claim.add_argument("--zkevm-specs", required=True, help="execution-specs projects/zkevm merged with eips/bogota/eip-8141")
    claim.add_argument("--record", help="write what happened to this JSON file")
    args = parser.parse_args()
    if args.command == "check-vectors":
        check_vectors(args.vectors)
    elif args.command == "advance":
        advance(args)
    else:
        claim_l2_message(args)


if __name__ == "__main__":
    main()
