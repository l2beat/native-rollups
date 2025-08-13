# L1 to L2 messaging

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [L1 to L2 messaging](#l1-to-l2-messaging)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Current approaches
L1 to L2 messaging systems are built on top of the L1 anchoring mechanism. We first discuss how some existing rollups handle L1 to L2 messaging to better understand how similar mechanisms can be implemented on top of the [L1 anchoring mechanism](l1_anchoring.md) proposed here for native rollups.

### OP stack

There are two ways to send messages from L1 to L2, either by using the low-level API of deposited transactions, or by using the high-level API of the "Cross Domain Messenger" contracts, which are built on top of the low-level API.

Deposited transactions are derived from `TransactionDeposited` events emitted in the `OptimismPortal` contract on L1. Deposited transactions are a new transaction type with prefix `0x7E` that have been added in the OP stack STF, which are fully derived on L1, they cannot be sent to L2 directly, and do not contain signatures as the authentication is already performed on L1. The deposited transaction on the L2 specifies the `tx.origin` and the `msg.sender` as the `msg.sender` of the transaction on L1 that emitted the `TransactionDeposited` event if EOA, if not, the aliased `msg.sender` is used to prevent conflicts with L2 contracts that might have the same address. Moreover the function mints L2 gas tokens based on the value that is sent on L1.

The Cross Domain Messengers are contracts built on top of this mechanism. The `sendMessage` function on L1 calls `OptimismPortal.depositTransaction`, and will therefore be the (aliased) `msg.sender` on the L2 side. The actual caller of the `sendMessage` function is passed as opaque bytes to be later unpacked. On L2, the corresponding Cross Domain Messenger contract receives a call to the `relayMessage` function, which checks that the `msg.sender` is the aliased L1 Cross Domain Messenger. A special `xDomainMsgSender` storage variable saves the actual L1 cross domain caller, and finally executes the call. The application on the other side will then be able to access the `xDomainMsgSender` variable to know who sent the message, and the `msg.sender` will be the Cross Domain Messenger contract on L2.

It's important to note that such messaging mechanism is completely disconnected from the onchain L1 anchoring mechanism that saves the L1 block information in the L2 `L1Block` contract, as it is fully handled by the derivation logic.
