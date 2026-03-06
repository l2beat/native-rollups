# Native rollup verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Re-execution vs ZK enforcement](#re-execution-vs-zk-enforcement)
- [Design principles](#design-principles)
- [Data layout](#data-layout)
  - [`StatelessInput`](#statelessinput)
  - [`NewPayloadRequest`](#newpayloadrequest)
  - [`ExecutionPayload`](#executionpayload)
- [Re-execution specification](#re-execution-specification)
  - [Overview](#overview)
  - [The EXECUTE precompile](#the-execute-precompile)
  - [L2-specific preprocessing](#l2-specific-preprocessing)
  - [NativeRollup contract (re-execution)](#nativerollup-contract-re-execution)
- [ZK specification](#zk-specification)
  - [Overview](#overview-1)
  - [From re-execution to ZK](#from-re-execution-to-zk)
  - [Proof-carrying transactions](#proof-carrying-transactions)
  - [The `PROOFROOT` opcode](#the-proofroot-opcode)
  - [Transaction processing](#transaction-processing)
  - [Proof validation](#proof-validation)
  - [Root computation](#root-computation)
  - [NativeRollup contract (ZK)](#nativerollup-contract-zk)
  - [Blob encoding](#blob-encoding)
  - [Proof strategies](#proof-strategies)
    - [Separate proofs](#separate-proofs-specified)
    - [Recursive proofs (TBD)](#recursive-proofs-tbd)
  - [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Re-execution vs ZK enforcement

The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) for the `EXECUTE` precompile presented two possible enforcement mechanisms: re-execution and ZK proofs. While the latter requires the L1 ZK-EVM upgrade to take place, the former can potentially be implemented beforehand and set the stage for the ZK version, in a similar way that proto-danksharding was first introduced without PeerDAS.

The re-execution variant would only be able to support optimistic rollups with a bisection protocol that goes down to single or few-L2-blocks sized steps, and that are EVM-equivalent. Today there are three stacks with working bisection protocols, namely Orbit stack (Arbitrum), OP stack (Optimism) and Cartesi. Cartesi is built to run a Linux VM so they wouldn't be able to use the precompile, and Orbit supports [Stylus](https://arbitrum.io/stylus) which doesn't make them fully EVM-equivalent, unless a Stylus-less version is implemented, but even in this case it wouldn't be able to support Arbitrum One. OP stack is mostly EVM-equivalent, but still requires heavy modifications to support native execution. It's therefore unclear whether trying to implement the re-execution version of the precompile is worth it, or if it's better to wait for the more powerful ZK version.

While the L1 ZK-EVM upgrade is not needed for the re-execution version, statelessness is, as we want L1 validators to be able to verify the precompile without having to hold all rollups' state. It's not clear whether the time interval between statelessness and L1 ZK-EVM will be long enough to justify the implementation of the re-execution variant, or whether statelessness will be implemented before the L1 ZK-EVM in the first place.

Both variants are specified in this document. The re-execution spec comes first because it is simpler and helps explain the progression to ZK: the core function being executed or proven is the same ([`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129)), and the contract patterns (state management, messaging, anchoring) are shared. The ZK spec then shows what changes when re-execution is replaced by proof verification.

## Design principles

The core principle is to re-use as many L1 components as possible. L2 operators run the same proving infrastructure as L1 provers: they prove the same program ([`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129)) with the same keys. L1 nodes verify L2 proofs using the same [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) infrastructure they use for L1 block proofs. In the re-execution variant, L1 validators run the same stateless validation function directly.

This means native rollups inherit whatever the L1 EVM supports: no custom transaction types, precompiles, or fee markets on the L2 side. Any such change would require modifying the shared program, making the proposal more complex and harder to accept. The L2-specific logic (blob transaction filtering, L1 anchoring) is kept outside the standard function, in a thin preprocessing layer.

Significant parts of the design depend on its [tech dependencies](tech_dependencies.md), which might still be in development. The specification tries to be written as if such features were already implemented, without trying to predict exact details. Significant changes are therefore expected once they mature.

## Data layout

Both variants execute or prove the same function ([`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129)) and therefore share the same L2 block structure. The following tables describe how native rollup blocks map to the standard spec types.

Fields marked **constrained** are validated during execution (wrong value = proof/execution fails). Fields marked **unconstrained** are free inputs chosen by the operator. Fields marked **fixed** have a constant value for L2.

The unconstrained fields (`fee_recipient`, `prev_randao`, `parent_beacon_block_root`) correspond to the [`PayloadAttributes`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L87) that on L1 are trusted to come from the consensus layer. The EL never validates them; it accepts whatever the CL provides. Since native rollups have no CL, these become free inputs for the operator. `timestamp` is also CL-provided on L1 but additionally constrained by the EL (`> parent_header.timestamp`).

### StatelessInput

[`StatelessInput`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L65): input to `verify_stateless_new_payload`.

| Field | Expected source | Notes |
|-------|-----------------|-------|
| `new_payload_request` | see below | |
| `witness` | calldata (re-execution) / offchain (ZK) | [`ExecutionWitness`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_types.py#L28): MPT node preimages, contract bytecodes, ancestor headers |
| `chain_config` | storage (`chain_id`) | [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L52): L2 chain configuration |
| `public_keys` | calldata (re-execution) / offchain (ZK) | Pre-recovered ECDSA keys to avoid expensive recovery in the ZK circuit |

### NewPayloadRequest

[`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L64) (inside `StatelessInput`):

| Field | Constrained | Expected source | Notes |
|-------|-------------|-----------------|-------|
| `execution_payload` | | | See below |
| `versioned_hashes` | yes | ZK: `BLOBHASH` / re-execution: empty | Ordered list of blob versioned hashes. On L1, the first `payload_blob_count` entries are payload blobs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142), carrying block data) and the rest are from type-3 blob transactions. On L2, since blob transactions are not supported, the list contains only payload blob hashes. In ZK, read via `BLOBHASH` from the proof-carrying transaction's blobs. Empty in re-execution (no blobs) |
| `parent_beacon_block_root` | no | computed onchain | Repurposed as the L1 anchor on L2. The existing [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) system transaction inside `apply_body` writes this value to the beacon roots predeploy, making it available to L2 contracts. The rollup contract chooses what to pass in this field (e.g. an L1 block hash, a message queue commitment, or any other value useful for L1->L2 communication). See [L1 anchoring](./l1_anchoring.md) and [L1->L2 messaging](./l1_l2_messaging.md) |
| `execution_requests` | fixed | constant | Empty for L2 |

### ExecutionPayload

[`ExecutionPayload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L30) (inside `NewPayloadRequest`):

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
| `transactions` | yes | calldata or blobs | Re-execution: full transactions in calldata. ZK: `transactions_root` in calldata, full transactions in blobs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)) |
| `withdrawals` | fixed | constant | Empty for L2 |
| `blob_gas_used` | fixed | constant | 0 for L2 |
| `excess_blob_gas` | fixed | constant | 0 for L2 |
| `block_access_list` | yes | TBD | TBD |

## Re-execution specification

### Overview

The re-execution variant uses an `EXECUTE` precompile that performs L2-specific preprocessing and then calls the standard stateless validation function. This is the simplest enforcement mechanism: the L1 EL directly re-executes the L2 state transition.

This variant is **not intended for production**. It is specified for:
- **Testing**: validating the native rollup contract patterns without needing ZK infrastructure.
- **Understanding**: providing a concrete, executable reference for the verification flow.
- **Progression**: showing the stepping stone from re-execution to ZK (see [From re-execution to ZK](#from-re-execution-to-zk)).

The ethrex project implements a version of this approach ([PR #6186](https://github.com/lambdaclass/ethrex/pull/6186)). Our spec follows the same pattern but wraps the standard [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129) directly, rather than using a custom `apply_body` variant with individual ABI-encoded fields.

### The EXECUTE precompile

The `EXECUTE` precompile wraps [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129) with L2-specific preprocessing. The pseudocode uses types and functions from the [execution-specs](https://github.com/ethereum/execution-specs/tree/projects/zkevm/src/ethereum/forks/amsterdam):

```python
from ethereum_types.numeric import U64, U256

from ethereum_rlp import rlp

from ethereum.forks.amsterdam.vm import Evm
from ethereum.forks.amsterdam.vm.gas import charge_gas
from ethereum.forks.amsterdam.vm.exceptions import ExceptionalHalt, InvalidParameter

from ethereum.forks.amsterdam.stateless import (
    StatelessInput,
    verify_stateless_new_payload,
)


def execute(evm: Evm) -> None:
    data = evm.message.data
    stateless_input = rlp.decode_to(StatelessInput, data)

    charge_gas(evm, stateless_input.new_payload_request.execution_payload.gas_used)

    # L2-specific preprocessing: enforce fixed fields.
    payload = stateless_input.new_payload_request.execution_payload
    request = stateless_input.new_payload_request
    if payload.blob_gas_used != U64(0) or payload.excess_blob_gas != U64(0):
        raise InvalidParameter
    if len(payload.withdrawals) != 0:
        raise InvalidParameter
    if len(request.execution_requests) != 0:
        raise InvalidParameter

    # Standard stateless validation (identical to L1).
    result = verify_stateless_new_payload(stateless_input)

    if not result.successful_validation:
        raise ExceptionalHalt

    evm.output = (
        result.new_payload_request_root
        + U256(result.chain_config.chain_id).to_be_bytes32()
    )
```

**Input:** The precompile reads its input from `evm.message.data`, which contains an RLP-encoded [`StatelessInput`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L65).

**Output:**

- **On success:** `evm.output` contains `new_payload_request_root` (32 bytes) followed by `chain_id` as a big-endian 32-byte word. The `new_payload_request_root` is the root of the [`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L64) and `chain_id` comes from the [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L52) used during execution (see [PR #2342](https://github.com/ethereum/execution-specs/pull/2342)).
- **On failure:** raises `InvalidParameter` for bad fixed fields, or `ExceptionalHalt` if validation fails. Both consume all gas and cause the `STATICCALL` to return `success = false` with empty output.

### L2-specific preprocessing

The preprocessing steps before `verify_stateless_new_payload` enforce that all fixed fields are zero/empty:

- **`blob_gas_used = 0` and `excess_blob_gas = 0`**: L2 does not support type-3 (blob-carrying) transactions. Rather than iterating through all transactions to detect them, the precompile asserts these two fields are zero. Since the STF verifies that the computed `blob_gas_used` matches the header claim, any blob transaction would cause a mismatch and be rejected inside `verify_stateless_new_payload` itself. This makes the check O(1) instead of O(n). See also [EIP-8079](https://eips.ethereum.org/EIPS/eip-8079) which defines a similar check.
- **`withdrawals` empty**: L2 has no beacon chain, so withdrawals must be empty. Without this check, an operator could mint arbitrary ETH on L2 via fake withdrawals.
- **`execution_requests` empty**: L2 has no validator operations (deposits, exits, consolidations).

L1 anchoring does not require preprocessing. The `parent_beacon_block_root` field of `NewPayloadRequest` is repurposed to carry an L1 anchor value chosen by the rollup contract. The existing [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) system transaction inside `apply_body` writes this value to the [beacon roots predeploy](https://eips.ethereum.org/EIPS/eip-4788#beacon-roots-contract), making it available to L2 contracts. This happens inside `verify_stateless_new_payload`, so the anchor write is covered by the L2 execution proof (or re-execution). No separate system transaction or predeploy is needed. The format of the anchor is not prescribed: rollups can pass an L1 block hash, a message queue commitment, or any other value useful for L1->L2 communication. See [L1 anchoring](./l1_anchoring.md) for more details.

### NativeRollup contract (re-execution)

The following contract is a proof of concept showing how the `EXECUTE` precompile can be used. It calls the precompile and updates its onchain state. Like the [ZK variant](#nativerollup-contract-zk), the contract constructs the precompile input from a mix of **storage** (chain state that the contract enforces) and **calldata** (operator-provided fields). The contract stores:

- `stateRoot`, `blockNumber`, `blockHash`, `gasLimit`: current L2 chain head
- `chainId`: L2 chain identifier (part of `ChainConfig`)
- `stateRootHistory`: mapping of block numbers to state roots (for [L2->L1 messaging](./l2_l1_messaging.md) via state proofs)

```solidity
contract NativeRollup {

    struct BlockParams {
        // Constrained fields (validated by re-execution)
        bytes32 stateRoot;
        bytes32 receiptsRoot;
        bytes   logsBloom;
        uint256 gasUsed;
        uint256 timestamp;
        uint256 baseFeePerGas;
        bytes32 blockHash;
        // Unconstrained fields (free operator inputs)
        address feeRecipient;
        bytes32 prevRandao;
        bytes32 extraData;
    }

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    bytes32 public stateRoot;
    uint256 public blockNumber;
    uint256 public gasLimit;
    uint256 public chainId;

    // L2 state root history (for L2->L1 messaging via state proofs)
    mapping(uint256 => bytes32) public stateRootHistory;

    // L1->L2 message queue. Messages are stored in this contract's
    // storage and become accessible on L2 via storage proofs against
    // the anchored L1 block hash.
    bytes32[] public pendingL1Messages;

    // EXECUTE precompile address (TBD)
    address constant EXECUTE = address(0xTBD);

    // Queue an L1->L2 message. The message hash is stored in this
    // contract's storage. On L2, a relayer provides a storage proof
    // against the anchored L1 block hash to prove the message exists.
    function sendMessage(address to, bytes calldata data) external payable {
        bytes32 messageHash = keccak256(
            abi.encodePacked(msg.sender, to, msg.value, keccak256(data), pendingL1Messages.length)
        );
        pendingL1Messages.push(messageHash);
    }

    function advance(
        BlockParams calldata params,
        bytes calldata transactions,
        bytes calldata witness,
        bytes calldata publicKeys
    ) external {
        // 1. Compute the L1 anchor.
        //    Passed as parent_beacon_block_root; the EIP-4788 system
        //    transaction inside apply_body writes it to the beacon
        //    roots predeploy, making it available to L2 contracts.
        //    The anchor format is up to the rollup. This example uses
        //    the L1 block hash, which lets L2 contracts use storage
        //    proofs to access any L1 state.
        //    See: l1_anchoring.md, l1_l2_messaging.md
        bytes32 l1Anchor = blockhash(block.number - 1);

        // 2. Call EXECUTE precompile with RLP-encoded StatelessInput.
        bytes memory input = RLP.encodeStatelessInput(
            NewPayloadRequest(
                ExecutionPayload(
                    blockHash,                  // parent_hash (from storage)
                    params.feeRecipient,
                    params.stateRoot,
                    params.receiptsRoot,
                    params.logsBloom,
                    params.prevRandao,
                    blockNumber + 1,            // block_number (from storage)
                    gasLimit,                   // gas_limit (from storage)
                    params.gasUsed,
                    params.timestamp,
                    params.extraData,
                    params.baseFeePerGas,
                    params.blockHash,
                    transactions,
                    new bytes[](0),             // withdrawals (empty for L2)
                    0,                          // blob_gas_used (zero for L2)
                    0                           // excess_blob_gas (zero for L2)
                ),
                new bytes32[](0),               // versioned_hashes (empty for re-execution)
                l1Anchor,                       // parent_beacon_block_root
                new bytes[](0)                  // execution_requests (empty for L2)
            ),
            witness,
            chainId,
            publicKeys
        );
        (bool success, bytes memory result) = EXECUTE.staticcall(input);
        require(success, "EXECUTE failed");

        // 3. Decode and verify result.
        //    The precompile only returns output on success, so no need
        //    to check a validationSuccessful field.
        (bytes32 newPayloadRequestRoot, uint256 provenChainId) =
            abi.decode(result, (bytes32, uint256));
        require(provenChainId == chainId, "chain_id mismatch");

        // 4. Update onchain state
        blockHash = params.blockHash;
        stateRoot = params.stateRoot;
        blockNumber = blockNumber + 1;
        stateRootHistory[blockNumber] = params.stateRoot;
    }
}
```

## ZK specification

### Overview

The ZK variant replaces the `EXECUTE` precompile with **proof-carrying transactions** and a **`PROOFROOT` opcode**. Instead of re-executing the L2 state transition on L1, the rollup operator generates a ZK proof and the consensus layer validates it. The rollup contract computes the expected commitment onchain and checks it against the proof.

This follows the same EL/CL split pattern as [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) blob transactions: the EL references a commitment (the `validation_result_root`, a hash of the proof's full public output), and the CL validates the corresponding proof.

The specification follows the [stateless execution model](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py) from the execution-specs, the [Block-in-Blobs (EIP-8142)](https://eips.ethereum.org/EIPS/eip-8142) pattern for transaction data availability, and the [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) proof validation infrastructure from the consensus-specs.

The flow:

1. The **rollup operator** builds an L2 block and generates an execution proof by proving [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129), the same program that L1 provers prove for L1 blocks.
2. The **operator** submits a [proof-carrying transaction](#proof-carrying-transactions) to L1. The transaction body includes the `validation_result_root` (accessible to the EVM via [`PROOFROOT`](#the-proofroot-opcode)) and `blob_versioned_hashes`. The sidecar carries the ZK proof and blobs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)-encoded L2 block data).
3. The **rollup contract** reconstructs the expected `validation_result_root` and checks it against `PROOFROOT` (see [Root computation](#root-computation)).
4. The **consensus layer** validates the proof from the sidecar (see [Transaction processing](#transaction-processing)). If the proof is invalid, the L1 block is rejected.
5. The **rollup contract** updates its onchain state (block hash, state root, etc.).

No precompile is needed. The rollup contract is the "programmable consensus layer" that decides which fields come from storage, which from the operator, and which are fixed.

There are two possible strategies for how L2 proofs are validated relative to the L1 block proof: **separate proofs** (nodes validate L2 proofs independently, 1 + N proofs per block) and **recursive proofs** (the L1 prover recursively verifies L2 proofs, 1 proof per block). This document describes the separate proofs approach. See [Proof strategies](#proof-strategies) for details on both.

### From re-execution to ZK

The ZK variant builds directly on the re-execution spec. The core function being verified is the same: [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129). What changes is *how* that function is verified and *where* the data lives.

| Aspect | Re-execution | ZK |
|--------|-------------|-----|
| **Enforcement** | `EXECUTE` precompile re-runs L2 STF | CL validates ZK proof |
| **L2 data** | Calldata (txs + witness + public keys) | Blobs for txs ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)), witness + public keys offchain |
| **EVM access** | Precompile return value | `BLOBHASH` + `PROOFROOT` opcodes |
| **Contract role** | Calls precompile, checks return | Computes `validation_result_root`, checks `PROOFROOT` |
| **CL involvement** | None (pure EL) | Validates proof ([EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)) |
| **L1 proves** | Full L2 execution (re-execution) | Only L2-specific preprocessing |
| **Requires** | Statelessness | Statelessness + L1 ZK-EVM |

**What stays the same:**
- NativeRollup contract pattern (state management, messaging)
- L1 anchoring mechanism
- L1->L2 and L2->L1 messaging patterns
- The function being proven/executed: `verify_stateless_new_payload`
- The `StatelessValidationResult` as the proof's public output (which contains `new_payload_request_root`)

**What changes:**

The main change is where L2 block data lives and who processes it:

- **Transactions and block access list**: in re-execution, the full transaction list is passed as calldata to the `EXECUTE` precompile, which re-executes them. In ZK, the full data moves to blobs following [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142): the block access list and RLP-encoded transactions are packed into blobs via [`execution_payload_data_to_blobs`](https://eips.ethereum.org/EIPS/eip-8142). The contract only receives `transactions_root` in calldata, a constrained field validated by the L2 proof. The blobs ensure data availability so that L2 nodes and provers can reconstruct the block.
- **Witness and public keys**: in re-execution, the [`ExecutionWitness`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_types.py#L28) (trie node preimages, contract codes, ancestor headers) and pre-recovered public keys are passed as calldata because the EL needs them to re-execute. In ZK, neither is needed onchain: the prover uses them offchain to generate the proof, and they are not posted to L1.
- **Block parameters**: remain in calldata in both variants. The contract needs them to either call the precompile (re-execution) or compute the `validation_result_root` onchain (ZK).
- **Verification**: the `EXECUTE` precompile is replaced by proof-carrying transactions + `PROOFROOT`. The CL validates the proof; the L1 EL no longer re-executes the L2 STF. The L1 block proof only covers the contract's Root computation and `PROOFROOT` check, not the full L2 execution.

### Proof-carrying transactions

Proof-carrying transactions are a new [EIP-2718](https://eips.ethereum.org/EIPS/eip-2718) transaction type that extends [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) blob transactions with a proof. Like blob transactions, they declare `blob_versioned_hashes` in their body and carry blobs in their sidecar. Additionally, they declare a `validation_result_root` and carry a ZK proof in the sidecar.

The blobs contain the L2 block data ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) encoded). The proof attests to the correctness of that block's execution. Bundling both in one transaction ensures that data availability (via DAS) and execution correctness (via the proof) are guaranteed atomically: if the blobs are unavailable, the L1 block is invalid; if the proof is invalid, the L1 block is also invalid.

Each proof-carrying transaction proves exactly one L2 block.

```
TransactionType: PROOF_TX_TYPE

TransactionPayloadBody:
[chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit,
 to, value, data, access_list, max_fee_per_blob_gas,
 blob_versioned_hashes, validation_result_root,
 y_parity, r, s]
```

During transaction gossip responses (`PooledTransactions`), the transaction payload is wrapped to include blobs, KZG commitments, KZG proofs, and the execution proof:

```
rlp([tx_payload_body, blobs, kzg_commitments, kzg_proofs, execution_proof])
```

The `validation_result_root` is a hash of the proof's public output (see [Proof validation](#proof-validation)). The `blob_versioned_hashes` commit to the [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)-encoded L2 block data. The `execution_proof` is validated by the CL (see [Transaction processing](#transaction-processing)).

The pattern extends EIP-4844:

| | Blob transactions (EIP-4844) | Proof-carrying transactions |
|---|---|---|
| **Blobs** | Transaction data | L2 block data ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)) |
| **Onchain reference** | `blob_versioned_hashes` | `blob_versioned_hashes` + `validation_result_root` |
| **EVM access** | `BLOBHASH` opcode | `BLOBHASH` + `PROOFROOT` opcodes |
| **CL validation** | Blob availability (KZG, DAS) | Blob availability + proof validity ([EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)) |
| **EL validation** | `is_valid_versioned_hashes` (protocol) | `validation_result_root != 0` (protocol) + contract checks `PROOFROOT` |

See also [Proof-carrying transactions](./proofs.md) for more context on the proof format and the two-proof model.

### The `PROOFROOT` opcode

A new opcode `PROOFROOT` provides EVM access to the `validation_result_root` declared in the current proof-carrying transaction. It is simpler than [`BLOBHASH`](https://eips.ethereum.org/EIPS/eip-4844#opcode-to-get-versioned-hashes) since there is exactly one root per transaction (no index input).

| | |
|---|---|
| **Opcode** | `0x4b` (TBD) |
| **Stack input** | none |
| **Stack output** | `validation_result_root` (`bytes32`) |
| **Gas cost** | `G_base` (2) |

The gas cost follows the same pricing as other zero-input environment opcodes like `ORIGIN`, `GASPRICE`, and `BLOBBASEFEE`, which all cost `G_base` (2 gas).

```python
def proofroot(evm: Evm) -> None:
    charge_gas(evm, GAS_BASE)

    root = evm.message.tx_env.validation_result_root
    push(evm.stack, root)

    evm.pc += Uint(1)
```

If the transaction is not a proof-carrying transaction, `PROOFROOT` returns `bytes32(0)`, analogous to how `BLOBHASH` returns zero for non-blob transactions.

### Transaction processing

Proof-carrying transactions are processed like blob transactions with one additional field (`validation_result_root`) and one additional CL validation step (proof verification). The EL treats the proof as opaque; the CL handles all proof validation via the existing [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) `ProofEngine`.

**EL: transaction decoding and validation.** A new transaction type (e.g. `PROOF_TX_TYPE = 0x05`) is added to [`transactions.py`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/transactions.py#L303). The `ProofCarryingTransaction` class extends [`BlobTransaction`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/transactions.py#L303) with `validation_result_root: Hash32`. The [signing hash](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/transactions.py#L818) includes `validation_result_root`, so the sender commits to which L2 block is being proven.

[`check_transaction`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/fork.py#L436) applies the same validation as blob transactions (blob count, version byte, `max_fee_per_blob_gas >= blob_gas_price`, balance coverage including blob gas) plus:
- `validation_result_root != bytes32(0)`

The blobs use the existing blob gas market. How to price the proof verification cost is an open question (see [Open questions](#open-questions)).

**EL: transaction environment.** [`TransactionEnvironment`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/vm/__init__.py#L106) is extended with `validation_result_root: Hash32` (set from the transaction in [`process_transaction`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/fork.py#L889), or `bytes32(0)` for non-proof-carrying txs). This is what the `PROOFROOT` opcode reads, mirroring how [`blob_versioned_hashes`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/vm/instructions/environment.py#L560) in `TransactionEnvironment` is what `BLOBHASH` reads.

**EL: engine API.** [`is_valid_versioned_hashes`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/new_payload.py#L43) validates that versioned hashes from the CL match blob transaction hashes in the payload. It would need to be updated to also extract `blob_versioned_hashes` from `ProofCarryingTransaction` instances (currently it only handles `BlobTransaction`). Beyond this, no additional engine API changes are needed: the EL does not see or validate the proof, just as it does not see blob contents.

**CL: proof validation.** The consensus layer extracts the `execution_proof` from the proof-carrying transaction's sidecar and validates it using [`proof_engine.verify_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_execution_proof). Unlike L1 block proofs, which are delivered as [`SignedExecutionProof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-signedexecutionproof) messages signed by active validators and processed via [`process_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-process_execution_proof), L2 proofs are delivered via the transaction sidecar and do not require a validator signature — the CL calls `verify_execution_proof` directly. The proof's public output must match the `validation_result_root` declared in the transaction body. If the proof is invalid, the L1 block is rejected, analogous to how an invalid KZG proof in a blob sidecar invalidates the block.

**CL: data availability.** Blob availability is handled by the existing DAS mechanism ([EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) / [EIP-7594](https://eips.ethereum.org/EIPS/eip-7594) PeerDAS). The CL treats proof-carrying transaction blobs identically to any other blobs: they are included in `blob_kzg_commitments`, sampled via DAS, and their versioned hashes are passed to the EL for cross-verification.

**P2P: gossip.** During transaction gossip (`PooledTransactions`), the network representation wraps the transaction body with blobs, KZG data, and the execution proof (as shown in the [sidecar format](#proof-carrying-transactions) above). Receiving nodes validate:
- Blob/commitment/proof counts match `blob_versioned_hashes` (same as blob txs)
- `kzg_to_versioned_hash(commitments[i]) == blob_versioned_hashes[i]` (same as blob txs)
- KZG proofs are valid for the blobs (same as blob txs, via `verify_blob_kzg_proof_batch`)
- The execution proof is valid for the declared `validation_result_root` (via `proof_engine.verify_execution_proof`). Note: this requires the EL gossip layer to have access to the proof engine, which is currently a CL component. How this cross-layer validation works at gossip time is TBD

**Summary of changes by layer:**

| Layer | Blob transactions (EIP-4844) | Proof-carrying transactions (additions) |
|-------|------------------------------|----------------------------------------|
| **EL: tx type** | `BlobTransaction` (0x03) | `ProofCarryingTransaction` (0x05), adds `validation_result_root` |
| **EL: validation** | Blob count, version byte, fee checks | Same + `validation_result_root != 0` |
| **EL: tx env** | `blob_versioned_hashes` | Same + `validation_result_root` |
| **EL: engine API** | `is_valid_versioned_hashes` | Updated to also handle `ProofCarryingTransaction` |
| **CL: validation** | `is_data_available` (DAS) | Same + `proof_engine.verify_execution_proof` |
| **P2P: sidecar** | `[tx, blobs, commitments, kzg_proofs]` | Same + `execution_proof` |

### Proof validation

An L2 execution proof uses the same [`ExecutionProof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-executionproof) structure as an L1 block proof. It proves that [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129) succeeded for the L2 block. The proof's public output is the full [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L96):

- `new_payload_request_root`: the root of the [`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L64) for that L2 block, computed via [`compute_new_payload_request_root`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L106)
- `successful_validation`: whether the state transition succeeded
- `chain_config`: the [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L52) (includes `chain_id`) used during execution (see [PR #2342](https://github.com/ethereum/execution-specs/pull/2342))

The `validation_result_root` declared in the proof-carrying transaction is a hash of this full `StatelessValidationResult`. Unlike L1 proofs, which are gossipped as [`SignedExecutionProof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-signedexecutionproof) messages signed by active validators, L2 proofs are delivered via the proof-carrying transaction sidecar and do not require a validator signature. The CL validates them using the same [`proof_engine.verify_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_execution_proof) function (see [Transaction processing](#transaction-processing)).

Together, the EL check (contract reconstructs expected root and matches `PROOFROOT`, see [Root computation](#root-computation)) and the CL check (valid proof for that root) guarantee that the L2 state transition was executed correctly.

**`chain_id` and proof binding.** The `chain_id` is part of [`StatelessInput.chain_config`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L52) but not part of [`NewPayloadRequest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/execution_engine/types.py#L64) or the block header. If the public output were only the `new_payload_request_root`, the prover could freely choose `chain_id` as a private input, enabling cross-chain transaction replay: for typed transactions ([EIP-2930](https://eips.ethereum.org/EIPS/eip-2930) and later), [`recover_sender`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/transactions.py#L651) uses the transaction's own `tx.chain_id` for signature recovery, not `block_env.chain_id`, so transactions from any chain would execute successfully. By including `chain_config` in `StatelessValidationResult` ([PR #2342](https://github.com/ethereum/execution-specs/pull/2342)), the proof attests to which `chain_id` was used, and the contract can verify it matches its stored value by reconstructing the full `StatelessValidationResult` before hashing.

### Root computation

The rollup contract must reconstruct the expected `validation_result_root` and check it against `PROOFROOT`. This requires two steps:

1. **Compute `new_payload_request_root`** from block header fields via [`compute_new_payload_request_root`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L106). The exact hashing scheme is TBD in the execution-specs (the function currently raises `NotImplementedError`). The contract only has header-level data (roots and scalar fields, not full transaction lists), so the scheme must allow reconstruction from [`NewPayloadRequestHeader`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-newpayloadrequestheader) fields. This is the same requirement that the CL has in [`process_execution_payload`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#modified-process_execution_payload), where the proof engine verifies a header against stored proofs.

2. **Hash the full `StatelessValidationResult`**: combine `new_payload_request_root` (from step 1), `successful_validation = true`, and `chain_config` (with `chain_id` from storage) into the `validation_result_root`.

The contract has access to every field needed for step 1:

| Leaf | Expected source |
|------|-----------------|
| Scalar header fields (`parent_hash`, `block_number`, etc.) | Contract storage and operator calldata |
| `transactions_root` | Operator calldata (constrained: proven by the L2 proof) |
| `withdrawals_root` | Known constant (empty for L2) |
| `versioned_hashes` | `BLOBHASH` from the proof-carrying transaction |
| `parent_beacon_block_root` | Computed onchain (L1 anchor) |
| `execution_requests` | Known constant (empty for L2) |

`transactions_root` is a constrained field: if the operator provides a wrong value, the L2 proof fails and the CL rejects the L1 block. This is the same trust model as `state_root` and `receipts_root`, which are also claimed by the operator and validated by the proof.

### NativeRollup contract (ZK)

The ZK contract implements the [Root computation](#root-computation) steps and checks the result against `PROOFROOT`. The operator submits a proof-carrying transaction where the blobs contain L2 block data, the calldata contains block parameters, and the sidecar contains the execution proof.

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
        uint256 payloadBlobCount;
        // Unconstrained fields (free operator inputs)
        address feeRecipient;
        bytes32 prevRandao;
        bytes32 extraData;
    }

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    bytes32 public stateRoot;
    uint256 public blockNumber;
    uint256 public gasLimit;
    uint256 public chainId;

    // L2 state root history (for L2->L1 messaging via state proofs)
    mapping(uint256 => bytes32) public stateRootHistory;

    // L1->L2 message queue (same as re-execution variant)
    bytes32[] public pendingL1Messages;

    function sendMessage(address to, bytes calldata data) external payable {
        bytes32 messageHash = keccak256(
            abi.encodePacked(msg.sender, to, msg.value, keccak256(data), pendingL1Messages.length)
        );
        pendingL1Messages.push(messageHash);
    }

    function advance(BlockParams calldata params) external {
        // 1. Compute the L1 anchor.
        //    Same pattern as the re-execution variant (see above).
        bytes32 l1Anchor = blockhash(block.number - 1);

        // 2. Compute new_payload_request_root from
        //    storage + calldata + versioned hashes + computed anchor.
        //    Uses header-level fields: transactions_root instead of
        //    full transactions list.
        //    TBD: exact hashing scheme and onchain library.
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
            payloadBlobCount:    params.payloadBlobCount,
            // NewPayloadRequest fields
            versionedHashes:     getVersionedHashes(params.payloadBlobCount),
            parentBeaconBlockRoot: l1Anchor,              // computed onchain
            executionRequests:   bytes32(0)               // empty for L2
        );

        // 3. Hash the full StatelessValidationResult:
        //    (new_payload_request_root, successful_validation, chain_config)
        //    This implicitly verifies chain_id (from storage) and
        //    successful_validation (always true for a valid proof).
        bytes32 validationResultRoot = hash(
            npRoot,
            true,               // successful_validation
            chainId             // from storage (chain_config)
        );

        // 4. Verify the computed root matches the proof-carrying tx's
        //    declared root. The CL validates the proof for this root.
        require(validationResultRoot == PROOFROOT, "root mismatch");

        // 5. Update onchain state.
        blockHash = params.blockHash;
        stateRoot = params.stateRoot;
        blockNumber = blockNumber + 1;
        stateRootHistory[blockNumber] = params.stateRoot;
    }

    function getVersionedHashes(uint256 count) internal view returns (bytes32[] memory) {
        // Read blob versioned hashes from the proof-carrying tx via
        // BLOBHASH. Since L2 has no type-3 blob transactions, all blobs
        // in the tx are payload blobs (EIP-8142 encoded L2 block data).
        // DAS guarantees these blobs are available.
        bytes32[] memory hashes = new bytes32[](count);
        for (uint256 i = 0; i < count; i++) {
            hashes[i] = blobhash(i);
        }
        return hashes;
    }

}
```

See also: [L1 anchoring](./l1_anchoring.md), [L1->L2 messaging](./l1_l2_messaging.md), [L2->L1 messaging](./l2_l1_messaging.md)

### Blob encoding

L2 block data (transactions + block access list) is encoded into blobs following [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142). The operator calls [`execution_payload_data_to_blobs`](https://eips.ethereum.org/EIPS/eip-8142) to produce an ordered list of blobs, which are included in the proof-carrying transaction's sidecar.

**Data availability guarantee.** The proof-carrying transaction carries both the blobs and the ZK proof. The `blob_versioned_hashes` in the transaction body commit to the blob data via KZG. The `validation_result_root` commits (through the `new_payload_request_root`) to a `NewPayloadRequest` that includes those same `versioned_hashes`. The contract reads the blob hashes via `BLOBHASH` and includes them in the root computation, binding the proof to the blob data. DAS ensures the blobs are available. An operator cannot withhold L2 data: if the blobs are missing, the L1 block is invalid (same as any blob transaction); if the versioned hashes don't match, the root check fails.

### Proof strategies

Each L1 block that contains native rollup state transitions involves two categories of proofs: one or more **L2 execution proofs** (one per proof-carrying transaction) and an **L1 block proof**. There are two strategies for how these proofs are validated.

#### Separate proofs

In this approach, L2 proofs and the L1 block proof are validated independently by the CL:

1. **N L2 execution proofs** (generated by rollup operators): each proves [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L129) for one L2 block. Carried in proof-carrying transaction sidecars and validated by the CL independently.

2. **1 L1 block proof** (generated by the L1 prover): proves the L1 block execution, including the rollup contract's root computation and `PROOFROOT` check. Does **not** re-execute any L2 state transitions, as those are already proven by the operators.

A node validates **1 + N** proofs per L1 block: the L1 block proof plus one L2 proof per proof-carrying transaction. This is the approach described in this document.

#### Recursive proofs (TBD)

In this approach, the L1 prover recursively verifies the L2 proofs as part of proving the L1 block:

1. The rollup operator generates the L2 execution proof and makes it available to the L1 prover.
2. The L1 prover, when proving the L1 block, also proves the *verification* of each L2 proof it encounters. The resulting L1 block proof recursively attests to the correctness of all L2 state transitions within that block.
3. A node validates only **1 proof** per L1 block, regardless of how many L2 blocks were proven.

This reduces per-block verification cost from 1 + N to 1, at the expense of increased complexity for the L1 prover (which must support recursive proof verification). The exact mechanism, including how L2 proofs are delivered to the L1 prover and how the recursive verification circuit is structured, is TBD. See also [Recursive L1+L2 proofs](./proofs.md#recursive-l1l2-proofs).

### Open questions

1. **Blob transaction restriction**: L2 does not support type-3 (blob-carrying) transactions. [EIP-8079](https://eips.ethereum.org/EIPS/eip-8079) explicitly checks for this in the precompile. In the current design, this could be enforced via the L2 `ChainConfig` (the proof circuit rejects blob txs), or via additional onchain checks. The exact mechanism is TBD.

2. **`block_access_list` handling**: With [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142), block access lists may also be encoded in blobs. If so, the operator would provide the `block_access_list` root as calldata (same pattern as `transactions_root`).

3. **Proof-carrying transaction pricing**: Proof verification imposes a cost on the CL (and on L1 provers in the ZK L1 model). Whether this requires a separate proof gas market (analogous to the blob gas market), a flat fee, or is folded into the existing gas model is TBD. Related: how the overall gas model works depends on the L1 ZK-EVM design. See [tech dependencies](./tech_dependencies.md).

4. **Root computation library**: The rollup contract needs to compute `new_payload_request_root` onchain (via `compute_new_payload_request_root`) and then hash the full `StatelessValidationResult`. The availability and gas cost of the required libraries in Solidity is a practical consideration.

5. **Re-execution data encoding**: The `EXECUTE` precompile takes an RLP-encoded `StatelessInput` as calldata. The encoding must be efficient given the potentially large witness size.

6. **Re-execution gas cost**: The gas cost of the `EXECUTE` precompile depends on the L2 block complexity. The gas metering model is TBD.

7. **Sequence-first-prove-later**: The current spec requires blobs and proof to be in the same transaction, so the operator must have the proof ready at data posting time. Supporting sequence-first-prove-later (post data first, prove later) would require a mechanism to reference past blobs from the proof-carrying transaction. `BLOBHASH` only accesses blobs in the current transaction. Possible approaches include a new opcode or precompile that can attest to blob availability from past blocks (within the DAS availability window), or a contract-level registry of blob commitments.

8. **Recursive proof delivery**: In the [recursive proofs](#recursive-proofs-tbd) strategy, how L2 proofs are delivered to the L1 prover, and the structure of the recursive verification circuit, are TBD.
