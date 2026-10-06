// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice An L1 token for the demo's users to bridge, minted to them at
///         deployment.
contract DemoToken is ERC20 {
    constructor(address[] memory holders, uint256 amount) ERC20("Demo token", "DEMO") {
        for (uint256 i = 0; i < holders.length; i++) {
            _mint(holders[i], amount);
        }
    }
}
