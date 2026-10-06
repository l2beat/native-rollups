// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";

import {NativeRollup} from "../src/NativeRollup.sol";
import {FramesNativeRollup} from "../src/frames/FramesNativeRollup.sol";
import {FramesSequencedRollup} from "../src/frames/FramesSequencedRollup.sol";
import {MockDependencyVerifier} from "../src/frames/MockDependencyVerifier.sol";

/// @notice Deploys a native rollup on an EIP-8141 chain without EIP-8288,
///         such as this repository's local copy of frames-devnet-0, whose
///         genesis holds the EIP-8357 registry with its first entry.
/// @dev    Environment: PRIVATE_KEY (deployer), PROVER (address signing mock
///         proofs), GENESIS_HASH and GENESIS_STATE_ROOT (the L2 genesis, from
///         `script/l2_node.py genesis`), and ROLLUP. The L2 genesis stores the
///         rollup's address in the L2 messenger while the rollup stores the
///         genesis hash, so the genesis is built for the address the rollup
///         will have, ROLLUP, which is the deployer's address at its current
///         nonce plus 1. With SEQUENCER, it deploys the preconfirmations
///         customization, whose sequencer bonds BOND wei, 10 ETH by default.
contract DeployFrames is Script {
    address constant EVM_VK_REGISTRY = 0x00005e9c1447C1A05A642ec9eB76D9C125468357;

    uint64 constant L2_CHAIN_ID = 8079;
    uint64 constant L2_GAS_LIMIT = 60_000_000;
    // The messenger predeploy of `script/l2_node.py`'s genesis.
    address constant L2_MESSENGER = 0x8079000000000000000000000000000000000001;

    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address prover = vm.envAddress("PROVER");
        (bool ok, bytes memory entry) = EVM_VK_REGISTRY.staticcall(abi.encode(bytes32(0)));
        require(ok && entry.length == 64, "no current entry in the EIP-8357 registry");

        vm.startBroadcast(key);

        MockDependencyVerifier verifier = new MockDependencyVerifier(prover);
        address sequencer = vm.envOr("SEQUENCER", address(0));
        FramesNativeRollup rollup = sequencer == address(0)
            ? new FramesNativeRollup(
                L2_CHAIN_ID,
                L2_GAS_LIMIT,
                vm.envBytes32("GENESIS_HASH"),
                vm.envBytes32("GENESIS_STATE_ROOT"),
                NativeRollup.VkPolicy.FollowCurrent,
                bytes32(0),
                EVM_VK_REGISTRY,
                L2_MESSENGER,
                address(verifier)
            )
            : new FramesSequencedRollup{value: vm.envOr("BOND", uint256(10 ether))}(
                L2_CHAIN_ID,
                L2_GAS_LIMIT,
                vm.envBytes32("GENESIS_HASH"),
                vm.envBytes32("GENESIS_STATE_ROOT"),
                NativeRollup.VkPolicy.FollowCurrent,
                bytes32(0),
                EVM_VK_REGISTRY,
                L2_MESSENGER,
                address(verifier),
                sequencer
            );
        require(address(rollup) == vm.envAddress("ROLLUP"), "rollup address differs from the L2 genesis");

        vm.stopBroadcast();

        console.log("registry", EVM_VK_REGISTRY);
        console.log("verifier", address(verifier));
        console.log("rollup  ", address(rollup));
        console.log("helper  ", rollup.framesHelper());
    }
}
