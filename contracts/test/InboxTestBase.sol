// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {RLP} from "@openzeppelin/contracts/utils/RLP.sol";
import "../src/ForcedInboxValidated.sol";
import "./mocks/MockRollup.sol";

/// @notice Shared base for the inbox test suite. Provides:
///         - `setUp` that deploys a `MockRollup` and a `ForcedInboxValidated`
///           wired to it,
///         - the canonical sample tx fields used across tests,
///         - `_buildAccountProof` to mint single-leaf MPT proofs for tests
///           that need to exercise the proof-based admission path,
///         - convenience submitters (`_submitSample`, `_submitWithFeeOn`)
///           that publish a fresh state root for the signer and then submit.
abstract contract InboxTestBase is Test {
    /// `keccak256("")`. Used as the default codeHash field when building
    /// account leaves in test proofs.
    bytes32 internal constant EMPTY_CODE_HASH =
        0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470;

    ForcedInboxValidated inbox;
    MockRollup mockRollup;

    /// Per-test counter so each `_submitSample`/`_submitWithFeeOn` call gets
    /// a distinct L2 block number, since each sender's proof is built against
    /// its own single-leaf trie root. Resets to 0 in every `setUp`.
    uint256 internal l2BlockCounter;

    function setUp() public virtual {
        mockRollup = new MockRollup();
        inbox = new ForcedInboxValidated(256, address(mockRollup));
        l2BlockCounter = 0;
    }

    function _sampleTx() internal pure returns (ForcedInboxValidated.Tx1559 memory t) {
        t = ForcedInboxValidated.Tx1559({
            chainId: 10,
            nonce: 5,
            maxPriorityFeePerGas: 1e9, // 1 gwei
            maxFeePerGas: 20e9, // 20 gwei
            gasLimit: 21000,
            to: 0x000000000000000000000000000000000000bEEF,
            isCreation: false,
            value: 1 ether,
            data: hex"",
            yParity: 0,
            r: bytes32(0),
            s: bytes32(0)
        });
    }

    /// Test-side equivalent of "is this sender queued?", reads the stored
    /// `l2TxHash` sentinel via the entry getter on the given inbox.
    function _isQueued(ForcedInboxValidated _inbox, address sender) internal view returns (bool) {
        (bytes32 h,,,,,) = _inbox.entry(sender);
        return h != bytes32(0);
    }

    /// Read the admission block recorded on the entry.
    function _entryAdmissionBlock(ForcedInboxValidated _inbox, address sender)
        internal
        view
        returns (uint64 admissionBlock)
    {
        (,,, admissionBlock,,) = _inbox.entry(sender);
    }

    /// Build a single-leaf Merkle-Patricia trie containing exactly one
    /// account, and return its root and the (one-element) inclusion proof.
    /// The trie shape is:
    ///   leaf = rlp([HP(keccak256(account)), rlp([nonce, balance, 0, codeHash])])
    /// where `HP` prefixes 0x20 (leaf, even nibble count) before the 32-byte
    /// secure key. The proof is just the leaf node itself; the verifier
    /// hashes it and checks against `root`.
    function _buildAccountProof(
        address account,
        uint64 nonce,
        uint256 balance,
        bytes32 codeHash
    ) internal pure returns (bytes32 root, bytes[] memory proof) {
        // Account RLP: [nonce, balance, storageHash, codeHash]. Empty storage
        // for an EOA: storageHash defaults to keccak256(rlp([])), but the
        // inbox doesn't read it so we just pass bytes32(0).
        bytes[] memory accountItems = new bytes[](4);
        accountItems[0] = RLP.encode(uint256(nonce));
        accountItems[1] = RLP.encode(balance);
        accountItems[2] = RLP.encode(abi.encodePacked(bytes32(0)));
        accountItems[3] = RLP.encode(abi.encodePacked(codeHash));
        bytes memory accountRlp = RLP.encode(accountItems);

        // HP path: 0x20 || keccak256(account). 33 bytes. The 0x20 prefix is
        // "leaf with even nibble count" and the secure key is exactly 64
        // nibbles (32 bytes), so even-count applies.
        bytes32 secureKey = keccak256(abi.encodePacked(account));
        bytes memory hpPath = abi.encodePacked(bytes1(0x20), secureKey);

        // Leaf node: [hpPath, accountRlp]. Both items are byte strings as
        // far as the outer RLP encoding is concerned.
        bytes[] memory leafItems = new bytes[](2);
        leafItems[0] = RLP.encode(hpPath);
        leafItems[1] = RLP.encode(accountRlp);
        bytes memory leafRlp = RLP.encode(leafItems);

        root = keccak256(leafRlp);
        proof = new bytes[](1);
        proof[0] = leafRlp;
    }

    /// Allocate the next L2 block number for this test and publish a state
    /// root on `mockRollup` that proves `(account, nonce, balance, codeHash)`.
    /// Returns the block number and the proof; the caller uses both at
    /// `inbox.add(...)`.
    function _publishAccount(
        address account,
        uint64 nonce,
        uint256 balance,
        bytes32 codeHash
    ) internal returns (uint256 l2Block, bytes[] memory proof) {
        l2BlockCounter += 1;
        l2Block = l2BlockCounter;
        bytes32 root;
        (root, proof) = _buildAccountProof(account, nonce, balance, codeHash);
        mockRollup.setStateRoot(l2Block, root);
    }

    /// Sign and submit `_sampleTx()` for `pk` on the default inbox, with the
    /// signer's account published as executable (nonce=5 matches
    /// `_sampleTx().nonce`, 10 ether covers `gas*maxFee + value`, codeHash
    /// is the empty-EOA sentinel).
    function _submitSample(uint256 pk) internal returns (address signer) {
        signer = vm.addr(pk);
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        inbox.add(t, proof, l2Block);
    }

    /// Sign `_sampleTx()` with a custom `maxFeePerGas`. Does not submit, so
    /// callers can `vm.expectRevert` on the submit step alone.
    function _signSampleWithFee(ForcedInboxValidated _inbox, uint256 pk, uint256 maxFee)
        internal
        pure
        returns (ForcedInboxValidated.Tx1559 memory t)
    {
        t = _sampleTx();
        t.maxFeePerGas = maxFee;
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, _inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
    }

    /// Sign and submit `_sampleTx()` with a custom `maxFeePerGas`. Publishes
    /// the signer's account as executable against the given inbox's rollup,
    /// then submits. Used by the queue/cap tests that fill an inbox with
    /// entries at chosen fees.
    function _submitWithFeeOn(ForcedInboxValidated _inbox, uint256 pk, uint256 maxFee)
        internal
        returns (address signer)
    {
        signer = vm.addr(pk);
        MockRollup rollupOf = MockRollup(_inbox.ROLLUP());
        l2BlockCounter += 1;
        uint256 l2Block = l2BlockCounter;
        (bytes32 root, bytes[] memory proof) =
            _buildAccountProof(signer, 5, 10 ether, EMPTY_CODE_HASH);
        rollupOf.setStateRoot(l2Block, root);

        ForcedInboxValidated.Tx1559 memory t = _signSampleWithFee(_inbox, pk, maxFee);
        _inbox.add(t, proof, l2Block);
    }
}
