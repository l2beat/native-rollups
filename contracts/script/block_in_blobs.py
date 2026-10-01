"""
EIP-8142 Block-in-Blobs: the canonical encoding of an execution payload's
data (its BAL and transactions) into blobs, and the KZG commitments, cell
proofs, and versioned hashes of those blobs.

Follows the EIP's helpers `execution_payload_data_to_blobs` and
`blobs_to_execution_payload_data`. Used by the L2 node to publish blocks, by
the operator to attach the blobs to `advance`, and by the follower to rebuild
the chain from L1.
"""

import hashlib
import importlib.util
import os

import ckzg
from ethereum_rlp import rlp

FIELD_ELEMENTS_PER_BLOB = 4096
BYTES_PER_FIELD_ELEMENT = 32
USABLE_BYTES_PER_FIELD_ELEMENT = BYTES_PER_FIELD_ELEMENT - 1
USABLE_BYTES_PER_BLOB = FIELD_ELEMENTS_PER_BLOB * USABLE_BYTES_PER_FIELD_ELEMENT
BLOB_BYTES = FIELD_ELEMENTS_PER_BLOB * BYTES_PER_FIELD_ELEMENT

_setup = None


def trusted_setup():
    """The KZG trusted setup that EEST ships."""
    global _setup
    if _setup is None:
        package = os.path.dirname(importlib.util.find_spec("execution_testing").origin)
        _setup = ckzg.load_trusted_setup(os.path.join(package, "test_types", "kzg_trusted_setup.txt"), 0)
    return _setup


def bytes_to_blobs(data: bytes) -> list:
    """Packs 31 bytes into each field element, leaving its first byte zero,
    and zero-pads the last blob."""
    blobs = []
    for offset in range(0, len(data), USABLE_BYTES_PER_BLOB):
        chunk = data[offset : offset + USABLE_BYTES_PER_BLOB].ljust(USABLE_BYTES_PER_BLOB, b"\x00")
        blob = bytearray(BLOB_BYTES)
        for i in range(FIELD_ELEMENTS_PER_BLOB):
            element = chunk[i * USABLE_BYTES_PER_FIELD_ELEMENT : (i + 1) * USABLE_BYTES_PER_FIELD_ELEMENT]
            blob[i * BYTES_PER_FIELD_ELEMENT + 1 : (i + 1) * BYTES_PER_FIELD_ELEMENT] = element
        blobs.append(bytes(blob))
    return blobs


def blobs_to_bytes(blobs: list) -> bytes:
    raw = bytearray()
    for blob in blobs:
        for i in range(FIELD_ELEMENTS_PER_BLOB):
            element = blob[i * BYTES_PER_FIELD_ELEMENT : (i + 1) * BYTES_PER_FIELD_ELEMENT]
            assert element[0] == 0, "invalid blob: first byte must be zero"
            raw.extend(element[1:])
    return bytes(raw)


def execution_payload_data_to_blobs(block_access_list: bytes, transactions: list) -> list:
    """[4-byte BAL length][4-byte transactions length][BAL][RLP(transactions)]."""
    txs = rlp.encode([bytes(tx) for tx in transactions])
    header = len(block_access_list).to_bytes(4, "big") + len(txs).to_bytes(4, "big")
    return bytes_to_blobs(header + block_access_list + txs)


def blobs_to_execution_payload_data(blobs: list) -> tuple:
    raw = blobs_to_bytes(blobs)
    bal_length = int.from_bytes(raw[0:4], "big")
    txs_length = int.from_bytes(raw[4:8], "big")
    assert len(raw) >= 8 + bal_length + txs_length
    block_access_list = raw[8 : 8 + bal_length]
    transactions = rlp.decode(raw[8 + bal_length : 8 + bal_length + txs_length])
    assert not any(raw[8 + bal_length + txs_length :]), "trailing data"
    return block_access_list, [bytes(tx) for tx in transactions]


def payload_data_length(block_access_list: bytes, transactions: list) -> int:
    return 8 + len(block_access_list) + len(rlp.encode([bytes(tx) for tx in transactions]))


def commitment(blob: bytes) -> bytes:
    return bytes(ckzg.blob_to_kzg_commitment(blob, trusted_setup()))


def cell_proofs(blob: bytes) -> list:
    """The EIP-7594 cell proofs that the network wrapper carries."""
    _, proofs = ckzg.compute_cells_and_kzg_proofs(blob, trusted_setup())
    return [bytes(p) for p in proofs]


def versioned_hash(kzg_commitment: bytes) -> bytes:
    return b"\x01" + hashlib.sha256(kzg_commitment).digest()[1:]
