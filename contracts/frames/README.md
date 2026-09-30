# Native rollup on frames-devnet-0

Runs the `NativeRollup` contract on a chain with [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions but without EIP-8288 or EIP-8357, such as [frames-devnet-0](https://notes.ethereum.org/@ethpandaops/frames-devnet-0). Three pieces stand in for what the chain lacks:

- `MockDependencyVerifier`: a `DEFAULT` frame calls it with the EIP-8288 dependency triple followed by a proof, here a signature by a trusted prover. `FramesNativeRollup` requires that frame to target the verifier and to have succeeded, and reads the triple from its data.
- The EIP-8357 registry runtime, with its system address replaced by an admin key that registers one EVM verification key hash.
- `frame_introspection.eas`: exposes `FRAMEPARAM` and `FRAMEDATACOPY` to Solidity, which does not support the frame instructions yet.

The L2 blocks are real. `script/l2_node.py` builds them under the L1 stateless validation program's rules with EEST, from a genesis holding the system contracts the Specification requires, and anchors each block to the L1 block the operator picks. It validates every block with `run_stateless_guest` and signs the dependency only if the program accepts it: a trusted signer instead of a zkVM proof, attesting to the real statement.

A native rollup runs its L1's rules, and this L1 has EIP-8141, so the program is execution-specs `projects/zkevm` merged with `eips/bogota/eip-8141`, which EEST labels as the `Bogota` pseudo-fork (Amsterdam with EIP-8141). The merge conflicts only where both sides add lines, and EEST needs one change to build stateless inputs for `Bogota`: `blockchain_stateless.py` accepts `fork.name()` `Bogota` alongside `Amsterdam`.

L1 to L2 messages follow the book's [messaging](../../src/messaging.md#l1-to-l2-messaging) and [gas token](../../src/gas_token_deposits.md) designs. The genesis holds the `L2Messenger` predeploy with a pre-minted supply of the gas token, and no other ETH. The node rebuilds the rollup contract's message tree from its `L1MessageSent` events, and each block claims the messages sent up to its anchor. Claims are frame transactions from the node's L2 account: `DEFAULT` frames claim the message, then the account's `VERIFY` frame approves payment, so a deposit to that account pays for its own claim, and the account pays for claims to other accounts once it has funds. The first claim of a block also proves the tree's root, with the anchor's header and `eth_getProof` proofs, in an earlier `DEFAULT` frame, and the others only carry their message's path.

L2 to L1 messages go the other way: the messenger's `sendMessage` locks the value back into its supply and appends the message hash to `sentMessages`, and the rollup contract's `claimL2Message` proves that entry against a state root in `stateRootHistory` and pays the value out of its escrow. The node sends messages from its account on request, and builds the proofs from the L2 state of the rollup's latest block.

## Local network

```shell
kurtosis run github.com/ethpandaops/ethereum-package --enclave frames --args-file frames/kurtosis.yaml
```

Frame transactions work from epoch 1, when the EIP-8141 fork activates. The network runs the four frames-devnet-0 clients. `advance` transactions carry blobs, and on frames-devnet-0 only Nethermind and Reth accept blob-carrying frame transactions, in the EIP-7594 network form. geth rejects that form and accepts the transaction without its blobs, which then makes the payloads it builds invalid until the transaction leaves its pool. ethrex does not support them yet. All four import the resulting blocks.

## Deploy and advance

The L2 genesis comes from the node and stores the rollup contract's address in the messenger, while the rollup contract stores the genesis hash, so the genesis is built for the address the deployer's fourth transaction will create. Foundry's simulation does not charge EIP-8037 state gas, so deployments use the node's gas estimates:

```shell
forge build
ROLLUP=$(cast compute-address <deployer> --nonce $(( $(cast nonce <deployer> --rpc-url <rpc>) + 3 )) | awk '{print $NF}')
uv run --project <execution-specs@projects/zkevm+eip-8141> python script/l2_node.py genesis --state <l2-state> --l1-rollup $ROLLUP

PRIVATE_KEY=<key> PROVER=<address> GENESIS_HASH=<hash> GENESIS_STATE_ROOT=<root> ROLLUP=$ROLLUP \
    forge script script/DeployFrames.s.sol --rpc-url <rpc> --broadcast --slow --skip-simulation

cast send $ROLLUP "sendMessage(address,bytes)" <l2-recipient> 0x --value 1ether --rpc-url <rpc> --private-key <key>

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py advance \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> \
    --operator-key <key> --prover-key <key> \
    --l2-state <l2-state> --zkevm-specs <execution-specs@projects/zkevm+eip-8141>
```

The two scripts use different execution-specs environments: the operator builds L1 frame transactions with the devnet branch, and the L2 runs the merged program. Each `advance` sends one frame transaction: the operator's `VERIFY` frame, the dependency frame, and a `SENDER` frame calling `advance`. `--corrupt-proof` sends an invalid proof, which makes the dependency frame and `advance` fail.

Each L2 block goes to L1 in [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) payload blobs, which `script/block_in_blobs.py` encodes, and `advance` binds their versioned hashes. The L1 program does not implement EIP-8142 yet, so the node derives the payload blobs itself, as `engine_newPayload`'s native variant does, and its mock proof attests to the public input a program with EIP-8142 would output. Pass `--submit-rpc` with a Nethermind or Reth RPC to `advance`.

`script/l2_follower.py` rebuilds the chain from L1 alone. For each `BlockAdded` event, it reads the header fields from the `advance` frame, fetches the payload blobs from the consensus layer's `/eth/v1/beacon/blobs/{slot}`, re-executes the block with execution-specs' `state_transition`, and checks the block hash the contract recorded:

```shell
uv run --project <execution-specs@projects/zkevm+eip-8141> python script/l2_follower.py \
    --l1-rpc <rpc> --beacon <cl-url> --rollup <rollup> --genesis <l2-state>
```

`advance` anchors the block to the latest L1 block, so it claims every message sent so far. `--withdraw <l1-recipient>:<wei>[:<data>]` also sends an L2 to L1 message, which `claim-l2-message` claims on L1 once the rollup has the block:

```shell
uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py claim-l2-message \
    --rpc <rpc> --rollup <rollup> --key <key> --index <index> \
    --l2-state <l2-state> --zkevm-specs <execution-specs@projects/zkevm+eip-8141>
```
