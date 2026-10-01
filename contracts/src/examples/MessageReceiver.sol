// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {NativeRollup} from "../NativeRollup.sol";
import {L2Messenger} from "../l2/L2Messenger.sol";

/// @notice An example app that receives messages from the other chain: on L2
///         from the `L2Messenger`, on L1 from the rollup contract. It accepts
///         any calldata, and records each message with its cross-chain
///         sender, which the messenger exposes while it delivers the call.
contract MessageReceiver {
    /// @notice `L2Messenger` on L2, the rollup contract on L1.
    address public immutable messenger;
    bool public immutable onL2;
    uint256 public received;

    event MessageReceived(address indexed crossChainSender, uint256 value, bytes data);

    constructor(address messenger_, bool onL2_) {
        messenger = messenger_;
        onL2 = onL2_;
    }

    fallback() external payable {
        require(msg.sender == messenger, "not the messenger");
        address sender = onL2 ? L2Messenger(messenger).l1Sender() : NativeRollup(messenger).l2Sender();
        received++;
        emit MessageReceived(sender, msg.value, msg.data);
    }
}
