# Native proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Abstract](#abstract)
- [Motivation](#motivation)
- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
  - [Example: Taiko (multi-verifier)](#example-taiko-multi-verifier)
- [Changes to EIP-8025](#changes-to-eip-8025)
- [Program hash stability (open problem)](#program-hash-stability-open-problem)
- [New EIP: Proof-carrying transactions](#new-eip-proof-carrying-transactions)
  - [Transaction format](#transaction-format)
  - [Opcodes](#opcodes)
  - [Multi-proof](#multi-proof)
  - [Proof propagation](#proof-propagation)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [Impact on native rollups](#impact-on-native-rollups)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Abstract

The overarching goal of this proposal is to massively derisk and simplify L2 bridges by replacing every rollup's bespoke onchain verifier stack with a standard L1 primitive. This is achieved through two changes:

1. **Generalize [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)** so the consensus-layer proof verification infrastructure becomes program-agnostic, not tied to EVM execution proofs.
2. **A new EIP** that exposes it to smart contracts through a proof-carrying transaction type and three opcodes (`PROGRAMHASH`, `PUBVALUESHASH`, `PROOFCOUNT`).

Together, they let any rollup retire its onchain verifier contracts and inherit L1's proof verification infrastructure directly, with zkVM fixes shipping through client releases rather than rollup governance upgrades.

## Motivation

Today, every Ethereum rollup maintains bespoke onchain proof verification infrastructure. ZK rollups deploy zkVM verifier contracts, adapter contracts, multi-proof dispatchers, and program whitelisting logic. Optimistic rollups ship their own onchain fraud-proof VMs (Arbitrum's WAVM, Optimism's Cannon MIPS machine) plus the surrounding dispute logic. In either case every contract is maintained, patched, and upgraded independently in response to bugs in its specific proof system or VM, with each upgrade gated by a custom multisig or DAO. This is slow, risky, and duplicated across the ecosystem.

[EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) introduces zkVM proof verification on Ethereum's consensus layer, but only for L1's own purposes: verifying execution payloads to enable stateless and sublinear validation. Rollups still need their own onchain verifier contracts.

However, the infrastructure that EIP-8025 brings to the CL, the `ProofEngine`, proof gossip, and verification logic, is not inherently L1-specific. If generalized to be program-agnostic and exposed to smart contracts via a new transaction type, any rollup, even non-EVM ones, could offload proof verification to the CL. When a zkVM implementation needs to be patched, Ethereum client teams release updated software the same way bugs in geth or Nethermind are fixed today: through client releases, without a hard fork. This is the same principle behind native rollups: just as native rollups inherit L1's execution environment, native proof verification lets any rollup inherit L1's proof verification infrastructure.

## How rollups verify proofs today

Each zkVM vendor provides a universal Solidity verifier contract (typically a Groth16 or Plonk check over BN254). The program identity and the public values (any inputs and outputs the circuit commits to) are passed alongside the proof. For SP1:

```solidity
interface ISP1Verifier {
    function verifyProof(
        bytes32 programVKey,         // program hash
        bytes calldata publicValues, // public values (inputs and/or outputs)
        bytes calldata proofBytes    // the proof
    ) external view;
}
```

**A note on terminology.** SP1 calls `programVKey` a "verification key", but this collides with the zkVM's own circuit verification key. This document keeps them separate:

- **Program hash** (called `programVKey` by SP1, `imageId` by Risc0): a `bytes32` identifying a compiled guest program. Because each zkVM compiles differently (e.g. RV32IMA vs RV64IMA), it is per-`(source, zkVM)` pair. [ERE](https://github.com/eth-act/ere) expresses this as each backend's [`zkVMVerifier::ProgramVk`](https://github.com/eth-act/ere/blob/master/crates/verifier/core/src/verifier.rs) associated type (wrapping `SP1VerifyingKey`, Risc0's `Digest`, etc.).
- **Verification key**: the zkVM's circuit VK (polynomial commitments, domain parameters). Hardcoded as constants in onchain verifiers, one per zkVM version, shared across all programs.

### Example: Taiko (multi-verifier)

Taiko illustrates the complexity that arises when a rollup uses multiple proof systems. Its verification architecture involves six contracts across three tiers (two raw verifiers, two adapters, one dispatcher, one SGX verifier), each independently maintained and upgraded through a custom multisig.

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
```

A parallel `Risc0Verifier` has the same shape, with `isImageTrusted` replacing `isProgramTrusted` and `sha256(buildPublicInputs(...))` as the journal digest.

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

## Changes to EIP-8025

EIP-8025 introduces optional execution proofs for L1 block validation. That functionality remains unchanged. The changes proposed here are to the underlying primitives, so that the same infrastructure can also serve the new proof-carrying transaction type. The generalization mirrors [ERE](https://github.com/eth-act/ere), whose [`zkVMVerifier`](https://github.com/eth-act/ere/blob/master/crates/verifier/core/src/verifier.rs) trait is program-agnostic and specific guest programs are built on top.

EIP-8025's current [`ProofType`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#types) is a `uint8` that encodes both the zkVM backend and the guest program as a single value. In the current [Lighthouse implementation](https://github.com/eth-act/lighthouse/blob/feat/eip8025/beacon_node/execution_layer/src/eip8025/types.rs), the mapping is:

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

The proposed change splits `ProofType` into two independent axes, following [ERE](https://github.com/eth-act/ere)'s design where the [`Compiler`](https://github.com/eth-act/ere/blob/master/crates/compiler/core/src/lib.rs) and the [`zkVMVerifier`](https://github.com/eth-act/ere/blob/master/crates/verifier/core/src/verifier.rs) backend are independent. `ProofType` is renamed to `BackendType` and becomes purely about the zkVM backend, and a new `program_hash: Bytes32` field identifies the guest program. Since each zkVM compiles the same source to a different binary (see [terminology note](#how-rollups-verify-proofs-today)), `program_hash` is specific to a `(guest program, zkVM)` pair. The [`verify_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_execution_proof) method (which verifies and stores individual proofs arriving via gossip) generalizes accordingly:

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
    program_hash: Bytes32              # guest program (per zkVM)
    public_values: ByteList[MAX_PUBLIC_VALUES_SIZE]

class Proof(Container):
    proof_data: ByteList[MAX_PROOF_SIZE]
    backend_type: BackendType          # zkVM backend only
    public_input: PublicInput

def verify_proof(self: ProofEngine, proof: Proof) -> bool: ...
```

`BackendType` is `ProofType` renamed (still `uint8`). The engine uses `backend_type` to select the circuit VK; `program_hash` is a public input to the circuit, checked alongside `public_values` during verification.

A second method, `has_valid_proof`, is a cheap lookup against proofs already verified by `verify_proof`. It is what the block-processing path (via `verify_new_payload_request_header`) calls to confirm a prior verification exists for a given `(program_hash, public_values_hash)` pair. Fresh proofs, whether from gossip or proof-carrying transaction sidecars, are always ingested through `verify_proof`.

```python
def has_valid_proof(
    self: ProofEngine,
    program_hash: Bytes32,
    public_values_hash: Bytes32,
) -> bool:
    """
    Return True if the engine holds a previously-verified Proof whose
    (public_input.program_hash, sha256(public_input.public_values))
    equals the given (program_hash, public_values_hash) pair.
    """
    ...
```

`backend_type` is not part of the lookup: `program_hash` is already per-`(source, zkVM)`, so distinct backends produce distinct hashes and no ambiguity arises.

EIP-8025's [`verify_new_payload_request_header`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_new_payload_request_header) (called from [`process_execution_payload`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#modified-process_execution_payload)) now builds the expected [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L126), hashes it, and delegates to `has_valid_proof` over the accepted L1 program hashes. The signature gains a `chain_config` parameter since `StatelessValidationResult` includes it; `process_execution_payload` supplies L1's own `ChainConfig` from the beacon state's chain spec. The expression `hash_tree_root(new_payload_request_header)` yields the same value as the root of the full `NewPayloadRequest` by construction, since the header is the roots-only form of the same container.

```python
from ethereum.forks.amsterdam.stateless import (
    StatelessValidationResult,
    ChainConfig,
)
from ethereum.forks.amsterdam.stateless_guest import (
    serialize_stateless_output,
)

# Well-known program hashes for L1's stateless validator. Because each
# `(client implementation, zkVM backend)` pair compiles to a different binary
# (e.g. ethrex-SP1 vs reth-SP1 vs ethrex-Risc0), the engine accepts a *set*,
# not a single value. Corresponds to ERE's zkVMVerifier::program_vk() for
# each compiled variant of verify_stateless_new_payload.
NATIVE_EVM_PROGRAM_HASHES: Set[Bytes32] = {...}  # configured per client


def verify_new_payload_request_header(
    self: ProofEngine,
    new_payload_request_header: NewPayloadRequestHeader,
    chain_config: ChainConfig,
) -> bool:
    """
    EVM-specific method kept as the entry point `process_execution_payload`
    calls. Implementation now delegates to the general has_valid_proof,
    iterating over every accepted L1 program hash: any one stored proof
    is sufficient.
    """
    expected_result = StatelessValidationResult(
        new_payload_request_root=hash_tree_root(new_payload_request_header),
        successful_validation=True,
        chain_config=chain_config,
    )
    # serialize_stateless_output() SSZ-encodes `expected_result`; the guest
    # program emits the same bytes via the same helper, so their sha256 hashes
    # agree by construction.
    expected_public_values_hash = sha256(serialize_stateless_output(expected_result))

    return any(
        self.has_valid_proof(
            program_hash=program_hash,
            public_values_hash=expected_public_values_hash,
        )
        for program_hash in NATIVE_EVM_PROGRAM_HASHES
    )
```

On the prover side, the [honest prover guide](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/prover.md) specializes the same way: extract `NewPayloadRequest` from the beacon block and call `request_proofs` with this prover's own `program_hash` (one entry of `NATIVE_EVM_PROGRAM_HASHES` drawn from [ethrex](https://github.com/lambdaclass/ethrex), [reth](https://github.com/paradigmxyz/reth), or any other accepted implementation).

## Program hash stability (open problem)

Native proof verification's "fixes ship through client releases, nothing onchain changes" property depends on one non-trivial requirement: the `program_hash` pinned onchain must remain stable across zkVM patches. If any patch moves the hash, rollups that pinned the old value are bricked unless they upgrade, and the upgrade story collapses back onto onchain governance.

No zkVM today delivers this directly. Both leading candidates fingerprint artifacts that change under normal SDK / dependency / toolchain churn, not just circuit-layer fixes:

- **Risc0's `imageId`** is a SHA-256 over `SystemState { pc: 0, merkle_root }` with `merkle_root` a Poseidon2 merkle root of the initial memory image, which contains both the user ELF and the kernel ELF ([binfmt/src/elf.rs](https://github.com/risc0/risc0/blob/main/risc0/binfmt/src/elf.rs)). The memory image captures the exact compiled bytes, so a dep bump, toolchain update, or kernel patch all change `imageId` even when STF semantics are unchanged.
- **SP1's `programVKey`** is a Poseidon2 over `(preprocessed_commit, pc_start, ...)` ([hypercube/src/verifier/hashable_key.rs](https://github.com/succinctlabs/sp1/blob/main/crates/hypercube/src/verifier/hashable_key.rs)). `preprocessed_commit` depends on the circuit's AIR preprocessing and `pc_start` on the linker, so circuit changes, SDK bumps, and toolchain changes all move it.

Using either directly as the onchain `program_hash` would make every zkVM release a rollup-visible event.

The realistic path is an **indirection layer**: the onchain `program_hash` is a stable, rollup-chosen identifier and the public input to the proof, while the zkVM-internal identifier is a private input, maintained by clients and free to change with every release. The proof must attest that the two are linked, so that the stable `program_hash` genuinely commits to what was executed. The exact mechanism is an open design question.

Native rollups using the `NATIVE_PROGRAM` sentinel sidestep this entirely: the sentinel just says "whatever L1 currently accepts", and the accepted set is itself a client-side artifact that updates with zkVM releases.

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
- `proofs`: a list of `(program_hash, backend_type)` pairs, one per proof in the sidecar. Each `program_hash` is a `bytes32` identifying the guest program for that specific zkVM backend (see [terminology note](#how-rollups-verify-proofs-today)). Each `backend_type` is a `uint8`. The length of this list determines `proof_count`.
- `public_values_hash`: a `bytes32` hash of the program's public output (shared across all proofs, since all backends prove the same statement).
- `blob_versioned_hashes`: same semantics as [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844); used when the transaction also carries payload blobs (e.g. [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) block data).

The CL-level `Proof` carries raw `public_values` bytes; the transaction body and EVM opcodes only expose `public_values_hash`. The contract reconstructs the expected bytes and compares hashes. Two invariants tie the two views together (checked by any node that handles the sidecar: on mempool propagation, and again by the builder when assembling the block):

- `sidecar[i].public_input.program_hash == proofs[i].program_hash` and `sidecar[i].backend_type == proofs[i].backend_type`.
- `sha256(sidecar[i].public_input.public_values) == public_values_hash`.

These are what let `has_valid_proof(program_hash, public_values_hash)` look up stored proofs by `(program_hash, sha256(public_values))`. See [Proof propagation](#proof-propagation) for how proofs reach the builder and how the L1 block proof covers them.

### Opcodes

New opcodes read the proof-carrying transaction's fields, following the same pattern as `ORIGIN`, `GASPRICE`, and `BLOBBASEFEE` (`G_base` cost). All return zero for non-proof-carrying transactions.

| Opcode | Input | Output | Description |
|--------|-------|--------|-------------|
| `PROGRAMHASH` | `index` | `program_hash` (`bytes32`) | Program hash for the i-th proof. Indexed like `BLOBHASH`; returns `bytes32(0)` if `index >= PROOFCOUNT()` |
| `PUBVALUESHASH` | none | `public_values_hash` (`bytes32`) | Hash of the program's public output (shared across all proofs) |
| `PROOFCOUNT` | none | `proof_count` (`uint8`) | Number of distinct zkVM proofs verified by the CL |

A custom rollup iterates with `PROOFCOUNT()` and checks each `PROGRAMHASH(i)` against its own whitelist.

For native rollups, `PROGRAMHASH(i)` returns a well-known sentinel value (e.g. `bytes32(1)`) when the i-th proof uses a program that L1 currently accepts for its own EVM execution proofs. This way the contract checks `PROGRAMHASH(i) == NATIVE_PROGRAM` without storing specific per-zkVM hashes, and automatically follows L1 upgrades. Whether the sentinel is returned depends on the L1-accepted set at the block being executed; this set is part of the client-side `ProofEngine` configuration and updates via client releases.

### Multi-proof

The `proofs` list lets each rollup pick its own security/cost trade-off: `[(hash, SP1)]` is a single proof, `[(hash_sp1, SP1), (hash_risc0, Risc0)]` requires the same statement to be independently proven by both before the CL accepts the transaction. The contract reads `PROOFCOUNT()` and enforces its own minimum.

This replaces contract-level multi-proof orchestration (like Taiko's `ComposeVerifier` requiring both SGX and a ZK verifier) with a protocol-level mechanism. Because `proofs` is in the signed transaction body, it cannot be tampered with.

### Proof propagation

> This section is a very early work in progress. The design is not settled.

The proof must reach a builder through the mempool, but needs no long-term availability. The proposed approach is an **ephemeral sidecar**: the proof travels alongside the transaction like an [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) blob sidecar, but the builder strips it before block inclusion, folds it into the recursive L1 block proof, and discards it. Validators see only the transaction body (the `proofs` list and `public_values_hash`) plus the L1 block proof; they never need the raw proof bytes. The L1 block proof thus recursively covers every proof-carrying transaction in the block (post-quantum proofs may be large enough that L1 is limited to one proof per slot).

**Size.** EIP-8025 sets `MAX_PROOF_SIZE = 300 KiB`. With geth's 1 MiB blob-pool cap, `len(proofs)` is effectively capped at 3 for mempool propagation. Any blobs the transaction also carries eat into the same 1 MiB budget and lower the effective cap.

## Impact on existing rollups

The table below reports Solidity SLOC (non-blank, non-comment source lines) for each project's onchain contracts, split between "core" rollup logic and the proof verification stack that native proof verification would retire.

| Project | Proof system | Core SLOC | Retired SLOC | % retired |
|---|---|---:|---:|---:|
| Arbitrum | Optimistic, WASM VM | 19,034 | 8,181 | 43.0% |
| Base | Optimistic, MIPS VM | 17,426 | 8,907 | 51.1% |
| ZKsync Era | Validity, EraVM | 10,823 | 2,379 | 22.0% |
| Linea | Validity, direct EVM | 8,111 | 2,460 | 30.3% |
| Lighter | Validity, no VM (custom circuits) | 5,417 | 1,699 | 31.4% |
| **Total** | | **60,811** | **23,626** | **38.9%** |

These numbers are rough estimates. They cover only on-chain Solidity code and exclude off-chain provers, sequencers, and the guest program behind each `program_hash`. Governance surfaces (multisigs, timelocks, DAO contracts, proxy admins), partner-specific bridges, and proxy boilerplate are excluded from both columns.

Taiko's six-contract multi-verifier stack collapses into a single inbox contract:

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

A single `isTrustedProgram` whitelist replaces both `isProgramTrusted` (SP1) and `isImageTrusted` (Risc0); `minProofCount` replaces `areVerifiersSufficient`.

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

    // SSZ-encode the StatelessValidationResult container:
    //   new_payload_request_root (32 bytes) || successful_validation (1 byte)
    //   || chain_id (8 bytes, little-endian).
    // Must match serialize_stateless_output() in execution-specs.
    bytes memory expectedPublicValues = SSZ.encodeStatelessValidationResult(
        npRoot, true, chainId
    );
    bytes32 expectedPubValuesHash = sha256(expectedPublicValues);

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

A native rollup is simply one whose `programHash` matches what L1 itself accepts; an L1 upgrade (e.g. a fork changing `verify_stateless_new_payload`) propagates automatically. Rollups with custom VMs use the same pattern with a different `programHash`.
