# Native rollups built on EIP-8288

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Summary](#summary)
- [Scope and assumptions](#scope-and-assumptions)
- [What EIP-8288 replaces](#what-eip-8288-replaces)
- [The native-execution dependency](#the-native-execution-dependency)
  - [Exact EIP-8288 dependency](#exact-eip-8288-dependency)
  - [EVM VK registry](#evm-vk-registry)
  - [Registry update boundary](#registry-update-boundary)
  - [Automatic and pinned policies](#automatic-and-pinned-policies)
  - [Public output](#public-output)
  - [One mandatory proof](#one-mandatory-proof)
- [Frame transaction](#frame-transaction)
  - [Self-paying smart account](#self-paying-smart-account)
  - [Sponsored transaction](#sponsored-transaction)
- [NativeRollup contract](#nativerollup-contract)
- [End-to-end lifecycle](#end-to-end-lifecycle)
- [Relationship to the mandatory L1 proof](#relationship-to-the-mandatory-l1-proof)
- [Data availability](#data-availability)
- [Security boundaries](#security-boundaries)
- [Verifier evolution](#verifier-evolution)
- [Unresolved upstream issues](#unresolved-upstream-issues)
- [Trade-offs](#trade-offs)
  - [Advantages](#advantages)
  - [Costs](#costs)
- [Minimum required delta](#minimum-required-delta)
- [Conclusion](#conclusion)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

> **Exploratory architecture, 30 July 2026.** This chapter asks what the
> native-rollup design would look like if it used
> [EIP-8288](https://github.com/ethereum/EIPs/pull/11772) as its complete
> application-proof verification primitive. It is not the main proposal and
> does not imply that EIP-8288 has been accepted. The reviewed EIP-8288 revision
> is [`9c67268`](https://github.com/ethereum/EIPs/pull/11772/commits/9c67268d7bac54eb24c787996e9f1045c10666c4);
> the PR remains open and unmerged. Its dependency,
> [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141), is also a draft.

## Summary

The design is coherent, but EIP-8288 is not merely an alternative envelope for
the proof-carrying transaction proposed elsewhere in this book. Adopting it
means adopting its full proving architecture:

- an EIP-8141 frame transaction declares a compact proof dependency;
- the proof travels in an EIP-8288 mempool wrapper rather than a transaction
  sidecar;
- mempool nodes recursively aggregate active dependencies;
- FOCIL participants carry aggregates;
- the builder produces the final recursive STARK committed by the L1 block;
- the native-rollup contract reads the declared dependency through EIP-8141
  frame introspection and checks that it represents the exact L2 state
  transition it expects.

No dedicated proof-carrying transaction, generalized EIP-8025 `ProofEngine`,
proof-specific EVM opcode, or verification-key wildcard is needed.

The EIP-8288 dependency carries one exact EVM verification-key value. A
protocol predeploy at a fixed address stores one canonical, fork-specific EVM
VK and activation timestamp for each registered L1 feature fork, together with
a pointer to the current key. Each native rollup chooses one of two policies:

- follow the registry's current VK automatically when a hard fork updates it;
  or
- pin a historical VK, retaining that fork's EVM semantics at its own risk.

The dependency remains exact and directly verifiable by EIP-8288. The VK
itself identifies the fork-specific EVM proof relation; there is no wildcard,
generic program identity, or separate onchain fork identifier.

The native dependency is a mandatory 1-of-1 proof. EIP-8288 has no threshold
semantics: every declared dependency must verify, and declaring several
dependencies means requiring all of them. This architecture therefore does not
reproduce the generalized proposal's configurable 2-of-3 backend policy.

EIP-8288 also does not replace the mandatory L1 execution proof assumed by this
book. Its recursive STARK proves only a flat set of cryptographic dependencies;
it does not prove the L1 state transition. If validators no longer download and
execute full payloads, the mandatory L1 proof must still prove ordinary block
validity, including the EIP-8288 dependency rules.

Conceptually:

```text
L2 execution proof
        |
        v
EIP-8288 native dependency
        |
        v
mempool / FOCIL / builder recursive aggregate
        |
        v
mandatory L1 execution proof
        |
        v
validator
```

## Scope and assumptions

This thought experiment makes the following assumptions:

1. EIP-8141 frame transactions are available.
2. EIP-8288 dependency frames, wrappers, recursive aggregation, and
   block-validity rules are available.
3. Every supported L1 feature fork has one canonical, fork-specific 32-byte
   EIP-8288 verification-key value.
4. A protocol predeploy maps each exact EVM `verification_key` to that fork's
   activation timestamp and identifies one key as current.
5. Future hard forks append one new current EVM key while retaining historical
   entries. Rollups may follow the current entry automatically or assume
   responsibility for a pinned historical entry.
6. Each native-rollup update declares exactly one mandatory native proof
   dependency. It is not a threshold over alternative backends.
7. The mandatory L1 proof and compact payload-header path needed for validators
   to stop downloading full payloads also exists.

The chapter does not design the recursive prover. Registry entries and current
key changes are protocol decisions activated by hard forks, as specified in
the standalone
[L1 EVM verification-key registry EIP draft](./evm_vk_registry.md).

## What EIP-8288 replaces

| Concern | Generalized proof-carrying transaction | EIP-8288-native design |
|---|---|---|
| Transaction envelope | New proof-carrying transaction type | EIP-8141 frame transaction |
| Proof declaration | `program_id`, backend metadata, public-values hash | Dependency triple in a `DEP_VERIFY` frame |
| Execution identity | Signed canonical `program_id` field | Fork-specific exact EVM VK selected through the system registry |
| EVM visibility | Proof-specific opcodes | Existing `FRAMEPARAM` and `FRAMEDATACOPY` |
| Proof transport | Ephemeral transaction sidecar | EIP-8288 mode-0/mode-1 wrapper |
| Aggregation | Deferred to the assumed L1 proving path | Required in the mempool, FOCIL path, and builder |
| Account authorization | Must be designed into the new transaction type | EIP-8141 `VERIFY`, `APPROVE`, and `SENDER` |
| Block commitment | Mandatory L1 execution proof | EIP-8288 recursive STARK plus the mandatory L1 execution proof |

The last row is intentionally not a one-for-one substitution. EIP-8288 proves
the validity of declared dependencies. The mandatory L1 proof proves the
validity of the L1 block that contains and consumes them.

## The native-execution dependency

### Exact EIP-8288 dependency

The native-rollup dependency uses the current EIP-8288 LeanSTARK shape:

```text
(
    LEANSTARK_SCHEME,
    validation_result_root,
    verification_key
)
```

where `validation_result_root` commits to the canonical
`StatelessValidationResult` produced by the L1 stateless-validation program,
and `verification_key` is the exact 32-byte field carried by EIP-8288. The
registry does not reinterpret that field. Whether it contains a complete VK or
a commitment remains an
[open EIP-8288 question](https://github.com/ethereum/EIPs/pull/11772#discussion_r3534964770).

The EIP-8288 aggregate verifies the proof against that exact key. It does not
resolve an alias and does not choose a verifier version. This keeps the
dependency relation unchanged and avoids introducing a native-only EIP-8288
scheme.

Every dependency is ultimately a LeanSTARK proof because that is the scheme
EIP-8288 verifies. If the concrete L1 execution prover uses another backend,
its proof must first be wrapped into the canonical LeanSTARK relation for the
selected EVM fork. EIP-8288 sees that fork's one exact outer
`verification_key` and one mandatory proof.

### EVM VK registry

A protocol-defined `EVM_VK_REGISTRY` system contract is predeployed at a
fixed address. It is canonical L1 state, readable by every EVM contract. This
follows the established fixed-address system-contract pattern used by
[EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) and
[EIP-2935](https://eips.ethereum.org/EIPS/eip-2935). Its exact interface and
fork-owned update path are developed in the
[registry EIP draft](./evm_vk_registry.md).

The predeploy's minimal read interface takes one 32-byte query:

- zero requests the current EVM verification key; and
- a nonzero value requests that exact historical or current verification key.

It returns:

```text
verification_key
|| activation_timestamp
```

An unregistered key reverts. A Solidity library can wrap the raw `STATICCALL`
for application code:

```solidity
struct EvmVkRecord {
    bytes32 verificationKey;
    uint256 activationTimestamp;
}

function readEvmVk(bytes32 query)
    view returns (EvmVkRecord memory result)
{
    (bool success, bytes memory output) =
        EVM_VK_REGISTRY.staticcall(abi.encodePacked(query));
    require(success && output.length == 64);
    (
        result.verificationKey,
        result.activationTimestamp
    ) = abi.decode(output, (bytes32, uint256));
}
```

The wrapper is illustrative; the raw predeploy interface and the following
semantics are defined by the registry EIP:

- each registered VK binds one deterministic, fork-specific EVM relation;
- the key's record supplies the activation timestamp needed for `ChainConfig`,
  with no activation block number;
- the current pointer supplies the default key for automatic-following
  rollups; and
- exact-key lookup keeps historical entries available to pinned rollups.

The registry stores the exact 32-byte EIP-8288 values and activation
timestamps. This keeps state bounded per registered fork.

### Registry update boundary

The registry is a system contract because its mapping defines the canonical
relationship between concrete VKs and L1 native-execution semantics. A normal
application-owned registry would make every native rollup depend on the
administrator of that application contract.

Registration intentionally requires a hard fork. The fork appends one key and
changes the current pointer. The previous entry remains available by exact-key
lookup.

Each fork that changes the registry defines one exact update.
In the fork-transition block, clients call the predeploy once as
`SYSTEM_ADDRESS`, after the EIP-4788 and EIP-2935 pre-execution calls and before
user transactions. There is no transaction-callable administrator or generic
registration path. Under EIP-7928, this system call's state accesses are
recorded in the activation block's BAL with block access index `0`. Its gas is
split between ordinary execution gas and the EIP-8037 state-gas reservoir.
Missing code or call failure invalidates the transition block, following the
checked system-call practice of EIP-7002 and EIP-7251; silently skipping this
one-time update would leave the registry on the wrong current key.

This does not prevent ordinary verifier maintenance. Nodes may replace buggy
or optimized verifier code in a normal client release when the replacement
preserves the exact protocol-defined verification relation and
`verification_key`. A new
key or consensus-observable relation instead requires a new append-only
registration in a fork.

### Automatic and pinned policies

Each native-rollup contract selects one policy:

```solidity
enum VkPolicy {
    FollowCurrent,
    Pinned
}

VkPolicy public vkPolicy;
bytes32 public pinnedVerificationKey;
```

Under `FollowCurrent`, the contract queries the registry with the all-zero
word and accepts only the returned exact current `verification_key`. A
hard-fork registry transition therefore moves the rollup automatically to the
new EVM key and activation timestamp without changing the rollup's code or storage.
"Automatic" describes the rollup contract; the Ethereum registry change is not
forkless.

Under `Pinned`, the contract queries the exact stored `pinnedVerificationKey`
and accepts only that entry. When L1 advances, the rollup retains both the old
key and its historical activation timestamp, deliberately preserving that
fork's EVM semantics. Once an entry is no longer current, L1 does not maintain
or monitor it. The rollup assumes all risk for detecting a vulnerability,
pausing, and changing its pin. It may later change its pin or return to
automatic following.

This is a rollup-selection policy over exact VKs, not proof-threshold
verification. Each included update still carries one mandatory proof under one
exact key.

### Public output

The second word reuses the existing native-rollup proof output:

```text
validation_result_root =
    hash_tree_root(SszStatelessValidationResult(
        new_payload_request_root,
        successful_validation = true,
        chain_config
    ))
```

The exact fields and root computation are unchanged from
[Specification](./specification.md#proof-validation):

- `new_payload_request_root` commits to the L2 execution payload, payload-blob
  versioned hashes, L1 anchor, and execution requests;
- `successful_validation` must be `true`;
- `chain_config` contains the stored L2 chain ID and the activation timestamp
  returned with the selected current or pinned registry key.

Defining the native dependency's `data_hash` directly as this 32-byte SSZ root
avoids introducing a second application-level hash. The proof must expose the
same root as its public output.

### One mandatory proof

EIP-8288 dependency validity is conjunctive:

```text
valid(transaction) requires valid(dependency[0])
                         and valid(dependency[1])
                         and ...
```

It does not let a contract declare alternatives and ask the protocol to accept
any two of three. The current draft also sets `MAX_STARKS_PER_TX = 1`.

For native rollups this is a useful simplification. A transition declares
exactly one `LEANSTARK_SCHEME` dependency under one registry-recognized
`verification_key`, and that dependency is mandatory. There is no `PROOFCOUNT`,
backend list, or rollup-selected proof threshold.

If backend redundancy is ever wanted, it must be encoded inside the single
native proof relation or introduced by a future protocol change. It is not part
of this architecture.

## Frame transaction

### Self-paying smart account

A minimal self-paying transaction is:

```text
frame 0: VERIFY
         target = sender
         flags  = APPROVE_EXECUTION_AND_PAYMENT
         data   = account authorization

frame 1: DEP_VERIFY
         target = None
         flags  = 0
         value  = 0
         data   = (
             LEANSTARK_SCHEME,
             validation_result_root,
             verification_key
         )

frame 2: SENDER
         target = NativeRollup
         data   = advance(block_params, dependency_frame_index = 1)
```

The `VERIFY` frame validates the smart account's signature and policy, then
calls `APPROVE(APPROVE_EXECUTION_AND_PAYMENT)`. It may inspect the future
dependency and execution frames before approving them. The canonical EIP-8141
transaction signature hash already commits to the complete frame list.

The dependency frame itself executes no EVM code and grants no authority. It
only declares a statement whose proof must be covered by the EIP-8288
aggregate.

The `SENDER` frame runs the rollup call with the smart account as `msg.sender`.
This is the main composability benefit over introducing another ordinary
transaction type: proof-backed rollup updates retain programmable smart
accounts.

### Sponsored transaction

Gas sponsorship separates execution approval from payment approval:

```text
frame 0: VERIFY sender
         flags = APPROVE_EXECUTION

frame 1: VERIFY paymaster
         flags = APPROVE_PAYMENT

frame 2: DEP_VERIFY
         data = native dependency

frame 3: SENDER NativeRollup
         data = advance(block_params, dependency_frame_index = 2)
```

This ordering deliberately puts the dependency after the validation prefix.
The latest EIP-8141 public-mempool rules recognize only specific `VERIFY`
prefixes until a payer is established. The current EIP-8288 examples place a
dependency frame before `VERIFY`, and do not yet reconcile that layout with
EIP-8141's newer validation-prefix rules. Because EIP-8141 introspection can
read future frames, placing `DEP_VERIFY` after approval preserves account
policy without putting an unrecognized frame inside the public validation
prefix.

This ordering is a proposed reconciliation, not a rule already specified by
either draft.

## NativeRollup contract

The rollup contract does not verify a proof. In a valid block, EIP-8288 already
guarantees that every declared dependency is covered by the recursive
aggregate. The contract only checks that the current transaction declares the
exact native-execution statement needed for this state transition.

Illustrative pseudocode:

```solidity
uint8 constant LEANSTARK_SCHEME = 0x11;
address constant EVM_VK_REGISTRY =
    /* protocol predeploy address */;

enum VkPolicy {
    FollowCurrent,
    Pinned
}

VkPolicy public vkPolicy;
bytes32 public pinnedVerificationKey;

function advance(
    BlockParams calldata params,
    uint256 dependencyFrameIndex
) external {
    Dependency memory dependency =
        decodeDependencyFrame(dependencyFrameIndex);
    require(dependency.scheme == LEANSTARK_SCHEME);

    bytes32 registryQuery = vkPolicy == VkPolicy.FollowCurrent
        ? bytes32(0)
        : pinnedVerificationKey;
    EvmVkRecord memory evmVk = readEvmVk(registryQuery);

    require(dependency.verificationKey == evmVk.verificationKey);

    ChainConfig memory l2ChainConfig = ChainConfig({
        chainId: chainId,
        activeFork: ForkConfig({
            activation: timestampForkActivation(
                evmVk.activationTimestamp
            )
        })
    });

    bytes32 newPayloadRequestRoot = computeNewPayloadRequestRoot(
        blockHash,
        params,
        getVersionedHashes(params.payloadBlobCount),
        blockhash(block.number - 1)
    );

    bytes32 expectedResultRoot =
        SSZ.hashTreeRootStatelessValidationResult(
            newPayloadRequestRoot,
            true,
            l2ChainConfig
        );

    require(dependency.dataHash == expectedResultRoot);

    blockHash = params.blockHash;
    stateRoot = params.stateRoot;
    blockNumber++;
    stateRootHistory[blockNumber] = params.stateRoot;
}
```

`decodeDependencyFrame` is only a Solidity helper in this pseudocode. It is not
a proposed opcode. It must use the existing EIP-8141 interfaces:

1. `FRAMEPARAM(0x02, index)` to require `DEP_VERIFY_FRAME_MODE`;
2. `FRAMEPARAM(0x04, index)` to require the expected data length;
3. `FRAMEDATACOPY` to copy and decode the 96-byte dependency triple.

The production contract should also require the intended canonical layout:
for example, exactly one native dependency associated with the `advance` call.
This prevents an account or relayer from exploiting ambiguous duplicate or
cross-frame matching rules.

No `PROGRAMID`, `BACKENDTYPE`, `PUBVALUESHASH`, `PROOFCOUNT`, or `PROOFROOT`
opcode is required. The dependency frame exposes the exact
`validation_result_root` and `verification_key`; the registry selects the exact
current or pinned EVM key and its matching activation timestamp.

## End-to-end lifecycle

1. **Build the L2 block.** The operator constructs the L2 execution payload,
   witness, and EIP-8142-style payload-blob data.
2. **Prove native execution.** A concrete backend proves
   `verify_stateless_new_payload` and outputs the full
   `StatelessValidationResult`.
3. **Select an exact key.** The operator uses a VK accepted by the rollup's
   automatic or pinned policy. The registry supplies that exact
   `verification_key` and the matching current or historical activation
   timestamp.
4. **Construct the frame transaction.** The operator includes the native
   dependency frame, the smart-account approval frame or frames, the
   `NativeRollup.advance` call, and the L2 data blobs.
5. **Broadcast a mode-0 wrapper.** The transaction and direct dependency proof
   enter the EIP-8288 network wrapper.
6. **Validate and aggregate.** Receiving nodes verify the direct proof. On each
   EIP-8288 aggregation interval, participating mempool nodes recursively
   combine their live dependency sets into mode-1 wrappers.
7. **Carry inclusion-list proofs.** If FOCIL is active, inclusion-list
   participants attach recursive aggregates for the dependencies of listed
   transactions.
8. **Build the block aggregate.** The builder combines direct proofs, mempool
   aggregates, and inclusion-list aggregates, removes dependencies for omitted
   transactions, and produces the block's `recursive_stark`.
9. **Prove the L1 block.** The mandatory L1 execution proof proves the normal
   L1 state transition, including extraction of dependencies from the selected
   frame transactions, agreement with `block_deps_hash`, validity of the
   EIP-8288 aggregate, and successful execution of `NativeRollup.advance`.
10. **Validate compactly.** A validator checks the mandatory L1 proof against
    the compact payload header. It does not need the original L2 execution
    proof or the full L1 execution payload.

Steps 6 through 8 are not optional optimizations in the EIP-8288 architecture.
They replace this book's previous assumption that ordinary mempool nodes only
verify and relay unchanged proof sidecars while aggregation is designed later.

## Relationship to the mandatory L1 proof

EIP-8288 states that its recursive circuit does not understand transactions or
blocks. It only verifies a flat list of `(scheme, data_hash,
verification_key)` triples and nested aggregates. Consequently, its
`recursive_stark` does not prove:

- that L1 transactions executed successfully;
- that the resulting L1 state root is correct;
- that a `NativeRollup` contract matched a dependency to the right state
  transition;
- that the dependency list was correctly extracted from the block, unless the
  validator independently has the transactions from which to extract it.

Under ordinary full-payload validation, validators can execute the block and
compute `dependencies(block)` themselves. That is the validation model assumed
by the current EIP-8288 draft.

This book instead assumes validators stop downloading full execution payloads.
The mandatory L1 execution proof must therefore absorb the checks that would
otherwise require those payloads. At minimum, its public input must bind:

- the compact `NewPayloadRequestHeader`;
- EIP-8288's `block_deps_hash`;
- the block's `recursive_stark` or a commitment to it; and
- the resulting L1 execution state.

There are two possible proof compositions:

1. validators separately verify the EIP-8288 recursive STARK and the mandatory
   L1 execution proof, while the L1 proof binds the same `block_deps_hash`; or
2. the mandatory L1 proof recursively verifies the EIP-8288 aggregate, leaving
   validators with one final proof.

Either composition uses EIP-8288 as the complete primitive for application
proof verification. The second proof still exists because it proves L1
execution, not because it reimplements native-rollup proof verification.

The exact additions to `NewPayloadRequestHeader` depend on how EIP-8288's new
header entry is integrated with the future L1 zkEVM specification. They are not
specified by either draft today.

## Data availability

EIP-8288 proves correctness of a dependency; it does not make the L2 block data
available. The frame transaction therefore retains blob versioned hashes and a
blob sidecar:

- the blobs carry the canonical EIP-8142-style encoding of the L2 block access
  list and transactions;
- EIP-8141 makes the outer transaction's blob versioned hashes available
  through `BLOBHASH` in every frame;
- `NativeRollup.advance` uses those hashes when reconstructing
  `new_payload_request_root`;
- the native execution proof checks consistency between its private BAL and
  transaction bytes, the public SSZ roots, and the blob commitments;
- DAS provides the availability guarantee.

The proof wrapper and the data sidecar have different lifetimes. The direct
proof can disappear after it has been folded into the EIP-8288 block
aggregate. The L2 data must remain available under the normal blob/DAS rules.

Current EIP-8141 public-mempool validation bans `BLOBHASH` during the validation
prefix. This does not prevent the design: the account signature commits to the
outer transaction, including its blob versioned hashes, and the later
`NativeRollup` `SENDER` frame reads `BLOBHASH` after payment has been approved.

## Security boundaries

The design separates five decisions:

| Layer | What it establishes |
|---|---|
| EVM VK registry | The selected exact `verification_key` and matching activation timestamp are either current or an explicitly pinned historical entry |
| Native dependency proof | A proof under that exact VK produced `validation_result_root` |
| EIP-8288 block aggregate | Every dependency declared by included transactions has a valid witness |
| NativeRollup contract | The declared root is exactly the expected next L2 transition |
| Mandatory L1 proof | The L1 block, including the preceding checks and resulting state, is valid |

Important consequences:

- **The current registration must be sound.** If a trivial or incorrectly
  constructed guest is registered as the current EVM VK, an attacker can prove
  a false state transition. L1's own mandatory proof path is exposed too.
- **Pinning transfers maintenance risk.** L1 does not monitor, revoke, or make
  continuing soundness claims about historical keys. A pinned rollup must
  detect failures and migrate or pause independently.
- **The current pointer is not proof validity.** Advancing it changes
  automatic-rollup policy; EIP-8288 still verifies each dependency against the
  exact key it declares.
- **The public root must be exact.** Binding only `new_payload_request_root`
  would leave `ChainConfig` unconstrained. The full
  `StatelessValidationResult` root is required.
- **Replay is constrained by state.** The expected root includes the current
  parent L2 block hash, block number, L2 chain ID, selected current or pinned
  activation timestamp, L1 anchor, and blob commitments. EIP-8141's nonce and
  canonical signature hash separately prevent transaction replay.
- **Proof validity is not authorization.** Only the smart account's `VERIFY`
  frame may call `APPROVE`; a dependency frame cannot authorize a sender.
- **A reverted call does not erase proving cost.** EIP-8288 dependencies are
  transaction-body declarations and remain chargeable even if a later frame
  reverts.

## Verifier evolution

Current EIP-8288 does not specify VK stability, rotation, aliases, or upgrade
semantics. It places `verification_key` directly in the dependency triple,
includes that triple in `block_deps_hash`, and passes the key directly to the
scheme verifier. Changing the key therefore changes the dependency. The only
open review discussion about this field asks whether its 32 bytes contain a
complete VK or a commitment to larger canonical verifier data; it does not
define a lifecycle for that key.

The EVM registry supplies that missing lifecycle without changing the EIP-8288
dependency relation. Three kinds of change must remain distinct:

1. **Verifier implementation patch, same relation and VK.** Nodes may replace
   buggy or optimized verifier code exactly as execution clients replace other
   implementations. The accepted mathematical relation is unchanged, so
   neither the dependency nor the registry changes.
2. **Feature fork and new VK.** The fork's Core EIP registers one exact
   `verification_key` and activation timestamp. Automatic rollups follow the new
   current record without a contract upgrade; pinned rollups retain the old
   fork's key, activation timestamp, and EVM semantics.
3. **Verifier-only emergency replacement.** A soundness failure may require a
   hard fork and a new key even if the intended EVM rules are unchanged. The
   replacement is still a new append-only registration; the previous key
   remains available as an unmaintained historical entry.

Current EIP-8288 fixes a protocol-level `AGGREGATED_VK`, but its circuit invokes
the generic LeanSTARK verifier with the dependency's exact key. A new
`verification_key` can therefore avoid changing `AGGREGATED_VK` only if it uses the
same LeanSTARK scheme, canonical key encoding, and resource bounds already
understood by the aggregate. A new cryptographic scheme or verifier relation
would still require an EIP-8288 circuit and protocol change.

Outside native rollups, EIP-8288 applications do not automatically inherit the
EVM registry. They must keep their concrete VK/commitment stable, explicitly
manage a set of accepted keys, or define an equally sound application-specific
semantic registry.

## Unresolved upstream issues

Several issues in the current EIP-8288 PR matter directly to this architecture:

1. **EVM VK registry.** Neither EIP-8288 nor another current L1 proposal
   defines the fixed-address current pointer or the mapping from each exact
   `verification_key` to its activation timestamp. The
   [standalone EIP draft](./evm_vk_registry.md) specifies the intended
   lookup and hard-fork update semantics, but its final address, bytecode,
   deployment transaction, initial entries, and executable tests remain open.
2. **Adding a key.** Every new key must be specified by a distinct Core EIP
   that depends on the registry EIP and defines the exact key and L1-approved
   verification relation.
3. **Fork transition schedule.** Current-pointer updates must be published in
   fork specifications early enough for provers, mempools, and contracts to
   agree across activation boundaries and reorgs.
4. **Compact L1 validation.** The draft assumes validators can extract
   dependencies from full transactions. It does not define how
   `block_deps_hash` and `recursive_stark` enter the compact
   `NewPayloadRequestHeader` or mandatory L1 proof.
5. **EIP-8141 ordering.** EIP-8288's examples place a dependency frame before a
   `VERIFY` frame, while current EIP-8141 public-mempool policy recognizes a
   restricted validation prefix until the payer is set.
6. **Wrapper capacity.** The draft's one-LeanSTARK-per-wrapper limit means a
   wrapper cannot cover multiple native-rollup transactions if each declares a
   STARK dependency, despite the text directing a node to emit one aggregate
   over all active transactions each interval.
7. **Dependency ordering and duplicates.** Reviewers have identified cases in
   which order-sensitive wrapper aggregates cannot be composed into the
   builder's transaction order, and duplicates can disagree with the circuit's
   deduplication. Sorting and deduplication have been suggested but are not in
   the draft.
8. **Recursion depth.** The mempool loop repeatedly wraps prior aggregates and
   therefore relies on high or unbounded recursion depth, not merely depth two.
9. **Verification-key encoding.** The 32-byte LeanSTARK
   `verification_key` field is not yet specified as a complete VK or a
   commitment to canonical verifier data.
10. **Verification-key lifecycle.** The draft defines no stability, rotation,
   alias, wildcard, or version-compatibility rules for either user-supplied
   verification keys or their application policies.
11. **Hash functions.** The draft does not yet select the dependency-list hash;
   its security considerations list Poseidon and BLAKE3 as candidates.
12. **Aggregation versioning.** `AGGREGATED_VK` is still `TBD`, with no specified
   compatibility mechanism for later verifier-relation changes.
13. **Implementation evidence.** The EIP's reference implementation is `TBD`.
14. **FOCIL coupling.** Review has suggested that the FOCIL extension may
    belong in a separate EIP.

These are active draft issues, not defects in a deployed protocol. Relevant
review threads include the discussions of
[duplicate dependencies](https://github.com/ethereum/EIPs/pull/11772#discussion_r3518853411),
[recursive depth](https://github.com/ethereum/EIPs/pull/11772#discussion_r3518906744),
[VK encoding](https://github.com/ethereum/EIPs/pull/11772#discussion_r3534964770),
and
[order-independent aggregation](https://github.com/ethereum/EIPs/pull/11772#discussion_r3547964052).

## Trade-offs

### Advantages

- Reuses the complete EIP-8141/EIP-8288 transaction, proof-propagation, and
  aggregation architecture.
- Preserves programmable smart accounts and separates sender approval from gas
  payment.
- Requires no dedicated proof-carrying transaction type or proof-specific EVM
  opcode.
- Gives contracts a compact consensus-backed proof claim without onchain
  cryptographic verifier calls.
- Reuses EIP-8288's exact LeanSTARK dependency instead of adding wildcard
  semantics or a native-only proof scheme.
- Uses the exact fork-specific VK as the relation identity, without a second
  onchain program or fork identifier.
- Supports automatic rollup adoption at fork boundaries and rollup-controlled
  migration delays.
- Keeps historical keys available to rollups that explicitly assume their
  maintenance risk.
- Matches EIP-8288's simple mandatory 1-of-1 dependency semantics.
- Integrates application proofs with the same recursive packaging intended for
  FOCIL and post-quantum signatures.

### Costs

- Makes ordinary participating mempool nodes recursive provers rather than
  proof-only verifiers and relays.
- Makes proof aggregation an immediate protocol dependency rather than work
  that can be deferred.
- Couples native rollups to LeanSTARK aggregation, EIP-8141, and the unresolved
  EIP-8288 block-header design.
- Adds a protocol system-contract registry and requires a hard fork for every
  key registration and current-pointer update.
- Makes the current registry entry part of L1 and native-rollup security.
- Still requires the mandatory L1 execution proof if validators stop
  downloading full payloads.
- Does not expose a configurable 2-of-3 backend threshold; one native proof
  dependency is mandatory.
- Gives L1 no responsibility for monitoring or reacting to vulnerabilities in
  historical keys; pinned rollups must operate that security lifecycle
  themselves.
- Allows a pinned rollup to keep executing the historical fork's EVM rules
  after L1 has advanced.
- Inherits substantial unresolved mempool, recursion, ordering, resource-limit,
  and versioning questions from a very early draft.

## Minimum required delta

If the EIP-8288 architecture becomes the L1 direction, native rollups need only
the following additions or alignments:

1. predeploy `EVM_VK_REGISTRY` at a protocol-defined fixed address;
2. define its current VK pointer and mapping from exact 32-byte verification
   keys to activation timestamps;
3. define fork-owned, one-time system calls for append-only registration and
   current-pointer updates, plus a Core EIP for every new key;
4. specify whether EIP-8288's 32-byte `verification_key` is the full VK or a
   commitment to canonical verifier data;
5. define `data_hash` as the canonical full
   `StatelessValidationResult` root;
6. let each rollup follow the L1-maintained current entry or explicitly retain
   a pinned historical key, activation timestamp, and EVM fork at its own risk;
7. reconcile dependency-frame placement with current EIP-8141 public-mempool
   prefixes;
8. bind `block_deps_hash` and `recursive_stark` into the compact mandatory L1
   proof path;
9. revise EIP-8288's STARK wrapper limits and dependency canonicalization so
   multiple native-rollup transactions can compose.

Everything else—the frame transaction, account authorization, blob attachment,
dependency introspection, proof wrappers, recursive aggregation, FOCIL
packaging, and builder output—is reused.

## Conclusion

EIP-8288 can serve as the complete application-proof verification primitive
for native rollups. The resulting contract is minimal: reconstruct the expected
`StatelessValidationResult` root, read one exact LeanSTARK dependency, check
its VK against the EVM registry and the rollup's automatic or pinned policy,
and advance state.

The system-contract registry uses the exact fork-specific EVM VK as the
identity and makes current versus pinned selection explicit. Normal rollups
adopt the new key and activation timestamp automatically when an Ethereum hard
fork changes the current pointer. Pinned rollups retain the historical fork's
key, activation timestamp, and EVM semantics, but L1 no longer maintains,
monitors, or responds to vulnerabilities in that key. No wildcard verification
semantics are needed.

This path is nevertheless a larger architectural commitment than the current
proof-carrying-transaction proposal. It adopts recursive mempool proving now,
inherits EIP-8288's early-draft open problems, and still needs the mandatory L1
execution proof required for payload-free validation. It should therefore
remain an explicit alternative until the EIP-8141/EIP-8288 direction and the
EVM VK registry mature.
