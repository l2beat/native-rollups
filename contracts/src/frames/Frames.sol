// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice EIP-8141 frame introspection through the helper assembled from
///         `frames/frame_introspection.eas`, since solc does not support the
///         frame instructions yet.
library Frames {
    // `FRAMEPARAM` parameters.
    uint256 internal constant TARGET = 0x00;
    uint256 internal constant MODE = 0x02;
    uint256 internal constant DATA_LENGTH = 0x04;
    uint256 internal constant STATUS = 0x05;

    uint256 internal constant STATUS_SUCCESS = 1;

    // `TXPARAM` parameter: the state gas left in the executing frame.
    uint256 internal constant STATE_GAS_LEFT = 0x0C;

    // Runtime of `frames/frame_introspection.eas`.
    bytes internal constant HELPER_RUNTIME =
        hex"366020146018573660401460225736606014602f575f5ffd5b5f35b05f5260205ff35b5f35602035b35f5260205ff35b5f356040356020355fb26040355ff3";

    /// @notice Deploys the helper behind a constructor that returns the rest
    ///         of the initcode as runtime code.
    function deployHelper() internal returns (address helper) {
        bytes memory initcode = bytes.concat(hex"600b380380600b3d393df3", HELPER_RUNTIME);
        assembly ("memory-safe") {
            helper := create(0, add(initcode, 0x20), mload(initcode))
        }
        require(helper != address(0), "helper deployment");
    }

    /// @notice `FRAMEPARAM(param, frameIndex)`.
    function param(address helper, uint256 which, uint256 frameIndex) internal view returns (uint256) {
        (bool ok, bytes memory out) = helper.staticcall(abi.encode(which, frameIndex));
        require(ok && out.length == 32, "FRAMEPARAM");
        return abi.decode(out, (uint256));
    }

    /// @notice The state gas a call can draw on. A frame transaction pays
    ///         state gas only out of the executing frame's budget, which
    ///         `TXPARAM(0x0C)` reads. Elsewhere the frame instructions halt,
    ///         and state gas spills into the call's gas once the
    ///         transaction's reservoir is empty, so it has no separate bound.
    function stateGasLeft(address helper) internal view returns (uint256) {
        (bool ok, bytes memory out) = helper.staticcall{gas: 1_000}(abi.encode(STATE_GAS_LEFT));
        return ok && out.length == 32 ? abi.decode(out, (uint256)) : type(uint256).max;
    }

    /// @notice `length` bytes of frame `frameIndex`'s data from `offset`.
    function data(address helper, uint256 frameIndex, uint256 offset, uint256 length)
        internal
        view
        returns (bytes memory out)
    {
        bool ok;
        (ok, out) = helper.staticcall(abi.encode(frameIndex, offset, length));
        require(ok && out.length == length, "FRAMEDATACOPY");
    }
}
