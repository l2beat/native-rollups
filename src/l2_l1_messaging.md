# L2 to L1 messaging
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Current approaches](#current-approaches)
  - [OP stack](#op-stack)
  - [Linea](#linea)
  - [Taiko](#taiko)
  - [Orbit stack](#orbit-stack)
- [Proposed design](#proposed-design)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Current approaches

While L2 -> L1 messaging can be built on top of the state root that the `EXECUTE` precompile already exposes, some projects expose a shallower interface to make it easier to provide inclusion proofs. We first discuss how existing projects implement L2 -> L1 messaging, to better understand how similar mechanisms can be implemented in native rollups.

### OP stack

[[spec](https://specs.optimism.io/fault-proof/stage-one/optimism-portal.html#block-output)] The piece of data that is used on the L1 side of the L2->L1 messaging bridge is a "block output root, which is defined as:
```solidity
struct BlockOutput {
  bytes32 version;
  bytes32 stateRoot;
  bytes32 messagePasserStorageRoot;
  bytes32 blockHash;
}
```

Inclusion proofs are verified against the `messagePasserStorageRoot` instead of the `stateRoot`, which represents the storage root of the `L2ToL1MessagePasser` contract on L2. On the L2 side, the `L2ToL1MessagePasser` contract takes a message, hashes it, and stores it in a mapping.

### Linea

[[docs](https://github.com/Consensys/linea-monorepo/blob/main/docs/architecture-description.md#l2---l1)] Linea uses a custom merkle tree of messages which is then provided as an input during settlement and verified as part of the validity proof. On the L2 side, an `MessageSent` event from the L2->L1 message.

### Taiko

[[docs](https://github.com/taikoxyz/taiko-mono/blob/56a28bb5b59510c9b708ed4222d5260f64d346c6/packages/protocol/docs/multihop_bridging_deployment.md)] Taiko uses the same mechanism as [L1->L2 messaging](./l1_l2_messaging.md#taiko) with a `SignalService`. The protocol is general enough to support both providing proofs againt a contract storage root or against a state root, by also providing an account proof.

### Orbit stack

WIP.

## Proposed design

At this point it's not clear whether it is possible to easily expose a custom data structure from L2 to L1. The `EXECUTE` precompile naturally exposes the state root, and the `block_output` can also expose the `receipts_trie` in some form, for example by exposing its root.

In principle, [EIP-7685: General purpose execution layer requests](https://eips.ethereum.org/EIPS/eip-7685) could be used, but this requires overloading its semantic from EL->CL to L2->L1 requests, and adding a new type of request that also "pollutes" the L1 execution environment.

On the other hand, it is expected that [statelessness](./tech_dependencies.md#statelessness-eip-6800) will help in reducing the cost of providing inclusion proofs directly against a state root, which might remove the need to provide a shallower interface.
