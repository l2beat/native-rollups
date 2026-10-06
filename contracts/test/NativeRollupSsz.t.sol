// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";

import {Ssz} from "../src/libs/Ssz.sol";
import {NativeRollupSsz} from "../src/NativeRollupSsz.sol";

/// @dev Exposes the library so memory arguments can be passed from tests.
contract Harness {
    function executionPayloadRoot(NativeRollupSsz.ExecutionPayloadHeader memory h) external view returns (bytes32) {
        return NativeRollupSsz.executionPayloadRoot(h);
    }

    function versionedHashesRoot(bytes32[] memory hashes) external view returns (bytes32) {
        return NativeRollupSsz.versionedHashesRoot(hashes);
    }

    function newPayloadRequestRoot(bytes32 a, bytes32 b, bytes32 c, bytes32 d) external view returns (bytes32) {
        return NativeRollupSsz.newPayloadRequestRoot(a, b, c, d);
    }

    function publicInputRoot(bytes32 npRoot, uint64 chainId, uint16 schemaId) external view returns (bytes32) {
        return NativeRollupSsz.publicInputRoot(npRoot, chainId, schemaId);
    }

    function zeroHash(uint256 depth) external pure returns (bytes32) {
        return Ssz.zeroHash(depth);
    }

    function sha(bytes32 a, bytes32 b) external view returns (bytes32) {
        return Ssz.sha(a, b);
    }

    /// @dev Gas of each step, measured inside the call.
    function measure(NativeRollupSsz.ExecutionPayloadHeader memory p, bytes32[] memory hashes)
        external
        view
        returns (uint256 payloadGas, uint256 hashesGas, uint256 requestGas, uint256 publicInputGas)
    {
        uint256 g = gasleft();
        bytes32 payload = NativeRollupSsz.executionPayloadRoot(p);
        payloadGas = g - gasleft();
        g = gasleft();
        bytes32 vh = NativeRollupSsz.versionedHashesRoot(hashes);
        hashesGas = g - gasleft();
        g = gasleft();
        bytes32 npRoot = NativeRollupSsz.newPayloadRequestRoot(payload, vh, bytes32(0), bytes32(0));
        requestGas = g - gasleft();
        g = gasleft();
        NativeRollupSsz.publicInputRoot(npRoot, 1, 1);
        publicInputGas = g - gasleft();
    }
}

/// @notice Checks every root against values computed with the compiled
///         consensus-specs types (`script/native_rollup_vectors.py`).
contract NativeRollupSszTest is Test {
    using stdJson for string;

    string json;
    Harness h;

    function setUp() public {
        json = vm.readFile("test/native_rollup_vectors.json");
        h = new Harness();
    }

    function _case(uint256 i) internal view returns (string memory) {
        return string.concat(".cases[", vm.toString(i), "]");
    }

    function _header(string memory c) internal view returns (NativeRollupSsz.ExecutionPayloadHeader memory p) {
        string memory k = string.concat(c, ".header");
        p.parentHash = json.readBytes32(string.concat(k, ".parentHash"));
        p.feeRecipient = json.readAddress(string.concat(k, ".feeRecipient"));
        p.stateRoot = json.readBytes32(string.concat(k, ".stateRoot"));
        p.receiptsRoot = json.readBytes32(string.concat(k, ".receiptsRoot"));
        p.logsBloom = json.readBytes(string.concat(k, ".logsBloom"));
        p.prevRandao = json.readBytes32(string.concat(k, ".prevRandao"));
        p.blockNumber = uint64(json.readUint(string.concat(k, ".blockNumber")));
        p.gasLimit = uint64(json.readUint(string.concat(k, ".gasLimit")));
        p.gasUsed = uint64(json.readUint(string.concat(k, ".gasUsed")));
        p.timestamp = uint64(json.readUint(string.concat(k, ".timestamp")));
        p.extraData = json.readBytes(string.concat(k, ".extraData"));
        p.baseFeePerGas = json.readUint(string.concat(k, ".baseFeePerGas"));
        p.blockHash = json.readBytes32(string.concat(k, ".blockHash"));
        p.transactionsRoot = json.readBytes32(string.concat(k, ".transactionsRoot"));
        p.withdrawalsRoot = json.readBytes32(string.concat(k, ".withdrawalsRoot"));
        p.blobGasUsed = uint64(json.readUint(string.concat(k, ".blobGasUsed")));
        p.excessBlobGas = uint64(json.readUint(string.concat(k, ".excessBlobGas")));
        p.blockAccessListRoot = json.readBytes32(string.concat(k, ".blockAccessListRoot"));
        p.slotNumber = uint64(json.readUint(string.concat(k, ".slotNumber")));
    }

    function test_zeroHashes() public view {
        bytes32 z;
        for (uint256 d = 1; d <= 12; d++) {
            z = h.sha(z, z);
            assertEq(h.zeroHash(d), z);
        }
    }

    function test_emptyListRoot() public view {
        assertEq(Ssz.ZERO_1, json.readBytes32(".emptyListRoot"));
    }

    function test_rootsMatchConsensusSpecs() public view {
        uint64 chainId = uint64(json.readUint(".chainId"));
        uint16 schemaId = uint16(json.readUint(".schemaId"));
        for (uint256 i = 0; json.keyExists(_case(i)); i++) {
            string memory c = _case(i);
            bytes32 payload = h.executionPayloadRoot(_header(c));
            assertEq(payload, json.readBytes32(string.concat(c, ".payloadRoot")), "payload root");

            bytes32 vh = h.versionedHashesRoot(json.readBytes32Array(string.concat(c, ".versionedHashes")));
            assertEq(vh, json.readBytes32(string.concat(c, ".versionedHashesRoot")), "versioned hashes root");

            bytes32 npRoot = h.newPayloadRequestRoot(
                payload,
                vh,
                json.readBytes32(string.concat(c, ".parentBeaconBlockRoot")),
                json.readBytes32(string.concat(c, ".executionRequestsRoot"))
            );
            assertEq(npRoot, json.readBytes32(string.concat(c, ".newPayloadRequestRoot")), "request root");

            assertEq(
                h.publicInputRoot(npRoot, chainId, schemaId),
                json.readBytes32(string.concat(c, ".publicInputRoot")),
                "public input root"
            );
        }
    }

    function test_measureGas() public view {
        bytes32[] memory hashes = json.readBytes32Array(".cases[2].versionedHashes");
        (uint256 payloadGas, uint256 hashesGas, uint256 requestGas, uint256 publicInputGas) =
            h.measure(_header(_case(2)), hashes);
        console.log("payload root gas            %d", payloadGas);
        console.log("versioned hashes root gas   %d (%d hashes)", hashesGas, hashes.length);
        console.log("request root gas            %d", requestGas);
        console.log("public input root gas       %d", publicInputGas);
    }
}
