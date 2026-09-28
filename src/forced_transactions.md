# Forced transactions

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Constraints](#constraints)
- [Design options](#design-options)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Rollups with centralized sequencers need a forced transaction mechanism to preserve L1's censorship resistance: a user must be able to submit a transaction on L1 with the guarantee that it is eventually included on L2.

## Constraints

Native rollups only support the transaction types that L1 has, so a forced transaction is an ordinary signed L2 transaction. It cannot be an unsigned transaction authenticated by its L1 sender, as in the OP and Orbit stacks. Forced transactions are usually posted in calldata rather than blobs, since a single transaction would leave most of a blob unused. Where possible, the mechanism should not interfere with the sequencer's preconfirmations.

## Design options

- **Contract-enforced queue.** Users post signed L2 transactions to an inbox on L1, and the rollup contract refuses to advance the chain unless every queued transaction older than a threshold is included. The contract can check inclusion with an SSZ inclusion proof against the block's `transactions_root`, which it already receives and which the proof binds, at some onchain cost. A sequencer that is offline, rather than censoring, also needs a fallback, such as removing the sequencer whitelist after a timeout.
- **Inclusion lists.** With [FOCIL](https://eips.ethereum.org/EIPS/eip-7805), L1 blocks must include the valid transactions of their inclusion lists. A native rollup could reuse the same logic with an inclusion list built by its contract, adding its own filters or delays to preserve preconfirmations. This requires the L1 stateless validation program to support FOCIL, and its public output to commit to the inclusion list and whether it was satisfied. FOCIL is a Hegotá headliner, but the current program does not include it yet.

## Open questions

- **Onchain mempool**: both options manage the equivalent of a mempool in a contract, which raises denial-of-service and resubmission problems. Some of these problems also affect existing forced transaction mechanisms.
