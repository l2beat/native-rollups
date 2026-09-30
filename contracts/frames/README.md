# Native rollup on frames-devnet-0

Runs the `NativeRollup` contract on a chain with [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions but without EIP-8288 or EIP-8357, such as [frames-devnet-0](https://notes.ethereum.org/@ethpandaops/frames-devnet-0). Three pieces stand in for what the chain lacks:

- `MockDependencyVerifier`: a `DEFAULT` frame calls it with the EIP-8288 dependency triple followed by a proof, here a signature by a trusted prover. `FramesNativeRollup` requires that frame to target the verifier and to have succeeded, and reads the triple from its data.
- The EIP-8357 registry runtime, with its system address replaced by an admin key that registers one EVM verification key hash.
- `frame_introspection.eas`: exposes `FRAMEPARAM` and `FRAMEDATACOPY` to Solidity, which does not support the frame instructions yet.

The L2 blocks are synthetic: the mock prover signs their public input root.

## Local network

```shell
kurtosis run github.com/ethpandaops/ethereum-package --enclave frames --args-file frames/kurtosis.yaml
```

Frame transactions work from epoch 1, when the EIP-8141 fork activates.

## Deploy and advance

Foundry's simulation does not charge EIP-8037 state gas, so deployments use the node's gas estimates:

```shell
PRIVATE_KEY=<key> PROVER=<address> forge script script/DeployFrames.s.sol \
    --rpc-url <rpc> --broadcast --slow --skip-simulation

uv run --project <execution-specs@devnets/frames/0> python script/frames_operator.py advance \
    --rpc <rpc> --rollup <rollup> --verifier <verifier> \
    --operator-key <key> --prover-key <key>
```

Each `advance` sends one frame transaction: the operator's `VERIFY` frame, the dependency frame, and a `SENDER` frame calling `advance`. `--corrupt-proof` sends an invalid proof, which makes the dependency frame and `advance` fail.
