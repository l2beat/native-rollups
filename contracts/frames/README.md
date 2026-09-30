# Native rollup on frames-devnet-0

Runs the `NativeRollup` contract on a chain with [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions but without EIP-8288 or EIP-8357, such as [frames-devnet-0](https://notes.ethereum.org/@ethpandaops/frames-devnet-0). Three pieces stand in for what the chain lacks:

- `MockDependencyVerifier`: a `DEFAULT` frame calls it with the EIP-8288 dependency triple followed by a proof, here a signature by a trusted prover. `FramesNativeRollup` requires that frame to target the verifier and to have succeeded, and reads the triple from its data.
- The EIP-8357 registry runtime, with its system address replaced by an admin key that registers one EVM verification key hash.
- `frame_introspection.eas`: exposes `FRAMEPARAM` and `FRAMEDATACOPY` to Solidity, which does not support the frame instructions yet.

The L2 blocks are real. `script/l2_node.py` builds them under the L1 stateless validation program's rules (execution-specs `projects/zkevm`, Amsterdam) with EEST, from a genesis holding the system contracts the Specification requires, and anchors each block to the L1 block the operator picks. It validates every block with `run_stateless_guest` and signs the dependency only if the program accepts it: a trusted signer instead of a zkVM proof, attesting to the real statement.

L1 to L2 messages follow the book's [messaging](../../src/messaging.md#l1-to-l2-messaging) and [gas token](../../src/gas_token_deposits.md) designs. The genesis holds the `L2Messenger` predeploy with a pre-minted supply of the gas token. The node reads the `L1MessageSent` events of the rollup contract, and each block claims the messages sent up to its anchor, with the anchor's header and `eth_getProof` proofs of their `pendingL1Messages` entries. A funded genesis account pays for the claims, standing in for the book's open question of who claims a user's first deposit.

## Local network

```shell
kurtosis run github.com/ethpandaops/ethereum-package --enclave frames --args-file frames/kurtosis.yaml
```

Frame transactions work from epoch 1, when the EIP-8141 fork activates.

## Deploy and advance

The L2 genesis comes from the node and stores the rollup contract's address in the messenger, while the rollup contract stores the genesis hash, so the genesis is built for the address the deployer's fourth transaction will create. Foundry's simulation does not charge EIP-8037 state gas, so deployments use the node's gas estimates:

```shell
forge build
ROLLUP=$(cast compute-address <deployer> --nonce $(( $(cast nonce <deployer> --rpc-url <rpc>) + 3 )) | awk '{print $NF}')
uv run --project <execution-specs@projects/zkevm> python script/l2_node.py genesis --state <l2-state> --l1-rollup $ROLLUP

PRIVATE_KEY=<key> PROVER=<address> GENESIS_HASH=<hash> GENESIS_STATE_ROOT=<root> ROLLUP=$ROLLUP \
    forge script script/DeployFrames.s.sol --rpc-url <rpc> --broadcast --slow --skip-simulation

cast send $ROLLUP "sendMessage(address,bytes)" <l2-recipient> 0x --value 1ether --rpc-url <rpc> --private-key <key>

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py advance \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> \
    --operator-key <key> --prover-key <key> \
    --l2-state <l2-state> --zkevm-specs <execution-specs@projects/zkevm>
```

The two scripts use different execution-specs branches, since the frame transaction types only exist on the devnet branch and the L2 runs the zkEVM project's program. `advance` anchors the block to the latest L1 block, so it claims every message sent so far. Each `advance` sends one frame transaction: the operator's `VERIFY` frame, the dependency frame, and a `SENDER` frame calling `advance`. `--corrupt-proof` sends an invalid proof, which makes the dependency frame and `advance` fail.
