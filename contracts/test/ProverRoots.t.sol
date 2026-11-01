// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";

import {Ssz} from "../src/libs/Ssz.sol";
import {NativeRollupSsz} from "../src/NativeRollupSsz.sol";

/// @notice Checks the contract's roots against requests that the EIP-8025
///         provers commit to (`script/prover_vectors.py`): a real devnet block
///         and two edge cases, generated with ere-guests, the guest code the
///         ethrex and reth provers run.
/// @dev    The contract follows the consensus-specs Gloas types, which
///         execution-specs #3913 matches. ere-guests hashes the request as a
///         plain container with a progressive list of versioned hashes, so its
///         roots differ until the guests adopt the Gloas layout.
contract ProverRootsTest is Test {
    using stdJson for string;

    string json;

    function setUp() public {
        json = vm.readFile("test/prover_vectors.json");
    }

    function _case(uint256 i) internal pure returns (string memory) {
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

    /// @dev ere-guests' request root: a plain four-field container whose
    ///      versioned hashes are a progressive list.
    function _guestRequestRoot(
        bytes32 payload,
        bytes32[] memory hashes,
        bytes32 parentBeaconBlockRoot,
        bytes32 requests
    ) internal view returns (bytes32) {
        uint256 n = hashes.length;
        uint256 ptr;
        assembly ("memory-safe") {
            ptr := add(hashes, 0x20)
        }
        bytes32 vh = Ssz.sha(Ssz.progressiveInPlace(ptr, n), Ssz.le256(n));
        return Ssz.sha(Ssz.sha(payload, vh), Ssz.sha(parentBeaconBlockRoot, requests));
    }

    function test_zkboostInputIsBlock93354() public view {
        assertTrue(json.readBool(".zkboostRequestMatchesBlock93354"));
        assertEq(json.readBytes(".zkboostSchemaId"), hex"1501");
    }

    function test_rootsMatchExecutionSpecs() public view {
        uint64 chainId = uint64(json.readUint(".chainId"));
        uint16 schemaId = uint16(json.readUint(".schemaId"));
        for (uint256 i = 0; json.keyExists(_case(i)); i++) {
            string memory c = _case(i);
            bytes32 payload = NativeRollupSsz.executionPayloadRoot(_header(c));
            assertEq(payload, json.readBytes32(string.concat(c, ".payloadRoot")), "payload root");

            bytes32 vh =
                NativeRollupSsz.versionedHashesRoot(json.readBytes32Array(string.concat(c, ".versionedHashes")));
            assertEq(vh, json.readBytes32(string.concat(c, ".versionedHashesRoot")), "versioned hashes root");

            bytes32 npRoot = NativeRollupSsz.newPayloadRequestRoot(
                payload,
                vh,
                json.readBytes32(string.concat(c, ".parentBeaconBlockRoot")),
                json.readBytes32(string.concat(c, ".executionRequestsRoot"))
            );
            assertEq(npRoot, json.readBytes32(string.concat(c, ".specRoot")), "request root");

            assertEq(
                NativeRollupSsz.publicInputRoot(npRoot, chainId, schemaId),
                json.readBytes32(string.concat(c, ".publicInputRoot")),
                "public input root"
            );
        }
    }

    /// @notice The provers' roots follow from the same payload and requests
    ///         roots, so the request layout is the only difference.
    function test_proverRootsDifferOnlyInRequestLayout() public view {
        for (uint256 i = 0; json.keyExists(_case(i)); i++) {
            string memory c = _case(i);
            bytes32 payload = NativeRollupSsz.executionPayloadRoot(_header(c));
            bytes32[] memory hashes = json.readBytes32Array(string.concat(c, ".versionedHashes"));
            bytes32 pbbr = json.readBytes32(string.concat(c, ".parentBeaconBlockRoot"));
            bytes32 requests = json.readBytes32(string.concat(c, ".executionRequestsRoot"));
            bytes32 guestRoot = json.readBytes32(string.concat(c, ".guestRoot"));

            bytes32 contractRoot = NativeRollupSsz.newPayloadRequestRoot(
                payload, NativeRollupSsz.versionedHashesRoot(hashes), pbbr, requests
            );
            assertNotEq(contractRoot, guestRoot, "layouts agree");
            // Last: the progressive merkleization overwrites `hashes`.
            assertEq(_guestRequestRoot(payload, hashes, pbbr, requests), guestRoot, "ere-guests root");
        }
    }
}
