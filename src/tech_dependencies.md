# Tech dependencies

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [L1 ZK-EVM](#l1-zk-evm)
- [Statelessness](#statelessness)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## Statelessness (EIP-6800)

L1 validators shouldn't store the state of all rollups, therefore the `EXECUTE` precompile requires its verification to be stateless. The statelessness upgrade is therefore required, with all its associated EIPs.

Some adjacent EIPs that are relevant in this context are:
- [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935): Serve historical block hashes from state (live with Pectra).
- [EIP-7709](https://eips.ethereum.org/EIPS/eip-7709): Read BLOCKHASH from storage and update cost (SFI in Fusaka).

## L1 ZK-EVM (EIP-?)

The ZK version of the `EXECUTE` precompile requires the L1 ZK-EVM upgrade to take place first and it will influence how exactly the precompile will be implemented:

- Offchain vs onchain proofs: influences whether the precompile needs to take a ZK proof (or multiple proofs) as input.
- Gas limit handling: influences whether the precompile needs to take a gas limit as an input or not. Some L1 ZK-EVM proposals suggest the complete removal of the gas limit, as long as the block proposer itself is also required to provide the ZK proof (see [Prover Killers Killer: You Build it, You Prove it](https://ethresear.ch/t/prover-killers-killer-you-build-it-you-prove-it/22308)).
