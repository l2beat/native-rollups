# Native proof verification

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Abstract](#abstract)
- [Motivation](#motivation)
- [How rollups verify proofs today](#how-rollups-verify-proofs-today)
  - [Example: Taiko (multi-verifier)](#example-taiko-multi-verifier)
- [Changes to EIP-8025](#changes-to-eip-8025)
- [Standardized program identity (assumption)](#standardized-program-identity-assumption)
- [New EIP: Proof-carrying transactions](#new-eip-proof-carrying-transactions)
  - [Transaction format](#transaction-format)
  - [Opcodes](#opcodes)
  - [Multi-proof](#multi-proof)
  - [Proof propagation](#proof-propagation)
- [Impact on existing rollups](#impact-on-existing-rollups)
- [Impact on native rollups](#impact-on-native-rollups)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Abstract

The overarching goal of this proposal is to massively derisk and simplify L2 bridges, and more generally any onchain application that verifies ZK proofs, by introducing a standard L1 primitive that any project can adopt in place of its bespoke onchain verifier stack. This is achieved through two changes:

1. **Generalize [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)** so the consensus-layer proof verification infrastructure becomes program-agnostic, not tied to EVM execution proofs.
2. **A new EIP** that exposes it to smart contracts through a proof-carrying transaction type and four opcodes (`PROGRAMID`, `BACKENDTYPE`, `PUBVALUESHASH`, `PROOFCOUNT`).

Together, they let any project inherit L1's proof verification infrastructure directly. Contracts commit to stable program semantics and backend families, while verifier implementations are maintained through node software rather than per-project governance upgrades.

## Motivation

Today, every Ethereum rollup maintains bespoke onchain proof verification infrastructure. ZK rollups deploy zkVM verifier contracts, adapter contracts, multi-proof dispatchers, and program whitelisting logic. Optimistic rollups ship their own onchain fraud-proof VMs (Arbitrum's WAVM, Optimism's Cannon MIPS machine) plus the surrounding dispute logic. In both cases every contract is maintained, patched, and upgraded independently in response to bugs in its specific proof system or VM, with each upgrade gated by a custom multisig or DAO. This is slow, risky, and duplicated across the ecosystem.

[EIP-8025](https://eips.ethereum.org/EIPS/eip-8025) introduces zkVM proof verification on Ethereum's consensus layer, but only for L1's own purposes: verifying execution payloads to enable stateless and sublinear validation. Rollups still need their own onchain verifier contracts.

However, the infrastructure that EIP-8025 brings to the CL, the `ProofEngine`, proof gossip, and verification logic, is not inherently L1-specific. If generalized to be program-agnostic and exposed to smart contracts via a new transaction type, any rollup, even non-EVM ones, could offload proof verification to the CL. Verifier implementation fixes that preserve protocol-defined validity semantics can ship exactly like ordinary geth or Nethermind maintenance. This is the same principle behind [native rollups](https://eips.ethereum.org/EIPS/eip-8079), but more generalized: just as native rollups inherit L1's execution environment, native proof verification lets any rollup inherit L1's proof verification infrastructure.

Although this document frames the proposal around rollups, the same primitive serves any contract that verifies a ZK proof onchain: privacy systems, ZK coprocessors, identity, ZK ML, and others.

## How rollups verify proofs today

Each zkVM vendor provides a universal Solidity verifier contract (typically a Groth16 or Plonk check over BN254). The program identity and the public values (any inputs and outputs the circuit commits to) are passed alongside the proof. For SP1:

```solidity
interface ISP1Verifier {
    function verifyProof(
        bytes32 programVKey,         // concrete SP1 program VK
        bytes calldata publicValues, // public values (inputs and/or outputs)
        bytes calldata proofBytes    // the proof
    ) external view;
}
```

**A note on terminology.** SP1 calls `programVKey` a "verification key", but this collides with the zkVM's own circuit verification key. This document keeps them separate:

- **Program identity (`program_id`)**: a standardized, backend-independent `bytes32` commitment to deterministic program semantics. This is the identity exposed to contracts and remains stable across behavior-preserving compiler and verifier changes.
- **Guest implementation**: a concrete implementation of those semantics, such as Ethrex, Reth, or Zesu for L1 stateless validation. Guest implementation and version are verifier metadata, not application identity; implementations fully bound to the same deterministic behavior share one `program_id`.
- **Concrete program VK** (called `programVKey` by SP1 and `imageId` by Risc0): a backend- and build-specific identifier for compiled guest code. [ERE](https://github.com/eth-act/ere) expresses this as each backend's [`zkVMVerifier::ProgramVk`](https://github.com/eth-act/ere/blob/master/crates/verifier/core/src/verifier.rs) associated type. It may change without changing `program_id`.
- **Backend family (`backend_type`)**: the named proof-system or zkVM family, such as SP1, Risc0, OpenVM, or ZisK. This is application-visible and is the unit counted for multi-proof diversity; distinct families may still share dependencies and are not assumed to be perfectly independent.

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

EIP-8025 introduces optional execution proofs for L1 block validation. The infrastructure it brings to the consensus layer (the `ProofEngine`, gossip, verification logic) is L1-specific only because its [`ExecutionProof.public_input`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#new-publicinput) carries a `new_payload_request_root: Root`. Its [`ProofType`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/beacon-chain.md#types) is an opaque `uint8`; the consensus specification deliberately lets peers advertise supported values dynamically.

The exact meaning and registry of `ProofType` are not yet standardized. The current zkboost [`ProofType`](https://github.com/eth-act/zkboost/blob/master/crates/types/src/proof_type.rs) implementation supplies a closed `(guest implementation, zkVM)` enum of its own and loads one concrete program VK for each configured value. Its numeric assignments are implementation-specific rather than a protocol registry.

Recent L1 zkEVM work has made the underlying separation explicit even though the wire-level `ProofType` remains flattened:

- [`ere-guests`](https://github.com/eth-act/ere-guests) gives the stateless validators a shared canonical `StatelessInput` / `StatelessValidationResult` I/O contract, then resolves a guest artifact from the independent pair [`(StatelessValidatorKind, zkVMKind)`](https://github.com/eth-act/ere-guests/blob/master/crates/stateless-validator-test/src/execution/zkvm.rs). `StatelessValidatorKind` currently distinguishes Ethrex, Reth, and Zesu.
- [ERE](https://github.com/eth-act/ere) separately selects a [`zkVMKind`](https://github.com/eth-act/ere/blob/master/crates/catalog/src/zkvm.rs), instantiates its verifier with a backend-specific [`ProgramVk`](https://github.com/eth-act/ere/blob/master/crates/verifier/verifier/src/verifier.rs), and returns backend-neutral `PublicValues`.

This is the guest-implementation / zkVM split needed below EIP-8025, but it is not the semantic split exposed by this proposal. The L1 team's current [proof-type encoding discussion](https://github.com/eth-act/execution-proofs-api/issues/1) identifies an exact `(guest program, zkVM kind + version)` combination, potentially by hashing the zkVM metadata together with its concrete program VK. Such an artifact identifier differs across builds and backends; it is not a backend-independent `program_id`. The encoding issue remains open, and no protocol registry has been adopted.

This EIP adds a generic verification primitive alongside, leaving EIP-8025's existing surface (`ExecutionProof`, `ProofType`, `verify_execution_proof`, `notify_new_payload`, `notify_forkchoice_updated`, `process_execution_proof`, `request_proofs`, `ProofAttributes`) untouched. It reuses the upstream separation while adding the missing semantic layer. EIP-8025's exact `ProofType`, guest implementation and version, concrete program VK, and verifier version remain proof-engine metadata. The application-facing primitive exposes only stable program semantics and the backend family.

The generic primitive exposes two application-relevant axes:

- `program_id`: the shared semantic program identity, independent of backend and verifier version;
- `backend_type`: the backend family, such as SP1 or Risc0.

Conceptually:

```python
class ProofPublicInput(Container):
    program_id: Bytes32
    public_values: ProgressiveByteList

class Proof(Container):
    proof_data: ProgressiveByteList
    backend_type: BackendType
    public_input: ProofPublicInput

def verify_proof(self: ProofEngine, proof: Proof) -> bool: ...
```

As in EIP-8025, `ProgressiveByteList` keeps the SSZ schema independent of operational size limits. Proof handling separately enforces:

```python
assert len(proof.proof_data) <= MAX_PROOF_SIZE
assert len(proof.public_input.public_values) <= MAX_PUBLIC_VALUES_SIZE
```

`MAX_PROOF_SIZE` reuses EIP-8025's limit. The value of `MAX_PUBLIC_VALUES_SIZE` is left open together with the other proof-carrying-transaction resource bounds.

The backend-specific verifier interprets `proof_data`. For L1 zkVM proofs, `backend_type` is the application-facing projection of ERE's `zkVMKind`; it does not replace the more specific proof-engine metadata used to select an exact verifier. This proposal deliberately leaves open how verifier and proof-format versions are selected, including whether versioning is explicit or self-describing. The concrete program VK may be supplied as a private input to the binding proof, but the verified statement must bind it and its behavior deterministically to the public `program_id`.

EIP-8025's [`verify_execution_proof`](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/proof-engine.md#new-verify_execution_proof) can be reimplemented as a thin wrapper over `verify_proof` for code sharing, with no observable change at the gossip layer:

```python
def verify_execution_proof(self: ProofEngine, ep: ExecutionProof) -> bool:
    # Map EIP-8025's execution-proof type into the generic application axes.
    backend_type, program_id = self.resolve_execution_proof_type(ep.proof_type)
    expected_public_values = serialize_stateless_output(StatelessValidationResult(
        new_payload_request_root=ep.public_input.new_payload_request_root,
        successful_validation=True,
        chain_config=self.chain_config,
    ))
    return self.verify_proof(Proof(
        proof_data=ep.proof_data,
        backend_type=backend_type,
        public_input=ProofPublicInput(
            program_id=program_id,
            public_values=expected_public_values,
        ),
    ))
```

The canonical [`serialize_stateless_output`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_guest.py#L27) encoding of [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L197) is used in [Impact on native rollups](#impact-on-native-rollups), since native-rollup contracts must reconstruct it onchain. Block validity remains decoupled from proof verification; the [honest prover guide](https://github.com/ethereum/consensus-specs/blob/master/specs/_features/eip8025/prover.md) is unchanged. Sidecar-arrived proofs (proof-carrying transactions, see [Proof propagation](#proof-propagation)) go through `verify_proof` directly, without the L1 wrapper.

Backend verifier implementations are node software, so maintenance that preserves protocol-defined validity semantics does not require contract changes or a new `program_id`. How incompatible proof formats, verifier changes, or consensus-affecting fixes are identified and coordinated is explicitly out of scope and left for future work.

## Standardized program identity (assumption)

Native proof verification assumes a standardized semantic program identity:

- `program_id` commits to the complete deterministic behavior of the program, not to one compiled artifact.
- The same `program_id` is used by every backend that proves those semantics.
- A concrete canonical program VK may be a private proof input.
- The proof must fully and deterministically bind that concrete VK and its behavior to the public `program_id`.
- Behavior-preserving compiler, dependency, kernel, circuit, and verifier changes retain the same `program_id`.
- A real semantic change requires a new `program_id`, or a fork-scoped definition of a well-known identity such as `NATIVE_PROGRAM`.

This identity layer is a prerequisite assumed by the proposal and will be designed separately. Backend-specific artifact identifiers are concrete program VKs, not substitutes for the shared semantic `program_id`.

## New EIP: Proof-carrying transactions

### Transaction format

```
TransactionType: PROOF_TX_TYPE

TransactionPayloadBody:
[chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit,
 to, value, data, access_list, max_fee_per_blob_gas,
 blob_versioned_hashes, program_id, backend_types, public_values_hash,
 y_parity, r, s]
```

Where:

- `program_id`: the shared `bytes32` semantic identity of the program proven by every backend.
- `backend_types`: a list of application-visible backend families. Each `backend_type` is a `uint8` and MUST be unique within the list, since verifying the same statement twice with the same backend does not add an independent security assumption. The length of this list determines `proof_count`.
- `public_values_hash`: a `bytes32` hash of the program's public output (shared across all proofs, since all backends prove the same statement).

The sidecar carries exactly one `Proof` for each signed backend family, in the same order as `backend_types`.

The CL-level `Proof` carries the raw `public_values` bytes; the transaction body (and the `PUBVALUESHASH` opcode) expose only their hash. The contract reconstructs the expected bytes and compares hashes. Nodes handling the sidecar enforce:

- the sidecar length equals `len(transaction.backend_types)`;
- `sidecar[i].backend_type == transaction.backend_types[i]`;
- `sidecar[i].public_input.program_id == transaction.program_id`;
- `sha256(sidecar[i].public_input.public_values) == transaction.public_values_hash`; and
- every sidecar proof verifies successfully.

These rules bind the EVM-visible semantic claim to the `Proof` objects passed to `verify_proof`.

### Opcodes

New opcodes read the proof-carrying transaction's fields and return zero for non-proof-carrying transactions.

| Opcode | Input | Output | Description |
|--------|-------|--------|-------------|
| `PROGRAMID` | none | `program_id` (`bytes32`) | Shared semantic identity of the proven program |
| `BACKENDTYPE` | `index` | `backend_type` (`uint8`) | Backend family for the i-th distinct backend; indexed like `BLOBHASH` |
| `PUBVALUESHASH` | none | `public_values_hash` (`bytes32`) | Hash of the program's public output (shared across all proofs) |
| `PROOFCOUNT` | none | `proof_count` (`uint8`) | Number of distinct backend families in `backend_types` |

They return zero for non-proof-carrying transactions; `BACKENDTYPE(i)` also returns zero when `i >= PROOFCOUNT()`, with backend value zero reserved. A custom rollup checks `PROGRAMID()` against its program whitelist, then iterates over `BACKENDTYPE(i)` if it wants to constrain the permitted backend families.

For native rollups, `PROGRAMID()` returns the well-known, fork-scoped `NATIVE_PROGRAM` identity. Behavior-preserving guest and verifier upgrades retain this identity. A fork that changes the semantic L1 execution-validation program must define whether it introduces a new identity or updates the fork-scoped meaning of `NATIVE_PROGRAM`.

### Multi-proof

The `backend_types` list lets each rollup pick its own security/cost trade-off: `[SP1]` is a single-backend claim, while `[SP1, Risc0]` requires the same `program_id` and public values to be proven separately by both backend families before the CL accepts the transaction. The contract reads `PROOFCOUNT()` and `BACKENDTYPE(i)` to enforce its own policy.

The protocol enforces backend uniqueness; the contract decides which backend families or combinations are sufficient. Because `backend_types` is in the signed transaction body, this policy-relevant claim cannot be changed by replacing the sidecar.

### Proof propagation

The proofs must reach a builder through the mempool, but need no long-term availability. The proposed approach is an **ephemeral sidecar**: proofs travel alongside the transaction like an [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844) blob sidecar. Mempool nodes and the builder run each proof through `verify_proof` and check the invariants from [Transaction format](#transaction-format) before forwarding or including the transaction.

**Alternative delivery proposal.** The newer [draft EIP-8288 proposal](https://github.com/ethereum/EIPs/pull/11772) describes a different delivery mechanism: compact cryptographic dependencies are declared in [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions, while their witnesses are propagated in mempool wrapper objects. This could become an alternative to the dedicated proof-carrying transaction envelope and ephemeral sidecar described here, but the proposal does not currently assume it. EIP-8288 depends on EIP-8141 Frame Transactions, which are only Considered for Inclusion rather than Scheduled for Inclusion, and EIP-8288 itself remains an unmerged draft. Its current design also defines independent LeanSPHINCS and LeanSTARK verification and recursive aggregation; it does not build on EIP-8025's `ProofEngine`, proof objects, proof gossip, or the related L1 zk execution-proof specifications. Reusing it for native proof verification would therefore require future alignment between those efforts.

This proposal assumes future infrastructure in which a mandatory L1 block proof recursively verifies every proof-carrying transaction proof. Under that assumption, the builder strips and discards the sidecar after folding the verified claims into the L1 block proof. Validators see only the signed `program_id`, `backend_types`, and `public_values_hash` plus the L1 block proof. Designing the aggregation scheme, proving pipeline, and deployment of that mandatory block-proof infrastructure is explicitly out of scope here and remains future work.

**Size.** The current EIP-8025 consensus specification sets `MAX_PROOF_SIZE = 4 MiB` per proof. Native proof verification needs explicit bounds for the number of proofs per transaction and the total sidecar size.

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

These numbers are rough estimates. They cover only on-chain Solidity code and exclude off-chain provers, sequencers, and the guest program behind each `program_id`. Governance surfaces (multisigs, timelocks, DAO contracts, proxy admins), partner-specific bridges, and proxy boilerplate are excluded from both columns.

Taiko's six-contract multi-verifier stack collapses into a single inbox contract:

```solidity
contract TaikoInbox {
    mapping(bytes32 => bool) public isTrustedProgram; // semantic program identities
    mapping(uint8 => bool) public isTrustedBackend;   // permitted backend families
    uint256 public minProofCount;                     // distinct-backend threshold

    function proveBatches(
        BatchMetadata[] calldata metas,
        Transition[] calldata trans
        // _proof parameter removed: verified by the CL
    ) external {
        require(isTrustedProgram[PROGRAMID()], "untrusted program");
        require(PROOFCOUNT() >= minProofCount, "insufficient proofs");
        for (uint256 i = 0; i < PROOFCOUNT(); i++) {
            require(isTrustedBackend[BACKENDTYPE(i)], "untrusted backend");
        }

        bytes memory publicInputs = buildPublicInputs(metas, trans);
        require(PUBVALUESHASH() == sha256(publicInputs), "wrong public values");

        // Accept the batches.
        ...
    }
}
```

A single backend-independent `isTrustedProgram` whitelist replaces both `isProgramTrusted` (SP1) and `isImageTrusted` (Risc0). `isTrustedBackend` and `minProofCount` replace the verifier-address policy in `areVerifiersSufficient`; backend-specific verifier details never enter contract storage.

## Impact on native rollups

The NativeRollup contract from the [ZK specification](./proof_carrying_transactions.md#nativerollup-contract-zk) uses the same pattern. Instead of `PROOFROOT` against a `validation_result_root`, it checks `PROGRAMID`, `PUBVALUESHASH`, and `PROOFCOUNT`; it may additionally inspect `BACKENDTYPE` if it wants a stricter backend policy. It constructs `ChainConfig` as specified in [Proof-carrying transactions](./proof_carrying_transactions.md#chainconfig): the stored L2 chain ID plus the complete active-fork activation read from the parameterless EVM environmental interface.

```solidity
bytes32 constant NATIVE_PROGRAM = bytes32(uint256(1));
uint256 public minProofCount;

function advance(BlockParams calldata params) external {
    bytes32 l1Anchor = blockhash(block.number - 1);

    // Parameterless EVM environmental interface assumed by the
    // proof-carrying transaction specification. Discard the returned
    // L1 chain ID, retain the complete active-fork activation, and use
    // the rollup's stored L2 chain ID.
    ChainConfig memory l2ChainConfig = L1CHAINCONFIG();
    l2ChainConfig.chainId = chainId;

    bytes32 npRoot = computeNewPayloadRequestRoot(
        blockHash, params.feeRecipient, params.stateRoot,
        // ... remaining fields ...
        getVersionedHashes(params.payloadBlobCount),
        l1Anchor, bytes32(0)
    );

    // SSZ-encode the complete StatelessValidationResult, including the
    // ChainConfig and active-fork activation data. This must use the same
    // schema as serialize_stateless_output() in execution-specs.
    bytes memory expectedPublicValues = SSZ.encodeStatelessValidationResult(
        npRoot, true, l2ChainConfig
    );
    bytes32 expectedPubValuesHash = sha256(expectedPublicValues);

    require(PROGRAMID() == NATIVE_PROGRAM, "not the native program");
    require(PROOFCOUNT() >= minProofCount, "insufficient proofs");
    require(PUBVALUESHASH() == expectedPubValuesHash, "wrong public values");

    blockHash = params.blockHash;
    stateRoot = params.stateRoot;
    blockNumber = blockNumber + 1;
    stateRootHistory[blockNumber] = params.stateRoot;
}
```

A native rollup is simply one whose `program_id` is the fork-scoped identity of L1's execution-validation program. Behavior-preserving guest and verifier upgrades propagate without changing the contract. Rollups with custom VMs use the same pattern with their own standardized `program_id`.
