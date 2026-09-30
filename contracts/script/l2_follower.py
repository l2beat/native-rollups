"""
An L2 node that follows the rollup from L1 alone: it tests that the data a
native rollup publishes is enough to rebuild its chain without the operator.

For each `BlockAdded` event of the rollup contract, it reads the `advance`
frame of the transaction that emitted it for the header fields, fetches the
transaction's EIP-8142 payload blobs from the consensus layer for the BAL and
transactions, and applies the block with execution-specs' `state_transition`,
which re-executes it and checks every root in the header. The rebuilt
block's hash must equal the one the rollup contract recorded.

Two header fields are not in L1 data and come from re-execution: the
EIP-7685 `requests_hash`, since `advance` only carries the SSZ root of the
requests, and the MPT transactions root, since it carries their SSZ root.

The genesis is the L2 node's, as the rollup's public configuration.

    uv run --project <execution-specs> python script/l2_follower.py \\
        --l1-rpc <rpc> --beacon <url> --rollup <address> --genesis <l2-state>
"""

import argparse
import json
import subprocess
import typing

from ethereum_rlp import rlp
from ethereum_types.numeric import U64, U256, Uint

from ethereum.crypto.hash import keccak256
from ethereum.merkle_patricia_trie import root
from ethereum.state import Account, Address
from ethereum.state_mpt import State, set_account, set_storage, store_code
from ethereum.forks.amsterdam import fork, vm
from ethereum.forks.amsterdam.block_access_lists import BlockAccessListBuilder
from ethereum.forks.amsterdam.blocks import Block, Header
from ethereum.forks.amsterdam.requests import compute_requests_hash
from ethereum.forks.amsterdam.state_tracker import BlockState
from ethereum.forks.amsterdam.transactions import decode_transaction
from ethereum.forks.amsterdam.transactions.frame_transaction import FrameMode

import block_in_blobs as bib
import l2_node

BLOCK_ADDED = keccak256(b"BlockAdded(uint64,bytes32)")
ADVANCE_SELECTOR = keccak256(
    b"advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)[:4]


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True).stdout.strip()


def rpc(url: str, method: str, *params: str):
    return json.loads(cast("rpc", "--rpc-url", url, method, *params))


def http_get(url: str):
    return json.loads(subprocess.run(["curl", "-sf", url], check=True, capture_output=True, text=True).stdout)


def header(fields: dict) -> Header:
    """A `Header` from field values, converted to the field types."""
    types = typing.get_type_hints(Header)
    return Header(**{name: types[name](value) for name, value in fields.items()})


def decode_advance(data: bytes) -> dict:
    """The `BlockParams` of an `advance` call."""
    assert data[:4] == ADVANCE_SELECTOR, "not an advance call"
    args = data[4:]
    params = args[int.from_bytes(args[0:32], "big") :]
    word = lambda i: params[32 * i : 32 * i + 32]  # noqa: E731
    number = lambda i: int.from_bytes(word(i), "big")  # noqa: E731

    def dynamic(i: int) -> bytes:
        offset = number(i)
        return params[offset + 32 : offset + 32 + int.from_bytes(params[offset : offset + 32], "big")]

    return {
        "stateRoot": word(0), "receiptsRoot": word(1), "logsBloom": dynamic(2), "gasUsed": number(3),
        "timestamp": number(4), "baseFeePerGas": number(5), "blockHash": word(6), "payloadBlobCount": number(9),
        "anchorBlockNumber": number(11), "feeRecipient": word(12)[12:], "prevRandao": word(13),
        "extraData": dynamic(14),
    }


def genesis(state_file: str) -> fork.BlockChain:
    fixture = l2_node.build_chain(json.load(open(state_file)), [])
    state = State()
    for address, account in fixture["pre"].items():
        address = Address(bytes.fromhex(address[2:]))
        code_hash = store_code(state, bytes.fromhex(account["code"][2:]))
        set_account(state, address, Account(Uint(int(account["nonce"], 16)), U256(int(account["balance"], 16)), code_hash))
        for key, value in account["storage"].items():
            set_storage(state, address, int(key, 16).to_bytes(32, "big"), U256(int(value, 16)))
    g = fixture["genesisBlockHeader"]
    names = {
        "parent_hash": "parentHash", "ommers_hash": "uncleHash", "coinbase": "coinbase", "state_root": "stateRoot",
        "transactions_root": "transactionsTrie", "receipt_root": "receiptTrie", "bloom": "bloom",
        "difficulty": "difficulty", "number": "number", "gas_limit": "gasLimit", "gas_used": "gasUsed",
        "timestamp": "timestamp", "extra_data": "extraData", "prev_randao": "mixHash", "nonce": "nonce",
        "base_fee_per_gas": "baseFeePerGas", "withdrawals_root": "withdrawalsRoot", "blob_gas_used": "blobGasUsed",
        "excess_blob_gas": "excessBlobGas", "parent_beacon_block_root": "parentBeaconBlockRoot",
        "requests_hash": "requestsHash", "block_access_list_hash": "blockAccessListHash", "slot_number": "slotNumber",
    }
    types = typing.get_type_hints(Header)
    genesis_header = header({
        name: (bytes.fromhex(g[key][2:]) if issubclass(types[name], bytes) else int(g[key], 16))
        for name, key in names.items()
    })
    assert "0x" + keccak256(rlp.encode(genesis_header)).hex() == g["hash"], "genesis hash"
    return fork.BlockChain(
        blocks=[Block(header=genesis_header, transactions=(), ommers=(), withdrawals=())],
        state=state,
        chain_id=U64(l2_node.L2_CHAIN_ID),
    )


def payload_blobs(args: argparse.Namespace, l1_block: dict, versioned_hashes: list) -> list:
    """The blobs with `versioned_hashes`, from the consensus layer."""
    genesis_time = int(http_get(f"{args.beacon}/eth/v1/beacon/genesis")["data"]["genesis_time"])
    seconds_per_slot = int(http_get(f"{args.beacon}/eth/v1/config/spec")["data"]["SECONDS_PER_SLOT"])
    slot = (int(l1_block["timestamp"], 16) - genesis_time) // seconds_per_slot
    by_hash = {}
    for blob in http_get(f"{args.beacon}/eth/v1/beacon/blobs/{slot}")["data"]:
        blob = bytes.fromhex(blob[2:])
        by_hash[bib.versioned_hash(bib.commitment(blob))] = blob
    return [by_hash[h] for h in versioned_hashes]


def follow(args: argparse.Namespace) -> None:
    chain = genesis(args.genesis)
    gas_limit = int(cast("call", "--rpc-url", args.l1_rpc, args.rollup, "gasLimit()(uint64)").split()[0])
    logs = rpc(args.l1_rpc, "eth_getLogs", json.dumps({
        "address": args.rollup, "fromBlock": "0x0", "toBlock": "latest", "topics": ["0x" + BLOCK_ADDED.hex()],
    }))
    for log in logs:
        number = int(log["topics"][1], 16)
        recorded_hash = bytes.fromhex(log["data"][2:66])

        # Header fields from the advance frame, the rest from the blobs.
        tx = decode_transaction(bytes.fromhex(rpc(args.l1_rpc, "eth_getRawTransactionByHash", log["transactionHash"])[2:]))
        frame = next(f for f in tx.frames if f.mode == FrameMode.SENDER and bytes(f.to) == bytes.fromhex(args.rollup[2:]))
        p = decode_advance(bytes(frame.data))
        l1_block = rpc(args.l1_rpc, "eth_getBlockByNumber", log["blockNumber"], "false")
        blobs = payload_blobs(args, l1_block, [bytes(h) for h in tx.blob_versioned_hashes[: p["payloadBlobCount"]]])
        bal, transactions = bib.blobs_to_execution_payload_data(blobs)
        anchor = bytes.fromhex(rpc(args.l1_rpc, "eth_getBlockByNumber", hex(p["anchorBlockNumber"]), "false")["hash"][2:])

        # Re-execute once for the fields that L1 only has as SSZ roots.
        parent = chain.blocks[-1].header
        block_env = vm.BlockEnvironment(
            chain_id=chain.chain_id,
            state=BlockState(pre_state=chain.state),
            block_gas_limit=Uint(gas_limit),
            block_hashes=fork.get_last_256_block_hashes(chain),
            coinbase=Address(p["feeRecipient"]),
            number=parent.number + Uint(1),
            base_fee_per_gas=Uint(p["baseFeePerGas"]),
            time=U256(p["timestamp"]),
            prev_randao=p["prevRandao"],
            excess_blob_gas=U64(0),
            parent_beacon_block_root=anchor,
            block_access_list_builder=BlockAccessListBuilder(),
            slot_number=U64(0),
        )
        output = fork.apply_body(block_env, tuple(transactions), ())

        h = header({
            "parent_hash": keccak256(rlp.encode(parent)), "ommers_hash": fork.EMPTY_OMMER_HASH,
            "coinbase": p["feeRecipient"], "state_root": p["stateRoot"],
            "transactions_root": root(output.transactions_trie), "receipt_root": p["receiptsRoot"],
            "bloom": p["logsBloom"], "difficulty": 0, "number": int(parent.number) + 1, "gas_limit": gas_limit,
            "gas_used": p["gasUsed"], "timestamp": p["timestamp"], "extra_data": p["extraData"],
            "prev_randao": p["prevRandao"], "nonce": bytes(8), "base_fee_per_gas": p["baseFeePerGas"],
            "withdrawals_root": root(output.withdrawals_trie), "blob_gas_used": 0, "excess_blob_gas": 0,
            "parent_beacon_block_root": anchor, "requests_hash": compute_requests_hash(output.requests),
            "block_access_list_hash": keccak256(bal), "slot_number": 0,
        })
        fork.state_transition(chain, Block(header=h, transactions=tuple(transactions), ommers=(), withdrawals=()))
        rebuilt_hash = keccak256(rlp.encode(h))
        assert rebuilt_hash == recorded_hash == p["blockHash"], f"L2 block {number}: rebuilt hash differs"
        print(
            f"L2 block {number} rebuilt from L1 block {int(log['blockNumber'], 16)}: {len(transactions)} transactions "
            f"and a {len(bal)}-byte BAL from {len(blobs)} blob(s), re-executed, hash 0x{rebuilt_hash.hex()} matches"
        )
    state_root = cast("call", "--rpc-url", args.l1_rpc, args.rollup, "stateRoot()(bytes32)")
    assert "0x" + bytes(chain.blocks[-1].header.state_root).hex() == state_root
    print(f"followed {len(logs)} L2 blocks from L1 data, state root {state_root} matches the rollup contract")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l1-rpc", required=True)
    parser.add_argument("--beacon", required=True, help="a consensus-layer API serving /eth/v1/beacon/blobs")
    parser.add_argument("--rollup", required=True)
    parser.add_argument("--genesis", required=True, help="the L2 node's state file, for the genesis configuration")
    follow(parser.parse_args())


if __name__ == "__main__":
    main()
