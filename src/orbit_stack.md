# Orbit stack

## Sequencing

The main function used to sequence blobs in the orbit stack is the `addSequencerFromBlobsImpl` function, whose interface is as follows:

```solidity
function addSequencerL2BatchFromBlobsImpl(
    uint256 sequenceNumber,
    uint256 afterDelayedMessagesRead,
    uint256 prevMessageCount,
    uint256 newMessageCount
) internal {
```

Example of calls:
1. Block: `22866981` ([link](https://etherscan.io/tx/0xaad2f94ba2ae05095f86bb7cf1a86cb0bac4cf17c3eeb4503513a3228ab19ecc))
   - `sequenceNumber`: `934316`
   - `afterDelayedMessagesRead`: `2032069`
   - `prevMessageCount`: `332968910`
   - `newMessageCount`: `332969371` (`+461`)

2. Block: `22866990` (`+9`) ([link](https://etherscan.io/tx/0x9dfc4151dbf46522e53a984e4c48939fd4eb77fb0abc489571e7f9753f0a2227))
    - `sequenceNumber`: `934317` (`+1`)
    - `afterDelayedMessagesRead`: `2032073` (`+4`)
    - `prevMessageCount`: `332969371` (`+0`) 
    - `newMessageCount`: `332969899` (`+528`)

3. Block: `22867001` (`+11`) ([link](https://etherscan.io/tx/0x9245bd5da3892fe9f263750042536dac156ef71a328079f5e7eec01689d35f81))
    - `sequenceNumber`: `934318` (`+1`)
    - `afterDelayedMessagesRead`: `2032073` (`+0`)
    - `prevMessageCount`: `332969899` (`+0`) 
    - `newMessageCount`: `332970398` (`+499`)

It's important to note that when a batch is submitted, also a "batch spending report" is submitted with the purpose of reimbursing the batch poster on the L2. The function will be analyzed later on.

The `formBlobDataHash` function is called to prepare the data that is then saved in storage. Its interface is as follows:

```solidity
function formBlobDataHash(
    uint256 afterDelayedMessagesRead
) internal view virtual returns (bytes32, IBridge.TimeBounds memory, uint256)
```

First, the function fetches the blob hashes of the current transaction using a [Reader4844 yul contract](https://github.com/OffchainLabs/nitro-contracts/blob/0b8c04e8f5f66fe6678a4f53aa15f23da417260e/yul/Reader4844.yul#L4). Then it creates a "packed header" using the `packHeader` function, which is defined as follows:

```solidity
function packHeader(
    uint256 afterDelayedMessagesRead
) internal view returns (bytes memory, IBridge.TimeBounds memory) {
```

The function takes the rollup's bridge "time bounds" and computes the appropriate bounds given the `maxTimeVariation` values, the current timestamp and block number. Such values are then returned together with the `afterDelayedMessagesRead` value.

A time bounds struct is defined as follows:
```solidity
struct TimeBounds {
    uint64 minTimestamp;
    uint64 maxTimestamp;
    uint64 minBlockNumber;
    uint64 maxBlockNumber;
}
```

and the `maxTimeVariation` is a set of four values representing how much in the past or in the future the time or blocks can be from the current time and block number. This is done to prevent reorgs from invalidating sequencer preconfirmations, while also establishing some bounds.

For Arbitrum One, these values are set to:
- `delayBlocks`: `7200` blocks (24 hours at 12s block time)
- `futureBlocks`: `64` blocks (12.8 minutes at 12s block time)
- `delaySeconds`: `86400` seconds (24 hours)
- `futureSeconds`: `768` seconds (12.8 minutes)

The `formBlobDataHash` function then computes the blobs cost by taking the current blob base fee, the (fixed) amount of gas used per blob and the number of blobs. Right after, the following value is returned:
```solidity
return (
    keccak256(bytes.concat(header, DATA_BLOB_HEADER_FLAG, abi.encodePacked(dataHashes))),
    timeBounds,
    block.basefee > 0 ? blobCost / block.basefee : 0
);
```

Now that the `dataHash` is computed, the `addSequencerL2BatchImpl` function is called, which is defined as follows:

```solidity
function addSequencerL2BatchImpl(
    bytes32 dataHash,
    uint256 afterDelayedMessagesRead,
    uint256 calldataLengthPosted,
    uint256 prevMessageCount,
    uint256 newMessageCount
)
    internal
    returns (uint256 seqMessageIndex, bytes32 beforeAcc, bytes32 delayedAcc, bytes32 acc)
```

The function, after some checks, calls the `enqueueSequencerMessage` on the bridge, passing the `dataHash`, `afterDelayedMessagesRead`, `prevMessageCount` and `newMessageCount` values.

The `enqueueSequencerMessage` function is defined as follows:

```solidity
function enqueueSequencerMessage(
    bytes32 dataHash,
    uint256 afterDelayedMessagesRead,
    uint256 prevMessageCount,
    uint256 newMessageCount
)
    external
    onlySequencerInbox
    returns (uint256 seqMessageIndex, bytes32 beforeAcc, bytes32 delayedAcc, bytes32 acc)
```

The function, after some checks, fetches the previous "accumulated" hash and merges it with the new `dataHash` and the "delayed inbox" accumulated hash given the current `afterDelayedMessagesRead`. This new hash is then pushed in the `sequencerInboxAccs` array, which represents the canonical list of inputs to the rollup state transition function.

### Where are these inputs used?

- When creating a new assertion, to ensure that the caller is creating an assertion on the expected inputs;
- When "fast confirming" assertions (in the context of AnyTrust);
- When validating the inputs in the last step of a fraud proof, specifically inside the `OneStepProverHostIo` contract.

### Batch spending report

TODO

## Proving
