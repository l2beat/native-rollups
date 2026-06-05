// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "./InboxTestBase.sol";

/// @notice Exercises `add`: the four-layer admission gate (basics + signature
///         + account-state proof + stateful check) and the happy-path
///         submission flow.
contract AddTest is InboxTestBase {
    // --- happy path ---

    /// Sign the contract's signing hash, submit with a valid proof, recover
    /// the signer, and confirm the stored entry matches `keccak256(rawTx)`.
    function test_recoverAndEncode() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;

        vm.recordLogs();
        address sender = inbox.add(t, proof, l2Block);
        assertEq(sender, signer, "recovered sender mismatch");

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes memory rawTx = abi.decode(logs[0].data, (bytes));

        (bytes32 storedHash, uint64 storedNonce,,,,) = inbox.entry(signer);
        assertEq(storedHash, keccak256(rawTx), "stored hash != keccak256(rawTx)");
        assertEq(uint256(storedNonce), t.nonce, "stored nonce mismatch");
        assertTrue(_isQueued(inbox, signer));
        assertEq(inbox.queuedCount(), 1);
        assertEq(inbox.firstQueued(), signer);
        assertEq(inbox.lastQueued(), signer);

        emit log_named_bytes("rawTx", rawTx);
        emit log_named_bytes32("l2TxHash", keccak256(rawTx));
    }

    function test_signingHashRecovers() public view {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        bytes32 h = inbox.signingHash(t);
        assertTrue(h != bytes32(0));
    }

    /// Non-empty calldata with ample gas passes the intrinsic-gas path.
    function test_acceptsWithCalldata() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.data = hex"0011002200";
        t.gasLimit = 100_000;
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        assertEq(inbox.add(t, proof, l2Block), signer);
    }

    // --- signature rejection (geth: Signer.Sender folded into validateTxBasics) ---

    function test_rejectsBadYParity() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.yParity = 2; // v=29 -> ecrecover returns 0
        vm.expectRevert(ECDSA.ECDSAInvalidSignature.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    function test_rejectsHighS() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.s = bytes32(type(uint256).max);
        vm.expectRevert(
            abi.encodeWithSelector(ECDSA.ECDSAInvalidSignatureS.selector, t.s)
        );
        inbox.add(t, new bytes[](0), 0);
    }

    // --- stateless rejection (geth: validateTxBasics) ---

    function test_rejectsGasLimitTooHigh() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.gasLimit = 16_777_216 + 1; // > TX_MAX_GAS_LIMIT (EIP-7825)
        vm.expectRevert(ForcedInboxValidated.GasLimitTooHigh.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    function test_rejectsNonceMax() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.nonce = type(uint64).max;
        vm.expectRevert(ForcedInboxValidated.NonceMax.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    function test_rejectsIntrinsicGas() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.gasLimit = 20_999; // below the 21000 base
        vm.expectRevert(ForcedInboxValidated.IntrinsicGas.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    function test_rejectsMaxInitCodeSizeExceeded() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.isCreation = true;
        t.gasLimit = 16_777_216;
        t.data = new bytes(49152 + 1);
        vm.expectRevert(ForcedInboxValidated.MaxInitCodeSizeExceeded.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    function test_rejectsTipAboveFeeCap() public {
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.maxPriorityFeePerGas = t.maxFeePerGas + 1;
        vm.expectRevert(ForcedInboxValidated.TipAboveFeeCap.selector);
        inbox.add(t, new bytes[](0), 0);
    }

    // --- proof-anchor rejection ---

    /// Reverts `ProofTooStale` if the proof anchors to a block more than
    /// `MAX_PROOF_AGE` behind the rollup's head. Mirrors the L1 BLOCKHASH
    /// window: stale proofs are admissible up to a bounded distance, not
    /// indefinitely.
    function test_rejectsProofTooStale() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // Publish at block 1 (the signer's account).
        (uint256 oldBlock, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);
        // Advance the rollup head beyond MAX_PROOF_AGE without publishing
        // a root that covers signer. The mock auto-updates `blockNumber`
        // on the highest setStateRoot.
        mockRollup.setStateRoot(oldBlock + 257, keccak256("unrelated"));

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert(ForcedInboxValidated.ProofTooStale.selector);
        inbox.add(t, proof, oldBlock);
    }

    /// Reverts `UnknownBlock` if the rollup has no state root for the given
    /// L2 block (no `setStateRoot` was published for that block).
    function test_rejectsUnknownBlock() public {
        uint256 pk = 0xA11CE;
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        bytes[] memory empty = new bytes[](0);
        vm.expectRevert(ForcedInboxValidated.UnknownBlock.selector);
        inbox.add(t, empty, 42); // block 42 was never published
    }

    /// Submitting with a proof that anchors to a published root but covers
    /// a different account reverts. `SecureMerkleTrie.get` follows
    /// `keccak256(signer)`'s nibbles into a leaf whose path is for some
    /// other key, so it fails MerkleTrie's path-remainder check and bubbles
    /// that error up. The exact revert reason comes from the vendored
    /// library, so we just assert "any revert".
    function test_rejectsMismatchedAccountInProof() public {
        uint256 pk = 0xA11CE;
        address other = address(0xB0B);

        // Publish a root that contains `other`, not the signer.
        l2BlockCounter += 1;
        uint256 l2Block = l2BlockCounter;
        (bytes32 root, bytes[] memory proof) =
            _buildAccountProof(other, 5, 10 ether, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(l2Block, root);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert();
        inbox.add(t, proof, l2Block);
    }

    /// Reverts `NotPureEOA` if the proven `codeHash` differs from the
    /// empty-EOA sentinel. This is the EIP-7702 gate: accounts with a
    /// delegation designator (or any code) can't admit forced txs.
    function test_rejectsNotPureEOA() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);
        bytes32 delegatedCodeHash = keccak256(abi.encodePacked(hex"ef0100", address(0xdead)));

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, delegatedCodeHash);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert(ForcedInboxValidated.NotPureEOA.selector);
        inbox.add(t, proof, l2Block);
    }

    // --- stateful rejection (geth: ValidateTransactionWithState) ---

    /// `NonceTooLow` if the proven nonce is greater than the tx's nonce
    /// (the sender's L2 nonce has advanced past this tx, it's stale).
    function test_rejectsNonceTooLow() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // Proven nonce 6, sample tx claims nonce 5 -> stale.
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 6, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert(ForcedInboxValidated.NonceTooLow.selector);
        inbox.add(t, proof, l2Block);
    }

    /// `NonceTooHigh` if the proven nonce is less than the tx's nonce (the
    /// tx is in the future; we deliberately disallow future-nonce pipelining).
    function test_rejectsNonceTooHigh() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 4, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert(ForcedInboxValidated.NonceTooHigh.selector);
        inbox.add(t, proof, l2Block);
    }

    /// `InsufficientFunds` if proven balance can't cover `gas*maxFee + value`.
    function test_rejectsInsufficientFunds() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // 1 wei vs ~1.00042 ether cost.
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 1, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        vm.expectRevert(ForcedInboxValidated.InsufficientFunds.selector);
        inbox.add(t, proof, l2Block);
    }
}
