// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";

import {PingPong} from "../src/examples/PingPong.sol";
import {FakeMessenger} from "./mocks/FakeMessengers.sol";

contract PingPongTest is Test {
    event PingReceived(uint256 indexed id, address indexed player);
    event PongReceived(uint256 indexed id, address indexed player);

    FakeMessenger l1Messenger = new FakeMessenger();
    FakeMessenger l2Messenger = new FakeMessenger();
    PingPong onL1;
    PingPong onL2;
    address alice = address(0xA11CE);

    function setUp() public {
        l1Messenger.link(l2Messenger);
        l2Messenger.link(l1Messenger);
        // Each side names the other, so the L2 side names where the L1 one
        // will be.
        onL2 = new PingPong(address(l2Messenger), true, vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1));
        onL1 = new PingPong(address(l1Messenger), false, address(onL2));
        vm.deal(alice, 1 ether);
    }

    function test_roundTripFromL1() public {
        vm.prank(alice);
        onL1.ping{value: 0.002 ether}();
        (, address to, uint256 value, uint256 fee,,) = l1Messenger.sent(0);
        assertEq(to, address(onL2));
        assertEq(fee, 0.001 ether);
        assertEq(value, 0.001 ether);

        vm.expectEmit(address(onL2));
        emit PingReceived(0, alice);
        l1Messenger.relay(0);
        // The pong's fee is the value the ping carried.
        (, to, value, fee,,) = l2Messenger.sent(0);
        assertEq(to, address(onL1));
        assertEq(fee, 0.001 ether);
        assertEq(value, 0);

        vm.expectEmit(address(onL1));
        emit PongReceived(0, alice);
        l2Messenger.relay(0);
    }

    function test_roundTripFromL2() public {
        vm.prank(alice);
        onL2.ping{value: 0.002 ether}();
        l2Messenger.relay(0);
        vm.expectEmit(address(onL2));
        emit PongReceived(0, alice);
        l1Messenger.relay(0);
    }

    function test_acceptsOnlyThePeer() public {
        vm.expectRevert("not the messenger");
        onL2.receivePing(0, alice);
        // A message from anyone else on L1 reaches it, but is refused.
        vm.prank(alice);
        l1Messenger.sendMessage(address(onL2), 0, 300_000, abi.encodeCall(PingPong.receivePong, (0, alice)));
        vm.expectRevert("not the peer");
        l1Messenger.relay(0);
    }
}
