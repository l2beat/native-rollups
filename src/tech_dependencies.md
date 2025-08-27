# Tech dependencies

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Statelessness (EIP-6800)](#statelessness-eip-6800)
- [L1 ZK-EVM](#l1-zk-evm)
- [RISC-V (or equivalent)](#risc-v-or-equivalent)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Statelessness (EIP-6800)
L1 validators shouldn't store the state of all rollups, therefore the `EXECUTE` precompile requires its verification to be stateless. The statelessness upgrade is therefore required, with all its associated EIPs.

Some adjacent EIPs that are relevant in this context are:
- [EIP-2935](https://eips.ethereum.org/EIPS/eip-2935): Serve historical block hashes from state (live with Pectra).
- [EIP-7709](https://eips.ethereum.org/EIPS/eip-7709): Read BLOCKHASH from storage and update cost (SFI in Fusaka).

## L1 ZK-EVM

The ZK version of the `EXECUTE` precompile requires the L1 ZK-EVM upgrade to take place first and it will influence how exactly the precompile will be implemented:

- Offchain vs onchain proofs: influences whether the precompile needs to take a ZK proof (or multiple proofs) as input.
- Gas limit handling: influences whether the precompile needs to take a gas limit as an input or not. Some L1 ZK-EVM proposals suggest the complete removal of the gas limit, as long as the block proposer itself is also required to provide the ZK proof (see [Prover Killers Killer: You Build it, You Prove it](https://ethresear.ch/t/prover-killers-killer-you-build-it-you-prove-it/22308)).

## RISC-V (or equivalent)

> ⚠️
> This is only to be considered for future versions of native rollups. General compatibility should still be kept in mind.


Non-EVM-based native rollups can be supported if L1 migrates it's low-level architecture to RISC-V or equivalent. At that point, L1 execution can provide two services to native rollups:
- A RISC-V or equivalent ISA that can be accessed directly. L1 will provide multi-proofs, audits, formal verification, and a socially "bug-free" implementation.
- An EVM host program that sits on top, which can be again considered socially bug-free and it's automatically upgraded through L1 governance process. Under the hood, the RISC-V or equivalent infrastructure is used.

One idea is to then split the `EXECUTE` into two versions: a `EVMEXECUTE` and a `RISCVEXECUTE` precompile, where non-EVM rollups would choose to call the second one with a custom host program. Note that this is highly speculative and heavily depends on the specific implementation of the RISC-V proposal. Open questions remain around how to guarantee availability of host programs to be able to detect bugs in the ZK verification process.
