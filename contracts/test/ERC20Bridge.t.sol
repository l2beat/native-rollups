// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";

import {BridgedERC20} from "../src/examples/BridgedERC20.sol";
import {DemoToken} from "../src/examples/DemoToken.sol";
import {L1ERC20Bridge} from "../src/examples/L1ERC20Bridge.sol";
import {L2ERC20Bridge} from "../src/examples/L2ERC20Bridge.sol";
import {FakeMessenger} from "./mocks/FakeMessengers.sol";

contract ERC20BridgeTest is Test {
    FakeMessenger l1Messenger = new FakeMessenger();
    FakeMessenger l2Messenger = new FakeMessenger();
    L1ERC20Bridge l1Bridge;
    L2ERC20Bridge l2Bridge;
    DemoToken token;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);

    function setUp() public {
        l1Messenger.link(l2Messenger);
        l2Messenger.link(l1Messenger);
        // Each bridge names the other, so the L2 one names where the L1 one
        // will be.
        l2Bridge = new L2ERC20Bridge(address(l2Messenger), vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1));
        l1Bridge = new L1ERC20Bridge(address(l1Messenger), address(l2Bridge));
        address[] memory holders = new address[](1);
        holders[0] = alice;
        token = new DemoToken(holders, 1000 ether);
        vm.deal(alice, 1 ether);
        vm.deal(bob, 1 ether);
    }

    function deposit(address to, uint256 amount) internal {
        vm.startPrank(alice);
        token.approve(address(l1Bridge), amount);
        l1Bridge.deposit{value: 0.001 ether}(address(token), to, amount);
        vm.stopPrank();
        l1Messenger.relay(l1Messenger.count() - 1);
    }

    function test_depositsAndWithdraws() public {
        deposit(bob, 100 ether);
        BridgedERC20 l2Token = BridgedERC20(l2Bridge.l2TokenOf(address(token)));
        assertEq(l2Token.balanceOf(bob), 100 ether);
        assertEq(l2Token.name(), "Demo token");
        assertEq(l2Token.symbol(), "DEMO");
        assertEq(l2Token.decimals(), 18);
        assertEq(l2Token.l1Token(), address(token));
        assertEq(token.balanceOf(address(l1Bridge)), 100 ether);

        // Later deposits mint the same token.
        deposit(bob, 50 ether);
        assertEq(l2Bridge.l2TokenOf(address(token)), address(l2Token));
        assertEq(l2Token.balanceOf(bob), 150 ether);

        vm.prank(bob);
        l2Bridge.withdraw{value: 0.001 ether}(address(l2Token), alice, 40 ether);
        assertEq(l2Token.balanceOf(bob), 110 ether);
        l2Messenger.relay(0);
        assertEq(token.balanceOf(alice), 1000 ether - 150 ether + 40 ether);
        assertEq(token.balanceOf(address(l1Bridge)), 110 ether);
    }

    function test_acceptsOnlyThePeer() public {
        vm.expectRevert("not the messenger");
        l1Bridge.finalizeWithdrawal(address(token), bob, bob, 1 ether);
        vm.expectRevert("not the messenger");
        l2Bridge.finalizeDeposit(address(token), bob, bob, 1 ether, "Demo token", "DEMO", 18);
        // A message from anyone else on L2 reaches the L1 bridge, but is
        // refused.
        vm.prank(bob);
        l2Messenger.sendMessage(address(l1Bridge), 0, 200_000, abi.encodeCall(L1ERC20Bridge.finalizeWithdrawal, (address(token), bob, bob, 1 ether)));
        vm.expectRevert("not the peer");
        l2Messenger.relay(0);
    }

    function test_refusesTokensItDidNotDeploy() public {
        deposit(bob, 100 ether);
        // A token that names a real L1 token, but that the bridge did not
        // deploy, cannot withdraw it.
        vm.prank(bob);
        BridgedERC20 fake = new BridgedERC20(address(token), "Demo token", "DEMO", 18);
        vm.prank(bob);
        vm.expectRevert("not a bridged token");
        l2Bridge.withdraw(address(fake), bob, 100 ether);
    }

    function test_onlyTheBridgeMintsAndBurns() public {
        deposit(bob, 100 ether);
        BridgedERC20 l2Token = BridgedERC20(l2Bridge.l2TokenOf(address(token)));
        vm.expectRevert("not the bridge");
        l2Token.mint(bob, 1 ether);
        vm.expectRevert("not the bridge");
        l2Token.burn(bob, 1 ether);
    }
}
