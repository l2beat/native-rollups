// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {L2ERC20Bridge} from "./L2ERC20Bridge.sol";
import {PeerApp} from "./PeerApp.sol";

/// @notice The L1 side of an example ERC-20 bridge, as rollups run them: it
///         holds deposited tokens, has its peer mint as many on L2, and
///         releases them when its peer burns them for a withdrawal.
contract L1ERC20Bridge is PeerApp {
    using SafeERC20 for IERC20Metadata;

    /// @notice The gas a deposit's delivery gets: a token's first deposit
    ///         deploys its L2 token.
    uint256 public constant DEPOSIT_GAS_LIMIT = 5_000_000;

    event DepositSent(address indexed token, address indexed from, address indexed to, uint256 amount);
    event WithdrawalFinalized(address indexed token, address indexed from, address indexed to, uint256 amount);

    constructor(address rollup, address l2Bridge) PeerApp(rollup, false, l2Bridge) {}

    /// @notice Locks `amount` of `token` here, for its L2 token to `to` on
    ///         L2. The value pays whoever claims the deposit there.
    function deposit(address token, address to, uint256 amount) external payable {
        IERC20Metadata t = IERC20Metadata(token);
        t.safeTransferFrom(msg.sender, address(this), amount);
        emit DepositSent(token, msg.sender, to, amount);
        callPeer(
            msg.value,
            msg.value,
            DEPOSIT_GAS_LIMIT,
            abi.encodeCall(L2ERC20Bridge.finalizeDeposit, (token, msg.sender, to, amount, t.name(), t.symbol(), t.decimals()))
        );
    }

    /// @notice Releases the tokens a withdrawal burned on L2.
    function finalizeWithdrawal(address token, address from, address to, uint256 amount) external onlyPeer {
        IERC20Metadata(token).safeTransfer(to, amount);
        emit WithdrawalFinalized(token, from, to, amount);
    }
}
