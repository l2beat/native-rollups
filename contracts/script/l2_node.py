"""
A minimal L2 node and mock prover for the native rollup.

It builds real L2 blocks under the L1 stateless validation program's rules
(execution-specs `projects/zkevm`, Amsterdam), validates each block with
`run_stateless_guest`, and signs the EIP-8288 dependency only for blocks the
program accepts, as a stand-in for a zkVM proof of that program.

Blocks are built with EEST's `BlockchainTest`, which is deterministic, so the
node only stores the inputs of each block and rebuilds the chain from genesis.
The genesis holds the system contracts the Specification requires, the
`L2Messenger` predeploy with the pre-minted gas token supply, and one funded
L2 account. The node follows the rollup contract on L1: each block claims
the L1 messages sent up to its anchor, with proofs against that anchor, then
fills up with random transfers from the funded account, which also pays for
the claims.

Run with the execution-specs `projects/zkevm` environment:

    uv run --project <execution-specs@projects/zkevm> python script/l2_node.py genesis --state <file> --l1-rollup <address>
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

L2_MESSENGER = Address(0x8079000000000000000000000000000000000001)
PREMINT = 10**27
MESSENGER_ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "out", "L2Messenger.sol", "L2Messenger.json")
CLAIM_SIGNATURE = "claimL1Message((address,address,uint256,bytes,uint256),uint256,bytes,bytes[],bytes[])"
CLAIM_GAS_LIMIT = 3_000_000
L1_MESSAGE_SENT = keccak256(b"L1MessageSent(uint256,address,address,uint256,bytes)")
QUEUE_SLOT = 5  # NativeRollup.pendingL1Messages
CLAIMED_SLOT = 1  # L2Messenger.claimed


def user() -> EOA:
    return EOA(key=USER_KEY)


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True).stdout.strip()


def build_chain(state: dict, specs: list) -> dict:
    """Builds the chain from genesis and returns the fixture as JSON."""
    sender = user()
    blocks = []
    for spec in specs:
        claims = [
            Transaction(
                sender=sender,
                to=L2_MESSENGER,
                data=bytes.fromhex(claim["calldata"][2:]),
                gas_limit=CLAIM_GAS_LIMIT,
                chain_id=L2_CHAIN_ID,
                max_fee_per_gas=10**9,
                max_priority_fee_per_gas=1,
            )
            for claim in spec["claims"]
        ]
        transfers = [
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
                txs=claims + transfers,
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
        pre=Alloc(
            {
                user(): Account(balance=10**24),
                L2_MESSENGER: Account(
                    code=bytes.fromhex(state["messengerCode"][2:]),
                    balance=PREMINT,
                    storage={0: int(state["l1Rollup"], 16)},
                ),
            }
        ),
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


def storage(fixture: dict, address: Address, slot: bytes) -> int:
    account = {k.lower(): v for k, v in fixture["postState"].items()}[str(address).lower()]
    return next((int(v, 16) for k, v in account["storage"].items() if int(k, 16) == int.from_bytes(slot, "big")), 0)


def balance(fixture: dict, address: str) -> int:
    account = {k.lower(): v for k, v in fixture["postState"].items()}.get(address.lower())
    return int(account["balance"], 16) if account else 0


def l1_messages(args: argparse.Namespace, first_index: int, timestamp: int) -> list:
    """Claims of the L1 messages from `first_index` sent up to the anchor,
    proven against the anchor, which the block stores at `timestamp`."""
    rpc = ("--rpc-url", args.l1_rpc)
    logs = json.loads(
        cast(
            "rpc", *rpc, "eth_getLogs",
            json.dumps({
                "address": args.rollup,
                "fromBlock": "0x0",
                "toBlock": hex(args.anchor_number),
                "topics": ["0x" + L1_MESSAGE_SENT.hex()],
            }),
        )
    )
    header = json.loads(cast("rpc", *rpc, "debug_getRawHeader", hex(args.anchor_number)))
    claims = []
    for log in logs:
        index = int(log["topics"][1], 16)
        if index < first_index:
            continue
        data = bytes.fromhex(log["data"][2:])
        value = int.from_bytes(data[0:32], "big")
        offset = int.from_bytes(data[32:64], "big")
        length = int.from_bytes(data[offset : offset + 32], "big")
        payload = data[offset + 32 : offset + 32 + length]
        message = {
            "index": index,
            "sender": "0x" + log["topics"][2][-40:],
            "to": "0x" + log["topics"][3][-40:],
            "value": value,
        }
        slot = int.from_bytes(keccak256(QUEUE_SLOT.to_bytes(32, "big")), "big") + index
        proof = json.loads(
            cast("rpc", *rpc, "eth_getProof", args.rollup, json.dumps([f"0x{slot:064x}"]), hex(args.anchor_number))
        )
        message["calldata"] = cast(
            "calldata", CLAIM_SIGNATURE,
            f"({message['sender']},{message['to']},{value},0x{payload.hex()},{index})",
            str(timestamp),
            header,
            "[" + ",".join(proof["accountProof"]) + "]",
            "[" + ",".join(proof["storageProof"][0]["proof"]) + "]",
        )
        claims.append(message)
    return claims


def load(path: str) -> dict:
    return json.load(open(path))


def genesis(args: argparse.Namespace) -> None:
    code = json.load(open(MESSENGER_ARTIFACT))["deployedBytecode"]["object"]
    state = {"l1Rollup": args.l1_rollup, "messengerCode": code, "blocks": [], "pending": None}
    json.dump(state, open(args.state, "w"), indent=2)
    header = build_chain(state, [])["genesisBlockHeader"]
    print(json.dumps({"genesisHash": header["hash"], "genesisStateRoot": header["stateRoot"]}))


def build(args: argparse.Namespace) -> None:
    state = load(args.state)

    # Keep the pending block only if the rollup accepted it.
    if state["pending"] is not None:
        if head(build_chain(state, state["blocks"] + [state["pending"]]))[0] == args.head_hash:
            state["blocks"].append(state["pending"])
        state["pending"] = None
    fixture = build_chain(state, state["blocks"])
    head_hash, head_number, head_timestamp = head(fixture)
    if head_hash != args.head_hash:
        raise SystemExit(f"L2 node at {head_hash}, rollup at {args.head_hash}")

    timestamp = max(int(time.time()), head_timestamp + 1)
    claimed = sum(len(b["claims"]) for b in state["blocks"])
    spec = {
        "timestamp": timestamp,
        "anchorHash": args.anchor_hash,
        "claims": l1_messages(args, claimed, timestamp),
        "transfers": [("0x" + os.urandom(20).hex(), int.from_bytes(os.urandom(2), "big")) for _ in range(2)],
    }
    fixture = build_chain(state, state["blocks"] + [spec])
    block = fixture["blocks"][-1]
    for claim in spec["claims"]:
        key = keccak256(claim["index"].to_bytes(32, "big") + CLAIMED_SLOT.to_bytes(32, "big"))
        if storage(fixture, L2_MESSENGER, key) != 1:
            raise SystemExit(f"the claim of L1 message {claim['index']} failed")

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
    proof = cast("wallet", "sign", "--no-hash", "--private-key", args.prover_key, "0x" + digest.hex())

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
                "claims": [
                    {k: c[k] for k in ("index", "sender", "to", "value")} | {"l2Balance": balance(fixture, c["to"])}
                    for c in spec["claims"]
                ],
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
    gen.add_argument("--l1-rollup", required=True, help="the rollup contract's address, which the messenger trusts")
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
    b.add_argument("--l1-rpc", required=True)
    b.add_argument("--rollup", required=True)
    args = parser.parse_args()
    genesis(args) if args.command == "genesis" else build(args)


if __name__ == "__main__":
    main()
