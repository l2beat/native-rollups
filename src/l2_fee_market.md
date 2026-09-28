# L2 fee market

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Fee collection](#fee-collection)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Native rollups run L1's fee market unchanged: EIP-1559 base fees, priority fees, and the same gas schedule.

## Fee collection

Priority fees go to the block's `fee_recipient`, an unconstrained field of the payload (see [Specification](./specification.md#executionpayload)). The rollup contract can take it from the operator or fix it, for example to a DAO treasury.

The base fee is burned, as on L1. Most L2s in production instead redirect it to a dedicated address, which native rollups cannot do without changing the program. One proposal is for the program to track the fees it burns in each block, both the base fee and the blob fee:

```python
block_output.burned_fees += (
    effective_gas_fee - gas_refund_amount - transaction_fee + blob_gas_fee
)
```

and to expose the total in the proof's public output. The rollup contract could then act on it; for example, when the gas token is bridged from L1, it could release the burned amount from the L1 escrow to a dedicated address. This is an L1 change: every L1 block proof would expose the value too.

## Open questions

- **Data availability costs**: L2 transactions cannot pay an explicit L1 data fee, as on OP stack chains, since that would change the program. How operators recover the cost of posting blobs, for example through priority fees, is open.
