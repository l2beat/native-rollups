// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {PeerApp} from "./PeerApp.sol";

/// @notice An example app that plays ping pong between the chains: a ping
///         sent here is delivered to the peer on the other chain, which
///         answers with a pong at once, so a round trip shows a call
///         authenticated in each direction.
contract PingPong is PeerApp {
    /// @notice The gas each delivery gets. Answering a ping sends a message,
    ///         which on L2 takes a new storage slot of the messenger.
    uint256 public constant GAS_LIMIT = 300_000;

    uint256 public pings;

    event PingSent(uint256 indexed id, address indexed player);
    event PingReceived(uint256 indexed id, address indexed player);
    event PongReceived(uint256 indexed id, address indexed player);

    constructor(address messenger_, bool onL2_, address peer_) PeerApp(messenger_, onL2_, peer_) {}

    /// @notice Sends a ping to the peer. Half the value pays whoever claims
    ///         the ping, the rest travels with it to pay whoever claims the
    ///         pong.
    function ping() external payable {
        uint256 id = pings++;
        emit PingSent(id, msg.sender);
        callPeer(msg.value, msg.value / 2, GAS_LIMIT, abi.encodeCall(this.receivePing, (id, msg.sender)));
    }

    /// @notice Answers a ping from the peer with a pong, whose fee is the
    ///         value the ping carried.
    function receivePing(uint256 id, address player) external payable onlyPeer {
        emit PingReceived(id, player);
        callPeer(msg.value, msg.value, GAS_LIMIT, abi.encodeCall(this.receivePong, (id, player)));
    }

    function receivePong(uint256 id, address player) external onlyPeer {
        emit PongReceived(id, player);
    }
}
