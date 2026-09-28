# The `EXECUTE` precompile

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Re-execution vs ZK enforcement](#re-execution-vs-zk-enforcement)
- [Re-execution specification](#re-execution-specification)
  - [Overview](#overview)
  - [The EXECUTE precompile](#the-execute-precompile)
  - [L2-specific preprocessing](#l2-specific-preprocessing)
  - [NativeRollup contract (re-execution)](#nativerollup-contract-re-execution)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Re-execution vs ZK enforcement

The [original proposal](https://ethresear.ch/t/native-rollups-superpowers-from-l1-execution/21517) for the `EXECUTE` precompile presented two possible enforcement mechanisms: re-execution and ZK proofs. While the latter requires the L1 ZK-EVM upgrade to take place, the former can potentially be implemented beforehand and set the stage for the ZK version, in a similar way that proto-danksharding was first introduced without PeerDAS.

The re-execution variant would only be able to support optimistic rollups with a bisection protocol that goes down to single or few-L2-blocks sized steps, and that are EVM-equivalent. Today there are three stacks with working bisection protocols, namely Orbit stack (Arbitrum), OP stack (Optimism) and Cartesi. Cartesi is built to run a Linux VM so they wouldn't be able to use the precompile, and Orbit supports [Stylus](https://arbitrum.io/stylus) which doesn't make them fully EVM-equivalent, unless a Stylus-less version is implemented, but even in this case it wouldn't be able to support Arbitrum One. OP stack is mostly EVM-equivalent, but still requires heavy modifications to support native execution. It's therefore unclear whether trying to implement the re-execution version of the precompile is worth it, or if it's better to wait for the more powerful ZK version.

While the L1 ZK-EVM upgrade is not needed for the re-execution version, statelessness is, as we want L1 validators to be able to verify the precompile without having to hold all rollups' state. It's not clear whether the time interval between statelessness and L1 ZK-EVM will be long enough to justify the implementation of the re-execution variant, or whether statelessness will be implemented before the L1 ZK-EVM in the first place.

Both variants are specified in this document. The re-execution spec comes first because it is simpler and helps explain the progression to ZK: the core function being executed or proven is the same ([`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L321)), and the contract patterns (state management, messaging, anchoring) are shared. The ZK spec then shows what changes when re-execution is replaced by proof verification.

## Re-execution specification

### Overview

The re-execution variant uses an `EXECUTE` precompile that performs L2-specific preprocessing and then calls the standard stateless validation function. This is the simplest enforcement mechanism: the L1 EL directly re-executes the L2 state transition.

This variant is **not intended for production**. It is specified for:
- **Testing**: validating the native rollup contract patterns without needing ZK infrastructure.
- **Understanding**: providing a concrete, executable reference for the verification flow.
- **Progression**: showing the stepping stone from re-execution to ZK (see [From re-execution to ZK](./specification.md#from-re-execution-to-zk)).

The ethrex project implements a version of this approach ([PR #6186](https://github.com/lambdaclass/ethrex/pull/6186)). Our spec follows the same pattern but wraps the standard [`verify_stateless_new_payload`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L321) directly, rather than using a custom `apply_body` variant with individual ABI-encoded fields.

### The EXECUTE precompile

The `EXECUTE` precompile is the precompile equivalent of [`run_stateless_guest`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_guest.py#L33), the ZK guest entry point that takes SSZ-serialized input, runs `verify_stateless_new_payload`, and returns SSZ-serialized output. The precompile adds L2-specific preprocessing (fixed-field checks) before calling the same function. The pseudocode uses types and functions from the [execution-specs](https://github.com/ethereum/execution-specs/tree/projects/zkevm/src/ethereum/forks/amsterdam):

```python
from ethereum_types.numeric import U64

from ethereum.forks.amsterdam.vm import Evm
from ethereum.forks.amsterdam.vm.gas import charge_gas
from ethereum.forks.amsterdam.vm.exceptions import ExceptionalHalt, InvalidParameter

from ethereum.forks.amsterdam.stateless import (
    verify_stateless_new_payload,
)
from ethereum.forks.amsterdam.stateless_guest import (
    deserialize_stateless_input,
    serialize_stateless_output,
)


def execute(evm: Evm) -> None:
    data = evm.message.data
    stateless_input = deserialize_stateless_input(data)

    charge_gas(evm, stateless_input.new_payload_request.execution_payload.gas_used)

    # L2-specific preprocessing: enforce fixed fields.
    payload = stateless_input.new_payload_request.execution_payload
    request = stateless_input.new_payload_request
    if payload.blob_gas_used != U64(0) or payload.excess_blob_gas != U64(0):
        raise InvalidParameter
    if len(payload.withdrawals) != 0:
        raise InvalidParameter
    if len(request.execution_requests) != 0:
        raise InvalidParameter

    # Standard stateless validation (identical to L1).
    result = verify_stateless_new_payload(stateless_input)

    if not result.successful_validation:
        raise ExceptionalHalt

    evm.output = serialize_stateless_output(result)
```

**Input:** The precompile reads its input from `evm.message.data`, which contains an SSZ-serialized [`StatelessInput`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L165), decoded via [`deserialize_stateless_input`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_guest.py#L35).

**Output:**

- **On success:** `evm.output` contains the SSZ-serialized [`StatelessValidationResult`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless.py#L197), encoded via [`serialize_stateless_output`](https://github.com/ethereum/execution-specs/blob/projects/zkevm/src/ethereum/forks/amsterdam/stateless_guest.py#L27). This includes `new_payload_request_root`, `successful_validation`, and the complete `chain_config`.
- **On failure:** raises `InvalidParameter` for bad fixed fields, or `ExceptionalHalt` if validation fails. Both consume all gas and cause the `STATICCALL` to return `success = false` with empty output.

### L2-specific preprocessing

The preprocessing steps before `verify_stateless_new_payload` enforce that all fixed fields are zero/empty:

- **`blob_gas_used = 0` and `excess_blob_gas = 0`**: L2 does not support type-3 (blob-carrying) transactions. Rather than iterating through all transactions to detect them, the precompile asserts these two fields are zero. Since the STF verifies that the computed `blob_gas_used` matches the header claim, any blob transaction would cause a mismatch and be rejected inside `verify_stateless_new_payload` itself. This makes the check O(1) instead of O(n). See also [EIP-8079](https://eips.ethereum.org/EIPS/eip-8079) which defines a similar check.
- **`withdrawals` empty**: L2 has no beacon chain, so withdrawals must be empty. Without this check, an operator could mint arbitrary ETH on L2 via fake withdrawals.
- **`execution_requests` empty**: L2 has no validator operations (deposits, exits, consolidations).

L1 anchoring does not require preprocessing. The `parent_beacon_block_root` field of `NewPayloadRequest` is repurposed to carry an L1 anchor value chosen by the rollup contract. The existing [EIP-4788](https://eips.ethereum.org/EIPS/eip-4788) system transaction inside `apply_body` writes this value to the [beacon roots predeploy](https://eips.ethereum.org/EIPS/eip-4788#beacon-roots-contract), making it available to L2 contracts. This happens inside `verify_stateless_new_payload`, so the anchor write is covered by the L2 execution proof (or re-execution). No separate system transaction or predeploy is needed. The format of the anchor is not prescribed: rollups can pass an L1 block hash, a message queue commitment, or any other value useful for L1->L2 communication. See [L1 anchoring](./l1_anchoring.md) for more details.

### NativeRollup contract (re-execution)

The following contract is a proof of concept showing how the `EXECUTE` precompile can be used. It calls the precompile and updates its onchain state. Like the [ZK variant](./specification.md#nativerollup-contract-zk), the contract constructs the precompile input from a mix of **storage** (chain state that the contract enforces) and **calldata** (operator-provided fields). The contract stores:

- `stateRoot`, `blockNumber`, `blockHash`, `gasLimit`: current L2 chain head
- `chainId`: L2 chain identifier (part of `ChainConfig`)
- `stateRootHistory`: mapping of block numbers to state roots (for [L2->L1 messaging](./l2_l1_messaging.md) via state proofs)

```solidity
contract NativeRollup {

    struct BlockParams {
        // Constrained fields (validated by re-execution)
        bytes32 stateRoot;
        bytes32 receiptsRoot;
        bytes   logsBloom;
        uint256 gasUsed;
        uint256 timestamp;
        uint256 baseFeePerGas;
        bytes32 blockHash;
        // Unconstrained fields (free operator inputs)
        address feeRecipient;
        bytes32 prevRandao;
        bytes32 extraData;
    }

    // L2 chain state tracked onchain
    bytes32 public blockHash;
    bytes32 public stateRoot;
    uint256 public blockNumber;
    uint256 public gasLimit;
    uint64 public chainId;

    // L2 state root history (for L2->L1 messaging via state proofs)
    mapping(uint256 => bytes32) public stateRootHistory;

    // L1->L2 message queue. Messages are stored in this contract's
    // storage and become accessible on L2 via storage proofs against
    // the anchored L1 block hash.
    bytes32[] public pendingL1Messages;

    // EXECUTE precompile address (TBD)
    address constant EXECUTE = address(0xTBD);

    // Queue an L1->L2 message. The message hash is stored in this
    // contract's storage. On L2, a relayer provides a storage proof
    // against the anchored L1 block hash to prove the message exists.
    function sendMessage(address to, bytes calldata data) external payable {
        bytes32 messageHash = keccak256(
            abi.encodePacked(msg.sender, to, msg.value, keccak256(data), pendingL1Messages.length)
        );
        pendingL1Messages.push(messageHash);
    }

    function advance(
        BlockParams calldata params,
        bytes calldata transactions,
        bytes calldata blockAccessList,
        bytes calldata witness,
        bytes calldata publicKeys
    ) external {
        // 1. Compute the L1 anchor.
        //    Passed as parent_beacon_block_root; the EIP-4788 system
        //    transaction inside apply_body writes it to the beacon
        //    roots predeploy, making it available to L2 contracts.
        //    The anchor format is up to the rollup. This example uses
        //    the L1 block hash, which lets L2 contracts use storage
        //    proofs to access any L1 state.
        //    See: l1_anchoring.md, l1_l2_messaging.md
        bytes32 l1Anchor = blockhash(block.number - 1);

        // 2. Construct the current L2 ChainConfig. L1CHAINCONFIG is the
        //    assumed parameterless EVM environmental interface. Its L1
        //    chain ID is overwritten; its complete active-fork activation
        //    is retained.
        ChainConfig memory l2ChainConfig = L1CHAINCONFIG();
        l2ChainConfig.chainId = chainId;

        // 3. Call EXECUTE precompile with SSZ-serialized StatelessInput.
        bytes memory input = SSZ.encodeStatelessInput(
            NewPayloadRequest(
                ExecutionPayload(
                    blockHash,                  // parent_hash (from storage)
                    params.feeRecipient,
                    params.stateRoot,
                    params.receiptsRoot,
                    params.logsBloom,
                    params.prevRandao,
                    blockNumber + 1,            // block_number (from storage)
                    gasLimit,                   // gas_limit (from storage)
                    params.gasUsed,
                    params.timestamp,
                    params.extraData,
                    params.baseFeePerGas,
                    params.blockHash,
                    transactions,
                    new bytes[](0),             // withdrawals (empty for L2)
                    0,                          // blob_gas_used (zero for L2)
                    0,                          // excess_blob_gas (zero for L2)
                    blockAccessList             // block_access_list (canonical EIP-7928 RLP bytes)
                ),
                new bytes32[](0),               // versioned_hashes (empty for re-execution)
                l1Anchor,                       // parent_beacon_block_root
                new bytes[](0)                  // execution_requests (empty for L2)
            ),
            witness,
            l2ChainConfig,
            publicKeys
        );
        (bool success, bytes memory result) = EXECUTE.staticcall(input);
        require(success, "EXECUTE failed");

        // 4. Decode and verify result (SSZ-encoded StatelessValidationResult).
        (
            bytes32 newPayloadRequestRoot,
            bool validationSuccessful,
            ChainConfig memory provenChainConfig
        ) =
            SSZ.decodeStatelessValidationResult(result);
        require(validationSuccessful, "L2 validation failed");
        require(
            SSZ.hashTreeRoot(provenChainConfig) == SSZ.hashTreeRoot(l2ChainConfig),
            "chain_config mismatch"
        );

        // 5. Update onchain state
        blockHash = params.blockHash;
        stateRoot = params.stateRoot;
        blockNumber = blockNumber + 1;
        stateRootHistory[blockNumber] = params.stateRoot;
    }
}
```
