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

- two raw zkVM verifiers, the vendor-provided SP1 Plonk and Risc0 Groth16 contracts,
- two adapters, one per zkVM, that implement Taiko's `IVerifier` interface and whitelist the trusted program verification keys,
- an SGX verifier, and
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

These numbers are rough estimates. They cover only onchain Solidity code and exclude offchain provers, sequencers, and each project's guest program. Governance surfaces (multisigs, timelocks, DAO contracts, proxy admins), partner-specific bridges, and proxy boilerplate are excluded from both columns. Under `leanPCS`, optimistic rollups still retire their entire fraud-proof VM and dispute stack, while validity rollups replace their bespoke elliptic-curve and PCS verifiers with smaller, shared onchain PIOP verifiers rather than zero Solidity.

## What L1 must provide

Generalized proof verification under [`leanPCS`](./leanpcs.md) needs four things from L1:

1. **A shared post-quantum PCS and field instruction.** Rather than enshrining a single zkVM ISA in consensus, [`leanPCS`](./leanpcs.md) fixes one binary field (`GF(2^128)`) and a versioned multilinear PCS (ZK-WHIR), paired with the `MULFIELD` opcode so any zkVM or circuit can verify its lightweight PIOP directly in the EVM.
2. **Claim delivery and access from contracts.** Extending [EIP-8288](./leanpcs.md) lets a transaction declare `PCS_SCHEME` opening-claim hashes in a dependency frame, which contracts check via EIP-8141 frame introspection (`FRAMEPARAM`, `FRAMEDATALOAD`).
3. **Aggregation.** Mempool nodes and the builder aggregate all PCS opening proofs in the block into a single recursive STARK (`DependencyProof`), and K\*'s mandatory L1 block proof must bind and cover the result.
4. **Program identity.** Contracts verify proofs against exact program hashes (and, under `leanPCS`, through an onchain PIOP verifier contract). For the EVM, L1 publishes the approved program hash and `schema_id` for each fork in the [EIP-8357 registry](./evm_vk_registry.md).

**Trade-off compared to protocol-enshrined verifiers.** Under `leanPCS`, custom rollups still retire their heavy elliptic-curve verifiers, PCS/Merkle logic, and optimistic fraud-proof VMs, and can share one lightweight PIOP verifier contract per zkVM. Unlike a consensus-enshrined `ProofEngine` ([Native proof verification](https://ethresear.ch/t/native-proof-verification/24798)), however, PIOP verifier upgrades for custom rollups remain onchain contract updates rather than consensus-client releases.

A native rollup is the special case whose program hash comes from the EIP-8357 registry, verifying K\*'s unwrapped L1 execution PIOP over `leanPCS` against the public output reconstructed from L1 state, as described in the [Specification](./specification.md). A rollup with a custom VM follows the same pattern with its own program and PIOP verifier.
