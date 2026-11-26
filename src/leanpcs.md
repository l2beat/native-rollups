# EIP-8288 and leanPCS

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [From LeanSTARK to leanPCS](#from-leanstark-to-leanpcs)
- [How leanPCS works](#how-leanpcs-works)
- [How a native rollup uses it](#how-a-native-rollup-uses-it)
  - [Why no recursion wrapper](#why-no-recursion-wrapper)
- [Relation to the mandatory L1 proof](#relation-to-the-mandatory-l1-proof)
- [Open issues](#open-issues)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## From LeanSTARK to leanPCS

[EIP-8288](https://eips.ethereum.org/EIPS/eip-8288), "In-mempool signature and proof aggregation", was merged as a Draft in September 2026. It adds a dependency verification frame mode (`DEP_VERIFY_FRAME_MODE = 4`) to [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions, where frames declare cryptographic claims that must hold for the block to be valid, while the proofs themselves stay out of the block and are aggregated into a single recursive STARK.

The original draft defined two schemes using 96-byte `(scheme, data_hash, verification_key_hash)` triples: `LEANSPHINCS_SCHEME` for post-quantum signatures and `LEANSTARK_SCHEME` for whole STARKs running on [leanVM](https://github.com/leanEthereum/leanVM). For native rollups, `LEANSTARK_SCHEME` assumed that leanVM would move to RISC-V ([leanVM #277](https://github.com/leanEthereum/leanVM/pull/277)) and also serve as L1's own execution zkVM.

That direction changed in late 2026:

- **leanVM stayed minimal.** Its RISC-V exploration ([leanVM #277](https://github.com/leanEthereum/leanVM/pull/277)) and separate [`leanvm-riscv`](https://github.com/leanEthereum/leanvm-riscv) workspace stalled, while leanVM's `main` branch focused on its minimal six-instruction custom ISA over the binary field `GF(2^128)` with Keccak-256 and Lean 4 formal verification. It is purpose-built for signature and PCS aggregation, not general-purpose RISC-V execution.
- **EIP-8288 is splitting into `leanSPHINCS`, `leanPCS`, and `leanMempool`.** Instead of aggregating whole STARKs under an enshrined ISA, the [`leanPCS`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft_leanPCS.md) draft (paired with the [`MULFIELD`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft-mulfield.md) opcode) replaces `LEANSTARK_SCHEME` with protocol-level aggregation of Polynomial Commitment Scheme (PCS) openings alone.

## How leanPCS works

Most of a post-quantum STARK's size and verification cost is its PCS opening proof (folding rounds and Merkle paths). The remaining part, the Polynomial Interactive Oracle Proof (PIOP), is mostly Fiat-Shamir Keccak hashes and finite-field arithmetic. `leanPCS` splits verification along that seam:

- **Off-chain / in-protocol (block aggregate):** Ethereum fixes one finite field, `K = GF(2)[Y]/(Y^64 + Y^4 + Y^3 + Y + 1)` and `E = K[X]/(X^2 + YX + 1)` (isomorphic to `GF(2^128)`, the same field leanVM uses), and a versioned multilinear PCS (starting at version `1`: ZK-WHIR over `E`, with two out-of-domain answers per 64-byte commitment so a root binds to a single polynomial in the list-decoding regime). Senders aggregate their transaction's PCS openings client-side into a single STARK carried in a mode-1 mempool wrapper, and mempool nodes and the builder recursively aggregate all wrappers in the block into one dependency proof. EIP-8288 carries it in the `ExecutionPayload` as `dependency_proof` and commits to it in the header as `dependency_proof_hash` ([EIPs#12549](https://github.com/ethereum/EIPs/pull/12549); the consensus side is [consensus-specs#5858](https://github.com/ethereum/consensus-specs/pull/5858)).
- **On-chain (EVM contract):** Contracts verify the cheap PIOP directly in the EVM using `XOR` (`0x18`) for addition in `E`, the companion `MULFIELD` (`0x1f`, 10 gas) opcode for multiplication in `E`, and `KECCAK256` for Fiat-Shamir, then check that the transaction's dependency frame declared the resulting PCS claim hashes.

`leanPCS` replaces EIP-8288's 96-byte triples with variable-width `scheme || payload` entries (with `get_deps_hash(deps) = keccak256(concat(deps))`):

| `scheme` | `payload` | Holds if |
|---|---|---|
| `LEANSPHINCS_SCHEME` | `msg_hash (32 B) \|\| public_key (32 B)` | `public_key` signs `msg_hash` with leanSPHINCS |
| `PCS_SCHEME` (`0x12`) | `keccak256(PCS_claim)` (32 B) | `PCS_claim` holds (`version \|\| n \|\| commitment \|\| point \|\| value`) |

A second scheme, `PCS_SIG_SCHEME` (`0x13`), lets `VERIFY` frames read claim hashes from an `ARBITRARY` EIP-8141 signature entry so a proof can commit to `compute_sig_hash(tx)`. Native rollup settlement runs in a `SENDER` frame after the operator's `VERIFY` frame, so it uses plain `PCS_SCHEME` in a `DEP_VERIFY` frame placed after the validation prefix (which EIP-8141's `FRAMEPARAM` and `FRAMEDATALOAD` can inspect).

A transaction may declare up to `MAX_PCS_CLAIMS_PER_TX = 256` claims, priced at `PCS_TX_GAS = 5000` plus `PCS_CLAIM_GAS = 1000` per claim (added to both intrinsic gas and EIP-7976's calldata floor). While the field `E` is fixed permanently, future forks can add new PCS versions (`v + 1`), and contracts can migrate a stored commitment by proving that the old and new commitments agree at two random Fiat-Shamir points. That proves both bind the same polynomial, but not that anyone besides the migrator can open the new one.

## How a native rollup uses it

Under the November 2026 [strawmap](https://strawmap.org/), `leanPCS frames` land in **J\*** and feed directly into **`mandatory 1-of-1 proofs`** in **K\*** (built on a formally verified canonical RV64IM guest such as `evm-asm` or Pancake and a formally verified zkVM), followed by `native rollups` in **L\***.

This book assumes that K\*'s mandatory 1-of-1 zkVM becomes natively compatible with `leanPCS`, meaning at least its final proof commits over `GF(2^128)` using `leanPCS`'s ZK-WHIR openings. A native rollup update then looks like this:

```text
frame 0: VERIFY      target = sender, flags = APPROVE_EXECUTION_AND_PAYMENT
frame 1: DEP_VERIFY  data = PCS_SCHEME || keccak256(claim_0) || ... || keccak256(claim_{k-1})
frame 2: SENDER      target = NativeRollup, data = advance(params, dep_frame_index = 1, piop_proof)
```

In `advance`, the rollup contract:

1. Rebuilds the expected `public_input_root` from storage, calldata, `BLOBHASH`, and the L1 anchor, using the `schema_id` and `verification_key_hash` (program hash) from [EIP-8357](./evm_vk_registry.md).
2. Runs the L1 zkVM's **PIOP verifier** (via an on-chain verifier contract) over `(verification_key_hash, public_input_root, piop_proof)` using `MULFIELD`, `XOR`, and `KECCAK256`, which computes the expected `PCS_claim`s.
3. Checks with `FRAMEPARAM` and `FRAMEDATALOAD` that frame `1` has mode `DEP_VERIFY_FRAME_MODE`, scheme `PCS_SCHEME`, and lists `keccak256(claim_j)` for each expected claim.
4. Updates its L2 state.

The heavy PCS opening proofs never enter the EVM or the block body. In a valid block, the `leanPCS` aggregate guarantees that every declared claim holds.

### Why no recursion wrapper

The [`leanPCS` prototype](https://github.com/leanEthereum/leanPCS) verifies a leanVM proof (128 signatures) in the EVM two ways:

- **Unwrapped**, with a PIOP redesigned for the EVM: 8,065 bytes of calldata and about 516k gas, bound by EIP-7976's calldata floor ([`crates/leanvm/univariate`](https://github.com/leanEthereum/leanPCS/tree/main/crates/leanvm/univariate)).
- **Wrapped**: a second SNARK (π2) proves that the first proof's (π1) verifier accepts, and the contract only verifies π2: 577 bytes and about 63k gas, with 7 PCS claims ([`crates/leanvm/rec`](https://github.com/leanEthereum/leanPCS/tree/main/crates/leanvm/rec)).

Native rollups prefer the unwrapped verifier despite the higher cost:

- **Equivalence with L1.** π2 is an L2-only recursion circuit, with its own soundness and its own bugs, and an extra proving step for the operator. Without it, the guest program, the zkVM's proving circuit, and the PCS layer are those of L1. The onchain PIOP verifier is still a separate implementation from the one L1 validators run, so a bug in it would accept an invalid L2 state transition without invalidating the L1 block: it should be a single shared contract, formally verified against K\*'s PIOP specification.
- **The cost is per L2 block.** An `advance` already spends about 121k gas plus blob gas, and today's rollups pay roughly 250k to 500k gas to verify a Groth16 or Plonk proof, so half a million gas is comparable. K\*'s PIOP, however, is not designed for EVM verification the way the prototype's is, so its unwrapped cost is likely higher and is not measured yet. If it is too high, a wrapper remains possible, at the price of the first point.

If K\*'s mandatory 1-of-1 zkVM ends up using a different field or PCS than `leanPCS`, avoiding a wrapper would instead require exposing K\*'s verifier directly at the protocol layer (as in [Proof-carrying transactions](https://ethresear.ch/t/native-proof-verification/24798) or EIP-8079), rather than going through `leanPCS`.

## Relation to the mandatory L1 proof

The `leanPCS` aggregate proof only proves that every declared `leanSPHINCS` signature and `PCS_claim` in the block is valid. It does not prove that the L1 transactions executed correctly, that the PIOP verifier accepted, or that the dependency list was extracted honestly from the block. With full-payload validation, validators check those things by executing the block themselves.

Under K\*'s mandatory 1-of-1 execution proofs, validators do not download the full payload. The mandatory L1 block proof must therefore also bind `block_deps_hash` and cover (or recursively verify) the block's `leanPCS` aggregate proof, leaving validators with a single proof per block.

## Open issues

1. **K\*'s mandatory 1-of-1 zkVM is not selected or `leanPCS`-native yet.** Current EIP-8025 optional-proof zkVMs (SP1, ZisK, OpenVM, Pico, Lambda VM) all prove RV64IM over prime fields (KoalaBear, Goldilocks, BabyBear), not `leanPCS`'s `GF(2^128)` binary field + ZK-WHIR. Direct unwrapped PIOP verification for native rollups requires K\*'s 1-of-1 zkVM to adopt `leanPCS` natively (at least in its final proof layer). Otherwise native rollups face a choice between an L2-only π2 wrapper or a separate protocol hook for K\*'s verifier.
2. **Binding the on-chain PIOP verifier in EIP-8357.** Under `LEANSTARK_SCHEME`, consensus enshrined the zkVM verifier, so EIP-8357 only had to record `(verification_key_hash, schema_id)`. Under `leanPCS`, the PIOP verifier is an EVM contract, so how immutable `FollowCurrent` rollups survive PIOP or `leanPCS`-version upgrades without governance is open until K\*'s verifier stabilizes (see [EIP-8357: Adapting for leanPCS](./evm_vk_registry.md#adapting-for-leanpcs)).
3. **Unfinished `leanPCS` and `MULFIELD` specs.** Both [`eip-draft_leanPCS.md`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft_leanPCS.md) and [`eip-draft-mulfield.md`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft-mulfield.md) are still early drafts outside `ethereum/EIPs`: version `1` (ZK-WHIR over `E`, supported `n`, zero-knowledge blinding) is not fully specified yet, the draft moved to `GF(2^128)` in November while the prototype and its benchmarks still use `GF(2^192)`, and `PCS_TX_GAS` / `PCS_CLAIM_GAS` depend on a block aggregator that is not benchmarked yet.
4. **Recursive proving in the mempool.** As in EIP-8288, participating mempool nodes recursively aggregate pending mode-1 wrappers at a fixed interval, with unbounded recursion depth. Whether ordinary mempool nodes can realistically sustain this, rather than only builders, remains open.
5. **Compact validation and payload integration.** EIP-8288 now carries the dependency proof in the `ExecutionPayload` ([EIPs#12549](https://github.com/ethereum/EIPs/pull/12549), with the consensus side in [consensus-specs#5858](https://github.com/ethereum/consensus-specs/pull/5858)), but how `block_deps_hash` and `dependency_proof_hash` enter K\*'s compact payload header and mandatory L1 proof is still unspecified.
6. **Frame mode name.** This book follows the `leanPCS` draft and EIP-8288 in calling mode `4` the `DEP_VERIFY` frame. [EIP-8504](https://github.com/ethereum/EIPs/pull/12672), the minimal leanSPHINCS proposal for Hegotá, keeps the same value and triple layout but calls it a proof frame (`PROOF_FRAME_MODE`), and `#pq-el` discussions continue on whether a proof frame should target a verifier contract directly. How claims are carried in EIP-8141 frames may still change before inclusion.
7. **Blob-carrying dependency transactions.** An `advance` carries its L2 block's data in blobs and its PCS claims in a dependency frame of the same transaction, so the mempool path for dependency-bearing transactions must carry blob transactions. [EIP-8437](https://eips.ethereum.org/EIPS/eip-8437) specifies it: wrappers carry them with the blob data elided, and peers fetch it as [EIP-8070](https://eips.ethereum.org/EIPS/eip-8070) cells. No devnet has tested it yet: daisugi-devnet-0, the only EIP-8288 devnet, cannot include blobs at all (see the [daisugi spike](https://github.com/l2beat/native-rollups/tree/main/spikes/daisugi)).
