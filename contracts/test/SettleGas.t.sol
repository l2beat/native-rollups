// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ForcedInboxValidated.sol";
import "./mocks/MockRollup.sol";
import {RealTxFixture} from "./RealTxFixture.sol";

contract _Exposed is ForcedInboxValidated {
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

/// @notice Measures `settle` execution gas (via `gasleft()` deltas) under
///         realistic IL shapes against a real-Ethereum mainnet block:
///         IL sizes 0, 1, 2, 4, 8, 16, 32 (= `MAX_IL_COUNT`) with all
///         entries included and verified via real `transactionsRoot`
///         multiproof-against-32-leaves.
contract SettleGasTest is Test {
    _Exposed inbox;

    function setUp() public {
        inbox = new _Exposed(1024, address(this));
    }

    function _emptyProofs()
        internal
        pure
        returns (ForcedInboxValidated.InclusionProof[] memory)
    {
        return new ForcedInboxValidated.InclusionProof[](0);
    }

    /// Seed `n` queue entries matching the first `n` fixture picks, all at
    /// the same fee so they queue in FIFO order matching the pick indices.
    function _seedReal(uint256 n) internal returns (address[] memory senders) {
        senders = new address[](n);
        for (uint256 i = 0; i < n; i++) {
            senders[i] = address(uint160(0xA110CA + i));
            inbox.forceInsert(
                senders[i],
                RealTxFixture.hashAt(i),
                RealTxFixture.gasAt(i),
                100e9
            );
        }
    }

    function _includedFor(address[] memory senders)
        internal
        pure
        returns (ForcedInboxValidated.InclusionProof[] memory inc)
    {
        inc = new ForcedInboxValidated.InclusionProof[](senders.length);
        for (uint256 i = 0; i < senders.length; i++) {
            (uint256 idx, bytes[] memory proof) = RealTxFixture.proofAt(i);
            inc[i] = ForcedInboxValidated.InclusionProof({
                sender: senders[i],
                txIndex: idx,
                proof: proof
            });
        }
    }

    function _measure(uint256 n) internal returns (uint256 settleGas) {
        address[] memory senders = _seedReal(n);
        ForcedInboxValidated.InclusionProof[] memory inc = _includedFor(senders);

        uint256 g0 = gasleft();
        inbox.settle(
            10_000_000,
            1e9,
            RealTxFixture.TX_ROOT,
            RealTxFixture.BLOCK_GAS_USED,
            RealTxFixture.BLOCK_GAS_LIMIT,
            inc
        );
        settleGas = g0 - gasleft();
    }

    /// `settle` cost sweep: 0, 1, 2, 4, 8, 16, 32 IL entries, all included,
    /// proofs verified against a real mainnet block's `transactionsRoot`.
    function test_gasSweep_realInclusions() public {
        uint256[] memory sizes = new uint256[](7);
        sizes[0] = 0;
        sizes[1] = 1;
        sizes[2] = 2;
        sizes[3] = 4;
        sizes[4] = 8;
        sizes[5] = 16;
        sizes[6] = 32;

        emit log("=== settle gas vs IL size (real mainnet block) ===");
        for (uint256 i = 0; i < sizes.length; i++) {
            // Fresh inbox per measurement to avoid cross-run state.
            inbox = new _Exposed(1024, address(this));
            uint256 g = _measure(sizes[i]);
            emit log_named_uint(_label("n=", sizes[i]), g);
        }
    }

    function _label(string memory prefix, uint256 n) internal pure returns (string memory) {
        return string(abi.encodePacked("settle gas (", prefix, _uintToStr(n), ")"));
    }

    function _uintToStr(uint256 v) internal pure returns (string memory) {
        if (v == 0) return "0";
        uint256 len;
        for (uint256 t = v; t > 0; t /= 10) len++;
        bytes memory b = new bytes(len);
        for (uint256 i = len; i > 0; i--) {
            b[i - 1] = bytes1(uint8(48 + v % 10));
            v /= 10;
        }
        return string(b);
    }
}
