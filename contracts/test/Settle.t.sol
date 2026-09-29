// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./InboxTestBase.sol";

/// @notice Exercises `settle`: operator-driven IL clearing called by the
///         rollup at settlement. Three outcomes per IL'd entry:
///         executed (proof matches tx in transactions_root), FOCIL-pruned
///         (not in block + headroom large enough that FOCIL implies
///         invalidity), or kept (didn't fit; FOCIL says nothing).
contract SettleTest is InboxTestBase {
    // --- helpers ---

    /// Build a single-leaf tx trie containing exactly one transaction at
    /// index 0. Tx-trie is unsecured; key = `rlp(0) = 0x80`, nibbles
    /// `[8, 0]`. HP path for an even leaf is `0x20 || 0x80`.
    function _txTrie1At0(bytes memory rawTx)
        internal
        pure
        returns (bytes32 root, bytes[] memory proof)
    {
        bytes memory hpPath = hex"2080";
        bytes[] memory leafItems = new bytes[](2);
        leafItems[0] = RLP.encode(hpPath);
        leafItems[1] = RLP.encode(rawTx);
        bytes memory leafRlp = RLP.encode(leafItems);
        root = keccak256(leafRlp);
        proof = new bytes[](1);
        proof[0] = leafRlp;
    }

    /// Queue one sample tx for `pk` and capture the emitted `rawTx`.
    function _submitOne(uint256 pk)
        internal
        returns (address signer, bytes memory rawTx)
    {
        signer = vm.addr(pk);
        (uint256 l2Block, bytes[] memory accountProof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;

        vm.recordLogs();
        inbox.add(t, accountProof, l2Block);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        rawTx = abi.decode(logs[0].data, (bytes));
    }

    function _noProofs()
        internal
        pure
        returns (ForcedInboxValidated.InclusionProof[] memory)
    {
        return new ForcedInboxValidated.InclusionProof[](0);
    }

    // --- happy paths ---

    /// Tx included in the block (proof verifies against transactions_root):
    /// settle removes the entry and emits `ForcedTxExecuted`.
    function test_executedClearsEntry() public {
        uint256 pk = 0xA11CE;
        (address signer, bytes memory rawTx) = _submitOne(pk);

        (bytes32 txRoot, bytes[] memory txProof) = _txTrie1At0(rawTx);
        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](1);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: signer,
            txIndex: 0,
            proof: txProof
        });

        vm.prank(address(mockRollup));
        inbox.settle(1_000_000, 1e9, txRoot, 21_000, 30_000_000, included);

        assertFalse(_isQueued(inbox, signer));
        assertEq(inbox.queuedCount(), 0);
    }

    /// Tx not in block AND headroom >= entry.gasLimit. FOCIL satisfied (we
    /// assume the rollup verified this) implies the entry was invalid.
    /// Settle clears it.
    function test_focilPrunesWhenHeadroom() public {
        uint256 pk = 0xA11CE;
        (address signer,) = _submitOne(pk);

        // No inclusion proof for this entry; ample headroom (30M - 0 = 30M
        // vs. the entry's 21k gas).
        vm.prank(address(mockRollup));
        inbox.settle(1_000_000, 1e9, bytes32(0), 0, 30_000_000, _noProofs());

        assertFalse(_isQueued(inbox, signer));
        assertEq(inbox.queuedCount(), 0);
    }

    /// Tx not in block AND headroom < entry.gasLimit: FOCIL satisfaction is
    /// consistent with "didn't fit", so we can't conclude invalidity. Keep.
    function test_keepsWhenNoHeadroom() public {
        uint256 pk = 0xA11CE;
        (address signer,) = _submitOne(pk);

        // Headroom = 30M - 30M = 0 < 21k.
        vm.prank(address(mockRollup));
        inbox.settle(1_000_000, 1e9, bytes32(0), 30_000_000, 30_000_000, _noProofs());

        assertTrue(_isQueued(inbox, signer));
        assertEq(inbox.queuedCount(), 1);
    }

    /// Underpriced entry (maxFee < baseFee) is excluded from the IL by the
    /// fee filter; settle leaves it untouched even with plenty of headroom.
    function test_underpricedNotInIL() public {
        uint256 pk = 0xA11CE;
        address signer = vm.addr(pk);

        (uint256 l2Block, bytes[] memory accountProof) =
            _publishAccount(signer, 5, 10 ether, EMPTY_CODE_HASH);

        ForcedInboxValidated.Tx1559 memory t = _sampleTx();
        t.maxFeePerGas = 1e9; // 1 gwei
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, inbox.signingHash(t));
        t.yParity = v - 27;
        t.r = r;
        t.s = s;
        inbox.add(t, accountProof, l2Block);

        vm.prank(address(mockRollup));
        inbox.settle(1_000_000, 2e9, bytes32(0), 0, 30_000_000, _noProofs());

        assertTrue(_isQueued(inbox, signer));
    }

    // --- reverts ---

    /// Anyone other than the rollup is rejected up front.
    function test_revertsNotRollup() public {
        vm.expectRevert(ForcedInboxValidated.NotRollup.selector);
        inbox.settle(1_000_000, 1e9, bytes32(0), 0, 30_000_000, _noProofs());
    }

    /// Inclusion proof verifies against the trie but the value at that
    /// index has a different keccak256 than the entry's `l2TxHash`. The
    /// operator can't pass off a wrong-tx proof as an inclusion attestation.
    function test_revertsWrongTxHash() public {
        uint256 pk = 0xA11CE;
        (address signer,) = _submitOne(pk);

        // A trie whose index-0 value is *not* the queued forced tx.
        bytes memory wrongTx = hex"deadbeef";
        (bytes32 txRoot, bytes[] memory txProof) = _txTrie1At0(wrongTx);

        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](1);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: signer,
            txIndex: 0,
            proof: txProof
        });

        vm.prank(address(mockRollup));
        vm.expectRevert(ForcedInboxValidated.WrongTxHash.selector);
        inbox.settle(1_000_000, 1e9, txRoot, 21_000, 30_000_000, included);
    }

    /// Providing more inclusion proofs than IL'd entries reverts. Catches
    /// misordered / surplus / out-of-IL-position witnesses.
    function test_revertsExtraInclusionProofs() public {
        uint256 pk = 0xA11CE;
        (, bytes memory rawTx) = _submitOne(pk);

        (bytes32 txRoot, bytes[] memory txProof) = _txTrie1At0(rawTx);

        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](2);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: vm.addr(0xA11CE),
            txIndex: 0,
            proof: txProof
        });
        included[1] = ForcedInboxValidated.InclusionProof({
            sender: address(0xB0B),
            txIndex: 1,
            proof: txProof
        });

        vm.prank(address(mockRollup));
        vm.expectRevert(ForcedInboxValidated.ExtraInclusionProofs.selector);
        inbox.settle(1_000_000, 1e9, txRoot, 21_000, 30_000_000, included);
    }
}
