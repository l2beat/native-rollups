// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

import {NativeRollup} from "./NativeRollup.sol";
import {MptProof} from "./libs/MptProof.sol";

/// @notice The book's preconfirmations customization: a native rollup whose
///         blocks only its sequencer posts, and which slashes the
///         sequencer's bond when a block it preconfirmed is not the one the
///         rollup has at that height.
/// @dev    A preconfirmation is the sequencer's signature over a block's
///         number, hash and anchor block number. Users check, when they
///         receive one, that the anchor block number is that of the anchor
///         in the block's header.
abstract contract SequencedNativeRollup is NativeRollup {
    struct Preconfirmation {
        uint64 number;
        bytes32 blockHash;
        uint64 anchorBlockNumber;
    }

    // EIP-2935 history contract, at the same address on L1 and L2. It keeps
    // the hashes of the last HISTORY_WINDOW blocks.
    address internal constant HISTORY = 0x0000F90827F1C53a10cb7A02335B175320002935;
    uint256 internal constant HISTORY_WINDOW = 8191;
    // Position of `parent_beacon_block_root`, the L1 anchor, in an L2 header.
    uint256 internal constant HEADER_ANCHOR = 19;
    // How long the sequencer may go without posting before anyone may post.
    // Longer than the posting deadline, an hour at most (see `advance`), so
    // that the sequencer's preconfirmed blocks are posted or dead by then.
    uint256 public constant SEQUENCER_TIMEOUT = 2 hours;
    // How long a bond withdrawal waits, in which the sequencer can still be
    // slashed for its earlier preconfirmations.
    uint256 public constant BOND_WITHDRAWAL_DELAY = 1 days;

    address public immutable sequencer;
    uint256 public bond;
    uint256 public withdrawalAmount;
    uint64 public withdrawalTime;
    // L1 timestamp of the last `advance`.
    uint64 public lastAdvance;

    event BondDeposited(address indexed from, uint256 amount);
    event BondWithdrawalRequested(uint256 amount);
    event BondWithdrawn(uint256 amount);
    event Slashed(uint64 indexed number, bytes32 preconfirmedHash, uint256 amount);

    constructor(address sequencer_) payable {
        sequencer = sequencer_;
        bond = msg.value;
        lastAdvance = uint64(block.timestamp);
    }

    modifier onlySequencer() {
        require(msg.sender == sequencer, "not the sequencer");
        _;
    }

    /// @notice Only the sequencer posts blocks, unless it posted none for
    ///         SEQUENCER_TIMEOUT.
    function advance(BlockParams calldata params, uint256 dependencyFrameIndex) public virtual override {
        require(
            msg.sender == sequencer || block.timestamp > lastAdvance + SEQUENCER_TIMEOUT, "not the sequencer"
        );
        lastAdvance = uint64(block.timestamp);
        super.advance(params, dependencyFrameIndex);
    }

    /// @notice What the sequencer signs to preconfirm a block.
    function preconfirmationDigest(Preconfirmation calldata p) public view returns (bytes32) {
        return keccak256(abi.encode(block.chainid, address(this), p.number, p.blockHash, p.anchorBlockNumber));
    }

    // Bond

    function depositBond() external payable {
        bond += msg.value;
        emit BondDeposited(msg.sender, msg.value);
    }

    function requestBondWithdrawal(uint256 amount) external onlySequencer {
        withdrawalAmount = amount;
        withdrawalTime = uint64(block.timestamp);
        emit BondWithdrawalRequested(amount);
    }

    function withdrawBond() external onlySequencer {
        require(withdrawalTime != 0 && block.timestamp >= withdrawalTime + BOND_WITHDRAWAL_DELAY, "withdrawal pending");
        uint256 amount = withdrawalAmount < bond ? withdrawalAmount : bond;
        bond -= amount;
        withdrawalAmount = 0;
        withdrawalTime = 0;
        (bool ok,) = sequencer.call{value: amount}("");
        require(ok, "withdrawal failed");
        emit BondWithdrawn(amount);
    }

    // Slashing

    /// @notice Slashes the sequencer for preconfirming two blocks at the same
    ///         height.
    function slashEquivocation(
        Preconfirmation calldata a,
        bytes calldata signatureA,
        Preconfirmation calldata b,
        bytes calldata signatureB
    ) external {
        require(a.number == b.number && a.blockHash != b.blockHash, "not an equivocation");
        _requireSigned(a, signatureA);
        _requireSigned(b, signatureB);
        _slash(a);
    }

    /// @notice Slashes the sequencer for a preconfirmed block that is not the
    ///         rollup's block at its height, whether another block took its
    ///         place or it expired, unless an L1 reorg removed its anchor.
    /// @param header       RLP of the preconfirmed block's header, for its
    ///                     anchor.
    /// @param historyBlock An L2 block after `p.number`, at most
    ///                     HISTORY_WINDOW blocks later, whose state holds the
    ///                     hash of the rollup's block at `p.number` in the L2
    ///                     history contract. Unused for the latest block.
    function slashDivergence(
        Preconfirmation calldata p,
        bytes calldata signature,
        bytes calldata header,
        uint256 historyBlock,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) external {
        _requireSigned(p, signature);
        require(keccak256(header) == p.blockHash, "not the preconfirmed header");
        bytes calldata anchor = MptProof.listItem(header, HEADER_ANCHOR);
        require(anchor.length == 32 && _l1BlockHash(p.anchorBlockNumber) == bytes32(anchor), "anchor not canonical");
        require(
            _postedHash(p.number, historyBlock, accountProof, storageProof) != p.blockHash, "block posted as preconfirmed"
        );
        _slash(p);
    }

    /// @notice The hash of the rollup's block `number`: its latest block, or
    ///         one the L2 history contract recorded.
    function _postedHash(
        uint256 number,
        uint256 historyBlock,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) internal view returns (bytes32) {
        require(number <= blockNumber, "block not posted");
        if (number == blockNumber) return blockHash;
        // Block `number + 1` records the hash of block `number`.
        require(historyBlock > number && historyBlock - number <= HISTORY_WINDOW, "block not in history");
        return bytes32(
            MptProof.storageValue(
                stateRootAt(historyBlock), HISTORY, bytes32(number % HISTORY_WINDOW), accountProof, storageProof
            )
        );
    }

    /// @notice The hash of a recent L1 block, from the L1 history contract.
    function _l1BlockHash(uint256 number) internal view returns (bytes32) {
        (bool ok, bytes memory out) = HISTORY.staticcall(abi.encode(number));
        require(ok && out.length == 32, "anchor not in history");
        return abi.decode(out, (bytes32));
    }

    function _requireSigned(Preconfirmation calldata p, bytes calldata signature) internal view {
        require(ECDSA.recover(preconfirmationDigest(p), signature) == sequencer, "not signed by the sequencer");
    }

    /// @dev Burns the bond: paying whoever proves the fault would let the
    ///      sequencer prove its own and keep it.
    function _slash(Preconfirmation calldata p) internal {
        uint256 amount = bond;
        require(amount > 0, "no bond");
        bond = 0;
        (bool ok,) = address(0).call{value: amount}("");
        require(ok, "burn failed");
        emit Slashed(p.number, p.blockHash, amount);
    }
}
