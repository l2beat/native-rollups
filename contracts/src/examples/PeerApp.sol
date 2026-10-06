// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {NativeRollup} from "../NativeRollup.sol";
import {L2Messenger} from "../l2/L2Messenger.sol";

/// @dev `sendMessage` of the rollup contract on L1 and of `L2Messenger` on
///      L2, which take the same arguments.
interface Messenger {
    function sendMessage(address to, uint256 fee, uint256 gasLimit, bytes calldata data) external payable;
}

/// @notice The base of an example app with a contract on each chain: each
///         side calls its peer on the other chain through messages, and
///         accepts only the calls that its messenger delivers from the peer.
abstract contract PeerApp {
    /// @notice `L2Messenger` on L2, the rollup contract on L1.
    address public immutable messenger;
    bool public immutable onL2;
    /// @notice The app's contract on the other chain.
    address public immutable peer;

    constructor(address messenger_, bool onL2_, address peer_) {
        messenger = messenger_;
        onL2 = onL2_;
        peer = peer_;
    }

    /// @notice Only a call that the messenger delivers from the peer, whose
    ///         address the messenger exposes while it calls.
    modifier onlyPeer() {
        require(msg.sender == messenger, "not the messenger");
        address sender = onL2 ? L2Messenger(messenger).l1Sender() : NativeRollup(messenger).l2Sender();
        require(sender == peer, "not the peer");
        _;
    }

    /// @notice Calls the peer with `data` and `value - fee`, giving the call
    ///         `gasLimit`, and pays `fee` to whoever claims the message there.
    function callPeer(uint256 value, uint256 fee, uint256 gasLimit, bytes memory data) internal {
        Messenger(messenger).sendMessage{value: value}(peer, fee, gasLimit, data);
    }
}
