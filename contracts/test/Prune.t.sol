// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./InboxTestBase.sol";

/// @notice `prune` (proof-based) behaviour.
contract PruneTest is InboxTestBase {
    /// Proven nonce > entry.nonce -> prune.
    function test_pruneStaleNonceFreesSlot() public {
        address signer = _submitSample(0xA11CE);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 6, 10 ether, EMPTY_CODE_HASH);
        inbox.prune(signer, proof, l2Block);

        assertTrue(!_isQueued(inbox, signer), "isQueued should clear on prune");
        _submitSample(0xA11CE); // slot is free
    }

    /// Proven balance < cost -> prune.
    function test_pruneUnaffordableFreesSlot() public {
        address signer = _submitSample(0xA11CE);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 0, EMPTY_CODE_HASH);
        inbox.prune(signer, proof, l2Block);

        assertTrue(!_isQueued(inbox, signer));
    }

    /// Proven state still executable -> NotPrunable.
    function test_pruneRevertsWhenExecutable() public {
        address signer = _submitSample(0xA11CE);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        vm.expectRevert(ForcedInboxValidated.NotPrunable.selector);
        inbox.prune(signer, proof, l2Block);
    }

    /// Anti-censorship: an underpriced (but still nonce-matching and
    /// funded) entry CANNOT be pruned. Even when the prevailing base fee
    /// is far above the entry's `maxFeePerGas` -- so the entry would be
    /// excluded from `currentIL` as underpriced -- the entry stays
    /// queued. `prune` has no `baseFee` input and only triggers on
    /// nonce-advanced or balance-below-cost.
    function test_pruneDoesNotEvictUnderpricedEntries() public {
        uint256 lowFee = 5e9; // 5 gwei
        address signer = _submitWithFeeOn(inbox, 0xA11CE, lowFee);

        // Confirm the entry IS underpriced from `currentIL`'s perspective.
        // We pass a baseFee above the entry's maxFeePerGas; the walk
        // short-circuits and the IL is empty.
        (bytes32 ilHash, uint256 ilCount) = inbox.currentIL(10_000_000, lowFee + 1);
        assertEq(ilCount, 0, "entry must be underpriced at this baseFee");
        assertEq(ilHash, bytes32(0));

        // The entry's L2 state is unchanged from admission (still
        // affordable and nonce-matching). Prune must revert NotPrunable
        // even though the entry is currently locked out of the IL.
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);
        vm.expectRevert(ForcedInboxValidated.NotPrunable.selector);
        inbox.prune(signer, proof, l2Block);

        assertTrue(_isQueued(inbox, signer), "underpriced entry stays queued");
    }

    /// `prune` reverts NotPrunable for a sender that was never queued,
    /// independently of what the proof says.
    function test_pruneRevertsWhenNotQueued() public {
        address signer = vm.addr(0xA11CE);
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 6, 0, EMPTY_CODE_HASH); // would-be prunable
        vm.expectRevert(ForcedInboxValidated.NotPrunable.selector);
        inbox.prune(signer, proof, l2Block);
    }

    /// Admission block is recorded on the entry; replacement updates it.
    function test_admissionBlockRecorded() public {
        address signer = _submitSample(0xA11CE);
        assertEq(uint256(_entryAdmissionBlock(inbox, signer)), 1);
    }

    /// Adversarial: an attacker tries to prune a healthy entry using a
    /// proof from a historical block where the sender's balance was below
    /// the entry's cost. Without the "prune block must be strictly after
    /// admission" check, this would falsely trigger the unaffordable path
    /// (1 wei < cost) and evict an entry whose current state is still
    /// fully admissible.
    function test_pruneCannotUseProofFromBeforeAdmission() public {
        address signer = vm.addr(0xA11CE);

        // Adversary publishes "historical poor state" at block 1: balance=1.
        (uint256 historicalBlock, bytes[] memory historicalProof) =
            _publishAccount(signer, 5, 1, EMPTY_CODE_HASH);

        // Sender admits at block 2 with a healthy balance that covers cost.
        _submitSample(0xA11CE);
        assertEq(uint256(_entryAdmissionBlock(inbox, signer)), 2);

        // Adversary tries to prune using the historical proof.
        vm.expectRevert(ForcedInboxValidated.ProofNotAfterAdmission.selector);
        inbox.prune(signer, historicalProof, historicalBlock);

        // Entry is still queued.
        assertTrue(_isQueued(inbox, signer));
    }

    /// The sender can defend against a *previously-dipped* balance by
    /// submitting a replacement (with a fresh proof) before anyone prunes.
    /// The replacement bumps `entry.l2BlockNumber` past the dip, so any
    /// proof from the dipped block is now ProofNotAfterAdmission and the
    /// entry survives. Demonstrates the user-driven recovery path that
    /// the strict-greater check on prune enables.
    function test_replacementShieldsAgainstUnprunedBalanceDip() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // Admit at block 1 with a healthy state. Sample tx cost is ~1.00042 eth.
        _submitSample(pk);
        assertEq(uint256(_entryAdmissionBlock(inbox, signer)), 1);

        // Adversary captures a "dip" proof at block 2 (balance below cost).
        // No one prunes yet.
        (uint256 dipBlock, bytes[] memory dipProof) =
            _publishAccount(signer, 5, 0.5 ether, EMPTY_CODE_HASH);

        // Sender refreshes via replacement: publish healthy state at block 3,
        // sign a fee-bumped tx, submit.
        (uint256 refreshBlock, bytes[] memory refreshProof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);
        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.maxFeePerGas = t.maxFeePerGas + 1;
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        inbox.add(t, refreshProof, refreshBlock);
        assertEq(uint256(_entryAdmissionBlock(inbox, signer)), refreshBlock);

        // Adversary tries to prune with the dipped block-2 proof.
        // dipBlock (2) <= refreshBlock (3) -> ProofNotAfterAdmission.
        vm.expectRevert(ForcedInboxValidated.ProofNotAfterAdmission.selector);
        inbox.prune(signer, dipProof, dipBlock);
        assertTrue(_isQueued(inbox, signer), "entry survived the dip via replace");
    }

    /// Boundary: prune at exactly the admission block also reverts,
    /// because the strict-greater check rejects equality. The proven
    /// state at that block equals what admission already validated.
    function test_pruneRevertsAtAdmissionBlock() public {
        address signer = _submitSample(0xA11CE); // admitted at block 1

        // Overwrite block 1's root with a hostile state (balance=0).
        (bytes32 hostileRoot, bytes[] memory hostileProof) =
            _buildAccountProof(signer, 5, 0, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(1, hostileRoot);

        vm.expectRevert(ForcedInboxValidated.ProofNotAfterAdmission.selector);
        inbox.prune(signer, hostileProof, 1);

        assertTrue(_isQueued(inbox, signer));
    }

    /// Adversary submits a prune proof that anchors to a published root
    /// but covers a different account's leaf. The MerkleTrie walk for the
    /// real sender's key fails the path-remainder check.
    function test_pruneRevertsWithMismatchedProof() public {
        address signer = _submitSample(0xA11CE); // admitted at block 1
        address other = vm.addr(0xB0B);

        // Proof at block 2 for `other`, not signer.
        (bytes32 root, bytes[] memory proof) =
            _buildAccountProof(other, 6, 0, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(2, root);

        vm.expectRevert(); // exact error from the vendored MerkleTrie
        inbox.prune(signer, proof, 2);
        assertTrue(_isQueued(inbox, signer));
    }

    /// Prune proofs are subject to the same recency window as admission:
    /// a proof from a block beyond `MAX_PROOF_AGE` behind the rollup head
    /// reverts `ProofTooStale`.
    function test_pruneRevertsWithStaleProof() public {
        address signer = _submitSample(0xA11CE); // admitted at block 1

        // Advance head to block 500 with an unrelated root.
        mockRollup.setStateRoot(500, keccak256("padding"));

        // Publish a hostile proof at block 100 (100 + 256 < 500 -> stale).
        (bytes32 root, bytes[] memory proof) =
            _buildAccountProof(signer, 6, 0, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(100, root);

        vm.expectRevert(ForcedInboxValidated.ProofTooStale.selector);
        inbox.prune(signer, proof, 100);
        assertTrue(_isQueued(inbox, signer));
    }
}
