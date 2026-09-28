# EIP-8357: EVM Verification Key Registry

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Motivation](#motivation)
- [How the registry works](#how-the-registry-works)
- [How native rollups use it](#how-native-rollups-use-it)
- [Security considerations](#security-considerations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

EIP-8357 is a draft Core EIP that tells contracts which verification key L1 has approved for proving EVM execution in each fork. It is the one piece native rollups need on top of [EIP-8288](./zkzkframes.md). This page summarizes the design; the EIP is authoritative:

- EIP text: [ethereum/EIPs#12055](https://github.com/ethereum/EIPs/pull/12055)
- Reference implementation: [ethereum/sys-asm#56](https://github.com/ethereum/sys-asm/pull/56)
- Tests: [ethereum/execution-specs#3466](https://github.com/ethereum/execution-specs/pull/3466)
- Discussion: [Ethereum Magicians](https://ethereum-magicians.org/t/eip-8357-evm-verification-key-registry/29222)

## Motivation

EIP-8288 verifies each proof dependency against an explicitly supplied `verification_key_hash`, a hash of the STARK verification key. It does not tell contracts which hashes L1 recognizes as its own EVM program, which one is current, or when each became active. A native rollup that hard-coded these values would have to upgrade through its own governance at every L1 feature fork, or remain on an older EVM: exactly the [governance risk](./introduction.md#governance-risk) native rollups are meant to remove.

EIP-8357 records these values once, in shared L1 state that every contract can read. Verifiers keep accepting exact hashes, while each rollup chooses whether to follow the current entry or pin a historical one.

## How the registry works

The registry is an ordinary EVM contract at a fixed address, `0x0000709b303ef147cee6c13f3af6c5a402da8357`. Anyone can deploy it through the [EIP-7997](https://eips.ethereum.org/EIPS/eip-7997) deterministic factory with a fixed salt, so the address is bound to the exact bytecode.

Its state is:

- a mapping from each registered `verification_key_hash` to the activation timestamp of the L1 feature fork whose EVM semantics it represents; and
- the current `verification_key_hash`.

Entries are append-only: a registered hash is never deleted, overwritten, or revoked.

**Reads.** Any caller sends a single 32-byte word:

- zero requests the current entry;
- a nonzero value requests that exact registered hash.

The registry returns `verification_key_hash || activation_timestamp`, and reverts if no such entry exists.

**Updates.** Only `SYSTEM_ADDRESS` can update the registry, and only in the first block of a hard fork, before any transactions are processed:

- a 64-byte registration adds a new hash with its activation timestamp and makes it current;
- a 32-byte reactivation makes a previously registered hash current again, keeping its original activation timestamp.

Each new or reactivated hash is specified by its own Core EIP, which names the L1 feature fork whose EVM semantics it represents. The fork that activates EIP-8357 also registers the initial hash. The system call's gas does not count toward the block, and the block is invalid if the call fails.

## How native rollups use it

A native rollup reads the registry when it advances its L2 state and uses the result in two places:

- it requires the EIP-8288 dependency in the transaction to carry the returned `verification_key_hash`; and
- it builds the L2 `ChainConfig` from its own chain ID and the returned activation timestamp, with the block-number coordinate absent.

The second point replaces the assumption, made elsewhere in this book, of an EVM environmental interface that exposes L1's `ChainConfig`.

Each rollup chooses one of two policies:

- **Follow current**: query with zero and accept only the current hash. When a hard fork updates the registry, the rollup moves to the new EVM automatically, without changing its code or storage.
- **Pin**: query a stored historical hash, and keep that fork's EVM semantics after L1 moves on. This gives the rollup its own upgrade window, at its own risk.

The full contract is shown in the [Specification](./specification.md#nativerollup-contract).

## Security considerations

- **The registered hash must be sound.** If a hash for an incorrect program were registered as current, an attacker could prove false state transitions for every rollup following it.
- **Following current means adopting each fork's key.** Proofs generated under the previous key may fail after the switch, and a rollup that cannot produce proofs under the new key may halt.
- **Pinning transfers maintenance risk.** Registered hashes are never revoked, even if proofs under them are later considered insecure. L1 does not maintain historical entries, and a pinned rollup must detect vulnerabilities and migrate on its own.
