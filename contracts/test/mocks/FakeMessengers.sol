// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @dev Stands in for the messenger on either chain: it queues what is sent
///      through it, and its counterpart delivers each message on the other
///      chain, exposing the sender under both names, as a claim does.
contract FakeMessenger {
    struct Message {
        address sender;
        address to;
        uint256 value;
        uint256 fee;
        uint256 gasLimit;
        bytes data;
    }

    Message[] public sent;
    FakeMessenger public other;
    address internal current;

    function link(FakeMessenger other_) external {
        other = other_;
    }

    function l1Sender() external view returns (address) {
        require(current != address(0), "no message");
        return current;
    }

    function l2Sender() external view returns (address) {
        require(current != address(0), "no message");
        return current;
    }

    function sendMessage(address to, uint256 fee, uint256 gasLimit, bytes calldata data) external payable {
        require(msg.value >= fee, "fee exceeds value");
        sent.push(Message(msg.sender, to, msg.value - fee, fee, gasLimit, data));
    }

    function count() external view returns (uint256) {
        return sent.length;
    }

    /// @dev Delivers message `index` sent here on the other chain.
    function relay(uint256 index) external {
        Message memory m = sent[index];
        other.deliver{value: m.value}(m.sender, m.to, m.gasLimit, m.data);
    }

    function deliver(address sender, address to, uint256 gasLimit, bytes calldata data) external payable {
        current = sender;
        (bool ok, bytes memory out) = to.call{value: msg.value, gas: gasLimit}(data);
        current = address(0);
        if (!ok) {
            assembly {
                revert(add(out, 32), mload(out))
            }
        }
    }
}
