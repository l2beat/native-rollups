# Proofs

> ⚠️ This is a heavy work in progress. Details are likely to change as the ZK L1 upgrade effort progresses.
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Problem statement](#problem-statement)
- [Background](#background)
- [Non-enshrined vs enshrined proofs](#non-enshrined-vs-enshrined-proofs)
- [Range proofs](#range-proofs)
- [Proof-carrying transactions](#proof-carrying-transactions)
- [Recursive L1+L2 proofs](#recursive-l1l2-proofs)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Problem statement

The ZK version of the `EXECUTE` precompile needs to provide a ZK proof for nodes to verify that its execution was correct. The exact mechanism used should resemble the way that proofs are provided and verified for L1 blocks. Some information on the current effort for the ZK L1 upgrade can be found [here](https://zkevm.fyi/introduction.html).

## Background

For L1, the ZK current interface candidate looks as follows:

- **Verifier**: takes in input the blockhash, parenthash and boolean saying whether the STF is valid or not. The blockhash already contains the parenthash, but we need to check whether the one we already have matches with the new block being validated.
- **Prover**: takes a block and an execution witness. The execution witness is computed during payload execution and it is made up of the all required state trie node preimages required for execution, list of all contract code preimages required for execution, all account and storage key preimages required for execution, and the state root of the previous block header which contains the pre-state and the parent header info required for validation.

Given that the inputs reflect those of the `state_transition` function, if the `state_transition` [variant](./execute_precompile.md#state_transition_variant) is chosen, the same verification keys can be used both for L1 blocks and for native rollup blocks.

## Non-enshrined vs enshrined proofs

It is expected that the first version of the ZK L1 upgrade will not enshrine any particular quorum of proof systems, but nodes will be able to choose which proof system to use. This means that every computation that gets ZK proven needs to somehow make its witness available for arbitrary prover nodes to pick it up and generate proofs. As a consequence, the `EXECUTE` precompile is forced to check for availability of transaction commitments, i.e. of blobs. For this reason, calls to the precompile should be able to directly reference a blob, rather than just pass an arbitrary commitment, which is the way existing rollup verifiers work.

If enough confidence is gained in the proof systems being used, a second version might enshrine a quorum of fixed proof systems. In this case, as long as some computation provides a proof, nodes will be able to verify it, regardless of whether the witness is available or not. In this scenario, it will be possible to support native alt-DA L2s too.

## Range proofs

While ideally the `EXECUTE` precompile would reuse the same verification keys as those used for L1 blocks, the downside is that a precompile call would only be able to verify one block at a time. If L1 provides a verification key that is able to verify multiple blocks within a single proof, then projects would not be forced to perform one `EXECUTE` call per block.

## Proof-carrying transactions

Since calls to the precompile need to provide a proof, and we don't want the proof to be sent onchain, at least with the first version of the ZK L1 upgrade, we introduce a new [EIP-2718](https://eips.ethereum.org/EIPS/eip-2718) transaction type, "proof-carrying transaction", where the `TransactionType` is `PROOF_TX_TYPE` and the `TransactionPayload` is the RLP serialization of the following `TransactionPayloadBody`: 

```
[chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit, to, value, data, access_list, y_parity, r, s]
```
similarly to [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844), proof-carrying transactions have two network representations. During transaction gossip responses (`PooledTransactions`), the EIP-2718 `TransactionPayload` of the proof-carrying transaction is wrapped to become:

```
rlp([tx_payload_body, proofs])
```

Proofs are validated on the consensus layer, similarly to how they'll be validated for L1 blocks. Multiple proofs might be needed to convince enough validators, since each one of them might be subscribed to receive proofs from different proof systems.

## Recursive L1+L2 proofs

There are two strategies that can be employed to verify L1 and `EXECUTE` precompile proofs:

1. **Separate proofs**: the L1 block proof covers everything, including the `EXECUTE` call, up until the the point where the `state_transition` function is recursively called. In this case, the node will required to verify an additional proof separetely.
2. **Recursive proofs**: the L1 `state_transition` proof and the L2 `state_transition` proof(s) are combined together in a single proof. This might introduce additional latency and complexity, but reduces the cost of verification for each block.
