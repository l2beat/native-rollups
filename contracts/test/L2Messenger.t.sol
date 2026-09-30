// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";

import {L2Messenger} from "../src/l2/L2Messenger.sol";
import {Message} from "../src/libs/Messages.sol";

/// @dev A destination that records how the messenger called it.
contract Recorder {
    address public l1Sender;
    uint256 public value;
    bytes public data;

    fallback() external payable {
        l1Sender = L2Messenger(msg.sender).l1Sender();
        value = msg.value;
        data = msg.data;
    }
}

contract Reverter {
    fallback() external payable {
        revert();
    }
}

/// @notice Claims real L1 messages: `l1_message_vectors.json` holds the
///         claims that `script/l2_node.py` built on a local frames network,
///         with proofs from the L1 node, and the anchor of each claiming block.
contract L2MessengerTest is Test {
    using stdJson for string;

    address constant BEACON_ROOTS = 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02;

    struct Claim {
        Message m;
        uint256 timestamp;
        bytes header;
        bytes[] accountProof;
        bytes[] storageProof;
    }

    string json;
    L2Messenger messenger;

    function setUp() public {
        json = vm.readFile("test/l1_message_vectors.json");
        messenger = new L2Messenger();
        vm.store(address(messenger), 0, bytes32(uint256(uint160(json.readAddress(".l1Rollup")))));
        vm.deal(address(messenger), 1e27);
    }

    function decode(bytes calldata data)
        external
        pure
        returns (Message memory, uint256, bytes memory, bytes[] memory, bytes[] memory)
    {
        return abi.decode(data[4:], (Message, uint256, bytes, bytes[], bytes[]));
    }

    /// Loads claim `i` and anchors its L1 block, as the L2 block's EIP-4788
    /// system call does.
    function _load(uint256 i) internal returns (Claim memory c) {
        string memory k = string.concat(".claims[", vm.toString(i), "]");
        (c.m, c.timestamp, c.header, c.accountProof, c.storageProof) =
            this.decode(json.readBytes(string.concat(k, ".calldata")));
        vm.mockCall(
            BEACON_ROOTS, abi.encode(c.timestamp), abi.encode(json.readBytes32(string.concat(k, ".anchorHash")))
        );
    }

    function _claim(Claim memory c) internal {
        messenger.claimL1Message(c.m, c.timestamp, c.header, c.accountProof, c.storageProof);
    }

    function test_claimsMessages() public {
        for (uint256 i = 0; json.keyExists(string.concat(".claims[", vm.toString(i), "]")); i++) {
            Claim memory c = _load(i);
            vm.etch(c.m.to, type(Recorder).runtimeCode);
            uint256 supply = address(messenger).balance;

            _claim(c);

            Recorder r = Recorder(payable(c.m.to));
            assertTrue(messenger.claimed(c.m.index));
            assertEq(r.l1Sender(), c.m.sender);
            assertEq(r.value(), c.m.value);
            assertEq(r.data(), c.m.data);
            assertEq(c.m.to.balance, c.m.value);
            assertEq(address(messenger).balance, supply - c.m.value);
        }
        vm.expectRevert(bytes("no message"));
        messenger.l1Sender();
    }

    function test_rejectsReplay() public {
        Claim memory c = _load(0);
        _claim(c);
        vm.expectRevert(bytes("already claimed"));
        _claim(c);
    }

    /// The proof shows a queue entry, which must hash the claimed message.
    function test_rejectsForgedMessage() public {
        Claim memory c = _load(1);
        c.m.value += 1;
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.sender = address(0xBAD);
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.data = hex"c0ffef";
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        // Another index is another slot, which the proof does not cover.
        c = _load(1);
        c.m.index = 0;
        vm.expectRevert();
        _claim(c);
    }

    /// Proofs only count against the L1 block the L2 block anchored.
    function test_rejectsWrongAnchor() public {
        Claim memory c = _load(1);
        c.header = _load(0).header;
        vm.expectRevert(bytes("header is not the anchor"));
        _claim(c);

        c = _load(1);
        c.timestamp += 1;
        vm.expectRevert(bytes("no anchor at timestamp"));
        _claim(c);

        // Message 1 was sent after the L1 block that L2 block 1 anchored, so
        // its proofs do not match that block's state root.
        c = _load(1);
        Claim memory early = _load(0);
        c.timestamp = early.timestamp;
        c.header = early.header;
        vm.expectRevert();
        _claim(c);
    }

    /// A failed delivery leaves the message claimable.
    function test_failedDelivery() public {
        Claim memory c = _load(0);
        vm.etch(c.m.to, type(Reverter).runtimeCode);
        vm.expectRevert(bytes("delivery failed"));
        _claim(c);
        assertFalse(messenger.claimed(c.m.index));

        vm.etch(c.m.to, type(Recorder).runtimeCode);
        _claim(c);
        assertTrue(messenger.claimed(c.m.index));
    }
}
