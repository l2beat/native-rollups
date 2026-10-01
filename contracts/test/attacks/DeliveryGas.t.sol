// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {L2Messenger} from "../../src/l2/L2Messenger.sol";
import {Message, Messages} from "../../src/libs/Messages.sol";
import {MessageTree} from "../../src/libs/MessageTree.sol";

/// @notice A receiver that runs a hook and tolerates its failure, as apps do
///         to keep a message from getting stuck.
contract HookReceiver {
    bool public hookRan;
    uint256 public hookFailures;

    fallback() external payable {
        try this.hook() {} catch {
            hookFailures++;
        }
    }

    function hook() external {
        bytes32 h;
        for (uint256 i = 0; i < 10_000; i++) {
            h = keccak256(abi.encode(h));
        }
        hookRan = true;
    }
}

/// @dev Stands in for the frame introspection helper in a frame transaction
///      whose frame has `left` state gas.
contract StateGasLeft {
    uint256 immutable left;

    constructor(uint256 left_) {
        left = left_;
    }

    fallback(bytes calldata) external returns (bytes memory) {
        return abi.encode(left);
    }
}

/// @notice Attack round: whoever claims a message chooses the claim's gas.
///         Without a gas limit in the message, a claimer could starve the
///         receiver's hook while the message still counts as delivered.
contract DeliveryGasTest is Test {
    address constant FRAMES_HELPER = 0x8079000000000000000000000000000000000002;
    // What the receiver needs for its hook to run.
    uint256 constant GAS_LIMIT = 3_300_000;

    L2Messenger messenger;
    HookReceiver receiver;
    Message m;

    function setUp() public {
        messenger = new L2Messenger();
        vm.deal(address(messenger), 1e27);
        receiver = new HookReceiver();
        m = Message({
            sender: address(0xA11CE), to: address(receiver), value: 1 ether, fee: 0, gasLimit: GAS_LIMIT, data: "", index: 0
        });
        vm.store(address(messenger), bytes32(uint256(4)), this.rootOf(m, new bytes32[](0)));
    }

    function rootOf(Message calldata message, bytes32[] calldata path) external pure returns (bytes32) {
        return MessageTree.rootFromPath(Messages.hash(message), 0, path);
    }

    function test_enoughGasRunsTheHook() public {
        messenger.claimL1Message{gas: 10_000_000}(m, new bytes32[](0), address(this));
        assertTrue(receiver.hookRan());
    }

    function test_claimBelowTheGasLimitFails() public {
        vm.expectRevert(bytes("gas below message limit"));
        messenger.claimL1Message{gas: 2_500_000}(m, new bytes32[](0), address(this));
        assertFalse(messenger.claimed(0));
    }

    /// Whatever gas a claim has, either the hook runs or the message stays
    /// claimable.
    function testFuzz_noClaimStarvesTheHook(uint256 gas) public {
        gas = bound(gas, 100_000, 10_000_000);
        try messenger.claimL1Message{gas: gas}(m, new bytes32[](0), address(this)) {
            assertTrue(receiver.hookRan());
        } catch {
            assertFalse(messenger.claimed(0));
        }
        assertEq(receiver.hookFailures(), 0);
    }

    /// A frame transaction pays state gas only from the frame's budget, which
    /// must cover the gas limit too.
    function test_frameStateBudgetBelowTheGasLimitFails() public {
        vm.etch(FRAMES_HELPER, address(new StateGasLeft(GAS_LIMIT - 1)).code);
        vm.expectRevert(bytes("gas below message limit"));
        messenger.claimL1Message{gas: 10_000_000}(m, new bytes32[](0), address(this));

        vm.etch(FRAMES_HELPER, address(new StateGasLeft(GAS_LIMIT)).code);
        messenger.claimL1Message{gas: 10_000_000}(m, new bytes32[](0), address(this));
        assertTrue(receiver.hookRan());
    }
}
