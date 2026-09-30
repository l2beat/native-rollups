// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {NativeRollup} from "../NativeRollup.sol";
import {Frames} from "./Frames.sol";

/// @notice The native rollup contract on a chain with EIP-8141 frame
///         transactions but without EIP-8288, such as frames-devnet-0. The
///         dependency is an earlier `DEFAULT` frame that calls the
///         `MockDependencyVerifier` with the dependency triple followed by a
///         proof. With EIP-8288 the frame becomes a dependency frame, and the
///         checks below become: `mode` is the dependency frame mode and the
///         data is exactly one 96-byte triple.
contract FramesNativeRollup is NativeRollup {
    address public immutable dependencyVerifier;
    address public immutable framesHelper;

    constructor(
        uint64 chainId_,
        uint64 gasLimit_,
        bytes32 genesisBlockHash,
        bytes32 genesisStateRoot,
        VkPolicy vkPolicy_,
        bytes32 pinnedVkHash_,
        address evmVkRegistry_,
        address l2Messenger_,
        address dependencyVerifier_
    )
        NativeRollup(
            chainId_,
            gasLimit_,
            genesisBlockHash,
            genesisStateRoot,
            vkPolicy_,
            pinnedVkHash_,
            evmVkRegistry_,
            l2Messenger_
        )
    {
        dependencyVerifier = dependencyVerifier_;
        framesHelper = Frames.deployHelper();
    }

    function _readDependency(uint256 frameIndex)
        internal
        view
        override
        returns (uint8 scheme, bytes32 dataHash, bytes32 vkHash)
    {
        require(
            Frames.param(framesHelper, Frames.TARGET, frameIndex) == uint256(uint160(dependencyVerifier)),
            "not a dependency frame"
        );
        // Only earlier frames have a status.
        require(Frames.param(framesHelper, Frames.STATUS, frameIndex) == Frames.STATUS_SUCCESS, "dependency not proven");
        require(Frames.param(framesHelper, Frames.DATA_LENGTH, frameIndex) >= 96, "dependency length");
        uint256 schemeWord;
        (schemeWord, dataHash, vkHash) =
            abi.decode(Frames.data(framesHelper, frameIndex, 0, 96), (uint256, bytes32, bytes32));
        require(schemeWord <= type(uint8).max, "scheme");
        scheme = uint8(schemeWord);
    }
}
