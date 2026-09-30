// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Message, Messages} from "../libs/Messages.sol";
import {MptProof} from "../libs/MptProof.sol";

/// @notice L2 predeploy of the book's messaging design. It holds the
///         pre-minted supply of the gas token and handles both directions:
///         - L1 to L2: it proves a message hash in the rollup contract's
///           `pendingL1Messages` queue against the L1 block hash anchored on
///           L2, releases the message's value, and calls the destination,
///           which can read the L1 sender from `l1Sender()` during the call.
///         - L2 to L1: `sendMessage` locks the value back into the supply and
///           appends the message hash to `sentMessages`, which the rollup
///           contract proves against an L2 state root.
/// @dev    Lives in the L2 genesis with its code, its balance, and `l1Rollup`
///         in storage, so it has no constructor.
contract L2Messenger {
    // EIP-4788 beacon roots contract, which stores each L2 block's anchor.
    address internal constant BEACON_ROOTS = 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02;
    // Storage slot of `NativeRollup.pendingL1Messages`.
    uint256 internal constant L1_QUEUE_SLOT = 3;

    address public l1Rollup;
    mapping(uint256 => bool) public claimed;
    bytes32[] public sentMessages;
    address internal transient currentL1Sender;

    event L1MessageClaimed(uint256 indexed index, address indexed sender, address indexed to, uint256 value);
    /// @notice Emitted so the message can be claimed on L1, where only its
    ///         hash is proven.
    event L2MessageSent(uint256 indexed index, address indexed sender, address indexed to, uint256 value, bytes data);

    /// @notice The L1 sender of the message being delivered.
    function l1Sender() external view returns (address) {
        require(currentL1Sender != address(0), "no message");
        return currentL1Sender;
    }

    function sendMessage(address to, bytes calldata data) external payable {
        uint256 index = sentMessages.length;
        sentMessages.push(Messages.hash(msg.sender, to, msg.value, data, index));
        emit L2MessageSent(index, msg.sender, to, msg.value, data);
    }

    /// @param m               The message, as sent to `NativeRollup.sendMessage`.
    /// @param anchorTimestamp Timestamp of an L2 block whose anchor is the L1
    ///                        block the proofs are against.
    /// @param l1Header        RLP of that L1 block's header.
    /// @param accountProof    Proof of the rollup contract's account in that
    ///                        block's state.
    /// @param storageProof    Proof of the queue entry in its storage.
    function claimL1Message(
        Message calldata m,
        uint256 anchorTimestamp,
        bytes calldata l1Header,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) external {
        require(!claimed[m.index], "already claimed");
        require(keccak256(l1Header) == _anchor(anchorTimestamp), "header is not the anchor");
        bytes calldata l1StateRoot = MptProof.listItem(l1Header, 3);
        require(l1StateRoot.length == 32, "invalid header");
        Messages.requireQueued(m, bytes32(l1StateRoot), l1Rollup, L1_QUEUE_SLOT, accountProof, storageProof);

        claimed[m.index] = true;
        currentL1Sender = m.sender;
        (bool ok,) = m.to.call{value: m.value}(m.data);
        currentL1Sender = address(0);
        require(ok, "delivery failed");
        emit L1MessageClaimed(m.index, m.sender, m.to, m.value);
    }

    function _anchor(uint256 timestamp) internal view returns (bytes32) {
        (bool ok, bytes memory out) = BEACON_ROOTS.staticcall(abi.encode(timestamp));
        require(ok && out.length == 32, "no anchor at timestamp");
        return abi.decode(out, (bytes32));
    }
}
