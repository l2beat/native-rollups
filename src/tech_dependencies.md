# Tech dependencies

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Relevant links](#relevant-links)
- [Statelessness (EIP-7864)](#statelessness-eip-7864)
- [L1 ZK-EVM](#l1-zk-evm)
- [EVM-readable L1 chain configuration](#evm-readable-l1-chain-configuration)
- [FOCIL (EIP-7805)](#focil-eip-7805)
- [RISC-V (or equivalent)](#risc-v-or-equivalent)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Relevant links

- [ZK L1 2026 Roadmap](https://github.com/eth-act/planning/blob/91152e188cd4e4d7ad77796ddfcaee9cbbcbec64/projects.md)
- [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025)
- [Execution Layer Rollout plan](https://hackmd.io/i4z1eOYATKq3Ang-9Zw5hQ)
- [Reth's `StatelessInput` definition](https://github.com/paradigmxyz/reth/blob/b3c00ed602f2a8805974f25be714a3bea26901e9/crates/stateless/src/lib.rs#L64)
- [ethrex's execution witness docs](https://github.com/lambdaclass/ethrex/blob/360f6a10bde8fcbc99514f672d00bc7dcbf283d9/docs/l2/fundamentals/execution_witness.md#L3-L13)
- [State redesign tracking issue](https://github.com/ethereum/execution-specs/issues/1865)

## Statelessness (EIP-7864)
L1 validators shouldn't store the state of all rollups, therefore the `EXECUTE` precompile requires its verification to be stateless. The statelessness upgrade is therefore required, with all its associated EIPs.

Some adjacent EIPs that are relevant in this context are:
- [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935): Serve historical block hashes from state (live with Pectra).
- [EIP-7709](https://eips.ethereum.org/EIPS/eip-7709): Read BLOCKHASH from storage and update cost (SFI in Fusaka).

## L1 ZK-EVM
The ZK version of the `EXECUTE` precompile requires the L1 ZK-EVM upgrade to take place first and it will influence how exactly the precompile will be implemented:

- Offchain vs onchain proofs: influences whether the precompile needs to take a ZK proof (or multiple proofs) as input.
- Gas limit handling: influences whether the precompile needs to take a gas limit as an input or not. Some L1 ZK-EVM proposals suggest the complete removal of the gas limit, as long as the block proposer itself is also required to provide the ZK proof (see [Prover Killers Killer: You Build it, You Prove it](https://ethresear.ch/t/prover-killers-killer-you-build-it-you-prove-it/22308)).

Relevant EIPs:
- [EIP-8025](https://eips.ethereum.org/EIPS/eip-8025): Optional Execution Proofs

## EVM-readable L1 chain configuration

The native-rollup contract must reconstruct the exact [`ChainConfig`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L130-L159) committed by stateless execution. This proposal assumes a parameterless EVM environmental interface exposes the current canonical L1 `ChainConfig`. The contract ignores the returned L1 `chain_id`, retains the complete `active_fork.activation`, and combines it with its stored L2 `chain_id`.

No current EVM instruction provides this value. [EIP-7910](https://eips.ethereum.org/EIPS/eip-7910) defines the parameterless `eth_config` JSON-RPC method, but it is not available to contracts. The exact opcode or precompile is left to the L1 zkEVM work; its value must change atomically with the L1 rules under which the current EVM execution occurs.

## FOCIL (EIP-7805)
While not strictly required, the addition of [FOCIL](https://eips.ethereum.org/EIPS/eip-7805) would help simplifying the design of forced transaction mechanisms, as described in the FOCIL section of the [Forced transactions](./forced_transactions.md#focil-eip-7805) page.

## RISC-V (or equivalent)

> ⚠️
> This is only to be considered for future versions of native rollups. General compatibility should still be kept in mind.


Non-EVM-based native rollups can be supported if L1 migrates it's low-level architecture to RISC-V or equivalent. At that point, L1 execution can provide two services to native rollups:
- A RISC-V or equivalent ISA that can be accessed directly. L1 will provide multi-proofs, audits, formal verification, and a socially "bug-free" implementation.
- An EVM host program that sits on top, which can be again considered socially bug-free and it's automatically upgraded through L1 governance process. Under the hood, the RISC-V or equivalent infrastructure is used.

One idea is to then split the `EXECUTE` into two versions: a `EVMEXECUTE` and a `RISCVEXECUTE` precompile, where non-EVM rollups would choose to call the second one with a custom host program. Note that this is highly speculative and heavily depends on the specific implementation of the RISC-V proposal. Open questions remain around how to guarantee availability of host programs to be able to detect bugs in the ZK verification process.
