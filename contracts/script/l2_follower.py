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
import os
import subprocess
import time

from ethereum_rlp import rlp
from ethereum_types.numeric import U64, U256, Uint

from ethereum.crypto.hash import keccak256
from ethereum.merkle_patricia_trie import root
from ethereum.state import EMPTY_CODE_HASH, Address
from ethereum.forks.amsterdam import fork, vm
from ethereum.forks.amsterdam.block_access_lists import BlockAccessListBuilder
from ethereum.forks.amsterdam.blocks import Block
from ethereum.forks.amsterdam.requests import compute_requests_hash
from ethereum.forks.amsterdam.stateless import STATELESS_INPUT_SCHEMA_ID
from ethereum.forks.amsterdam.state_tracker import BlockState
from ethereum.forks.amsterdam.transactions import decode_transaction
from ethereum.forks.amsterdam.transactions.frame_transaction import FrameMode

import block_in_blobs as bib
import explorer as ex
import l2_node
from ssz_roots import container4, payload_root, public_input_root, versioned_hashes_root

BLOCK_ADDED = keccak256(b"BlockAdded(uint64,bytes32)")
# NativeRollup.EMPTY_LIST_ROOT: the SSZ root of no withdrawals.
EMPTY_LIST_ROOT = bytes.fromhex("f5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b")
L1_MESSAGE_SENT = keccak256(b"L1MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")
L2_MESSAGE_CLAIMED = keccak256(b"L2MessageClaimed(uint256,address,address,uint256,uint256,address)")
ADVANCE_SELECTOR = keccak256(
    b"advance((bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,bytes32,uint256,bytes32,uint256,address,bytes32,bytes),uint256)"
)[:4]


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True, timeout=180).stdout.strip()


def rpc(url: str, method: str, *params: str):
    return json.loads(cast("rpc", "--rpc-url", url, method, *params))


def http_get(url: str):
    return json.loads(subprocess.run(["curl", "-sf", "-m", "60", url], check=True, capture_output=True, text=True).stdout)


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
    return l2_node.genesis_chain(json.load(open(state_file)))


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


def write_record(path: str, entry: dict) -> None:
    with open(path + ".tmp", "w") as f:
        json.dump(entry, f)
    os.replace(path + ".tmp", path)


def follow(args: argparse.Namespace) -> None:
    chain = genesis(args.genesis)
    gas_limit = int(cast("call", "--rpc-url", args.l1_rpc, args.rollup, "l2GasLimit()(uint64)").split()[0])
    ex.load_signatures(args.abis)

    def flatten(path: str, name: str) -> str | None:
        """One contract's flat source, from L2BEAT's flattener."""
        out = subprocess.run(
            ["node", args.flatten, args.l2beat, "--file", path, "--name", name], capture_output=True, text=True, timeout=120
        )
        return out.stdout if out.returncode == 0 and out.stdout.strip() else None

    explorer = ex.Explorer(args.explorer, args.rollup, str(l2_node.L2_MESSENGER), flatten) if args.explorer else None
    verified, from_block, seen = [], 0, set()
    while True:
        logs = rpc(args.l1_rpc, "eth_getLogs", json.dumps({
            "address": args.rollup, "fromBlock": hex(from_block), "toBlock": "latest",
            "topics": ["0x" + BLOCK_ADDED.hex()],
        }))
        for log in logs:
            if int(log["topics"][1], 16) <= len(verified):
                continue
            entry, details = rebuild(args, chain, gas_limit, log)
            verified.append(entry)
            from_block = int(log["blockNumber"], 16)
            if explorer:
                index_block(args, explorer, details)
            if args.record:
                write_record(args.record, {"rollup": args.rollup, "verified": verified, "updatedAt": int(time.time())})
        if explorer:
            index_messages(args, explorer, seen)
            explorer.save_index()
            explorer.publish()
        if not args.watch:
            break
        time.sleep(args.interval)
    state_root = cast("call", "--rpc-url", args.l1_rpc, args.rollup, "stateRoot()(bytes32)")
    assert "0x" + bytes(chain.blocks[-1].header.state_root).hex() == state_root
    print(f"followed {len(verified)} L2 blocks from L1 data, state root {state_root} matches the rollup contract")


def l1_tx(args: argparse.Namespace, tx_hash: str) -> dict:
    tx = rpc(args.l1_rpc, "eth_getTransactionByHash", tx_hash)
    receipt = rpc(args.l1_rpc, "eth_getTransactionReceipt", tx_hash)
    block = rpc(args.l1_rpc, "eth_getBlockByNumber", tx["blockNumber"], "false")
    return ex.l1_transaction(tx, receipt, block)


def has_code(state, address: str) -> bool:
    account = state.get_account_optional(Address(bytes.fromhex(address[2:])))
    return account is not None and account.code_hash != EMPTY_CODE_HASH


def proof_input(h, params: dict, advance: dict) -> dict:
    """The roots `NativeRollup.advance` rebuilds from its storage, the
    calldata, BLOBHASH and BLOCKHASH, and the registry, up to the root it
    requires the proof's data hash to equal."""
    b = lambda x: bytes.fromhex(x[2:])  # noqa: E731
    payload = payload_root({
        "parentHash": bytes(h.parent_hash), "blockNumber": int(h.number), "gasLimit": int(h.gas_limit),
        **{k: b(params[k]) for k in (
            "feeRecipient", "stateRoot", "receiptsRoot", "logsBloom", "prevRandao", "extraData", "blockHash",
            "transactionsRoot", "blockAccessListRoot",
        )},
        **{k: int(params[k]) for k in ("gasUsed", "timestamp", "baseFeePerGas")},
        "withdrawalsRoot": EMPTY_LIST_ROOT, "blobGasUsed": 0, "excessBlobGas": 0, "slotNumber": 0,
    })
    hashes = versioned_hashes_root([b(x) for x in advance["blobVersionedHashes"]])
    requests = b(params["executionRequestsRoot"])
    np_root = container4(payload, hashes, bytes(h.parent_beacon_block_root), requests)
    dependency = next(f["dependency"] for f in advance["frames"] if f.get("dependency"))
    return {
        "executionPayloadRoot": ex.hx(payload), "versionedHashesRoot": ex.hx(hashes), "anchor": ex.hx(h.parent_beacon_block_root),
        "executionRequestsRoot": ex.hx(requests), "newPayloadRequestRoot": ex.hx(np_root),
        "chainId": l2_node.L2_CHAIN_ID, "schemaId": int(STATELESS_INPUT_SCHEMA_ID),
        "publicInputRoot": ex.hx(public_input_root(np_root, l2_node.L2_CHAIN_ID, int(STATELESS_INPUT_SCHEMA_ID))),
        "dataHash": dependency["dataHash"], "verificationKeyHash": dependency["verificationKeyHash"],
    }


def index_block(args: argparse.Namespace, explorer: ex.Explorer, d: dict) -> None:
    """Writes a rebuilt L2 block, its transactions and the L1 transaction that carried it."""
    h, output = d["header"], d["output"]
    transactions, previous = [], 0
    for raw, receipt in zip(d["transactions"], ex.receipts(output)):
        gas = int(receipt.cumulative_gas_used) - previous
        previous = int(receipt.cumulative_gas_used)
        transactions.append(ex.l2_transaction(
            raw, receipt, gas, str(l2_node.L2_MESSENGER).lower(), int(h.base_fee_per_gas), lambda a: has_code(d["state"], a)
        ))
    advance = l1_tx(args, d["log"]["transactionHash"])
    params = next(f["call"]["args"]["params"] for f in advance["frames"] if (f.get("call") or {}).get("function") == "advance")
    block = {
        "number": int(h.number), "hash": ex.hx(keccak256(rlp.encode(h))), "parentHash": ex.hx(h.parent_hash),
        "stateRoot": ex.hx(h.state_root), "receiptsRoot": ex.hx(h.receipt_root),
        "transactionsRoot": ex.hx(h.transactions_root), "gasUsed": int(h.gas_used), "gasLimit": int(h.gas_limit),
        "timestamp": int(h.timestamp), "baseFeePerGas": int(h.base_fee_per_gas), "feeRecipient": ex.hx(h.coinbase),
        "prevRandao": ex.hx(h.prev_randao), "extraData": ex.hx(h.extra_data), "withdrawalsRoot": ex.hx(h.withdrawals_root),
        "blobGasUsed": int(h.blob_gas_used), "excessBlobGas": int(h.excess_blob_gas),
        "parentBeaconBlockRoot": ex.hx(h.parent_beacon_block_root), "requestsHash": ex.hx(h.requests_hash),
        "blockAccessListHash": ex.hx(h.block_access_list_hash), "slotNumber": int(h.slot_number),
        "anchorBlockNumber": params["anchorBlockNumber"], "balBytes": len(d["bal"]),
        "payloadBytes": bib.payload_data_length(d["bal"], d["transactions"]),
        "sszRoots": {k: params[k] for k in ("transactionsRoot", "blockAccessListRoot", "executionRequestsRoot")},
        "l1": {"tx": advance["hash"], "block": advance["block"], "blobVersionedHashes": advance["blobVersionedHashes"]},
        "recordedHash": d["recordedHash"],
        "proofInput": proof_input(h, params, advance),
    }
    explorer.add_l2_block(block, transactions)
    explorer.update_accounts(d["state"], block, transactions)
    explorer.add_l1_tx(advance, "advance", l2Block=block["number"])


def index_messages(args: argparse.Namespace, explorer: ex.Explorer, seen: set) -> None:
    """Indexes the L1 transactions that send deposits and claim withdrawals."""
    for topic, kind in ((L1_MESSAGE_SENT, "deposit"), (L2_MESSAGE_CLAIMED, "withdrawal claim")):
        for log in rpc(args.l1_rpc, "eth_getLogs", json.dumps({
            "address": args.rollup, "fromBlock": "0x0", "toBlock": "latest", "topics": ["0x" + topic.hex()],
        })):
            if log["transactionHash"] not in seen:
                seen.add(log["transactionHash"])
                explorer.add_l1_tx(l1_tx(args, log["transactionHash"]), kind)


def rebuild(args: argparse.Namespace, chain: fork.BlockChain, gas_limit: int, log: dict) -> tuple:
    """Rebuilds and applies the L2 block that `log` announces."""
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

    h = l2_node.header({
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
        f"and a {len(bal)}-byte BAL from {len(blobs)} blob(s), re-executed, hash 0x{rebuilt_hash.hex()} matches",
        flush=True,
    )
    details = {
        "header": h, "output": output, "transactions": transactions, "bal": bal, "log": log, "state": chain.state,
        "recordedHash": "0x" + recorded_hash.hex(),
    }
    return {
        "number": number,
        "l1Block": int(log["blockNumber"], 16),
        "l1Tx": log["transactionHash"],
        "blockHash": "0x" + rebuilt_hash.hex(),
        "stateRoot": "0x" + bytes(h.state_root).hex(),
        "transactions": len(transactions),
        "balBytes": len(bal),
        "blobs": len(blobs),
        "match": True,
    }, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l1-rpc", required=True)
    parser.add_argument("--beacon", required=True, help="a consensus-layer API serving /eth/v1/beacon/blobs")
    parser.add_argument("--rollup", required=True)
    parser.add_argument("--genesis", required=True, help="the L2 node's state file, for the genesis configuration")
    parser.add_argument("--watch", action="store_true", help="keep following new L2 blocks")
    parser.add_argument("--interval", type=float, default=4, help="seconds between polls with --watch")
    parser.add_argument("--record", help="keep the verified blocks in this JSON file")
    parser.add_argument("--explorer", help="write the decoded blocks and transactions to this directory")
    parser.add_argument(
        "--abis", nargs="*", default=[os.path.join(os.path.dirname(__file__), "..", "out")],
        help="directories with ABIs, from JSON artifacts or Go bindings, to decode calls and events with",
    )
    parser.add_argument("--l2beat", default=os.path.expanduser("~/work/l2beat"), help="for L2BEAT's flattener")
    parser.add_argument(
        "--flatten", default=os.path.join(os.path.dirname(__file__), "..", "..", "demo", "flatten.mjs"),
        help="the script that runs L2BEAT's flattener",
    )
    follow(parser.parse_args())


if __name__ == "__main__":
    main()
