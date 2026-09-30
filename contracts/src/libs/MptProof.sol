// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @notice Verifies Merkle Patricia Trie proofs in `eth_getProof` form and
///         reads RLP, both directly from calldata, without copying or
///         decoding whole nodes into memory.
/// @dev    Every node below the root must be referenced by its hash. Nodes
///         whose RLP is shorter than 32 bytes are embedded in their parent
///         instead, and proofs through them revert. With 32-byte hashed keys,
///         only a leaf deep in a very large storage trie holding a small value
///         could be that short.
library MptProof {
    /// @notice The value stored under `key`, a 32-byte hashed path, in the
    ///         trie with root `root`.
    function get(bytes32 root, bytes32 key, bytes[] calldata proof) internal pure returns (bytes calldata value) {
        bytes32 expected = root;
        uint256 depth; // nibbles of `key` consumed so far
        for (uint256 i = 0; i < proof.length; i++) {
            bytes calldata node = proof[i];
            require(_keccak(node) == expected, "MPT: invalid node");
            (uint256 start, uint256 length,, bool isList) = item(node, 0);
            require(isList && start + length == node.length, "MPT: invalid node");

            (uint256 pathStart, uint256 pathLength, uint256 next,) = item(node, start);
            (uint256 secondStart, uint256 secondLength, uint256 end,) = item(node, next);
            if (end == node.length) {
                // Leaf or extension: [hex-prefix path, value or child].
                require(pathLength > 0, "MPT: invalid node");
                uint256 flags = uint8(node[pathStart]) >> 4;
                require(flags < 4, "MPT: invalid node");
                uint256 nibbles = 2 * pathLength - 2 + (flags & 1);
                require(depth + nibbles <= 64, "MPT: key not found");
                require(_pathMatches(node, pathStart, flags & 1, nibbles, key, depth), "MPT: key not found");
                depth += nibbles;
                if (flags >= 2) {
                    require(depth == 64, "MPT: key not found");
                    return node[secondStart:secondStart + secondLength];
                }
                expected = _hashReference(node, secondStart, secondLength);
            } else {
                // Branch: 16 children and a value, which hashed keys never use.
                require(depth < 64, "MPT: invalid node");
                uint256 target = _nibble(key, depth);
                (uint256 childStart, uint256 childLength) = (pathStart, pathLength);
                if (target == 1) {
                    (childStart, childLength) = (secondStart, secondLength);
                } else if (target > 1) {
                    next = end;
                    for (uint256 n = 2; n < target; n++) {
                        next = _skipChild(node, next);
                    }
                    (childStart, childLength,,) = item(node, next);
                }
                require(childLength > 0, "MPT: key not found");
                expected = _hashReference(node, childStart, childLength);
                depth++;
            }
        }
        revert("MPT: incomplete proof");
    }

    /// @notice The content of item `index` of the RLP list `list`.
    function listItem(bytes calldata list, uint256 index) internal pure returns (bytes calldata) {
        (uint256 start, uint256 length,, bool isList) = item(list, 0);
        require(isList && start + length == list.length, "RLP: not a list");
        uint256 next = start;
        uint256 itemStart;
        uint256 itemLength;
        for (uint256 i = 0; i <= index; i++) {
            (itemStart, itemLength, next,) = item(list, next);
        }
        require(next <= start + length, "RLP: invalid list");
        return list[itemStart:itemStart + itemLength];
    }

    /// @notice The unsigned integer an RLP string holds, as storage values are
    ///         encoded.
    function toUint(bytes calldata encoded) internal pure returns (uint256 value) {
        (uint256 start, uint256 length, uint256 end, bool isList) = item(encoded, 0);
        require(!isList && end == encoded.length && length <= 32, "RLP: not an integer");
        for (uint256 i = start; i < end; i++) {
            value = (value << 8) | uint8(encoded[i]);
        }
    }

    /// @notice Content offset and length of the RLP item at `offset`, the
    ///         offset right after it, and whether it is a list.
    function item(bytes calldata data, uint256 offset)
        internal
        pure
        returns (uint256 start, uint256 length, uint256 next, bool isList)
    {
        require(offset < data.length, "RLP: out of bounds");
        uint256 prefix = uint8(data[offset]);
        if (prefix < 0x80) {
            (start, length) = (offset, 1);
        } else if (prefix < 0xb8) {
            (start, length) = (offset + 1, prefix - 0x80);
        } else if (prefix < 0xc0) {
            uint256 lengthBytes = prefix - 0xb7;
            (start, length) = (offset + 1 + lengthBytes, _bigEndian(data, offset + 1, lengthBytes));
        } else if (prefix < 0xf8) {
            (start, length, isList) = (offset + 1, prefix - 0xc0, true);
        } else {
            uint256 lengthBytes = prefix - 0xf7;
            (start, length, isList) = (offset + 1 + lengthBytes, _bigEndian(data, offset + 1, lengthBytes), true);
        }
        next = start + length;
        require(next <= data.length, "RLP: out of bounds");
    }

    /// @dev Whether the `nibbles` nibbles of a hex-prefix path starting at
    ///      `pathStart` equal those of `key` from nibble `depth`, compared as
    ///      left-aligned words. Odd paths keep their first nibble in the
    ///      flag byte, even paths start after it.
    function _pathMatches(
        bytes calldata node,
        uint256 pathStart,
        uint256 odd,
        uint256 nibbles,
        bytes32 key,
        uint256 depth
    ) private pure returns (bool) {
        if (nibbles == 0) return true;
        uint256 path;
        assembly ("memory-safe") {
            let at := add(node.offset, pathStart)
            switch odd
            case 1 { path := shl(4, calldataload(at)) }
            default { path := calldataload(add(at, 1)) }
        }
        uint256 mask = nibbles == 64 ? type(uint256).max : ~(type(uint256).max >> (4 * nibbles));
        return (path ^ (uint256(key) << (4 * depth))) & mask == 0;
    }

    /// @dev Offset after the branch child at `offset`: usually empty or a
    ///      32-byte hash.
    function _skipChild(bytes calldata node, uint256 offset) private pure returns (uint256) {
        require(offset < node.length, "RLP: out of bounds");
        uint256 prefix = uint8(node[offset]);
        if (prefix == 0x80) return offset + 1;
        if (prefix == 0xa0) return offset + 33;
        (,, uint256 next,) = item(node, offset);
        return next;
    }

    /// @dev A child reference that is a 32-byte hash, the only kind supported.
    function _hashReference(bytes calldata node, uint256 start, uint256 length) private pure returns (bytes32) {
        require(length == 32 && uint8(node[start - 1]) == 0xa0, "MPT: embedded node");
        return bytes32(node[start:start + 32]);
    }

    function _nibble(bytes32 key, uint256 index) private pure returns (uint256) {
        return (uint8(key[index / 2]) >> (4 * (1 - index % 2))) & 0xf;
    }

    function _bigEndian(bytes calldata data, uint256 offset, uint256 length) private pure returns (uint256 value) {
        require(length <= 8 && offset + length <= data.length, "RLP: out of bounds");
        for (uint256 i = 0; i < length; i++) {
            value = (value << 8) | uint8(data[offset + i]);
        }
    }

    function _keccak(bytes calldata data) private pure returns (bytes32 hash) {
        assembly ("memory-safe") {
            let ptr := mload(0x40)
            calldatacopy(ptr, data.offset, data.length)
            hash := keccak256(ptr, data.length)
        }
    }
}
