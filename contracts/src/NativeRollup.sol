// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {NativeRollupSsz} from "./NativeRollupSsz.sol";

/// @title NativeRollup
/// @notice The rollup contract of the book's Specification. Each `advance`
///         call adds one L2 block. The contract verifies no proof itself: it
///         rebuilds the root of the public output the L2 proof must have, and
///         requires the transaction's EIP-8288 dependency to carry that root
///         under the EVM verification key hash of the EIP-8357 registry. In a
///         valid L1 block, EIP-8288 guarantees that the dependency is proven.
/// @dev    Abstract only because reading the dependency frame needs the
///         EIP-8141 introspection instructions; see `_readDependency`.
abstract contract NativeRollup {
    struct BlockParams {
        // Constrained fields (validated by the L2 proof)
        bytes32 stateRoot;
        bytes32 receiptsRoot;
        bytes logsBloom; // 256 bytes
        uint64 gasUsed;
        uint64 timestamp;
        uint256 baseFeePerGas;
        bytes32 blockHash;
        bytes32 transactionsRoot;
        bytes32 blockAccessListRoot; // SSZ root of the progressive byte list containing RLP(BAL)
        // EIP-8142: number of blobs carrying L2 block data. It selects the
        // BLOBHASH values, and the versioned hashes root commits to their
        // number. EIP-8142 also adds it to the payload, but no consensus-specs
        // type places it yet, so it is not a payload leaf here.
        uint256 payloadBlobCount;
        bytes32 executionRequestsRoot; // any requests are accepted
        // Unconstrained fields (free operator inputs)
        address feeRecipient;
        bytes32 prevRandao;
        bytes extraData; // at most 32 bytes
    }

    enum VkPolicy {
        FollowCurrent,
        Pinned
    }

    uint8 internal constant LEANSTARK_SCHEME = 0x11; // EIP-8288
    address internal constant EVM_VK_REGISTRY = 0x00005e9c1447C1A05A642ec9eB76D9C125468357; // EIP-8357
    uint64 internal constant L2_SLOT_NUMBER = 0; // TBD
    // SSZ root of an empty progressive list: sha256 of 64 zero bytes.
    bytes32 internal constant EMPTY_LIST_ROOT = 0xf5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b;

    uint64 public immutable chainId;
    uint64 public immutable gasLimit;
    VkPolicy public immutable vkPolicy;
    bytes32 public immutable pinnedVkHash;

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    bytes32 public stateRoot;
    uint256 public blockNumber;

    // L2 state root history (for L2->L1 messaging via state proofs)
    mapping(uint256 => bytes32) public stateRootHistory;

    // L1->L2 message queue. Messages are stored in this contract's storage
    // and become accessible on L2 via storage proofs against the anchored L1
    // block hash.
    bytes32[] public pendingL1Messages;

    constructor(
        uint64 chainId_,
        uint64 gasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        VkPolicy vkPolicy_,
        bytes32 pinnedVkHash_
    ) {
        chainId = chainId_;
        gasLimit = gasLimit_;
        vkPolicy = vkPolicy_;
        pinnedVkHash = pinnedVkHash_;
        blockHash = genesisBlockHash;
        stateRoot = genesisStateRoot;
        stateRootHistory[0] = genesisStateRoot;
    }

    function sendMessage(address to, bytes calldata data) external payable {
        bytes32 messageHash =
            keccak256(abi.encodePacked(msg.sender, to, msg.value, keccak256(data), pendingL1Messages.length));
        pendingL1Messages.push(messageHash);
    }

    function advance(BlockParams calldata params, uint256 dependencyFrameIndex) external {
        // 1. Read the EIP-8288 dependency declared by this transaction.
        (uint8 scheme, bytes32 dataHash, bytes32 vkHash) = _readDependency(dependencyFrameIndex);
        require(scheme == LEANSTARK_SCHEME, "not a LeanSTARK dependency");

        // 2. Select the EVM verification key hash and its schema ID from the
        //    EIP-8357 registry. Zero selects the current entry.
        (bytes32 expectedVkHash, uint16 schemaId) =
            _readRegistry(vkPolicy == VkPolicy.FollowCurrent ? bytes32(0) : pinnedVkHash);
        require(vkHash == expectedVkHash, "wrong verification key");

        // 3. Rebuild the proof's public output from storage, calldata, the
        //    versioned hashes, and the L1 anchor, and compare it with the
        //    declared dependency.
        bytes32 npRoot = _newPayloadRequestRoot(params);
        require(dataHash == NativeRollupSsz.publicInputRoot(npRoot, chainId, schemaId), "root mismatch");

        // 4. Update onchain state.
        blockHash = params.blockHash;
        stateRoot = params.stateRoot;
        blockNumber = blockNumber + 1;
        stateRootHistory[blockNumber] = params.stateRoot;
    }

    function _newPayloadRequestRoot(BlockParams calldata params) internal view returns (bytes32) {
        NativeRollupSsz.ExecutionPayloadHeader memory header = NativeRollupSsz.ExecutionPayloadHeader({
            parentHash: blockHash, // from storage
            feeRecipient: params.feeRecipient,
            stateRoot: params.stateRoot,
            receiptsRoot: params.receiptsRoot,
            logsBloom: params.logsBloom,
            prevRandao: params.prevRandao,
            blockNumber: uint64(blockNumber + 1), // from storage
            gasLimit: gasLimit, // fixed
            gasUsed: params.gasUsed,
            timestamp: params.timestamp,
            extraData: params.extraData,
            baseFeePerGas: params.baseFeePerGas,
            blockHash: params.blockHash,
            transactionsRoot: params.transactionsRoot, // constrained (proven)
            withdrawalsRoot: EMPTY_LIST_ROOT, // no withdrawals on L2
            blobGasUsed: 0, // fixed for L2
            excessBlobGas: 0, // fixed for L2
            blockAccessListRoot: params.blockAccessListRoot, // constrained (proven)
            slotNumber: L2_SLOT_NUMBER // fixed for L2 (value TBD)
        });
        return NativeRollupSsz.newPayloadRequestRoot(
            NativeRollupSsz.executionPayloadRoot(header),
            NativeRollupSsz.versionedHashesRoot(_versionedHashes(params.payloadBlobCount)),
            blockhash(block.number - 1), // L1 anchor
            params.executionRequestsRoot // proven, not interpreted
        );
    }

    /// @dev Since L2 has no type-3 blob transactions, all blobs in the
    ///      transaction are payload blobs (EIP-8142 encoded L2 block data).
    function _versionedHashes(uint256 count) internal view returns (bytes32[] memory hashes) {
        hashes = new bytes32[](count);
        for (uint256 i = 0; i < count; i++) {
            hashes[i] = blobhash(i);
        }
    }

    function _readRegistry(bytes32 query) internal view returns (bytes32 vkHash, uint16 schemaId) {
        (bool ok, bytes memory out) = EVM_VK_REGISTRY.staticcall(abi.encode(query));
        require(ok && out.length == 64, "registry");
        (bytes32 hash, uint256 schema) = abi.decode(out, (bytes32, uint256));
        return (hash, uint16(schema));
    }

    /// @notice The `(scheme, data_hash, verification_key_hash)` triple of the
    ///         EIP-8288 dependency frame at `frameIndex`.
    /// @dev    With EIP-8141, this requires `FRAMEPARAM(0x02, frameIndex)` to
    ///         be the dependency frame mode and `FRAMEPARAM(0x04, frameIndex)`
    ///         to be 96, a single triple, and reads it with `FRAMEDATACOPY`.
    ///         Solidity cannot express these instructions yet.
    function _readDependency(uint256 frameIndex)
        internal
        view
        virtual
        returns (uint8 scheme, bytes32 dataHash, bytes32 vkHash);
}
