# L1 vs L2 differences

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Blob-carrying transactions](#blob-carrying-transactions)
- [RANDAO](#randao)
- [Slot number](#slot-number)
- [Beacon roots storage](#beacon-roots-storage)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

A native rollup runs the L1 program unchanged, so its differences from L1 come only from the values the rollup contract puts in the payload (see [Specification](./specification.md#data-layout)). This page summarizes the differences visible to L2 users and contracts.

| Area | L1 behavior | Native rollup (L2) behavior |
|---|---|---|
| `CHAINID` | `1` | Rollup's fixed `chainId` |
| Type-3 blob transactions | Supported | Rejected (`blob_gas_used = 0`, `excess_blob_gas = 0`) |
| `PREVRANDAO` | Beacon chain RANDAO mix | Unconstrained (operator-supplied or fixed constant) |
| `SLOTNUM` ([EIP-7843](https://eips.ethereum.org/EIPS/eip-7843)) | Beacon slot number | Fixed constant (value TBD) |
| Beacon roots ([EIP-4788](https://eips.ethereum.org/EIPS/eip-4788)) | Stores parent beacon block root | Repurposed to store the [L1 anchor](./messaging.md#l1-anchoring) |
| Consensus withdrawals | Beacon validator withdrawals | Always empty (`EMPTY_LIST_ROOT`) |
| Request contracts (EIP-7002 / 7251 / 8282) | Processed by CL | Deployed in genesis (required from Amsterdam onward), but requests have no effect on L2 |
| Base fee | Burned on L1 | Burned on L2 (unless L1 later exposes `burned_fees`, see [L2 fee market](./l2_fee_market.md)) |

## Blob-carrying transactions

Rollups have no consensus layer that handles blobs, so they cannot support blob-carrying transactions. The rollup contract fixes `blob_gas_used` and `excess_blob_gas` to zero, so any block containing a blob transaction fails validation. `BLOBHASH` and the point evaluation precompile are unchanged, and behave as in an L1 block without blob transactions.

## RANDAO

`prev_randao` is an unconstrained field: the rollup contract takes it from the operator or fixes it. Existing rollups differ: Orbit stack chains return the constant `1`, OP stack chains return the value from the latest L1 block synced on L2, Linea returns `2`, Scroll returns `0`, and ZKsync returns `2500000000000000`.

## Slot number

`slot_number` is a fixed constant for L2, whose value is still to be defined (see [Specification](./specification.md#executionpayload)), so the `SLOTNUM` opcode ([EIP-7843](https://eips.ethereum.org/EIPS/eip-7843)) returns the same value in every block.

## Beacon roots storage

Native rollups repurpose `parent_beacon_block_root` as the [L1 anchor](./messaging.md#l1-anchoring), so the [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) predeploy stores anchors rather than beacon block roots. Of the rollups surveyed, only the [OP stack](https://specs.optimism.io/protocol/exec-engine.html#ecotone-beacon-block-root) supports EIP-4788, while the [Orbit stack](https://arbiscan.io/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Taiko](https://taikoscan.io/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Linea](https://lineascan.build/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), [Scroll](https://scrollscan.com/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02), and [ZKsync Era](https://era.zksync.network/address/0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02) do not.
