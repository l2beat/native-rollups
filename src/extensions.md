# Native rollups with extensions

Top rollups such as Arbitrum and Optimism have expressed interest in an extensible native program: most execution comes from the native program, but the project is free to add precompiles, opcodes, transaction types, and so on.

No research has been done on this yet. One idea to explore is to take the native program's verification key hash from the [EIP-8357 registry](./evm_vk_registry.md) as an input, and express the rollup's program as a delta on top of it. The native part would then follow L1 upgrades automatically, while the rollup maintains only its extensions. Those extensions do not inherit the property that an L2 bug is also an L1 bug, so they carry their own bug and governance risk.
