// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {NativeRollup} from "../NativeRollup.sol";
import {SequencedNativeRollup} from "../SequencedNativeRollup.sol";
import {FramesNativeRollup} from "./FramesNativeRollup.sol";

/// @notice `FramesNativeRollup` with the preconfirmations customization.
contract FramesSequencedRollup is SequencedNativeRollup, FramesNativeRollup {
    constructor(
        uint64 chainId_,
        uint64 l2GasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        VkPolicy vkPolicy_,
        bytes32 pinnedVkHash_,
        address evmVkRegistry_,
        address l2Messenger_,
        address dependencyVerifier_,
        address sequencer_
    )
        payable
        FramesNativeRollup(
            chainId_,
            l2GasLimit_,
            genesisBlockHash,
            genesisStateRoot,
            vkPolicy_,
            pinnedVkHash_,
            evmVkRegistry_,
            l2Messenger_,
            dependencyVerifier_
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
