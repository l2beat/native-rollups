// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {NativeRollupSsz} from "./NativeRollupSsz.sol";
import {Message, Messages} from "./libs/Messages.sol";
import {MessageTree, Tree} from "./libs/MessageTree.sol";

/// @title NativeRollup
/// @notice The rollup contract of the book's Specification. Each `advance`
///         call adds one L2 block. The contract verifies no proof itself: it
///         rebuilds the root of the public output the L2 proof must have, and
///         requires the transaction's EIP-8288 dependency to carry that root
///         under the EVM verification key hash of the EIP-8357 registry. In a
///         valid L1 block, EIP-8288 guarantees that the dependency is proven.
/// @dev    Abstract only because reading the dependency frame and the state
///         gas left need the EIP-8141 introspection instructions; see
///         `_readDependency` and `_stateGasLeft`.
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
        // L1 block whose hash becomes the L1 anchor. Chosen by the operator,
        // since the anchor enters L2 state and must be known before proving.
        uint256 anchorBlockNumber;
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
    uint64 internal constant L2_SLOT_NUMBER = 0; // TBD
    // SSZ root of an empty progressive list: sha256 of 64 zero bytes.
    bytes32 internal constant EMPTY_LIST_ROOT = 0xf5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b;
    // Storage slot of `L2Messenger.sentMessages`.
    uint256 internal constant L2_QUEUE_SLOT = 2;
    // Number of recent L2 state roots kept, as EIP-2935 keeps L1 block
    // hashes. A ring buffer only creates new storage for the first blocks.
    uint256 public constant STATE_ROOT_HISTORY = 8191;
    // How far L2 time may lag L1 time. A block must reach L1 while its anchor
    // is in the BLOCKHASH window, 256 L1 blocks, under an hour, so the bound
    // only rejects blocks whose builder held L2 time back.
    uint256 public constant MAX_TIMESTAMP_LAG = 1 hours;

    // The EIP-8357 registry, at 0x00005e9c1447C1A05A642ec9eB76D9C125468357
    // on chains that activate it.
    address public immutable evmVkRegistry;
    uint64 public immutable chainId;
    uint64 public immutable l2GasLimit;
    VkPolicy public immutable vkPolicy;
    bytes32 public immutable pinnedVkHash;
    // The L2 messenger predeploy, whose queue holds the L2 to L1 messages.
    address public immutable l2Messenger;

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    uint64 public blockNumber;
    uint64 public anchorBlockNumber;

    // State roots of the last STATE_ROOT_HISTORY L2 blocks, by block number
    // modulo STATE_ROOT_HISTORY (for L2->L1 messaging via state proofs).
    mapping(uint256 => bytes32) internal stateRootHistory;

    // L1->L2 messages, as a Merkle tree of their hashes. L2 proves its root
    // against the anchored L1 block hash, then each message's path.
    Tree internal l1Messages;

    // L2->L1 messages already delivered, as a bitmap (see Messages), and the
    // sender of the one being delivered.
    mapping(uint256 => uint256) internal claimedL2MessageBits;
    address internal transient currentL2Sender;

    constructor(
        uint64 chainId_,
        uint64 l2GasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        VkPolicy vkPolicy_,
        bytes32 pinnedVkHash_,
        address evmVkRegistry_,
        address l2Messenger_
    ) {
        evmVkRegistry = evmVkRegistry_;
        l2Messenger = l2Messenger_;
        chainId = chainId_;
        l2GasLimit = l2GasLimit_;
        vkPolicy = vkPolicy_;
        pinnedVkHash = pinnedVkHash_;
        blockHash = genesisBlockHash;
        stateRootHistory[0] = genesisStateRoot;
    }

    /// @notice Emitted so the message can be claimed on L2, where only its
    ///         hash is proven.
    event L1MessageSent(
        uint256 indexed index,
        address indexed sender,
        address indexed to,
        uint256 value,
        uint256 fee,
        uint256 gasLimit,
        bytes data
    );

    /// @notice Emitted for each L2 block, so that nodes can find the
    ///         transactions that carry the L2 blocks and their blobs.
    event BlockAdded(uint64 indexed number, bytes32 blockHash);
    event L2MessageClaimed(
        uint256 indexed index, address indexed sender, address indexed to, uint256 value, uint256 fee, address feeRecipient
    );

    /// @notice Sends `msg.value - fee` to `to` on L2, with a call that gets
    ///         `gasLimit`, and `fee` to whoever claims the message there.
    function sendMessage(address to, uint256 fee, uint256 gasLimit, bytes calldata data) external payable {
        require(msg.value >= fee, "fee exceeds value");
        // A call from the L2 messenger to itself could send an L2 to L1
        // message in its name.
        require(to != l2Messenger, "message to the messenger");
        uint256 index = l1Messages.count;
        MessageTree.insert(l1Messages, Messages.hash(msg.sender, to, msg.value - fee, fee, gasLimit, data, index));
        emit L1MessageSent(index, msg.sender, to, msg.value - fee, fee, gasLimit, data);
    }

    function l1MessageRoot() external view returns (bytes32) {
        return l1Messages.root;
    }

    function l1MessageCount() external view returns (uint256) {
        return l1Messages.count;
    }

    function stateRoot() external view returns (bytes32) {
        return stateRootHistory[blockNumber % STATE_ROOT_HISTORY];
    }

    /// @notice The state root of a recent L2 block.
    function stateRootAt(uint256 l2BlockNumber) public view returns (bytes32) {
        require(
            l2BlockNumber <= blockNumber && blockNumber - l2BlockNumber < STATE_ROOT_HISTORY, "L2 block not in history"
        );
        return stateRootHistory[l2BlockNumber % STATE_ROOT_HISTORY];
    }

    function claimedL2Messages(uint256 index) external view returns (bool) {
        return Messages.isClaimed(claimedL2MessageBits, index);
    }

    /// @notice The L2 sender of the message being delivered.
    function l2Sender() external view returns (address) {
        require(currentL2Sender != address(0), "no message");
        return currentL2Sender;
    }

    /// @notice Delivers an L2 to L1 message, proven against the state root of
    ///         any recent L2 block since it was sent, and pays its value and
    ///         fee out of the ETH escrowed by L1 to L2 messages. Sent messages stay in
    ///         the messenger's storage, so a proof can always use a recent
    ///         root.
    /// @param accountProof Proof of the L2 messenger's account in that block's
    ///                     state.
    /// @param storageProof Proof of the queue entry in its storage.
    /// @param feeRecipient Who receives the message's fee.
    function claimL2Message(
        Message calldata m,
        uint256 l2BlockNumber,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof,
        address feeRecipient
    ) external {
        Messages.markClaimed(claimedL2MessageBits, m.index);
        Messages.requireQueued(m, stateRootAt(l2BlockNumber), l2Messenger, L2_QUEUE_SLOT, accountProof, storageProof);

        currentL2Sender = m.sender;
        Messages.deliver(m, feeRecipient, _stateGasLeft());
        currentL2Sender = address(0);
        emit L2MessageClaimed(m.index, m.sender, m.to, m.value, m.fee, feeRecipient);
    }

    function advance(BlockParams calldata params, uint256 dependencyFrameIndex) public virtual {
        // 1. Read the EIP-8288 dependency declared by this transaction.
        (uint8 scheme, bytes32 dataHash, bytes32 vkHash) = _readDependency(dependencyFrameIndex);
        require(scheme == LEANSTARK_SCHEME, "not a LeanSTARK dependency");

        // 2. Select the EVM verification key hash and its schema ID from the
        //    EIP-8357 registry. Zero selects the current entry.
        (bytes32 expectedVkHash, uint16 schemaId) =
            _readRegistry(vkPolicy == VkPolicy.FollowCurrent ? bytes32(0) : pinnedVkHash);
        require(vkHash == expectedVkHash, "wrong verification key");

        // 3. Bound the L2 timestamp by L1 time. The program only requires
        //    timestamps to increase, so a block at the maximum timestamp
        //    would halt the rollup, since no block could follow it, and
        //    blocks far in the past would hold L2 time back, past users'
        //    deadlines.
        require(params.timestamp <= block.timestamp, "timestamp in the future");
        require(params.timestamp + MAX_TIMESTAMP_LAG >= block.timestamp, "timestamp too old");

        // 4. Rebuild the proof's public output from storage, calldata, the
        //    versioned hashes, and the L1 anchor, and compare it with the
        //    declared dependency.
        bytes32 npRoot = _newPayloadRequestRoot(params, _anchor(params.anchorBlockNumber));
        require(dataHash == NativeRollupSsz.publicInputRoot(npRoot, chainId, schemaId), "root mismatch");

        // 5. Update onchain state.
        uint64 number = blockNumber + 1;
        blockHash = params.blockHash;
        blockNumber = number;
        anchorBlockNumber = uint64(params.anchorBlockNumber); // a past L1 block, see _anchor
        stateRootHistory[number % STATE_ROOT_HISTORY] = params.stateRoot;
        emit BlockAdded(number, params.blockHash);
    }

    /// @notice The L1 anchor: the hash of a recent L1 block, which the proof
    ///         binds as `parent_beacon_block_root`. It must not move backwards
    ///         and must be within the `BLOCKHASH` window of the last 256 blocks.
    function _anchor(uint256 number) internal view returns (bytes32 anchor) {
        require(number >= anchorBlockNumber, "anchor moved backwards");
        anchor = blockhash(number);
        require(anchor != bytes32(0), "anchor not available");
    }

    function _newPayloadRequestRoot(BlockParams calldata params, bytes32 anchor) internal view returns (bytes32) {
        // Assigned field by field: a struct literal keeps all 19 values on the
        // stack at once.
        NativeRollupSsz.ExecutionPayloadHeader memory header;
        header.parentHash = blockHash; // from storage
        header.feeRecipient = params.feeRecipient;
        header.stateRoot = params.stateRoot;
        header.receiptsRoot = params.receiptsRoot;
        header.logsBloom = params.logsBloom;
        header.prevRandao = params.prevRandao;
        header.blockNumber = blockNumber + 1; // from storage
        header.gasLimit = l2GasLimit; // fixed
        header.gasUsed = params.gasUsed;
        header.timestamp = params.timestamp;
        header.extraData = params.extraData;
        header.baseFeePerGas = params.baseFeePerGas;
        header.blockHash = params.blockHash;
        header.transactionsRoot = params.transactionsRoot; // constrained (proven)
        header.withdrawalsRoot = EMPTY_LIST_ROOT; // no withdrawals on L2
        header.blobGasUsed = 0; // fixed for L2
        header.excessBlobGas = 0; // fixed for L2
        header.blockAccessListRoot = params.blockAccessListRoot; // constrained (proven)
        header.slotNumber = L2_SLOT_NUMBER; // fixed for L2 (value TBD)
        return NativeRollupSsz.newPayloadRequestRoot(
            NativeRollupSsz.executionPayloadRoot(header),
            NativeRollupSsz.versionedHashesRoot(_versionedHashes(params.payloadBlobCount)),
            anchor, // L1 anchor
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
        (bool ok, bytes memory out) = evmVkRegistry.staticcall(abi.encode(query));
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

    /// @notice The state gas a call can draw on (see `Messages.deliver`).
    /// @dev    In an EIP-8141 frame transaction, `TXPARAM(0x0C)`, which
    ///         Solidity cannot express yet.
    function _stateGasLeft() internal view virtual returns (uint256);
}
