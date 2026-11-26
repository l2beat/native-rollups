// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Frames} from "frames/Frames.sol";

/// @notice Reads an EIP-8288 dependency frame the way a native rollup would,
///         and logs what it sees.
contract DepProbe {
    address public immutable helper;

    event Dependency(
        uint256 mode, uint256 dataLength, bool hasStatus, uint256 status, uint256 scheme, bytes32 dataHash, bytes32 vkHash
    );

    constructor() {
        helper = Frames.deployHelper();
    }

    function probe(uint256 frameIndex) external {
        uint256 mode = Frames.param(helper, Frames.MODE, frameIndex);
        uint256 dataLength = Frames.param(helper, Frames.DATA_LENGTH, frameIndex);
        // A frame that runs no code may have no status.
        (bool hasStatus, bytes memory out) = helper.staticcall(abi.encode(Frames.STATUS, frameIndex));
        uint256 status = hasStatus && out.length == 32 ? abi.decode(out, (uint256)) : type(uint256).max;
        (uint256 scheme, bytes32 dataHash, bytes32 vkHash) =
            abi.decode(Frames.data(helper, frameIndex, 0, 96), (uint256, bytes32, bytes32));
        emit Dependency(mode, dataLength, hasStatus, status, scheme, dataHash, vkHash);
    }
}
