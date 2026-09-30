"""
Operator for a native rollup on an EIP-8141 chain without EIP-8288, such as
frames-devnet-0 (see FramesNativeRollup).

`advance` builds a synthetic L2 block on top of the rollup's head, anchors it
to a recent L1 block, computes the public input root the contract rebuilds,
has the mock prover sign the dependency, and sends one frame transaction:

    frame 0  VERIFY   the operator's account approves execution and payment
    frame 1  DEFAULT  MockDependencyVerifier(scheme || data_hash || vk_hash || proof)
    frame 2  SENDER   rollup.advance(params, 1)

`check-vectors` checks the Python root computation against the
consensus-specs vectors that the Solidity tests use.

Run with the execution-specs `devnets/frames/0` environment, which provides
the frame transaction types, and Foundry's `cast` on the PATH:

    uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py \
        advance --rpc <url> --rollup <address> --verifier <address> \
        --operator-key <key> --prover-key <key>
"""

import argparse
import hashlib
from dataclasses import replace
import json
import os
import subprocess
import time

from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes0, Bytes20, Bytes32
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

LEANSTARK_SCHEME = 0x11
# SSZ root of ExecutionRequests with its five lists empty.
EMPTY_REQUESTS_ROOT = bytes.fromhex(
    "87b69a306c8e430d0857f7c4ac5e27cecffa1108d43c2e5df7388056fea7a423"
)
ADVANCE_SIGNATURE = (
    "advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,"
    "bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)
DEPENDENCY_FRAME_INDEX = 1


# ---------------------------------------------------------------------------
# SSZ roots, mirroring NativeRollupSsz.sol
# ---------------------------------------------------------------------------


def sha(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(a + b).digest()


ZERO = [bytes(32)]
for _ in range(12):
    ZERO.append(sha(ZERO[-1], ZERO[-1]))


def le(value: int, size: int) -> bytes:
    return value.to_bytes(size, "little").ljust(32, b"\0")


def merkleize(chunks: list, depth: int) -> bytes:
    if not chunks:
        return ZERO[depth]
    layer = list(chunks)
    for d in range(depth):
        if len(layer) % 2:
            layer.append(ZERO[d])
        layer = [sha(layer[i], layer[i + 1]) for i in range(0, len(layer), 2)]
    return layer[0]


def progressive(chunks: list) -> bytes:
    subtrees, start, depth = [], 0, 0
    while start < len(chunks):
        subtrees.append(merkleize(chunks[start : start + (1 << depth)], depth))
        start += 1 << depth
        depth += 2
    acc = bytes(32)
    for subtree in reversed(subtrees):
        acc = sha(subtree, acc)
    return acc


def payload_root(h: dict) -> bytes:
    bloom = h["logsBloom"]
    extra = h["extraData"]
    leaves = [
        h["parentHash"],
        h["feeRecipient"].ljust(32, b"\0"),
        h["stateRoot"],
        h["receiptsRoot"],
        merkleize([bloom[i : i + 32] for i in range(0, 256, 32)], 3),
        h["prevRandao"],
        le(h["blockNumber"], 8),
        le(h["gasLimit"], 8),
        le(h["gasUsed"], 8),
        le(h["timestamp"], 8),
        sha(extra.ljust(32, b"\0"), le(len(extra), 32)),
        le(h["baseFeePerGas"], 32),
        h["blockHash"],
        h["transactionsRoot"],
        h["withdrawalsRoot"],
        le(h["blobGasUsed"], 8),
        le(h["excessBlobGas"], 8),
        h["blockAccessListRoot"],
        le(h["slotNumber"], 8),
    ]
    return sha(progressive(leaves), bytes([0xFF, 0xFF, 0x07]).ljust(32, b"\0"))


def versioned_hashes_root(hashes: list) -> bytes:
    return sha(merkleize(hashes, 12), le(len(hashes), 32))


def container4(a: bytes, b: bytes, c: bytes, d: bytes) -> bytes:
    rest = sha(sha(sha(b, c), sha(d, bytes(32))), bytes(32))
    return sha(sha(a, rest), bytes([0x0F]).ljust(32, b"\0"))


def public_input_root(np_root: bytes, chain_id: int, schema_id: int) -> bytes:
    return container4(np_root, b"\x01".ljust(32, b"\0"), le(chain_id, 8), le(schema_id, 2))


def check_vectors(path: str) -> None:
    data = json.load(open(path))
    b = lambda x: bytes.fromhex(x[2:])  # noqa: E731
    for case in data["cases"] + data["chain"]["blocks"]:
        h = {k: (b(v) if isinstance(v, str) and v.startswith("0x") and k != "baseFeePerGas" else v)
             for k, v in case["header"].items()}
        h["baseFeePerGas"] = int(case["header"]["baseFeePerGas"], 16)
        payload = payload_root(h)
        assert payload == b(case["payloadRoot"]), "payload root"
        vh = versioned_hashes_root([b(x) for x in case["versionedHashes"]])
        assert vh == b(case["versionedHashesRoot"]), "versioned hashes root"
        np_root = container4(payload, vh, b(case["parentBeaconBlockRoot"]), b(case["executionRequestsRoot"]))
        assert np_root == b(case["newPayloadRequestRoot"]), "request root"
        pi = public_input_root(np_root, data["chainId"], data["schemaId"])
        assert pi == b(case["publicInputRoot"]), "public input root"
    print(f"all {len(data['cases']) + len(data['chain']['blocks'])} vectors match")


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

    # Rollup state and the registry's current entry.
    parent = bytes.fromhex(call(rpc, args.rollup, "blockHash()(bytes32)")[2:])
    number = int(call(rpc, args.rollup, "blockNumber()(uint256)").split()[0]) + 1
    gas_limit = int(call(rpc, args.rollup, "gasLimit()(uint64)").split()[0])
    l2_chain_id = int(call(rpc, args.rollup, "chainId()(uint64)").split()[0])
    registry = call(rpc, args.rollup, "evmVkRegistry()(address)")
    entry = cast("call", "--rpc-url", rpc, registry, "0x" + "00" * 32)
    vk_hash, schema_id = bytes.fromhex(entry[2:66]), int(entry[66:130], 16)

    # Anchor to a recent L1 block the operator already knows.
    anchor_number = int(cast("block-number", "--rpc-url", rpc)) - 1
    anchor = bytes.fromhex(cast("block", "--rpc-url", rpc, str(anchor_number), "--field", "hash")[2:])

    # A synthetic L2 block: the mock prover attests to its public input.
    header = {
        "parentHash": parent,
        "feeRecipient": os.urandom(20),
        "stateRoot": os.urandom(32),
        "receiptsRoot": os.urandom(32),
        "logsBloom": bytes(256),
        "prevRandao": os.urandom(32),
        "blockNumber": number,
        "gasLimit": gas_limit,
        "gasUsed": 21000,
        "timestamp": int(time.time()),
        "extraData": b"frames-devnet",
        "baseFeePerGas": 7,
        "blockHash": os.urandom(32),
        "transactionsRoot": os.urandom(32),
        "withdrawalsRoot": ZERO[1],
        "blobGasUsed": 0,
        "excessBlobGas": 0,
        "blockAccessListRoot": os.urandom(32),
        "slotNumber": 0,
    }
    np_root = container4(payload_root(header), versioned_hashes_root([]), anchor, EMPTY_REQUESTS_ROOT)
    data_hash = public_input_root(np_root, l2_chain_id, schema_id)

    # The dependency triple, as EIP-8288 frame data, and its mock proof.
    triple = LEANSTARK_SCHEME.to_bytes(32, "big") + data_hash + vk_hash
    digest = bytes.fromhex(cast("call", "--rpc-url", rpc, args.verifier, "digest(bytes)(bytes32)", hx(triple))[2:])
    proof = sign_hash(args.prover_key, digest)
    if args.corrupt_proof:
        proof = bytes([proof[0] ^ 1]) + proof[1:]

    params = (
        f"({hx(header['stateRoot'])},{hx(header['receiptsRoot'])},{hx(header['logsBloom'])},"
        f"{header['gasUsed']},{header['timestamp']},{header['baseFeePerGas']},{hx(header['blockHash'])},"
        f"{hx(header['transactionsRoot'])},{hx(header['blockAccessListRoot'])},0,{hx(EMPTY_REQUESTS_ROOT)},"
        f"{anchor_number},{hx(header['feeRecipient'])},{hx(header['prevRandao'])},{hx(header['extraData'])})"
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

    print(f"L2 block {number}, anchor L1 block {anchor_number}, data_hash {hx(data_hash)}")
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
    adv.add_argument("--corrupt-proof", action="store_true", help="send an invalid mock proof")
    args = parser.parse_args()
    if args.command == "check-vectors":
        check_vectors(args.vectors)
    else:
        advance(args)


if __name__ == "__main__":
    main()
