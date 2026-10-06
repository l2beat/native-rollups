# Forced transactions

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Constraints](#constraints)
- [L2 FOCIL](#l2-focil)
- [What L1 must provide](#what-l1-must-provide)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Rollups with centralized sequencers need a forced transaction mechanism to preserve L1's censorship resistance: a user must be able to submit a transaction on L1 with the guarantee that it is eventually included on L2.

## Constraints

Native rollups only support the transaction types that L1 has, so a forced transaction is an ordinary signed L2 transaction. It cannot be an unsigned transaction authenticated by its L1 sender, as in the OP and Orbit stacks, and it cannot reserve space in the block or skip the base fee. Where possible, the mechanism should not interfere with the sequencer's preconfirmations.

A queue that requires every old entry to be included does not work: a signed transaction can become invalid after submission, for example if its sender spends the balance, and the chain could not advance past it.

## L2 FOCIL

[FOCIL](https://eips.ethereum.org/EIPS/eip-7805) already forces transactions into L1 blocks without new transaction types. The execution layer checks the block against an inclusion list, and a listed transaction may only be missing if it could not have been appended at the end of the block: it is invalid, underpriced, or does not fit. A native rollup can reuse this check unchanged and replace the parts that build the list, the inclusion list committee and the mempool, with an L1 inbox contract. The design is described in [Repurposing FOCIL as an L2 forced transaction mechanism](https://ethresear.ch/t/repurposing-focil-as-an-l2-forced-transaction-mechanism/25233), with a [prototype contract](https://github.com/l2beat/native-rollups/blob/main/contracts/src/ForcedInboxValidated.sol) in this repository.

The inbox works as follows:

1. **Submission.** Anyone submits a signed L2 transaction. The contract runs the stateless checks (signature, intrinsic gas, bounds), and the nonce and balance checks against an account proof on a recent L2 state root. Without the stateful checks, the head of the queue could be filled with high-fee transactions that can never execute. Entries are ordered by `maxFeePerGas`, with one entry per sender, and a full queue evicts its cheapest entry.
2. **Inclusion list.** When the rollup advances, the list is the head of the queue, up to a gas budget and while entries pay the base fee. Only entries submitted at least a threshold before the block's anchor qualify, and a replacement counts as a new submission, so the sequencer knows every entry the list could hold when it builds the block, however late it posts it, as its [preconfirmations](./preconfirmations.md) require. Entries can still leave the queue before then, bringing later qualified entries into the list, so a sequencer that preconfirms includes every qualified entry it can.
3. **Enforcement.** The list is an input to the L2 proof, and the rollup contract only advances if the proof reports it satisfied. On L1, satisfaction affects fork choice; here it becomes a condition for advancing.
4. **Clearing.** Satisfaction does not say which listed transactions were included, so entries leave the queue in two ways. Anyone can prune an entry with an account proof at a newer block, showing that its nonce advanced or that its balance no longer covers it. The nonce alone is not enough, since [EIP-7702](https://eips.ethereum.org/EIPS/eip-7702) lets a balance fall without it. At settlement, the operator proves which listed transactions are in the block's `transactions_root`, and an absent entry is dropped if the block had room for it, since it must then have been invalid. An entry that did not fit stays for the next block.
5. **Offline sequencer.** A timeout removes the sequencer whitelist if the sequencer stops producing blocks.

Block stuffing remains possible but costly, as on L1: EIP-1559 raises the base fee exponentially while the listed transactions wait. This relies on every L2 block carrying the list, otherwise the operator could publish empty blocks to lower the base fee cheaply. The [Specification](./specification.md) already proves one L2 block per update.

In the prototype, a submission costs about 620k gas and a prune about 430k gas, roughly 0.0006 and 0.0004 ETH at 1 gwei. Settlement costs about 100k to 115k gas per included entry with Merkle-Patricia proofs, so a list of 32 entries takes about 3.7M gas. Against the SSZ `transactions_root` that the rollup contract already receives, an inclusion proof is a short sha256 branch.

## What L1 must provide

The rollup reuses L1's stateless validation program, so that program must take the inclusion list as an input, and its proven output must commit to the list and to whether it was satisfied. FOCIL is scheduled for Hegotá and specified on top of Amsterdam in [`eips/amsterdam/eip-7805`](https://github.com/ethereum/execution-specs/tree/eips/amsterdam/eip-7805), where satisfaction is computed separately from block validity and consumed by fork choice. The zkEVM program does not include it yet, and neither EIP-8025 nor the consensus specs say how a proof commits to it. L1 needs the same once attesters verify proofs instead of executing payloads.

## Open questions

- **Account proofs.** Submitting and pruning require account proofs, which today require a full node, prohibitive for most users of an L2. Block-level access lists carry storage diffs but not storage roots, so tracking them is not enough to build account proofs. [EIP-8268](https://eips.ethereum.org/EIPS/eip-8268) would add storage roots to them, so that nodes tracking only accounts, as in [VOPS](https://ethresear.ch/t/a-pragmatic-path-towards-validity-only-partial-statelessness-vops/22236), could serve these proofs.
- **Pruning incentives.** A prune costs about 430k gas and benefits everyone waiting in the queue. Submitters could post a small bond that refunds whoever prunes their entry.
