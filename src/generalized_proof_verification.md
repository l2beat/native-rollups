# Generalized proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Motivation](#motivation)
- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [What L1 must provide](#what-l1-must-provide)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Motivation

Today, every Ethereum rollup maintains bespoke onchain proof verification infrastructure. ZK rollups deploy zkVM verifier contracts, adapter contracts, multi-proof dispatchers, and program whitelisting logic. Optimistic rollups ship their own onchain fraud-proof VMs (Arbitrum's WAVM, Optimism's Cannon MIPS machine) plus the surrounding dispute logic. In both cases every contract is maintained, patched, and upgraded independently in response to bugs in its specific proof system or VM, with each upgrade gated by a custom multisig or DAO. This is slow, risky, and duplicated across the ecosystem.

[EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) introduces zkVM proof verification on Ethereum's consensus layer, but only for L1's own purposes: verifying execution payloads to enable stateless and sublinear validation. Rollups still need their own onchain verifier contracts.

The proof verification infrastructure being built for L1 is not inherently L1-specific. As argued in the [introduction](./introduction.md#thesis-generalized-proof-verification), once the L1 zkEVM separates the program from the VM that proves it, verifying a proof of any program is essentially the same work. If L1 verified proofs of arbitrary programs, any rollup, including non-EVM ones, and any other application that verifies ZK proofs could rely on L1's verification instead of its own. Verifier implementation fixes that preserve the verified relation would then ship like ordinary client maintenance, rather than through each project's governance.

## How rollups verify proofs today

Each zkVM vendor provides a universal Solidity verifier contract, typically a Groth16 or Plonk check over BN254. The program's verification key and the public values are passed alongside the proof. For SP1:

```solidity
interface ISP1Verifier {
    function verifyProof(
        bytes32 programVKey,         // program verification key
        bytes calldata publicValues, // public values (inputs and/or outputs)
        bytes calldata proofBytes    // the proof
    ) external view;
}
```

Rollups then build their own stack around these verifiers. Taiko, for example, runs six contracts, each maintained and upgraded through a custom multisig:

- two raw zkVM verifiers, the vendor-provided SP1 Plonk and Risc0 Groth16 contracts;
- two adapters, one per zkVM, that implement Taiko's `IVerifier` interface and whitelist the trusted program verification keys;
- an SGX verifier; and
- a `ComposeVerifier` dispatcher that calls several verifiers and requires a sufficient combination: SGX plus either SP1 or Risc0.

## Impact on existing rollups

The table below reports Solidity SLOC (non-blank, non-comment source lines) for each project's onchain contracts, split between "core" rollup logic and the proof verification stack that generalized proof verification would retire.

| Project | Proof system | Core SLOC | Retired SLOC | % retired |
|---|---|---:|---:|---:|
| Arbitrum | Optimistic, WASM VM | 19,034 | 8,181 | 43.0% |
| Base | Optimistic, MIPS VM | 17,426 | 8,907 | 51.1% |
| ZKsync Era | Validity, EraVM | 10,823 | 2,379 | 22.0% |
| Linea | Validity, direct EVM | 8,111 | 2,460 | 30.3% |
| Lighter | Validity, no VM (custom circuits) | 5,417 | 1,699 | 31.4% |
| **Total** | | **60,811** | **23,626** | **38.9%** |

These numbers are rough estimates. They cover only onchain Solidity code and exclude offchain provers, sequencers, and each project's guest program. Governance surfaces (multisigs, timelocks, DAO contracts, proxy admins), partner-specific bridges, and proxy boilerplate are excluded from both columns.

## What L1 must provide

Generalized proof verification needs four things from L1:

1. **A program-agnostic verifier.** [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)'s proof engine only verifies proofs of L1 execution. It must be generalized to verify a proof of any program against that program's verification key and public input.
2. **Proof delivery and access from contracts.** [EIP-8288](./zkzkframes.md) lets a transaction declare a proof dependency in a frame, which contracts read through frame introspection.
3. **Aggregation.** Proofs are too large to include in blocks individually. EIP-8288 aggregates them recursively in the mempool and in the builder, and the mandatory L1 block proof must cover the result.
4. **Program identity.** A contract accepts proofs under exact verification key hashes. Applications manage the hashes they accept, as they whitelist programs today. For the EVM, L1 itself publishes the approved hash for each fork in the [EIP-8357 registry](./evm_vk_registry.md).

A native rollup is the special case whose verification key hash comes from the EIP-8357 registry, and whose contract reconstructs the proof's public output from L1 state, as described in the [Specification](./specification.md). A rollup with a custom VM follows the same pattern with its own program and verification key.
