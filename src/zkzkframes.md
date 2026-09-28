# EIP-8288 (zkzkframes)

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [What EIP-8288 is](#what-eip-8288-is)
- [How a native rollup uses it](#how-a-native-rollup-uses-it)
  - [NativeRollup contract](#nativerollup-contract)
- [Relation to the mandatory L1 proof](#relation-to-the-mandatory-l1-proof)
- [Comparison with proof-carrying transactions](#comparison-with-proof-carrying-transactions)
- [Open issues](#open-issues)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## What EIP-8288 is

[EIP-8288](https://eips.ethereum.org/EIPS/eip-8288), "In-mempool signature and proof aggregation", adds a dependency frame mode to [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions. A dependency frame declares one or more `(scheme, data_hash, verification_key_hash)` triples. It executes no EVM code; each triple is a claim that must be proven valid for the block to be valid. Contracts read the declared triples with the existing EIP-8141 introspection instructions.

The proofs themselves never enter the block. Transactions travel in mempool wrappers that carry either the direct proofs or a recursive STARK over them. Mempool nodes recursively aggregate the dependencies of all their pending transactions at a fixed interval, FOCIL inclusion lists carry their own aggregate, and the builder produces one final recursive STARK over the dependencies of the included transactions. The block header commits to that STARK and to the hash of the block's sorted, deduplicated dependency list.

The EIP was merged as a Draft in September 2026 and supports two schemes: LeanSPHINCS signatures and LeanSTARK proofs.

## How a native rollup uses it

A native rollup update declares exactly one dependency:

```text
(LEANSTARK_SCHEME, validation_result_root, verification_key_hash)
```

- `validation_result_root` is the `hash_tree_root` of the `StatelessValidationResult` produced by proving the L2 block with L1's stateless validation program, as defined in the [Specification](./specification.md#proof-validation).
- `verification_key_hash` is the hash of the EVM program's verification key selected from the [EIP-8357 registry](./evm_vk_registry.md), either the current entry or a pinned one.

EIP-8288 only verifies LeanSTARK proofs. If the L2 block is proven with another zkVM, that proof must first be wrapped into the canonical LeanSTARK relation for the selected EVM fork. Declaring a dependency means requiring it, so the native proof is always a single mandatory 1-of-1 proof.

A minimal self-paying transaction looks like this:

```text
frame 0: VERIFY      target = sender, flags = APPROVE_EXECUTION_AND_PAYMENT
frame 1: DEP_VERIFY  data = (LEANSTARK_SCHEME, validation_result_root, verification_key_hash)
frame 2: SENDER      target = NativeRollup, data = advance(params, dependency_frame_index = 1)
```

The `VERIFY` frame runs the smart account's authorization; the dependency frame grants no authority on its own. A sponsored transaction replaces frame 0 with an `only_verify` frame for the sender followed by a `pay` frame for the paymaster. The L2 data still travels in blobs: EIP-8288 proves correctness, not availability, and `BLOBHASH` returns the transaction's versioned hashes in every frame.

### NativeRollup contract

The rollup contract verifies no proof itself. It checks that the transaction declares exactly the dependency it expects for the next L2 block:

```solidity
uint8 constant LEANSTARK_SCHEME = 0x11;
address constant EVM_VK_REGISTRY = 0x0000709b303ef147cee6c13f3af6c5a402da8357;

enum VkPolicy { FollowCurrent, Pinned }
VkPolicy public vkPolicy;
bytes32 public pinnedVkHash;

function advance(BlockParams calldata params, uint256 dependencyFrameIndex) external {
    // Read the declared triple with FRAMEPARAM and FRAMEDATACOPY. Requiring a
    // single 96-byte triple avoids ambiguous matching across frames.
    (uint8 scheme, bytes32 dataHash, bytes32 vkHash) = readDependency(dependencyFrameIndex);
    require(scheme == LEANSTARK_SCHEME);

    // EIP-8357: zero selects the current entry.
    (bytes32 expectedVkHash, uint64 activationTimestamp) =
        readRegistry(vkPolicy == VkPolicy.FollowCurrent ? bytes32(0) : pinnedVkHash);
    require(vkHash == expectedVkHash);

    // L2 ChainConfig: stored chain ID, activation by timestamp only.
    ChainConfig memory l2ChainConfig = chainConfig(chainId, activationTimestamp);

    bytes32 npRoot = computeNewPayloadRequestRoot(
        blockHash, params, getVersionedHashes(params.payloadBlobCount), blockhash(block.number - 1)
    );
    require(dataHash == SSZ.hashTreeRootStatelessValidationResult(npRoot, true, l2ChainConfig));

    blockHash = params.blockHash;
    stateRoot = params.stateRoot;
    blockNumber++;
    stateRootHistory[blockNumber] = params.stateRoot;
}
```

Replay is constrained by state: the expected root commits to the parent L2 block hash and number, the L2 chain ID, the activation timestamp, the L1 anchor, and the blob versioned hashes.

## Relation to the mandatory L1 proof

EIP-8288's recursive STARK only proves that every declared dependency is valid. It does not prove that the L1 transactions executed correctly, that the rollup contract matched the dependency to the right transition, or that the dependency list was correctly extracted from the block. With full-payload validation, validators check those things by executing the block themselves.

This book assumes validators instead verify a mandatory L1 execution proof and do not download the full payload. That proof must therefore also bind EIP-8288's dependency hash and recursive STARK. Validators can either verify both proofs separately against the same dependency hash, or the mandatory L1 proof can recursively verify the EIP-8288 aggregate, leaving a single proof.

## Comparison with proof-carrying transactions

| Concern | [Proof-carrying transactions](./native_verification.md) | EIP-8288 |
|---|---|---|
| Envelope | New transaction type | EIP-8141 frame transaction |
| Proof declaration | Signed `program_id`, backend types, public values hash | Dependency triple in a dependency frame |
| Program identity | Canonical `program_id` | Exact verification key hash from EIP-8357 |
| EVM access | New opcodes | Existing `FRAMEPARAM` and `FRAMEDATACOPY` |
| Proof transport | Ephemeral transaction sidecar | Mempool wrapper |
| Aggregation | Deferred to the mandatory L1 proving path | Recursive, in the mempool, FOCIL, and builder |
| Account authorization | Must be designed into the new type | EIP-8141 `VERIFY`, `APPROVE`, and `SENDER` |
| Multi-proof | Configurable backend threshold | One mandatory proof |

## Open issues

1. **Recursive proving in the mempool.** Every participating mempool node produces a new recursive STARK over its pending transactions once per second, and aggregates are wrapped repeatedly with no bound on recursion depth. It is an open question whether ordinary mempool nodes can realistically do this, rather than only the builder.
2. **Wrapper capacity.** `MAX_LEANSTARK_DEPS_PER_WRAPPER = 1`, yet each node is expected to broadcast one wrapper covering all of its active transactions. As written, two native-rollup transactions cannot share a wrapper.
3. **Frame placement.** EIP-8288's examples put the dependency frame before `VERIFY`, while EIP-8141's public mempool only recognizes four validation prefixes. The layout above places it after the prefix; neither EIP specifies this yet.
4. **Compact validation.** EIP-8288 assumes validators extract dependencies from the full transactions. How its dependency hash and recursive STARK enter the compact payload header and the mandatory L1 proof is not specified.
5. **Alignment with the zkEVM specifications.** EIP-8288 treats leanVM as a black box instead of building on EIP-8025's proof engine and proof objects. It needs to be made concrete with respect to those specifications.
6. **Unfinished parameters.** `AGGREGATED_VK` is still to be defined, with no mechanism for later changes to the verifier relation. The dependency-list hash function is not final, although BLAKE3 is the leading choice, and there is no reference implementation yet.
