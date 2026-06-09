// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./InboxTestBase.sol";

/// @notice Exercises queue dynamics: replacement, fee-ordered insertion,
///         cap-induced eviction, and the `currentIL` walk.
contract QueueTest is InboxTestBase {
    // --- replacement ---

    /// A second submission from the same sender with the same `maxFeePerGas`
    /// reverts `ReplaceUnderpriced`. Both submissions share the proof, so
    /// the rejection is on fee, not on executability.
    function test_rejectsReplaceUnderpriced() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        // First submission.
        ForcedInboxValidated.Tx1559 memory t1 = _sampleTx();
        (uint8 v1, bytes32 r1, bytes32 s1) = vm.sign(pk, inbox.signingHash(t1));
        t1.yParity = v1 - 27;
        t1.r = r1;
        t1.s = s1;
        inbox.add(t1, proof, l2Block);

        // Second: same nonce, same maxFee, different value -> non-bump.
        ForcedInboxValidated.Tx1559 memory t2 = _sampleTx();
        t2.value = 2 ether;
        (uint8 v2, bytes32 r2, bytes32 s2) = vm.sign(pk, inbox.signingHash(t2));
        t2.yParity = v2 - 27;
        t2.r = r2;
        t2.s = s2;
        vm.expectRevert(ForcedInboxValidated.ReplaceUnderpriced.selector);
        inbox.add(t2, proof, l2Block);
    }

    /// Replacement: a strict bump on `maxFeePerGas` updates the entry in
    /// place and re-sorts it by the new fee. With both senders starting
    /// tied, the bump strictly outranks `other`, so the bumped entry
    /// stays at the head.
    function test_replacement() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2BlockA, bytes[] memory proofA) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t1 = _sampleTx();
        (uint8 v1, bytes32 r1, bytes32 s1) = vm.sign(pk, inbox.signingHash(t1));
        t1.yParity = v1 - 27;
        t1.r = r1;
        t1.s = s1;
        inbox.add(t1, proofA, l2BlockA);

        address other = _submitSample(0xB0B);
        assertEq(inbox.firstQueued(), signer);
        assertEq(inbox.lastQueued(), other);

        // Replace with maxFeePerGas + 1. Same nonce, distinct value to
        // verify field overwrites.
        ForcedInboxValidated.Tx1559 memory t2 = _sampleTx();
        t2.value = 2 ether;
        t2.maxFeePerGas = t1.maxFeePerGas + 1;
        (uint8 v2, bytes32 r2, bytes32 s2) = vm.sign(pk, inbox.signingHash(t2));
        t2.yParity = v2 - 27;
        t2.r = r2;
        t2.s = s2;
        inbox.add(t2, proofA, l2BlockA);

        assertEq(inbox.queuedCount(), 2);
        assertEq(inbox.firstQueued(), signer, "bumped fee keeps signer at head");
        assertEq(inbox.lastQueued(), other);

        (, uint64 storedNonce,,, uint256 storedMaxFee, uint256 storedValue) = inbox.entry(signer);
        assertEq(uint256(storedNonce), t2.nonce);
        assertEq(storedMaxFee, t2.maxFeePerGas);
        assertEq(storedValue, t2.value);
    }

    /// Adversary holds a sender's signed replacement tx and submits it
    /// with a proof from an *older* block than the current
    /// `entry.l2BlockNumber`, attempting to regress the admission block.
    /// Without `ProofRegression` this would reopen the historical-state
    /// window that `prune` would otherwise refuse to walk.
    function test_replacementRevertsOnProofRegression() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        // Admit at block 5 directly (skip ahead to leave older blocks
        // available as "regression" targets).
        (bytes32 root5, bytes[] memory proof5) =
            _buildAccountProof(signer, 5, 10 ether, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(5, root5);
        ForcedInboxValidated.Tx1559 memory t1 = _sampleTx();
        (uint8 v1, bytes32 r1, bytes32 s1) = vm.sign(pk, inbox.signingHash(t1));
        t1.yParity = v1 - 27;
        t1.r = r1;
        t1.s = s1;
        inbox.add(t1, proof5, 5);

        // Adversary builds a proof at the older block 3 with valid state.
        (bytes32 root3, bytes[] memory proof3) =
            _buildAccountProof(signer, 5, 10 ether, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(3, root3);

        // Sign a replacement (fee bump).
        ForcedInboxValidated.Tx1559 memory t2 = _sampleTx();
        t2.maxFeePerGas = t1.maxFeePerGas + 1;
        (uint8 v2, bytes32 r2, bytes32 s2) = vm.sign(pk, inbox.signingHash(t2));
        t2.yParity = v2 - 27;
        t2.r = r2;
        t2.s = s2;

        vm.expectRevert(ForcedInboxValidated.ProofRegression.selector);
        inbox.add(t2, proof3, 3);
    }

    /// Replacement re-sorts: an entry at the tail bumps its fee above
    /// another entry's and moves to the head.
    function test_replacement_resortsByFee() public {
        // A at 30 gwei, B at 50 gwei.
        address a = _submitWithFeeOn(inbox, 0xA11CE, 30e9);
        address b = _submitWithFeeOn(inbox, 0xB0B, 50e9);
        assertEq(inbox.firstQueued(), b);
        assertEq(inbox.lastQueued(), a);

        // A bumps to 60 gwei. Has to publish a fresh proof to satisfy the
        // admission gate, even though the underlying state is unchanged.
        _submitWithFeeOn(inbox, 0xA11CE, 60e9);
        assertEq(inbox.firstQueued(), a, "bump moves A above B");
        assertEq(inbox.lastQueued(), b);
        assertEq(inbox.queuedCount(), 2, "no eviction on replace");
    }

    // --- cap and priority-based eviction ---

    /// When the queue is full and a submission's `maxFeePerGas <= tail.fee`,
    /// admission reverts `Underpriced`. The rejected sender must still be
    /// executable to isolate the cap branch from the executability one.
    function test_capRejectsUnderpricedWhenFull() public {
        ForcedInboxValidated capped = new ForcedInboxValidated(2, address(mockRollup));
        address a = _submitWithFeeOn(capped, 0xA11CE, 10e9);
        address b = _submitWithFeeOn(capped, 0xB0B, 20e9);
        assertEq(capped.firstQueued(), b);
        assertEq(capped.lastQueued(), a);
        assertEq(capped.queuedCount(), 2);

        // Publish CAFE's account so the failure is the cap, not the proof.
        address cafe = vm.addr(0xCAFE);
        l2BlockCounter += 1;
        uint256 l2Block = l2BlockCounter;
        (bytes32 root, bytes[] memory proof) =
            _buildAccountProof(cafe, 5, 10 ether, EMPTY_CODE_HASH);
        mockRollup.setStateRoot(l2Block, root);

        // Strictly below tail.
        ForcedInboxValidated.Tx1559 memory under = _signSampleWithFee(capped, 0xCAFE, 5e9);
        vm.expectRevert(ForcedInboxValidated.Underpriced.selector);
        capped.add(under, proof, l2Block);

        // Equal to tail (still Underpriced, must strictly outbid).
        ForcedInboxValidated.Tx1559 memory atFloor = _signSampleWithFee(capped, 0xCAFE, 10e9);
        vm.expectRevert(ForcedInboxValidated.Underpriced.selector);
        capped.add(atFloor, proof, l2Block);

        assertEq(capped.queuedCount(), 2);
        assertEq(capped.firstQueued(), b);
        assertEq(capped.lastQueued(), a);
    }

    /// When the queue is full and a submission strictly outbids the tail,
    /// the tail entry is evicted and the new entry is inserted by fee.
    function test_capEvictsLowestFeeOnOutbid() public {
        ForcedInboxValidated capped = new ForcedInboxValidated(2, address(mockRollup));
        address a = _submitWithFeeOn(capped, 0xA11CE, 10e9);
        address b = _submitWithFeeOn(capped, 0xB0B, 20e9);

        vm.recordLogs();
        address c = _submitWithFeeOn(capped, 0xCAFE, 15e9);

        // A evicted; queue is now [B @ 20 gwei, C @ 15 gwei].
        assertEq(capped.queuedCount(), 2);
        assertEq(capped.firstQueued(), b);
        assertEq(capped.lastQueued(), c);
        assertTrue(!_isQueued(capped, a), "A should be evicted");
        assertTrue(_isQueued(capped, c), "C should be queued");

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 prunedSig = keccak256("ForcedTxPruned(address,bytes32)");
        bool sawPrunedForA;
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].topics[0] == prunedSig && logs[i].topics[1] == bytes32(uint256(uint160(a)))) {
                sawPrunedForA = true;
                break;
            }
        }
        assertTrue(sawPrunedForA, "expected ForcedTxPruned(a, ...) on cap eviction");
    }

    /// Replacement is allowed when the queue is at cap: it doesn't grow
    /// the queue, so no eviction is needed.
    function test_capAllowsReplacementWhenFull() public {
        ForcedInboxValidated capped = new ForcedInboxValidated(2, address(mockRollup));
        address a = _submitWithFeeOn(capped, 0xA11CE, 10e9);
        address b = _submitWithFeeOn(capped, 0xB0B, 20e9);
        assertEq(capped.queuedCount(), 2);

        // A bumps to 25 gwei; queue stays at 2, A moves to head, no Pruned.
        vm.recordLogs();
        _submitWithFeeOn(capped, 0xA11CE, 25e9);

        assertEq(capped.queuedCount(), 2, "no eviction on replacement at cap");
        assertEq(capped.firstQueued(), a);
        assertEq(capped.lastQueued(), b);

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 prunedSig = keccak256("ForcedTxPruned(address,bytes32)");
        for (uint256 i; i < logs.length; ++i) {
            assertTrue(logs[i].topics[0] != prunedSig, "no Pruned on replace-at-cap");
        }
    }

    // --- currentIL ---

    /// `currentIL` walks the queue in fee-descending order, hashing in
    /// each entry's `l2TxHash` up to `gasBudget`. With both senders at
    /// the default fee, tie-break puts A first.
    function test_currentIL_order() public {
        address a = _submitSample(0xA11CE);
        address b = _submitSample(0xB0B);

        (bytes32 hash, uint256 n) = inbox.currentIL(10_000_000, 0);
        assertEq(n, 2);
        (bytes32 ha,,,,,) = inbox.entry(a);
        (bytes32 hb,,,,,) = inbox.entry(b);
        bytes32 expected = keccak256(abi.encodePacked(keccak256(abi.encodePacked(bytes32(0), ha)), hb));
        assertEq(hash, expected, "IL order should be A then B");
    }

    /// `gasBudget` caps the walk: with the budget set to exactly one
    /// 21000-gas entry, only A fits and B is skipped.
    function test_currentIL_stopsAtGasBudget() public {
        address a = _submitSample(0xA11CE);
        _submitSample(0xB0B);

        (bytes32 hash, uint256 n) = inbox.currentIL(21_000, 0);
        assertEq(n, 1);
        (bytes32 ha,,,,,) = inbox.entry(a);
        assertEq(hash, keccak256(abi.encodePacked(bytes32(0), ha)));
    }

    /// `baseFee` above every queued entry's `maxFeePerGas` short-circuits
    /// the walk at the head.
    function test_currentIL_shortCircuitsOnUnderpriced() public {
        _submitSample(0xA11CE);
        _submitSample(0xB0B);

        (bytes32 hash, uint256 n) = inbox.currentIL(10_000_000, 2e10 + 1);
        assertEq(n, 0);
        assertEq(hash, bytes32(0));
    }

    /// `currentIL` doesn't filter "currently non-executable" entries: the
    /// walk is structural over what's queued. The mechanism that removes
    /// such entries from the IL is `prune`, exercised here by pruning A
    /// (stale nonce) and observing that the next IL contains only B.
    function test_currentIL_pruneCleansIL() public {
        address a = _submitWithFeeOn(inbox, 0xA11CE, 50e9);
        address b = _submitWithFeeOn(inbox, 0xB0B, 20e9);
        assertEq(inbox.firstQueued(), a, "A has higher fee, head");

        // Before prune: both entries hashed into the IL.
        (, uint256 nBefore) = inbox.currentIL(10_000_000, 0);
        assertEq(nBefore, 2);

        // Prune A using a proof of stale nonce at a later block.
        (uint256 l2Block, bytes[] memory proof) =
            _publishAccount(a, 6, 10 ether, EMPTY_CODE_HASH);
        inbox.prune(a, proof, l2Block);

        // After prune: only B remains in the IL.
        (bytes32 hash, uint256 nAfter) = inbox.currentIL(10_000_000, 0);
        assertEq(nAfter, 1);
        (bytes32 hb,,,,,) = inbox.entry(b);
        assertEq(hash, keccak256(abi.encodePacked(bytes32(0), hb)));
    }
}
