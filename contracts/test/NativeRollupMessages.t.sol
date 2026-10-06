// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";

import {NativeRollup} from "../src/NativeRollup.sol";
import {Message, Messages} from "../src/libs/Messages.sol";
import {TestNativeRollup} from "./NativeRollup.t.sol";

/// @dev A destination that records how the rollup contract called it.
contract L1Recorder {
    address public l2Sender;
    uint256 public value;
    bytes public data;

    fallback() external payable {
        l2Sender = NativeRollup(msg.sender).l2Sender();
        value = msg.value;
        data = msg.data;
    }
}

contract L1Reverter {
    fallback() external payable {
        revert();
    }
}

/// @notice The rollup contract's side of messaging. L2 to L1 claims use real
///         proofs: `l2_message_vectors.json` holds messages sent on the L2 of
///         a rollup on a local frames network, with proofs against the state
///         root of its latest block, as `script/record_message_vectors.py`
///         records them.
contract NativeRollupMessagesTest is Test {
    using stdJson for string;

    uint256 constant BLOCK_NUMBER_SLOT = 1;
    uint256 constant STATE_ROOTS_SLOT = 2;

    event L1MessageSent(
        uint256 indexed index,
        address indexed sender,
        address indexed to,
        uint256 value,
        uint256 fee,
        uint256 gasLimit,
        bytes data
    );

    // The gas the recorder needs: three new slots and the data.
    uint256 constant RECORDER_GAS = 150_000;

    // Receives the fees of the claims in these tests.
    address constant CLAIMER = address(0xC1A1);

    struct Claim {
        Message m;
        uint256 blockNumber;
        bytes[] accountProof;
        bytes[] storageProof;
    }

    string json;
    TestNativeRollup rollup;

    function setUp() public {
        json = vm.readFile("test/l2_message_vectors.json");
        rollup = new TestNativeRollup(
            8079,
            60_000_000,
            bytes32(0),
            bytes32(0),
            NativeRollup.VkPolicy.FollowCurrent,
            bytes32(0),
            address(0),
            json.readAddress(".l2Messenger")
        );
        vm.deal(address(rollup), 10 ether);
    }

    /// Loads claim `i` and gives the rollup the state root it is proven
    /// against, as if `advance` had just added that block.
    function _load(uint256 i) internal returns (Claim memory c) {
        string memory k = string.concat(".claims[", vm.toString(i), "]");
        c.m.sender = json.readAddress(string.concat(k, ".message.sender"));
        c.m.to = json.readAddress(string.concat(k, ".message.to"));
        c.m.value = json.readUint(string.concat(k, ".message.value"));
        c.m.fee = json.readUint(string.concat(k, ".message.fee"));
        c.m.gasLimit = json.readUint(string.concat(k, ".message.gasLimit"));
        c.m.data = json.readBytes(string.concat(k, ".message.data"));
        c.m.index = json.readUint(string.concat(k, ".message.index"));
        c.blockNumber = json.readUint(string.concat(k, ".blockNumber"));
        c.accountProof = json.readBytesArray(string.concat(k, ".accountProof"));
        c.storageProof = json.readBytesArray(string.concat(k, ".storageProof"));
        vm.store(address(rollup), bytes32(BLOCK_NUMBER_SLOT), bytes32(c.blockNumber));
        vm.store(
            address(rollup),
            keccak256(abi.encode(c.blockNumber % rollup.STATE_ROOT_HISTORY(), STATE_ROOTS_SLOT)),
            json.readBytes32(string.concat(k, ".stateRoot"))
        );
    }

    function _claim(Claim memory c) internal {
        rollup.claimL2Message(c.m, c.blockNumber, c.accountProof, c.storageProof, CLAIMER);
    }

    function test_sendMessage() public {
        bytes memory data = hex"c0ffee";
        vm.expectEmit(address(rollup));
        emit L1MessageSent(0, address(this), address(0xB0B), 0.9 ether, 0.1 ether, 50_000, data);
        rollup.sendMessage{value: 1 ether}(address(0xB0B), 0.1 ether, 50_000, data);
        assertEq(rollup.l1MessageCount(), 1);
        assertEq(address(rollup).balance, 11 ether);

        vm.expectRevert(bytes("fee exceeds value"));
        rollup.sendMessage{value: 1 ether}(address(0xB0B), 1 ether + 1, 0, data);
    }

    /// After every message, the root matches a tree built from all leaves at
    /// once, as the L2 node builds it.
    function test_messageTree() public {
        bytes32[] memory leaves = new bytes32[](33);
        for (uint256 i = 0; i < leaves.length; i++) {
            address to = address(uint160(i + 1));
            rollup.sendMessage{value: i}(to, i / 2, i, "");
            leaves[i] = keccak256(abi.encodePacked(address(this), to, i - i / 2, i / 2, i, keccak256(""), i));
            assertEq(rollup.l1MessageRoot(), _root(leaves, i + 1));
        }
    }

    function _root(bytes32[] memory leaves, uint256 count) internal pure returns (bytes32) {
        bytes32[] memory level = new bytes32[](count);
        for (uint256 i = 0; i < count; i++) {
            level[i] = leaves[i];
        }
        bytes32 zero;
        for (uint256 height = 0; height < 32; height++) {
            uint256 parents = (count + 1) / 2;
            for (uint256 i = 0; i < parents; i++) {
                bytes32 right = 2 * i + 1 < count ? level[2 * i + 1] : zero;
                level[i] = keccak256(abi.encodePacked(level[2 * i], right));
            }
            count = parents;
            zero = keccak256(abi.encodePacked(zero, zero));
        }
        return level[0];
    }

    function test_claimsL2Messages() public {
        for (uint256 i = 0; json.keyExists(string.concat(".claims[", vm.toString(i), "]")); i++) {
            Claim memory c = _load(i);
            // Messages to contracts carry the gas their call needs.
            bool record = c.m.gasLimit >= RECORDER_GAS;
            if (record) vm.etch(c.m.to, type(L1Recorder).runtimeCode);
            uint256 escrow = address(rollup).balance;
            uint256 balance = c.m.to.balance;
            uint256 fees = CLAIMER.balance;

            _claim(c);

            assertTrue(rollup.claimedL2Messages(c.m.index));
            if (record) {
                L1Recorder r = L1Recorder(payable(c.m.to));
                assertEq(r.l2Sender(), c.m.sender);
                assertEq(r.value(), c.m.value);
                assertEq(r.data(), c.m.data);
            }
            assertEq(c.m.to.balance, balance + c.m.value);
            assertEq(CLAIMER.balance, fees + c.m.fee);
            assertEq(address(rollup).balance, escrow - c.m.value - c.m.fee);
        }
        vm.expectRevert(bytes("no message"));
        rollup.l2Sender();
    }

    function test_rejectsReplay() public {
        Claim memory c = _load(0);
        _claim(c);
        vm.expectRevert(bytes("already claimed"));
        _claim(c);
    }

    function test_rejectsForgedMessage() public {
        Claim memory c = _load(1);
        c.m.value += 1;
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.fee += 1;
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.gasLimit += 1;
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.sender = address(0xBAD);
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        c = _load(1);
        c.m.data = hex"beee";
        vm.expectRevert(bytes("message not queued"));
        _claim(c);

        // Another index is another slot, which the proof does not cover.
        c = _load(1);
        c.m.index = 0;
        vm.expectRevert();
        _claim(c);
    }

    /// Proofs only count against a recent state root the rollup stored.
    function test_rejectsUnknownRoot() public {
        Claim memory c = _load(0);
        c.blockNumber += 1;
        vm.expectRevert(bytes("L2 block not in history"));
        _claim(c);

        c = _load(0);
        vm.store(address(rollup), bytes32(BLOCK_NUMBER_SLOT), bytes32(c.blockNumber + rollup.STATE_ROOT_HISTORY()));
        vm.expectRevert(bytes("L2 block not in history"));
        _claim(c);

        c = _load(0);
        vm.store(
            address(rollup),
            keccak256(abi.encode(c.blockNumber % rollup.STATE_ROOT_HISTORY(), STATE_ROOTS_SLOT)),
            bytes32(uint256(1))
        );
        vm.expectRevert();
        _claim(c);
    }

    /// A failed delivery leaves the message claimable.
    function test_failedDelivery() public {
        Claim memory c = _load(0);
        vm.etch(c.m.to, type(L1Reverter).runtimeCode);
        vm.expectRevert(bytes("delivery failed"));
        _claim(c);
        assertFalse(rollup.claimedL2Messages(c.m.index));

        vm.etch(c.m.to, "");
        _claim(c);
        assertTrue(rollup.claimedL2Messages(c.m.index));
    }

    /// Execution gas only: Foundry does not charge EIP-8037 state gas.
    function test_measureClaimGas() public {
        for (uint256 i = 0; json.keyExists(string.concat(".claims[", vm.toString(i), "]")); i++) {
            Claim memory c = _load(i);
            vm.cool(address(rollup));
            uint256 g = gasleft();
            _claim(c);
            console.log("claimL2Message gas %d (%d proof bytes)", g - gasleft(), _size(c));
        }
    }

    function _size(Claim memory c) internal pure returns (uint256 n) {
        for (uint256 i = 0; i < c.accountProof.length; i++) {
            n += c.accountProof[i].length;
        }
        for (uint256 i = 0; i < c.storageProof.length; i++) {
            n += c.storageProof[i].length;
        }
    }
}
