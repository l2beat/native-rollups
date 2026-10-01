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
import re
import time

from eth_abi import decode as abi_decode
from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes0
from ethereum_types.numeric import Uint

from ethereum.crypto.hash import keccak256
from ethereum.state import EMPTY_CODE_HASH, Address
from ethereum.forks.amsterdam.blocks import FrameTransactionReceipt, decode_receipt
from ethereum.forks.amsterdam.transactions import SetCodeTransaction, decode_transaction, get_transaction_hash, recover_sender
from ethereum.forks.amsterdam.utils.address import compute_contract_address
from ethereum.forks.amsterdam.vm.eoa_delegation import recover_authority
from ethereum.forks.amsterdam.transactions.frame_transaction import FrameTransaction

MODES = {0: "DEFAULT", 1: "VERIFY", 2: "SENDER"}
FLAGS = {1: "APPROVE_PAYMENT", 2: "APPROVE_EXECUTION", 4: "ATOMIC_BATCH"}
# Arguments too large to show, summarized by name.
SUMMARIZED = {"accountProof", "storageProof", "l1Header"}


def abi_type(param: dict) -> str:
    """The canonical type of an ABI parameter, with tuples spelled out."""
    t = param["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(abi_type(c) for c in param["components"]) + ")" + t[len("tuple"):]
    return t


def named(param: dict, value):
    """A decoded value with its tuples as objects keyed by component name."""
    t = param["type"]
    if t.endswith("]"):
        inner = {**param, "type": t[: t.rindex("[")]}
        return [named(inner, v) for v in value]
    if t == "tuple":
        return {c["name"] or str(i): named(c, v) for i, (c, v) in enumerate(zip(param["components"], value))}
    return value


class Signatures:
    """Function and event signatures and contract creation codes from the
    ABIs found under some directories, as explorers use signature databases
    and verified sources: JSON artifacts with an `abi`, such as Foundry's,
    and abigen's Go bindings, which embed the ABI and the creation code."""

    GO_BINDING = re.compile(r'(\w+)MetaData = &bind\.MetaData\{(.*?)\n\}', re.S)
    GO_ABI = re.compile(r'ABI: "((?:[^"\\]|\\.)*)"')
    GO_BIN = re.compile(r'Bin: "0x([0-9a-fA-F]+)"')

    def __init__(self, directories: list):
        self.roots = [os.path.abspath(d) for d in directories]
        self.functions = {}  # selector -> ABI entry
        self.events = {}  # topic -> ABI entries, one per indexing
        self.creations = {}  # creation code -> the artifacts and bindings that have it
        for directory in self.roots:
            for root, _, files in os.walk(directory):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        if name.endswith(".json"):
                            artifact = json.load(open(path))
                            abi = artifact.get("abi")
                            self.add(abi if isinstance(abi, list) else [])
                            code = artifact.get("bytecode")
                            code = code.get("object") if isinstance(code, dict) else code
                            # Hardhat names the source file; Foundry has it as the compilation
                            # target, in metadata that some tools keep as a JSON string.
                            metadata = artifact.get("metadata") or {}
                            metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                            target = (metadata.get("settings") or {}).get("compilationTarget") or {}
                            source_name = artifact.get("sourceName") or next(iter(target), None)
                            self.creation(artifact.get("contractName") or name.split(".")[0], code, path, source_name)
                        elif name.endswith(".go"):
                            for m in self.GO_BINDING.finditer(open(path).read()):
                                abi, code = self.GO_ABI.search(m.group(2)), self.GO_BIN.search(m.group(2))
                                if abi:
                                    self.add(json.loads(json.loads('"' + abi.group(1) + '"')))
                                if code:
                                    self.creation(m.group(1), code.group(1), path)
                    except (ValueError, AttributeError, UnicodeDecodeError):
                        continue

    def creation(self, name: str, code, path: str, source_name: str | None = None) -> None:
        # Skip interfaces, abstract contracts, and code with unlinked libraries.
        if isinstance(code, str) and len(code) > 4 and "_" not in code:
            entry = {"name": name, "path": path, "sourceName": source_name}
            self.creations.setdefault(bytes.fromhex(code.removeprefix("0x")), []).append(entry)

    def contract(self, init_code: bytes) -> dict:
        """The name of the contract whose creation code starts `init_code`,
        followed by its constructor arguments, and the source file that
        compiles to that code, if one is on disk."""
        matches = [code for code in self.creations if init_code.startswith(code)]
        if not matches:
            return {"name": None, "source": None}
        entries = self.creations[max(matches, key=len)]
        source = next((s for s in map(self.source, entries) if s), None)
        return {"name": entries[0]["name"], "source": source}

    def source(self, entry: dict) -> str | None:
        """The source file of an artifact or binding: the artifact's
        `sourceName` under a directory above it, as in npm packages, or a file
        next to it or in its package that declares the contract."""
        directory = os.path.dirname(entry["path"])
        if entry["sourceName"]:
            parts = entry["sourceName"].split("/")
            for up in range(4):
                base = os.path.normpath(os.path.join(directory, *[".."] * up))
                for i in range(len(parts) - 1):
                    candidate = os.path.join(base, *parts[i:])
                    if os.path.isfile(candidate):
                        return candidate
        declares = re.compile(rf"^\s*(abstract\s+)?contract\s+{re.escape(entry['name'])}\b", re.M)
        package = next(
            (d for d in (os.path.normpath(os.path.join(directory, *[".."] * up)) for up in range(1, 3))
             if os.path.isfile(os.path.join(d, "package.json"))),
            None,
        )
        for root in [directory] + ([package] if package else []):
            for dirpath, _, files in os.walk(root):
                for name in sorted(files):
                    path = os.path.join(dirpath, name)
                    if name.endswith(".sol") and declares.search(open(path, errors="replace").read()):
                        return path
        return None

    def display(self, path: str) -> str:
        """A source path as readers know it: a package's name and version, or
        a repository's name."""
        parts = path.split(os.sep)
        if "node_modules" in parts:
            rest = parts[len(parts) - parts[::-1].index("node_modules"):]
            size = 2 if rest[0].startswith("@") else 1
            package = os.path.join(os.sep.join(parts[: len(parts) - len(rest)]), *rest[:size], "package.json")
            version = json.load(open(package)).get("version") if os.path.isfile(package) else None
            return "/".join(rest[:size]) + (f"@{version}" if version else "") + "/" + "/".join(rest[size:])
        # A source can sit beside the directory its artifact is in, as
        # Foundry's src/ beside out/.
        root = next((r for r in self.roots if path.startswith(os.path.dirname(r) + os.sep)), os.path.dirname(path))
        return os.path.relpath(path, os.path.dirname(os.path.dirname(root)))

    def add(self, abi: list) -> None:
        for entry in abi:
            if not isinstance(entry, dict) or entry.get("type") not in ("function", "event") or entry.get("anonymous"):
                continue
            signature = f"{entry['name']}({','.join(abi_type(p) for p in entry['inputs'])})"
            if entry["type"] == "function":
                self.functions.setdefault(keccak256(signature.encode())[:4], entry)
            else:
                entries = self.events.setdefault(keccak256(signature.encode()), [])
                indexed = [p["indexed"] for p in entry["inputs"]]
                if all([q["indexed"] for q in e["inputs"]] != indexed for e in entries):
                    entries.append(entry)


SIGNATURES = Signatures([])


def load_signatures(directories: list) -> None:
    global SIGNATURES
    SIGNATURES = Signatures(directories)


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


def signature(entry: dict) -> str:
    """A human-readable signature, with parameter names."""
    def param(p: dict) -> str:
        t = p["type"]
        if t.startswith("tuple"):
            t = "(" + ", ".join(param(c) for c in p["components"]) + ")" + t[len("tuple"):]
        return " ".join(x for x in (t, "indexed" if p.get("indexed") else "", p.get("name", "")) if x)
    return f"{entry['name']}({', '.join(param(p) for p in entry['inputs'])})"


def decode_call(data: bytes) -> dict | None:
    """The function and arguments of a call whose signature is known."""
    entry = SIGNATURES.functions.get(bytes(data[:4])) if len(data) >= 4 else None
    if entry is None:
        return None
    try:
        values = abi_decode([abi_type(p) for p in entry["inputs"]], bytes(data[4:]))
    except Exception:
        return None
    args = {}
    for i, (p, value) in enumerate(zip(entry["inputs"], values)):
        name = p["name"] or str(i)
        if name in SUMMARIZED:
            value = {"bytes": len(value), "blockHash": hx(keccak256(value))} if isinstance(value, bytes) else {
                "nodes": len(value), "bytes": sum(len(n) for n in value)
            }
        args[name] = jsonable(named(p, value))
    call = {"function": entry["name"], "signature": signature(entry), "args": args}
    if entry["name"] == "claimL1Message":
        call["pathToRoot"] = path_to_root(*values[:2])
    names = [p["name"] for p in entry["inputs"]]
    if "accountProof" in names and "storageProof" in names:
        call["proofs"] = {k: trie_path(values[names.index(k)]) for k in ("accountProof", "storageProof")}
    return call


def trie_path(proof: tuple) -> dict:
    """The nodes of a Merkle Patricia proof from its root down: for a branch,
    which children it has and the one the proof follows, found by the next
    node's hash; for an extension or leaf, its part of the key."""
    nodes, key = [], ""
    for i, raw in enumerate(proof):
        node = rlp.decode(raw)
        following = keccak256(proof[i + 1]) if i + 1 < len(proof) else None
        if len(node) == 17:
            taken = next((d for d, child in enumerate(node[:16]) if child == following), None)
            entry = {"kind": "branch", "children": [len(child) > 0 for child in node[:16]], "nibble": taken}
            key += "?" if taken is None else f"{taken:x}"
        else:
            path = bytes(node[0]).hex()
            flag = int(path[0], 16)
            entry = {"kind": "leaf" if flag >= 2 else "extension", "path": path[1:] if flag & 1 else path[2:]}
            key += entry["path"]
            if flag >= 2:
                value = rlp.decode(node[1])
                entry["decoded"] = {
                    "nonce": int.from_bytes(value[0], "big"), "balance": str(int.from_bytes(value[1], "big")),
                    "storageRoot": hx(value[2]), "codeHash": hx(value[3]),
                } if isinstance(value, list) else {"word": hx(bytes(value).rjust(32, b"\0"))}
        entry["hash"] = hx(keccak256(raw))
        nodes.append(entry)
    return {"root": hx(keccak256(proof[0])), "key": key, "nodes": nodes}


MESSAGE_TREE_DEPTH = 32  # MessageTree.DEPTH


def path_to_root(m: tuple, path: list) -> dict:
    """The nodes from a message's leaf up to the message tree's root, as
    `MessageTree.rootFromPath` computes them."""
    sender, to, value, fee, data, index = m
    node = keccak256(
        bytes.fromhex(sender[2:]) + bytes.fromhex(to[2:]) + value.to_bytes(32, "big") + fee.to_bytes(32, "big")
        + keccak256(data) + index.to_bytes(32, "big")
    )
    out = {"leaf": hx(node), "levels": []}
    zero = bytes(32)
    for height in range(MESSAGE_TREE_DEPTH):
        right = index >> height & 1 == 1
        # Above the path, the siblings are empty subtrees on the right.
        sibling = path[height] if height < len(path) else zero
        node = keccak256(sibling + node) if right else keccak256(node + sibling)
        if height < len(path):
            # A sibling equal to the empty subtree holds no messages yet.
            out["levels"].append({"right": right, "sibling": hx(sibling), "node": hx(node), "empty": sibling == zero})
        zero = keccak256(zero + zero)
    out["root"] = hx(node)
    return out


def decode_log(address: str, topics: list, data: bytes) -> dict:
    entry = {"address": address.lower(), "topics": [hx(t) for t in topics], "data": hx(data)}
    for event in SIGNATURES.events.get(bytes(topics[0]), []) if topics else []:
        indexed = [p for p in event["inputs"] if p["indexed"]]
        if len(indexed) != len(topics) - 1:
            continue
        try:
            values = iter(abi_decode([abi_type(p) for p in event["inputs"] if not p["indexed"]], bytes(data)))
            args = {}
            topic = iter(topics[1:])
            for i, p in enumerate(event["inputs"]):
                if p["indexed"]:
                    # Dynamic indexed values are only their hash.
                    raw = bytes(next(topic))
                    static = not (p["type"] in ("string", "bytes") or p["type"].endswith("]") or p["type"].startswith("tuple"))
                    value = abi_decode([p["type"]], raw)[0] if static else raw
                else:
                    value = next(values)
                args[p["name"] or str(i)] = jsonable(named(p, value))
        except Exception:
            continue
        entry["event"] = {"name": event["name"], "signature": signature(event), "args": args}
        break
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


# EIP-7997's CREATE2 factory: its calldata is a salt and an init code.
CREATE2_FACTORY = "0x4e59b44847b379578588920ca78fbf26c0b4956c"


def created(sender: bytes, nonce: int, to, data: bytes, succeeded: bool) -> list:
    """The contracts a transaction creates, directly or through EIP-7997's
    factory, with their names when the explorer knows the creation code."""
    if not succeeded:
        return []
    if not len(to):
        return [{"address": hx(compute_contract_address(sender, Uint(nonce))), **SIGNATURES.contract(data)}]
    if hx(to) == CREATE2_FACTORY and len(data) >= 32:
        salt, init_code = data[:32], data[32:]
        address = keccak256(b"\xff" + bytes(to) + salt + keccak256(init_code))[12:]
        return [{"address": hx(address), **SIGNATURES.contract(init_code)}]
    return []


def l2_kind(tx: dict, messenger: str) -> str:
    calls = [f.get("call") or {} for f in tx.get("frames", [])] or [tx.get("call") or {}]
    functions = {c.get("function") for c in calls}
    if "claimL1Message" in functions:
        return "deposit claim"
    if tx.get("to") == messenger and "sendMessage" in functions:
        return "withdrawal"
    if tx.get("type") == 6:
        return "frame transaction"
    if tx.get("type") == 4:
        return "delegation"
    if tx.get("to") is None or tx.get("to") == CREATE2_FACTORY:
        return "deploy"
    if tx.get("data") == "0x":
        return "transfer"
    return "call"


def fees(tx, gas_used: int, base_fee: int) -> dict:
    """What a transaction offered to pay per gas, what it paid, and where
    the fee went: the base fee is burned, the rest goes to the fee
    recipient."""
    prices = tx.fees if isinstance(tx, FrameTransaction) else tx
    if hasattr(prices, "max_fee_per_gas"):
        offered = {"maxFeePerGas": int(prices.max_fee_per_gas), "maxPriorityFeePerGas": int(prices.max_priority_fee_per_gas)}
        price = min(int(prices.max_fee_per_gas), base_fee + int(prices.max_priority_fee_per_gas))
    else:
        offered = {"gasPrice": int(tx.gas_price)}
        price = int(tx.gas_price)
    return {
        **offered, "baseFeePerGas": base_fee, "effectiveGasPrice": price,
        "fee": gas_used * price, "burned": gas_used * base_fee, "tip": gas_used * (price - base_fee),
    }


def l2_transaction(raw: bytes, receipt, gas_used: int, messenger: str, base_fee: int, has_code=lambda a: None) -> dict:
    tx = decode_transaction(raw)
    out = {"hash": hx(get_transaction_hash(raw)), "type": raw[0] if raw[0] < 0x80 else 0, "bytes": len(raw), "gasUsed": gas_used}
    if isinstance(tx, FrameTransaction):
        sender = hx(tx.sender)
        out.update({
            "from": sender, "nonce": int(tx.nonce),
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
                # Accounts without code run EIP-8141's default code.
                "targetHasCode": has_code(target),
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
        sender = recover_sender(tx)
        out["created"] = created(sender, int(tx.nonce), tx.to, bytes(tx.data), receipt.succeeded)
        if isinstance(tx, SetCodeTransaction):
            out["authorizations"] = []
            for a in tx.authorizations:
                try:
                    authority = hx(recover_authority(a))
                except Exception:  # an invalid signature, which the transaction skips
                    authority = None
                out["authorizations"].append({"chainId": int(a.chain_id), "address": hx(a.address), "nonce": int(a.nonce), "authority": authority})
        out.update({
            "from": hx(sender), "to": hx(tx.to) if len(tx.to) else None, "nonce": int(tx.nonce),
            "value": int(tx.value), "data": hx(tx.data), "gasLimit": int(tx.gas),
            "call": decode_call(bytes(tx.data)),
            "status": int(receipt.succeeded),
            "logs": [decode_log(hx(log.address), log.topics, log.data) for log in receipt.logs],
        })
    out.update(fees(tx, gas_used, base_fee))
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
        "gasLimit": int(tx["gas"], 16) if tx.get("gas") else None,
        "effectiveGasPrice": int(receipt.get("effectiveGasPrice", "0x0"), 16),
        "blobVersionedHashes": tx.get("blobVersionedHashes") or [],
        "blobGasUsed": int(receipt.get("blobGasUsed") or "0x0", 16),
        "blobGasPrice": int(receipt.get("blobGasPrice") or "0x0", 16),
        "logs": [decode_log(log["address"], [bytes.fromhex(t[2:]) for t in log["topics"]], bytes.fromhex(log["data"][2:])) for log in receipt["logs"]],
    }
    base_fee, price, gas = int(block.get("baseFeePerGas") or "0x0", 16), out["effectiveGasPrice"], out["gasUsed"]
    offered = ("maxFeePerGas", "maxPriorityFeePerGas") if tx.get("maxFeePerGas") else ("gasPrice",)
    out.update(jsonable({
        **{k: int(tx[k], 16) for k in offered}, "baseFeePerGas": base_fee,
        "fee": gas * price, "burned": gas * base_fee, "tip": gas * (price - base_fee),
        "blobFee": out["blobGasUsed"] * out["blobGasPrice"],
    }))
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


def method(tx: dict) -> str | None:
    """The function a transaction calls: its name, or its selector."""
    calls = [f for f in tx.get("frames") or [tx] if (f.get("data") or "0x") != "0x"]
    if tx.get("kind") == "deploy" or not calls:
        return None
    return ", ".join((c.get("call") or {}).get("function") or c["data"][:10] for c in calls)


def addresses(tx: dict) -> list:
    """The addresses a decoded transaction involves: its sender, target,
    frame targets and payer, the emitters of its events, and the accounts its
    ETH transfer logs move ETH between."""
    found = [tx.get("from"), tx.get("to"), tx.get("payer")] + [f["target"] for f in tx.get("frames") or []]
    found += [c["address"] for c in tx.get("created") or []]
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
    def __init__(self, directory: str, rollup: str, messenger: str, flatten=None):
        self.dir = directory
        # Flattens a source file to one contract's flat source, or None.
        self.flatten = flatten
        self.flat = {}
        self.accounts = {}  # L2 address -> the transactions that involve it
        self.index = {
            "rollup": rollup.lower(), "messenger": messenger.lower(), "l2Blocks": [], "l1Txs": [],
            "deposits": {}, "withdrawals": {}, "contracts": {},
        }
        for sub in ("l2/blocks", "l2/txs", "l1/txs", "l2/accounts", "l2/sources"):
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
                    entry.update({
                        "index": a["index"], "from": a["sender"], "to": a["to"], "value": a["value"], "fee": a["fee"],
                        "l1Tx": tx["hash"], "l1Block": tx["block"],
                    })
        if kind == "withdrawal claim":
            index = tx["call"]["args"]["m"]["index"]
            entry = self.index["withdrawals"].setdefault(str(index), {"index": index})
            entry.update({"l1Tx": tx["hash"], "l1Block": tx["block"]})

    def write_source(self, contract: dict) -> None:
        """The source a created contract was compiled from, flattened as
        L2BEAT flattens the contracts it tracks, or as is if that fails."""
        key = (contract["source"], contract["name"])
        if key not in self.flat:
            flat = self.flatten(*key) if self.flatten else None
            self.flat[key] = flat or open(contract["source"], errors="replace").read()
        self.write(f"l2/sources/{contract['address']}.json", {
            "name": contract["name"], "file": SIGNATURES.display(contract["source"]), "flat": self.flat[key],
            "flattened": self.flatten is not None,
        })

    def update_accounts(self, state, block: dict, transactions: list) -> None:
        """Writes the state after `block` of every L2 account it touched."""
        touched = {block["feeRecipient"].lower()}
        for tx in transactions:
            for a in addresses(tx):
                touched.add(a)
                self.accounts.setdefault(a, []).append({
                    "hash": tx["hash"], "block": block["number"], "time": block["timestamp"], "kind": tx["kind"],
                    "method": method(tx), "from": tx["from"], "to": tx.get("to"), "value": tx.get("value", 0), "fee": tx.get("fee"),
                })
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
            for c in tx.get("created") or []:
                self.index["contracts"][c["address"]] = {"name": c["name"], "tx": tx["hash"], "source": bool(c.get("source"))}
                if c.get("source"):
                    self.write_source(c)
                    c["source"] = SIGNATURES.display(c["source"])
            self.write(f"l2/txs/{tx['hash']}.json", tx)
            for frame in tx.get("frames") or [tx]:
                call = frame.get("call") or {}
                if call.get("function") == "claimL1Message":
                    m = call["args"]["m"]
                    tx["deposit"] = m["index"]
                    entry = self.index["deposits"].setdefault(str(m["index"]), {"index": m["index"]})
                    entry.update({"l2Tx": tx["hash"], "l2Block": block["number"]})
            for log in (tx.get("logs") or []) + [l for f in tx.get("frames") or [] for l in f.get("logs") or []]:
                event = log.get("event") or {}
                if event.get("name") == "L2MessageSent":
                    a = event["args"]
                    entry = self.index["withdrawals"].setdefault(str(a["index"]), {"index": a["index"]})
                    entry.update({
                        "from": a["sender"], "to": a["to"], "value": a["value"], "fee": a["fee"],
                        "l2Tx": tx["hash"], "l2Block": block["number"],
                    })
        # What the block's transaction table shows, so it needs no other file.
        block["transactions"] = [
            {k: tx.get(k) for k in ("hash", "kind", "from", "to", "value", "gasUsed", "bytes", "status", "fee", "deposit")} | {"method": method(tx)}
            for tx in transactions
        ]
        self.write(f"l2/blocks/{block['number']}.json", block)
        self.index["l2Blocks"].append({
            "number": block["number"], "hash": block["hash"], "timestamp": block["timestamp"],
            "transactions": len(transactions), "kinds": kinds, "gasUsed": block["gasUsed"],
            "l1Tx": block["l1"]["tx"], "l1Block": block["l1"]["block"], "payloadBytes": block["payloadBytes"],
        })
