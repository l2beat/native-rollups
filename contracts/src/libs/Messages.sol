// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {RLPReader} from "./RLPReader.sol";
import {SecureMerkleTrie} from "./SecureMerkleTrie.sol";

/// @notice A message between L1 and L2, in either direction.
struct Message {
    address sender;
    address to;
    uint256 value;
    bytes data;
    uint256 index;
}

/// @notice Messaging in both directions works the same way: the sending side
///         appends each message's hash to a queue in its storage, and the
///         receiving side proves the queue entry against a state root of the
///         sending chain.
library Messages {
    function hash(address sender, address to, uint256 value, bytes calldata data, uint256 index)
        internal
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(sender, to, value, keccak256(data), index));
    }

    /// @notice Requires `m` to be entry `m.index` of the `bytes32[]` queue at
    ///         `queueSlot` of `account`, in the state under `stateRoot`.
    function requireQueued(
        Message calldata m,
        bytes32 stateRoot,
        address account,
        uint256 queueSlot,
        bytes[] calldata accountProof,
        bytes[] calldata storageProof
    ) internal pure {
        bytes memory encoded = SecureMerkleTrie.get(abi.encodePacked(account), accountProof, stateRoot);
        bytes32 storageRoot = bytes32(RLPReader.readBytes(RLPReader.readList(encoded)[2]));
        bytes32 slot = bytes32(uint256(keccak256(abi.encode(queueSlot))) + m.index);
        bytes memory entry =
            RLPReader.readBytes(SecureMerkleTrie.get(abi.encodePacked(slot), storageProof, storageRoot));
        require(bytes32(_toUint(entry)) == hash(m.sender, m.to, m.value, m.data, m.index), "message not queued");
    }

    /// @dev Storage values are RLP-encoded with leading zero bytes removed.
    function _toUint(bytes memory b) private pure returns (uint256 v) {
        for (uint256 i = 0; i < b.length; i++) {
            v = (v << 8) | uint8(b[i]);
        }
    }
}
