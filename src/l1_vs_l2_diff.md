# L1 vs L2 diff

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Blob-carrying transactions](#blob-carrying-transactions)
- [RANDAO](#randao)
- [Beacon roots storage](#beacon-roots-storage)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Blob-carrying transactions

Since rollups are not (supposed to be) connected to a dedicated consensus layer that handles blobs, they cannot support blob-carrying transactions and related functionality. This is solved by the `EXECUTE` by filtering all type-3 transactions before calling the state transition function.

As a consequence, all blocks will simply not contain any blob-carrying transactions, which allows maintaing `BLOBHASH` and point evaluation operations untouched, since they would behave the same as in an L1 block with no blob-carrying transactions.

Since the `EXECUTE` precompile does a recursive call to `apply_body` and not `state_transition`, header checks are skipped, and `block_env` values can either be passed as an input, or re-use the values from L1. Since no blob-carrying transactions are present, the `excess_blob_gas` would default to zero, unless another value is passed from L1. It's important to note that L1 exposes `block.blobbasefee` and not `excess_blob_gas`, so some translation would be needed to have the proper input for `block_env`, or some other changes on L1 needs to be made.

## RANDAO

The `block.prevrandao` behaviour across existing rollups varies. Orbit stack chains return the constant `1`. OP stack chains return the value from the latest synced L1 block on L2. Linea returns the constant `2`. Scroll returns the constant `0`. ZKsync returns the constant `2500000000000000`.

The current proposal is to leave the field as an input to the `EXECUTE` precompile so that projects can decide by themselves how to handle it.

## Beacon roots storage

Not all projects support [EIP-4788: Beacon block root in the EVM](https://eips.ethereum.org/EIPS/eip-4788) as rollups are not directly connected to the beacon chain. The `EXECUTE` precompile leaves the `parent_beacon_block_root` as an input so that projects can decide by themselves how to handle it.

- Chains with support: [OP stack](https://specs.optimism.io/protocol/exec-engine.html#ecotone-beacon-block-root).
- Chains without support: [Orbit stack](https://arbiscan.io/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Taiko](https://taikoscan.io/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Linea](https://lineascan.build/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Scroll](https://scrollscan.com/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [ZKsync Era](https://era.zksync.network/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02).
