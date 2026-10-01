// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {NativeRollupTest} from "../NativeRollup.t.sol";

/// @notice Attack round: without a lower bound on L2 timestamps, whoever
///         builds a block could keep L2 time in the past, past users'
///         deadlines.
contract StaleTimestampTest is NativeRollupTest {
    function test_rejectsL2TimeAMonthBehind() public {
        _setL1Context(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        vm.warp(block.timestamp + 30 days);
        vm.expectRevert(bytes("timestamp too old"));
        rollup.advance(_params(0), 1);
    }

    function test_acceptsL2TimeUpToTheLag() public {
        _setL1Context(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        vm.warp(block.timestamp + rollup.MAX_TIMESTAMP_LAG());
        rollup.advance(_params(0), 1);

        _setL1Context(1);
        rollup.setDependency(LEANSTARK, _publicInputRoot(1), K1);
        vm.warp(block.timestamp + rollup.MAX_TIMESTAMP_LAG() + 1);
        vm.expectRevert(bytes("timestamp too old"));
        rollup.advance(_params(1), 1);
    }
}
