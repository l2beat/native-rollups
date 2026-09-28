# Specification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Design principles](#design-principles)
- [Overview](#overview)
- [Data layout](#data-layout)
  - [StatelessInput](#statelessinput)
  - [ChainConfig](#chainconfig)
  - [NewPayloadRequest](#newpayloadrequest)
  - [ExecutionPayload](#executionpayload)
- [Proof statement](#proof-statement)
- [Root computation](#root-computation)
- [NativeRollup contract](#nativerollup-contract)
- [Blob encoding](#blob-encoding)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Design principles

The core principle is to reuse as many L1 components as possible. L2 operators prove the same program as L1 provers, [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L321), under the verification key that L1 approves for the current fork. The mandatory L1 block proof covers the L2 proofs, and validators only verify that block proof.

This means native rollups inherit whatever the L1 EVM supports: no custom transaction types, precompiles, or fee markets on the L2 side. Any such change would require modifying the shared program. L2-specific logic, such as L1 anchoring and the fixed fields below, lives in the rollup contract, which decides which fields come from storage, which from the operator, and which are constants.

Parts of the design depend on L1 features that are still in development, listed in [tech dependencies](tech_dependencies.md). The specification is written as if they were already implemented, and will change as they mature.

## Overview

This design targets the future L1 in which execution proofs are mandatory and validators do not download full execution payloads. Validators sample payload data through DAS and verify the mandatory L1 block proof against a compact `NewPayloadRequestHeader`, which has the same SSZ `hash_tree_root` as the full `NewPayloadRequest`.

A native rollup advances as follows:

1. The **operator** builds an L2 block and proves `verify_stateless_new_payload` for it. The proof's public output is the block's `StatelessValidationResult`, whose root is the `validation_result_root` (see [Proof statement](#proof-statement)).
2. The **operator** submits an [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transaction that carries the L2 block data in [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) blobs, declares the [EIP-8288](./zkzkframes.md) dependency `(LEANSTARK_SCHEME, validation_result_root, verification_key_hash)`, and calls the rollup contract.
3. The **rollup contract** reconstructs the expected `validation_result_root` from its storage, the calldata, and the blob versioned hashes, checks the verification key hash against the [EIP-8357](./evm_vk_registry.md) registry, and updates its state.
4. The **EIP-8288 aggregate** proves the dependency, and the **mandatory L1 block proof** covers both the aggregate and the contract's execution.

## Data layout

Native rollups prove the same function as L1, and therefore share its block structure. The following tables describe how native rollup blocks map to the standard spec types.

Fields marked **constrained** are validated during execution (wrong value = proof fails). Fields marked **unconstrained** are free inputs chosen by the operator. Fields marked **fixed** have a constant value for L2.

The unconstrained fields (`fee_recipient`, `prev_randao`, `parent_beacon_block_root`) correspond to the [`PayloadAttributes`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L86) that on L1 are trusted to come from the consensus layer. The EL never validates them; it accepts whatever the CL provides. Since native rollups have no CL, these become free inputs for the operator. `timestamp` is also CL-provided on L1 but additionally constrained by the EL (`> parent_header.timestamp`).

### StatelessInput

[`StatelessInput`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L165): input to `verify_stateless_new_payload`.

| Field | Expected source | Notes |
|-------|-----------------|-------|
| `new_payload_request` | see below | |
| `witness` | offchain | [`ExecutionWitness`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L27): MPT node preimages, contract bytecodes, ancestor headers. Used by the prover, never posted to L1 |
| `chain_config` | storage (`chain_id`) + EIP-8357 registry (activation timestamp) | L2 chain configuration; see below |
| `public_keys` | offchain | Pre-recovered ECDSA keys to avoid expensive recovery in the ZK circuit |

### ChainConfig

The current L1 zkEVM [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L130-L159) has the following logical shape:

```text
ChainConfig {
    chain_id: uint64,
    active_fork: {
        activation: {
            block_number: Optional[uint64],
            timestamp: Optional[uint64],
        },
    },
}
```

At least one activation coordinate must be present, and stateless validation checks that the active fork's activation has been reached by the target payload. `ChainConfig` contains no fork identifier: the fork's rules are bound by the verification key hash.

A native rollup builds its L2 `ChainConfig` from the L2 `chain_id` stored in its contract and the activation timestamp that the EIP-8357 registry returns with the selected verification key hash, with the block-number coordinate absent. A rollup that follows the current registry entry therefore moves to each new L1 fork without a contract upgrade. The same `ChainConfig` is used as `StatelessInput.chain_config` and when reconstructing the proof's public output.

### NewPayloadRequest

[`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L63) (inside `StatelessInput`):

| Field | Constrained | Expected source | Notes |
|-------|-------------|-----------------|-------|
| `execution_payload` | | | See below |
| `versioned_hashes` | yes | `BLOBHASH` | Ordered list of blob versioned hashes. On L1, the first `payload_blob_count` entries are payload blobs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142), carrying block data) and the rest are from type-3 blob transactions. On L2, since blob transactions are not supported, the list contains only payload blob hashes, read via `BLOBHASH` from the transaction's blobs |
| `parent_beacon_block_root` | no | computed onchain | Repurposed as the L1 anchor on L2. The existing [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) system transaction inside `apply_body` writes this value to the beacon roots predeploy, making it available to L2 contracts. The rollup contract chooses what to pass in this field (e.g. an L1 block hash, a message queue commitment, or any other value useful for L1->L2 communication). See [L1 anchoring](./l1_anchoring.md) and [L1->L2 messaging](./l1_l2_messaging.md) |
| `execution_requests` | fixed | constant | Empty: L2 has no validator operations (deposits, exits, consolidations) |

### ExecutionPayload

[`ExecutionPayload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L29) (inside `NewPayloadRequest`):

| Field | Constrained | Expected source | Notes |
|-------|-------------|-----------------|-------|
| `parent_hash` | yes | storage | Hash of the previous L2 block. Chain continuity link |
| `fee_recipient` | no | various | Fee recipient (coinbase). Could come from calldata, or be a fixed address in storage (e.g. a DAO treasury) |
| `state_root` | yes | calldata | Post-state root |
| `receipts_root` | yes | calldata | Post-receipts root |
| `logs_bloom` | yes | calldata | Computed during execution |
| `prev_randao` | no | various | See [L1 vs L2 diff: RANDAO](./l1_vs_l2_diff.md#randao) |
| `block_number` | yes | storage | Must equal `parent_header.number + 1` |
| `gas_limit` | yes | storage | Bounds check against parent (1/1024 rule). TBD: ZK gas handling |
| `gas_used` | yes | calldata | Computed during execution |
| `timestamp` | yes | calldata | Must be `> parent_header.timestamp` |
| `extra_data` | no | various | Max 32 bytes |
| `base_fee_per_gas` | yes | calldata | Must match EIP-1559 formula from parent header |
| `block_hash` | yes | calldata | Computed from header |
| `transactions` | yes | calldata + blobs | `transactions_root` in calldata, full transactions in [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) payload blobs |
| `withdrawals` | fixed | constant | Empty: L2 has no beacon chain. Without this, an operator could mint ETH on L2 through fake withdrawals |
| `blob_gas_used` | fixed | constant | 0: L2 does not support blob transactions, and any blob transaction would make the computed value mismatch |
| `excess_blob_gas` | fixed | constant | 0 for L2 |
| `block_access_list` | yes | calldata + blobs | Canonical RLP bytes defined by [EIP-7928](https://eips.ethereum.org/EIPS/eip-7928). `block_access_list_root` in calldata, full bytes in [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) payload blobs |

The BAL has two distinct commitments. Let `block_access_list = RLP.encode(BAL)`, as defined by EIP-7928:

- `block_access_list_hash = keccak256(block_access_list)` is the existing EIP-7928 execution-block-header field. Stateless validation computes it from the full BAL and checks it through `block_hash`.
- `block_access_list_root = hash_tree_root(ProgressiveByteList(block_access_list))` is a compact SSZ summary defined by this proposal for `NewPayloadRequestHeader`. Replacing the full `block_access_list` field with this root preserves the `hash_tree_root` of the enclosing progressive `ExecutionPayload`.

`block_access_list_root` is not a new execution-block-header field and is not part of EIP-7928. It is only the root-equivalent representation needed by contracts and validators that do not download the full execution payload.

## Proof statement

The L2 proof shows that [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L321) succeeded for the L2 block. Its public output is the full [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L197):

- `new_payload_request_root`: the root of the [`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L66) for that L2 block, computed via [`compute_new_payload_request_root`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L229)
- `successful_validation`: whether the state transition succeeded
- `chain_config`: the complete [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L130-L159) used during execution (see [PR #2342](https://github.com/ethereum/execution-specs/pull/2342))

The EIP-8288 dependency's `data_hash` is the `hash_tree_root` of this result, the `validation_result_root`.

**Why the full result.** `ChainConfig` is part of [`StatelessInput`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L165) but not part of [`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L63) or the block header. If the public output were only the `new_payload_request_root`, the prover could freely choose it as a private input. In particular, choosing another `chain_id` would enable cross-chain transaction replay: for typed transactions ([EIP-2930](https://eips.ethereum.org/EIPS/eip-2930) and later), [`recover_sender`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/transactions.py#L665) uses the transaction's own `tx.chain_id` for signature recovery, not `block_env.chain_id`, so transactions from any chain would execute successfully. Including the complete `chain_config` binds both the rollup's stored L2 chain ID and the activation timestamp of the selected fork. The activation acts as an additional guard, since stateless validation rejects a payload that predates the active fork.

## Root computation

The rollup contract must reconstruct the expected `validation_result_root` and check it against the declared dependency. This requires two steps:

1. **Compute `new_payload_request_root` from `NewPayloadRequestHeader`.** The mandatory-proof model requires a compact header because neither validators nor the contract have the full transaction list or BAL. SSZ defines [root-equivalent summaries](https://github.com/ethereum/consensus-specs/blob/master/ssz/simple-serialize.md#summaries-and-expansions): variable-sized payload fields can be replaced by their `hash_tree_root` without changing the root of the enclosing object. Consequently, `hash_tree_root(NewPayloadRequestHeader) == hash_tree_root(NewPayloadRequest)`. [`NewPayloadRequestHeader`](https://github.com/ethereum/consensus-specs/issues/5076) was removed from the current optional EIP-8025 flow only because validators still receive the full payload; this proposal assumes its return for mandatory proofs. The root itself follows the zkEVM [`SszNewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_ssz.py) schema. Merged [execution-specs PR #3248](https://github.com/ethereum/execution-specs/pull/3248) aligns that schema with Gloas's EIP-7688 progressive merkleization, which changes `new_payload_request_root` while leaving its SSZ serialization unchanged. This proposal targets that progressive root.

2. **Hash the full `StatelessValidationResult`**: build the L2 `ChainConfig` as described in [ChainConfig](#chainconfig), and compute `hash_tree_root` of the [`SszStatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_ssz.py#L254-L259) containing `new_payload_request_root` (from step 1), `successful_validation = true`, and that `chain_config`. The result is the `validation_result_root`.

The contract has access to every field needed for step 1:

| Leaf | Expected source |
|------|-----------------|
| Scalar header fields (`parent_hash`, `block_number`, etc.) | Contract storage and operator calldata |
| `transactions_root` | Operator calldata (constrained: proven by the L2 proof) |
| `withdrawals_root` | Known constant (empty for L2) |
| `block_access_list_root` | Operator calldata: `hash_tree_root(ProgressiveByteList(block_access_list))` (constrained by the L2 proof) |
| `slot_number` | TBD: its L2 interpretation must be defined |
| `versioned_hashes` | `BLOBHASH` from the transaction's blobs |
| `parent_beacon_block_root` | Computed onchain (L1 anchor) |
| `execution_requests` | Known constant (empty for L2) |

`transactions_root` and `block_access_list_root` are constrained fields: if the operator provides a wrong value, the reconstructed `new_payload_request_root` does not match the L2 proof, and the mandatory L1 proof fails. This is the same trust model as `state_root` and `receipts_root`, which are also claimed by the operator and validated by the proof.

The contract does not separately receive EIP-7928's `block_access_list_hash`. The L2 proof computes that Keccak commitment from the full BAL, validates it as part of the execution block header, and binds the resulting `block_hash`.

## NativeRollup contract

The contract implements the [root computation](#root-computation) and checks the result against the EIP-8288 dependency declared in the same transaction. It verifies no proof itself: in a valid block, EIP-8288 guarantees that every declared dependency is proven.

```solidity
contract NativeRollup {

    struct BlockParams {
        // Constrained fields (validated by the L2 proof)
        bytes32 stateRoot;
        bytes32 receiptsRoot;
        bytes   logsBloom;
        uint256 gasUsed;
        uint256 timestamp;
        uint256 baseFeePerGas;
        bytes32 blockHash;
        bytes32 transactionsRoot;
        bytes32 blockAccessListRoot; // SSZ root of the progressive byte list containing RLP(BAL)
        uint256 payloadBlobCount;    // EIP-8142: number of blobs carrying L2 block data
        // Unconstrained fields (free operator inputs)
        address feeRecipient;
        bytes32 prevRandao;
        bytes32 extraData;
    }

    uint8 constant LEANSTARK_SCHEME = 0x11; // EIP-8288
    address constant EVM_VK_REGISTRY = 0x0000709b303ef147cee6c13f3af6c5a402da8357; // EIP-8357

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    bytes32 public stateRoot;
    uint256 public blockNumber;
    uint256 public gasLimit;
    uint64 public chainId;

    // Verification key policy (see EIP-8357)
    enum VkPolicy { FollowCurrent, Pinned }
    VkPolicy public vkPolicy;
    bytes32 public pinnedVkHash;

    // L2 state root history (for L2->L1 messaging via state proofs)
    mapping(uint256 => bytes32) public stateRootHistory;

    // L1->L2 message queue. Messages are stored in this contract's
    // storage and become accessible on L2 via storage proofs against
    // the anchored L1 block hash.
    bytes32[] public pendingL1Messages;

    function sendMessage(address to, bytes calldata data) external payable {
        bytes32 messageHash = keccak256(
            abi.encodePacked(msg.sender, to, msg.value, keccak256(data), pendingL1Messages.length)
        );
        pendingL1Messages.push(messageHash);
    }

    function advance(BlockParams calldata params, uint256 dependencyFrameIndex) external {
        // 1. Read the EIP-8288 dependency declared by this transaction.
        (uint8 scheme, bytes32 dataHash, bytes32 vkHash) = readDependency(dependencyFrameIndex);
        require(scheme == LEANSTARK_SCHEME, "not a LeanSTARK dependency");

        // 2. Select the EVM verification key hash and its activation
        //    timestamp from the EIP-8357 registry. Zero selects the
        //    current entry.
        (bytes32 expectedVkHash, uint64 activationTimestamp) =
            readRegistry(vkPolicy == VkPolicy.FollowCurrent ? bytes32(0) : pinnedVkHash);
        require(vkHash == expectedVkHash, "wrong verification key");

        // 3. Compute new_payload_request_root from storage, calldata,
        //    versioned hashes, and the L1 anchor (see L1 anchoring).
        //    Hashing scheme is SSZ hash_tree_root. TBD: onchain library.
        bytes32 npRoot = computeNewPayloadRequestRoot(
            // ExecutionPayloadHeader fields
            parentHash:          blockHash,              // from storage
            feeRecipient:        params.feeRecipient,
            stateRoot:           params.stateRoot,
            receiptsRoot:        params.receiptsRoot,
            logsBloom:           params.logsBloom,
            prevRandao:          params.prevRandao,
            blockNumber:         blockNumber + 1,        // from storage
            gasLimit:            gasLimit,                // from storage
            gasUsed:             params.gasUsed,
            timestamp:           params.timestamp,
            extraData:           params.extraData,
            baseFeePerGas:       params.baseFeePerGas,
            blockHash:           params.blockHash,
            transactionsRoot:    params.transactionsRoot, // constrained (proven)
            withdrawalsRoot:     bytes32(0),              // empty for L2
            blobGasUsed:         0,                       // fixed for L2
            excessBlobGas:       0,                       // fixed for L2
            blockAccessListRoot: params.blockAccessListRoot, // constrained (proven)
            payloadBlobCount:    params.payloadBlobCount,
            // NewPayloadRequest fields
            versionedHashes:     getVersionedHashes(params.payloadBlobCount),
            parentBeaconBlockRoot: blockhash(block.number - 1), // L1 anchor
            executionRequests:   bytes32(0)               // empty for L2
        );

        // 4. Hash the full StatelessValidationResult and compare it with
        //    the declared dependency.
        ChainConfig memory l2ChainConfig = chainConfig(chainId, activationTimestamp);
        bytes32 validationResultRoot =
            SSZ.hashTreeRootStatelessValidationResult(npRoot, true, l2ChainConfig);
        require(dataHash == validationResultRoot, "root mismatch");

        // 5. Update onchain state.
        blockHash = params.blockHash;
        stateRoot = params.stateRoot;
        blockNumber = blockNumber + 1;
        stateRootHistory[blockNumber] = params.stateRoot;
    }

    function getVersionedHashes(uint256 count) internal view returns (bytes32[] memory) {
        // Since L2 has no type-3 blob transactions, all blobs in the
        // transaction are payload blobs (EIP-8142 encoded L2 block data).
        bytes32[] memory hashes = new bytes32[](count);
        for (uint256 i = 0; i < count; i++) {
            hashes[i] = blobhash(i);
        }
        return hashes;
    }
}
```

`readDependency` uses the EIP-8141 introspection instructions: `FRAMEPARAM(0x02, i)` to require the dependency frame mode, `FRAMEPARAM(0x04, i)` to require exactly one 96-byte triple, and `FRAMEDATACOPY` to read it. Requiring a single triple avoids ambiguous matching across frames. `readRegistry` is a `STATICCALL` to the EIP-8357 registry.

Replay is constrained by state: the expected root commits to the parent L2 block hash and number, the L2 chain ID, the activation timestamp, the L1 anchor, and the blob versioned hashes.

See also: [L1 anchoring](./l1_anchoring.md), [L1->L2 messaging](./l1_l2_messaging.md), [L2->L1 messaging](./l2_l1_messaging.md)

## Blob encoding

L2 block data is encoded into blobs following [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142), which explicitly depends on EIP-7928. The operator calls [`execution_payload_data_to_blobs`](https://eips.ethereum.org/EIPS/eip-8142) to encode the canonical RLP BAL followed by the RLP transaction list into an ordered list of blobs attached to the transaction. Native rollups do not define a separate BAL encoding.

**Data availability guarantee.** EIP-8288 proves correctness, not availability, so the L2 data travels in blobs. Following EIP-8142's zkEVM path, the L2 proof derives payload blobs from its private BAL and transaction data and verifies blob/commitment consistency against the public versioned hashes. The `validation_result_root` additionally commits, through `new_payload_request_root`, to those versioned hashes, `transactions_root`, and `block_access_list_root`. The contract reconstructs the same root using `BLOBHASH` and the two operator-provided SSZ summaries. DAS ensures the blobs are available. Missing blobs make the L1 block invalid; inconsistent blob data makes the L2 proof invalid; and incorrect summary roots make the contract's root check fail.

## Open questions

1. **Proof pricing**: covering an L2 proof in the mandatory L1 proof adds work for the L1 prover. EIP-8288 charges a fixed `LEANSTARK_VERIFICATION_GAS` per dependency. Whether that is adequate, or a separate proof gas market is needed, depends on the L1 zkEVM gas model. See [tech dependencies](./tech_dependencies.md).

2. **Root computation library**: the rollup contract needs to compute `new_payload_request_root` onchain via SSZ `hash_tree_root` (over `SszNewPayloadRequest`) and then `hash_tree_root` the full `SszStatelessValidationResult`. The availability and gas cost of an SSZ `hash_tree_root` library in Solidity is a practical consideration.

3. **One L2 block per transaction**: EIP-8288 allows one STARK dependency per transaction, and the native program proves one block, so every L2 block needs its own L1 transaction. Proving a range of blocks with a single proof would require L1 to approve a program that validates multiple blocks.

4. **Sequence-first-prove-later**: the current design requires blobs and proof to be in the same transaction, so the operator must have the proof ready at data posting time. Supporting sequence-first-prove-later (post data first, prove later) would require a mechanism to reference past blobs. `BLOBHASH` only accesses blobs in the current transaction. Possible approaches include a new opcode or precompile that can attest to blob availability from past blocks (within the DAS availability window), or a contract-level registry of blob commitments.
