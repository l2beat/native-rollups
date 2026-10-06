// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice The L2 token of an L1 token, which the L2 bridge deploys at the
///         token's first deposit, mints for deposits and burns for
///         withdrawals.
contract BridgedERC20 is ERC20 {
    address public immutable bridge;
    address public immutable l1Token;
    uint8 private immutable _decimals;

    constructor(address l1Token_, string memory name_, string memory symbol_, uint8 decimals_) ERC20(name_, symbol_) {
        bridge = msg.sender;
        l1Token = l1Token_;
        _decimals = decimals_;
    }

    modifier onlyBridge() {
        require(msg.sender == bridge, "not the bridge");
        _;
    }

    function decimals() public view override returns (uint8) {
        return _decimals;
    }

    function mint(address to, uint256 amount) external onlyBridge {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyBridge {
        _burn(from, amount);
    }
}
