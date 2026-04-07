# Native proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Motivation](#motivation)
- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
  - [Example: Taiko (multi-verifier)](#example-taiko-multi-verifier)
- [Design overview](#design-overview)
- [Changes to EIP-8025](#changes-to-eip-8025)
- [New EIP: Proof-carrying transactions](#new-eip-proof-carrying-transactions)
  - [Transaction format](#transaction-format)
  - [Opcodes](#opcodes)
  - [Multi-proof](#multi-proof)
  - [Proof propagation](#proof-propagation)
- [EVM execution proofs](#evm-execution-proofs)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [Impact on native rollups](#impact-on-native-rollups)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Motivation

Today, every ZK rollup on Ethereum deploys and maintains its own proof verification infrastructure: onchain verifier contracts for each zkVM it uses, adapter contracts, multi-proof dispatchers, and program whitelisting logic. When a bug is found in a zkVM's verification circuit, each rollup must independently deploy a patched verifier contract and coordinate a governance upgrade to point to it. This is slow, risky, and duplicated across the ecosystem.

[EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) introduces zkVM proof verification on Ethereum's consensus layer, but only for L1's own purposes: verifying execution payloads to enable stateless validation. It does not change the story for rollups. Rollups still need their own onchain verifier contracts.

However, the infrastructure that EIP-8025 brings to the CL, the `ProofEngine`, proof gossip, and verification logic, is not inherently L1-specific. If generalized to be program-agnostic and exposed to smart contracts via a new transaction type, any rollup could offload proof verification to the CL. Rollups no longer maintain their own verification infrastructure. When a zkVM implementation needs to be patched, Ethereum client teams release updated software, the same way bugs in execution clients like geth or Nethermind are fixed today: through client releases, without requiring a hard fork. Every rollup using native proof verification benefits from the fix automatically, with no onchain upgrade or governance action needed.

This is the same principle behind native rollups: just as native rollups inherit L1's execution environment and automatically benefit from EVM upgrades, native proof verification lets any rollup inherit L1's proof verification infrastructure and automatically benefit from zkVM fixes and improvements.

The proposal has two parts:

1. **Generalize EIP-8025**: make the CL proof verification infrastructure program-agnostic, so it can verify proofs for any guest program, not just EVM execution.
2. **New EIP**: introduce a proof-carrying transaction type and opcodes that let any smart contract leverage EIP-8025's verification infrastructure, replacing onchain verifier contracts entirely.

## How rollups verify proofs today

Each zkVM vendor (SP1, Risc0, etc.) provides a Solidity verifier contract that implements the final proof verification step, typically a Groth16 or Plonk pairing check over BN254. The verifier is a universal circuit: it can verify proofs for any guest program. The program identity is passed as a public input alongside the hash of the program's output. For SP1:

```solidity
interface ISP1Verifier {
    function verifyProof(
        bytes32 programVKey,        // program hash
        bytes calldata publicValues, // program output
        bytes calldata proofBytes    // the proof
    ) external view;
}
```

**A note on terminology.** SP1 calls `programVKey` a "verification key", but this is different from the zkVM's own verification key. There are two distinct keys:

- The **program hash** (called `programVKey` by SP1, `imageId` by Risc0): a `bytes32` identifying the guest program. It is a hash of the compiled binary. Since each zkVM compiles to a different target (e.g. RV32IMA vs RV64IMA), the same source program produces a different program hash per zkVM. In [ERE](https://github.com/eth-act/ere), each backend has its own [`ProgramDigest`](https://github.com/eth-act/ere/blob/main/crates/zkvm-interface/src/zkvm.rs) type (`SP1VerifyingKey`, `Digest`, `ProgramVk`, etc.). A program hash therefore identifies a `(guest program, zkVM)` pair, not just a guest program.
- The **verification key**: the cryptographic key used by the zkVM's proof system to verify proofs. This is a large structured object (polynomial commitments, domain parameters, etc.) that is specific to the zkVM circuit, not to the guest program. In onchain verifier contracts, the verification key is hardcoded as constants. All programs share the same verification key for a given zkVM version.

This document uses "program hash" for the former and "verification key" for the latter.

### Example: Taiko (multi-verifier)

Taiko illustrates the complexity that arises when a rollup uses multiple proof systems. Its verification architecture involves five contracts across three tiers:

**1. Raw zkVM verifiers.** Taiko deploys both an SP1 Plonk verifier (`SP1Verifier.sol`) and a Risc0 Groth16 verifier (`RiscZeroGroth16Verifier.sol`). These are the vendor-provided universal verifier contracts.

**2. Taiko-specific adapters.** Each raw verifier is wrapped in an adapter contract that implements Taiko's `IVerifier` interface:

```solidity
// TaikoSP1Verifier: adapter for SP1
contract TaikoSP1Verifier is IVerifier {
    address public sp1RemoteVerifier;                    // raw SP1 verifier
    mapping(bytes32 => bool) public isProgramTrusted;    // whitelisted programs

    function verifyProof(Context[] calldata _ctxs, bytes calldata _proof) external view {
        bytes32 aggregationProgram = bytes32(_proof[:32]);
        bytes32 blockProvingProgram = bytes32(_proof[32:64]);
        require(isProgramTrusted[aggregationProgram]);
        require(isProgramTrusted[blockProvingProgram]);

        bytes memory publicInputs = buildPublicInputs(_ctxs);
        ISP1Verifier(sp1RemoteVerifier).verifyProof(
            aggregationProgram, publicInputs, _proof[64:]
        );
    }
}

// Risc0Verifier: adapter for Risc0 (same IVerifier interface)
contract Risc0Verifier is IVerifier {
    address public riscoGroth16Verifier;                 // raw Risc0 verifier
    mapping(bytes32 => bool) public isImageTrusted;      // whitelisted images

    function verifyProof(Context[] calldata _ctxs, bytes calldata _proof) external view {
        (bytes memory seal, bytes32 blockImageId, bytes32 aggregationImageId) =
            abi.decode(_proof, (bytes, bytes32, bytes32));
        require(isImageTrusted[blockImageId]);
        require(isImageTrusted[aggregationImageId]);

        bytes32 journalDigest = sha256(buildPublicInputs(_ctxs));
        IRiscZeroVerifier(riscoGroth16Verifier).verify(
            seal, aggregationImageId, journalDigest
        );
    }
}
```

**3. Multi-verifier dispatcher.** A `ComposeVerifier` contract orchestrates multiple verifiers and enforces that a sufficient set has verified each proof:

```solidity
contract MainnetVerifier is ComposeVerifier {
    address public immutable sgxGethVerifier;    // SGX verifier (required)
    address public immutable risc0RethVerifier;  // Risc0 option
    address public immutable sp1RethVerifier;    // SP1 option

    function verifyProof(Context[] calldata _ctxs, bytes calldata _proof) external {
        SubProof[] memory subProofs = abi.decode(_proof, (SubProof[]));
        for (uint256 i = 0; i < subProofs.length; ++i) {
            IVerifier(subProofs[i].verifier).verifyProof(_ctxs, subProofs[i].proof);
        }
        require(areVerifiersSufficient(verifiers));
    }

    function areVerifiersSufficient(address[] memory _verifiers) internal view override {
        // Must have exactly 2: sgxGethVerifier + (risc0 or sp1)
    }
}
```

This is six deployed contracts (two raw verifiers, two adapters, one dispatcher, one SGX verifier), each with their own upgrade lifecycle, program whitelisting, and failure modes.

## Design overview

This document proposes a **new EIP** that introduces a proof-carrying transaction type and three new opcodes (`PROGRAMHASH`, `PUBVALUESHASH`, `PROOFCOUNT`). This new transaction type allows any smart contract to verify zkVM proofs through L1's consensus layer, replacing onchain verifier contracts entirely.

The new EIP depends on [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) for the underlying CL proof verification infrastructure. However, EIP-8025 as currently specified is tied to EVM execution proofs. For the new EIP to work, EIP-8025 needs to be generalized so that its `ProofEngine`, types, and P2P protocols are program-agnostic rather than execution-specific. This document describes both the new EIP and the required changes to EIP-8025.

The design mirrors [ERE](https://github.com/eth-act/ere)'s architecture, where the [`zkVM`](https://github.com/eth-act/ere/blob/main/crates/zkvm-interface/src/zkvm.rs) trait is program-agnostic and specific guest programs (like the stateless validators) are built on top.

## Changes to EIP-8025

EIP-8025 introduces optional execution proofs for L1 block validation. That functionality remains unchanged. The changes proposed here are to the underlying primitives so that the same infrastructure can also serve the new proof-carrying transaction type.

EIP-8025's current [`ProofType`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#types) is a `uint8` that encodes both the zkVM backend and the guest program as a single value. In the current [Lighthouse implementation](https://github.com/sigp/lighthouse), the mapping is:

| `ProofType` | Guest program | zkVM backend |
|---|---|---|
| 0 | ethrex | Risc0 |
| 1 | ethrex | SP1 |
| 2 | ethrex | Zisk |
| 3 | reth | OpenVM |
| 4 | reth | Risc0 |
| 5 | reth | SP1 |
| 6 | reth | Zisk |

This works for L1 execution proofs where the set of guest programs is small and known in advance. But it cannot accommodate arbitrary rollup programs: adding a new guest program requires assigning new `ProofType` values and updating every client.

The proposed change splits `ProofType` into two independent axes, following [ERE](https://github.com/eth-act/ere)'s design where the [`Compiler`](https://github.com/eth-act/ere/blob/main/crates/zkvm-interface/src/compiler.rs) and the [`zkVM`](https://github.com/eth-act/ere/blob/main/crates/zkvm-interface/src/zkvm.rs) backend are independent. `ProofType` is renamed to `BackendType` and becomes purely about the zkVM backend, and a new `program_id: Bytes32` field identifies the guest program. Since each zkVM compiles the same source to a different binary (see [terminology note](#how-rollups-verify-proofs-today)), `program_id` is specific to a `(guest program, zkVM)` pair. The [`verify_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_execution_proof) method (which verifies and stores individual proofs arriving via gossip) generalizes accordingly:

```python
# Before (current EIP-8025):
class PublicInput(Container):
    new_payload_request_root: Root

class ExecutionProof(Container):
    proof_data: ByteList[MAX_PROOF_SIZE]
    proof_type: ProofType              # encodes both program and backend (current)
    public_input: PublicInput

def verify_execution_proof(self: ProofEngine, execution_proof: ExecutionProof) -> bool: ...

# After (generalized):
class PublicInput(Container):
    program_id: Bytes32                # guest program (per zkVM)
    public_values: ByteList[MAX_PUBLIC_VALUES_SIZE]

class Proof(Container):
    proof_data: ByteList[MAX_PROOF_SIZE]
    backend_type: BackendType          # zkVM backend only
    public_input: PublicInput

def verify_proof(self: ProofEngine, proof: Proof) -> bool: ...
```

The engine maps `backend_type` to the appropriate verification key (one per zkVM version). The `program_id` is not part of this lookup: it is a public input to the verification circuit, checked during proof verification alongside the `public_values`.

Today, EIP-8025's [`process_execution_payload`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#modified-process_execution_payload) constructs a [`NewPayloadRequestHeader`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-newpayloadrequestheader) from the block, then calls [`proof_engine.verify_new_payload_request_header(header)`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_new_payload_request_header). With the generalization, `verify_new_payload_request_header` keeps its name but its implementation changes: it constructs the expected [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L126) and delegates to the general `has_valid_proof`. The signature gains a `chain_config` parameter since `StatelessValidationResult` includes it. The pseudocode uses types from the [execution-specs](https://github.com/ethereum/execution-specs/tree/projects/zkevm/src/ethereum/forks/amsterdam):

```python
from ethereum.forks.amsterdam.stateless import (
    StatelessValidationResult,
    ChainConfig,
)

# The well-known program hash for L1's stateless validator.
# Corresponds to ERE's zkVMProgramDigest::program_digest() for the
# compiled verify_stateless_new_payload guest program.
NATIVE_EVM_PROGRAM_HASH = Bytes32(...)  # configured per client, per zkVM backend


def verify_new_payload_request_header(
    self: ProofEngine,
    new_payload_request_header: NewPayloadRequestHeader,
    chain_config: ChainConfig,
) -> bool:
    """
    EVM-specific method on ProofEngine (unchanged interface).
    Implementation now delegates to the general has_valid_proof.
    """
    expected_result = StatelessValidationResult(
        new_payload_request_root=hash_tree_root(new_payload_request_header),
        successful_validation=True,
        chain_config=chain_config,
    )

    return self.has_valid_proof(
        program_id=NATIVE_EVM_PROGRAM_HASH,
        public_values=serialize(expected_result),
    )
```

`process_execution_payload` calls this exactly as before. Proof-carrying transactions from rollups call the same underlying `has_valid_proof` with their own program hashes (see [EVM execution proofs](#evm-execution-proofs)).

## New EIP: Proof-carrying transactions

### Transaction format

```
TransactionType: PROOF_TX_TYPE

TransactionPayloadBody:
[chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit,
 to, value, data, access_list, max_fee_per_blob_gas,
 blob_versioned_hashes, proofs, public_values_hash,
 y_parity, r, s]
```

Where:
- `proofs`: a list of `(program_hash, backend_type)` pairs, one per proof in the sidecar. Each `program_hash` is a `bytes32` identifying the guest program for that specific zkVM backend (see [terminology note](#how-rollups-verify-proofs-today)). Each `backend_type` is a `uint8`. The length of this list determines `proof_count`
- `public_values_hash`: a `bytes32` hash of the program's public output (shared across all proofs, since all backends prove the same statement)

Note: the CL-level `Proof` container carries the raw `public_values` bytes (needed for proof verification), while the transaction body and EVM opcodes only expose the hash. The contract reconstructs the expected public values and compares hashes.

The block builder produces a single L1 block proof that recursively verifies all native proof verification transactions within that block. Validators verify only the L1 block proof, not individual rollup proofs. One constraint shaping this design: post-quantum proofs are expected to be large, which may limit L1 to one proof per slot. See [Proof propagation](#proof-propagation) for details on how proofs are delivered to the builder.

### Opcodes

New opcodes read the proof-carrying transaction's fields, following the same pattern as `ORIGIN`, `GASPRICE`, and `BLOBBASEFEE` (`G_base` cost). All return zero for non-proof-carrying transactions.

| Opcode | Input | Output | Description |
|--------|-------|--------|-------------|
| `PROGRAMHASH` | `index` | `program_hash` (`bytes32`) | Program hash for the i-th proof. Indexed like `BLOBHASH` |
| `PUBVALUESHASH` | none | `public_values_hash` (`bytes32`) | Hash of the program's public output (shared across all proofs) |
| `PROOFCOUNT` | none | `proof_count` (`uint8`) | Number of distinct zkVM proofs verified by the CL |

`PROGRAMHASH` takes an index because each proof in the tx targets a different `(program_hash, backend_type)` pair. A custom rollup iterates with `PROOFCOUNT()` and checks each `PROGRAMHASH(i)` against its own whitelist.

For native rollups, `PROGRAMHASH(i)` returns a well-known sentinel value (e.g. `bytes32(1)`) when the i-th proof uses a program that L1 currently accepts for its own EVM execution proofs. This way the contract checks `PROGRAMHASH(i) == NATIVE_PROGRAM` without storing specific per-zkVM hashes, and automatically follows L1 upgrades.

### Multi-proof

The `backend_types` list allows each rollup to choose its own security/cost trade-off. A rollup that only needs one proof sets `backend_types = [SP1]`. A rollup that wants higher security can require multiple backends, e.g. `backend_types = [SP1, Risc0]`, meaning the same statement must be independently proven by both before the CL accepts the transaction. The contract reads `PROOFCOUNT()` (the length of `backend_types`) and enforces its own minimum.

This replaces contract-level multi-proof orchestration (like Taiko's `ComposeVerifier` requiring both SGX and a ZK verifier) with a protocol-level mechanism. The `backend_types` are declared in the transaction body and signed by the sender, so they cannot be tampered with.

### Proof propagation

> This section is a very early work in progress. The design is not settled.

A proof-carrying transaction must propagate through the mempool so that any builder can pick it up, without requiring a special relationship between the rollup operator and a specific builder. At the same time, the raw proof bytes are only needed by the builder: validators never need them because the L1 block proof recursively covers all native proof verifications within the block.

This creates an asymmetry: the proof must travel through the mempool for liveness, but it has no long-term availability requirement. Putting the proof in blobs would guarantee availability via DAS, but nobody needs that availability after the builder has consumed the proof. Putting the proof in calldata would make it persist in history forever, which is equally wasteful.

The proposed approach is an ephemeral sidecar: the proof travels with the transaction in the mempool but is not included in the final block. The rollup operator builds a proof-carrying transaction with the proof data attached as a sidecar. The transaction and sidecar propagate together through the mempool, similar to how [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) blob transactions propagate with their blob sidecars. The builder receives both, strips the sidecar, includes only the transaction body in the block, and recursively verifies the proofs as part of the L1 block proof. Validators see only the transaction body (`program_hash`, `public_values_hash`, `proof_count`) and verify the L1 block proof, which covers all proof verifications. They never need the raw proof bytes.

The difference from blob transactions: blob sidecar data becomes blobs in the block (for DA via DAS), while proof sidecar data is consumed by the builder and discarded.

**Size considerations.** [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) defines `MAX_PROOF_SIZE` as 300 KiB. The geth blob pool accepts transactions up to 1 MiB (including sidecar). This means `len(backend_types)` is effectively capped at 3 for mempool propagation (3 * 300 KiB = 900 KiB, fitting within 1 MiB with room for the transaction body).

**Open design questions:**

- Whether proof sidecars need a new sidecar type or can reuse the blob sidecar format.
- Whether the mempool should validate proofs before propagating (expensive but prevents spam) or propagate optimistically.
- How this interacts with [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) (Block-in-Blobs) when a proof-carrying transaction also carries blobs for L2 block data.
- Whether the proof should be committed to in the tx body (e.g. a hash of the proof bytes) so the builder can verify the sidecar matches what the sender signed.

## EVM execution proofs

This layer defines how the general proof verification infrastructure is used for Ethereum execution payload verification. It is a thin specialization on top of the general layer.

The well-known `program_hash` for the L1 stateless validator:

```python
NATIVE_EVM_PROGRAM_HASH = Bytes32(...)  # configured per client, per zkVM backend
```

Multiple implementations of `verify_stateless_new_payload` may exist (e.g. [ethrex](https://github.com/lambdaclass/ethrex), [reth](https://github.com/paradigmxyz/reth)). Each compiles to a different binary with a different program hash per zkVM backend. The `ProofEngine` is configured with the set of accepted `program_hash` values.

The public values follow the [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L126) format. The beacon chain's `process_execution_payload` constructs the expected public values from the block header and calls `proof_engine.has_valid_proof(program_hash=NATIVE_EVM_PROGRAM_HASH, public_values_hash=hash(expected))`.

The [honest prover guide](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/prover.md) becomes a thin specialization: extract `NewPayloadRequest` from the beacon block (EVM-specific), then call the general `request_proofs` with `NATIVE_EVM_PROGRAM_HASH`.

## Impact on existing rollups

Taiko's entire multi-verifier architecture (six contracts, adapter contracts, dispatcher, program whitelisting) collapses:

```solidity
contract TaikoInbox {
    mapping(bytes32 => bool) public isTrustedProgram;  // whitelisted per-zkVM program hashes
    uint256 public minProofCount;                       // multi-proof threshold (e.g. 2)

    function proveBatches(
        BatchMetadata[] calldata metas,
        Transition[] calldata trans
        // _proof parameter removed: verified by the CL
    ) external {
        // Verify all proofs used trusted programs.
        require(PROOFCOUNT() >= minProofCount, "insufficient proofs");
        for (uint256 i = 0; i < PROOFCOUNT(); i++) {
            require(isTrustedProgram[PROGRAMHASH(i)], "untrusted program");
        }

        bytes memory publicInputs = buildPublicInputs(metas, trans);
        require(PUBVALUESHASH() == sha256(publicInputs), "wrong public values");

        // Accept the batches.
        ...
    }
}
```

The contract no longer needs to know which zkVM produced the proofs. The `isTrustedProgram` mapping replaces both `isProgramTrusted` (SP1) and `isImageTrusted` (Risc0) with a single unified whitelist that accepts per-zkVM program hashes. The `minProofCount` replaces `areVerifiersSufficient`.

## Impact on native rollups

The NativeRollup contract from the [ZK specification](./execute_precompile.md#nativerollup-contract-zk) uses the same pattern. Instead of `PROOFROOT` against a `validation_result_root`, it checks `PROGRAMHASH`, `PUBVALUESHASH`, and `PROOFCOUNT`:

```solidity
bytes32 constant NATIVE_PROGRAM = bytes32(uint256(1));
uint256 public minProofCount;

function advance(BlockParams calldata params) external {
    bytes32 l1Anchor = blockhash(block.number - 1);

    bytes32 npRoot = computeNewPayloadRequestRoot(
        blockHash, params.feeRecipient, params.stateRoot,
        // ... remaining fields ...
        getVersionedHashes(params.payloadBlobCount),
        l1Anchor, bytes32(0)
    );

    bytes32 expectedPubValuesHash = sha256(abi.encode(
        npRoot, true, chainId
    ));

    require(PROOFCOUNT() >= minProofCount, "insufficient proofs");
    for (uint256 i = 0; i < PROOFCOUNT(); i++) {
        require(PROGRAMHASH(i) == NATIVE_PROGRAM, "not a native program");
    }
    require(PUBVALUESHASH() == expectedPubValuesHash, "wrong public values");

    blockHash = params.blockHash;
    stateRoot = params.stateRoot;
    blockNumber = blockNumber + 1;
    stateRootHistory[blockNumber] = params.stateRoot;
}
```

A native rollup is simply a rollup where `programHash` points to whatever program L1 itself uses for block validation. If L1 upgrades its program (e.g. a hard fork changes `verify_stateless_new_payload`), native rollups automatically follow. Rollups with custom VMs use the exact same pattern with a different `programHash`.

## Open questions

1. **Program registration**: how does the CL `ProofEngine` learn to verify proofs for a new `program_hash`? Options include static configuration at fork boundaries, an onchain registry, or leaving it implementation-dependent. The current `ProofEngine` is already [implementation-dependent](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md), so the last option preserves that pattern.

2. **Program identity scheme**: each zkVM compiles the same source to a different binary (RV32IMA vs RV64IMA), so `program_hash` is per `(source, zkVM)` pair, matching [ERE](https://github.com/eth-act/ere)'s per-zkVM `ProgramDigest`. Multi-proof transactions carry one `(program_hash, backend_type)` pair per proof. Whether multiple implementations of the same logical program (e.g. ethrex and reth both implementing `verify_stateless_new_payload`) should be accepted for the same rollup is a contract-level policy decision.

3. **Public values size**: the raw `public_values` are carried in the CL-level `PublicInput` container. The EVM only sees `public_values_hash` (a `bytes32`). The CL container needs a `MAX_PUBLIC_VALUES_SIZE` limit that accommodates expected use cases without bloating gossip.

4. **Hash function for `public_values_hash`**: SP1's onchain verifier uses `sha256` truncated to 253 bits (to fit the BN254 scalar field). The native proof verification `public_values_hash` could use `sha256`, `keccak256`, or the hash native to the proof system.

5. **Proof-carrying transaction pricing**: whether proof verification needs a separate gas dimension or is folded into the existing gas model is TBD.
