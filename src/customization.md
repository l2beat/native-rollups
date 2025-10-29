# Customization
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Custom gas tokens](#custom-gas-tokens)
  - [Arbitrary ERC20](#arbitrary-erc20)
  - [ETH burn](#eth-burn)
  - [NFT-gated gas credits](#nft-gated-gas-credits)
- [Custom sequencing](#custom-sequencing)
- [Custom VMs](#custom-vms)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Custom gas tokens
We present PoCs for custom gas token implementations using Linea's messaging bridge as inspiration, which is reported here:

```solidity
  /**
   * @notice Adds a message for sending cross-chain and emits MessageSent.
   * @dev The message number is preset (nextMessageNumber) and only incremented at the end if successful for the next caller.
   * @dev This function should be called with a msg.value = _value + _fee. The fee will be paid on the destination chain.
   * @param _to The address the message is intended for.
   * @param _fee The fee being paid for the message delivery.
   * @param _calldata The calldata to pass to the recipient.
   */
  function sendMessage(
    address _to,
    uint256 _fee,
    bytes calldata _calldata
  ) external payable whenTypeAndGeneralNotPaused(PauseType.L1_L2) {
    if (_to == address(0)) {
      revert ZeroAddressNotAllowed();
    }

    if (_fee > msg.value) {
      revert ValueSentTooLow();
    }

    uint256 messageNumber = nextMessageNumber++;
    uint256 valueSent = msg.value - _fee;

    bytes32 messageHash = MessageHashing._hashMessage(msg.sender, _to, _fee, valueSent, messageNumber, _calldata);

    _addRollingHash(messageNumber, messageHash);

    emit MessageSent(msg.sender, _to, _fee, valueSent, messageNumber, _calldata, messageHash);
  }
```

### Arbitrary ERC20

Instead of using `msg.value` as the `valueSent`, we use ERC20 transfer amount.

```solidity
function sendMessage(
  address _to,
  uint256 _fee,
  uint256 _value,
  bytes calldata _calldata
) external whenTypeAndGeneralNotPaused(PauseType.L1_L2) {
  if (_to == address(0)) {
    revert ZeroAddressNotAllowed();
  }

  uint256 totalAmount = _value + _fee;

  bool success = gasToken.transferFrom(msg.sender, address(this), totalAmount);
  if (!success) {
    revert TokenTransferFailed();
  }

  uint256 messageNumber = nextMessageNumber++;

  bytes32 messageHash = MessageHashing._hashMessage(
    msg.sender,
    _to,
    _fee,
    _value,
    messageNumber,
    _calldata
  );

  _addRollingHash(messageNumber, messageHash);

  emit MessageSent(msg.sender, _to, _fee, _value, messageNumber, _calldata, messageHash);
}
```

Special considerations apply when tokens with non-standard decimals or transfer logic are used.

### ETH burn

Instead of collecting deposits, the ETH sent is burned to a designated burn address.

```solidity
function sendMessage(
  address _to,
  uint256 _fee,
  bytes calldata _calldata
) external payable whenTypeAndGeneralNotPaused(PauseType.L1_L2) {
  if (_to == address(0)) {
    revert ZeroAddressNotAllowed();
  }

  if (_fee > msg.value) {
    revert ValueSentTooLow();
  }

  uint256 messageNumber = nextMessageNumber++;
  uint256 valueSent = msg.value - _fee;

  // Burn the full amount
  (bool success, ) = address(BURN_ADDRESS).call{value: msg.value}("");
  if (!success) {
    revert BurnFailed();
  }

  bytes32 messageHash = MessageHashing._hashMessage(msg.sender, _to, _fee, valueSent, messageNumber, _calldata);

  _addRollingHash(messageNumber, messageHash);

  emit MessageSent(msg.sender, _to, _fee, valueSent, messageNumber, _calldata, messageHash);
}
```

### NFT-gated gas credits

NFT holders can claim free gas tokens on L2 once per NFT.

```solidity
function sendMessage(
  address _to,
  uint256 _tokenId,
  bytes calldata _calldata
) external whenTypeAndGeneralNotPaused(PauseType.L1_L2) {
  if (_to == address(0)) {
    revert ZeroAddressNotAllowed();
  }

  if (NFT_COLLECTION.ownerOf(_tokenId) != msg.sender) {
    revert NotNFTOwner();
  }

  if (claimed[_tokenId]) {
    revert AlreadyClaimed();
  }

  claimed[_tokenId] = true;

  uint256 messageNumber = nextMessageNumber++;
  uint256 valueSent = GAS_CREDIT_AMOUNT;

  bytes32 messageHash = MessageHashing._hashMessage(msg.sender, _to, 0, valueSent, messageNumber, _calldata);

  _addRollingHash(messageNumber, messageHash);

  emit MessageSent(msg.sender, _to, 0, valueSent, messageNumber, _calldata, messageHash);
}
```

## Custom sequencing
TODO

## Custom VMs
TODO
