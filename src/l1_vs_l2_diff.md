# L1 vs L2 diff

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [L1 vs L2 diff](#l1-vs-l2-diff)
  - [Blob-carrying transactions](#blob-carrying-transactions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Blob-carrying transactions

Since rollups are not (supposed to be) connected to a dedicated consensus layer that handles blobs, they cannot support blob-carrying transactions and related functionality. This is solved by the `EXECUTE` by filtering all type-3 transactions before calling the state transition function.

As a consequence, all blocks will simply not contain any blob-carrying transactions, which allows maintaing `BLOBHASH` and point evaluation operations untouched, since they would behave the same as in an L1 block with no blob-carrying transactions.

Since the `EXECUTE` precompile does a recursive call to `apply_body` and not `state_transition`, header checks are skipped, and `block_env` values can either be passed as an input, or re-use the values from L1. Since no blob-carrying transactions are present, the `excess_blob_gas` would default to zero, unless another value is passed from L1. It's important to note that L1 exposes `block.blobbasefee` and not `excess_blob_gas`, so some translation would be needed to have the proper input for `block_env`.

## RANDAO

The `block.prevrandao` behaviour across existing rollups varies. Orbit stack chains return the constant `1`. OP stack chains return the value from the latest synced L1 block on L2. Linea returns the constant `2`. Scroll returns the constant `0`. ZKsync returns the constant `2500000000000000`.

The current proposal is to leave the field as an input to the `EXECUTE` precompile so that projects can decide by themselves how to handle it.
