# L1 ZK-EVM tracker

Last update: Feb 25, 2026

## Blocks in blobs

- [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)

```py
def execution_payload_data_to_blobs(data: ExecutionPayloadData) -> List[Blob]:
    """
    Canonically encode the execution-payload data into an ordered list of blobs.

    Encoding steps:
      1. bal_bytes = data.blockAccessList
      2. transactions_bytes = RLP.encode(data.transactions)
      3. Create 8-byte header: [4 bytes BAL length][4 bytes tx length]
      4. payload_bytes = header + bal_bytes + transactions_bytes
      5. return bytes_to_blobs(payload_bytes)

    The first blob will contain (in order):
      - [4 bytes] BAL data length
      - [4 bytes] Transaction data length
      - [variable] BAL data (may span multiple blobs)
      - [variable] Transaction data (may span multiple blobs)

    This allows extracting just the BAL data without transactions.

    Note: blockAccessList is already RLP-encoded per EIP-7928. Transactions are RLP-encoded as a list.
    """
    bal_bytes = data.blockAccessList
    transactions_bytes = RLP.encode(data.transactions)

    # Create 8-byte header: [4 bytes BAL length][4 bytes tx length]
    bal_length = len(bal_bytes).to_bytes(4, 'little')
    txs_length = len(transactions_bytes).to_bytes(4, 'little')
    header = bal_length + txs_length

    # Combine header + data
    payload_bytes = header + bal_bytes + transactions_bytes

    return bytes_to_blobs(payload_bytes)
```

## Stateless validation
- [stateless_types.py](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_types.py#L28)
- [stateless.py](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L128)
- [execution_engine/types.py](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L30)

```py
@slotted_freezable
@dataclass
class ExecutionPayload:
    """
    Represent a new block to be processed by the execution layer.

    The consensus layer constructs this from a beacon block body and
    passes it to the execution engine for validation.

    Note: execution_request_hash is not a direct field in ExecutionPayload
    but it is indirectly committed to via `block_hash` since `request_hash`
    is in the EL-block header.
    """

    parent_hash: Hash32
    fee_recipient: Address
    state_root: Root
    receipts_root: Root
    logs_bloom: Bloom
    prev_randao: Bytes32
    block_number: Uint
    gas_limit: Uint
    gas_used: Uint
    timestamp: U256
    extra_data: Bytes
    base_fee_per_gas: Uint
    block_hash: Hash32
    transactions: Tuple[LegacyTransaction | Bytes, ...]
    withdrawals: Tuple[Withdrawal, ...]
    blob_gas_used: U64
    excess_blob_gas: U64
    block_access_list: Bytes
```

```py
@slotted_freezable
@dataclass
class NewPayloadRequest:
    """
    Contains an execution payload along with versioned hashes, the
    parent beacon block root, and execution requests for the
    ``verify_and_notify_new_payload`` entry point.

    This corresponds to the consensus-layer `NewPayloadRequest`
    container used for Engine API calls.

    [Bellatrix `NewPayloadRequest`]:
    https://ethereum.github.io/consensus-specs/specs/bellatrix/beacon-chain/#newpayloadrequest
    [Electra modified `NewPayloadRequest`]:
    https://ethereum.github.io/consensus-specs/specs/electra/beacon-chain/#modified-newpayloadrequest
    """

    execution_payload: ExecutionPayload
    versioned_hashes: Tuple[VersionedHash, ...]
    parent_beacon_block_root: Root
    execution_requests: ExecutionRequests
```

```py
@slotted_freezable
@dataclass
class ExecutionWitness:
    """
    Execution witness data for stateless validation.
    """

    state: Tuple[Bytes, ...]
    """
    Hashed trie-node preimages needed during execution and state-root
    recomputation.
    """

    codes: Tuple[Bytes, ...]
    """
    Contract-code preimages (created or accessed) needed during execution.
    """

    headers: Tuple[Bytes, ...]
    """
    RLP-encoded block headers used for pre-state and ``BLOCKHASH`` correctness
    proofs. This may trend toward empty EIP-7709.
    """
```


```py
@slotted_freezable
@dataclass
class StatelessInput:
    """
    Input to stateless validation.
    """

    new_payload_request: NewPayloadRequest
    """
    Consensus-layer payload request to validate statelessly. See
    ``execution_engine.NewPayloadRequest`` for structure and links to
    consensus-specs.
    """

    witness: ExecutionWitness
    """
    Execution witness material required to re-execute the core
    state transition function statelessly.
    """

    chain_config: ChainConfig
    """
    Chain configuration values needed during stateless validation.
    """

    public_keys: Tuple[Bytes, ...]
    """
    Recovered transaction public keys, in transaction order.
    """
```

```py
def verify_stateless_new_payload(
    stateless_input: StatelessInput,
) -> StatelessValidationResult:
    """
    Statelessly validate the execution payload.
    """
    # TODO: We can fill this in properly once the pre-state PR
    # TODO: and state change PRs are completed.
    # TODO: We would effectively call `verify_and_notify_new_payload`

    return StatelessValidationResult(
        new_payload_request_root=compute_new_payload_request_root(
            stateless_input
        ),
        successful_validation=True,
    )
```
