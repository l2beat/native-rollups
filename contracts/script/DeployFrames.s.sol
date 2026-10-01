// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";

import {NativeRollup} from "../src/NativeRollup.sol";
import {FramesNativeRollup} from "../src/frames/FramesNativeRollup.sol";
import {FramesSequencedRollup} from "../src/frames/FramesSequencedRollup.sol";
import {MockDependencyVerifier} from "../src/frames/MockDependencyVerifier.sol";

/// @notice Deploys a native rollup on an EIP-8141 chain without EIP-8288 or
///         EIP-8357, such as frames-devnet-0.
/// @dev    The registry is the EIP-8357 runtime with its system address
///         replaced by the deployer, which registers one EVM verification key
///         hash, since no fork on this chain performs the system call.
///         Environment: PRIVATE_KEY (deployer and registry admin), PROVER
///         (address signing mock proofs), GENESIS_HASH and GENESIS_STATE_ROOT
///         (the L2 genesis, from `script/l2_node.py genesis`), and ROLLUP.
///         The L2 genesis stores the rollup's address in the L2 messenger
///         while the rollup stores the genesis hash, so the genesis is built
///         for the address the rollup will have, ROLLUP, which is the
///         deployer's address at its current nonce plus 3. With SEQUENCER,
///         it deploys the preconfirmations customization, whose sequencer
///         bonds BOND wei, 10 ETH by default.
contract DeployFrames is Script {
    bytes20 constant SYSTEM_ADDRESS = hex"fffffffffffffffffffffffffffffffffffffffe";
    // Constructor of the sys-asm registry initcode: copies and returns the
    // 165-byte runtime that follows it.
    bytes constant REGISTRY_CTOR = hex"60a58060095f395ff3";
    // Placeholder EVM verification key hash for the mock proofs.
    bytes32 constant VK_HASH = keccak256("frames-devnet mock EVM verification key");
    uint16 constant SCHEMA_ID = 0x1501; // Amsterdam, revision 1

    uint64 constant L2_CHAIN_ID = 8079;
    uint64 constant L2_GAS_LIMIT = 60_000_000;
    // The messenger predeploy of `script/l2_node.py`'s genesis.
    address constant L2_MESSENGER = 0x8079000000000000000000000000000000000001;

    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address admin = vm.addr(key);
        address prover = vm.envAddress("PROVER");

        vm.startBroadcast(key);

        address registry = _deployRegistry(admin);
        (bool ok,) = registry.call(abi.encodePacked(VK_HASH, uint256(SCHEMA_ID)));
        require(ok, "registration");

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
                registry,
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
                registry,
                L2_MESSENGER,
                address(verifier),
                sequencer
            );
        require(address(rollup) == vm.envAddress("ROLLUP"), "rollup address differs from the L2 genesis");

        vm.stopBroadcast();

        console.log("registry", registry);
        console.log("verifier", address(verifier));
        console.log("rollup  ", address(rollup));
        console.log("helper  ", rollup.framesHelper());
    }

    function _deployRegistry(address admin) internal returns (address registry) {
        bytes memory runtime = vm.parseBytes(vm.readFile("test/eip8357_registry.hex"));
        // The runtime pushes the system address right after its first five
        // bytes: CALLVALUE PUSH1 0xa1 JUMPI CALLER PUSH20 <address>.
        require(runtime.length == 165 && uint8(runtime[5]) == 0x73, "unexpected runtime");
        bytes20 current;
        assembly {
            current := mload(add(runtime, 38))
        }
        require(current == SYSTEM_ADDRESS, "unexpected system address");
        for (uint256 i = 0; i < 20; i++) {
            runtime[6 + i] = bytes20(admin)[i];
        }
        bytes memory initcode = bytes.concat(REGISTRY_CTOR, runtime);
        assembly {
            registry := create(0, add(initcode, 0x20), mload(initcode))
        }
        require(registry != address(0), "registry deployment");
    }
}
