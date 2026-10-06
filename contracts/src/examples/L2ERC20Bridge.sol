// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {BridgedERC20} from "./BridgedERC20.sol";
import {L1ERC20Bridge} from "./L1ERC20Bridge.sol";
import {PeerApp} from "./PeerApp.sol";

/// @notice The L2 side of an example ERC-20 bridge: one L2 token per L1
///         token, deployed at its first deposit, minted for deposits and
///         burned for withdrawals.
contract L2ERC20Bridge is PeerApp {
    /// @notice The gas a withdrawal's delivery gets on L1.
    uint256 public constant WITHDRAWAL_GAS_LIMIT = 200_000;

    mapping(address l1Token => address) public l2TokenOf;

    event TokenDeployed(address indexed l1Token, address indexed l2Token);
    event DepositFinalized(address indexed l1Token, address indexed from, address indexed to, uint256 amount);
    event WithdrawalSent(address indexed l1Token, address indexed from, address indexed to, uint256 amount);

    constructor(address l2Messenger, address l1Bridge) PeerApp(l2Messenger, true, l1Bridge) {}

    /// @notice Mints the L2 token of `l1Token` for a deposit, deploying it
    ///         at the first one.
    function finalizeDeposit(
        address l1Token,
        address from,
        address to,
        uint256 amount,
        string calldata name,
        string calldata symbol,
        uint8 decimals
    ) external onlyPeer {
        address token = l2TokenOf[l1Token];
        if (token == address(0)) {
            token = address(new BridgedERC20(l1Token, name, symbol, decimals));
            l2TokenOf[l1Token] = token;
            emit TokenDeployed(l1Token, token);
        }
        BridgedERC20(token).mint(to, amount);
        emit DepositFinalized(l1Token, from, to, amount);
    }

    /// @notice Burns `amount` of the sender's `l2Token`, for its L1 token to
    ///         `to` on L1. The value pays whoever claims the withdrawal there.
    function withdraw(address l2Token, address to, uint256 amount) external payable {
        address l1Token = BridgedERC20(l2Token).l1Token();
        // A contract that is not a token of this bridge could name any L1
        // token.
        require(l2TokenOf[l1Token] == l2Token, "not a bridged token");
        BridgedERC20(l2Token).burn(msg.sender, amount);
        emit WithdrawalSent(l1Token, msg.sender, to, amount);
        callPeer(
            msg.value,
            msg.value,
            WITHDRAWAL_GAS_LIMIT,
            abi.encodeCall(L1ERC20Bridge.finalizeWithdrawal, (l1Token, msg.sender, to, amount))
        );
    }
}
