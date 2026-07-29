# The Native Rollups Book

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [What are native rollups?](#what-are-native-rollups)
- [Problem statement](#problem-statement)
  - [Governance risk](#governance-risk)
  - [Bug risk](#bug-risk)
- [Proof-carrying transactions](#proof-carrying-transactions)
  - [The `EXECUTE` re-execution reference](#the-execute-re-execution-reference)
- [Purpose of this book](#purpose-of-this-book)

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

## Proof-carrying transactions

The leading design uses **proof-carrying transactions** rather than asking the L1 EVM to re-execute every L2 block. It assumes a standardized identity for Ethereum's canonical stateless execution program. A native-rollup operator proves the L2 state transition with that program and submits a commitment to its public output. The rollup contract reconstructs the expected commitment from its own state and the submitted L2 block data.

This book assumes a future L1 in which block proofs are mandatory. The mandatory L1 block proof recursively verifies the proofs carried by transactions, while validators verify that block proof against a compact `NewPayloadRequestHeader` and sample the payload data through DAS. Validators therefore do not need to download the full execution payload or separately verify every L2 proof.

By reusing L1's execution program and proof-verification infrastructure, native rollups avoid maintaining a bespoke execution verifier and its upgrade governance. Changes to Ethereum execution semantics follow L1 protocol upgrades, while verifier implementation fixes that preserve those semantics can ship through ordinary node releases. The exact mandatory-proof aggregation machinery is assumed here and will be designed separately.

### The `EXECUTE` re-execution reference

The original proposal introduced an `EXECUTE` precompile that directly re-executes the same stateless validation function inside the L1 EVM. This remains useful as a concrete reference, for testing the native-rollup contract design, and for explaining the transition from re-execution to proof verification. It is not the leading production mechanism in this book; the detailed specification treats proof-carrying transactions backed by mandatory L1 proofs as the target design.

## Purpose of this book

This book is designed to serve as a comprehensive resource for understanding and contributing to our work on native rollups.

Goals of this book include:

- Specify the proof-carrying transaction design and how it reuses mandatory L1 proof infrastructure.
- Provide technical guidance for building native rollups around L1's canonical stateless execution program.
- Educate readers on the benefits of native execution and how the proposal compares to other scalability solutions.
- Provide a starting point for community members to discuss and contribute to the design and implementation of native rollups.
