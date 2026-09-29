// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal test stand-in for the rollup contract. The inbox queries
///         `stateRootHistory(l2BlockNumber)` at admission to anchor account
///         proofs and `blockNumber()` to enforce that the proof is recent;
///         tests publish roots via `setStateRoot`, which also advances
///         `blockNumber` to the highest published L2 block. This stands in
///         for what a real `NativeRollup.advance` would do after `PROOFROOT`
///         verifies the underlying state transition.
contract MockRollup {
    mapping(uint256 => bytes32) public stateRootHistory;

    /// @notice Highest L2 block number for which a root has been published.
    ///         The inbox reads this to enforce the recency window.
    uint256 public blockNumber;

    function setStateRoot(uint256 l2BlockNumber, bytes32 root) external {
        stateRootHistory[l2BlockNumber] = root;
        if (l2BlockNumber > blockNumber) {
            blockNumber = l2BlockNumber;
        }
    }
}
