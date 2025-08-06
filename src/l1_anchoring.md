# L1 Anchoring
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [L1 Anchoring](#l1-anchoring)
  - [Overview](#overview)
  - [Current approaches](#current-approaches)
    - [OP stack](#op-stack)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Overview
To allow messaging from L1 to L2, a rollup needs to be able to obtain some information from the L1 chain, with the most general information being an L1 block hash.

## Current approaches

We first discuss how existing rollups handle the L1 anchoring problem to better inform the design of the `EXECUTE` precompile.

### OP stack
[[spec](https://specs.optimism.io/protocol/deposits.html#l1-attributes-predeployed-contract)]
A special `L1Block` contract is predeployed on L2 which processes "L1 attributes deposited transactions" during derivation. The contract stores L1 information such as the latest L1 block number, hash, timestamp, and base fee. A deposited transaction is a custom transaction type that is derived from the L1, does not include a signature and does not consume L2 gas.

It's important to note that reception of L1 to L2 messages on the L2 side does not depend on this contract, but rather on "user-deposited transactions" that are derived from events emitted on L1, which again are implemented through the custom transaction type.

### Linea

WIP

