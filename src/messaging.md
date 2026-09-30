# Messaging

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [L1 anchoring](#l1-anchoring)
- [L1 to L2 messaging](#l1-to-l2-messaging)
- [L2 to L1 messaging](#l2-to-l1-messaging)
- [Existing rollups](#existing-rollups)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Native rollups exchange messages with L1 using only what the L1 program already supports: no new transaction types and no L2-only system logic. L1 to L2 messages build on an L1 anchor, while L2 to L1 messages build on the L2 state root that the rollup contract stores.

## L1 anchoring

To receive messages from L1, a rollup needs some information about the L1 chain on L2, the most general being an L1 block hash. Placing such a reference on L2 is called anchoring.

Native rollups reuse the `parent_beacon_block_root` field of `NewPayloadRequest` as the L1 anchor. The L1 stateless validation function already runs the [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) system call, which writes this value to the beacon roots predeploy, so L2 contracts can read the anchor without any change to the L1 program.

The rollup contract chooses the value, and the proof binds it through `new_payload_request_root`, so the operator cannot pick a different one. The [reference contract](./specification.md#nativerollup-contract) passes the hash of a recent L1 block, which lets L2 contracts prove any L1 state with storage proofs. A message queue commitment or any other `bytes32` works too.

The anchor enters L2 state through the system call, so it must be known before the block is proven. The operator therefore picks the L1 block number, and the contract requires its hash to be within the `BLOCKHASH` window of the last 256 blocks and the number not to move backwards. Anchoring to the parent of the including block, `blockhash(block.number - 1)`, would tie each proof to the exact L1 block that includes it. Taiko applies the same rules to its anchor, checked by its proofs with a 512-block window.

The trade-off is that the field no longer holds a beacon block root on L2, so a rollup cannot expose both a beacon root and a custom anchor. Most rollups do not support EIP-4788 today anyway (see [L1 vs L2 differences](./l1_vs_l2_diff.md#beacon-roots-storage)).

Alternatives that were considered and dropped:

- A dedicated `L1_ANCHOR` predeploy written by its own system call, modeled on [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935). The extra system call is not part of the L1 program, so it would require a different program.
- Passing arbitrary bytes as in-memory context, as in [this proposal](https://hackmd.io/@peter-scroll/rJSKJAFnyx). Reading it requires an L2-only precompile that does not exist on L1.

## L1 to L2 messaging

The rollup contract stores the hash of each L1 to L2 message in its own storage, like the `pendingL1Messages` queue of the reference contract, and emits the full message so that relayers can deliver it. On L2, a messenger contract verifies a storage proof of the message against the anchored L1 block hash, marks it as claimed, and calls the destination. For the duration of the call, it exposes the original L1 sender so that the destination can authenticate it, as Linea's `sender()` and Taiko's `context()` already do. Alternatively, the sender can be passed directly to the destination contract.

Native rollups do not add an unsigned transaction type that executes messages with the L1 sender as `msg.sender`, as the OP and Orbit stacks do. L1 has no such transaction type, so supporting it would require a different program. The cost is that destination contracts must explicitly support the messenger interface for cross-chain authentication, instead of relying on `msg.sender`. Many projects already work this way, and standardizing the interface across projects would reduce this cost.

## L2 to L1 messaging

The rollup contract stores the state roots of recent L2 blocks, like the `stateRootHistory` of the reference contract, which keeps the last 8,191 as [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935) does for L1 block hashes. An L1 contract proves an L2 message against one of them with a storage proof, for example against the storage of an L2 contract that records sent messages, as OP's `L2ToL1MessagePasser` does. Since sent messages stay in that storage, a proof can always use a recent root. Keeping every root instead would create a storage slot on every block, which [EIP-8037](https://eips.ethereum.org/EIPS/eip-8037) charges as state growth, about half the gas of each `advance`. The block's `receipts_root` is also bound by the proof, so a rollup could store it too and prove events instead of storage.

A shallower interface, such as a dedicated root of L2 to L1 messages, cannot be added to the proof's public output without changing the L1 program. [EIP-7685](https://eips.ethereum.org/EIPS/eip-7685) requests could carry such a root, but only by overloading execution-to-consensus requests with L2 to L1 semantics and adding a request type to L1. Cheaper state proofs from the planned binary state tree ([EIP-7864](https://eips.ethereum.org/EIPS/eip-7864)) are expected to reduce the need for a shallower interface.

## Existing rollups

| Stack | L1 anchor | L1 to L2 | L2 to L1 |
|---|---|---|---|
| [OP stack](https://specs.optimism.io/protocol/deposits.html) | The `L1Block` predeploy, written by an unsigned deposit transaction carrying L1 attributes | Unsigned deposit transactions derived from `OptimismPortal` events, with an aliased L1 sender. The `CrossDomainMessenger` builds on them and exposes `xDomainMessageSender` | Proofs against the storage root of the `L2ToL1MessagePasser`, included in each output root |
| Linea | A permissioned relayer submits rolling hashes of L1 messages, checked by the settlement proof | Messages claimed on L2 against the relayed hashes. `sender()` exposes the L1 sender | A custom Merkle tree of messages, verified in the validity proof |
| [Taiko](https://github.com/taikoxyz/taiko-mono/blob/31df8fe8ef7c027840abf122ec36f87c41c3ce94/packages/protocol/docs/Derivation.md) | An `anchorV4` call at the start of every block saves an L1 block number, hash, and state root chosen by the proposer, within 512 blocks of the proposal's L1 block and never moving backwards, checked by the proof | `SignalService` proofs against the anchored L1 state root. The `Bridge` exposes `context()` | The same `SignalService` mechanism in the opposite direction |
| Orbit stack | None: each L1 message becomes its own transaction | Delayed messages in the L1 `Bridge` become unsigned transactions with an aliased L1 sender, force-included after a delay | ArbOS accumulates messages in a Merkle tree whose root is confirmed on L1. The `Outbox` executes them with Merkle proofs |

Each stack relies on transaction types or system logic that L1 does not have, or on a permissioned relayer. Native rollups get the same functionality from the reused anchor, storage proofs, and messenger contracts.
