// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";

import {NativeRollup} from "../src/NativeRollup.sol";

/// @dev Stands in for the EIP-8141 frame introspection: the test sets the
///      dependency triple that the transaction would declare.
contract TestNativeRollup is NativeRollup {
    uint8 internal depScheme;
    bytes32 internal depDataHash;
    bytes32 internal depVkHash;

    constructor(
        uint64 chainId_,
        uint64 gasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        VkPolicy vkPolicy_,
        bytes32 pinnedVkHash_,
        address evmVkRegistry_,
        address l2Messenger_
    )
        NativeRollup(
            chainId_,
            gasLimit_,
            genesisBlockHash,
            genesisStateRoot,
            vkPolicy_,
            pinnedVkHash_,
            evmVkRegistry_,
            l2Messenger_
        )
    {}

    function setDependency(uint8 scheme, bytes32 dataHash, bytes32 vkHash) external {
        depScheme = scheme;
        depDataHash = dataHash;
        depVkHash = vkHash;
    }

    function _readDependency(uint256) internal view override returns (uint8, bytes32, bytes32) {
        return (depScheme, depDataHash, depVkHash);
    }
}

/// @notice Advances the rollup through a chain whose public input roots were
///         computed with the compiled consensus-specs types
///         (`script/native_rollup_vectors.py`), against the real EIP-8357
///         registry runtime.
contract NativeRollupTest is Test {
    using stdJson for string;

    address constant REGISTRY = 0x00005e9c1447C1A05A642ec9eB76D9C125468357;
    address constant SYSTEM_ADDRESS = 0xffffFFFfFFffffffffffffffFfFFFfffFFFfFFfE;
    bytes32 constant K1 = keccak256("evm verification key, amsterdam");
    bytes32 constant K2 = keccak256("evm verification key, next fork");
    uint8 constant LEANSTARK = 0x11;

    string json;
    uint16 schemaId;
    TestNativeRollup rollup;

    function setUp() public {
        json = vm.readFile("test/native_rollup_vectors.json");
        schemaId = uint16(json.readUint(".schemaId"));

        // The registry runtime from ethereum/sys-asm#56, with its first key
        // registered through the fork-update path.
        vm.etch(REGISTRY, vm.parseBytes(vm.readFile("test/eip8357_registry.hex")));
        _register(K1, schemaId);

        rollup = _deploy(NativeRollup.VkPolicy.FollowCurrent, bytes32(0));
    }

    function _register(bytes32 vkHash, uint256 schema) internal {
        vm.prank(SYSTEM_ADDRESS);
        (bool ok,) = REGISTRY.call(abi.encodePacked(vkHash, bytes32(schema)));
        require(ok, "registration");
    }

    function _deploy(NativeRollup.VkPolicy policy, bytes32 pinned) internal returns (TestNativeRollup) {
        return new TestNativeRollup(
            uint64(json.readUint(".chainId")),
            uint64(json.readUint(".chain.gasLimit")),
            json.readBytes32(".chain.genesisHash"),
            json.readBytes32(".chain.genesisStateRoot"),
            policy,
            pinned,
            REGISTRY,
            address(0)
        );
    }

    function _block(uint256 i) internal pure returns (string memory) {
        return string.concat(".chain.blocks[", vm.toString(i), "]");
    }

    function _params(uint256 i) internal view returns (NativeRollup.BlockParams memory p) {
        string memory b = _block(i);
        string memory k = string.concat(b, ".header");
        p.stateRoot = json.readBytes32(string.concat(k, ".stateRoot"));
        p.receiptsRoot = json.readBytes32(string.concat(k, ".receiptsRoot"));
        p.logsBloom = json.readBytes(string.concat(k, ".logsBloom"));
        p.gasUsed = uint64(json.readUint(string.concat(k, ".gasUsed")));
        p.timestamp = uint64(json.readUint(string.concat(k, ".timestamp")));
        p.baseFeePerGas = json.readUint(string.concat(k, ".baseFeePerGas"));
        p.blockHash = json.readBytes32(string.concat(k, ".blockHash"));
        p.transactionsRoot = json.readBytes32(string.concat(k, ".transactionsRoot"));
        p.blockAccessListRoot = json.readBytes32(string.concat(k, ".blockAccessListRoot"));
        p.payloadBlobCount = json.readUint(string.concat(b, ".payloadBlobCount"));
        p.executionRequestsRoot = json.readBytes32(string.concat(b, ".executionRequestsRoot"));
        p.anchorBlockNumber = _anchorNumber(i);
        p.feeRecipient = json.readAddress(string.concat(k, ".feeRecipient"));
        p.prevRandao = json.readBytes32(string.concat(k, ".prevRandao"));
        p.extraData = json.readBytes(string.concat(k, ".extraData"));
    }

    function _publicInputRoot(uint256 i) internal view returns (bytes32) {
        return json.readBytes32(string.concat(_block(i), ".publicInputRoot"));
    }

    /// The L1 block block `i` anchors to, a few blocks before its inclusion.
    function _anchorNumber(uint256 i) internal pure returns (uint256) {
        return 990 + 5 * i;
    }

    /// Sets the L1 context block `i` was proven against: its payload blobs
    /// and the L1 anchor, the hash of the L1 block the operator chose. L1
    /// time is the block's timestamp, the latest the contract accepts.
    function _setL1Context(uint256 i) internal {
        vm.blobhashes(json.readBytes32Array(string.concat(_block(i), ".versionedHashes")));
        vm.roll(1000 + 10 * i);
        vm.warp(json.readUint(string.concat(_block(i), ".header.timestamp")));
        vm.setBlockhash(_anchorNumber(i), json.readBytes32(string.concat(_block(i), ".parentBeaconBlockRoot")));
    }

    function _advance(TestNativeRollup r, uint256 i, bytes32 vkHash) internal {
        _setL1Context(i);
        r.setDependency(LEANSTARK, _publicInputRoot(i), vkHash);
        r.advance(_params(i), 1);
    }

    function test_advancesThroughChain() public {
        for (uint256 i = 0; json.keyExists(_block(i)); i++) {
            _advance(rollup, i, K1);
            assertEq(rollup.blockNumber(), i + 1);
            assertEq(rollup.blockHash(), json.readBytes32(string.concat(_block(i), ".header.blockHash")));
            assertEq(rollup.stateRoot(), json.readBytes32(string.concat(_block(i), ".header.stateRoot")));
            assertEq(rollup.stateRootAt(i + 1), rollup.stateRoot());
            assertEq(rollup.anchorBlockNumber(), _anchorNumber(i));
        }
    }

    function test_rejectsWrongDependency() public {
        _setL1Context(0);
        NativeRollup.BlockParams memory p = _params(0);

        rollup.setDependency(0x10, _publicInputRoot(0), K1);
        vm.expectRevert(bytes("not a LeanSTARK dependency"));
        rollup.advance(p, 1);

        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K2);
        vm.expectRevert(bytes("wrong verification key"));
        rollup.advance(p, 1);

        rollup.setDependency(LEANSTARK, _publicInputRoot(0) ^ bytes32(uint256(1)), K1);
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(p, 1);
    }

    /// Every input the proof binds must match: blobs, anchor, order, fields.
    function test_rejectsMismatchedBlock() public {
        NativeRollup.BlockParams memory p = _params(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K1);

        _setL1Context(0);
        vm.blobhashes(new bytes32[](1)); // a different payload blob
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(p, 1);

        _setL1Context(0);
        vm.setBlockhash(_anchorNumber(0), bytes32(uint256(1))); // a different L1 anchor
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(p, 1);

        _setL1Context(0);
        p.executionRequestsRoot ^= bytes32(uint256(1));
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(p, 1);

        // Block 2 cannot be applied before block 1.
        _setL1Context(1);
        rollup.setDependency(LEANSTARK, _publicInputRoot(1), K1);
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(_params(1), 1);
    }

    /// The anchor may repeat but not move backwards, and must be a recent,
    /// past L1 block.
    function test_anchorRules() public {
        _advance(rollup, 0, K1);
        NativeRollup.BlockParams memory p = _params(1);
        _setL1Context(1);
        rollup.setDependency(LEANSTARK, _publicInputRoot(1), K1);

        p.anchorBlockNumber = _anchorNumber(0) - 1;
        vm.expectRevert(bytes("anchor moved backwards"));
        rollup.advance(p, 1);

        p.anchorBlockNumber = block.number; // the current block has no hash yet
        vm.expectRevert(bytes("anchor not available"));
        rollup.advance(p, 1);

        vm.roll(_anchorNumber(1) + 257); // the anchor left the BLOCKHASH window
        p.anchorBlockNumber = _anchorNumber(1);
        vm.expectRevert(bytes("anchor not available"));
        rollup.advance(p, 1);
    }

    /// L2 time cannot run ahead of L1 time. The program only requires
    /// timestamps to increase, so without this bound anyone could halt the
    /// rollup with a block at the maximum timestamp.
    function test_rejectsFutureTimestamp() public {
        _setL1Context(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        vm.warp(block.timestamp - 1);
        vm.expectRevert(bytes("timestamp in the future"));
        rollup.advance(_params(0), 1);
    }

    /// State roots stay available for STATE_ROOT_HISTORY blocks, after which
    /// their slot is reused.
    function test_stateRootHistoryWindow() public {
        _advance(rollup, 0, K1);
        assertEq(rollup.stateRootAt(0), json.readBytes32(".chain.genesisStateRoot"));
        assertEq(rollup.stateRootAt(1), json.readBytes32(string.concat(_block(0), ".header.stateRoot")));
        vm.expectRevert(bytes("L2 block not in history"));
        rollup.stateRootAt(2);

        // Pretend STATE_ROOT_HISTORY more blocks were added.
        vm.store(address(rollup), bytes32(uint256(1)), bytes32(1 + rollup.STATE_ROOT_HISTORY()));
        vm.expectRevert(bytes("L2 block not in history"));
        rollup.stateRootAt(1);
    }

    /// Consecutive L2 blocks can share an anchor.
    function test_repeatedAnchor() public {
        _advance(rollup, 0, K1);
        NativeRollup.BlockParams memory p = _params(1);
        _setL1Context(1);
        vm.setBlockhash(_anchorNumber(0), json.readBytes32(string.concat(_block(1), ".parentBeaconBlockRoot")));
        p.anchorBlockNumber = _anchorNumber(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(1), K1);
        rollup.advance(p, 1);
        assertEq(rollup.anchorBlockNumber(), _anchorNumber(0));
    }

    /// A fork that registers a new key moves rollups that follow the current
    /// entry, while pinned rollups keep the key they chose.
    function test_keyRotation() public {
        TestNativeRollup pinned = _deploy(NativeRollup.VkPolicy.Pinned, K1);
        _register(K2, schemaId);

        _setL1Context(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        vm.expectRevert(bytes("wrong verification key"));
        rollup.advance(_params(0), 1);
        _advance(rollup, 0, K2);

        _advance(pinned, 0, K1);
        assertEq(pinned.blockNumber(), 1);
    }

    /// A key whose program accepts a different input schema yields a
    /// different public input, so proofs under the old schema are rejected.
    function test_schemaIdFromRegistry() public {
        _register(K2, schemaId + 1);
        _setL1Context(0);
        rollup.setDependency(LEANSTARK, _publicInputRoot(0), K2);
        vm.expectRevert(bytes("root mismatch"));
        rollup.advance(_params(0), 1);
    }

    function test_measureAdvanceGas() public {
        for (uint256 i = 0; json.keyExists(_block(i)); i++) {
            _setL1Context(i);
            rollup.setDependency(LEANSTARK, _publicInputRoot(i), K1);
            NativeRollup.BlockParams memory p = _params(i);
            vm.cool(REGISTRY);
            vm.cool(address(rollup));
            uint256 g = gasleft();
            rollup.advance(p, 1);
            console.log("advance gas %d (%d blobs)", g - gasleft(), p.payloadBlobCount);
        }
    }
}
