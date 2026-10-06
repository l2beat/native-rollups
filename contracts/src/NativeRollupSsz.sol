// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ssz} from "./libs/Ssz.sol";

/// @notice The roots a native rollup contract rebuilds to check an L2 proof,
///         following the consensus-specs types: the Gloas `ExecutionPayload`
///         and `NewPayloadRequest` (EIP-7688 progressive containers) and the
///         EIP-8025 `PublicInput`. See the book's Specification.
library NativeRollupSsz {
    /// @notice An `ExecutionPayload` with its variable-size lists replaced by
    ///         their roots, which leaves the payload root unchanged.
    struct ExecutionPayloadHeader {
        bytes32 parentHash;
        address feeRecipient;
        bytes32 stateRoot;
        bytes32 receiptsRoot;
        bytes logsBloom; // 256 bytes
        bytes32 prevRandao;
        uint64 blockNumber;
        uint64 gasLimit;
        uint64 gasUsed;
        uint64 timestamp;
        bytes extraData; // at most 32 bytes
        uint256 baseFeePerGas;
        bytes32 blockHash;
        bytes32 transactionsRoot;
        bytes32 withdrawalsRoot;
        uint64 blobGasUsed;
        uint64 excessBlobGas;
        bytes32 blockAccessListRoot;
        uint64 slotNumber;
    }

    // Active-fields bitvectors of the progressive containers.
    bytes32 internal constant PAYLOAD_ACTIVE_FIELDS = bytes32(hex"ffff07"); // 19 fields
    bytes32 internal constant FOUR_ACTIVE_FIELDS = bytes32(hex"0f");

    // `VersionedHashes` is `List[VersionedHash, MAX_BLOB_COMMITMENTS_PER_BLOCK]`,
    // a bounded list of 2^12 chunks.
    uint256 internal constant VERSIONED_HASHES_DEPTH = 12;

    function executionPayloadRoot(ExecutionPayloadHeader memory h) internal view returns (bytes32) {
        bytes32[19] memory f;
        f[0] = h.parentHash;
        f[1] = bytes32(bytes20(h.feeRecipient));
        f[2] = h.stateRoot;
        f[3] = h.receiptsRoot;
        f[4] = logsBloomRoot(h.logsBloom);
        f[5] = h.prevRandao;
        f[6] = Ssz.le64(h.blockNumber);
        f[7] = Ssz.le64(h.gasLimit);
        f[8] = Ssz.le64(h.gasUsed);
        f[9] = Ssz.le64(h.timestamp);
        f[10] = extraDataRoot(h.extraData);
        f[11] = Ssz.le256(h.baseFeePerGas);
        f[12] = h.blockHash;
        f[13] = h.transactionsRoot;
        f[14] = h.withdrawalsRoot;
        f[15] = Ssz.le64(h.blobGasUsed);
        f[16] = Ssz.le64(h.excessBlobGas);
        f[17] = h.blockAccessListRoot;
        f[18] = Ssz.le64(h.slotNumber);
        uint256 ptr;
        assembly ("memory-safe") {
            ptr := f
        }
        return Ssz.sha(Ssz.progressiveInPlace(ptr, 19), PAYLOAD_ACTIVE_FIELDS);
    }

    /// @notice Root of the 256-byte `LogsBloom` vector: eight chunks.
    function logsBloomRoot(bytes memory bloom) internal view returns (bytes32) {
        require(bloom.length == 256, "logs bloom length");
        bytes32[8] memory chunks;
        uint256 ptr;
        assembly ("memory-safe") {
            ptr := chunks
            mcopy(ptr, add(bloom, 0x20), 256)
        }
        return Ssz.merkleizeInPlace(ptr, 8, 3);
    }

    /// @notice Root of `ExtraData`, a `ByteList[32]`: one chunk and its length.
    function extraDataRoot(bytes memory extra) internal view returns (bytes32) {
        uint256 len = extra.length;
        require(len <= 32, "extra data length");
        bytes32 chunk;
        assembly ("memory-safe") {
            chunk := mload(add(extra, 0x20))
        }
        if (len < 32) chunk &= ~bytes32(type(uint256).max >> (8 * len));
        return Ssz.sha(chunk, Ssz.le256(len));
    }

    /// @notice Root of `VersionedHashes`.
    function versionedHashesRoot(bytes32[] memory hashes) internal view returns (bytes32) {
        uint256 n = hashes.length;
        require(n <= 1 << VERSIONED_HASHES_DEPTH, "too many versioned hashes");
        bytes32[] memory copy = new bytes32[](n);
        uint256 ptr;
        assembly ("memory-safe") {
            ptr := add(copy, 0x20)
            mcopy(ptr, add(hashes, 0x20), shl(5, n))
        }
        return Ssz.sha(Ssz.merkleizeInPlace(ptr, n, VERSIONED_HASHES_DEPTH), Ssz.le256(n));
    }

    function newPayloadRequestRoot(
        bytes32 payloadRoot,
        bytes32 versionedHashesRoot_,
        bytes32 parentBeaconBlockRoot,
        bytes32 executionRequestsRoot
    ) internal view returns (bytes32) {
        return progressiveContainer4(payloadRoot, versionedHashesRoot_, parentBeaconBlockRoot, executionRequestsRoot);
    }

    /// @notice Root of the EIP-8025 `PublicInput` of a successful validation.
    function publicInputRoot(bytes32 newPayloadRequestRoot_, uint64 chainId, uint16 schemaId)
        internal
        view
        returns (bytes32)
    {
        bytes32 successful = bytes32(uint256(1) << 248);
        return progressiveContainer4(newPayloadRequestRoot_, successful, Ssz.le64(chainId), Ssz.le16(schemaId));
    }

    function progressiveContainer4(bytes32 a, bytes32 b, bytes32 c, bytes32 d) private view returns (bytes32) {
        // Progressive subtrees of 1 and 4 leaves: [a] and [b, c, d, 0].
        bytes32 rest = Ssz.sha(Ssz.sha(Ssz.sha(b, c), Ssz.sha(d, bytes32(0))), bytes32(0));
        return Ssz.sha(Ssz.sha(a, rest), FOUR_ACTIVE_FIELDS);
    }
}
