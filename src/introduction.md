# The Native Rollups Book

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [What are native rollups?](#what-are-native-rollups)
- [Problem statement](#problem-statement)
  - [Governance risk](#governance-risk)
  - [Bug risk](#bug-risk)
- [Two problems to solve](#two-problems-to-solve)
  - [Reusing the L1 STF for L2s](#reusing-the-l1-stf-for-l2s)
  - [Proof infrastructure](#proof-infrastructure)
- [Thesis: generalized proof verification](#thesis-generalized-proof-verification)
- [Candidate designs](#candidate-designs)
- [How this book is organized](#how-this-book-is-organized)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## What are native rollups?

A native rollup is a new type of EVM-based rollup that directly makes use of Ethereum's execution environment for its own state transitions, removing the need for complex and hard-to-maintain custom proof systems.

## Problem statement

### Governance risk

Today, EVM-based rollups need to make a trade-off between security and L1 equivalence. Everytime Ethereum forks, EVM-based rollups need to go through bespoke governance processes to upgrade the contracts and maintain equivalence with L1 features. A rollup governance system cannot be forced to follow Ethereum's governance decisions, and thus is free to arbitrarily diverge from it. Because of this, the best that an EVM-based rollup that strives for L1 equivalence can do is to provide a long exit window for their users, to protect them from its governance going rogue.

Exit windows present themselves with yet another trade-off:
- They can be short and reduce the un-equivalence time for users, but reducing at the same time the cases in which the exit window is effective. All protocols that require long enough time delays (e.g. vesting contracts, staking contracts, timelocked governance) would not be protected by the exit window.
- They can be long to protect more cases, at the cost of increased un-equivalence time. It's important to remember though that no finite (and reasonable) exit window length can protect all possible applications.

The only way to avoid governance risk today is to give up upgrades, remain immutable and accept that the rollup will increasingly diverge from L1 over time.

### Bug risk

EVM-based rollups need to implement complex proof systems just to be able to support what Ethereum already provides on L1. Such proof systems, even though they are getting faster and cheaper over time, are still considered not safe to be used in production in a permissionless environment. Rollups today aim to reduce this problem by implementing multiple independent proof systems that need to agree before a state transition can be considered valid, which increases protocol costs and complexity.

## Two problems to solve

Shipping native rollups requires solving two separate problems.

### Reusing the L1 STF for L2s

Even when both are EVM chains, an L2 differs from L1 in important ways. It exchanges arbitrary messages with L1, accepts native token deposits from L1 and allows withdrawals back to it, does not support blob transactions but posts its own data as blobs, has no beacon chain attached to it, allows different sequencing strategies such as centralized sequencing, and may want to collect the base fee. At the same time, a native rollup is protected from bugs only if every bug in its program is also an L1 bug. Since Ethereum cannot afford to maintain two programs, L1 and L2 must run exactly the same one, and all L2-specific behavior has to live outside it, in the rollup contract.

This problem is largely solved. [Reusing the L1 STF](./specification.md) specifies how L2 blocks map onto L1's stateless validation function, and how anchoring, messaging, deposits, fees, and forced transactions work without modifying it. Ethrex has built a [proof of concept](https://github.com/lambdaclass/ethrex/pull/6186). The remaining questions concern newer EIPs, such as Block-in-Blobs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)), and how native rollups stay compatible as L1 upgrades.

### Proof infrastructure

To verify L2 state transitions with the same infrastructure as L1, native rollups need:

- mandatory L1 execution proofs, so that L1 itself verifies its execution program through proofs;
- a way to attach proofs to transactions, either a new transaction type or a new frame mode; and
- a way to aggregate those proofs, since they are too large to include in blocks individually.

This book assumes a future L1 in which block proofs are mandatory and validators verify a single block proof instead of downloading and executing the full payload. The rest of this proof infrastructure does not exist yet, and it is the focus of [Proof verification](./generalized_proof_verification.md).

## Thesis: generalized proof verification

Building this proof infrastructure only for native rollups is not worth it. Native rollups must reuse exactly the same STF as L1: no new transaction types, no precompiles, and no other VM customization. As of today, only Linea is trying to maintain exact L1 semantics in production.

The L1 zkEVM, however, plans to separate the program from the VM that proves it. From a verifier's perspective, verifying a proof of the EVM program or of any other program is then essentially the same work. Allowing proofs of arbitrary programs to be verified supports every L2 STF, and every other ZK application as well: privacy protocols, ZK coprocessors, and more. These projects still maintain their own programs, but no longer their own proof verification infrastructure, which is arguably the hardest part.

In this world, a native rollup is just the minimal special case that uses the native program. The native rollups upgrade is massively de-risked, because most of its complexity moves into generalized proof verification, a feature that is useful on its own.

## Candidate designs

Two proposals currently describe how proofs are delivered and aggregated:

- [Proof-carrying transactions](https://ethresear.ch/t/native-proof-verification/24798): a new transaction type with an ephemeral proof sidecar, which is discarded once the builder aggregates the proof, plus opcodes that expose the proof's inputs. It follows the [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) specifications as closely as possible to maximize code reuse.
- [EIP-8288](./zkzkframes.md), also known as zkzkframes: a new [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame mode instead of a new transaction type, with proof inputs exposed through frame introspection and proofs aggregated recursively in the mempool and by the builder.

The two are equivalent in practice, and this book prefers EIP-8288 because it builds on frame transactions. It still needs to be made concrete with respect to the existing zkEVM specifications, and it is an open question whether mempool nodes can realistically perform recursive proving. On top of EIP-8288, native rollups need one more piece: the [EVM verification key registry (EIP-8357)](./evm_vk_registry.md), which lets rollup contracts refer to the same program as L1.

## How this book is organized

- **Proof verification**: the case for generalized proof verification, EIP-8288, and the EVM verification key registry.
- **Reusing the L1 STF**: the native rollup specification, and how L2-specific features work without modifying the L1 program.
- **Beyond minimal native rollups**: directions beyond the minimal design, such as execution sharding.
- **Appendix**: open problems, a review of the Orbit stack, and dependency tracking.
