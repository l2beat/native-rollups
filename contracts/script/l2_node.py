"""
A minimal L2 node and mock prover for the native rollup.

It builds real L2 blocks under the L1 stateless validation program's rules
(execution-specs `projects/zkevm`, Amsterdam), validates each block with
`run_stateless_guest`, and signs the EIP-8288 dependency only for blocks the
program accepts, as a stand-in for a zkVM proof of that program.

Blocks are built with EEST's `BlockchainTest`, which is deterministic, so the
node only stores the inputs of each block and rebuilds the chain from genesis.
The genesis holds the system contracts the Specification requires and one
funded L2 account, whose transfers fill the blocks.

Run with the execution-specs `projects/zkevm` environment:

    uv run --project <execution-specs@projects/zkevm> python script/l2_node.py genesis --state <file>
    uv run --project <execution-specs@projects/zkevm> python script/l2_node.py build --state <file> ...

`build` prints a JSON bundle that `frames_operator.py` submits.
"""

import argparse
import json
import os
import subprocess
import time

from execution_testing import EOA, Account, Address, Alloc, Environment, Hash, Transaction
from execution_testing.client_clis import ExecutionSpecsTransitionTool
from execution_testing.fixtures.blockchain import BlockchainFixture
from execution_testing.forks import Amsterdam
from execution_testing.specs.blockchain import Block, BlockchainTest

from ethereum.crypto.hash import keccak256
from ethereum.forks.amsterdam.stateless import STATELESS_INPUT_SCHEMA_ID
from ethereum.forks.amsterdam.stateless_guest import deserialize_stateless_input, run_stateless_guest
from ethereum.forks.amsterdam.stateless_host import deserialize_stateless_output
from ethereum.utils.ssz import _to_view

from ssz_roots import container4, payload_root, public_input_root, versioned_hashes_root  # noqa: E402

L2_CHAIN_ID = 8079
L2_GAS_LIMIT = 60_000_000
LEANSTARK_SCHEME = 0x11
USER_KEY = 0x6E61746976652D726F6C6C75702D75736572  # "native-rollup-user"
FEE_RECIPIENT = Address(0xFEE)


def user() -> EOA:
    return EOA(key=USER_KEY)


def build_chain(specs: list) -> dict:
    """Builds the chain from genesis and returns the fixture as JSON."""
    sender = user()
    blocks = []
    for spec in specs:
        txs = [
            Transaction(
                sender=sender,
                to=Address(int(to, 16)),
                value=value,
                gas_limit=21_000,
                chain_id=L2_CHAIN_ID,
                max_fee_per_gas=10**9,
                max_priority_fee_per_gas=1,
            )
            for to, value in spec["transfers"]
        ]
        blocks.append(
            Block(
                txs=txs,
                timestamp=spec["timestamp"],
                parent_beacon_block_root=Hash(spec["anchorHash"]),
                slot_number=0,
                fee_recipient=FEE_RECIPIENT,
                prev_randao=Hash(keccak256(bytes.fromhex(spec["anchorHash"][2:]))),
                extra_data=b"native-rollup",
            )
        )
    test = BlockchainTest(
        fork=Amsterdam,
        pre=Alloc({user(): Account(balance=10**24)}),
        post={},
        chain_id=L2_CHAIN_ID,
        genesis_environment=Environment(gas_limit=L2_GAS_LIMIT),
        blocks=blocks,
    )
    result = test.generate(t8n=ExecutionSpecsTransitionTool(), fixture_format=BlockchainFixture)
    return result.fixture.model_dump(mode="json", by_alias=True)


def head(fixture: dict) -> tuple:
    header = fixture["blocks"][-1]["blockHeader"] if fixture["blocks"] else fixture["genesisBlockHeader"]
    return header["hash"], int(header["number"], 16), int(header["timestamp"], 16)


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"blocks": [], "pending": None}
    return json.load(open(path))


def genesis(args: argparse.Namespace) -> None:
    state = load(args.state)
    json.dump(state, open(args.state, "w"), indent=2)
    header = build_chain([])["genesisBlockHeader"]
    print(json.dumps({"genesisHash": header["hash"], "genesisStateRoot": header["stateRoot"]}))


def build(args: argparse.Namespace) -> None:
    state = load(args.state)

    # Keep the pending block only if the rollup accepted it.
    if state["pending"] is not None:
        if head(build_chain(state["blocks"] + [state["pending"]]))[0] == args.head_hash:
            state["blocks"].append(state["pending"])
        state["pending"] = None
    fixture = build_chain(state["blocks"])
    head_hash, head_number, head_timestamp = head(fixture)
    if head_hash != args.head_hash:
        raise SystemExit(f"L2 node at {head_hash}, rollup at {args.head_hash}")

    spec = {
        "timestamp": max(int(time.time()), head_timestamp + 1),
        "anchorHash": args.anchor_hash,
        "transfers": [("0x" + os.urandom(20).hex(), int.from_bytes(os.urandom(2), "big")) for _ in range(2)],
    }
    block = build_chain(state["blocks"] + [spec])["blocks"][-1]

    # Validate the block with the L1 stateless validation program.
    input_bytes = bytes.fromhex(block["statelessInputBytes"][2:])
    output = deserialize_stateless_output(run_stateless_guest(input_bytes))
    if not output.successful_validation:
        raise SystemExit("the stateless program rejected the block")
    if int(output.chain_id) != L2_CHAIN_ID or int(output.schema_id) != args.schema_id:
        raise SystemExit("unexpected chain ID or schema ID")
    assert STATELESS_INPUT_SCHEMA_ID == args.schema_id

    # The public input root the rollup contract rebuilds, from the request
    # the program validated.
    request = deserialize_stateless_input(input_bytes).new_payload_request
    payload = request.execution_payload
    view = _to_view(payload)
    header = {
        "parentHash": bytes(payload.parent_hash),
        "feeRecipient": bytes(payload.fee_recipient),
        "stateRoot": bytes(payload.state_root),
        "receiptsRoot": bytes(payload.receipts_root),
        "logsBloom": bytes(payload.logs_bloom),
        "prevRandao": bytes(payload.prev_randao),
        "blockNumber": int(payload.block_number),
        "gasLimit": int(payload.gas_limit),
        "gasUsed": int(payload.gas_used),
        "timestamp": int(payload.timestamp),
        "extraData": bytes(payload.extra_data),
        "baseFeePerGas": int(payload.base_fee_per_gas),
        "blockHash": bytes(payload.block_hash),
        "transactionsRoot": bytes(view.transactions.hash_tree_root()),
        "withdrawalsRoot": bytes(view.withdrawals.hash_tree_root()),
        "blobGasUsed": int(payload.blob_gas_used),
        "excessBlobGas": int(payload.excess_blob_gas),
        "blockAccessListRoot": bytes(view.block_access_list.hash_tree_root()),
        "slotNumber": int(payload.slot_number),
    }
    assert payload_root(header) == bytes(view.hash_tree_root()), "payload root"
    assert "0x" + bytes(request.parent_beacon_block_root).hex() == args.anchor_hash
    requests_root = bytes(_to_view(request.execution_requests).hash_tree_root())
    np_root = container4(
        payload_root(header),
        versioned_hashes_root([bytes(h) for h in request.versioned_hashes]),
        bytes(request.parent_beacon_block_root),
        requests_root,
    )
    data_hash = public_input_root(np_root, L2_CHAIN_ID, args.schema_id)

    # Sign the dependency, as MockDependencyVerifier.digest expects.
    triple = LEANSTARK_SCHEME.to_bytes(32, "big") + data_hash + bytes.fromhex(args.vk_hash[2:])
    digest = keccak256(args.l1_chain_id.to_bytes(32, "big") + bytes.fromhex(args.verifier[2:]) + triple)
    proof = subprocess.run(
        ["cast", "wallet", "sign", "--no-hash", "--private-key", args.prover_key, "0x" + digest.hex()],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    state["pending"] = spec
    json.dump(state, open(args.state, "w"), indent=2)
    hx = lambda b: "0x" + b.hex()  # noqa: E731
    print(
        json.dumps(
            {
                "number": header["blockNumber"],
                "blockHash": hx(header["blockHash"]),
                "stateRoot": hx(header["stateRoot"]),
                "transactions": len(payload.transactions),
                "params": {
                    "stateRoot": hx(header["stateRoot"]),
                    "receiptsRoot": hx(header["receiptsRoot"]),
                    "logsBloom": hx(header["logsBloom"]),
                    "gasUsed": header["gasUsed"],
                    "timestamp": header["timestamp"],
                    "baseFeePerGas": header["baseFeePerGas"],
                    "blockHash": hx(header["blockHash"]),
                    "transactionsRoot": hx(header["transactionsRoot"]),
                    "blockAccessListRoot": hx(header["blockAccessListRoot"]),
                    "payloadBlobCount": len(request.versioned_hashes),
                    "executionRequestsRoot": hx(requests_root),
                    "anchorBlockNumber": args.anchor_number,
                    "feeRecipient": hx(header["feeRecipient"]),
                    "prevRandao": hx(header["prevRandao"]),
                    "extraData": hx(header["extraData"]),
                },
                "triple": hx(triple),
                "proof": proof,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("genesis")
    gen.add_argument("--state", required=True)
    b = sub.add_parser("build")
    b.add_argument("--state", required=True)
    b.add_argument("--head-hash", required=True)
    b.add_argument("--anchor-number", type=int, required=True)
    b.add_argument("--anchor-hash", required=True)
    b.add_argument("--schema-id", type=int, required=True)
    b.add_argument("--vk-hash", required=True)
    b.add_argument("--l1-chain-id", type=int, required=True)
    b.add_argument("--verifier", required=True)
    b.add_argument("--prover-key", required=True)
    args = parser.parse_args()
    genesis(args) if args.command == "genesis" else build(args)


if __name__ == "__main__":
    main()
