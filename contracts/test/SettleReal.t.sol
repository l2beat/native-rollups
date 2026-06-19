// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ForcedInboxValidated.sol";
import "./mocks/MockRollup.sol";
import {RealTxFixture} from "./RealTxFixture.sol";

/// @notice Wraps the inbox to expose `forceInsert` for tests that need to
///         seed the queue with arbitrary `(sender, l2TxHash, gasLimit, fee)`
///         tuples that match real-block transactions — bypassing the usual
///         `add` path (which would require signing as the real tx's sender).
contract ExposedInbox is ForcedInboxValidated {
    constructor(uint256 q, address r) ForcedInboxValidated(q, r) {}

    function forceInsert(
        address sender,
        bytes32 l2TxHash,
        uint64 gasLimit,
        uint256 maxFee
    ) external {
        Entry storage e = entry[sender];
        e.l2TxHash = l2TxHash;
        e.gasLimit = gasLimit;
        e.maxFeePerGas = maxFee;
        e.l2BlockNumber = uint64(block.number);
        _insertSorted(sender);
    }
}

/// @notice Exercises `settle` against a real Ethereum mainnet block's
///         `transactionsRoot`. Covers both the positive branch
///         (entry's `l2TxHash` matches a real tx in the block and a valid
///         inclusion proof verifies) and the FOCIL-pruning negative branch
///         (entry not in the block, real `transactionsRoot` plus block
///         gas headroom drives invalidity).
contract SettleRealTest is Test {
    MockRollup mockRollup;
    ExposedInbox inbox;

    function setUp() public {
        mockRollup = new MockRollup();
        inbox = new ExposedInbox(256, address(mockRollup));
    }

    function _isQueued(address sender) internal view returns (bool) {
        (bytes32 h,,,,,) = inbox.entry(sender);
        return h != bytes32(0);
    }

    function _empty()
        internal
        pure
        returns (ForcedInboxValidated.InclusionProof[] memory)
    {
        return new ForcedInboxValidated.InclusionProof[](0);
    }

    // --- positive: entry's tx is in a real block ---

    /// Inject an entry whose `l2TxHash` equals the keccak of a real tx at
    /// index 0 in a real mainnet block. Provide the matching MPT proof
    /// against `transactionsRoot`. Settle should recognize it as executed
    /// and remove it.
    function test_realInclusion_pick0() public {
        address sender = vm.addr(0xA11CE);
        inbox.forceInsert(
            sender,
            RealTxFixture.HASH_0,
            RealTxFixture.GAS_0,
            100e9 // generous fee — above any reasonable baseFee
        );
        assertTrue(_isQueued(sender), "preconditioin: queued");

        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](1);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: sender,
            txIndex: RealTxFixture.IDX_0,
            proof: RealTxFixture.proof_0()
        });

        vm.prank(address(mockRollup));
        inbox.settle(
            10_000_000, // gasBudget
            1e9, // baseFee (1 gwei, well below the entry's 100 gwei)
            RealTxFixture.TX_ROOT,
            RealTxFixture.BLOCK_GAS_USED,
            RealTxFixture.BLOCK_GAS_LIMIT,
            included
        );

        assertFalse(_isQueued(sender), "should be cleared (executed)");
    }

    // --- negative: entry not in block, FOCIL prunes via headroom ---

    /// Inject an entry with a hash that's NOT in the real block. Don't
    /// provide an inclusion proof. The block has ~13 M gas of headroom
    /// (60 M limit vs 47 M used) and the entry only needs 21 k — so the
    /// FOCIL invariant guarantees the entry is invalid, and settle prunes.
    function test_realExclusion_focilPrunes() public {
        address sender = vm.addr(0xB0B);
        bytes32 fakeHash = keccak256("not-in-block");
        uint64 entryGas = 21_000;
        inbox.forceInsert(sender, fakeHash, entryGas, 100e9);
        assertTrue(_isQueued(sender), "precondition: queued");

        vm.prank(address(mockRollup));
        inbox.settle(
            10_000_000,
            1e9,
            RealTxFixture.TX_ROOT, // real txRoot but our entry isn't in it
            RealTxFixture.BLOCK_GAS_USED,
            RealTxFixture.BLOCK_GAS_LIMIT,
            _empty()
        );

        assertFalse(_isQueued(sender), "FOCIL should have pruned");
    }

    /// Same as above but the block has no headroom for our entry. Settle
    /// keeps it (FOCIL satisfaction is consistent with "didn't fit").
    function test_realExclusion_keepsWhenNoHeadroom() public {
        address sender = vm.addr(0xB0B);
        bytes32 fakeHash = keccak256("not-in-block");
        uint64 entryGas = 21_000;
        inbox.forceInsert(sender, fakeHash, entryGas, 100e9);

        // Pretend the block was packed; headroom = 0 < 21 k.
        vm.prank(address(mockRollup));
        inbox.settle(
            10_000_000,
            1e9,
            RealTxFixture.TX_ROOT,
            RealTxFixture.BLOCK_GAS_LIMIT, // gasUsed == gasLimit
            RealTxFixture.BLOCK_GAS_LIMIT,
            _empty()
        );

        assertTrue(_isQueued(sender), "kept (didn't fit)");
    }

    // --- mixed: 3 IL entries, 2 in-block + 1 not ---

    /// IL of 3 entries: two match real picks (#0 and #1), one is fake.
    /// Settle executes the two real ones and FOCIL-prunes the fake. All
    /// three entries get cleared.
    ///
    /// The queue ordering is fee-descending; we insert all three with the
    /// same fee so they take FIFO order. The `included` array must mirror
    /// queue order: real picks first, then any fakes.
    function test_realMixed_executesAndPrunes() public {
        address a = vm.addr(0xA11CE);
        address b = vm.addr(0xB0B);
        address c = vm.addr(0xC0DE);

        uint256 fee = 100e9;
        inbox.forceInsert(a, RealTxFixture.HASH_0, RealTxFixture.GAS_0, fee);
        inbox.forceInsert(b, RealTxFixture.HASH_1, RealTxFixture.GAS_1, fee);
        inbox.forceInsert(c, keccak256("fake"), uint64(21_000), fee);

        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](2);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: a,
            txIndex: RealTxFixture.IDX_0,
            proof: RealTxFixture.proof_0()
        });
        included[1] = ForcedInboxValidated.InclusionProof({
            sender: b,
            txIndex: RealTxFixture.IDX_1,
            proof: RealTxFixture.proof_1()
        });

        vm.prank(address(mockRollup));
        inbox.settle(
            10_000_000,
            1e9,
            RealTxFixture.TX_ROOT,
            RealTxFixture.BLOCK_GAS_USED,
            RealTxFixture.BLOCK_GAS_LIMIT,
            included
        );

        assertFalse(_isQueued(a), "a executed");
        assertFalse(_isQueued(b), "b executed");
        assertFalse(_isQueued(c), "c FOCIL-pruned");
        assertEq(inbox.queuedCount(), 0);
    }

    /// Wrong-hash protection against a real txRoot: claim pick #0's proof
    /// belongs to a sender whose stored hash is `HASH_1` (different tx).
    /// `MerkleTrie.get` returns pick 0's raw bytes whose keccak doesn't
    /// equal `HASH_1`, so settle reverts.
    function test_realInclusion_wrongHashReverts() public {
        address sender = vm.addr(0xA11CE);
        // Inject hash for pick #1 but provide proof for pick #0.
        inbox.forceInsert(sender, RealTxFixture.HASH_1, RealTxFixture.GAS_0, 100e9);

        ForcedInboxValidated.InclusionProof[] memory included =
            new ForcedInboxValidated.InclusionProof[](1);
        included[0] = ForcedInboxValidated.InclusionProof({
            sender: sender,
            txIndex: RealTxFixture.IDX_0,
            proof: RealTxFixture.proof_0()
        });

        vm.prank(address(mockRollup));
        vm.expectRevert(ForcedInboxValidated.WrongTxHash.selector);
        inbox.settle(
            10_000_000,
            1e9,
            RealTxFixture.TX_ROOT,
            RealTxFixture.BLOCK_GAS_USED,
            RealTxFixture.BLOCK_GAS_LIMIT,
            included
        );
    }
}
