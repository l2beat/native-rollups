# Status and roadmap

*Last updated: September 2026.*

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Where things stand](#where-things-stand)
- [L1 dependencies](#l1-dependencies)
- [The case for native rollups](#the-case-for-native-rollups)
- [Timeline](#timeline)
  - [Can anything ship earlier?](#can-anything-ship-earlier)
- [Open question: upgrades with the zkEVM](#open-question-upgrades-with-the-zkevm)
- [Next steps](#next-steps)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Where things stand

- **Reusing the L1 STF** is largely specified in the [Specification](./specification.md), and ethrex has [merged a proof of concept](https://github.com/lambdaclass/ethrex/pull/6418) of the earlier `EXECUTE` precompile design, already on Glamsterdam. The remaining issues are newer EIPs, such as Block-in-Blobs, and compatibility with future L1 upgrades.
- **Proof-carrying transactions**, proposed in the [Native proof verification](https://ethresear.ch/t/native-proof-verification/24798) post in May 2026, are the main alternative to EIP-8288. The book follows EIP-8288, as explained in the [introduction](./introduction.md#candidate-designs).
- **EIP-8288** (zkzkframes) was merged as a Draft on 9 September 2026. See [EIP-8288 (zkzkframes)](./zkzkframes.md).
- **EIP-8357**, the EVM verification key registry, is under review in [ethereum/EIPs#12055](https://github.com/ethereum/EIPs/pull/12055), with a reference implementation in [ethereum/sys-asm#56](https://github.com/ethereum/sys-asm/pull/56) and tests in [ethereum/execution-specs#3466](https://github.com/ethereum/execution-specs/pull/3466). See [EIP-8357](./evm_vk_registry.md).

## L1 dependencies

| Dependency | Needed for | Status |
|---|---|---|
| L1 stateless validation program | The program native rollups prove | In development in [execution-specs `projects/zkevm`](https://github.com/ethereum/execution-specs/tree/projects/zkevm) |
| Mandatory execution proofs | One L1 block proof that covers the L2 proofs | Optional proofs ([EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)) proposed for Hegotá; mandatory in K\* under the current strawmap ordering |
| [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions | The transaction envelope for EIP-8288 dependencies | Scheduled for Hegotá |
| Compact `NewPayloadRequestHeader` | Validation without full payloads, and the contract's root computation | Removed from the optional-proof flow ([consensus-specs#5076](https://github.com/ethereum/consensus-specs/issues/5076)), assumed to return with mandatory proofs |
| [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) Block-in-Blobs | L2 block data in blobs | Declined for Hegotá on 21 September 2026 |
| [EIP-7928](https://eips.ethereum.org/EIPS/eip-7928) block-level access lists | Part of every proven payload | Scheduled for Glamsterdam |
| [EIP-7997](https://eips.ethereum.org/EIPS/eip-7997) deterministic factory | Deploying the EIP-8357 registry | Scheduled for Glamsterdam |
| [EIP-7805](https://eips.ethereum.org/EIPS/eip-7805) FOCIL | [Forced transactions](./forced_transactions.md) through L2 inclusion lists, once the L1 stateless validation program supports it | Scheduled for Hegotá |
| [EIP-7864](https://eips.ethereum.org/EIPS/eip-7864) binary state tree | Optional: cheaper storage proofs for [messaging](./messaging.md) | Draft |

## The case for native rollups

If there is no confidence that many existing rollups will become native, is the upgrade worth it? Several teams have expressed interest, including Gnosis, Celo, Linea, Taiko, and Scroll. Even so, native rollups are worth shipping only if the feature is kept minimal, so that its low complexity justifies it even if usage turns out to be low.

Top rollups such as Arbitrum and Optimism have instead expressed interest in an extensible native program, explored in [Native rollups with extensions](./extensions.md). A more ambitious direction is to use native rollups as a form of [execution sharding](./sharding_comparison.md).

## Timeline

Mandatory proofs and a zkzkframes-like feature are both hard requirements. According to the strawmap, zkzkframes can go live in 2028-2029 at the earliest. The Ethereum Foundation's [protocol priorities](https://blog.ethereum.org/2026/09/07/protocol-priorities) post places mandatory proofs in K\* under the current strawmap ordering, and notes that they may move back to L\* if the strawmap swaps the two forks to accelerate post-quantum consensus.

### Can anything ship earlier?

Probably not. The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) suggested shipping native rollups through re-execution instead of ZK proofs. Re-execution, however, only supports optimistic rollups with bisection games, which still require a long challenge period for withdrawals, something all rollups are trying to move away from.

Another suggested path is to ship native rollups and generalized proof verification before mandatory proofs, as a testing ground: if something fails, only the applications that chose to take that risk fail, not all of L1. Delaying mandatory proofs is not desirable, but this remains an option if there is not enough confidence to ship them at all.

## Open question: upgrades with the zkEVM

How will upgrades work with the zkEVM? Suppose a bug is found: how do nodes upgrade? Today, most patches are backwards compatible until the bug is hit in production. With the zkEVM, the verification key needs to change, and old proofs can no longer be verified. What does the runbook for this scenario look like?

## Next steps

1. Follow leanVM's move to RISC-V, so that the zkVM shared by L1 execution proofs and EIP-8288 can prove the L1 stateless validation program and verify proofs of arbitrary programs, which LeanSTARK dependencies require (see [EIP-8288 open issues](./zkzkframes.md#open-issues)).
2. Specify how the mandatory L1 proof binds and absorbs the EIP-8288 aggregate.
3. Make the proof aggregation design concrete.
4. Move the ethrex proof of concept from the `EXECUTE` precompile to zkzkframes and the EVM verification key registry.
5. Research native rollups with extensions.
