# EIP-8288 spike on a local daisugi-devnet-0

Can a native rollup's post carry a real EIP-8288 dependency? [daisugi-devnet-0](https://notes.ethereum.org/@pk910/daisugi-devnet-0) is the only devnet with EIP-8288. Its only client is Nethermind's `feature/eip8288-lean`, and it accepts generic leanSTARK dependencies (scheme `0x11`) next to leanSPHINCS. This spike runs it locally, outside the public demo.

The proofs are real leanVM proofs, but of lean-bindings' test program that accepts any public input. So the dependency is real, while what it proves is not: leanVM cannot run the EVM program.

## Running it

1. Build the genesis generator and start the chain (see [kurtosis.yaml](./kurtosis.yaml)):
   ```sh
   docker build -t native-rollups/genesis-generator:daisugi-spike spikes/daisugi/genesis-generator
   kurtosis run github.com/ethpandaops/ethereum-package@5795b77e65235ad223d4d3b413b2739190bf60cc \
       --enclave daisugi --args-file spikes/daisugi/kurtosis.yaml
   ```
2. Get the prover: `fixtures` for linux-arm64 or linux-x64, from [lean-bindings](https://github.com/NethermindEth/lean-bindings) `src/bindings/Nethermind.Crypto.Lean/testing/runtimes/<rid>/native/fixtures` (Git LFS), at the commit released as `Nethermind.Crypto.Lean` 0.21.0.110, which the node pins. Prove the test program over a hash, in a Linux container:
   ```sh
   fixtures --prove-stark <64 hex digits> stark.bin   # writes key (32 bytes) || proof
   ```
3. Build the probe ([probe/](./probe), `forge build`) and the sender ([deptx/](./deptx), `go build`). Then:
   ```sh
   deptx deploy -rpc $EL -initcode probe.hex
   deptx send -rpc $EL -probe $PROBE -witness stark.bin -data 0x<hash>
   ```
   `send` builds a frame transaction `[VERIFY, DEP_VERIFY, SENDER]`. The `DEP_VERIFY` frame declares `(0x11, hash, key)`, and the `SENDER` frame calls `DepProbe.probe`, which reads the dependency frame the way a rollup contract would. The transaction goes in a mode-0 proof wrapper through `eth_sendProofWrapper`. `-order`, `-declare-data` and `-declare-key` give the variants below.

## Results

**Stage 1, the chain.**
- The devnet notes' local example forks only the execution layer. The generator's consensus template has no Daisugi keys, so [genesis-generator/](./genesis-generator) adds them.
- This package version also schedules the BPO forks at genesis, which need Fulu, so they are disabled.

**Stage 2, a bare leanSTARK dependency.**
- **It works end to end.** Nethermind accepts the wrapper in 10 to 50 ms after checking the 165 kB proof, and includes the transaction in the next block. The block carries a dependency proof of about 252 kB, and the header commits to it (`starkProofHash`, `blockDepsHash`).
- **What a contract sees:** `FRAMEPARAM` gives mode 4 and data length 96, and `FRAMEDATACOPY` gives the triple as declared. An earlier dependency frame has status 1. A later one has none, and reading it halts the call, burning its gas: that run used 378k gas instead of 90k. So a rollup should check the mode and read the triple, not the status. Inclusion already means the dependency is proven.
- **Frame order:** the dependency frame can come first, in the middle or last.
- **Invalid proofs:** a wrong data hash or a wrong key is rejected at `eth_sendProofWrapper` ("a wrapper dependency proof failed verification") and never reaches a block.
- **Gas:** 90,007 for the whole transaction. About 60,000 of it is the dependency: the frame's 30,000 budget and the 30,000 share of the block proof.
- **Proving:**
  - The sender's proof of the test program takes 0.05 s and 33 to 55 MB.
  - Nethermind proves the block's aggregate in the background on admission: 451 ms the first time, 170 ms after that, on an M4 Pro. It verifies it in 6 to 9 ms.
  - The same dependency set reuses the cached aggregate.
- **Reuse:** a dependency proof is not bound to its transaction. The same triple was accepted in four transactions. For a rollup this is harmless, since the claim is about a root the contract recomputes.
