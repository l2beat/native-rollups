// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";

import {MessageReceiver} from "../src/examples/MessageReceiver.sol";

/// @dev Stands in for either messenger: it exposes the cross-chain sender
///      under both names while it delivers a call.
contract FakeMessenger {
    address public sender;

    function l1Sender() external view returns (address) {
        return sender;
    }

    function l2Sender() external view returns (address) {
        return sender;
    }

    function deliver(address from, address to, bytes calldata data) external payable {
        sender = from;
        (bool ok,) = to.call{value: msg.value}(data);
        sender = address(0);
        require(ok, "delivery failed");
    }
}

contract MessageReceiverTest is Test {
    event MessageReceived(address indexed crossChainSender, uint256 value, bytes data);

    function test_recordsTheCrossChainSender() public {
        for (uint256 i = 0; i < 2; i++) {
            FakeMessenger messenger = new FakeMessenger();
            MessageReceiver receiver = new MessageReceiver(address(messenger), i == 0);
            vm.expectEmit(address(receiver));
            emit MessageReceived(address(0xA11CE), 1 ether, hex"c0ffee");
            messenger.deliver{value: 1 ether}(address(0xA11CE), address(receiver), hex"c0ffee");
            assertEq(receiver.received(), 1);
            assertEq(address(receiver).balance, 1 ether);
        }
    }

    function test_rejectsOtherCallers() public {
        MessageReceiver receiver = new MessageReceiver(address(new FakeMessenger()), true);
        (bool ok,) = address(receiver).call(hex"c0ffee");
        assertFalse(ok);
    }
}
