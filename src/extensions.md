# Native rollups with extensions

Top rollups such as Arbitrum and Optimism have expressed interest in an extensible native program: most execution comes from the native program, but the project is free to add precompiles, opcodes, transaction types, and so on.

No detailed design exists yet, though two directions stand out:

- **Program delta over EIP-8357:** Take the native program's `verification_key_hash` from the [EIP-8357 registry](./evm_vk_registry.md) as an input and express the rollup's program as a delta on top of it, so the native core follows L1 upgrades automatically while the rollup maintains only its extensions.
- **Composing custom PIOPs over `leanPCS`:** Because [`leanPCS`](./leanpcs.md) allows up to `MAX_PCS_CLAIMS_PER_TX = 256` claims in a single transaction, a rollup could verify auxiliary proofs (for custom precompiles, offchain coprocessors, or specialized circuits) alongside the native EVM proof in the same `DEP_VERIFY` frame without changing the shared L1 program.

In either case, extensions do not inherit the property that an L2 bug is also an L1 bug, so they carry their own bug and governance risk.
