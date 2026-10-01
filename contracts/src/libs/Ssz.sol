// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice SSZ hash-tree-root primitives: sha256 merkleization over memory
///         words, EIP-7916 progressive lists, and EIP-7495 progressive
///         containers. Trees are hashed in place.
library Ssz {
    // sha256 zero-subtree roots: ZERO_i is the root of 2^i zero chunks.
    bytes32 internal constant ZERO_1 = 0xf5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b;
    bytes32 internal constant ZERO_2 = 0xdb56114e00fdd4c1f85c892bf35ac9a89289aaecb1ebd0a96cde606a748b5d71;
    bytes32 internal constant ZERO_3 = 0xc78009fdf07fc56a11f122370658a353aaa542ed63e44c4bc15ff4cd105ab33c;
    bytes32 internal constant ZERO_4 = 0x536d98837f2dd165a55d5eeae91485954472d56f246df256bf3cae19352a123c;
    bytes32 internal constant ZERO_5 = 0x9efde052aa15429fae05bad4d0b1d7c64da64d03d7a1854a588c2cb8430c0d30;
    bytes32 internal constant ZERO_6 = 0xd88ddfeed400a8755596b21942c1497e114c302e6118290f91e6772976041fa1;
    bytes32 internal constant ZERO_7 = 0x87eb0ddba57e35f6d286673802a4af5975e22506c7cf4c64bb6be5ee11527f2c;
    bytes32 internal constant ZERO_8 = 0x26846476fd5fc54a5d43385167c95144f2643f533cc85bb9d16b782f8d7db193;
    bytes32 internal constant ZERO_9 = 0x506d86582d252405b840018792cad2bf1259f1ef5aa5f887e13cb2f0094f51e1;
    bytes32 internal constant ZERO_10 = 0xffff0ad7e659772f9534c195c815efc4014ef1e1daed4404c06385d11192e92b;
    bytes32 internal constant ZERO_11 = 0x6cf04127db05441cd833107a52be852868890e4317e6a02ab47683aa75964220;
    bytes32 internal constant ZERO_12 = 0xb7d05f875f140027ef5118a2247bbb84ce8f2f0f1123623085daf7960c329f5f;

    function sha(bytes32 a, bytes32 b) internal view returns (bytes32 r) {
        assembly ("memory-safe") {
            mstore(0x00, a)
            mstore(0x20, b)
            if iszero(staticcall(gas(), 0x02, 0x00, 0x40, 0x00, 0x20)) { revert(0, 0) }
            r := mload(0x00)
        }
    }

    function zeroHash(uint256 depth) internal pure returns (bytes32 z) {
        assembly ("memory-safe") {
            switch depth
            case 0 { z := 0 }
            case 1 { z := ZERO_1 }
            case 2 { z := ZERO_2 }
            case 3 { z := ZERO_3 }
            case 4 { z := ZERO_4 }
            case 5 { z := ZERO_5 }
            case 6 { z := ZERO_6 }
            case 7 { z := ZERO_7 }
            case 8 { z := ZERO_8 }
            case 9 { z := ZERO_9 }
            case 10 { z := ZERO_10 }
            case 11 { z := ZERO_11 }
            case 12 { z := ZERO_12 }
            default { revert(0, 0) }
        }
    }

    /// @notice Little-endian encoding of a uint16 in a 32-byte chunk.
    function le16(uint16 v) internal pure returns (bytes32) {
        return bytes32(uint256((v >> 8) | (v << 8)) << 240);
    }

    /// @notice Little-endian encoding of a uint64 in a 32-byte chunk.
    function le64(uint64 v) internal pure returns (bytes32) {
        uint256 x = v;
        x = ((x & 0xFF00FF00FF00FF00) >> 8) | ((x & 0x00FF00FF00FF00FF) << 8);
        x = ((x & 0xFFFF0000FFFF0000) >> 16) | ((x & 0x0000FFFF0000FFFF) << 16);
        x = (x >> 32) | ((x & 0xFFFFFFFF) << 32);
        return bytes32(x << 192);
    }

    /// @notice Little-endian encoding of a uint256 in a 32-byte chunk.
    function le256(uint256 v) internal pure returns (bytes32) {
        v = ((v >> 8) & 0x00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff)
            | ((v & 0x00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff) << 8);
        v = ((v >> 16) & 0x0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff)
            | ((v & 0x0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff) << 16);
        v = ((v >> 32) & 0x00000000ffffffff00000000ffffffff00000000ffffffff00000000ffffffff)
            | ((v & 0x00000000ffffffff00000000ffffffff00000000ffffffff00000000ffffffff) << 32);
        v = ((v >> 64) & 0x0000000000000000ffffffffffffffff0000000000000000ffffffffffffffff)
            | ((v & 0x0000000000000000ffffffffffffffff0000000000000000ffffffffffffffff) << 64);
        return bytes32((v >> 128) | (v << 128));
    }

    /// @notice Root of the `count` words at `ptr`, zero-padded to 2^depth
    ///         leaves. Overwrites the words.
    function merkleizeInPlace(uint256 ptr, uint256 count, uint256 depth) internal view returns (bytes32 root) {
        if (count == 0) return zeroHash(depth);
        uint256 n = count;
        for (uint256 d = 0; d < depth; d++) {
            bytes32 z = zeroHash(d);
            assembly ("memory-safe") {
                let m := shr(1, add(n, 1))
                for { let i := 0 } lt(i, m) { i := add(i, 1) } {
                    let left := add(ptr, shl(6, i))
                    mstore(0x00, mload(left))
                    switch lt(add(shl(1, i), 1), n)
                    case 1 { mstore(0x20, mload(add(left, 0x20))) }
                    default { mstore(0x20, z) }
                    if iszero(staticcall(gas(), 0x02, 0x00, 0x40, add(ptr, shl(5, i)), 0x20)) {
                        revert(0, 0)
                    }
                }
                n := m
            }
        }
        assembly ("memory-safe") {
            root := mload(ptr)
        }
    }

    /// @notice Progressive merkleization of the `count` words at `ptr`:
    ///         subtrees of 1, 4, 16, ... leaves, each paired with the
    ///         progressive root of the remaining leaves. Overwrites the words.
    function progressiveInPlace(uint256 ptr, uint256 count) internal view returns (bytes32 acc) {
        bytes32[6] memory subtrees;
        uint256 k;
        uint256 start;
        uint256 depth;
        while (start < count) {
            uint256 size = 1 << depth;
            uint256 n = count - start < size ? count - start : size;
            subtrees[k++] = merkleizeInPlace(ptr + 32 * start, n, depth);
            start += size;
            depth += 2;
        }
        for (uint256 i = k; i > 0; i--) {
            acc = sha(subtrees[i - 1], acc);
        }
    }
}
