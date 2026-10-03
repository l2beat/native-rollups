"""
A minimal L2 node and mock prover for the native rollup.

It runs the L2 chain with execution-specs under the L1 stateless validation
program's rules, holds the chain's state in memory, and serves a JSON-RPC
with a mempool. A native rollup runs its L1's rules, and the L1 here has
EIP-8141 frame transactions, so the rules come from execution-specs
`projects/zkevm` merged with `eips/bogota/eip-8141` (Amsterdam with
EIP-8141).

The genesis holds the system contracts the Specification requires, the
`L2Messenger` predeploy with the pre-minted gas token supply, and the frame
introspection helper the messenger reads the state gas left with. No account
is funded: all L2 ETH comes from deposits.

The node sequences a rollup with the book's preconfirmations customization
(`SequencedNativeRollup`). The operator asks for each block with
`nr_preconfirm`: the node builds it on its latest block, validates it with
`run_stateless_guest`, and signs the EIP-8288 dependency only for blocks the
program accepts, as a stand-in for a zkVM proof of that program. It adds the
block to its chain at once, with the sequencer's preconfirmation, a
signature over the block's number, hash and anchor, and keeps what posting
it takes until the operator asks for it with `nr_waitingPosts`. It follows the
rollup contract on L1 and marks blocks posted once the contract has them:
the RPC's `latest` block is preconfirmed, and its `safe` block posted, the
one L2 to L1 messages are proven against.

Blocks take the mempool's transactions, and the node holds no keys: the
operator sends the sequencer's and the prover's with each request. Users
claim their deposits like any transaction, with frame transactions they sign
themselves, which `l2_claims.py` builds from L1 data and this RPC. The node
admits a frame transaction only if it is valid on its head state. A claim
runs before any frame approves payment, so a claimer that cannot pay up
front may only claim messages that pay itself, and each message has one
pending claim.

Each block goes to L1 in EIP-8142 payload blobs, which encode its BAL and
transactions. The L1 program does not implement EIP-8142 yet, so the node
derives the payload blobs itself, as `engine_newPayload`'s native variant
does, and signs a public input that binds their versioned hashes, as a
program with EIP-8142 would.

Run with the environment of that execution-specs merge:

    uv run --project <execution-specs> python script/l2_node.py genesis --state <file> --l1-rollup <address>
    uv run --project <execution-specs> python script/l2_node.py serve --state <file> --l1-rpc <url> --rollup <address>

The state file keeps the genesis configuration, the blocks the rollup
contract has, those preconfirmed after them, which `serve` replays when it
starts, and every preconfirmation.
"""

import argparse
import json
import os
import subprocess
import threading
import time
import traceback
import typing
import urllib.request
from dataclasses import dataclass
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rlp as pyrlp
from eth_abi import decode as abi_decode
from trie import HexaryTrie

from execution_testing import Alloc, Environment
from execution_testing import Account as TestAccount
from execution_testing import Address as TestAddress
from execution_testing.client_clis import ExecutionSpecsTransitionTool
from execution_testing.fixtures.blockchain import BlockchainFixture
from execution_testing.forks import Bogota
from execution_testing.specs.blockchain import BlockchainTest

from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes, Bytes0, Bytes20, Bytes32
from ethereum_types.numeric import U64, U256, Uint

from ethereum.crypto.hash import keccak256
from ethereum.exceptions import EthereumException
from ethereum.merkle_patricia_trie import copy_trie, root, trie_get, trie_set
from ethereum.state import Account, Address
from ethereum.state_mpt import State, apply_changes_to_state, set_account, set_storage, store_code
from ethereum.forks.amsterdam import fork, vm
from ethereum.forks.amsterdam.block_access_lists import BlockAccessListBuilder
from ethereum.forks.amsterdam.blocks import Block, FrameTransactionReceipt, Header, decode_receipt
from ethereum.forks.amsterdam.execution_engine.requests import decode_execution_requests
from ethereum.forks.amsterdam.requests import compute_requests_hash
from ethereum.forks.amsterdam.state_tracker import BlockState, TransactionState, extract_block_diff, get_account, incorporate_tx_into_block
from ethereum.forks.amsterdam.state_tracker import set_account as set_tracked_account
from ethereum.forks.amsterdam.stateless import STATELESS_INPUT_SCHEMA_ID
from ethereum.forks.amsterdam.stateless_guest import deserialize_stateless_input, run_stateless_guest
from ethereum.forks.amsterdam.stateless_host import (
    build_stateless_input,
    deserialize_stateless_output,
    serialize_stateless_input,
)
from ethereum.forks.amsterdam.stateless_host_exec_witness import build_execution_witness
from ethereum.forks.amsterdam.transactions import (
    AccessListTransaction,
    BlobTransaction,
    FeeMarketTransaction,
    LegacyTransaction,
    SetCodeTransaction,
    decode_transaction,
    recover_sender,
    validate_transaction,
)
from ethereum.forks.amsterdam.transactions.frame_transaction import FrameMode, FrameTransaction, validate_frame_transaction
from ethereum.forks.amsterdam.utils.address import compute_contract_address
from ethereum.forks.amsterdam.vm.gas import GasCosts, allocate_evm_gas
from ethereum.forks.amsterdam.vm.interpreter import process_top_level
from ethereum.utils.ssz import _to_view

import block_in_blobs as bib
from ssz_roots import container4, payload_root, public_input_root, versioned_hashes_root  # noqa: E402

L2_CHAIN_ID = 8079
L2_GAS_LIMIT = 60_000_000
LEANSTARK_SCHEME = 0x11
FEE_RECIPIENT = Address((0xFEE).to_bytes(20, "big"))
EXTRA_DATA = b"native-rollup"

L2_MESSENGER = "0x8079000000000000000000000000000000000001"
MESSENGER = Address(bytes.fromhex(L2_MESSENGER[2:]))
PREMINT = 10**27
MESSENGER_ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "out", "L2Messenger.sol", "L2Messenger.json")
# The frame introspection helper, at `L2Messenger.FRAMES_HELPER`.
FRAMES_HELPER = "0x8079000000000000000000000000000000000002"
FRAMES_HELPER_CODE = os.path.join(os.path.dirname(__file__), "..", "frames", "frame_introspection.hex")
MESSAGE_TYPE = "(address,address,uint256,uint256,uint256,bytes,uint256)"
L2_MESSAGE_SENT = keccak256(b"L2MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")
CLAIM_SELECTOR = keccak256(f"claimL1Message({MESSAGE_TYPE},bytes32[],address)".encode())[:4]
PROVE_ROOT_SELECTOR = keccak256(b"proveL1MessageRoot(uint256,bytes,bytes[],bytes[])")[:4]
BLOCK_HASH_SELECTOR = keccak256(b"blockHash()")[:4]
MAX_TIMESTAMP_LAG_SELECTOR = keccak256(b"MAX_TIMESTAMP_LAG()")[:4]
# A posted block's anchor must be one of the last 256 L1 blocks, which
# BLOCKHASH reaches.
BLOCKHASH_WINDOW = 256
# How long before L1 would refuse a preconfirmed block the node gives up on
# it, leaving room for a post on its way.
EXPIRY_MARGIN_BLOCKS = 4
EXPIRY_MARGIN_SECONDS = 60
# How often the node snapshots the posted chain, in posted blocks, and how
# many recent blocks the RPC keeps in memory.
SNAPSHOT_INTERVAL = 300
KEEP_BLOCKS = 2048
# The priority fee the RPC suggests. Blocks include any transaction that
# pays the base fee.
PRIORITY_FEE = 10**6
# Execution-specs runs the EVM in Python, which bounds the transactions a
# block can take in reasonable time.
MAX_BLOCK_TXS = 100


def cast(*args: str) -> str:
    return subprocess.run(["cast", *args], check=True, capture_output=True, text=True).stdout.strip()


def json_rpc(url: str, method: str, *params):
    request = urllib.request.Request(
        url,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        out = json.load(response)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def hx(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def address(value: str) -> Address:
    return Address(bytes.fromhex(value[2:]))


def header(fields: dict) -> Header:
    """A `Header` from field values, converted to the field types."""
    types = typing.get_type_hints(Header)
    return Header(**{name: types[name](value) for name, value in fields.items()})


def raw_transaction(tx) -> bytes:
    """A block's transaction as sent: typed bytes, or a legacy RLP list."""
    return rlp.encode(tx) if isinstance(tx, LegacyTransaction) else bytes(tx)


def block_transaction(raw: bytes):
    """A transaction in the form blocks hold it."""
    return rlp.decode_to(LegacyTransaction, raw) if raw[0] >= 0xC0 else Bytes(raw)


def sender_of(tx) -> Address:
    return tx.sender if isinstance(tx, FrameTransaction) else recover_sender(tx)


# ---------------------------------------------------------------------------
# Genesis
# ---------------------------------------------------------------------------


def copy_state(state: State) -> State:
    """A copy of `state` that changes to `state` do not reach. Tries only
    hold frozen values, so copies share them."""
    return State(
        _main_trie=copy_trie(state._main_trie),
        _storage_tries={a: copy_trie(t) for a, t in state._storage_tries.items()},
        _code_store=dict(state._code_store),
    )


def dump_state(state: State) -> bytes:
    """The accounts, storage and code of `state`, as RLP."""
    accounts = [[a, x.nonce, x.balance, x.code_hash] for a, x in state._main_trie._data.items() if x is not None]
    storage = [[a, [[k, v] for k, v in t._data.items()]] for a, t in state._storage_tries.items()]
    return rlp.encode([accounts, storage, list(state._code_store.values())])


def load_state(data: bytes) -> State:
    """The state `dump_state` stored, rebuilt with execution-specs' own
    functions."""
    accounts, storage, code = rlp.decode(data)
    state = State()
    for c in code:
        store_code(state, Bytes(c))
    for a, nonce, balance, code_hash in accounts:
        account = Account(Uint.from_be_bytes(nonce), U256.from_be_bytes(balance), Bytes32(code_hash))
        set_account(state, Bytes20(a), account)
    for a, slots in storage:
        for k, v in slots:
            set_storage(state, Bytes20(a), Bytes32(k), U256.from_be_bytes(v))
    return state


def genesis_fixture(config: dict) -> dict:
    """The genesis, as EEST builds it with the fork's system contracts."""
    test = BlockchainTest(
        fork=Bogota,
        pre=Alloc(
            {
                TestAddress(L2_MESSENGER): TestAccount(
                    code=bytes.fromhex(config["messengerCode"][2:]),
                    balance=PREMINT,
                    storage={0: int(config["l1Rollup"], 16)},
                ),
                TestAddress(FRAMES_HELPER): TestAccount(code=bytes.fromhex(config["framesHelperCode"][2:])),
            }
        ),
        post={},
        chain_id=L2_CHAIN_ID,
        genesis_environment=Environment(gas_limit=L2_GAS_LIMIT),
        blocks=[],
    )
    result = test.generate(t8n=ExecutionSpecsTransitionTool(), fixture_format=BlockchainFixture)
    return result.fixture.model_dump(mode="json", by_alias=True)


def genesis_chain(config: dict) -> fork.BlockChain:
    """The chain at genesis, in execution-specs' form."""
    fixture = genesis_fixture(config)
    state = State()
    for addr, account in fixture["pre"].items():
        code_hash = store_code(state, bytes.fromhex(account["code"][2:]))
        set_account(state, address(addr), Account(Uint(int(account["nonce"], 16)), U256(int(account["balance"], 16)), code_hash))
        for key, value in account["storage"].items():
            set_storage(state, address(addr), int(key, 16).to_bytes(32, "big"), U256(int(value, 16)))
    g = fixture["genesisBlockHeader"]
    names = {
        "parent_hash": "parentHash", "ommers_hash": "uncleHash", "coinbase": "coinbase", "state_root": "stateRoot",
        "transactions_root": "transactionsTrie", "receipt_root": "receiptTrie", "bloom": "bloom",
        "difficulty": "difficulty", "number": "number", "gas_limit": "gasLimit", "gas_used": "gasUsed",
        "timestamp": "timestamp", "extra_data": "extraData", "prev_randao": "mixHash", "nonce": "nonce",
        "base_fee_per_gas": "baseFeePerGas", "withdrawals_root": "withdrawalsRoot", "blob_gas_used": "blobGasUsed",
        "excess_blob_gas": "excessBlobGas", "parent_beacon_block_root": "parentBeaconBlockRoot",
        "requests_hash": "requestsHash", "block_access_list_hash": "blockAccessListHash", "slot_number": "slotNumber",
    }
    types = typing.get_type_hints(Header)
    genesis_header = header({
        name: (bytes.fromhex(g[key][2:]) if issubclass(types[name], bytes) else int(g[key], 16))
        for name, key in names.items()
    })
    assert hx(keccak256(rlp.encode(genesis_header))) == g["hash"], "genesis hash"
    return fork.BlockChain(
        blocks=[Block(header=genesis_header, transactions=(), ommers=(), withdrawals=())],
        state=state,
        chain_id=U64(L2_CHAIN_ID),
    )


def genesis(args: argparse.Namespace) -> None:
    code = json.load(open(MESSENGER_ARTIFACT))["deployedBytecode"]["object"]
    helper = "0x" + open(FRAMES_HELPER_CODE).read().strip()
    config = {"l1Rollup": args.l1_rollup, "messengerCode": code, "framesHelperCode": helper, "blocks": []}
    json.dump(config, open(args.state, "w"), indent=2)
    h = genesis_chain(config).blocks[0].header
    print(json.dumps({"genesisHash": hx(keccak256(rlp.encode(h))), "genesisStateRoot": hx(h.state_root)}))


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: str | None = None):
        super().__init__(message)
        self.code, self.data = code, data


@dataclass
class PoolTransaction:
    raw: bytes
    tx: object
    sender: Address
    nonce: int
    arrival: int
    claims: tuple  # the L1 messages it claims


def calls_messenger(frame, selector: bytes) -> bool:
    return len(frame.to) > 0 and bytes(frame.to) == bytes(MESSENGER) and bytes(frame.data[:4]) == selector


def claims(tx) -> list:
    """The L1 messages a frame transaction claims, each with the fee
    recipient its claim names."""
    if not isinstance(tx, FrameTransaction):
        return []
    return [
        abi_decode([MESSAGE_TYPE, "bytes32[]", "address"], bytes(frame.data[4:]))[::2]
        for frame in tx.frames if calls_messenger(frame, CLAIM_SELECTOR)
    ]


@dataclass
class Pending:
    """A block executed on the chain's latest block, before it joins the
    chain."""

    block: Block
    hash: bytes
    block_state: BlockState
    output: vm.BlockOutput
    pool_hashes: list


class Node:
    def __init__(self, state_file: str, l1_rpc: str, rollup: str, beacon: str | None = None):
        self.state_file, self.l1_rpc, self.rollup, self.beacon = state_file, l1_rpc, rollup, beacon
        self.config = json.load(open(state_file))
        self.chain = genesis_chain(self.config)
        self.lock = threading.RLock()
        self.pool: dict[bytes, PoolTransaction] = {}
        self.arrivals = 0
        self.blocks: list[dict] = []  # recent blocks, for the RPC, from block `first`
        self.first = 0
        self.transactions: dict[bytes, tuple] = {}  # hash -> (block number, index)
        self.messages: list[dict] = []  # the logs of every L2 to L1 message
        self.logs_from = 0  # the first block whose logs the RPC has
        self.diffs: dict[int, object] = {}  # state changes of blocks not posted yet
        snapshot = self.load_snapshot() if self.config.get("snapshot") else 0
        if not snapshot:
            self.index(self.chain.blocks[0], None)
        for stored in self.config["blocks"]:
            if int(rlp.decode_to(Header, bytes.fromhex(stored["header"][2:])).number) > snapshot:
                self.commit(self.replay(stored))
        # The state of the latest block the rollup contract has.
        self.posted = int(self.head.number)
        self.posted_state = copy_state(self.chain.state)
        self.diffs.clear()
        # Blocks preconfirmed before the node stopped, which L1 may have since.
        for stored in self.config.setdefault("preconfirmed", []):
            self.commit(self.replay(stored))
        self.config.setdefault("preconfirmations", {})
        self.max_timestamp_lag = None  # the rollup contract's, once read
        # Transactions of preconfirmed blocks the node dropped, valid or not.
        for raw in self.config.pop("repool", []):
            try:
                self.send_raw_transaction(raw)
            except RpcError:
                pass
        print(f"L2 node at block {int(self.head.number)}, block {self.posted} posted", flush=True)

    def load_snapshot(self) -> int:
        """Starts from the last snapshot of the posted chain, and returns its
        block number."""
        number, state, headers, messages = rlp.decode(open(self.config["snapshot"], "rb").read())
        self.chain.state = load_state(state)
        self.chain.blocks = [Block(header=rlp.decode_to(Header, h), transactions=(), ommers=(), withdrawals=()) for h in headers]
        self.first = int(self.chain.blocks[0].header.number)
        for block in self.chain.blocks:
            self.index(block, None)
        self.messages = json.loads(messages)
        self.logs_from = int.from_bytes(number, "big") + 1
        return self.logs_from - 1

    def snapshot(self) -> None:
        """Stores the posted chain, which the node then starts from instead of
        replaying every block: its state, the headers BLOCKHASH reaches, and
        the L2 to L1 messages, which can be claimed at any time. Then drops
        what the snapshot holds from the state file, and the oldest blocks
        from the RPC's memory."""
        n = self.posted
        headers = [rlp.encode(self.block_by_number(k)["header"]) for k in range(max(self.first, n - 254), n + 1)]
        messages = [m for m in self.messages if int(m["blockNumber"], 16) <= n]
        path = self.state_file + ".snapshot"
        with open(path + ".tmp", "wb") as f:
            f.write(rlp.encode([Uint(n), dump_state(self.posted_state), headers, json.dumps(messages).encode()]))
        os.replace(path + ".tmp", path)
        self.config["snapshot"] = path
        self.config["blocks"] = [
            b for b in self.config["blocks"] if int(rlp.decode_to(Header, bytes.fromhex(b["header"][2:])).number) > n
        ]
        self.config["preconfirmations"] = {k: v for k, v in self.config["preconfirmations"].items() if int(k) > n}
        self.save()
        drop = len(self.blocks) - KEEP_BLOCKS
        if drop > 0:
            for block in self.blocks[:drop]:
                for entry in block["transactions"]:
                    self.transactions.pop(entry["hash"], None)
            del self.blocks[:drop]
            self.first += drop
            self.logs_from = max(self.logs_from, self.first)
        print(f"snapshot of the posted chain at L2 block {n}", flush=True)

    def block_by_number(self, number: int) -> dict | None:
        i = number - self.first
        return self.blocks[i] if 0 <= i < len(self.blocks) else None

    def tag_number(self, tag) -> int:
        if tag in ("safe", "finalized"):
            return self.posted
        if tag in ("latest", "pending", None):
            return int(self.head.number)
        if tag == "earliest":
            return 0
        return int(tag, 16)

    def replay(self, stored: dict) -> Pending:
        """Re-executes a stored block on the head."""
        block = Block(
            header=rlp.decode_to(Header, bytes.fromhex(stored["header"][2:])),
            transactions=tuple(block_transaction(bytes.fromhex(t[2:])) for t in stored["transactions"]),
            ommers=(),
            withdrawals=(),
        )
        h = block.header
        block_state = BlockState(pre_state=self.chain.state)
        env = self.environment(block_state, h.timestamp, h.parent_beacon_block_root, h.prev_randao, h.base_fee_per_gas)
        output, included, _ = self.execute(env, list(block.transactions), [])
        assert len(included) == len(block.transactions), "a stored block no longer executes"
        return Pending(block, keccak256(rlp.encode(h)), block_state, output, [])

    def save(self) -> None:
        """Stores the posted and preconfirmed blocks, which the node replays
        when it starts, and the preconfirmations."""
        with open(self.state_file + ".tmp", "w") as f:
            json.dump(self.config, f, indent=1)
        os.replace(self.state_file + ".tmp", self.state_file)

    @property
    def head(self) -> Header:
        return self.chain.blocks[-1].header

    # Execution

    def environment(self, block_state: BlockState, timestamp, anchor, prev_randao, base_fee) -> vm.BlockEnvironment:
        return vm.BlockEnvironment(
            chain_id=self.chain.chain_id,
            state=block_state,
            block_gas_limit=Uint(L2_GAS_LIMIT),
            block_hashes=fork.get_last_256_block_hashes(self.chain),
            coinbase=FEE_RECIPIENT,
            number=self.head.number + Uint(1),
            base_fee_per_gas=Uint(base_fee),
            time=U256(timestamp),
            prev_randao=Bytes32(prev_randao),
            excess_blob_gas=U64(0),
            parent_beacon_block_root=Bytes32(anchor),
            block_access_list_builder=BlockAccessListBuilder(),
            slot_number=U64(0),
        )

    def execute(self, env: vm.BlockEnvironment, required: list, optional: list) -> tuple:
        """Runs a block body, as `apply_body` does, with the `required`
        transactions, then those of `optional` that are valid on top."""
        output = vm.BlockOutput()
        fork.process_unchecked_system_transaction(
            block_env=env, target_address=fork.BEACON_ROOTS_ADDRESS, data=env.parent_beacon_block_root
        )
        fork.process_unchecked_system_transaction(
            block_env=env, target_address=fork.HISTORY_STORAGE_ADDRESS, data=env.block_hashes[-1]
        )
        included, rejected = [], []
        for tx in required:
            fork.process_transaction(env, output, decode_transaction(tx), Uint(len(included)))
            included.append(tx)
        for tx in optional:
            index = Uint(len(included))
            try:
                fork.process_transaction(env, output, decode_transaction(tx), index)
                included.append(tx)
            except Exception as e:
                trie_set(output.transactions_trie, rlp.encode(index), None)
                rejected.append((tx, e))
        env.block_access_list_builder.block_access_index = fork.BlockAccessIndex(Uint(len(included)) + Uint(1))
        fork.process_withdrawals(env, output, ())
        fork.process_general_purpose_requests(block_env=env, block_output=output)
        output.block_access_list = fork.build_block_access_list(env.block_access_list_builder, env.state)
        fork.validate_block_access_list_gas_limit(
            block_access_list=output.block_access_list, block_gas_limit=env.block_gas_limit
        )
        return output, included, rejected

    def next_base_fee(self) -> int:
        h = self.head
        return int(fork.calculate_base_fee_per_gas(Uint(L2_GAS_LIMIT), h.gas_limit, h.gas_used, h.base_fee_per_gas))

    def nonce(self, sender: Address) -> int:
        account = self.chain.state.get_account_optional(sender)
        return int(account.nonce) if account else 0

    def storage(self, addr: Address, slot: int) -> int:
        return int(self.chain.state.get_storage(addr, Bytes32(slot.to_bytes(32, "big"))))

    # Blocks

    def preconfirm(self, p: dict) -> dict:
        """Builds the next block on the latest one, validates it, and adds it
        to the chain with the sequencer's preconfirmation. Keeps what the
        operator needs to post it: the block's parameters, blobs and mock
        proof."""
        started = time.time()
        parent = self.head
        # The sequencer's slot, or now.
        timestamp = p.get("timestamp") or max(int(time.time()), int(parent.timestamp) + 1)
        if timestamp <= int(parent.timestamp):
            raise RpcError(-32000, f"timestamp {timestamp} is not after the latest block's")
        anchor = bytes.fromhex(p["anchorHash"][2:])
        prev_randao = keccak256(anchor)
        base_fee = self.next_base_fee()
        block_state = BlockState(pre_state=self.chain.state)
        env = self.environment(block_state, timestamp, anchor, prev_randao, base_fee)

        # Transactions in order of arrival, each sender's in nonce order.
        arrived = sorted(self.pool.values(), key=lambda t: t.arrival)
        queues = {}
        for t in sorted(arrived, key=lambda t: t.nonce):
            queues.setdefault(t.sender, []).append(t)
        candidates = [queues[t.sender].pop(0) for t in arrived][:MAX_BLOCK_TXS]
        by_raw = {t.raw: t for t in candidates}
        output, included, rejected = self.execute(env, [], [block_transaction(t.raw) for t in candidates])
        self.drop_rejected(block_state, rejected)

        diff = extract_block_diff(block_state)
        state_root = self.chain.state.compute_state_root(diff)
        h = header({
            "parent_hash": keccak256(rlp.encode(parent)), "ommers_hash": fork.EMPTY_OMMER_HASH,
            "coinbase": FEE_RECIPIENT, "state_root": state_root, "transactions_root": root(output.transactions_trie),
            "receipt_root": root(output.receipts_trie), "bloom": fork.logs_bloom(output.block_logs), "difficulty": 0,
            "number": int(parent.number) + 1, "gas_limit": L2_GAS_LIMIT,
            "gas_used": max(output.block_gas_used, output.block_state_gas_used), "timestamp": timestamp,
            "extra_data": EXTRA_DATA, "prev_randao": prev_randao, "nonce": bytes(8), "base_fee_per_gas": base_fee,
            "withdrawals_root": root(output.withdrawals_trie), "blob_gas_used": 0, "excess_blob_gas": 0,
            "parent_beacon_block_root": anchor, "requests_hash": compute_requests_hash(output.requests),
            "block_access_list_hash": fork.hash_block_access_list(output.block_access_list), "slot_number": 0,
        })
        block = Block(header=h, transactions=tuple(included), ommers=(), withdrawals=())

        # Validate the block with the L1 stateless validation program.
        witness = build_execution_witness(
            block_state,
            expected_post_state_root=state_root,
            pre_state_accounts_data=self.chain.state._main_trie,
            pre_state_storages_data=self.chain.state._storage_tries,
            blockchain_headers=[rlp.encode(b.header) for b in self.chain.blocks],
        )
        input_bytes = serialize_stateless_input(build_stateless_input(
            block,
            execution_witness=witness,
            execution_requests=decode_execution_requests(output.requests),
            block_access_list=output.block_access_list,
            chain_id=self.chain.chain_id,
        ))
        result = deserialize_stateless_output(run_stateless_guest(input_bytes))
        if not result.successful_validation:
            raise RpcError(-32000, "the stateless program rejected the block")
        if int(result.chain_id) != L2_CHAIN_ID or int(result.schema_id) != p["schemaId"]:
            raise RpcError(-32000, "unexpected chain ID or schema ID")
        assert STATELESS_INPUT_SCHEMA_ID == p["schemaId"]

        # The public input root the rollup contract rebuilds, from the request
        # the program validated.
        request = deserialize_stateless_input(input_bytes).new_payload_request
        payload = request.execution_payload
        view = _to_view(payload)
        fields = {
            "parentHash": bytes(payload.parent_hash),
            "feeRecipient": bytes(payload.fee_recipient),
            "stateRoot": bytes(payload.state_root),
            "receiptsRoot": bytes(payload.receipts_root),
            "logsBloom": bytes(payload.logs_bloom),
            "prevRandao": bytes(payload.prev_randao),
            "blockNumber": int(payload.block_number),
            "gasLimit": int(payload.gas_limit),
            "gasUsed": int(payload.gas_used),
            "timestamp": int(payload.timestamp),
            "extraData": bytes(payload.extra_data),
            "baseFeePerGas": int(payload.base_fee_per_gas),
            "blockHash": bytes(payload.block_hash),
            "transactionsRoot": bytes(view.transactions.hash_tree_root()),
            "withdrawalsRoot": bytes(view.withdrawals.hash_tree_root()),
            "blobGasUsed": int(payload.blob_gas_used),
            "excessBlobGas": int(payload.excess_blob_gas),
            "blockAccessListRoot": bytes(view.block_access_list.hash_tree_root()),
            "slotNumber": int(payload.slot_number),
        }
        assert payload_root(fields) == bytes(view.hash_tree_root()), "payload root"
        assert bytes(request.parent_beacon_block_root) == anchor
        requests_root = bytes(_to_view(request.execution_requests).hash_tree_root())

        # EIP-8142 payload blobs, first in the list of versioned hashes. L2
        # blocks have no blob transactions, so they are the whole list.
        assert not request.versioned_hashes, "L2 blocks carry no blob transactions"
        bal, transactions = bytes(payload.block_access_list), [bytes(tx) for tx in payload.transactions]
        blobs = bib.execution_payload_data_to_blobs(bal, transactions)
        versioned_hashes = [bib.versioned_hash(bib.commitment(blob)) for blob in blobs]

        np_root = container4(
            payload_root(fields),
            versioned_hashes_root(versioned_hashes),
            bytes(request.parent_beacon_block_root),
            requests_root,
        )
        data_hash = public_input_root(np_root, L2_CHAIN_ID, p["schemaId"])

        # Sign the dependency, as MockDependencyVerifier.digest expects.
        triple = LEANSTARK_SCHEME.to_bytes(32, "big") + data_hash + bytes.fromhex(p["vkHash"][2:])
        digest = keccak256(p["l1ChainId"].to_bytes(32, "big") + bytes.fromhex(p["verifier"][2:]) + triple)
        proof = cast("wallet", "sign", "--no-hash", "--private-key", p["proverKey"], hx(digest))

        block_hash = keccak256(rlp.encode(h))
        assert block_hash == fields["blockHash"]
        self.commit(Pending(block, block_hash, block_state, output, [keccak256(t) for t in by_raw if block_transaction(t) in included]))

        # The preconfirmation, as SequencedNativeRollup.preconfirmationDigest
        # expects.
        number = fields["blockNumber"]
        digest = keccak256(
            p["l1ChainId"].to_bytes(32, "big") + bytes(12) + bytes.fromhex(p["rollup"][2:]) + number.to_bytes(32, "big")
            + block_hash + p["anchorNumber"].to_bytes(32, "big")
        )
        preconfirmation = {
            "number": number, "blockHash": hx(block_hash), "anchorBlockNumber": p["anchorNumber"],
            "signature": cast("wallet", "sign", "--no-hash", "--private-key", p["sequencerKey"], hx(digest)),
            "time": int(time.time()),
        }
        l2_messages = [self.l2_message(log) for log in output.block_logs if log.address == MESSENGER and log.topics[0] == L2_MESSAGE_SENT]
        print(
            f"preconfirmed L2 block {number}: {len(included)} transactions, in {time.time() - started:.1f} s",
            flush=True,
        )
        post = {
            "number": number,
            "blockHash": hx(fields["blockHash"]),
            "stateRoot": hx(fields["stateRoot"]),
            "timestamp": fields["timestamp"],
            "gasUsed": fields["gasUsed"],
            "transactions": len(payload.transactions),
            "txs": [self.summary(btx) for btx in included],
            "anchor": {"number": p["anchorNumber"], "hash": p["anchorHash"]},
            "validation": {"successful": bool(result.successful_validation), "chainId": L2_CHAIN_ID, "schemaId": p["schemaId"]},
            "newPayloadRequestRoot": hx(np_root),
            "publicInputRoot": hx(data_hash),
            "balBytes": len(bal),
            "l2Messages": l2_messages,
            "params": {
                "stateRoot": hx(fields["stateRoot"]),
                "receiptsRoot": hx(fields["receiptsRoot"]),
                "logsBloom": hx(fields["logsBloom"]),
                "gasUsed": fields["gasUsed"],
                "timestamp": fields["timestamp"],
                "baseFeePerGas": fields["baseFeePerGas"],
                "blockHash": hx(fields["blockHash"]),
                "transactionsRoot": hx(fields["transactionsRoot"]),
                "blockAccessListRoot": hx(fields["blockAccessListRoot"]),
                "payloadBlobCount": len(blobs),
                "executionRequestsRoot": hx(requests_root),
                "anchorBlockNumber": p["anchorNumber"],
                "feeRecipient": hx(fields["feeRecipient"]),
                "prevRandao": hx(fields["prevRandao"]),
                "extraData": hx(fields["extraData"]),
            },
            "payloadBytes": bib.payload_data_length(bal, transactions),
            "blobs": [hx(blob) for blob in blobs],
            "versionedHashes": [hx(v) for v in versioned_hashes],
            "triple": hx(triple),
            "proof": proof,
            "preconfirmation": preconfirmation,
        }
        self.config["preconfirmed"].append({
            "header": hx(rlp.encode(h)), "transactions": [hx(raw_transaction(tx)) for tx in block.transactions], "post": post,
        })
        self.config["preconfirmations"][str(number)] = preconfirmation
        self.save()
        return {k: v for k, v in post.items() if k not in ("blobs", "versionedHashes")}

    def waiting_posts(self, head_hash: str, limit: int) -> list:
        """What posting the preconfirmed blocks after the rollup's head takes,
        in order, at most `limit` of them."""
        number = self.number_of(bytes.fromhex(head_hash[2:]))
        if number is None:
            raise RpcError(-32000, f"the rollup's head {head_hash} is not in the node's chain")
        self.mark_posted(number)
        return [stored["post"] for stored in self.config["preconfirmed"][:limit]]

    def summary(self, btx) -> dict:
        """A transaction's hash, sender, recipient and value, as a
        preconfirmation lists it."""
        tx = decode_transaction(btx)
        frames = isinstance(tx, FrameTransaction)
        return {
            "hash": hx(keccak256(raw_transaction(btx))), "from": hx(sender_of(tx)),
            "to": None if frames or not len(tx.to) else hx(tx.to), "value": 0 if frames else int(tx.value),
            **({"frames": len(tx.frames)} if frames else {}),
        }

    def number_of(self, block_hash: bytes) -> int | None:
        return next((int(b["header"].number) for b in reversed(self.blocks) if b["hash"] == block_hash), None)

    def drop_rejected(self, block_state: BlockState, rejected: list) -> None:
        """Keeps rejected transactions that may become valid, such as those
        waiting for an earlier nonce or for funds, and drops the others."""
        state = TransactionState(parent=block_state)
        for tx, error in rejected:
            h = keccak256(raw_transaction(tx))
            t = self.pool[h]
            waiting = t.nonce > int(get_account(state, t.sender).nonce) or type(error).__name__ in (
                "InsufficientBalanceError", "GasUsedExceedsLimitError",
            )
            if not waiting:
                print(f"dropped {hx(h)}: {error!r}", flush=True)
                del self.pool[h]

    def commit(self, pending: Pending) -> None:
        diff = extract_block_diff(pending.block_state)
        apply_changes_to_state(self.chain.state, diff)
        self.chain.blocks.append(pending.block)
        self.chain.blocks = self.chain.blocks[-255:]
        self.diffs[int(pending.block.header.number)] = diff
        self.added(pending.block, pending.output, pending.pool_hashes)

    def added(self, block: Block, output: vm.BlockOutput, pool_hashes: list) -> None:
        """Indexes a block the chain added, and drops its transactions from
        the pool."""
        self.index(block, output)
        for h in pool_hashes:
            self.pool.pop(h, None)
        for h, t in list(self.pool.items()):
            if t.nonce < self.nonce(t.sender):
                del self.pool[h]

    def mark_posted(self, number: int) -> None:
        """Moves the preconfirmed blocks up to `number`, which the rollup
        contract has, to the posted ones."""
        while self.posted < number:
            stored = self.config["preconfirmed"].pop(0)
            self.posted += 1
            assert int(rlp.decode_to(Header, bytes.fromhex(stored["header"][2:])).number) == self.posted
            self.config["blocks"].append({"header": stored["header"], "transactions": stored["transactions"]})
            apply_changes_to_state(self.posted_state, self.diffs.pop(self.posted))
            self.save()
            print(f"L2 block {self.posted} is on L1", flush=True)
            if self.posted % SNAPSHOT_INTERVAL == 0:
                self.snapshot()

    def catch_up(self) -> None:
        """Derives from L1 the blocks the rollup contract has and the node
        does not, such as a block it built and dropped before L1 included it,
        as the follower does: from the advance calldata and the blobs,
        re-executed."""
        if not self.beacon:
            return
        import l2_follower  # it imports this module

        source = SimpleNamespace(l1_rpc=self.l1_rpc, beacon=self.beacon, rollup=self.rollup)
        logs = json_rpc(self.l1_rpc, "eth_getLogs", {
            "address": self.rollup, "fromBlock": "0x0", "toBlock": "latest", "topics": [hx(l2_follower.BLOCK_ADDED)],
        })
        derived = False
        for log in sorted(logs, key=lambda log: int(log["topics"][1], 16)):
            if int(log["topics"][1], 16) == int(self.head.number) + 1:
                derived = True
                _, details = l2_follower.rebuild(source, self.chain, L2_GAS_LIMIT, log)
                block = self.chain.blocks[-1]
                self.added(block, details["output"], [])
                self.config["blocks"].append({
                    "header": hx(rlp.encode(block.header)), "transactions": [hx(raw_transaction(tx)) for tx in block.transactions],
                })
                self.posted = int(block.header.number)
                self.save()
                print(f"L2 block {self.posted} is on L1, derived from it", flush=True)
        if derived:
            self.posted_state = copy_state(self.chain.state)

    def drop_expired(self) -> None:
        """Drops the preconfirmed blocks once L1 is about to refuse the oldest
        of them, and with it those built on it: its anchor must be one of the
        last 256 L1 blocks, and its timestamp at most `MAX_TIMESTAMP_LAG`
        behind L1's, so after an outage it can no longer be posted."""
        if not self.config["preconfirmed"]:
            return
        if self.max_timestamp_lag is None:
            lag = json_rpc(self.l1_rpc, "eth_call", {"to": self.rollup, "data": hx(MAX_TIMESTAMP_LAG_SELECTOR)}, "latest")
            self.max_timestamp_lag = int(lag, 16)
        params = self.config["preconfirmed"][0]["post"]["params"]
        l1 = json_rpc(self.l1_rpc, "eth_getBlockByNumber", "latest", False)
        if params["anchorBlockNumber"] + BLOCKHASH_WINDOW - EXPIRY_MARGIN_BLOCKS <= int(l1["number"], 16):
            reason = f"its anchor, L1 block {params['anchorBlockNumber']}, leaves BLOCKHASH's window"
        elif params["timestamp"] + self.max_timestamp_lag - EXPIRY_MARGIN_SECONDS <= int(l1["timestamp"], 16):
            reason = f"its timestamp falls {self.max_timestamp_lag} seconds behind L1's"
        else:
            return
        print(f"L2 block {self.posted + 1} can no longer reach L1: {reason}", flush=True)
        self.drop_preconfirmed()

    def drop_preconfirmed(self) -> None:
        """Drops the preconfirmed blocks and exits, for the runner to restart
        the node from the posted ones. The preconfirmations stay, as
        evidence, and the blocks' transactions go back to the pool."""
        self.config["repool"] = [raw for stored in self.config["preconfirmed"] for raw in stored["transactions"]]
        self.config["preconfirmed"] = []
        self.save()
        os._exit(1)

    def follow(self) -> None:
        """Marks blocks posted as soon as the rollup contract has them, and
        derives from L1 those it has that the node never had."""
        while True:
            try:
                head = bytes.fromhex(json_rpc(self.l1_rpc, "eth_call", {"to": self.rollup, "data": hx(BLOCK_HASH_SELECTOR)}, "latest")[2:])
                with self.lock:
                    number = self.number_of(head)
                    if number is not None:
                        self.mark_posted(number)
                        self.drop_expired()
                    elif not self.config["preconfirmed"]:
                        self.catch_up()
                    else:
                        print(f"the rollup's head {hx(head)} is not in the node's chain", flush=True)
                        self.drop_preconfirmed()
            except Exception:
                traceback.print_exc()
            time.sleep(2)

    # The RPC's view of blocks

    def l2_message(self, log) -> dict:
        value, fee, gas_limit, data = abi_decode(["uint256", "uint256", "uint256", "bytes"], bytes(log.data))
        return {
            "index": int.from_bytes(log.topics[1], "big"), "sender": "0x" + bytes(log.topics[2])[12:].hex(),
            "to": "0x" + bytes(log.topics[3])[12:].hex(), "value": value, "fee": fee, "gasLimit": gas_limit,
            "data": hx(data),
        }

    def index(self, block: Block, output: vm.BlockOutput | None) -> None:
        h = block.header
        block_hash = keccak256(rlp.encode(h))
        receipts = [decode_receipt(trie_get(output.receipts_trie, key)) for key in output.receipt_keys] if output else []
        entries, log_index, previous = [], 0, 0
        for i, (btx, receipt) in enumerate(zip(block.transactions, receipts)):
            raw = raw_transaction(btx)
            tx = decode_transaction(btx)
            tx_hash = keccak256(raw)
            sender = sender_of(tx)
            frame = isinstance(tx, FrameTransaction)
            logs = [log for fr in receipt.frame_receipts for log in fr.logs] if frame else list(receipt.logs)
            created = None
            if not frame and isinstance(tx.to, Bytes0) and receipt.succeeded:
                created = hx(compute_contract_address(sender, Uint(tx.nonce)))
            rpc_logs = []
            for log in logs:
                rpc_logs.append({
                    "address": hx(log.address), "topics": [hx(t) for t in log.topics], "data": hx(log.data),
                    "blockNumber": hex(int(h.number)), "blockHash": hx(block_hash), "transactionHash": hx(tx_hash),
                    "transactionIndex": hex(i), "logIndex": hex(log_index), "removed": False,
                })
                log_index += 1
            entries.append({
                "raw": raw, "tx": tx, "hash": tx_hash, "from": sender, "receipt": receipt, "frame": frame,
                "gasUsed": int(receipt.cumulative_gas_used) - previous, "logs": rpc_logs, "contractAddress": created,
            })
            self.messages += [
                log for log in rpc_logs if log["address"] == hx(MESSENGER) and log["topics"][:1] == [hx(L2_MESSAGE_SENT)]
            ]
            previous = int(receipt.cumulative_gas_used)
            self.transactions[tx_hash] = (int(h.number), i)
        self.blocks.append({"header": h, "hash": block_hash, "transactions": entries})

    def effective_gas_price(self, tx, base_fee: int) -> int:
        if isinstance(tx, (LegacyTransaction, AccessListTransaction)):
            return int(tx.gas_price)
        fees = tx.fees if isinstance(tx, FrameTransaction) else tx
        return min(int(fees.max_fee_per_gas), base_fee + int(fees.max_priority_fee_per_gas))

    def rpc_transaction(self, entry: dict, block: dict | None, index: int | None) -> dict:
        tx = entry["tx"]
        out = {
            "hash": hx(entry["hash"]), "type": hex(entry["raw"][0] if entry["raw"][0] < 0xC0 else 0),
            "nonce": hex(int(tx.nonce)), "from": hx(entry["from"]),
            "blockHash": hx(block["hash"]) if block else None,
            "blockNumber": hex(int(block["header"].number)) if block else None,
            "transactionIndex": hex(index) if block else None,
        }
        if isinstance(tx, FrameTransaction):
            out.update({
                "chainId": hex(int(tx.chain_id)), "sender": hx(tx.sender),
                "maxFeePerGas": hex(int(tx.fees.max_fee_per_gas)),
                "maxPriorityFeePerGas": hex(int(tx.fees.max_priority_fee_per_gas)),
                "gas": hex(sum(int(f.gas_limits.execution) + int(f.gas_limits.state) for f in tx.frames)),
                "value": "0x0", "input": "0x",
                "frames": [
                    {
                        "mode": hex(int(f.mode)), "flags": hex(int(f.flags)), "target": hx(f.to) if len(f.to) else None,
                        "gasLimit": hex(int(f.gas_limits.execution)), "stateGasLimit": hex(int(f.gas_limits.state)),
                        "value": hex(int(f.value)), "data": hx(f.data),
                    }
                    for f in tx.frames
                ],
            })
            return out
        out.update({
            "gas": hex(int(tx.gas)), "to": hx(tx.to) if len(tx.to) else None, "value": hex(int(tx.value)),
            "input": hx(tx.data), "r": hex(int(tx.r)), "s": hex(int(tx.s)),
        })
        if isinstance(tx, LegacyTransaction):
            out.update({"gasPrice": hex(int(tx.gas_price)), "v": hex(int(tx.v))})
            return out
        base_fee = int(block["header"].base_fee_per_gas) if block else self.next_base_fee()
        out.update({
            "chainId": hex(int(tx.chain_id)), "yParity": hex(int(tx.y_parity)), "v": hex(int(tx.y_parity)),
            "accessList": [{"address": hx(a.account), "storageKeys": [hx(k) for k in a.slots]} for a in tx.access_list],
            "gasPrice": hex(self.effective_gas_price(tx, base_fee)),
        })
        if not isinstance(tx, AccessListTransaction):
            out.update({"maxFeePerGas": hex(int(tx.max_fee_per_gas)), "maxPriorityFeePerGas": hex(int(tx.max_priority_fee_per_gas))})
        if isinstance(tx, SetCodeTransaction):
            out["authorizationList"] = [
                {"chainId": hex(int(a.chain_id)), "address": hx(a.address), "nonce": hex(int(a.nonce)),
                 "yParity": hex(int(a.y_parity)), "r": hex(int(a.r)), "s": hex(int(a.s))}
                for a in tx.authorizations
            ]
        return out

    def rpc_receipt(self, block: dict, index: int) -> dict:
        entry = block["transactions"][index]
        tx, receipt = entry["tx"], entry["receipt"]
        out = {
            "transactionHash": hx(entry["hash"]), "transactionIndex": hex(index), "blockHash": hx(block["hash"]),
            "blockNumber": hex(int(block["header"].number)), "from": hx(entry["from"]),
            "to": None if entry["frame"] or not len(tx.to) else hx(tx.to),
            "cumulativeGasUsed": hex(int(receipt.cumulative_gas_used)), "gasUsed": hex(entry["gasUsed"]),
            "effectiveGasPrice": hex(self.effective_gas_price(tx, int(block["header"].base_fee_per_gas))),
            "contractAddress": entry["contractAddress"], "logs": entry["logs"],
            "type": hex(entry["raw"][0] if entry["raw"][0] < 0xC0 else 0),
        }
        if isinstance(receipt, FrameTransactionReceipt):
            # The receipt's logs are its frames' logs in order.
            frames, first = [], 0
            for fr in receipt.frame_receipts:
                frames.append({
                    "status": hex(int(fr.status)), "executionGasUsed": hex(int(fr.gas_used.execution)),
                    "stateGasUsed": hex(int(fr.gas_used.state)), "logs": entry["logs"][first : first + len(fr.logs)],
                })
                first += len(fr.logs)
            out.update({
                "status": "0x1", "logsBloom": hx(fork.logs_bloom([log for fr in receipt.frame_receipts for log in fr.logs])),
                "payer": hx(receipt.payer), "frameReceipts": frames,
            })
        else:
            out.update({"status": hex(int(receipt.succeeded)), "logsBloom": hx(receipt.bloom)})
        return out

    def rpc_block(self, block: dict | None, full: bool) -> dict | None:
        if block is None:
            return None
        h = block["header"]
        return {
            "number": hex(int(h.number)), "hash": hx(block["hash"]), "parentHash": hx(h.parent_hash),
            "sha3Uncles": hx(h.ommers_hash), "miner": hx(h.coinbase), "stateRoot": hx(h.state_root),
            "transactionsRoot": hx(h.transactions_root), "receiptsRoot": hx(h.receipt_root),
            "logsBloom": hx(h.bloom), "difficulty": "0x0", "totalDifficulty": "0x0", "gasLimit": hex(int(h.gas_limit)),
            "gasUsed": hex(int(h.gas_used)), "timestamp": hex(int(h.timestamp)), "extraData": hx(h.extra_data),
            "mixHash": hx(h.prev_randao), "nonce": hx(h.nonce), "baseFeePerGas": hex(int(h.base_fee_per_gas)),
            "withdrawalsRoot": hx(h.withdrawals_root), "withdrawals": [], "blobGasUsed": hex(int(h.blob_gas_used)),
            "excessBlobGas": hex(int(h.excess_blob_gas)), "parentBeaconBlockRoot": hx(h.parent_beacon_block_root),
            "requestsHash": hx(h.requests_hash), "blockAccessListHash": hx(h.block_access_list_hash),
            "slotNumber": hex(int(h.slot_number)), "uncles": [],
            "size": hex(len(rlp.encode(h)) + sum(len(e["raw"]) for e in block["transactions"])),
            "transactions": [
                self.rpc_transaction(e, block, i) if full else hx(e["hash"]) for i, e in enumerate(block["transactions"])
            ],
        }

    def block_at(self, tag) -> dict | None:
        return self.block_by_number(self.tag_number(tag))

    # Calls

    def simulate(self, call: dict, gas: int):
        """Runs a call on the head state as a transaction without a
        signature, fee or nonce check."""
        block_state = BlockState(pre_state=self.chain.state)
        h = self.head
        env = self.environment(block_state, max(int(time.time()), int(h.timestamp) + 1), h.parent_beacon_block_root, h.prev_randao, self.next_base_fee())
        sender = address(call.get("from") or "0x" + "00" * 20)
        state = TransactionState(parent=block_state)
        nonce = get_account(state, sender).nonce
        to = address(call["to"]) if call.get("to") else Bytes0(b"")
        tx = FeeMarketTransaction(
            chain_id=U64(L2_CHAIN_ID), nonce=U256(nonce), max_priority_fee_per_gas=Uint(0), max_fee_per_gas=Uint(0),
            gas=Uint(gas), to=to, value=U256(int(call.get("value") or "0x0", 16)),
            data=Bytes(bytes.fromhex((call.get("input") or call.get("data") or "0x")[2:])),
            access_list=(), y_parity=U256(0), r=U256(0), s=U256(0),
        )
        intrinsic = validate_transaction(tx, sender)
        allocation = allocate_evm_gas(tx.gas, intrinsic)
        is_create = isinstance(to, Bytes0)
        recipient = compute_contract_address(sender, nonce) if is_create else to
        tx_env = vm.TransactionEnvironment(
            origin=sender, gas_limit=tx.gas, effective_gas_price=Uint(0),
            execution_gas_grant=allocation.execution_gas, state_gas_reservoir=allocation.state_gas_reservoir,
            calldata_floor=intrinsic.calldata_floor, access_list_addresses=set(), access_list_storage_keys=set(),
            accounts_with_paid_writes={sender} | ({recipient} if is_create or tx.value else set()),
            state=state, blob_versioned_hashes=(), authorizations=(), index_in_block=Uint(0), tx_hash=None,
            top_level_context=vm.TopLevelContext(recipient=recipient, is_create=is_create, data=tx.data, value=tx.value),
            frame_context=None,
        )
        fork.update_sender_state(env, tx_env, tx)
        return process_top_level(env, tx_env)

    def call(self, call: dict, tag=None) -> str:
        out = self.simulate(call, int(call.get("gas") or hex(GasCosts.TX_MAX_GAS_LIMIT), 16))
        if out.error is not None:
            raise RpcError(3, f"execution reverted: {out.error!r}", hx(out.return_data))
        return hx(out.return_data)

    def estimate_gas(self, call: dict, tag=None) -> str:
        cap = min(L2_GAS_LIMIT, int(GasCosts.TX_MAX_TOTAL_GAS_LIMIT))

        def succeeds(gas: int) -> bool:
            try:
                return self.simulate(call, gas).error is None
            except EthereumException:
                return False

        out = self.simulate(call, cap)
        if out.error is not None:
            raise RpcError(3, f"execution reverted: {out.error!r}", hx(out.return_data))
        used = cap - int(out.gas_left) - int(out.state_gas_left)
        low, high = used - 1, cap
        guess = min(cap, used * 6 // 5 + 5_000)
        if succeeds(guess):
            high = guess
        while high - low > max(1_000, high // 50):
            middle = (low + high) // 2
            if succeeds(middle):
                high = middle
            else:
                low = middle
        return hex(high)

    # Transactions

    def send_raw_transaction(self, data: str) -> str:
        raw = bytes.fromhex(data[2:])
        try:
            btx = block_transaction(raw)
            tx = decode_transaction(btx)
            sender = sender_of(tx)
            if not isinstance(tx, FrameTransaction):
                validate_transaction(tx, sender)
        except Exception as e:
            raise RpcError(-32000, f"invalid transaction: {e!r}")
        if not isinstance(tx, LegacyTransaction) and int(tx.chain_id) != L2_CHAIN_ID:
            raise RpcError(-32000, "wrong chain ID")
        if isinstance(tx, (BlobTransaction, FrameTransaction)) and tx.blob_versioned_hashes:
            raise RpcError(-32000, "L2 blocks carry no blob transactions")
        if int(tx.nonce) < self.nonce(sender):
            raise RpcError(-32000, "nonce too low")
        # One pending claim per message, which succeeds, so a pending claim
        # cannot keep a message from being claimed.
        claimed = tuple(message[6] for message, _ in claims(tx))
        for index in claimed:
            if any(index in t.claims for t in self.pool.values() if (t.sender, t.nonce) != (sender, int(tx.nonce))):
                raise RpcError(-32000, f"a claim of L1 message {index} is already pending")
        # The envelope names a frame transaction's sender, and its VERIFY
        # frames check the signatures, so only running it shows who sent it.
        if isinstance(tx, FrameTransaction):
            self.check_unfunded(tx, sender)
            self.check_frame_transaction(tx, sender)
        # A transaction replaces the sender's one with the same nonce.
        for h, t in list(self.pool.items()):
            if t.sender == sender and t.nonce == int(tx.nonce):
                del self.pool[h]
        self.arrivals += 1
        self.pool[keccak256(raw)] = PoolTransaction(raw, tx, sender, int(tx.nonce), self.arrivals, claimed)
        return hx(keccak256(raw))

    def check_unfunded(self, tx: FrameTransaction, sender: Address) -> None:
        """A claim runs before the VERIFY frame approves payment, so a sender
        that cannot pay up front relies on it to pay. If one transaction
        could make many such claims fail, each would cost the node its
        execution and pay nothing, which is why EIP-8141's public mempool
        rejects them. The node takes them only when they depend on no state
        anyone else can change: frames that only prove the root and claim
        messages paying the sender itself, which has no code."""
        account = self.chain.state.get_account_optional(sender)
        max_cost = int(validate_frame_transaction(tx).max_gas) * int(tx.fees.max_fee_per_gas)
        if account is not None and int(account.balance) >= max_cost:
            return
        prefix = next((i for i, f in enumerate(tx.frames) if f.mode == FrameMode.VERIFY), len(tx.frames))
        own = all(
            calls_messenger(f, CLAIM_SELECTOR) or calls_messenger(f, PROVE_ROOT_SELECTOR) for f in tx.frames[:prefix]
        ) and all(
            address(message[1]) == sender and (message[3] == 0 or address(recipient) == sender)
            for message, recipient in claims(tx)
        )
        if not own or (account is not None and self.chain.state.get_code(account.code_hash)):
            raise RpcError(-32000, "a sender that cannot pay up front may only claim messages that pay itself")

    def check_frame_transaction(self, tx: FrameTransaction, sender: Address) -> None:
        """Runs a frame transaction on the head state, at its own nonce, and
        requires it to be valid and its claims to succeed."""
        block_state = BlockState(pre_state=self.chain.state)
        h = self.head
        env = self.environment(
            block_state, max(int(time.time()), int(h.timestamp) + 1), h.parent_beacon_block_root, h.prev_randao, self.next_base_fee()
        )
        # Earlier transactions of the sender may be pending, so run it at its
        # own nonce.
        state = TransactionState(parent=block_state)
        account = get_account(state, sender)
        set_tracked_account(state, sender, Account(nonce=Uint(tx.nonce), balance=account.balance, code_hash=account.code_hash))
        incorporate_tx_into_block(state, env.block_access_list_builder)
        output = vm.BlockOutput()
        try:
            fork.process_transaction(env, output, tx, Uint(0))
        except Exception as e:
            raise RpcError(-32000, f"the transaction is invalid on the head state: {e!r}")
        receipt = decode_receipt(trie_get(output.receipts_trie, output.receipt_keys[0]))
        for frame, result in zip(tx.frames, receipt.frame_receipts):
            if calls_messenger(frame, CLAIM_SELECTOR) and int(result.status) != 1:
                raise RpcError(-32000, "the claim fails on the head state")

    def transaction_count(self, addr: str, tag=None) -> str:
        sender = address(addr)
        nonce = self.nonce(sender)
        if tag == "pending":
            pooled = {t.nonce for t in self.pool.values() if t.sender == sender}
            while nonce in pooled:
                nonce += 1
        return hex(nonce)

    def transaction_by_hash(self, h: str) -> dict | None:
        key = bytes.fromhex(h[2:])
        if key in self.transactions:
            number, index = self.transactions[key]
            block = self.block_by_number(number)
            return self.rpc_transaction(block["transactions"][index], block, index)
        if key in self.pool:
            t = self.pool[key]
            return self.rpc_transaction({"raw": t.raw, "tx": t.tx, "hash": key, "from": t.sender}, None, None)
        return None

    def receipt(self, h: str) -> dict | None:
        key = bytes.fromhex(h[2:])
        if key not in self.transactions:
            return None
        number, index = self.transactions[key]
        return self.rpc_receipt(self.block_by_number(number), index)

    def block_receipts(self, tag) -> list | None:
        if isinstance(tag, dict):  # {"blockHash": ...} or {"blockNumber": ...}
            tag = tag.get("blockHash") or tag.get("blockNumber")
        if isinstance(tag, str) and len(tag) == 66:
            block = next((b for b in self.blocks if hx(b["hash"]) == tag), None)
        else:
            block = self.block_at(tag)
        return None if block is None else [self.rpc_receipt(block, i) for i in range(len(block["transactions"]))]

    def logs(self, criteria: dict) -> list:
        first = self.tag_number(criteria.get("fromBlock", "latest"))
        last = min(self.tag_number(criteria.get("toBlock", "latest")), int(self.head.number))
        addresses = criteria.get("address")
        addresses = {a.lower() for a in ([addresses] if isinstance(addresses, str) else addresses or [])}
        topics = criteria.get("topics") or []
        start = max(first, self.logs_from)
        recent = self.blocks[max(0, start - self.first) : max(0, last - self.first + 1)]
        candidates = [log for block in recent for entry in block["transactions"] for log in entry["logs"]]
        if first < self.logs_from:
            # Of older blocks, the node only keeps the L2 to L1 messages.
            candidates = [m for m in self.messages if first <= int(m["blockNumber"], 16) < min(self.logs_from, last + 1)] + candidates
        return [
            log for log in candidates
            if (not addresses or log["address"] in addresses) and all(
                t is None or (log["topics"][i] if i < len(log["topics"]) else None) in ([t] if isinstance(t, str) else t)
                for i, t in enumerate(topics)
            )
        ]

    # State

    def balance(self, addr: str, tag=None) -> str:
        account = self.chain.state.get_account_optional(address(addr))
        return hex(int(account.balance) if account else 0)

    def code(self, addr: str, tag=None) -> str:
        account = self.chain.state.get_account_optional(address(addr))
        return hx(self.chain.state.get_code(account.code_hash)) if account else "0x"

    def storage_at(self, addr: str, slot: str, tag=None) -> str:
        return "0x" + self.storage(address(addr), int(slot, 16)).to_bytes(32, "big").hex()

    def proof(self, addr: str, slots: list, tag=None) -> dict:
        """`eth_getProof` against the state root of the latest block, or of
        the latest posted one for `safe` and `finalized`."""
        posted = tag in ("safe", "finalized")
        state = self.posted_state if posted else self.chain.state
        header = self.block_by_number(self.posted)["header"] if posted else self.head

        def secure_trie(entries: dict) -> HexaryTrie:
            trie = HexaryTrie({})
            for key, value in entries.items():
                trie[keccak256(key)] = value
            return trie

        storage_tries, accounts = {}, {}
        for a, account in state._main_trie._data.items():
            stored = state._storage_tries.get(a)
            storage = secure_trie({bytes(k): pyrlp.encode(int(v)) for k, v in (stored._data.items() if stored else []) if int(v)})
            storage_tries[bytes(a)] = storage
            accounts[bytes(a)] = pyrlp.encode([int(account.nonce), int(account.balance), storage.root_hash, bytes(account.code_hash)])
        trie = secure_trie(accounts)
        assert trie.root_hash == bytes(header.state_root), "state root"
        target = bytes(address(addr))
        account = state.get_account_optional(address(addr))
        storage = storage_tries.get(target, HexaryTrie({}))
        return {
            "address": addr,
            "accountProof": [hx(pyrlp.encode(n)) for n in trie.get_proof(keccak256(target))],
            "balance": hex(int(account.balance) if account else 0),
            "codeHash": hx(account.code_hash) if account else hx(keccak256(b"")),
            "nonce": hex(int(account.nonce) if account else 0),
            "storageHash": hx(storage.root_hash),
            "storageProof": [
                {
                    "key": slot, "value": hex(int(state.get_storage(address(addr), Bytes32(int(slot, 16).to_bytes(32, "big"))))),
                    "proof": [hx(pyrlp.encode(n)) for n in storage.get_proof(keccak256(int(slot, 16).to_bytes(32, "big")))],
                }
                for slot in slots
            ],
        }

    def fee_history(self, count, newest, percentiles=None) -> dict:
        last = int(self.block_at(newest)["header"].number)
        first = max(0, last - int(count, 16) + 1 if isinstance(count, str) else last - count + 1)
        blocks = self.blocks[max(0, first - self.first) : max(0, last - self.first + 1)]
        return {
            "oldestBlock": hex(first),
            "baseFeePerGas": [hex(int(b["header"].base_fee_per_gas)) for b in blocks] + [hex(self.next_base_fee())],
            "gasUsedRatio": [int(b["header"].gas_used) / int(b["header"].gas_limit) for b in blocks],
            "reward": [[hex(PRIORITY_FEE)] * len(percentiles or []) for _ in blocks],
        }

    def handle(self, request: dict) -> dict:
        methods = {
            "web3_clientVersion": lambda: "native-rollup-l2-node/execution-specs",
            "net_version": lambda: str(L2_CHAIN_ID),
            "eth_chainId": lambda: hex(L2_CHAIN_ID),
            "eth_syncing": lambda: False,
            "eth_accounts": lambda: [],
            "eth_blockNumber": lambda: hex(int(self.head.number)),
            "eth_gasPrice": lambda: hex(self.next_base_fee() + PRIORITY_FEE),
            "eth_maxPriorityFeePerGas": lambda: hex(PRIORITY_FEE),
            "eth_feeHistory": self.fee_history,
            "eth_getBalance": self.balance,
            "eth_getTransactionCount": self.transaction_count,
            "eth_getCode": self.code,
            "eth_getStorageAt": self.storage_at,
            "eth_getProof": self.proof,
            "eth_call": self.call,
            "eth_estimateGas": self.estimate_gas,
            "eth_sendRawTransaction": self.send_raw_transaction,
            "eth_getTransactionByHash": self.transaction_by_hash,
            "eth_getTransactionReceipt": self.receipt,
            "eth_getBlockByNumber": lambda tag, full=False: self.rpc_block(self.block_at(tag), full),
            "eth_getBlockByHash": lambda h, full=False: self.rpc_block(next((b for b in self.blocks if hx(b["hash"]) == h), None), full),
            "eth_getBlockReceipts": self.block_receipts,
            "debug_getRawHeader": lambda tag: (b := self.block_at(tag)) and hx(rlp.encode(b["header"])),
            "eth_getLogs": self.logs,
            "nr_preconfirm": self.preconfirm,
            "nr_waitingPosts": self.waiting_posts,
            "nr_getPreconfirmation": lambda number: self.config["preconfirmations"].get(str(int(number, 16))),
        }
        reply = {"jsonrpc": "2.0", "id": request.get("id")}
        method = request.get("method")
        if method not in methods:
            reply["error"] = {"code": -32601, "message": f"{method} is not supported"}
            return reply
        try:
            with self.lock:
                reply["result"] = methods[method](*request.get("params", []))
        except RpcError as e:
            reply["error"] = {"code": e.code, "message": str(e)} | ({"data": e.data} if e.data else {})
        except Exception as e:
            traceback.print_exc()
            reply["error"] = {"code": -32000, "message": f"{e!r}"}
        return reply


def serve(args: argparse.Namespace) -> None:
    node = Node(args.state, args.l1_rpc, args.rollup, args.beacon)
    threading.Thread(target=node.follow, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = [node.handle(r) for r in body] if isinstance(body, list) else node.handle(body)
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_) -> None:
            pass

    class Server(ThreadingHTTPServer):
        # Load generators open many connections at once, more than the
        # default backlog of 5.
        request_queue_size = 256

    print(f"L2 RPC on http://127.0.0.1:{args.port}", flush=True)
    Server(("127.0.0.1", args.port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("genesis")
    gen.add_argument("--state", required=True)
    gen.add_argument("--l1-rollup", required=True, help="the rollup contract's address, which the messenger trusts")
    s = sub.add_parser("serve")
    s.add_argument("--state", required=True)
    s.add_argument("--l1-rpc", required=True)
    s.add_argument("--rollup", required=True)
    s.add_argument("--port", type=int, default=8547)
    s.add_argument("--beacon", help="a consensus-layer API serving blobs, to derive blocks the node is missing from L1")
    args = parser.parse_args()
    {"genesis": genesis, "serve": serve}[args.command](args)


if __name__ == "__main__":
    main()
