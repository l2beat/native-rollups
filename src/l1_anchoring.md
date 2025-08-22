# L1 Anchoring
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Overview](#overview)
- [Current approaches](#current-approaches)
  - [OP stack](#op-stack)
  - [Linea](#linea)
  - [Taiko](#taiko)
  - [Orbit stack](#orbit-stack)
- [Proposed design](#proposed-design)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Overview
To allow messaging from L1 to L2, a rollup needs to be able to obtain some information from the L1 chain, with the most general information being an L1 block hash. The process of placing a "cross-chain validity reference" is tipically called "anchoring". In practice, projects relay from L1 various types of information depending on their specific needs.

## Current approaches

We first discuss how some existing rollups handle the L1 anchoring problem to better inform the design of the `EXECUTE` precompile.

### OP stack
[[spec](https://specs.optimism.io/protocol/deposits.html#l1-attributes-predeployed-contract)]
A special `L1Block` contract is predeployed on L2 which processes "L1 attributes deposited transactions" during derivation. The contract stores L1 information such as the latest L1 block number, hash, timestamp, and base fee. A deposited transaction is a custom transaction type that is derived from the L1, does not include a signature and does not consume L2 gas.

It's important to note that reception of L1 to L2 messages on the L2 side does not depend on this contract, but rather on "user-deposited transactions" that are derived from events emitted on L1, which again are implemented through the custom transaction type.

### Linea

Linea, in their `L2MessageService` contract on L2, adds a function that allows a permissioned relayer to send information from L1 to L2:

```solidity
function anchorL1L2MessageHashes(
    bytes32[] calldata _messageHashes,
    uint256 _startingMessageNumber,
    uint256 _finalMessageNumber,
    bytes32 _finalRollingHash
) external whenTypeNotPaused(PauseType.GENERAL) onlyRole(L1_L2_MESSAGE_SETTER_ROLE)
```

The permissioned relayer is supposed to only relay rolling hashes that are associated with L1 blocks that are finalized. On L1, a wrapper around the STF checks that the "rolling hash" being relayed is correct, otherwise proof verificatoin fails. Since anchoring is done through regular transactions, the function is permissioned, otherwise any user could send a transaction with an invalid rolling hash, which would be accepted by the L2 but rejected during settlement. In other words, blocks containing invalid anchor transactions are not considered no-ops.

### Taiko
[[docs](https://github.com/taikoxyz/taiko-mono/blob/a36f99f1e820e52e12f97f804837c2828e941a41/packages/protocol/docs/how_taiko_proves_blocks.md#anchor-transactions)] An `anchorV3` function is implemented in the `TaikoAnchor` contract which allows a `GOLDEN_TOUCH_ADDRESS` to relay an L1 state root to L2. The private key of the `GOLDEN_TOUCH_ADDRESS` is publicly known, but the node guarantees that the first transaction is always an anchor transaction, and that other transactions present in the block revert.

```solidity
function anchorV3(
    uint64 _anchorBlockId,
    bytes32 _anchorStateRoot,
    uint32 _parentGasUsed,
    LibSharedData.BaseFeeConfig calldata _baseFeeConfig,
    bytes32[] calldata _signalSlots
)
    external
    nonZeroBytes32(_anchorStateRoot)
    nonZeroValue(_anchorBlockId)
    nonZeroValue(_baseFeeConfig.gasIssuancePerSecond)
    nonZeroValue(_baseFeeConfig.adjustmentQuotient)
    onlyGoldenTouch
    nonReentrant
```

Since proposing blocks in Taiko is untrusted, some additional checks are performed on the validity of anchor blocks, which are passed on L1. In particular, it is checked that the anchor block number is not more than 96 blocks in the past, that it is less than current block number, and that it is greater than the latest anchor block.

The validity of the `_anchorStateRoot` value is [explicitly checked](https://github.com/taikoxyz/taiko-mono/blob/56a28bb5b59510c9b708ed4222d5260f64d346c6/packages/protocol/docs/how_taiko_proves_blocks.md#signal-storage) by Taiko's proof system.

### Orbit stack

Orbit stack chains relay information from L1 to L2 per message, similarly to the OP stack. New transaction types without signatures are introduced which are derived and authenticated by L1. In particular, the following types are added:

```go
ArbitrumDepositTxType         = 0x64
ArbitrumUnsignedTxType        = 0x65
ArbitrumContractTxType        = 0x66
ArbitrumRetryTxType           = 0x68
ArbitrumSubmitRetryableTxType = 0x69
ArbitrumInternalTxType        = 0x6A
ArbitrumLegacyTxType          = 0x78
```

ArbOS handles the translation from [message types](./orbit_stack.md#l1-to-l2-messaging) to transaction types. For example, a `L1MessageType_L2FundedByL1` message generates two transactions, one with type `ArbitrumDepositTxType` for funding and a `ArbitrumUnsignedTxType` for the actual message.

As opposed to other chains, retryable messages are implemented as a new transaction type instead of being implemented within smart contract logic. There's no higher-level API that is recommended to be used for messaging. 

## Proposed design

An `L1_ANCHOR` system contract is predeployed on L2 that receives an arbitrary `bytes32` value from L1 to be saved in its storage. The contract is intended to be used for L1->L2 messaging without being tied to any specific format, as long it is encoded as a `bytes32` value. Validation of this value is left to the rollup contract on L1. The exact implementation of the contract is TBD, but [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935) can be used as a reference. A messaging system can be implemented on top of this by passing roots and providing proofs of inclusions on the L2. Such mechanisms are better discussed in [L1 to L2 messaging](./l1_l2_messaging.md).
