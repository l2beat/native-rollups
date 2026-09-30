"""
Reference roots for NativeRollupSsz.

Builds Gloas execution payloads and EIP-8025 public inputs with the compiled
consensus-specs types, and prints the leaves the rollup contract receives
together with every root it must rebuild. The payload root is computed from
the full payload, so matching it from the leaves also checks that summaries
(transactions, withdrawals and block access list roots) preserve the root.
Run with a consensus-specs checkout that has been built (`make build`):

    uv run --project <consensus-specs> python script/native_rollup_vectors.py \
        > test/native_rollup_vectors.json
"""

import json
import random

from eth_consensus_specs.eip8025 import mainnet as spec

rng = random.Random(8079)

CHAIN_ID = 8079
SCHEMA_ID = 0x1501
GAS_LIMIT = 60_000_000


def rand_bytes(n: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(n))


def hx(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def build(
    n_txs: int,
    bal_len: int,
    extra_len: int,
    n_blobs: int,
    requests: bool,
    parent_hash: bytes | None = None,
    block_number: int | None = None,
    gas_limit: int | None = None,
) -> dict:
    payload = spec.ExecutionPayload(
        parent_hash=spec.Hash32(parent_hash or rand_bytes(32)),
        fee_recipient=spec.ExecutionAddress(rand_bytes(20)),
        state_root=spec.Bytes32(rand_bytes(32)),
        receipts_root=spec.Bytes32(rand_bytes(32)),
        logs_bloom=spec.LogsBloom.decode_bytes(rand_bytes(256)),
        prev_randao=spec.Bytes32(rand_bytes(32)),
        block_number=spec.Uint64(rng.getrandbits(40) if block_number is None else block_number),
        gas_limit=spec.Uint64(rng.getrandbits(30) if gas_limit is None else gas_limit),
        gas_used=spec.Uint64(rng.getrandbits(29)),
        timestamp=spec.Uint64(rng.getrandbits(34)),
        extra_data=spec.ExtraData.decode_bytes(rand_bytes(extra_len)),
        base_fee_per_gas=spec.Uint256(rng.getrandbits(80)),
        block_hash=spec.Hash32(rand_bytes(32)),
        transactions=spec.Transactions(
            data=[
                spec.Transaction.decode_bytes(rand_bytes(rng.randrange(1, 400)))
                for _ in range(n_txs)
            ]
        ),
        withdrawals=spec.Withdrawals(),
        blob_gas_used=spec.Uint64(0),
        excess_blob_gas=spec.Uint64(0),
        block_access_list=spec.BlockAccessList.decode_bytes(rand_bytes(bal_len)),
        slot_number=spec.Uint64(0),
    )
    versioned_hashes = spec.VersionedHashes(
        data=[spec.VersionedHash(b"\x01" + rand_bytes(31)) for _ in range(n_blobs)]
    )
    execution_requests = spec.ExecutionRequests()
    if requests:
        execution_requests = spec.ExecutionRequests(
            withdrawals=spec.WithdrawalRequests(
                data=[
                    spec.WithdrawalRequest(
                        source_address=spec.ExecutionAddress(rand_bytes(20)),
                        validator_pubkey=spec.BLSPubkey(rand_bytes(48)),
                        amount=spec.Gwei(rng.getrandbits(40)),
                    )
                ]
            )
        )
    request = spec.NewPayloadRequest(
        execution_payload=payload,
        versioned_hashes=versioned_hashes,
        parent_beacon_block_root=spec.Root(rand_bytes(32)),
        execution_requests=execution_requests,
    )
    npr_root = spec.hash_tree_root(request)
    public_input = spec.PublicInput(
        new_payload_request_root=spec.Root(npr_root),
        successful_validation=spec.Boolean(True),
        chain_id=spec.Uint64(CHAIN_ID),
        schema_id=spec.Uint16(SCHEMA_ID),
    )
    return {
        "header": {
            "parentHash": hx(payload.parent_hash),
            "feeRecipient": hx(payload.fee_recipient),
            "stateRoot": hx(payload.state_root),
            "receiptsRoot": hx(payload.receipts_root),
            "logsBloom": hx(payload.logs_bloom.encode_bytes()),
            "prevRandao": hx(payload.prev_randao),
            "blockNumber": int(payload.block_number),
            "gasLimit": int(payload.gas_limit),
            "gasUsed": int(payload.gas_used),
            "timestamp": int(payload.timestamp),
            "extraData": hx(payload.extra_data.encode_bytes()),
            "baseFeePerGas": hex(int(payload.base_fee_per_gas)),
            "blockHash": hx(payload.block_hash),
            "transactionsRoot": hx(spec.hash_tree_root(payload.transactions)),
            "withdrawalsRoot": hx(spec.hash_tree_root(payload.withdrawals)),
            "blobGasUsed": int(payload.blob_gas_used),
            "excessBlobGas": int(payload.excess_blob_gas),
            "blockAccessListRoot": hx(spec.hash_tree_root(payload.block_access_list)),
            "slotNumber": int(payload.slot_number),
        },
        "versionedHashes": [hx(h) for h in versioned_hashes.data],
        "parentBeaconBlockRoot": hx(request.parent_beacon_block_root),
        "executionRequestsRoot": hx(spec.hash_tree_root(execution_requests)),
        "payloadRoot": hx(spec.hash_tree_root(payload)),
        "versionedHashesRoot": hx(spec.hash_tree_root(request.versioned_hashes)),
        "newPayloadRequestRoot": hx(npr_root),
        "publicInputRoot": hx(spec.hash_tree_root(public_input)),
    }


def main() -> None:
    cases = [
        build(n_txs=0, bal_len=0, extra_len=0, n_blobs=0, requests=False),
        build(n_txs=3, bal_len=300, extra_len=7, n_blobs=1, requests=False),
        build(n_txs=40, bal_len=5000, extra_len=32, n_blobs=6, requests=True),
        build(n_txs=1, bal_len=31, extra_len=31, n_blobs=17, requests=False),
    ]
    # A chain the rollup contract advances through: each block extends the
    # previous one, with the gas limit and slot number the contract fixes.
    genesis_hash = rand_bytes(32)
    genesis_state_root = rand_bytes(32)
    blocks = []
    parent = genesis_hash
    for number, shape in enumerate(
        [(2, 100, 0, 1, False), (5, 800, 12, 3, True), (0, 0, 32, 0, False)], start=1
    ):
        block = build(*shape, parent_hash=parent, block_number=number, gas_limit=GAS_LIMIT)
        block["payloadBlobCount"] = len(block["versionedHashes"])
        blocks.append(block)
        parent = bytes.fromhex(block["header"]["blockHash"][2:])
    print(
        json.dumps(
            {
                "chainId": CHAIN_ID,
                "schemaId": SCHEMA_ID,
                "emptyListRoot": hx(spec.hash_tree_root(spec.Withdrawals())),
                "cases": cases,
                "chain": {
                    "genesisHash": hx(genesis_hash),
                    "genesisStateRoot": hx(genesis_state_root),
                    "gasLimit": GAS_LIMIT,
                    "blocks": blocks,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
