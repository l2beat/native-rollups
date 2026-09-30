// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MptProof} from "./MptProof.sol";

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
    /// @notice Marks message `index` claimed in a bitmap of 256 flags per
    ///         slot, which creates a new slot once per 256 messages instead
    ///         of once per message, and reverts if it already was.
    function markClaimed(mapping(uint256 => uint256) storage bitmap, uint256 index) internal {
        uint256 bit = 1 << (index & 0xff);
        uint256 word = bitmap[index >> 8];
        require(word & bit == 0, "already claimed");
        bitmap[index >> 8] = word | bit;
    }

    function isClaimed(mapping(uint256 => uint256) storage bitmap, uint256 index) internal view returns (bool) {
        return bitmap[index >> 8] & (1 << (index & 0xff)) != 0;
    }

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
        // The account is [nonce, balance, storage root, code hash].
        bytes calldata encoded = MptProof.get(stateRoot, keccak256(abi.encodePacked(account)), accountProof);
        bytes calldata storageRoot = MptProof.listItem(encoded, 2);
        require(storageRoot.length == 32, "invalid account");
        uint256 slot = uint256(keccak256(abi.encode(queueSlot))) + m.index;
        uint256 entry = MptProof.toUint(MptProof.get(bytes32(storageRoot), keccak256(abi.encode(slot)), storageProof));
        require(bytes32(entry) == hash(m.sender, m.to, m.value, m.data, m.index), "message not queued");
    }
}
