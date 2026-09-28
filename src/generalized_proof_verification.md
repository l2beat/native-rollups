# Generalized proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [What L1 must provide](#what-l1-must-provide)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

This page shows what generalized proof verification would replace, and what L1 must provide for it. The case for it is made in the [introduction](./introduction.md#thesis-generalized-proof-verification).

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

Optimistic rollups instead ship their own onchain fraud-proof VMs, such as Arbitrum's WAVM and Optimism's Cannon MIPS machine, plus the dispute logic around them.

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
3. **Aggregation.** EIP-8288 aggregates proofs recursively in the mempool and in the builder, and the mandatory L1 block proof must cover the result.
4. **Program identity.** A contract accepts proofs under exact verification key hashes. Applications manage the hashes they accept, as they whitelist programs today. For the EVM, L1 itself publishes the approved hash for each fork in the [EIP-8357 registry](./evm_vk_registry.md).

A native rollup is the special case whose verification key hash comes from the EIP-8357 registry, and whose contract reconstructs the proof's public output from L1 state, as described in the [Specification](./specification.md). A rollup with a custom VM follows the same pattern with its own program and verification key.
