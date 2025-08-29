# Forced transactions (WIP)

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Overview](#overview)
- [Proposed designs](#proposed-designs)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Overview

Rollups with centralized sequencers must implement a forced transaction mechanism if they wish to preserve L1 censorship resistance properties. Users need to be able to permissionlessly send transactions to L1 and have the guarantee that they will be eventually included on L2 if they are accepted on L1.

Fundamentally, proposed forced transactions do not need to include an L2 transaction signature because they can be authenticated using the L1 transaction signature, assuming that they are supposed to be the same address on L1 and L2. In existing implementations, forced transactions are usually pushed through calldata on L1, given that using a blob would leave most of the space unused and might therefore be cost-inefficient. Forced transaction mechanisms should be designed not to interfere with centralized preconfirmations where possible.

## Brainstorming
> ⚠️
> The following are just examples to demonstrate that forced transaction mechanisms are compatible with native rollups and that their implementations isn't unrealistic. Projects are free to design their own mechanisms and the precompile aims to be flexible enough to accommodate them.

The `EXECUTE` precompile can only support transactions with signatures, so forced transactions must include a signature too. On L1, one exception is made for withdrawals from the beacon chain, which are not authenticated on the execution layer. The limitation of withdrawals is that they only cover ETH minting and they cannot be used as a replacement for general message passing.

One approach consists in having a mechanism to detect whether sequenced blocks contain individual forced transactions from a queue on L1 that are older than a certain time threshold, and if not, revert the block submission. It's unclear whether proving inclusion of arbitrary bytes in blobs is feasible to be done on L1 or if it requires a dedicated ZK verifier. This design alone doesn't solve for a sequencer that is not only censoring but is completely offline, and therefore an additional fallback mechanism that removes the sequencer whitelist might be needed.

Another solution is to allow the `EXECUTE` precompile not only to reference blobs, but also storage or calldata. In this way users can save their forced txs in storage and the contract around the `EXECUTE` calls can force the `transactions` input to be read from that storage if forced txs older than a certain time threshold are present.

## FOCIL (EIP-7805)

[FOCIL](https://eips.ethereum.org/EIPS/eip-7805) introduces a new `inclusion_list_transactions` parameter to the `state_transition` function and the `apply_body` function, that conditions the validity of the block with a `validate_inclusion_list_transactions` function. In particular, it is checked that block transactions include all valid transactions from the IL that can fit in the block. The execution spec diff on top of osaka can be found [here](https://github.com/ethereum/execution-specs/pull/1349/files).

Native rollups can re-use such logic where the IL comes from a smart contract as opposed to the CL. Custom logic can be applied that act as an additional filter, or that add delays to preserve the validity of already issued preconfirmations. The IL would therefore become another input to the `EXECUTE` precompile. In this case, FOCIL inclusion on L1 becomes an obvious tech dependency.
