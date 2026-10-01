"""
SSZ roots the native rollup contract rebuilds, mirroring NativeRollupSsz.sol:
the Gloas ExecutionPayload root from its leaves, the bounded versioned hashes
list, and the progressive NewPayloadRequest and EIP-8025 PublicInput roots.
Checked against the consensus-specs vectors by `frames_operator.py
check-vectors`. Only uses the standard library, so both the operator and the
L2 node can import it.
"""

import hashlib
import json

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
