# L2 fee market

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Pricing](#pricing)
- [Fee collection](#fee-collection)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Fee collection

The `EXECUTE` precompile exposes the `coinbase` address as an input parameter so that projects can decide by themselves how to collect priority fees on the L2.

While L1 burns the base fee, most L2s in production today decide to redirect it to a dedicated address. This is not possible to be supported by native rollups out of the box, and requires additional changes to the L1 protocol.

One proposal consists in exposing in the `block_output` the cumulative fee that is burned in the block, both by the `effective_gas_fee` and the `blob_gas_fee`. We highlight in `green` the two additional lines that need to be added to the `BlockOutput` class and to the `process_transaction` function to support this feature.

```diff
+++ vm/__init__.py
class BlockOutput:
    block_gas_used
    transactions_trie
    receipts_trie
    receipt_keys
    block_logs
    withdrawals_trie
    blob_gas_used
    requests
+   burned_fees
```


```diff
+++ fork.py
def process_transaction(​block_env: ethereum.osaka.vm.BlockEnvironment, ​​block_output: ethereum.osaka.vm.BlockOutput, ​​tx: Transaction, ​​index: Uint​) -> None:
    """
    Execute a transaction against the provided environment.
    This function processes the actions needed to execute a transaction.
    It decrements the sender's account after calculating the gas fee and
    refunds them the proper amount after execution. Calling contracts,
    deploying code, and incrementing nonces are all examples of actions that
    happen within this function or from a call made within this function.
    Accounts that are marked for deletion are processed and destroyed after
    execution.
    Parameters
    ----------
    block_env :
        Environment for the Ethereum Virtual Machine.
    block_output :
        The block output for the current block.
    tx :
        Transaction to execute.
    index:
        Index of the transaction in the block.
    """
    trie_set(
        block_output.transactions_trie,
        rlp.encode(index),
        encode_transaction(tx),
    )
    intrinsic_gas, calldata_floor_gas_cost = validate_transaction(tx)
    (
        sender,
        effective_gas_price,
        blob_versioned_hashes,
        tx_blob_gas_used,
    ) = check_transaction(
        block_env=block_env,
        block_output=block_output,
        tx=tx,
    )
    sender_account = get_account(block_env.state, sender)
    if isinstance(tx, BlobTransaction):
        blob_gas_fee = calculate_data_fee(block_env.excess_blob_gas, tx)
    else:
        blob_gas_fee = Uint(0)
    effective_gas_fee = tx.gas * effective_gas_price
    gas = tx.gas - intrinsic_gas
    increment_nonce(block_env.state, sender)
    sender_balance_after_gas_fee = (
        Uint(sender_account.balance) - effective_gas_fee - blob_gas_fee
    )
    set_account_balance(
        block_env.state, sender, U256(sender_balance_after_gas_fee)
    )
    access_list_addresses = set()
    access_list_storage_keys = set()
    access_list_addresses.add(block_env.coinbase)
    if isinstance(
        tx,
        (
            AccessListTransaction,
            FeeMarketTransaction,
            BlobTransaction,
            SetCodeTransaction,
        ),
    ):
        for access in tx.access_list:
            access_list_addresses.add(access.account)
            for slot in access.slots:
                access_list_storage_keys.add((access.account, slot))
    authorizations: Tuple[Authorization, ...] = ()
    if isinstance(tx, SetCodeTransaction):
        authorizations = tx.authorizations
    tx_env = vm.TransactionEnvironment(
        origin=sender,
        gas_price=effective_gas_price,
        gas=gas,
        access_list_addresses=access_list_addresses,
        access_list_storage_keys=access_list_storage_keys,
        transient_storage=TransientStorage(),
        blob_versioned_hashes=blob_versioned_hashes,
        authorizations=authorizations,
        index_in_block=index,
        tx_hash=get_transaction_hash(encode_transaction(tx)),
    )
    message = prepare_message(block_env, tx_env, tx)
    tx_output = process_message_call(message)
    # For EIP-7623 we first calculate the execution_gas_used, which includes
    # the execution gas refund.
    tx_gas_used_before_refund = tx.gas - tx_output.gas_left
    tx_gas_refund = min(
        tx_gas_used_before_refund // Uint(5), Uint(tx_output.refund_counter)
    )
    tx_gas_used_after_refund = tx_gas_used_before_refund - tx_gas_refund
    # Transactions with less execution_gas_used than the floor pay at the
    # floor cost.
    tx_gas_used_after_refund = max(
        tx_gas_used_after_refund, calldata_floor_gas_cost
    )
    tx_gas_left = tx.gas - tx_gas_used_after_refund
    gas_refund_amount = tx_gas_left * effective_gas_price
    # For non-1559 transactions effective_gas_price == tx.gas_price
    priority_fee_per_gas = effective_gas_price - block_env.base_fee_per_gas
    transaction_fee = tx_gas_used_after_refund * priority_fee_per_gas
    # refund gas
    sender_balance_after_refund = get_account(
        block_env.state, sender
    ).balance + U256(gas_refund_amount)
    set_account_balance(block_env.state, sender, sender_balance_after_refund)
    # transfer miner fees
    coinbase_balance_after_mining_fee = get_account(
        block_env.state, block_env.coinbase
    ).balance + U256(transaction_fee)
    if coinbase_balance_after_mining_fee != 0:
        set_account_balance(
            block_env.state,
            block_env.coinbase,
            coinbase_balance_after_mining_fee,
        )
    elif account_exists_and_is_empty(block_env.state, block_env.coinbase):
        destroy_account(block_env.state, block_env.coinbase)
    for address in tx_output.accounts_to_delete:
        destroy_account(block_env.state, address)
    block_output.block_gas_used += tx_gas_used_after_refund
    block_output.blob_gas_used += tx_blob_gas_used
+   block_output.burned_fees += 
+       effective_gas_fee - gas_refund_amount - transaction_fee + blob_gas_fee
    receipt = make_receipt(
        tx, tx_output.error, block_output.block_gas_used, tx_output.logs
    )
    receipt_key = rlp.encode(Uint(index))
    block_output.receipt_keys += (receipt_key,)
    trie_set(
        block_output.receipts_trie,
        receipt_key,
        receipt,
    )
    block_output.block_logs += tx_output.logs
```

This value can be exposed as an output of the `EXECUTE` precompile for projects to decide how to handle it. For example, for those projects whose gas token is bridged through L1, the L1 bridge can decide to credit the burned fees to a dedicated address.

## Pricing

WIP.

To be discussed:
- handling of DA costs in the L2 transactions fee market.
