# The EXECUTE precompile

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Re-execution vs ZK enforcement](#re-execution-vs-zk-enforcement)
- [(WIP) Specification](#wip-specification)
- [(WIP) Usage example](#wip-usage-example)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Re-execution vs ZK enforcement

The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) for the `EXECUTE` precompile presented two possible enforcement mechanisms: re-execution and ZK proofs. While the latter requires the L1 ZK-EVM upgrade to take place, the former can potentially be implemented beforehand and set the stage for the ZK version, in a similar way that proto-danksharding was first introduced without PeerDAS.

The re-execution variant would only be able to support optimistic rollups with a bisection protocol that goes down to single or few-L2-blocks sized steps, and that are EVM-equivalent. Today there are three stacks with working bisection protocols, namely Orbit stack (Arbitrum), OP stack (Optimism) and Cartesi. Cartesi is built to run a Linux VM so they wouldn't be able to use the precompile, and Orbit supports [Stylus](https://arbitrum.io/stylus) which doesn't make them fully EVM-equivalent, unless a Stylus-less version is implemented, but even in this case it wouldn't be able to support Arbitrum One. OP stack is mostly EVM-equivalent, but still requires heavy modifications to support native execution. It's therefore unclear whether trying to implement the re-execution version of the precompile is worth it, or if it's better to wait for the more powerful ZK version.

While L1 ZK-EVM is not needed for the re-execution version, statelessness is, as we want L1 validators to be able to verify the precompile without having to hold all rollups' state. It's not clear whether the time interval between statelessness and L1 ZK-EVM will be long enough to justify the implementation of the re-execution variant.

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
    prev_randao = ... buffer_read(...)
    excess_blob_gas = ... buffer_read(...)
	transactions = ... buffer_read(...) # TBD: this should be a ref to blobs
    l1_anchor = ... buffer_read(...) # TBD: arbitrary info that is passed from L1 to L2 storage

    # Disable blob-carrying transactions
    for tx in map(decode_transaction, transactions):
        if isinstance(tx, BlobTransaction):
            raise ExecuteError
	
	block_env = vm.BlockEnvironment(
		chain_id=chain_id,
		state=state,
		block_gas_limit=block_gas_limit,
		block_hashes=..., # TBD: depends how it will look like post-7709
		coinbase=coinbase,
		number=number, # TBD: they probably need to be strictly sequential
		base_fee_per_gas=..., # TBD
		time=..., # TBD: depends if we want to use sequencing or proving time 
		prev_randao=prev_randao # NOTE: assigning `evm.message.block_env.prev_randao` prevents ahead-of-time sequencing
		excess_blob_gas=excess_blob_gas, # TODO: consider proposals where blob and calldata gas is merged for L2 pricing
		parent_beacon_block_root=... # TBD

    # Handle L1 anchoring
    process_unchecked_system_transaction( # TODO: consider unchecked vs checked
        block_env=block_env,
        target_address=L1_ANCHOR_ADDRESS, # TBD: exact predeploy address + implementation
        data=l1_anchor # TBD: exact format
    )

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

The following example shows how an ahead-of-time sequenced rollup can use the `EXECUTE` precompile to settle blocks.

```solidity
contract Rollup {
	uint64 public constant chainId = 1234321;
	uint public gasLimit;
	
	// latest settled state
	bytes32 public state;
	
	// receipts of latest settled block
	bytes32 public receipts;
	
	// block number to be sequenced next
	uint public nextBlockNumberToSequence;

    // block number to be settled next
    uint public nextBlockNumberToSettle;
	
	// blob refs store (L2 block number, (L1 block number, blob hash))
	mapping(uint => (uint, bytes32)) public blocks;
	
    // assumes that one blob is one block
	function sequence(uint blobIndex) public {
		blocks[nextBlockNumberToSequence] = (block.number, blobhash(blobIndex));
        nextBlockNumberToSequence++;
	}
	
	function settle(
		bytes32 _newState,
		bytes32 _receipts,
	) public {
        (uint l1AnchorBlock, _) = blocks[nextBlockNumberToSettle];

		EXECUTE(
			chainId,
			_nextBlockNumberToSettle,
            l1AnchorBlock,
			state,
			_newState,
			_receipts,
			gasLimit,
			msg.sender,
			blocks[l2BlockNumber] // TBD: unclear how to reference past blobs at this point
		)

		state = _newState;
		receipts = _receipts;
        nextBlockNumberToSettle++;
	}
}

```
