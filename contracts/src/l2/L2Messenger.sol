// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {MessageTree} from "../libs/MessageTree.sol";
import {Message, Messages} from "../libs/Messages.sol";
import {MptProof} from "../libs/MptProof.sol";

/// @notice L2 predeploy of the book's messaging design. It holds the
///         pre-minted supply of the gas token and handles both directions:
///         - L1 to L2: `proveL1MessageRoot` proves the root of the rollup
///           contract's message tree against the L1 block hash anchored on
///           L2, once per anchor. `claimL1Message` proves a message's path
///           to that root, releases its value, and calls the destination,
///           which can read the L1 sender from `l1Sender()` during the call.
///         - L2 to L1: `sendMessage` locks the value back into the supply and
///           appends the message hash to `sentMessages`, which the rollup
///           contract proves against an L2 state root.
/// @dev    Lives in the L2 genesis with its code, its balance, and `l1Rollup`
///         in storage, so it has no constructor.
contract L2Messenger {
    // EIP-4788 beacon roots contract, which stores each L2 block's anchor.
    address internal constant BEACON_ROOTS = 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02;
    // Storage slot of the root of `NativeRollup.l1Messages`.
    uint256 internal constant L1_MESSAGE_ROOT_SLOT = 3;

    address public l1Rollup;
    // L1->L2 messages already delivered, as a bitmap (see Messages).
    mapping(uint256 => uint256) internal claimedBits;
    bytes32[] public sentMessages;
    // The L1 message root last proven, and the timestamp of the L2 block
    // whose anchor it was proven against. Overwritten by each newer anchor,
    // so it never grows.
    uint256 public provenAnchorTimestamp;
    bytes32 public provenL1MessageRoot;
    address internal transient currentL1Sender;

    event L1MessageRootProven(uint256 indexed anchorTimestamp, bytes32 root);
    event L1MessageClaimed(uint256 indexed index, address indexed sender, address indexed to, uint256 value);
    /// @notice Emitted so the message can be claimed on L1, where only its
    ///         hash is proven.
    event L2MessageSent(uint256 indexed index, address indexed sender, address indexed to, uint256 value, bytes data);

    function claimed(uint256 index) external view returns (bool) {
        return Messages.isClaimed(claimedBits, index);
    }

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

    /// @notice Proves the rollup contract's L1 message root in an anchored L1
    ///         block, for the claims that follow. Anchors never move
    ///         backwards, so a later L2 block's anchor has at least as many
    ///         messages, and the root only moves forward.
    /// @param anchorTimestamp Timestamp of the L2 block whose anchor is the L1
    ///                        block the proofs are against.
    /// @param l1Header        RLP of that L1 block's header.
    /// @param accountProof    Proof of the rollup contract's account in that
    ///                        block's state.
    /// @param storageProof    Proof of the root's slot in its storage.
    function proveL1MessageRoot(
        uint256 anchorTimestamp,
        bytes calldata l1Header,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) external {
        require(anchorTimestamp >= provenAnchorTimestamp, "older anchor");
        require(keccak256(l1Header) == _anchor(anchorTimestamp), "header is not the anchor");
        bytes calldata l1StateRoot = MptProof.listItem(l1Header, 3);
        require(l1StateRoot.length == 32, "invalid header");
        bytes32 root = bytes32(
            MptProof.storageValue(
                bytes32(l1StateRoot), l1Rollup, bytes32(L1_MESSAGE_ROOT_SLOT), accountProof, storageProof
            )
        );
        provenAnchorTimestamp = anchorTimestamp;
        provenL1MessageRoot = root;
        emit L1MessageRootProven(anchorTimestamp, root);
    }

    /// @param m    The message, as sent to `NativeRollup.sendMessage`.
    /// @param path The siblings on the message's path to the proven root, up
    ///             to the tree's height.
    function claimL1Message(Message calldata m, bytes32[] calldata path) external {
        Messages.markClaimed(claimedBits, m.index);
        bytes32 leaf = Messages.hash(m.sender, m.to, m.value, m.data, m.index);
        require(MessageTree.rootFromPath(leaf, m.index, path) == provenL1MessageRoot, "message not in root");

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
