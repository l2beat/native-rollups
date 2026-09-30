// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

uint256 constant DEPTH = 32;

/// @notice An append-only Merkle tree of message hashes, with the root kept
///         in storage so that the other chain can prove it with a single
///         storage proof. Insertion follows the beacon chain deposit
///         contract, with keccak256: it keeps one node per level, so after
///         the first messages it only overwrites existing slots.
/// @dev    `root` must stay the first field: the other chain proves the
///         struct's first slot.
struct Tree {
    bytes32 root;
    uint256 count;
    bytes32[DEPTH] branch;
}

library MessageTree {
    /// @notice Appends `leaf` and updates the root. Returns the leaf's index.
    function insert(Tree storage tree, bytes32 leaf) internal returns (uint256 index) {
        index = tree.count;
        uint256 size = index + 1;
        require(size < 2 ** DEPTH, "tree full");
        tree.count = size;

        // Complete the subtrees the new leaf closes, and keep the resulting
        // node at the first level where the tree is not full.
        bytes32 node = leaf;
        uint256 level;
        while (size & 1 == 0) {
            node = _hash(tree.branch[level], node);
            size >>= 1;
            level++;
        }
        tree.branch[level] = node;

        // Hash up to the root: kept nodes on the left, empty subtrees on the
        // right.
        bytes32 zero;
        for (uint256 i = 0; i < level; i++) {
            zero = _hash(zero, zero);
        }
        node = _hash(node, zero);
        for (uint256 i = level + 1; i < DEPTH; i++) {
            size >>= 1;
            zero = _hash(zero, zero);
            node = size & 1 == 1 ? _hash(tree.branch[i], node) : _hash(node, zero);
        }
        tree.root = node;
    }

    /// @notice The root of a tree holding `leaf` at `index`, given the
    ///         siblings on its path up to the tree's height. Above it, the
    ///         path only has empty subtrees on the right, which need no data.
    function rootFromPath(bytes32 leaf, uint256 index, bytes32[] calldata path) internal pure returns (bytes32) {
        require(path.length <= DEPTH && index >> path.length == 0, "path too short");
        bytes32 node = leaf;
        bytes32 zero;
        for (uint256 i = 0; i < DEPTH; i++) {
            if (i < path.length) {
                node = (index >> i) & 1 == 1 ? _hash(path[i], node) : _hash(node, path[i]);
            } else {
                node = _hash(node, zero);
            }
            zero = _hash(zero, zero);
        }
        return node;
    }

    function _hash(bytes32 left, bytes32 right) private pure returns (bytes32 hash) {
        assembly ("memory-safe") {
            mstore(0x00, left)
            mstore(0x20, right)
            hash := keccak256(0x00, 0x40)
        }
    }
}
