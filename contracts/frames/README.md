# Native rollup on frames-devnet-0

Runs the `NativeRollup` contract on a chain with [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions but without EIP-8288 or EIP-8357, such as [frames-devnet-0](https://notes.ethereum.org/@ethpandaops/frames-devnet-0). Three pieces stand in for what the chain lacks:

- `MockDependencyVerifier`: a `DEFAULT` frame calls it with the EIP-8288 dependency triple followed by a proof, here a signature by a trusted prover. `FramesNativeRollup` requires that frame to target the verifier and to have succeeded, and reads the triple from its data.
- The EIP-8357 registry runtime, with its system address replaced by an admin key that registers one EVM verification key hash.
- `frame_introspection.eas`: exposes `TXPARAM`, `FRAMEPARAM` and `FRAMEDATACOPY` to Solidity, which does not support the frame instructions yet.

The L2 blocks are real. `script/l2_node.py` is an L2 node that runs the chain with execution-specs under the L1 stateless validation program's rules, from a genesis holding the system contracts the Specification requires. It keeps the state in memory and serves a JSON-RPC with a mempool. The rollup runs the book's [preconfirmations](../../src/preconfirmations.md) customization, `FramesSequencedRollup`: only its sequencer posts blocks, against a bond. The operator asks the node for each block, anchored to an L1 block a few slots behind the head. The node validates every block with `run_stateless_guest` and signs the dependency only if the program accepts it: a trusted signer instead of a zkVM proof, attesting to the real statement. It adds the block to its chain at once, preconfirmed with the sequencer's signature, and the operator posts it later.

A native rollup runs its L1's rules, and this L1 has EIP-8141, so the program is execution-specs `projects/zkevm` merged with `eips/bogota/eip-8141`, which EEST labels as the `Bogota` pseudo-fork (Amsterdam with EIP-8141). The merge conflicts only where both sides add lines. The node uses EEST for the genesis and to sign claims.

L1 to L2 messages follow the book's [messaging](../../src/messaging.md#l1-to-l2-messaging) and [gas token](../../src/gas_token_deposits.md) designs. The genesis holds the `L2Messenger` predeploy with a pre-minted supply of the gas token, and no other ETH. Users claim messages themselves, with transactions they sign and send to the node's RPC like any other. `script/l2_claims.py` builds them as a wallet would, from L1 data and the L2 RPC alone: it rebuilds the rollup contract's message tree from its `L1MessageSent` events, up to the anchor of the latest L2 block. A claim is a frame transaction from the claimer: `DEFAULT` frames claim the message, then the claimer's `VERIFY` frame approves payment, so a deposit to the claimer pays for its own claim. Unless the messenger already has a root with the message, an earlier `DEFAULT` frame proves the tree's root against the anchor, with the anchor's header and `eth_getProof` proofs, which the EIP-4788 contract lets L2 check. Messages carry a fee, which the sender pays on top of the value, for whoever claims them, so a relayer can claim messages to addresses that cannot claim themselves, such as contracts. Since their calls may run code anyone can change, one transaction could make many such claims fail, so the node only takes them from a claimer that can pay up front, and the relayer funds itself with a message to itself. Recipients claim their own messages for free. Messages also carry a gas limit, which their call gets whoever claims them, in both gas dimensions: a frame transaction pays state gas only out of the frame's budget, so the claim frame needs room for it there too.

L2 to L1 messages go the other way: the messenger's `sendMessage` locks the value back into its supply and appends the message hash to `sentMessages`, and the rollup contract's `claimL2Message` proves that entry against a state root in `stateRootHistory` and pays the value out of its escrow. Any L2 account sends one through the RPC, and `claim-l2-message` proves it with the node's `eth_getProof` against the rollup's latest block, and receives its fee.

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

PRIVATE_KEY=<key> PROVER=<address> GENESIS_HASH=<hash> GENESIS_STATE_ROOT=<root> ROLLUP=$ROLLUP SEQUENCER=<address> \
    forge script script/DeployFrames.s.sol --rpc-url <rpc> --broadcast --slow --skip-simulation

uv run --project <execution-specs@projects/zkevm+eip-8141> python script/l2_node.py serve \
    --state <l2-state> --l1-rpc <rpc> --rollup $ROLLUP --port 8547 --beacon <cl-url>

cast send $ROLLUP "sendMessage(address,uint256,uint256,bytes)" <l2-recipient> <fee> <gas-limit> 0x --value 1ether --rpc-url <rpc> --private-key <key>

uv run --project <execution-specs@projects/zkevm+eip-8141> python script/l2_claims.py \
    --l1-rpc <rpc> --rollup $ROLLUP --l2-rpc http://127.0.0.1:8547 --wallet <recipient key> [--relayer <key>]

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py preconfirm \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> \
    --sequencer-key <key> --prover-key <key> --l2-rpc http://127.0.0.1:8547

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py advance \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> --operator-key <key> --l2-rpc http://127.0.0.1:8547
```

Without `SEQUENCER`, the script deploys `FramesNativeRollup`, which takes blocks from anyone. `preconfirm` builds, validates and preconfirms the next block, and `advance` posts the oldest preconfirmed block the rollup does not have yet. The node keeps the blocks the rollup contract has and those preconfirmed after them in the state file, and replays them when it starts. Its RPC serves the methods wallets and load generators use: balances, nonces, code, storage, `eth_call`, `eth_estimateGas`, `eth_sendRawTransaction`, blocks, receipts, logs and `eth_getProof`. Its `latest` block is preconfirmed and its `safe` block posted, which L2 to L1 claims prove against. `nr_preconfirm`, `nr_nextPost` and `nr_getPreconfirmation` are the operator's.

The two scripts use different execution-specs environments: the operator builds L1 frame transactions with the devnet branch, and the L2 runs the merged program. Each `advance` sends one frame transaction: the operator's `VERIFY` frame, the dependency frame, and a `SENDER` frame calling `advance`. `--corrupt-proof` sends an invalid proof, which makes the dependency frame and `advance` fail.

Each L2 block goes to L1 in [EIP-8142](https://eips.ethereum.org/EIPS/eip-8142) payload blobs, which `script/block_in_blobs.py` encodes, and `advance` binds their versioned hashes. The L1 program does not implement EIP-8142 yet, so the node derives the payload blobs itself, as `engine_newPayload`'s native variant does, and its mock proof attests to the public input a program with EIP-8142 would output. Pass `--submit-rpc` with a Nethermind or Reth RPC to `advance`.

`script/l2_follower.py` rebuilds the chain from L1 alone. For each `BlockAdded` event, it reads the header fields from the `advance` frame, fetches the payload blobs from the consensus layer's `/eth/v1/beacon/blobs/{slot}`, re-executes the block with execution-specs' `state_transition`, and checks the block hash the contract recorded:

```shell
uv run --project <execution-specs@projects/zkevm+eip-8141> python script/l2_follower.py \
    --l1-rpc <rpc> --beacon <cl-url> --rollup <rollup> --genesis <l2-state>
```

`preconfirm` anchors the block to an L1 block two slots behind the head, and the block includes the transactions in the node's mempool. Messages sent by the anchor can be claimed in the next block. An L2 account withdraws with the messenger's `sendMessage`, which `claim-l2-message` claims on L1 once the rollup has the block:

```shell
cast send 0x8079000000000000000000000000000000000001 "sendMessage(address,uint256,uint256,bytes)" <l1-recipient> <fee> <gas-limit> 0x \
    --value 0.1ether --rpc-url http://127.0.0.1:8547 --private-key <key>

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py claim-l2-message \
    --rpc <rpc> --rollup <rollup> --key <key> --index <index> --l2-rpc http://127.0.0.1:8547
```
