// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {stdJson} from "forge-std/StdJson.sol";

import {NativeRollup} from "../src/NativeRollup.sol";
import {SequencedNativeRollup} from "../src/SequencedNativeRollup.sol";
import {NativeRollupFixture, TestNativeRollup} from "./NativeRollup.t.sol";

contract TestSequencedRollup is SequencedNativeRollup, TestNativeRollup {
    constructor(
        uint64 chainId_,
        uint64 l2GasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        address evmVkRegistry_,
        address sequencer_
    )
        payable
        TestNativeRollup(
            chainId_,
            l2GasLimit_,
            genesisBlockHash,
            genesisStateRoot,
            VkPolicy.FollowCurrent,
            bytes32(0),
            evmVkRegistry_,
            address(0)
        )
        SequencedNativeRollup(sequencer_)
    {}

    function advance(BlockParams calldata params, uint256 dependencyFrameIndex)
        public
        override(NativeRollup, SequencedNativeRollup)
    {
        super.advance(params, dependencyFrameIndex);
    }
}

/// @notice The preconfirmations customization: sequencer-only blocks, and
///         slashing for equivocation and divergence. Divergence proofs from
///         the L2 history contract use real proofs: `history_vectors.json`
///         holds an L2 block's hash and header, with a proof of the history
///         contract's slot against a later state root, as
///         `script/record_history_vectors.py` records them.
contract SequencedNativeRollupTest is NativeRollupFixture {
    using stdJson for string;

    address constant HISTORY = 0x0000F90827F1C53a10cb7A02335B175320002935;
    uint256 constant SEQUENCER_KEY = 0x5e9;
    uint256 constant BOND = 10 ether;
    uint64 constant ANCHOR = 7000;
    uint256 constant BLOCK_NUMBER_SLOT = 1;
    uint256 constant STATE_ROOTS_SLOT = 2;

    TestSequencedRollup seq;
    address sequencer;
    string history;

    event Slashed(uint64 indexed number, bytes32 preconfirmedHash, uint256 amount);

    function setUp() public override {
        super.setUp();
        history = vm.readFile("test/history_vectors.json");
        sequencer = vm.addr(SEQUENCER_KEY);
        // Deployed at the L1 time of the chain's first block, so that the
        // sequencer is not timed out when it posts it.
        vm.warp(json.readUint(string.concat(_block(0), ".header.timestamp")));
        seq = new TestSequencedRollup{value: BOND}(
            uint64(json.readUint(".chainId")),
            uint64(json.readUint(".chain.gasLimit")),
            json.readBytes32(".chain.genesisHash"),
            json.readBytes32(".chain.genesisStateRoot"),
            REGISTRY,
            sequencer
        );
    }

    function _post(uint256 i) internal {
        _setL1Context(i);
        seq.setDependency(LEANSTARK, _publicInputRoot(i), K1);
        vm.prank(sequencer);
        seq.advance(_params(i), 1);
    }

    function _sign(SequencedNativeRollup.Preconfirmation memory p, uint256 key) internal view returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, seq.preconfirmationDigest(p));
        return abi.encodePacked(r, s, v);
    }

    /// An L2 header whose anchor, `parent_beacon_block_root`, is `anchor`:
    /// twenty 32-byte items, the last of them the anchor.
    function _header(bytes32 anchor, bytes32 salt) internal pure returns (bytes memory items) {
        for (uint256 i = 0; i < 19; i++) {
            items = bytes.concat(items, hex"a0", keccak256(abi.encode(salt, i)));
        }
        items = bytes.concat(hex"f90294", items, hex"a0", anchor);
    }

    /// Makes the L1 history contract return `anchor` for block ANCHOR.
    function _l1History(bytes32 anchor) internal {
        vm.mockCall(HISTORY, abi.encode(uint256(ANCHOR)), abi.encode(anchor));
    }

    /// Gives the rollup the history vector's state root as that of its
    /// latest block.
    function _storeHistoryRoot() internal {
        uint256 head = history.readUint(".historyBlock");
        vm.store(address(seq), bytes32(BLOCK_NUMBER_SLOT), bytes32(head));
        vm.store(
            address(seq),
            keccak256(abi.encode(head % seq.STATE_ROOT_HISTORY(), STATE_ROOTS_SLOT)),
            history.readBytes32(".stateRoot")
        );
    }

    function _divergence(SequencedNativeRollup.Preconfirmation memory p, bytes memory header, bytes memory signature)
        internal
    {
        seq.slashDivergence(
            p,
            signature,
            header,
            history.readUint(".historyBlock"),
            history.readBytesArray(".accountProof"),
            history.readBytesArray(".storageProof")
        );
    }

    function test_onlyTheSequencerPosts() public {
        _setL1Context(0);
        seq.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        NativeRollup.BlockParams memory params = _params(0);
        vm.expectRevert(bytes("not the sequencer"));
        seq.advance(params, 1);

        vm.prank(sequencer);
        seq.advance(params, 1);
        assertEq(seq.blockNumber(), 1);
    }

    /// Anyone posts once the sequencer has posted nothing for the timeout.
    function test_anyonePostsAfterTheTimeout() public {
        _setL1Context(0);
        vm.warp(block.timestamp - seq.SEQUENCER_TIMEOUT() - 1);
        TestSequencedRollup late = new TestSequencedRollup(
            uint64(json.readUint(".chainId")),
            uint64(json.readUint(".chain.gasLimit")),
            json.readBytes32(".chain.genesisHash"),
            json.readBytes32(".chain.genesisStateRoot"),
            REGISTRY,
            sequencer
        );
        _setL1Context(0);
        late.setDependency(LEANSTARK, _publicInputRoot(0), K1);
        late.advance(_params(0), 1);
        assertEq(late.blockNumber(), 1);
    }

    function test_slashesEquivocation() public {
        SequencedNativeRollup.Preconfirmation memory a = SequencedNativeRollup.Preconfirmation(5, keccak256("a"), ANCHOR);
        SequencedNativeRollup.Preconfirmation memory b = SequencedNativeRollup.Preconfirmation(5, keccak256("b"), ANCHOR);
        bytes memory signatureA = _sign(a, SEQUENCER_KEY);
        bytes memory signatureB = _sign(b, SEQUENCER_KEY);
        bytes memory forged = _sign(b, 0xbad);
        uint256 burned = address(0).balance;

        vm.expectRevert(bytes("not an equivocation"));
        seq.slashEquivocation(a, signatureA, a, signatureA);
        vm.expectRevert(bytes("not signed by the sequencer"));
        seq.slashEquivocation(a, signatureA, b, forged);

        vm.expectEmit(address(seq));
        emit Slashed(5, a.blockHash, BOND);
        seq.slashEquivocation(a, signatureA, b, signatureB);
        assertEq(seq.bond(), 0);
        assertEq(address(0).balance, burned + BOND);
    }

    /// The latest block needs no proof of its hash.
    function test_slashesDivergenceAtTheHead() public {
        _post(0);
        bytes32 anchor = keccak256("anchor");
        bytes memory header = _header(anchor, "preconfirmed");
        SequencedNativeRollup.Preconfirmation memory p =
            SequencedNativeRollup.Preconfirmation(1, keccak256(header), ANCHOR);
        _l1History(anchor);

        seq.slashDivergence(p, _sign(p, SEQUENCER_KEY), header, 0, new bytes[](0), new bytes[](0));
        assertEq(seq.bond(), 0);
    }

    /// An earlier block's hash comes from the L2 history contract.
    function test_slashesDivergenceThroughHistory() public {
        _storeHistoryRoot();
        bytes32 anchor = keccak256("anchor");
        bytes memory header = _header(anchor, "preconfirmed");
        SequencedNativeRollup.Preconfirmation memory p =
            SequencedNativeRollup.Preconfirmation(uint64(history.readUint(".number")), keccak256(header), ANCHOR);
        _l1History(anchor);

        _divergence(p, header, _sign(p, SEQUENCER_KEY));
        assertEq(seq.bond(), 0);
    }

    function test_keepsTheBondForAPostedPreconfirmation() public {
        _storeHistoryRoot();
        bytes memory header = history.readBytes(".header");
        SequencedNativeRollup.Preconfirmation memory p = SequencedNativeRollup.Preconfirmation(
            uint64(history.readUint(".number")), history.readBytes32(".blockHash"), ANCHOR
        );
        // The real header's anchor, at RLP item 19.
        _l1History(this.anchorOf(header));
        bytes memory signature = _sign(p, SEQUENCER_KEY);

        vm.expectRevert(bytes("block posted as preconfirmed"));
        _divergence(p, header, signature);
        assertEq(seq.bond(), BOND);
    }

    /// A block whose anchor an L1 reorg removed could not be posted.
    function test_keepsTheBondWhenTheAnchorWasReorged() public {
        _post(0);
        bytes memory header = _header(keccak256("orphaned anchor"), "preconfirmed");
        SequencedNativeRollup.Preconfirmation memory p =
            SequencedNativeRollup.Preconfirmation(1, keccak256(header), ANCHOR);
        _l1History(keccak256("canonical anchor"));
        bytes memory signature = _sign(p, SEQUENCER_KEY);

        vm.expectRevert(bytes("anchor not canonical"));
        seq.slashDivergence(p, signature, header, 0, new bytes[](0), new bytes[](0));
    }

    function test_needsThePostedBlock() public {
        bytes32 anchor = keccak256("anchor");
        bytes memory header = _header(anchor, "preconfirmed");
        SequencedNativeRollup.Preconfirmation memory p =
            SequencedNativeRollup.Preconfirmation(1, keccak256(header), ANCHOR);
        _l1History(anchor);
        bytes memory signature = _sign(p, SEQUENCER_KEY);

        vm.expectRevert(bytes("block not posted"));
        seq.slashDivergence(p, signature, header, 0, new bytes[](0), new bytes[](0));
    }

    /// The bond stays slashable while a withdrawal waits.
    function test_bondWithdrawal() public {
        vm.prank(sequencer);
        seq.requestBondWithdrawal(4 ether);
        vm.prank(sequencer);
        vm.expectRevert(bytes("withdrawal pending"));
        seq.withdrawBond();

        vm.warp(block.timestamp + seq.BOND_WITHDRAWAL_DELAY());
        vm.prank(sequencer);
        seq.withdrawBond();
        assertEq(sequencer.balance, 4 ether);
        assertEq(seq.bond(), BOND - 4 ether);

        vm.prank(sequencer);
        seq.requestBondWithdrawal(BOND);
        SequencedNativeRollup.Preconfirmation memory a = SequencedNativeRollup.Preconfirmation(5, keccak256("a"), ANCHOR);
        SequencedNativeRollup.Preconfirmation memory b = SequencedNativeRollup.Preconfirmation(5, keccak256("b"), ANCHOR);
        seq.slashEquivocation(a, _sign(a, SEQUENCER_KEY), b, _sign(b, SEQUENCER_KEY));
        vm.warp(block.timestamp + seq.BOND_WITHDRAWAL_DELAY());
        vm.prank(sequencer);
        seq.withdrawBond();
        assertEq(sequencer.balance, 4 ether);
    }

    function anchorOf(bytes calldata header) external pure returns (bytes32) {
        // Item 19 of the header list, after the list prefix.
        uint256 offset = 3;
        for (uint256 i = 0; i < 19; i++) {
            uint8 prefix = uint8(header[offset]);
            if (prefix < 0x80) offset += 1;
            else if (prefix < 0xb8) offset += 1 + prefix - 0x80;
            else {
                uint256 lengthBytes = prefix - 0xb7;
                uint256 length;
                for (uint256 j = 0; j < lengthBytes; j++) {
                    length = (length << 8) | uint8(header[offset + 1 + j]);
                }
                offset += 1 + lengthBytes + length;
            }
        }
        require(uint8(header[offset]) == 0xa0, "anchor");
        return bytes32(header[offset + 1:offset + 33]);
    }
}
