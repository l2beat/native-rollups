// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MptProof} from "./MptProof.sol";

/// @notice A message between L1 and L2, in either direction. `value` goes
///         to `to` with the call, and `fee` to whoever claims the message,
///         which pays for claims that the recipient cannot make itself.
///         Whoever claims also chooses the claim's gas, so the call gets
///         `gasLimit` or the claim fails.
struct Message {
    address sender;
    address to;
    uint256 value;
    uint256 fee;
    uint256 gasLimit;
    bytes data;
    uint256 index;
}

/// @notice Message hashing, claimed flags, and queue proofs for messaging in
///         both directions. L2 to L1 messages sit in a queue in the L2
///         messenger's storage, proven entry by entry against an L2 state
///         root. L1 to L2 messages sit in a `MessageTree` instead.
library Messages {
    // What the call costs before `to` runs: mostly a cold account access and
    // a value transfer.
    uint256 internal constant CALL_OVERHEAD = 40_000;

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

    function hash(Message calldata m) internal pure returns (bytes32) {
        return hash(m.sender, m.to, m.value, m.fee, m.gasLimit, m.data, m.index);
    }

    function hash(
        address sender,
        address to,
        uint256 value,
        uint256 fee,
        uint256 gasLimit,
        bytes calldata data,
        uint256 index
    ) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(sender, to, value, fee, gasLimit, keccak256(data), index));
    }

    /// @notice Delivers `m`: calls `to` with its value, data and gas limit,
    ///         then pays its fee to `feeRecipient`. Since whoever claims
    ///         chooses the claim's gas, the claim fails unless the call gets
    ///         the gas limit: EIP-150 passes on at most 63/64 of the gas left.
    ///         Under EIP-8037 the call also draws state gas, which a frame
    ///         transaction pays only out of the frame's budget, so that
    ///         budget, `stateGasLeft`, must cover the gas limit too.
    function deliver(Message calldata m, address feeRecipient, uint256 stateGasLeft) internal {
        bytes memory data = m.data;
        require(
            gasleft() >= m.gasLimit * 64 / 63 + CALL_OVERHEAD && stateGasLeft >= m.gasLimit, "gas below message limit"
        );
        (bool ok,) = m.to.call{gas: m.gasLimit, value: m.value}(data);
        require(ok, "delivery failed");
        if (m.fee > 0) {
            (ok,) = feeRecipient.call{value: m.fee}("");
            require(ok, "fee payment failed");
        }
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
        bytes32 slot = bytes32(uint256(keccak256(abi.encode(queueSlot))) + m.index);
        uint256 entry = MptProof.storageValue(stateRoot, account, slot, accountProof, storageProof);
        require(bytes32(entry) == hash(m), "message not queued");
    }
}
