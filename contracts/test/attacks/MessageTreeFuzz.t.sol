// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {DEPTH, MessageTree, Tree} from "../../src/libs/MessageTree.sol";

/// @notice Attack round: the incremental tree and the path check against a
///         tree built in full, for random sizes and leaves.
contract MessageTreeFuzzTest is Test {
    Tree tree;

    function rootFromPath(bytes32 leaf, uint256 index, bytes32[] calldata path) external pure returns (bytes32) {
        return MessageTree.rootFromPath(leaf, index, path);
    }

    /// @dev The levels of the full tree over `leaves`, padded with zeros.
    function _levels(bytes32[] memory leaves) internal pure returns (bytes32[][] memory levels) {
        levels = new bytes32[][](DEPTH + 1);
        levels[0] = leaves;
        for (uint256 d = 0; d < DEPTH; d++) {
            bytes32[] memory below = levels[d];
            bytes32[] memory level = new bytes32[]((below.length + 1) / 2);
            bytes32 zero = _zero(d);
            for (uint256 i = 0; i < level.length; i++) {
                bytes32 right = 2 * i + 1 < below.length ? below[2 * i + 1] : zero;
                level[i] = keccak256(abi.encodePacked(below[2 * i], right));
            }
            levels[d + 1] = level;
        }
    }

    function _zero(uint256 depth) internal pure returns (bytes32 z) {
        for (uint256 i = 0; i < depth; i++) {
            z = keccak256(abi.encodePacked(z, z));
        }
    }

    function _path(bytes32[][] memory levels, uint256 index, uint256 height) internal pure returns (bytes32[] memory p) {
        p = new bytes32[](height);
        for (uint256 d = 0; d < height; d++) {
            uint256 sibling = (index >> d) ^ 1;
            p[d] = sibling < levels[d].length ? levels[d][sibling] : _zero(d);
        }
    }

    function testFuzz_pathsProveEveryLeaf(uint256 seed, uint8 count, uint256 pick) public {
        uint256 n = bound(count, 1, 80);
        bytes32[] memory leaves = new bytes32[](n);
        for (uint256 i = 0; i < n; i++) {
            leaves[i] = keccak256(abi.encode(seed, i));
            assertEq(MessageTree.insert(tree, leaves[i]), i);
        }
        bytes32[][] memory levels = _levels(leaves);
        assertEq(tree.root, levels[DEPTH][0], "incremental root");

        uint256 index = bound(pick, 0, n - 1);
        uint256 height = n == 1 ? 0 : 256 - _clz(n - 1);
        bytes32[] memory path = _path(levels, index, height);
        assertEq(this.rootFromPath(leaves[index], index, path), tree.root, "path");

        // A longer path, padded with empty subtrees, proves the same leaf.
        assertEq(this.rootFromPath(leaves[index], index, _path(levels, index, height + 1)), tree.root, "longer path");

        // Another leaf, or the leaf at another index, does not.
        assertNotEq(this.rootFromPath(keccak256(abi.encode(leaves[index])), index, path), tree.root, "other leaf");
        if (n > 1) {
            uint256 other = (index + 1) % n;
            if (other >> height == 0) {
                assertNotEq(this.rootFromPath(leaves[index], other, path), tree.root, "other index");
            }
        }
    }

    function _clz(uint256 x) internal pure returns (uint256 n) {
        n = 256;
        while (x != 0) {
            x >>= 1;
            n--;
        }
    }
}
