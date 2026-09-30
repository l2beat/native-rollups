# Gas token deposits

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Design](#design)
- [Existing rollups](#existing-rollups)
- [Open questions](#open-questions)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Rollup users need the L2 gas token to send transactions. Native rollups cannot mint it with a special transaction type, since L1 has none. Instead, the gas token is pre-minted on L2 and released by L1 to L2 messages.

## Design

A predeployed L2 contract holds a pre-minted supply of the gas token. When the [messenger](./messaging.md#l1-to-l2-messaging) processes a deposit message from L1, the contract releases the corresponding amount to the recipient. Withdrawals lock tokens back into the contract and send an [L2 to L1 message](./messaging.md#l2-to-l1-messaging).

Claiming a deposit is an L2 transaction, and all L2 ETH starts in the pre-minted supply, so the claimed ETH has to pay for its own claim. [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141) frame transactions allow this: a `DEFAULT` frame claims the message, then a `VERIFY` frame of the recipient approves execution and payment. EIP-8141 collects the fee from the payer only when it approves, and by then the recipient holds the deposit, so later frames can already use the rest. Deposits to contracts that cannot approve payment still need someone else to claim them.

Because the L2 contract only reacts to messages, the L1 side decides what backs the gas token. Custom gas tokens are therefore only a matter of L1 contract design:

- **ETH**: the L1 contract escrows the ETH sent with each message, as Linea does.
- **ERC20**: the L1 contract transfers an ERC20 into escrow instead. Tokens with non-standard decimals or transfer logic need special care.
- **ETH burn**: the L1 contract burns the ETH instead of escrowing it.
- **NFT-gated credits**: NFT holders can claim a fixed amount of gas token once per NFT.

Alternatives that were considered and dropped:

- A transaction type that mints the gas token, as in the OP and Orbit stacks. L1 has no such transaction type.
- Minting through the `withdrawals` field of the payload. The rollup contract could fill it from its own deposit queue, but withdrawals are only processed at the end of a block, so deposits could not be used within the same block. The reference design keeps `withdrawals` empty.

## Existing rollups

| Stack | Deposit mechanism |
|---|---|
| OP stack | A deposit transaction type mints the gas token from `TransactionDeposited` event fields |
| Linea | [Pre-minted tokens](https://lineascan.build/address/0x508Ca82Df566dCD1B0DE8296e70a96332cD644ec) in the `L2MessageService`, unlocked by L1 to L2 messages |
| Taiko | [Pre-minted tokens](https://taikoscan.io/address/0x1670000000000000000000000000000000000001) in the L2 `Bridge`, unlocked by L1 to L2 messages |
| Orbit stack | A deposit transaction type (`ArbitrumDepositTx`) mints the gas token |

## Open questions

- **Claims before EIP-8141**: native rollups only have frame transactions once L1 does. Until then, nobody can pay for the first claim, since all L2 ETH starts in the pre-minted supply. Funding an account at genesis would work around it, but L1 would not back that ETH.
- **Unpaid claims**: the claim frame runs before payment is approved, so the operator executes a failing claim without collecting a fee. Such transactions fall outside the public mempool's validation rules, so the operator needs its own admission check, such as looking up the message on L1 before including the claim.
