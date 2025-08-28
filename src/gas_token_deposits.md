# Gas token deposits
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Overview](#overview)
- [Current approaches](#current-approaches)
  - [OP stack](#op-stack)
  - [Linea](#linea)
  - [Taiko](#taiko)
  - [Orbit stack](#orbit-stack)
- [Other approaches](#other-approaches)
  - [Manual state manipulation](#manual-state-manipulation)
  - [Beacon chain withdrawals](#beacon-chain-withdrawals)
- [Proposed design](#proposed-design)
  - [The first deposit problem](#the-first-deposit-problem)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Overview

Rollup users need a way to obtain the gas token to be able to send transactions on the L2. Existing solutions divide into two approaches: either an escrow contract contains preminted tokens that are unlocked through the [L2 to L1 messaging](l2_l1_messaging.md) channel, or a new transaction type that is able to mint the gas token is added to the STF. This page will also discuss two more approaches that are currently not used in any project.

## Current approaches

### OP stack

The custom `DepositTransaction` type allows to mint the gas token based on `TransactionDeposited` event fields. On L2, the gas token magically appears in the user's balance.

### Linea

Linea uses [preminted tokens](https://lineascan.build/address/0x508Ca82Df566dCD1B0DE8296e70a96332cD644ec) in the `L2MessageService` contract, which are then unlocked when L1 to L2 messages are processed on the L2. No new transaction type that can mint gas token is added to the STF.

### Taiko

Taiko uses [preminted tokens](https://taikoscan.io/address/0x1670000000000000000000000000000000000001) in the L2 `Bridge` contract, which are then unlocked when L1 to L2 messages are processed on the L2. No new transaction type that can mint gas token is added to the STF.

### Orbit stack

Orbit stack uses a custom transaction type that is able to mint the gas token based on the `ArbitrumDepositTx` type. On L2, the gas token magically appears in the user's balance.

## Other approaches

### Manual state manipulation

Before (and after) calling the `EXECUTE` precompile, projects are free to modify the L2 state root directly with custom execution, including dedicated proving systems. This can be used to touch balances, but it requires doing all updates either before or after block execution. This strategy cannot be used to support arbitrary intra-block gas-token deposits.

### Beacon chain withdrawals

Another possible mechanism is to use the beacon chain withdrawal mechanisms which mints the gas token on L1. Withdrawals are processed at the end of a block, so they wouldn't be able to allow deposits to be processed intra-block. As of now, no existing project uses beacon chain withdrawals for gas token deposits, but the mechanism can be left open for use.

## Proposed design

Following the [design principles](./execute_precompile.md#design-principles), it is preferred not to add a new transaction type that can mint gas tokens, as existing projects already handle gas token deposits through other means. The preferred approach is to use a predeployed contract that contains preminted tokens, which are then unlocked when L1 to L2 messages are processed on the L2. This design fully supports custom gas tokens as it is not opinionated on what type of message unlocks the gas token on L2, it being ETH, an ERC20, NFTs, or mining mechanisms.

### The first deposit problem

WIP.

To be discussed: 
- How can a user claim on L2 the first deposit if they don't have any gas token to pay for the transaction fees?
