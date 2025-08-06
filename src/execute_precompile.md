# The EXECUTE precompile

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [(WIP) Specification](#wip-specification)
- [(WIP) Usage example](#wip-usage-example)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## (WIP) Specification

```python
def execute(evm: Evm) -> None:
	data = evm.message.data
	...
	charge_gas(...) # TBD
	...
    # Inputs
	chain_id = ... buffer_read(...) # likely hard-coded in a contract
	number = ... buffer_read(...)
	pre_state = ... buffer_read(...)
	post_state = ... buffer_read(...)
	post_receipts = ... buffer_read(...) # TODO: consider for L2->L1 msgs
	block_gas_limit = ... buffer_read(...) # TBD: depends on ZK gas handling
	coinbase = ... buffer_read(...)
	transactions = ... buffer_read(...) # TBD: this should be a ref to blobs
	
	block_env = vm.BlockEnvironment(
		chain_id=chain_id,
		state=state,
		block_gas_limit=block_gas_limit,
		block_hashes=..., # TBD: depends how it will look like post-7709
		coinbase=coinbase,
		number=number, # TBD: they probably need to be strictly sequential
		base_fee_per_gas=..., # TBD
		time=..., # TBD: depends if we want to use sequencing or proving time
		prev_randao=evm.message.block_env.prev_randao,
		excess_blob_gas=evm.message.block_env.excess_blob_gas,
		parent_beacon_block_root=... # TBD
	...
	# TODO: decide what to do things that are not valid on a rollup, e.g. blobs
	block_output = apply_body(
		block_env=block_env,
		transactions=transactions,
		withdrawals=() # TODO: consider using this for deposits
	)
	# NOTE: some things might look different with statelessness
	block_state_root = state_root(block_env.state)
	receipt_root = root(block_output.receipts_trie)
	# TODO: consider using requests_hash for withdrawals
	# TODO: consider adding a gas_used top-level param
	
	if block_state_root != post_state:
		raise ExecuteError # TODO: check if this is the proper way to handle errs
	if receipt_root != post_receipts
		raise ExecuteError
	
	evm.output = ... # TBD: maybe block_gas_used?

```

## (WIP) Usage example

The following example shows how an ahead of time sequenced rollup can use the `EXECUTE` precompile to settle blocks.

```solidity
contract Rollup {
	uint64 public constant chainId = 1234321;
	uint public gasLimit;
	
	// latest settled state
	bytes32 public state;
	
	// receipts of latest settled block
	bytes32 public receipts;
	
	// block number to be sequenced next
	uint public nextBlockNumber;
	
	// blob refs store (L2 block number, (L1 block number, blob hash))
	mapping(uint => (uint, bytes32) public blocks;
	
	function sequence(uint blobIndex) public {
		blocks[nextBlockNumber] = (block.number, blobhash(blobIndex));
	}
	
	function settle(
		bytes32 _newState,
		bytes32 _receipts,
		uint l2BlockNumber
	) public {
		EXECUTE(
			chainId,
			l2BlockNumber,
			state,
			_newState,
			_receipts,
			gasLimit,
			msg.sender,
			blocks[l2BlockNumber] // TBD
		)

		state = _newState;
		receipts = _receipts;
	}
}

```
