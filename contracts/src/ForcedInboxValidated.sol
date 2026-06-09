// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Hashes} from "@openzeppelin/contracts/utils/cryptography/Hashes.sol";
import {RLP} from "@openzeppelin/contracts/utils/RLP.sol";

import {SecureMerkleTrie} from "./libs/SecureMerkleTrie.sol";
import {RLPReader} from "./libs/RLPReader.sol";

/// @notice Slice of the rollup the inbox reads at admission. Both getters
///         exist on the book's `NativeRollup`.
interface IRollup {
    function stateRootHistory(uint256 l2BlockNumber) external view returns (bytes32);
    function blockNumber() external view returns (uint256);
}

/// @title ForcedInboxValidated
/// @notice Forced-tx inbox. See `src/forced_transactions.md` for the design.
/// @dev Stores decoded EIP-1559 fields and re-RLP-encodes on-chain: the
///      contract needs to both compute the signing hash (for `ecrecover`)
///      and reproduce canonical raw bytes; decoding RLP in Solidity is
///      awkward, encoding it isn't.
contract ForcedInboxValidated {
    struct Tx1559 {
        uint256 chainId;
        uint256 nonce;
        uint256 maxPriorityFeePerGas;
        uint256 maxFeePerGas;
        uint256 gasLimit;
        address to;
        bool isCreation; // true => empty `to`
        uint256 value;
        bytes data;
        uint8 yParity;
        bytes32 r;
        bytes32 s;
    }

    /// @dev `l2TxHash` doubles as the queued sentinel: `== 0` iff no entry.
    ///      `l2BlockNumber` is the admission block; prune proofs must be
    ///      strictly newer, so historical state from before admission
    ///      cannot trigger the balance branch.
    struct Entry {
        bytes32 l2TxHash;
        uint64 nonce;
        uint64 gasLimit;
        uint64 l2BlockNumber;
        uint256 maxFeePerGas;
        uint256 value;
    }

    mapping(address => Entry) public entry;

    // Doubly-linked list, sorted by maxFeePerGas descending. On ties, the
    // later submission goes after the earlier one (FIFO at the tie).
    mapping(address => address) public prevQueued;
    mapping(address => address) public nextQueued;
    address public firstQueued;
    address public lastQueued; // tail = eviction target on cap
    uint256 public queuedCount;

    uint256 public immutable MAX_QUEUE_SIZE;
    address public immutable ROLLUP;

    /// @dev Mirrors L1's `BLOCKHASH` window as a familiar default. Pick to
    ///      taste based on L2 block time and L1 propagation.
    uint256 internal constant MAX_PROOF_AGE = 256;

    // Stateless validity constants, execution-specs (amsterdam).
    uint256 private constant TX_MAX_GAS_LIMIT = 16_777_216; // EIP-7825
    uint256 private constant MAX_INIT_CODE_SIZE = 49152;    // EIP-3860
    uint256 private constant GAS_TX_BASE = 21000;
    uint256 private constant GAS_TX_DATA_TOKEN_FLOOR = 10;  // EIP-7623
    uint256 private constant GAS_TX_DATA_TOKEN_STANDARD = 4;
    uint256 private constant GAS_TX_CREATE = 32000;
    uint256 private constant GAS_CODE_INIT_PER_WORD = 2;

    event ForcedTx(address indexed sender, bytes32 indexed l2TxHash, bytes rawTx);
    event ForcedTxPruned(address indexed sender, bytes32 indexed l2TxHash);

    // Error names track geth where possible (core/txpool/errors.go,
    // core/error.go, core/vm/errors.go) so the two codebases line up.

    // validateTxBasics
    error GasLimitTooHigh();          // ErrGasLimitTooHigh
    error NonceMax();                 // ErrNonceMax
    error MaxInitCodeSizeExceeded();  // ErrMaxInitCodeSizeExceeded
    error IntrinsicGas();             // ErrIntrinsicGas
    error TipAboveFeeCap();           // ErrTipAboveFeeCap

    // ValidateTransactionWithState
    error NonceTooLow();              // ErrNonceTooLow
    error NonceTooHigh();             // ErrNonceTooHigh
    error InsufficientFunds();        // ErrInsufficientFunds

    // pool.add
    error ReplaceUnderpriced();       // ErrReplaceUnderpriced
    error Underpriced();              // ErrUnderpriced

    // inbox-specific
    error NotPrunable();
    error InvalidMaxQueueSize();
    error InvalidRollup();
    error UnknownBlock();
    error ProofTooStale();
    error ProofNotAfterAdmission();
    error ProofRegression();

    constructor(uint256 maxQueueSize, address rollup) {
        if (maxQueueSize == 0) revert InvalidMaxQueueSize();
        if (rollup == address(0)) revert InvalidRollup();
        MAX_QUEUE_SIZE = maxQueueSize;
        ROLLUP = rollup;
    }

    /// @notice Enqueue or replace the sender's forced EIP-1559 tx
    ///         (geth: `pool.add`). Anyone may submit; the signature authorizes.
    function add(
        Tx1559 calldata t,
        bytes[] calldata accountProof,
        uint256 l2BlockNumber
    ) external returns (address sender) {
        _validateTxBasics(t);
        sender = _validateSignature(t);

        bytes32 stateRoot = _recentStateRootAt(l2BlockNumber);
        (uint64 provenNonce, uint256 provenBalance) =
            _extractAccountState(sender, stateRoot, accountProof);
        _validateTxWithState(t, provenNonce, provenBalance);

        bytes memory rawTx = _encodeSigned(t);
        bytes32 l2TxHash = keccak256(rawTx);

        Entry storage e = entry[sender];
        if (e.l2TxHash != bytes32(0)) {
            // Replacement: strict bump on maxFeePerGas; resort by fee.
            if (t.maxFeePerGas <= e.maxFeePerGas) revert ReplaceUnderpriced();
            // Block cannot regress: prevents a relay holding a signed
            // replacement from setting `entry.l2BlockNumber` backward and
            // reopening the historical-state window for `prune`.
            if (l2BlockNumber < e.l2BlockNumber) revert ProofRegression();
            _linkRemove(sender);
        } else if (queuedCount >= MAX_QUEUE_SIZE) {
            // Cap hit: outbid the tail (lowest-fee entry) or revert.
            address tail = lastQueued;
            if (t.maxFeePerGas <= entry[tail].maxFeePerGas) revert Underpriced();
            bytes32 evictedHash = entry[tail].l2TxHash;
            _linkRemove(tail);
            delete entry[tail];
            emit ForcedTxPruned(tail, evictedHash);
        }

        e.l2TxHash = l2TxHash;
        e.nonce = uint64(t.nonce);
        e.gasLimit = uint64(t.gasLimit);
        e.l2BlockNumber = uint64(l2BlockNumber);
        e.maxFeePerGas = t.maxFeePerGas;
        e.value = t.value;

        _insertSorted(sender);

        emit ForcedTx(sender, l2TxHash, rawTx);
    }

    /// @notice Permissionless drop of `sender`'s entry. The caller supplies
    ///         an account proof at a block *strictly after* admission; the
    ///         entry is dropped if either the proven nonce has advanced
    ///         past `entry.nonce` (executed elsewhere) or the proven
    ///         balance no longer covers `gas*maxFee + value`.
    ///         Anti-censorship: fee is deliberately not a prune trigger.
    function prune(
        address sender,
        bytes[] calldata accountProof,
        uint256 l2BlockNumber
    ) external {
        Entry storage e = entry[sender];
        if (e.l2TxHash == bytes32(0)) revert NotPrunable();
        if (l2BlockNumber <= e.l2BlockNumber) revert ProofNotAfterAdmission();

        bytes32 stateRoot = _recentStateRootAt(l2BlockNumber);
        (uint64 provenNonce, uint256 provenBalance) =
            _extractAccountState(sender, stateRoot, accountProof);

        uint256 cost = uint256(e.gasLimit) * e.maxFeePerGas + e.value;
        bool stale = provenNonce > e.nonce;
        bool unaffordable = provenBalance < cost;
        if (!stale && !unaffordable) revert NotPrunable();

        bytes32 hash = e.l2TxHash;
        _linkRemove(sender);
        delete entry[sender];

        emit ForcedTxPruned(sender, hash);
    }

    /// @notice IL commitment for the next L2 block. Stops at the first
    ///         underpriced entry (rest are lower-or-equal fee) or when the
    ///         next entry overflows `gasBudget`. `ilHash == 0` iff
    ///         `count == 0`. Structural walk only: the contract has no
    ///         live snapshot of L2 state, so an entry whose sender has
    ///         since gone stale, unfunded, or underpriced vs the live
    ///         base fee still gets hashed in. Such entries are validly
    ///         excluded under FOCIL because `check_inclusion_list_transactions`
    ///         re-runs `check_transaction` at block-end state and accepts
    ///         the `NonceMismatchError` / `InsufficientBalanceError` /
    ///         `InsufficientMaxFeePerGasError` / `GasUsedExceedsLimitError`
    ///         exits. `prune` eventually clears them from the queue.
    function currentIL(uint256 gasBudget, uint256 baseFee)
        external
        view
        returns (bytes32 ilHash, uint256 count)
    {
        bytes32 acc = bytes32(0);
        uint256 used = 0;
        uint256 n = 0;
        address cur = firstQueued;
        while (cur != address(0)) {
            Entry storage e = entry[cur];
            // Fee-descending: once underpriced, every later entry is too.
            if (e.maxFeePerGas < baseFee) break;
            uint256 g = e.gasLimit;
            if (used + g > gasBudget) break;
            acc = Hashes.efficientKeccak256(acc, e.l2TxHash);
            used += g;
            n++;
            cur = nextQueued[cur];
        }
        return (acc, n);
    }

    // --- Helpers: signature, encoding, linked list ---

    function signingHash(Tx1559 calldata t) external pure returns (bytes32) {
        return _signingHash(t);
    }

    function _validateSignature(Tx1559 calldata t) private pure returns (address) {
        return ECDSA.recover(_signingHash(t), 27 + t.yParity, t.r, t.s);
    }

    /// @dev Runs before any state mutation, including cap-eviction, so a
    ///      bad submission can't collaterally evict the tail.
    function _validateTxWithState(
        Tx1559 calldata t,
        uint64 provenNonce,
        uint256 provenBalance
    ) private pure {
        if (t.nonce < provenNonce) revert NonceTooLow();
        if (t.nonce > provenNonce) revert NonceTooHigh();
        uint256 cost = uint256(t.gasLimit) * t.maxFeePerGas + t.value;
        if (provenBalance < cost) revert InsufficientFunds();
    }

    /// @dev Split from `_extractAccountState` to keep that one a pure
    ///      function over (sender, root, proof).
    function _recentStateRootAt(uint256 l2BlockNumber) private view returns (bytes32 stateRoot) {
        stateRoot = IRollup(ROLLUP).stateRootHistory(l2BlockNumber);
        if (stateRoot == bytes32(0)) revert UnknownBlock();
        unchecked {
            uint256 latestBlock = IRollup(ROLLUP).blockNumber();
            if (l2BlockNumber + MAX_PROOF_AGE < latestBlock) revert ProofTooStale();
        }
    }

    /// @dev MPT walk + RLP decode of the account leaf
    ///      `rlp([nonce, balance, storageHash, codeHash])`. Only the first
    ///      two fields are extracted; integers are stored with leading
    ///      zeros stripped. Bubbles up `MerkleTrie` errors if the proof
    ///      doesn't include the sender.
    function _extractAccountState(
        address sender,
        bytes32 stateRoot,
        bytes[] calldata accountProof
    ) internal pure returns (uint64 nonce, uint256 balance) {
        bytes[] memory proof = accountProof; // SecureMerkleTrie wants memory
        bytes memory rlpAccount =
            SecureMerkleTrie.get(abi.encodePacked(sender), proof, stateRoot);

        RLPReader.RLPItem[] memory fields = RLPReader.readList(rlpAccount);
        nonce = uint64(_rlpBytesToUint(RLPReader.readBytes(fields[0])));
        balance = _rlpBytesToUint(RLPReader.readBytes(fields[1]));
    }

    function _rlpBytesToUint(bytes memory b) private pure returns (uint256 result) {
        for (uint256 i; i < b.length; ++i) {
            result = (result << 8) | uint8(b[i]);
        }
    }

    /// @dev Mirrors geth's `validateTxBasics` plus the static subset of
    ///      execution-specs `check_transaction`. EIP-1559, empty access
    ///      list, not SetCode: AL/auth gas are zero by construction.
    function _validateTxBasics(Tx1559 calldata t) private pure {
        if (t.gasLimit > TX_MAX_GAS_LIMIT) revert GasLimitTooHigh();
        if (t.nonce >= type(uint64).max) revert NonceMax(); // nonce < 2**64 - 1
        if (t.isCreation && t.data.length > MAX_INIT_CODE_SIZE) revert MaxInitCodeSizeExceeded();
        if (t.maxFeePerGas < t.maxPriorityFeePerGas) revert TipAboveFeeCap();

        // EIP-7623 calldata tokens: 1 per zero byte, 4 per non-zero byte.
        uint256 zeros;
        for (uint256 i; i < t.data.length; ++i) {
            if (t.data[i] == bytes1(0)) ++zeros;
        }
        uint256 tokens = zeros + 4 * (t.data.length - zeros);

        uint256 floorGas = GAS_TX_BASE + tokens * GAS_TX_DATA_TOKEN_FLOOR;
        uint256 intrinsicGas = GAS_TX_BASE + tokens * GAS_TX_DATA_TOKEN_STANDARD;
        if (t.isCreation) {
            uint256 words = (t.data.length + 31) / 32;
            intrinsicGas += GAS_TX_CREATE + GAS_CODE_INIT_PER_WORD * words;
        }

        uint256 minGas = intrinsicGas > floorGas ? intrinsicGas : floorGas;
        if (minGas > t.gasLimit) revert IntrinsicGas();
    }

    /// @dev O(N). Assumes `entry[sender]` is populated and `sender` is not
    ///      currently in the list. Ties go after the existing same-fee entries.
    function _insertSorted(address sender) private {
        uint256 fee = entry[sender].maxFeePerGas;
        address cur = firstQueued;
        while (cur != address(0) && entry[cur].maxFeePerGas >= fee) {
            cur = nextQueued[cur];
        }
        // Insert `sender` before `cur` (or at tail if `cur == 0`).
        if (cur == address(0)) {
            address prev = lastQueued;
            prevQueued[sender] = prev;
            // nextQueued[sender] defaults to 0
            if (prev == address(0)) firstQueued = sender;
            else nextQueued[prev] = sender;
            lastQueued = sender;
        } else {
            address prev = prevQueued[cur];
            prevQueued[sender] = prev;
            nextQueued[sender] = cur;
            if (prev == address(0)) firstQueued = sender;
            else nextQueued[prev] = sender;
            prevQueued[cur] = sender;
        }
        queuedCount++;
    }

    function _linkRemove(address sender) private {
        address prev = prevQueued[sender];
        address next = nextQueued[sender];
        if (prev == address(0)) firstQueued = next;
        else nextQueued[prev] = next;
        if (next == address(0)) lastQueued = prev;
        else prevQueued[next] = prev;
        delete prevQueued[sender];
        delete nextQueued[sender];
        queuedCount--;
    }

    /// @dev keccak256(0x02 || rlp(unsigned-9-field-list)).
    function _signingHash(Tx1559 calldata t) private pure returns (bytes32) {
        return keccak256(abi.encodePacked(hex"02", RLP.encode(_unsignedItems(t))));
    }

    /// @dev 0x02 || rlp([...unsigned, yParity, r, s]).
    function _encodeSigned(Tx1559 calldata t) private pure returns (bytes memory) {
        bytes[] memory items = _unsignedItems(t);
        bytes[] memory signed = new bytes[](12);
        for (uint256 i; i < 9; ++i) {
            signed[i] = items[i];
        }
        signed[9] = RLP.encode(uint256(t.yParity));
        signed[10] = RLP.encode(uint256(t.r));
        signed[11] = RLP.encode(uint256(t.s));
        return abi.encodePacked(hex"02", RLP.encode(signed));
    }

    function _unsignedItems(Tx1559 calldata t) private pure returns (bytes[] memory items) {
        items = new bytes[](9);
        items[0] = RLP.encode(t.chainId);
        items[1] = RLP.encode(t.nonce);
        items[2] = RLP.encode(t.maxPriorityFeePerGas);
        items[3] = RLP.encode(t.maxFeePerGas);
        items[4] = RLP.encode(t.gasLimit);
        items[5] = t.isCreation ? RLP.encode(new bytes(0)) : RLP.encode(t.to);
        items[6] = RLP.encode(t.value);
        items[7] = RLP.encode(t.data);
        items[8] = RLP.encode(new bytes[](0)); // empty access list -> 0xc0
    }

}
