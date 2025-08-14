# Gas token deposits
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Overview](#overview)
- [Current approaches](#current-approaches)
  - [OP stack](#op-stack)
  - [Linea](#linea)
  - [Taiko](#taiko)
- [Other approaches](#other-approaches)
  - [Manual state manipulation](#manual-state-manipulation)
  - [Beacon chain withdrawals](#beacon-chain-withdrawals)
- [Proposed design](#proposed-design)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Overview

Rollup users need a way to obtain the gas token to be able to send transactions on the L2. Existing solutions divide into two approaches: either an escrow contract contains preminted tokens that are unlocked through the [L2 to L1 messaging](l2_l1_messaging.md) channel, or a new transaction type that is able to mint the gas token is added to the STF. This page will also discuss two more approaches that are currently not used in any project.

## Current approaches

### OP stack

The custom "deposited transaction" type allows to mint the gas token based on `TransactionDeposited` event fields. On L2, the gas token magically appears in the user's balance.

### Linea

WIP

### Taiko

WIP

## Other approaches

### Manual state manipulation

WIP

### Beacon chain withdrawals

WIP

## Proposed design

WIP
