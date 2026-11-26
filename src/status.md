# Status and roadmap

*Last updated: November 2026.*

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Where things stand](#where-things-stand)
- [L1 dependencies](#l1-dependencies)
- [The case for native rollups](#the-case-for-native-rollups)
- [Timeline](#timeline)
  - [Can anything ship earlier?](#can-anything-ship-earlier)
- [Open question: upgrades with the zkEVM](#open-question-upgrades-with-the-zkevm)
- [Next steps](#next-steps)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Where things stand

- **Reusing the L1 STF** is largely specified in the [Specification](./specification.md), and ethrex has [merged a proof of concept](https://github.com/lambdaclass/ethrex/pull/6418) of the earlier `EXECUTE` precompile design, already on Glamsterdam. The remaining issues are newer EIPs, such as Block-in-Blobs, and compatibility with future L1 upgrades.
- **EIP-8288 and `leanPCS`**: [EIP-8288](https://eips.ethereum.org/EIPS/eip-8288) was merged as a Draft on 9 September 2026, now carries its dependency proof in the `ExecutionPayload` ([EIPs#12549](https://github.com/ethereum/EIPs/pull/12549), with the consensus side in [consensus-specs#5858](https://github.com/ethereum/consensus-specs/pull/5858)), and is splitting into `leanSPHINCS`, `leanPCS`, and `leanMempool`. [`leanEthereum/leanPCS`](https://github.com/leanEthereum/leanPCS) drafts [`leanPCS`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft_leanPCS.md) and [`MULFIELD`](https://github.com/leanEthereum/leanPCS/blob/main/eip-draft-mulfield.md). The book follows `leanPCS`, and assumes that K\*'s mandatory zkVM will commit with it natively, which no zkVM does today. See [EIP-8288 and leanPCS](./leanpcs.md) for why the earlier `LEANSTARK_SCHEME` design was dropped.
- **Proof-carrying transactions**, proposed in [Native proof verification](https://ethresear.ch/t/native-proof-verification/24798) in May 2026, remain the main alternative if mandatory L1 proofs do not become natively `leanPCS`-compatible (since verifying a non-`leanPCS` zkVM on `leanPCS` without a separate wrapper is not possible).
- **The reference implementation**: the [NativeRollup contract](https://github.com/l2beat/native-rollups/tree/main/contracts) runs end to end on a local EIP-8141 devnet (`bogota-devnet-2`), with real L2 blocks, mock proofs, and the EIP-8357 registry in its genesis. It still mocks EIP-8288's earlier 96-byte `LEANSTARK_SCHEME` triple rather than `leanPCS` claims, until `leanPCS` and K\*'s mandatory proof verifier settle. A [public explorer](https://nativerollups.fyi) shows each L2 block and message, and what is still mocked.
- **EIP-8357**, the EVM verification key registry, was merged as a [Draft](https://eips.ethereum.org/EIPS/eip-8357) on 14 October 2026, with a reference implementation in [ethereum/sys-asm#56](https://github.com/ethereum/sys-asm/pull/56) and tests in [ethereum/execution-specs#3466](https://github.com/ethereum/execution-specs/pull/3466). See [EIP-8357](./evm_vk_registry.md) for how it may need to adapt once `leanPCS` and K\*'s mandatory proof verifier are specified.

## L1 dependencies

| Dependency | Needed for | Status |
|---|---|---|
| L1 stateless validation program | The program native rollups prove | In development in [execution-specs `projects/zkevm`](https://github.com/ethereum/execution-specs/tree/projects/zkevm) (now on Bogota). Formal verification for a canonical RV64IM guest explored in `evm-asm` / `stateless-pancaketh` |
| Mandatory 1-of-1 execution proofs | One L1 block proof that covers L1 execution and `leanPCS` claims | Optional proofs ([EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)) declined as an onchain Hegotá EIP on 15 October 2026 (tracked as offchain in Hegotá). Mandatory 1-of-1 proofs are the **K\*** EL headliner on the November 2026 [strawmap](https://strawmap.org/) |
| [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions | The transaction envelope for dependency frames | SFI for Hegotá |
| `leanPCS` frames & `MULFIELD` | Protocol-level PCS opening aggregation and `GF(2^128)` multiplication in the EVM | Draft specs in [`leanEthereum/leanPCS`](https://github.com/leanEthereum/leanPCS). `leanSPHINCS` in **I\***, `leanPCS frames` as the **J\*** EL headliner on the strawmap |
| Compact `NewPayloadRequestHeader` | Validation without full payloads, and the contract's root computation | Removed from optional EIP-8025 ([consensus-specs#5076](https://github.com/ethereum/consensus-specs/issues/5076)). Payload-decoupling options open in `#5729` / `#5753`, assumed to return with mandatory proofs |
| [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) Block-in-Blobs (`payload sampling`) | L2 block data in blobs | Declined for Hegotá on 21 September 2026 (fast payload broadcast split into [EIP-8411](https://eips.ethereum.org/EIPS/eip-8411), CFI for Hegotá, without blobs). `payload sampling` slated for **J\*** on the strawmap |
| [EIP-7928](https://eips.ethereum.org/EIPS/eip-7928) block-level access lists | Part of every proven payload | Last Call, SFI for Glamsterdam |
| [EIP-7997](https://eips.ethereum.org/EIPS/eip-7997) deterministic factory | Deploying the EIP-8357 registry | Last Call, scheduled for Glamsterdam |
| [EIP-7805](https://eips.ethereum.org/EIPS/eip-7805) FOCIL | [Forced transactions](./forced_transactions.md) through L2 inclusion lists, once the L1 stateless validation program supports it | SFI for Hegotá |
| [EIP-7864](https://eips.ethereum.org/EIPS/eip-7864) binary state tree | Optional: cheaper storage proofs for [messaging](./messaging.md) | Draft (`partitioned binary tree` slated for **I\*** on the strawmap) |

## The case for native rollups

If there is no confidence that many existing rollups will become native, is the upgrade worth it? Several teams have expressed interest, including Gnosis, Celo, Linea, Taiko, and Scroll. Even so, native rollups are worth shipping only if the feature is kept minimal, so that its low complexity justifies it even if usage turns out to be low.

Top rollups such as Arbitrum and Optimism have instead expressed interest in an extensible native program, explored in [Native rollups with extensions](./extensions.md). A more ambitious direction is to use native rollups as a form of [execution sharding](./sharding_comparison.md).

## Timeline

Both `leanPCS` (or an equivalent proof-delivery layer) and mandatory 1-of-1 proofs are hard requirements. Under the November 2026 [strawmap](https://strawmap.org/), `leanSPHINCS` lands in **I\*** (2028), `leanPCS frames` and `payload sampling` in **J\*** (2028-2029), `mandatory 1-of-1 proofs` in **K\*** (2029), and `native rollups` in **L\*** (2029-2030).

### Can anything ship earlier?

Probably not without weakening the security model. The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) suggested shipping native rollups through re-execution instead of ZK proofs. Re-execution, however, only supports optimistic rollups with bisection games, which still require a long challenge period for withdrawals, something all rollups are trying to move away from.

Once `leanPCS` lands in J\*, rollups could in principle verify proofs onchain before mandatory L1 proofs ship in K\*, as a testing ground where a bug affects only opting-in rollups and not L1. That would still require either a `leanPCS`-native zkVM or a temporary wrapper, and would not yet share L1's mandatory execution prover.

## Open question: upgrades with the zkEVM

How will upgrades work with the zkEVM? Suppose a bug is found: how do nodes upgrade? Today, most patches are backwards compatible until the bug is hit in production. With the zkEVM, the verification key needs to change, and old proofs can no longer be verified. What does the runbook for this scenario look like?

## Next steps

1. Follow `leanPCS` and `MULFIELD` ([`leanEthereum/leanPCS`](https://github.com/leanEthereum/leanPCS)) into formal EIPs and confirm whether K\*'s mandatory 1-of-1 zkVM can natively emit `leanPCS` claims (so native rollups can verify its unwrapped PIOP without a π2 wrapper, see [EIP-8288 and leanPCS](./leanpcs.md#open-issues)).
2. Keep [EIP-8357](./evm_vk_registry.md) moving through review and tests (`sys-asm#56`, `execution-specs#3466`), and revisit how it binds the onchain PIOP verifier once K\*'s 1-of-1 zkVM settles.
3. Specify how K\*'s mandatory 1-of-1 L1 proof binds and absorbs the block's `leanPCS` aggregate proof (`dependency_proof`).
4. Track `payload sampling` ([EIP-8142](https://eips.ethereum.org/EIPS/eip-8142)) for **J\*** now that it was declined for Hegotá and [EIP-8411](https://eips.ethereum.org/EIPS/eip-8411) split fast payload broadcast off blobs, or identify another way to bind L2 block data to blobs.
5. Replace the reference implementation's mock proofs with real zkVM proofs over `leanPCS` once the proof format, verifier interface, and consensus/execution public inputs settle.
6. Research [native rollups with extensions](./extensions.md).
