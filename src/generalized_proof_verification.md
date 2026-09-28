# Generalized proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Motivation](#motivation)
- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
  - [Example: Taiko (multi-verifier)](#example-taiko-multi-verifier)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [Custom VMs](#custom-vms)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Motivation

Today, every Ethereum rollup maintains bespoke onchain proof verification infrastructure. ZK rollups deploy zkVM verifier contracts, adapter contracts, multi-proof dispatchers, and program whitelisting logic. Optimistic rollups ship their own onchain fraud-proof VMs (Arbitrum's WAVM, Optimism's Cannon MIPS machine) plus the surrounding dispute logic. In both cases every contract is maintained, patched, and upgraded independently in response to bugs in its specific proof system or VM, with each upgrade gated by a custom multisig or DAO. This is slow, risky, and duplicated across the ecosystem.

[EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) introduces zkVM proof verification on Ethereum's consensus layer, but only for L1's own purposes: verifying execution payloads to enable stateless and sublinear validation. Rollups still need their own onchain verifier contracts.

However, the infrastructure that EIP-8025 brings to the CL, the `ProofEngine`, proof gossip, and verification logic, is not inherently L1-specific. If generalized to be program-agnostic and exposed to smart contracts via a new transaction type, any rollup, even non-EVM ones, could offload proof verification to the CL. Verifier implementation fixes that preserve protocol-defined validity semantics can ship exactly like ordinary geth or Nethermind maintenance. This is the same principle behind [native rollups](https://eips.ethereum.org/EIPS/eip-8079), but more generalized: just as native rollups inherit L1's execution environment, native proof verification lets any rollup inherit L1's proof verification infrastructure.

Although this document frames the proposal around rollups, the same primitive serves any contract that verifies a ZK proof onchain: privacy systems, ZK coprocessors, identity, ZK ML, and others.

## How rollups verify proofs today

Each zkVM vendor provides a universal Solidity verifier contract (typically a Groth16 or Plonk check over BN254). The program identity and the public values (any inputs and outputs the circuit commits to) are passed alongside the proof. For SP1:

```solidity
interface ISP1Verifier {
    function verifyProof(
        bytes32 programVKey,         // concrete SP1 program VK
        bytes calldata publicValues, // public values (inputs and/or outputs)
        bytes calldata proofBytes    // the proof
    ) external view;
}
```

**A note on terminology.** SP1 calls `programVKey` a "verification key", but this collides with the zkVM's own circuit verification key. This document keeps them separate:

- **Program identity (`program_id`)**: a standardized, backend-independent `bytes32` commitment to deterministic program semantics. This is the identity exposed to contracts and remains stable across behavior-preserving compiler and verifier changes.
- **Guest implementation**: a concrete implementation of those semantics, such as Ethrex, Reth, or Zesu for L1 stateless validation. Guest implementation and version are verifier metadata, not application identity; implementations fully bound to the same deterministic behavior share one `program_id`.
- **Concrete program VK** (called `programVKey` by SP1 and `imageId` by Risc0): a backend- and build-specific identifier for compiled guest code. [ERE](https://github.com/eth-act/ere) expresses this as each backend's [`zkVMVerifier::ProgramVk`](https://github.com/eth-act/ere/blob/master/crates/verifier/core/src/verifier.rs) associated type. It may change without changing `program_id`.
- **Backend family (`backend_type`)**: the named proof-system or zkVM family, such as SP1, Risc0, OpenVM, or ZisK. This is application-visible and is the unit counted for multi-proof diversity; distinct families may still share dependencies and are not assumed to be perfectly independent.

### Example: Taiko (multi-verifier)

Taiko illustrates the complexity that arises when a rollup uses multiple proof systems. Its verification architecture involves six contracts across three tiers (two raw verifiers, two adapters, one dispatcher, one SGX verifier), each independently maintained and upgraded through a custom multisig.

**1. Raw zkVM verifiers.** Taiko deploys both an SP1 Plonk verifier (`SP1Verifier.sol`) and a Risc0 Groth16 verifier (`RiscZeroGroth16Verifier.sol`). These are the vendor-provided universal verifier contracts.

**2. Taiko-specific adapters.** Each raw verifier is wrapped in an adapter contract that implements Taiko's `IVerifier` interface:

```solidity
// TaikoSP1Verifier: adapter for SP1
contract TaikoSP1Verifier is IVerifier {
    address public sp1RemoteVerifier;                    // raw SP1 verifier
    mapping(bytes32 => bool) public isProgramTrusted;    // whitelisted programs

    function verifyProof(Context[] calldata _ctxs, bytes calldata _proof) external view {
        bytes32 aggregationProgram = bytes32(_proof[:32]);
        bytes32 blockProvingProgram = bytes32(_proof[32:64]);
        require(isProgramTrusted[aggregationProgram]);
        require(isProgramTrusted[blockProvingProgram]);

        bytes memory publicInputs = buildPublicInputs(_ctxs);
        ISP1Verifier(sp1RemoteVerifier).verifyProof(
            aggregationProgram, publicInputs, _proof[64:]
        );
    }
}
```

A parallel `Risc0Verifier` has the same shape, with `isImageTrusted` replacing `isProgramTrusted` and `sha256(buildPublicInputs(...))` as the journal digest.

**3. Multi-verifier dispatcher.** A `ComposeVerifier` contract orchestrates multiple verifiers and enforces that a sufficient set has verified each proof:

```solidity
contract MainnetVerifier is ComposeVerifier {
    address public immutable sgxGethVerifier;    // SGX verifier (required)
    address public immutable risc0RethVerifier;  // Risc0 option
    address public immutable sp1RethVerifier;    // SP1 option

    function verifyProof(Context[] calldata _ctxs, bytes calldata _proof) external {
        SubProof[] memory subProofs = abi.decode(_proof, (SubProof[]));
        for (uint256 i = 0; i < subProofs.length; ++i) {
            IVerifier(subProofs[i].verifier).verifyProof(_ctxs, subProofs[i].proof);
        }
        require(areVerifiersSufficient(verifiers));
    }

    function areVerifiersSufficient(address[] memory _verifiers) internal view override {
        // Must have exactly 2: sgxGethVerifier + (risc0 or sp1)
    }
}
```

## Impact on existing rollups

The table below reports Solidity SLOC (non-blank, non-comment source lines) for each project's onchain contracts, split between "core" rollup logic and the proof verification stack that native proof verification would retire.

| Project | Proof system | Core SLOC | Retired SLOC | % retired |
|---|---|---:|---:|---:|
| Arbitrum | Optimistic, WASM VM | 19,034 | 8,181 | 43.0% |
| Base | Optimistic, MIPS VM | 17,426 | 8,907 | 51.1% |
| ZKsync Era | Validity, EraVM | 10,823 | 2,379 | 22.0% |
| Linea | Validity, direct EVM | 8,111 | 2,460 | 30.3% |
| Lighter | Validity, no VM (custom circuits) | 5,417 | 1,699 | 31.4% |
| **Total** | | **60,811** | **23,626** | **38.9%** |

These numbers are rough estimates. They cover only on-chain Solidity code and exclude off-chain provers, sequencers, and the guest program behind each `program_id`. Governance surfaces (multisigs, timelocks, DAO contracts, proxy admins), partner-specific bridges, and proxy boilerplate are excluded from both columns.

## Custom VMs
Rollups with custom VMs (non-EVM) can use L1's proof verification infrastructure through the [native proof verification](./native_verification.md) proposal. Instead of deploying their own onchain verifier contracts, they submit proof-carrying transactions with their custom guest program's hash. The contract pattern is identical to a native rollup, just with a different `program_hash`. See the [native proof verification](./native_verification.md) page for details.
