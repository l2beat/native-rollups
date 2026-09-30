"""
Decodes what the follower rebuilt from L1 into the JSON the demo's explorer
shows: L2 blocks, L2 transactions with their receipts, and the L1
transactions of the rollup, with frames, calls and events decoded.

Everything here comes from L1: the L1 transactions and receipts from the L1
node, and the L2 blocks, transactions and receipts from the follower's
re-execution of the data those transactions carry.
"""

import json
import os
import time

from eth_abi import decode as abi_decode
from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes0

from ethereum.crypto.hash import keccak256
from ethereum.state import EMPTY_CODE_HASH, Address
from ethereum.forks.amsterdam.blocks import FrameTransactionReceipt, decode_receipt
from ethereum.forks.amsterdam.transactions import decode_transaction, get_transaction_hash, recover_sender
from ethereum.forks.amsterdam.transactions.frame_transaction import FrameTransaction

MODES = {0: "DEFAULT", 1: "VERIFY", 2: "SENDER"}
FLAGS = {1: "APPROVE_PAYMENT", 2: "APPROVE_EXECUTION", 4: "ATOMIC_BATCH"}
MESSAGE = "(address,address,uint256,bytes,uint256)"
MESSAGE_FIELDS = ["sender", "to", "value", "data", "index"]
BLOCK_PARAMS = "(bytes32,bytes32,bytes,uint64,uint64,uint256,bytes32,bytes32,bytes32,uint256,bytes32,uint256,address,bytes32,bytes)"
BLOCK_PARAMS_FIELDS = [
    "stateRoot", "receiptsRoot", "logsBloom", "gasUsed", "timestamp", "baseFeePerGas", "blockHash",
    "transactionsRoot", "blockAccessListRoot", "payloadBlobCount", "executionRequestsRoot", "anchorBlockNumber",
    "feeRecipient", "prevRandao", "extraData",
]

# name, argument types, argument names
FUNCTIONS = {
    "advance": (f"advance({BLOCK_PARAMS},uint256)", [BLOCK_PARAMS, "uint256"], ["params", "dependencyFrameIndex"]),
    "sendMessage": ("sendMessage(address,bytes)", ["address", "bytes"], ["to", "data"]),
    "claimL2Message": (
        f"claimL2Message({MESSAGE},uint256,bytes[],bytes[])",
        [MESSAGE, "uint256", "bytes[]", "bytes[]"],
        ["message", "l2BlockNumber", "accountProof", "storageProof"],
    ),
    "proveL1MessageRoot": (
        "proveL1MessageRoot(uint256,bytes,bytes[],bytes[])",
        ["uint256", "bytes", "bytes[]", "bytes[]"],
        ["anchorTimestamp", "l1Header", "accountProof", "storageProof"],
    ),
    "claimL1Message": (f"claimL1Message({MESSAGE},bytes32[])", [MESSAGE, "bytes32[]"], ["message", "path"]),
}
SELECTORS = {keccak256(sig.encode())[:4]: (name, types, names) for name, (sig, types, names) in FUNCTIONS.items()}

# signature, indexed argument names, data argument types and names
EVENTS = {
    "L1MessageSent": ("L1MessageSent(uint256,address,address,uint256,bytes)", ["index", "sender", "to"], ["uint256", "bytes"], ["value", "data"]),
    "BlockAdded": ("BlockAdded(uint64,bytes32)", ["number"], ["bytes32"], ["blockHash"]),
    "L2MessageClaimed": ("L2MessageClaimed(uint256,address,address,uint256)", ["index", "sender", "to"], ["uint256"], ["value"]),
    "L1MessageRootProven": ("L1MessageRootProven(uint256,bytes32)", ["anchorTimestamp"], ["bytes32"], ["root"]),
    "L1MessageClaimed": ("L1MessageClaimed(uint256,address,address,uint256)", ["index", "sender", "to"], ["uint256"], ["value"]),
    "L2MessageSent": ("L2MessageSent(uint256,address,address,uint256,bytes)", ["index", "sender", "to"], ["uint256", "bytes"], ["value", "data"]),
    "Transfer": ("Transfer(address,address,uint256)", ["from", "to"], ["uint256"], ["value"]),
}
TOPICS = {keccak256(sig.encode()): (name, indexed, types, names) for name, (sig, indexed, types, names) in EVENTS.items()}


def hx(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def jsonable(value):
    """ABI values as JSON: addresses and bytes as hex, big integers as strings."""
    if isinstance(value, (bytes, bytearray)):
        return hx(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) < 2**53 else str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
        return value.lower()
    return value


def decode_call(data: bytes) -> dict | None:
    """The function and arguments of a call to the rollup or the messenger."""
    if len(data) < 4 or bytes(data[:4]) not in SELECTORS:
        return None
    name, types, names = SELECTORS[bytes(data[:4])]
    values = abi_decode(types, bytes(data[4:]))
    args = {}
    for arg, value in zip(names, values):
        if arg == "params":
            value = dict(zip(BLOCK_PARAMS_FIELDS, value))
        elif arg == "message":
            value = dict(zip(MESSAGE_FIELDS, value))
        elif arg in ("accountProof", "storageProof"):
            value = {"nodes": len(value), "bytes": sum(len(n) for n in value)}
        elif arg == "l1Header":
            value = {"bytes": len(value), "blockHash": hx(keccak256(value))}
        elif arg == "path":
            value = [hx(v) for v in value]
        args[arg] = jsonable(value)
    return {"function": name, "args": args}


def decode_log(address: str, topics: list, data: bytes) -> dict:
    entry = {"address": address.lower(), "topics": [hx(t) for t in topics], "data": hx(data)}
    if topics and bytes(topics[0]) in TOPICS:
        name, indexed, types, names = TOPICS[bytes(topics[0])]
        args = {}
        for arg, topic in zip(indexed, topics[1:]):
            args[arg] = "0x" + bytes(topic)[-20:].hex() if arg in ("sender", "to", "from") else int.from_bytes(bytes(topic), "big")
        args.update({arg: jsonable(v) for arg, v in zip(names, abi_decode(types, bytes(data)))})
        entry["event"] = {"name": name, "args": args}
    return entry


def frame_flags(flags: int) -> list:
    return [name for bit, name in FLAGS.items() if flags & bit]


def dependency(data: bytes) -> dict | None:
    """The frame data of the mock EIP-8288 verifier: the dependency triple and a signature."""
    if len(data) != 96 + 65:
        return None
    return {
        "scheme": data[31], "dataHash": hx(data[32:64]), "verificationKeyHash": hx(data[64:96]),
        "signature": hx(data[96:]),
    }


# ---------------------------------------------------------------------------
# L2
# ---------------------------------------------------------------------------


def l2_kind(tx: dict, messenger: str) -> str:
    calls = [f.get("call") or {} for f in tx.get("frames", [])] or [tx.get("call") or {}]
    functions = {c.get("function") for c in calls}
    if "claimL1Message" in functions:
        return "deposit claim"
    if tx.get("to") == messenger and "sendMessage" in functions:
        return "withdrawal"
    if tx.get("type") != 6 and tx.get("data") == "0x":
        return "transfer"
    return "call"


def l2_transaction(raw: bytes, receipt, gas_used: int, messenger: str) -> dict:
    tx = decode_transaction(raw)
    out = {"hash": hx(get_transaction_hash(raw)), "type": raw[0] if raw[0] < 0x80 else 0, "bytes": len(raw), "gasUsed": gas_used}
    if isinstance(tx, FrameTransaction):
        sender = hx(tx.sender)
        out.update({
            "from": sender, "nonce": int(tx.nonce),
            "maxFeePerGas": int(tx.fees.max_fee_per_gas), "maxPriorityFeePerGas": int(tx.fees.max_priority_fee_per_gas),
            "signatures": [{"scheme": int(s.scheme), "signer": hx(s.signer) if len(s.signer) else sender} for s in tx.signatures],
            "frames": [],
        })
        frame_receipts = receipt.frame_receipts if isinstance(receipt, FrameTransactionReceipt) else ()
        out["payer"] = hx(receipt.payer) if isinstance(receipt, FrameTransactionReceipt) else None
        for i, frame in enumerate(tx.frames):
            target = sender if isinstance(frame.to, Bytes0) or len(frame.to) == 0 else hx(frame.to)
            entry = {
                "mode": MODES[int(frame.mode)], "flags": frame_flags(int(frame.flags)), "target": target,
                "executionGasLimit": int(frame.gas_limits.execution), "stateGasLimit": int(frame.gas_limits.state),
                "value": int(frame.value), "data": hx(frame.data), "call": decode_call(bytes(frame.data)),
            }
            if i < len(frame_receipts):
                fr = frame_receipts[i]
                entry.update({
                    "status": int(fr.status), "executionGasUsed": int(fr.gas_used.execution),
                    "stateGasUsed": int(fr.gas_used.state),
                    "logs": [decode_log(hx(log.address), log.topics, log.data) for log in fr.logs],
                })
            out["frames"].append(entry)
        out["status"] = 1
    else:
        out.update({
            "from": hx(recover_sender(tx)), "to": hx(tx.to) if len(tx.to) else None, "nonce": int(tx.nonce),
            "value": int(tx.value), "data": hx(tx.data), "gasLimit": int(tx.gas),
            "call": decode_call(bytes(tx.data)),
            "status": int(receipt.succeeded),
            "logs": [decode_log(hx(log.address), log.topics, log.data) for log in receipt.logs],
        })
    out["kind"] = l2_kind(out, messenger)
    return jsonable(out)


def receipts(output) -> list:
    from ethereum.merkle_patricia_trie import trie_get

    return [decode_receipt(trie_get(output.receipts_trie, key)) for key in output.receipt_keys]


# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------


def l1_transaction(tx: dict, receipt: dict, block: dict) -> dict:
    """An L1 transaction from the L1 node, with its frames, calls and events decoded."""
    out = {
        "hash": tx["hash"], "type": int(tx["type"], 16), "from": tx["from"].lower(),
        "to": (tx.get("to") or "").lower() or None, "nonce": int(tx["nonce"], 16),
        "value": jsonable(int(tx.get("value") or "0x0", 16)), "block": int(tx["blockNumber"], 16),
        "timestamp": int(block["timestamp"], 16), "builder": bytes.fromhex(block["extraData"][2:]).decode(errors="replace"),
        "status": int(receipt["status"], 16), "gasUsed": int(receipt["gasUsed"], 16),
        "effectiveGasPrice": int(receipt.get("effectiveGasPrice", "0x0"), 16),
        "blobVersionedHashes": tx.get("blobVersionedHashes") or [],
        "blobGasUsed": int(receipt.get("blobGasUsed") or "0x0", 16),
        "blobGasPrice": int(receipt.get("blobGasPrice") or "0x0", 16),
        "logs": [decode_log(log["address"], [bytes.fromhex(t[2:]) for t in log["topics"]], bytes.fromhex(log["data"][2:])) for log in receipt["logs"]],
    }
    if tx.get("frames"):
        frame_receipts = receipt.get("frameReceipts") or []
        out["frames"] = []
        for i, frame in enumerate(tx["frames"]):
            data = bytes.fromhex(frame["data"][2:])
            entry = {
                "mode": MODES[int(frame["mode"], 16)], "flags": frame_flags(int(frame["flags"], 16)),
                "target": (frame.get("target") or tx["from"]).lower(),
                "executionGasLimit": int(frame["executionGas"], 16), "stateGasLimit": int(frame["stateGas"], 16),
                "value": int(frame["value"], 16), "data": frame["data"] if len(data) <= 4096 else frame["data"][:8194] + "…",
                "call": decode_call(data), "dependency": dependency(data),
            }
            if i < len(frame_receipts):
                fr = frame_receipts[i]
                entry.update({
                    "status": int(fr["status"], 16), "executionGasUsed": int(fr["executionGasUsed"], 16),
                    "stateGasUsed": int(fr["stateGasUsed"], 16),
                })
            out["frames"].append(entry)
        out["payer"] = (receipt.get("payer") or "").lower() or None
    else:
        data = bytes.fromhex(tx["input"][2:])
        out.update({"data": tx["input"], "call": decode_call(data)})
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def addresses(tx: dict) -> list:
    """The addresses a decoded transaction involves: its sender, target,
    frame targets and payer, the emitters of its events, and the accounts its
    ETH transfer logs move ETH between."""
    found = [tx.get("from"), tx.get("to"), tx.get("payer")] + [f["target"] for f in tx.get("frames") or []]
    logs = (tx.get("logs") or []) + [log for f in tx.get("frames") or [] for log in f.get("logs") or []]
    for log in logs:
        found.append(log["address"])
        event = log.get("event") or {}
        if event.get("name") == "Transfer":
            found += [event["args"]["from"], event["args"]["to"]]
    out = []
    for a in found:
        if a and a.lower() not in out:
            out.append(a.lower())
    return out


class Explorer:
    def __init__(self, directory: str, rollup: str, messenger: str):
        self.dir = directory
        self.accounts = {}  # L2 address -> the transactions that involve it
        self.index = {
            "rollup": rollup.lower(), "messenger": messenger.lower(), "l2Blocks": [], "l1Txs": [],
            "deposits": {}, "withdrawals": {},
        }
        for sub in ("l2/blocks", "l2/txs", "l1/txs", "l2/accounts"):
            os.makedirs(os.path.join(directory, sub), exist_ok=True)

    def write(self, path: str, value) -> None:
        full = os.path.join(self.dir, path)
        with open(full + ".tmp", "w") as f:
            json.dump(value, f)
        os.replace(full + ".tmp", full)

    def save_index(self) -> None:
        self.index["updatedAt"] = int(time.time())
        self.write("index.json", self.index)

    def add_l1_tx(self, tx: dict, kind: str, **links) -> None:
        tx.update({"kind": kind, **links})
        self.write(f"l1/txs/{tx['hash']}.json", tx)
        self.index["l1Txs"] = [t for t in self.index["l1Txs"] if t["hash"] != tx["hash"]]
        self.index["l1Txs"].append({
            "hash": tx["hash"], "kind": kind, "block": tx["block"], "timestamp": tx["timestamp"],
            "gasUsed": tx["gasUsed"], "addresses": addresses(tx), **links,
        })
        if kind == "deposit":
            for log in tx["logs"]:
                event = log.get("event") or {}
                if event.get("name") == "L1MessageSent":
                    a = event["args"]
                    entry = self.index["deposits"].setdefault(str(a["index"]), {})
                    entry.update({"index": a["index"], "from": a["sender"], "to": a["to"], "value": a["value"], "l1Tx": tx["hash"], "l1Block": tx["block"]})
        if kind == "withdrawal claim":
            index = tx["call"]["args"]["message"]["index"]
            entry = self.index["withdrawals"].setdefault(str(index), {"index": index})
            entry.update({"l1Tx": tx["hash"], "l1Block": tx["block"]})

    def update_accounts(self, state, block: dict, transactions: list) -> None:
        """Writes the state after `block` of every L2 account it touched."""
        touched = {block["feeRecipient"].lower()}
        for tx in transactions:
            for a in addresses(tx):
                touched.add(a)
                self.accounts.setdefault(a, []).append({"hash": tx["hash"], "block": block["number"], "kind": tx["kind"]})
        for a in touched:
            account = state.get_account_optional(Address(bytes.fromhex(a[2:])))
            entry = {"address": a, "block": block["number"], "txs": self.accounts.get(a, [])[-200:], "exists": account is not None}
            if account is not None:
                code = b"" if account.code_hash == EMPTY_CODE_HASH else state.get_code(account.code_hash)
                entry.update({"balance": str(int(account.balance)), "nonce": int(account.nonce), "codeSize": len(code), "codeHash": hx(account.code_hash)})
                if 0 < len(code) <= 32768:
                    entry["code"] = hx(code)
            if a == self.index["messenger"]:
                slot = lambda n: int(state.get_storage(Address(bytes.fromhex(a[2:])), n.to_bytes(32, "big")))  # noqa: E731
                entry["state"] = {
                    "l1Rollup": "0x%040x" % slot(0), "sentMessages": slot(2),
                    "provenAnchorTimestamp": slot(3), "provenL1MessageRoot": "0x%064x" % slot(4),
                }
            self.write(f"l2/accounts/{a}.json", entry)

    def add_l2_block(self, block: dict, transactions: list) -> None:
        kinds = {}
        for tx in transactions:
            kinds[tx["kind"]] = kinds.get(tx["kind"], 0) + 1
            tx["block"] = block["number"]
            self.write(f"l2/txs/{tx['hash']}.json", tx)
            for frame in tx.get("frames") or [tx]:
                call = frame.get("call") or {}
                if call.get("function") == "claimL1Message":
                    m = call["args"]["message"]
                    entry = self.index["deposits"].setdefault(str(m["index"]), {"index": m["index"]})
                    entry.update({"l2Tx": tx["hash"], "l2Block": block["number"]})
            for log in (tx.get("logs") or []) + [l for f in tx.get("frames") or [] for l in f.get("logs") or []]:
                event = log.get("event") or {}
                if event.get("name") == "L2MessageSent":
                    a = event["args"]
                    entry = self.index["withdrawals"].setdefault(str(a["index"]), {"index": a["index"]})
                    entry.update({"from": a["sender"], "to": a["to"], "value": a["value"], "l2Tx": tx["hash"], "l2Block": block["number"]})
        block["transactions"] = [
            {"hash": tx["hash"], "kind": tx["kind"], "from": tx["from"], "gasUsed": tx["gasUsed"], "bytes": tx["bytes"]}
            for tx in transactions
        ]
        self.write(f"l2/blocks/{block['number']}.json", block)
        self.index["l2Blocks"].append({
            "number": block["number"], "hash": block["hash"], "timestamp": block["timestamp"],
            "transactions": len(transactions), "kinds": kinds, "gasUsed": block["gasUsed"],
            "l1Tx": block["l1"]["tx"], "l1Block": block["l1"]["block"], "payloadBytes": block["payloadBytes"],
        })
