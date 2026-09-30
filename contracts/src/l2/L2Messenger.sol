// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {RLPReader} from "../libs/RLPReader.sol";
import {SecureMerkleTrie} from "../libs/SecureMerkleTrie.sol";

/// @notice L2 predeploy of the book's messaging design. It holds the
///         pre-minted supply of the gas token and delivers L1 to L2 messages:
///         it proves a message hash in the rollup contract's
///         `pendingL1Messages` queue against the L1 block hash anchored on
///         L2, releases the message's value, and calls the destination, which
///         can read the L1 sender from `l1Sender()` during the call.
/// @dev    Lives in the L2 genesis with its code, its balance, and `l1Rollup`
///         in storage, so it has no constructor.
contract L2Messenger {
    struct L1Message {
        address sender;
        address to;
        uint256 value;
        bytes data;
        uint256 index;
    }

    // EIP-4788 beacon roots contract, which stores each L2 block's anchor.
    address internal constant BEACON_ROOTS = 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02;
    // Storage slot of `NativeRollup.pendingL1Messages`.
    uint256 internal constant QUEUE_SLOT = 5;

    address public l1Rollup;
    mapping(uint256 => bool) public claimed;
    address internal currentL1Sender;

    event L1MessageClaimed(uint256 indexed index, address indexed sender, address indexed to, uint256 value);

    /// @notice The L1 sender of the message being delivered.
    function l1Sender() external view returns (address) {
        require(currentL1Sender != address(0), "no message");
        return currentL1Sender;
    }

    /// @param m               The message, as sent to `NativeRollup.sendMessage`.
    /// @param anchorTimestamp Timestamp of an L2 block whose anchor is the L1
    ///                        block the proofs are against.
    /// @param l1Header        RLP of that L1 block's header.
    /// @param accountProof    Proof of the rollup contract's account in that
    ///                        block's state.
    /// @param storageProof    Proof of the queue entry in its storage.
    function claimL1Message(
        L1Message calldata m,
        uint256 anchorTimestamp,
        bytes calldata l1Header,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) external {
        require(!claimed[m.index], "already claimed");
        require(keccak256(l1Header) == _anchor(anchorTimestamp), "header is not the anchor");

        bytes32 l1StateRoot = bytes32(RLPReader.readBytes(RLPReader.readList(l1Header)[3]));
        bytes memory account = SecureMerkleTrie.get(abi.encodePacked(l1Rollup), accountProof, l1StateRoot);
        bytes32 storageRoot = bytes32(RLPReader.readBytes(RLPReader.readList(account)[2]));
        bytes32 slot = bytes32(uint256(keccak256(abi.encode(QUEUE_SLOT))) + m.index);
        uint256 stored =
            _toUint(RLPReader.readBytes(SecureMerkleTrie.get(abi.encodePacked(slot), storageProof, storageRoot)));
        bytes32 messageHash = keccak256(abi.encodePacked(m.sender, m.to, m.value, keccak256(m.data), m.index));
        require(bytes32(stored) == messageHash, "message not queued");

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

    /// @dev Storage values are RLP-encoded with leading zero bytes removed.
    function _toUint(bytes memory b) internal pure returns (uint256 v) {
        for (uint256 i = 0; i < b.length; i++) {
            v = (v << 8) | uint8(b[i]);
        }
    }
}
