# Preconfirmations

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Commitments](#commitments)
- [Slashing](#slashing)
- [Partial blocks](#partial-blocks)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

The [Specification](./specification.md) describes a permissionless rollup, in which anyone with a proof can post the next block. A rollup can instead customize it with a centralized sequencer, which confirms transactions long before their block reaches L1: the sequencer shares each block as soon as it builds it, and users act on it. These preconfirmations are only as good as the sequencer's word. The OP stack gossips blocks that the sequencer signs, and its nodes [expect that the sequencer will not equivocate](https://specs.optimism.io/protocol/rollup-node-p2p.html#branch-selection). Arbitrum's sequencer feed and Taiko's preconfirmations work the same way, and none of them can penalize a broken promise. [Lighter](https://assets.lighter.xyz/whitepaper.pdf) plans a pre-commitment scheme that binds the data its sequencer broadcasts to the state it later commits onchain.

A native rollup can back preconfirmations with a bond that L1 slashes. The rollup contract receives the hash of every L2 block, and the proof binds it, so a preconfirmation can be a signature over the hash that will be posted, and a broken one is a mismatch between two hashes.

## Commitments

The sequencer signs the block's number, hash, and anchor block number, for the rollup contract's address and L1 chain ID. The hash fixes everything `advance` rebuilds: the payload fields, the versioned hashes, since [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)'s encoding is deterministic, the [anchor](./messaging.md#l1-anchoring) and the execution requests. With the signature, each user receives the header and proofs of their transaction and its receipt against it, and checks that the anchor block number is that of the anchor the header holds, which the slashing below relies on.

The hash does not fix the L1 context in which the block is posted:

1. **Who posts.** In the [Specification](./specification.md#nativerollup-contract), anyone with a proof can call `advance`, so another party could post a different block at the same height. A sequenced rollup takes blocks only from the sequencer, except after the [forced transactions](./forced_transactions.md) timeout, which must be longer than the posting deadline.
2. **Deadline.** A block must reach L1 while its anchor is in the `BLOCKHASH` window, 256 L1 blocks or about 51 minutes, and its timestamp is at most an hour behind L1 time. This plays the role of the OP stack's [sequencing window](https://specs.optimism.io/glossary.html#sequencing-window), and bounds the proving latency, while users only wait for the preconfirmation.
3. **L1 reorgs.** If L1 reorgs the anchor block out, the blocks anchored to it cannot be posted. The sequencer anchors a few slots behind the L1 head, trading deposit latency for safety.
4. **L1 forks.** The [EIP-8357](./evm_vk_registry.md) registry switches the verification key at an L1 fork, after which the contract rejects blocks proven under the previous key, so the sequencer posts its preconfirmed blocks before the fork.
5. **Forced transactions.** The block's anchor, not the time of posting, fixes which forced transactions it must include (see [L2 FOCIL](./forced_transactions.md#l2-focil)).

## Slashing

The sequencer bonds collateral in the rollup contract, which slashes all of it for:

1. **Equivocation**: two signatures for the same height with different hashes.
2. **Divergence**: a signature for block `N` with hash `X`, while the rollup's block `N` has another hash.

Divergence includes a preconfirmed block that was not posted before its deadline, whether the sequencer let it expire on purpose or failed to post it in time: with about 51 minutes to post, the sequencer carries the risk of L1 congestion and censorship. The only exception is a block whose anchor an L1 reorg removed, which L1's [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935) history contract shows for 8,191 blocks.

The rollup contract only stores the hash of its latest block, but the L2's own EIP-2935 history contract keeps the hashes of the last 8,191 L2 blocks in L2 state. A slasher proves the hash of block `N` with a storage proof against a recent state root, as [L2 to L1 messages](./messaging.md#l2-to-l1-messaging) are proven, which makes the history contract a requirement of the [genesis](./specification.md#genesis).

The reference implementation's [`SequencedNativeRollup`](https://github.com/l2beat/native-rollups/tree/main/contracts) implements this customization, and the demo runs it: its sequencer preconfirms each block when it builds it and posts it a few blocks later.

The OP stack could only enforce such a bond through its output roots, which include the latest L2 block hash but are [proposed](https://specs.optimism.io/protocol/proposals.html) for some blocks only and final after the dispute window. A native rollup posts and proves every block hash as it goes. The bond is only as credible as the contract that holds it: a native rollup follows L1 forks without upgrades, so its contract can be immutable.

## Partial blocks

[EIP-8288](./zkzkframes.md) allows one proof dependency per transaction, and EIP-8142 takes at least one blob per block, so frequent small blocks are expensive (see the [Specification's open questions](./specification.md#open-questions)). The sequencer can instead build longer blocks and preconfirm them as they grow, as the OP stack's [Flashblocks](https://specs.optimism.io/protocol/flashblocks.html) do: it signs each prefix of the block's transaction list together with the parent hash. A prefix and its parent fix the result of every transaction in it, so these are execution preconfirmations, and they are slashable: the header of the posted block, proven through the history contract, holds its transactions root, against which a slasher proves the transactions of the prefix. Intermediate state roots are not slashable, since the header only holds the final one.

## Open questions

- **Bond size.** A fault slashes the bond once, however many preconfirmations it breaks, so the bond caps the value that preconfirmations can protect at any time. Larger transfers wait for the block to be posted.
- **Liveness faults.** Slashing the whole bond for a block that expired by accident may deter sequencers. A smaller penalty for blocks posted after their deadline, as [based preconfirmations](https://ethresear.ch/t/based-preconfirmations/17353) give liveness faults, would need the contract to record when each block was posted, and must still exceed what a sequencer gains by letting a block expire on purpose.
- **Recipients.** Whether the slashed bond pays the users holding broken preconfirmations or whoever proves the fault is open.
