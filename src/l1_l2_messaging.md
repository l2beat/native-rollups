# L1 to L2 messaging

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
L1 to L2 messaging systems are built on top of the L1 anchoring mechanism. We first discuss how some existing rollups handle L1 to L2 messaging to better understand how similar mechanisms can be implemented on top of the [L1 anchoring mechanism](l1_anchoring.md#proposed-design) proposed here for native rollups.

### OP stack

There are two ways to send messages from L1 to L2, either by using the low-level API of deposited transactions, or by using the high-level API of the "Cross Domain Messenger" contracts, which are built on top of the low-level API.

Deposited transactions are derived from `TransactionDeposited` events emitted in the `OptimismPortal` contract on L1. Deposited transactions are a new transaction type with prefix `0x7E` that have been added in the OP stack STF, which are fully derived on L1, they cannot be sent to L2 directly, and do not contain signatures as the authentication is already performed on L1. The deposited transaction on the L2 specifies the `tx.origin` and the `msg.sender` as the `msg.sender` of the transaction on L1 that emitted the `TransactionDeposited` event if EOA, if not, the aliased `msg.sender` is used to prevent conflicts with L2 contracts that might have the same address. Moreover the function mints L2 gas tokens based on the value that is sent on L1.

The Cross Domain Messengers are contracts built on top of this mechanism. The `sendMessage` function on L1 calls `OptimismPortal.depositTransaction`, and will therefore be the (aliased) `msg.sender` on the L2 side. The actual caller of the `sendMessage` function is passed as opaque bytes to be later unpacked. On L2, the corresponding Cross Domain Messenger contract receives a call to the `relayMessage` function, which checks that the `msg.sender` is the aliased L1 Cross Domain Messenger. A special `xDomainMsgSender` storage variable saves the actual L1 cross domain caller, and finally executes the call. The application on the other side will then be able to access the `xDomainMsgSender` variable to know who sent the message, and the `msg.sender` will be the Cross Domain Messenger contract on L2. If the sender on L1 was a contract, the address is not and doesn't need to be aliased as checking the `xDomainMsgSender` already scopes callers to just L1 callers and no conflict with L2 contracts can happen.

It's important to note that such messaging mechanism is completely disconnected from the onchain [L1 anchoring mechanism](l1_anchoring.md#op-stack) that saves the L1 block information in the L2 `L1Block` contract, as it is fully handled by the derivation logic.

### Linea

The `sendMessage` function is called on the `LineaRollup` contract on L1, also identified as the "message service" contract by others. A numbered "rolling hash" is saved in a mapping with the content of the message to be sent on L2. During [Linea's anchoring process](l1_anchoring.md#linea), such rolling hash is relayed on the L2 together with all the message hashes that make up the rolling hashes that are then saved in the `inboxL1L2MessageStatus` mapping. The message is finally executed by calling the `claimMessage` function on the `L2MessageService`, which references the message status mapping. The destination contract can call the `sender()` function on the `L2MessageService` to check who was the original sender of the message on L1. The value is set only for the duration of the call and is reset to default values after the call returns. If the sender on L1 was a contract, the address is not and doesn't need to be aliased as checking the `sender()` already scopes callers to just L1 callers and no conflict with L2 contracts can happen.

### Taiko

To send a message from L1 to L2, the `sendSignal` function is called on the `SignalService` contract on L1, which stores message hashes in its storage at slots computed based on the message itself. On the L2 side, after [anchoring](l1_anchoring.md#taiko) of the L1 block state root, the `proveSignalReceived` function is called on the `SignalService` L2 contract, with complex merkle proofs that unpack the so-passed state root and gets to the message hashes saved in storage of the L1 `SignalService` contract.

A higher-level `Bridge` contract is deployed on L1 that performs the actuall contract call  through the `processMessage` function given the informations received by the `SignalService` L2 contract. The destination contract can call the `context()` function on the `Bridge` to check what was the origin chain and the origin sender of the message. The `context()` is set only for the duration of the call and it is reset to default values after the call returns. If the sender on L1 was a contract, the address is not and doesn't need to be aliased as checking the `context()` already scopes callers to just L1 callers and no conflict with L2 contracts can happen.

### Orbit stack

Messages are sent from L1 to L2 by enqueuing "delayed messages" on the `Bridge` contract using authorized `Inbox` contracts. Those messages can have different "kinds" based on their purposes:

```solidity
uint8 constant L2_MSG = 3;
uint8 constant L1MessageType_L2FundedByL1 = 7;
uint8 constant L1MessageType_submitRetryableTx = 9;
uint8 constant L1MessageType_ethDeposit = 12;
uint8 constant L1MessageType_batchPostingReport = 13;
uint8 constant L2MessageType_unsignedEOATx = 0;
uint8 constant L2MessageType_unsignedContractTx = 1;
```

Those message types will then internally correspond to different transaction types, as already listed in [Orbit's L1 Anchoring section](./l1_anchoring.md#orbit-stack). For those messages to be included on the L2, the permissioned sequencer needs to either explicitly include them in an L2 block, or if they are not processed within some time they can be forced included by the user. On the L2, transactions magically appear and have no signature, without the need to explicitly claim them, and have the proper `msg.sender` from L1, which is aliased if the sender on L1 is a contract.

## Proposed design

Designs can be classified into two categories, those that support L1 to L2 messages with the proper `msg.sender` on the L2, and those that don't. Using the proper L1 `msg.sender` (aliased if the sender is a contract) for the L2 transaction has the advantage that many contracts don't need to be modified to explicitly support L1 to L2 messages, as access control works in the usual way by checking the `msg.sender`. The downside is that this requires the addition of a new transaction type without signature, that needs to be scoped for native rollups usage only and prohibited on L1. 

Following the [design principles](./execute_precompile.md#design-principles), and the fact that existing projects can already handle L1 to L2 messaging without an additional transaction type, it is preferred not to add a new transaction type. The downside is that now contracts need to be explicitly modified to support the L1 to L2 message interface for crosschain message authentication. Many projects already do this, and effort can be made to standardize the interface across projects.
