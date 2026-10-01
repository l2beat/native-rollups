// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
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

/// @notice Claims real L1 messages: `l1_message_vectors.json` holds, for
///         three L2 blocks of a rollup on a local frames network, their
///         anchor, the proof of the L1 message root against it, and the
///         claims of the new messages with their paths to that root, as
///         `script/record_message_vectors.py` records them.
contract L2MessengerTest is Test {
    using stdJson for string;

    address constant BEACON_ROOTS = 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02;

    struct RootProof {
        uint256 timestamp;
        bytes header;
        bytes[] accountProof;
        bytes[] storageProof;
    }

    struct Claim {
        Message m;
        bytes32[] path;
    }

    // Receives the fees of the claims in these tests.
    address constant CLAIMER = address(0xC1A1);
    // The gas the recorder needs: three new slots and the data.
    uint256 constant RECORDER_GAS = 150_000;

    string json;
    L2Messenger messenger;

    function setUp() public {
        json = vm.readFile("test/l1_message_vectors.json");
        messenger = new L2Messenger();
        vm.store(address(messenger), 0, bytes32(uint256(uint160(json.readAddress(".l1Rollup")))));
        vm.deal(address(messenger), 1e27);
    }

    function decodeRootProof(bytes calldata data) external pure returns (RootProof memory p) {
        (p.timestamp, p.header, p.accountProof, p.storageProof) =
            abi.decode(data[4:], (uint256, bytes, bytes[], bytes[]));
    }

    function decodeClaim(bytes calldata data) external pure returns (Claim memory c) {
        (c.m, c.path,) = abi.decode(data[4:], (Message, bytes32[], address));
    }

    function _block(uint256 b) internal pure returns (string memory) {
        return string.concat(".blocks[", vm.toString(b), "]");
    }

    /// Loads block `b`'s root proof and stores its anchor, as the L2 block's
    /// EIP-4788 system call does.
    function _rootProof(uint256 b) internal returns (RootProof memory p) {
        p = this.decodeRootProof(json.readBytes(string.concat(_block(b), ".proveRoot")));
        vm.mockCall(
            BEACON_ROOTS, abi.encode(p.timestamp), abi.encode(json.readBytes32(string.concat(_block(b), ".anchorHash")))
        );
    }

    function _prove(RootProof memory p) internal {
        messenger.proveL1MessageRoot(p.timestamp, p.header, p.accountProof, p.storageProof);
    }

    function _claim(uint256 b, uint256 i) internal view returns (Claim memory) {
        return this.decodeClaim(json.readBytes(string.concat(_block(b), ".claims[", vm.toString(i), "]")));
    }

    function test_claimsMessages() public {
        for (uint256 b = 0; json.keyExists(_block(b)); b++) {
            _prove(_rootProof(b));
            for (uint256 i = 0; json.keyExists(string.concat(_block(b), ".claims[", vm.toString(i), "]")); i++) {
                Claim memory c = _claim(b, i);
                // Messages to contracts carry the gas their call needs.
                bool record = c.m.gasLimit >= RECORDER_GAS;
                if (record) vm.etch(c.m.to, type(Recorder).runtimeCode);
                uint256 supply = address(messenger).balance;
                uint256 balance = c.m.to.balance;
                uint256 fees = CLAIMER.balance;

                messenger.claimL1Message(c.m, c.path, CLAIMER);

                assertTrue(messenger.claimed(c.m.index));
                if (record) {
                    Recorder r = Recorder(payable(c.m.to));
                    assertEq(r.l1Sender(), c.m.sender);
                    assertEq(r.value(), c.m.value);
                    assertEq(r.data(), c.m.data);
                }
                assertEq(c.m.to.balance, balance + c.m.value);
                assertEq(CLAIMER.balance, fees + c.m.fee);
                assertEq(address(messenger).balance, supply - c.m.value - c.m.fee);
            }
        }
        vm.expectRevert(bytes("no message"));
        messenger.l1Sender();
    }

    function test_rejectsReplay() public {
        _prove(_rootProof(0));
        Claim memory c = _claim(0, 0);
        messenger.claimL1Message(c.m, c.path, CLAIMER);
        vm.expectRevert(bytes("already claimed"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);
    }

    /// The path leads to the proven root only from the message's own hash
    /// and position.
    function test_rejectsForgedMessage() public {
        _prove(_rootProof(1));

        Claim memory c = _claim(1, 0);
        c.m.value += 1;
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.fee += 1;
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.gasLimit += 1;
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.sender = address(0xBAD);
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.data = hex"c0ffef";
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.index ^= 1;
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        c = _claim(1, 0);
        c.m.index = 1 << c.path.length;
        vm.expectRevert(bytes("path too short"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);
    }

    /// Roots only come from the anchored L1 block, and never move back to an
    /// earlier one.
    function test_rootRules() public {
        // No root yet.
        Claim memory c = _claim(0, 0);
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        RootProof memory p = _rootProof(1);
        p.header = _rootProof(0).header;
        vm.expectRevert(bytes("header is not the anchor"));
        _prove(p);

        p = _rootProof(1);
        p.timestamp += 1;
        vm.expectRevert(bytes("no anchor at timestamp"));
        _prove(p);

        // Message 1 was sent after the L1 block that L2 block 1 anchored.
        _prove(_rootProof(0));
        c = _claim(1, 0);
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);

        _prove(_rootProof(1));
        RootProof memory older = _rootProof(0);
        vm.expectRevert(bytes("older anchor"));
        _prove(older);
    }

    /// A failed delivery leaves the message claimable.
    function test_failedDelivery() public {
        _prove(_rootProof(0));
        Claim memory c = _claim(0, 0);
        vm.etch(c.m.to, type(Reverter).runtimeCode);
        vm.expectRevert(bytes("delivery failed"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);
        assertFalse(messenger.claimed(c.m.index));

        vm.etch(c.m.to, "");
        messenger.claimL1Message(c.m, c.path, CLAIMER);
        assertTrue(messenger.claimed(c.m.index));
    }

    /// Execution gas only: Foundry does not charge EIP-8037 state gas.
    function test_measureGas() public {
        uint256 last = 2;
        RootProof memory p = _rootProof(last);
        uint256 g = gasleft();
        _prove(p);
        console.log(
            "proveL1MessageRoot gas %d (%d proof nodes)", g - gasleft(), p.accountProof.length + p.storageProof.length
        );
        // A message to a contract, whose call runs.
        Claim memory c = _claim(last, 0);
        for (uint256 i = 1; c.m.gasLimit < RECORDER_GAS; i++) {
            c = _claim(last, i);
        }
        vm.etch(c.m.to, type(Recorder).runtimeCode);
        g = gasleft();
        messenger.claimL1Message(c.m, c.path, CLAIMER);
        console.log("claimL1Message gas %d (path of %d)", g - gasleft(), c.path.length);
    }
}
