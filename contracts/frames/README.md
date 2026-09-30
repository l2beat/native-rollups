# Native rollup on frames-devnet-0

Runs the `NativeRollup` contract on a chain with [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions but without EIP-8288 or EIP-8357, such as [frames-devnet-0](https://notes.ethereum.org/@ethpandaops/frames-devnet-0). Three pieces stand in for what the chain lacks:

- `MockDependencyVerifier`: a `DEFAULT` frame calls it with the EIP-8288 dependency triple followed by a proof, here a signature by a trusted prover. `FramesNativeRollup` requires that frame to target the verifier and to have succeeded, and reads the triple from its data.
- The EIP-8357 registry runtime, with its system address replaced by an admin key that registers one EVM verification key hash.
- `frame_introspection.eas`: exposes `FRAMEPARAM` and `FRAMEDATACOPY` to Solidity, which does not support the frame instructions yet.

The L2 blocks are real. `script/l2_node.py` builds them under the L1 stateless validation program's rules (execution-specs `projects/zkevm`, Amsterdam) with EEST, from a genesis holding the system contracts the Specification requires, and anchors each block to the L1 block the operator picks. It validates every block with `run_stateless_guest` and signs the dependency only if the program accepts it: a trusted signer instead of a zkVM proof, attesting to the real statement.

## Local network

```shell
kurtosis run github.com/ethpandaops/ethereum-package --enclave frames --args-file frames/kurtosis.yaml
```

Frame transactions work from epoch 1, when the EIP-8141 fork activates.

## Deploy and advance

The L2 genesis comes from the node. Foundry's simulation does not charge EIP-8037 state gas, so deployments use the node's gas estimates:

```shell
uv run --project <execution-specs@projects/zkevm> python script/l2_node.py genesis --state <l2-state>

PRIVATE_KEY=<key> PROVER=<address> GENESIS_HASH=<hash> GENESIS_STATE_ROOT=<root> \
    forge script script/DeployFrames.s.sol --rpc-url <rpc> --broadcast --slow --skip-simulation

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py advance \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> \
    --operator-key <key> --prover-key <key> \
    --l2-state <l2-state> --zkevm-specs <execution-specs@projects/zkevm>
```

The two scripts use different execution-specs branches, since the frame transaction types only exist on the devnet branch and the L2 runs the zkEVM project's program. Each `advance` sends one frame transaction: the operator's `VERIFY` frame, the dependency frame, and a `SENDER` frame calling `advance`. `--corrupt-proof` sends an invalid proof, which makes the dependency frame and `advance` fail.
