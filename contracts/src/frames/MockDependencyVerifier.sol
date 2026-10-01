// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/// @notice Stand-in for EIP-8288 on a chain with EIP-8141 but without it,
///         such as frames-devnet-0. A `DEFAULT` frame calls this contract
///         with the EIP-8288 frame data followed by a proof:
///         `scheme || data_hash || verification_key_hash || proof`. The call
///         succeeds only if the proof is valid, and a rollup requires the
///         frame to target this contract and to have succeeded.
/// @dev    The proof is a signature by a trusted prover over the triple, in
///         place of the recursive STARK that EIP-8288 requires.
contract MockDependencyVerifier {
    address public immutable prover;

    constructor(address prover_) {
        prover = prover_;
    }

    /// @notice The digest the prover signs for a dependency triple.
    function digest(bytes calldata triple) public view returns (bytes32) {
        require(triple.length == 96, "triple length");
        return keccak256(abi.encodePacked(block.chainid, address(this), triple));
    }

    fallback() external {
        require(msg.data.length == 96 + 65, "length");
        require(ECDSA.recover(digest(msg.data[:96]), msg.data[96:]) == prover, "not proven");
    }
}
