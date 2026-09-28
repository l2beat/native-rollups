# Status and roadmap

*Last updated: September 2026.*

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Where things stand](#where-things-stand)
- [The case for native rollups](#the-case-for-native-rollups)
- [Timeline](#timeline)
  - [Can anything ship earlier?](#can-anything-ship-earlier)
- [Open question: upgrades with the zkEVM](#open-question-upgrades-with-the-zkevm)
- [Next steps](#next-steps)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Where things stand

- **Reusing the L1 STF** is largely specified in the [Specification](./specification.md), and ethrex has built a [proof of concept](https://github.com/lambdaclass/ethrex/pull/6186). The remaining issues are newer EIPs, such as Block-in-Blobs, and compatibility with future L1 upgrades.
- **Proof-carrying transactions** were proposed in the [Native proof verification](https://ethresear.ch/t/native-proof-verification/24798) post on ethresear.ch in May 2026, and are described in [Native proof verification](./native_verification.md). The post assumed n-of-m mandatory proofs; the 1-of-1 mandatory proofs proposed by the [strawmap](https://strawmap.org/) would simplify the specification.
- **EIP-8288** (zkzkframes) was merged as a Draft on 9 September 2026. See [Native rollups built on EIP-8288](./zkzkframes.md).
- **EIP-8357**, the EVM verification key registry, is under review in [ethereum/EIPs#12055](https://github.com/ethereum/EIPs/pull/12055), with a reference implementation in [ethereum/sys-asm#56](https://github.com/ethereum/sys-asm/pull/56) and tests in [ethereum/execution-specs#3466](https://github.com/ethereum/execution-specs/pull/3466). See [EIP-8357](./evm_vk_registry.md).

## The case for native rollups

If there is no confidence that many existing rollups will become native, is the upgrade worth it? Several teams have expressed interest, including Gnosis, Celo, Linea, Taiko, and Scroll. Even so, native rollups are worth shipping only if the feature is kept minimal, so that its low complexity justifies it even if usage turns out to be low.

Top rollups such as Arbitrum and Optimism have expressed interest in an extensible native program: most execution comes from the native program, but the project is free to add precompiles, opcodes, transaction types, and so on. No research has been done on this yet. One idea to explore is to take the native program's key from the EIP-8357 registry as an input, and express the rollup's program as a delta on top of it.

A more ambitious idea is to use native rollups explicitly as a form of sharding. Even once verification is no longer the bottleneck, the next bottleneck is serial execution when generating the block witness, and one way to address it is parallel block building. This is what rollups already do: separate state and a separate block-building pipeline that communicates asynchronously with L1 through messaging. Native rollups would allow multiple instances that are identical to L1, trustless and fully proven, as the original sharding vision intended. Two talks cover this in more detail:

- [Ethereum's roadmap to 10M TPS](https://www.youtube.com/watch?v=0O3JyJpMQLQ) (TOKEN2049 Singapore 2025): a higher-level talk on the intuitions behind parallel block building.
- [Execution sharding through native rollups](https://www.youtube.com/watch?v=69NKLnejppk) (ETHDenver 2026): a lower-level talk comparing sharding through native rollups with the live sharding implementations of Polkadot and Near.

## Timeline

Mandatory proofs and a zkzkframes-like feature are both hard requirements. According to the strawmap, zkzkframes can go live in 2028-2029 at the earliest. The Ethereum Foundation's [protocol priorities](https://blog.ethereum.org/2026/09/07/protocol-priorities) post places mandatory proofs in K\* under the current strawmap ordering, and notes that they may move back to L\* if the strawmap swaps the two forks to accelerate post-quantum consensus.

### Can anything ship earlier?

Probably not. The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) suggested shipping native rollups through [re-execution](./execute_reexecution.md) instead of ZK proofs. Re-execution, however, only supports optimistic rollups with bisection games, which still require a long challenge period for withdrawals, something all rollups are trying to move away from.

Another suggested path is to ship native rollups and generalized proof verification before mandatory proofs, as a testing ground: if something fails, only the applications that chose to take that risk fail, not all of L1. Delaying mandatory proofs is not desirable, but this remains an option if there is not enough confidence to ship them at all.

## Open question: upgrades with the zkEVM

How will upgrades work with the zkEVM? Suppose a bug is found: how do nodes upgrade? Today, most patches are backwards compatible until the bug is hit in production. With the zkEVM, the verification key needs to change, and old proofs can no longer be verified. What does the runbook for this scenario look like?

## Next steps

1. Rebase the native rollup proof of concept on Glamsterdam, and then on Hegotá.
2. Generalize the EIP-8025 specification so that arbitrary programs can be verified, with tests.
3. Make zkzkframes concrete with respect to the EIP-8025 specification.
4. Make the proof aggregation design concrete.
5. Build a proof of concept of native rollups using zkzkframes and the EVM verification key registry.
6. Research native rollups with extensions.
