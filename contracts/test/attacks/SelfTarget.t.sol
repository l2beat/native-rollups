// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {NativeRollup} from "../../src/NativeRollup.sol";
import {L2Messenger} from "../../src/l2/L2Messenger.sol";
import {TestNativeRollup} from "../NativeRollup.t.sol";

/// @notice Attack round: a message to the other chain's messenger, whose data
///         calls its `sendMessage`, would make it send a message in its own
///         name, which the other side reads as `l1Sender()` or `l2Sender()`.
contract SelfTargetTest is Test {
    address constant L2_MESSENGER = 0x8079000000000000000000000000000000000001;

    function test_l1RejectsMessagesToTheL2Messenger() public {
        TestNativeRollup rollup = new TestNativeRollup(
            8079, 60_000_000, 0, 0, NativeRollup.VkPolicy.FollowCurrent, 0, address(0), L2_MESSENGER
        );
        bytes memory data = abi.encodeCall(L2Messenger.sendMessage, (address(0xBEEF), 0, 0, hex"c0ffee"));
        vm.expectRevert(bytes("message to the messenger"));
        rollup.sendMessage{value: 1 ether}(L2_MESSENGER, 0, 100_000, data);
    }

    function test_l2RejectsMessagesToTheRollup() public {
        L2Messenger messenger = new L2Messenger();
        address rollup = address(0x1234);
        vm.store(address(messenger), 0, bytes32(uint256(uint160(rollup))));
        bytes memory data = abi.encodeCall(NativeRollup.sendMessage, (address(0xBEEF), 0, 0, hex"c0ffee"));
        vm.expectRevert(bytes("message to the rollup"));
        messenger.sendMessage{value: 1 ether}(rollup, 0, 100_000, data);
    }
}
