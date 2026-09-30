"""
Operator for a native rollup on an EIP-8141 chain without EIP-8288, such as
frames-devnet-0 (see FramesNativeRollup).

`advance` anchors the next L2 block to a recent L1 block, has the L2 node
(`l2_node.py`) build it on the rollup's head, validate it with the L1
stateless validation program, and sign the dependency, then sends one frame
transaction:

    frame 0  VERIFY   the operator's account approves execution and payment
    frame 1  DEFAULT  MockDependencyVerifier(scheme || data_hash || vk_hash || proof)
    frame 2  SENDER   rollup.advance(params, 1)

`check-vectors` checks the Python root computation against the
consensus-specs vectors that the Solidity tests use.

Run with the execution-specs `devnets/frames/0` environment, which provides
the frame transaction types, and Foundry's `cast` on the PATH:

    uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py \
        advance --rpc <url> --rollup <address> --verifier <address> \
        --operator-key <key> --prover-key <key> \
        --l2-state <file> --zkevm-specs <execution-specs@projects/zkevm>
"""

import argparse
from dataclasses import replace
import json
import os
import subprocess

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

from ssz_roots import check_vectors  # noqa: E402

ADVANCE_SIGNATURE = (
    "advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,"
    "bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)
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


def advance(args: argparse.Namespace) -> None:
    rpc = args.rpc
    operator = cast("wallet", "address", "--private-key", args.operator_key)

    # Rollup head and the registry's current entry.
    head_hash = call(rpc, args.rollup, "blockHash()(bytes32)")
    registry = call(rpc, args.rollup, "evmVkRegistry()(address)")
    entry = cast("call", "--rpc-url", rpc, registry, "0x" + "00" * 32)
    vk_hash, schema_id = "0x" + entry[2:66], int(entry[66:130], 16)

    # Anchor to a recent L1 block the operator already knows.
    anchor_number = int(cast("block-number", "--rpc-url", rpc)) - 1
    anchor_hash = cast("block", "--rpc-url", rpc, str(anchor_number), "--field", "hash")

    # The L2 node builds the next block on the rollup's head, validates it
    # with the stateless program, and signs the dependency.
    node = subprocess.run(
        [
            "uv", "run", "--project", args.zkevm_specs, "python",
            os.path.join(os.path.dirname(__file__), "l2_node.py"), "build",
            "--state", args.l2_state,
            "--head-hash", head_hash,
            "--anchor-number", str(anchor_number),
            "--anchor-hash", anchor_hash,
            "--schema-id", str(schema_id),
            "--vk-hash", vk_hash,
            "--l1-chain-id", cast("chain-id", "--rpc-url", rpc),
            "--verifier", args.verifier,
            "--prover-key", args.prover_key,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    bundle = json.loads(node.stdout.strip().splitlines()[-1])
    p = bundle["params"]
    triple = bytes.fromhex(bundle["triple"][2:])
    proof = bytes.fromhex(bundle["proof"][2:])
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
            max_fee_per_blob_gas=U256(0),
        ),
        blob_versioned_hashes=(),
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

    print(
        f"L2 block {bundle['number']} ({bundle['transactions']} transactions, state root {bundle['stateRoot']}), "
        f"anchor L1 block {anchor_number}, data_hash 0x{triple[32:64].hex()}"
    )
    tx_hash = json.loads(cast("publish", "--rpc-url", rpc, hx(raw)))["transactionHash"]
    print(f"included {tx_hash}")
    receipt = json.loads(cast("receipt", "--rpc-url", rpc, tx_hash, "--json"))
    for i, frame in enumerate(receipt.get("frameReceipts", [])):
        print(f"frame {i}: status {int(frame['status'], 16)}, gas {int(frame['gasUsed'], 16)}")
    head = int(call(rpc, args.rollup, "blockNumber()(uint256)").split()[0])
    print(f"rollup at L2 block {head}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-vectors")
    check.add_argument("--vectors", default="test/native_rollup_vectors.json")
    adv = sub.add_parser("advance")
    adv.add_argument("--rpc", required=True)
    adv.add_argument("--rollup", required=True)
    adv.add_argument("--verifier", required=True)
    adv.add_argument("--operator-key", required=True)
    adv.add_argument("--prover-key", required=True)
    adv.add_argument("--l2-state", required=True, help="the L2 node's state file")
    adv.add_argument("--zkevm-specs", required=True, help="an execution-specs checkout of projects/zkevm")
    adv.add_argument("--corrupt-proof", action="store_true", help="send an invalid mock proof")
    args = parser.parse_args()
    if args.command == "check-vectors":
        check_vectors(args.vectors)
    else:
        advance(args)


if __name__ == "__main__":
    main()
