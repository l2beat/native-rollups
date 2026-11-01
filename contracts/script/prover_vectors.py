"""
Reference roots for requests that the EIP-8025 provers commit to.

The fixtures are the SSZ-encoded Gloas `NewPayloadRequest`s of eth-act/lighthouse
63a526aa (beacon_node/proof_engine/tests/fixtures/new_payload_gloas), generated
with ere-guests v0.17.0, the guest code the ethrex and reth provers run. One is
glamsterdam-devnet-8 block 93354, the request inside zkboost's proving input
(eth-act/zkboost 7f99d70, crates/server/tests/fixture/stateless_input_amsterdam.ssz).

Decodes each request with the execution-specs projects/zkevm types, which
match the Gloas consensus types since execution-specs #3913, and prints the
leaves the rollup contract receives, the execution-specs root, and the root
ere-guests computed. Run with an environment that has remerkleable:

    python -I script/prover_vectors.py <execution-specs projects/zkevm src> \
        <fixtures dir> > test/prover_vectors.json
"""

import json
import sys

sys.path.insert(0, sys.argv[1])
FIX = sys.argv[2]

from ethereum.crypto.hash import Hash32  # noqa: E402
from ethereum.forks.amsterdam.execution_engine.types import NewPayloadRequest  # noqa: E402
from ethereum.forks.amsterdam.stateless import StatelessValidationResult  # noqa: E402
from ethereum.utils.ssz import _to_view  # noqa: E402
from ethereum_types.numeric import U16, U64  # noqa: E402

SCHEMA_ID = 0x1501  # Amsterdam, revision 1

# Roots that ere-guests v0.17.0 computed for each fixture (Lighthouse
# beacon_node/proof_engine/tests/new_payload_request_vectors.rs).
GUEST_ROOTS = {
    "block_93354": "8c3a890206a189727e151767653f846ccddbd269eb29fb0a2f97371f23a481c6",
    "populated": "1e3a96403f54a092c167d2fc3493bb0127dbe1258491a3bd2486206d6cf1324a",
    "empty_lists": "57a969c82f7f9ae4290a3f5170a255f4e62a1c5ddc4edc5d1d131087fa118e23",
}


def hx(b) -> str:
    return "0x" + bytes(b).hex()


def case(name: str, chain_id: int) -> dict:
    data = open(f"{FIX}/{name}.ssz", "rb").read()
    request = NewPayloadRequest.decode_bytes(data)
    view = _to_view(request)
    p = request.execution_payload
    pv = view.execution_payload
    return {
        "name": name,
        "header": {
            "parentHash": hx(p.parent_hash),
            "feeRecipient": hx(p.fee_recipient),
            "stateRoot": hx(p.state_root),
            "receiptsRoot": hx(p.receipts_root),
            "logsBloom": hx(p.logs_bloom),
            "prevRandao": hx(p.prev_randao),
            "blockNumber": int(p.block_number),
            "gasLimit": int(p.gas_limit),
            "gasUsed": int(p.gas_used),
            "timestamp": int(p.timestamp),
            "extraData": hx(p.extra_data),
            "baseFeePerGas": hex(int(p.base_fee_per_gas)),
            "blockHash": hx(p.block_hash),
            "transactionsRoot": hx(pv.transactions.hash_tree_root()),
            "withdrawalsRoot": hx(pv.withdrawals.hash_tree_root()),
            "blobGasUsed": int(p.blob_gas_used),
            "excessBlobGas": int(p.excess_blob_gas),
            "blockAccessListRoot": hx(pv.block_access_list.hash_tree_root()),
            "slotNumber": int(p.slot_number),
        },
        "counts": {
            "transactions": len(p.transactions),
            "withdrawals": len(p.withdrawals),
            "blockAccessListBytes": len(p.block_access_list),
        },
        "versionedHashes": [hx(h) for h in request.versioned_hashes],
        "parentBeaconBlockRoot": hx(request.parent_beacon_block_root),
        "executionRequestsRoot": hx(view.execution_requests.hash_tree_root()),
        "payloadRoot": hx(pv.hash_tree_root()),
        "versionedHashesRoot": hx(view.versioned_hashes.hash_tree_root()),
        "specRoot": hx(view.hash_tree_root()),
        "guestRoot": "0x" + GUEST_ROOTS[name],
        "publicInputRoot": hx(
            StatelessValidationResult(
                new_payload_request_root=Hash32(view.hash_tree_root()),
                successful_validation=True,
                chain_id=U64(chain_id),
                schema_id=U16(SCHEMA_ID),
            ).hash_tree_root()
        ),
    }


def zkboost_input() -> tuple:
    """The schema id, request and chain id of zkboost's proving input."""
    raw = open(f"{FIX}/stateless_input_amsterdam.ssz", "rb").read()
    body = raw[2:]  # 2-byte schema id, then (request, witness, chain_id, ...)
    start = int.from_bytes(body[0:4], "little")
    end = int.from_bytes(body[4:8], "little")
    chain_id = int.from_bytes(body[8:16], "little")
    return raw[:2], body[start:end], chain_id


def main() -> None:
    schema, req, chain_id = zkboost_input()
    block = open(f"{FIX}/block_93354.ssz", "rb").read()
    print(
        json.dumps(
            {
                "zkboostSchemaId": hx(schema),
                "zkboostRequestMatchesBlock93354": req == block,
                "chainId": chain_id,
                "schemaId": SCHEMA_ID,
                "cases": [case(n, chain_id) for n in GUEST_ROOTS],
            },
            indent=2,
        )
    )


main()
