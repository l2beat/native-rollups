"""
Runs the native rollup demo on the local frames devnet (`contracts/frames/`).

It deploys a fresh rollup with the preconfirmations customization, starts
the L2 node with its RPC, the operator's sequencer, and a follower that
rebuilds the L2 chain from L1 on its own. The sequencer preconfirms a block
every 4 seconds, empty if no transaction waits, and posts each once a
stand-in proving time has passed. Each block's preconfirmation and post go
to `demo/data/blocks/<number>.json`. The runner meanwhile plays a scripted
story, one step every few seconds: Alice and Bob deposit from L1, and once an
L2 block anchors their deposits, each claims theirs with a frame transaction
they sign, which pays for itself. The users pay each other through the
L2 RPC, and Charlie, who never deposits, withdraws ETH received on L2 to L1
and claims it there. Alice and Bob also move a demo token through an example
ERC-20 bridge, to L2 and back, and ping an example ping pong on the other
chain, which answers with a pong. Every step is recorded in `demo/data/session.json`,
which `demo/server.py` serves to the site. When it starts, or if the L2 node
stops, the runner resumes the last rollup if L1 still has it: the L2 node
replays its blocks, the follower rebuilds the chain from L1, and the story
goes on. `--new` deploys a new rollup instead.

Around the story, spamoor (github.com/ethpandaops/spamoor, `--spamoor`)
generates activity: ERC-20 transfers, Uniswap swaps, EIP-7702 delegations
and EIP-8141 frame transactions on L2, and messages in both directions: ETH
to random addresses, and pings to an example ping pong on the other chain,
which answers each with a pong. Those recipients cannot claim, so relayers
do: the L2 node's relayer account on L2, and a claimer account on L1.

Only uses the standard library: it runs the contract scripts in their uv
environments, as the README of `contracts/frames/` describes.

    python3 demo/runner.py
"""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(ROOT, "contracts")
DATA = os.path.join(ROOT, "demo", "data")
# The rollup's mock prover key, which the site does not serve.
PROVER = os.path.join(DATA, "prover.json")
# npm packages whose artifacts spamoor deploys, for their sources.
PACKAGES = os.path.join(DATA, "packages", "node_modules")
# Spamoor's deposits go to one of 100 addresses, and its EIP-7702
# delegations reuse 20 accounts, so that its traffic does not grow the L2
# state: the node's work for each block grows with the whole state.
DEPOSIT_RECIPIENTS = "0x" + "de90" + "0" * 34 + "{random:9}{random:9}"
# The story events the session keeps.
EVENTS_KEPT = 100
# What the follower rebuilds from L1, which the explorer shows.
EXPLORER = os.path.join(DATA, "explorer.db")

# ethereum-package's prefunded development keys.
OPERATOR_KEY = "0xbcdf20249abf0ed6d944c0288fad489e33f66b3960d9e6229c1cd214ed3bbe31"
# Each user has one key, so one address, on both chains.
USER_KEYS = {
    "Alice": "0x39725efee3fb28614de3bacaffe4cc4bd8c436257e2c8bb887c4b5c4be45e76d",
    "Bob": "0x53321db7c1e331d93a11a41d16f004d7ff63972ec8ec7c25db329728ceeb1710",
    "Charlie": "0xab63b23eb7941c1251757e24b3d2350d2bc05c3c388d06f8fe6feafefb1e8c70",
}
# Claims L1 to L2 messages that their recipients cannot claim, for their fee.
RELAYER_KEY = "0x5d2344259f42259f82d2c140aa66102ba89b57b4883ee441a8b312622bd42491"
# Claims L2 to L1 messages that their recipients cannot claim, for their fee.
CLAIMER_KEY = "0x27515f805127bebad2fb9b183508bdacb8c763da16f54e0678b16e8f28ef3fff"
# Spamoor's funding wallets: on L1 for deposits, on L2 for its transactions,
# and on L2 for its messages to L1, which it sends at a slower pace.
SPAMOOR_L1_KEY = "0x7ff1a4c1d57e5e784d327c4c7651e952350bc271f156afb3d00d20f5ef924856"
SPAMOOR_L2_KEY = "0x3a91003acaf4c21b3953d94fa4a6db694fa69e5242b2e37be05dd82761058899"
SPAMOOR_L2_MESSAGES_KEY = "0xbb1d0f125b4fb2bb173c318cdead45468474ca71474e2247776b2b4c0fa2d3f5"
# Deploys the example apps: a ping pong and an ERC-20 bridge.
APPS_KEY = "0x850643a0224065ecce3882673c21f56bcf6eef86274cc21cadff15930b59fc8c"
# The deterministic deployment proxy, on both chains.
CREATE2_FACTORY = "0x4e59b44847b379578588920ca78fbf26c0b4956c"
L2_MESSENGER = "0x8079000000000000000000000000000000000001"
SEND_MESSAGE_ABI = json.dumps([{
    "type": "function", "name": "sendMessage", "stateMutability": "payable", "outputs": [],
    "inputs": [
        {"name": "to", "type": "address"}, {"name": "fee", "type": "uint256"}, {"name": "gasLimit", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
}])
PING_ABI = json.dumps([{"type": "function", "name": "ping", "stateMutability": "payable", "outputs": [], "inputs": []}])
# keccak256("L2MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")
L2_MESSAGE_SENT = "0xe854ec33ea124f454be5a868ee40540f56f837bd6eae1cbd8d5add769ccc7ed6"
# keccak256("L1MessageSent(uint256,address,address,uint256,uint256,uint256,bytes)")
L1_MESSAGE_SENT = "0x1aaf2a3a1847e999050228d1cbe598e5d4c1522411ec1422281a18b4ad3ffe10"
# The fee of messages to addresses that cannot claim them, which covers a
# relayer's claim in either direction.
MESSAGE_FEE = 10**15
# A ping's value: half pays for the ping's claim, half for the pong's. Each
# message's call gets 300k gas, which the claimer on L1 asks a bigger fee
# for.
PING_VALUE = 4 * MESSAGE_FEE
# A bound on the gas of a claim on L1 besides its call, for the claimer's
# fee check.
L1_CLAIM_GAS = 400_000
ETH = 10**18
# What a user keeps on L2 for fees.
RESERVE = ETH // 20
# The sequencer's bond, which the rollup contract slashes if a block it
# preconfirmed is not the one the rollup has at that height.
BOND = 10 * ETH


def run(cmd: list, cwd: str = CONTRACTS, timeout: int = 900) -> str:
    """Runs a command, which fails if it takes over `timeout` seconds, so
    that nothing hangs the story. A claim on L1 waits for its receipt."""
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{' '.join(cmd[:6])}...: no answer in {timeout} s")
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}...: {(out.stderr or out.stdout).strip()[-600:]}")
    return out.stdout


def cast(*args: str) -> str:
    return run(["cast", *args], timeout=180).strip()


def devnet_url(container: str, port: int) -> str | None:
    """The local URL of `port` of the devnet container whose name starts
    with `container`, as kurtosis names them. Docker assigns new host ports
    whenever it restarts."""
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    name = next((n for n in names if n.startswith(container)), None)
    if name is None:
        return None
    mapping = subprocess.run(["docker", "port", name, str(port)], capture_output=True, text=True).stdout.splitlines()
    return f"http://127.0.0.1:{mapping[0].rsplit(':', 1)[1]}" if mapping else None


def create2_address(init_code: str) -> str:
    """Where the CREATE2 factory deploys `init_code`, with a zero salt."""
    return "0x" + cast("keccak", "0xff" + CREATE2_FACTORY[2:] + "00" * 32 + cast("keccak", init_code)[2:])[-40:]


def json_rpc(url: str, method: str, *params):
    request = urllib.request.Request(
        url,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        out = json.load(response)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']['message']}")
    return out["result"]


def write_json(path: str, value) -> None:
    with open(path + ".tmp", "w") as f:
        json.dump(value, f, indent=1)
    os.replace(path + ".tmp", path)


class Episode:
    def __init__(self, args: argparse.Namespace, number: int):
        self.args = args
        self.number = number
        self.state = os.path.join(DATA, "l2_state.json")
        self.record = os.path.join(DATA, "record.json")
        self.session = {"episode": number, "startedAt": int(time.time()), "contracts": {}, "events": []}
        self.claimed = set()  # L2 to L1 messages claimed on L1
        self.lock = threading.Lock()
        self.node = None
        self.follower = None
        self.operator = None
        self.spamoor = []

    # Recording

    def event(self, kind: str, title: str, **fields) -> None:
        with self.lock:
            self.session["events"].append({"type": kind, "title": title, "time": int(time.time()), **fields})
            # The site shows the latest ones, and the journal keeps them all.
            del self.session["events"][:-EVENTS_KEPT]
            write_json(os.path.join(DATA, "session.json"), self.session)
        print(f"[episode {self.number}] {title}", flush=True)

    # Setup

    def deploy(self) -> None:
        a = self.args
        prover = json.loads(cast("wallet", "new", "--json"))[0]
        self.prover_key = prover["private_key"]
        deployer = cast("wallet", "address", "--private-key", OPERATOR_KEY)
        nonce = int(cast("nonce", "--rpc-url", a.rpc, deployer))
        rollup = cast("compute-address", "--nonce", str(nonce + 3), deployer).split()[-1]
        genesis = json.loads(run([
            "uv", "run", "--project", a.zkevm_specs, "python", "script/l2_node.py",
            "genesis", "--state", self.state, "--l1-rollup", rollup,
        ]).strip().splitlines()[-1])
        out = subprocess.run(
            ["forge", "script", "script/DeployFrames.s.sol", "--rpc-url", a.rpc, "--broadcast", "--slow", "--skip-simulation"],
            cwd=CONTRACTS, capture_output=True, text=True, timeout=300,
            env={**os.environ, "PRIVATE_KEY": OPERATOR_KEY, "PROVER": prover["address"], "ROLLUP": rollup,
                 "SEQUENCER": cast("wallet", "address", "--private-key", OPERATOR_KEY), "BOND": str(BOND),
                 "GENESIS_HASH": genesis["genesisHash"], "GENESIS_STATE_ROOT": genesis["genesisStateRoot"]},
        )
        found = dict(re.findall(r"^\s+(registry|verifier|rollup|helper)\s+(0x[0-9a-fA-F]{40})", out.stdout, re.M))
        if out.returncode != 0 or found.get("rollup", "").lower() != rollup.lower():
            raise RuntimeError(f"deployment failed: {out.stdout[-600:]} {out.stderr[-600:]}")
        self.users = {name: cast("wallet", "address", "--private-key", key) for name, key in USER_KEYS.items()}
        address = lambda key: cast("wallet", "address", "--private-key", key)  # noqa: E731
        self.contracts = {
            "rollup": found["rollup"], "verifier": found["verifier"], "registry": found["registry"],
            "framesHelper": found["helper"], "l2Messenger": L2_MESSENGER, "prover": prover["address"],
            "operator": deployer, "users": self.users, "l2GenesisHash": genesis["genesisHash"],
            "relayer": address(RELAYER_KEY), "claimer": address(CLAIMER_KEY),
            "spamoorL1": address(SPAMOOR_L1_KEY), "spamoorL2": address(SPAMOOR_L2_KEY),
        }
        self.session["contracts"] = self.contracts
        write_json(PROVER, {"rollup": found["rollup"], "key": self.prover_key})
        # The previous rollup's blocks.
        shutil.rmtree(os.path.join(DATA, "blocks"), ignore_errors=True)
        self.event("deployed", "Deployed a new rollup on L1", contracts=self.contracts)
        self.start()

    def resume(self) -> int | None:
        """Picks up the last rollup if L1 still has it and the L2 node has its
        chain, and returns the story's next step."""
        try:
            session = json.load(open(os.path.join(DATA, "session.json")))
            prover = json.load(open(PROVER))
        except (OSError, ValueError):
            return None
        contracts = session.get("contracts") or {}
        rollup = contracts.get("rollup")
        if not rollup or prover.get("rollup") != rollup or not os.path.exists(self.state):
            return None
        # A node still syncing L1 would not have the rollup yet: only trust
        # its answer once it follows the chain's head.
        latest = json_rpc(self.args.rpc, "eth_getBlockByNumber", "latest", False)
        if int(latest["timestamp"], 16) < time.time() - 300:
            raise RuntimeError("the L1 node is not at the chain's head yet")
        if cast("code", "--rpc-url", self.args.rpc, rollup) == "0x":
            return None
        self.session, self.contracts, self.users, self.prover_key = session, contracts, contracts["users"], prover["key"]
        self.number = session["episode"]
        steps = session.get("step", -1) + 1
        self.event("resumed", "Resumed the rollup")
        self.start()
        if steps > 3 and self.args.spamoor:
            self.start_spamoor()
        return steps

    def start(self) -> None:
        self.start_node()
        self.start_follower()
        self.start_operator()
        threading.Thread(target=self.relay_withdrawals, daemon=True).start()

    def init_code(self, name: str, constructor: str, *args: str) -> str:
        artifact = json.load(open(os.path.join(CONTRACTS, "out", f"{name}.sol", f"{name}.json")))
        return artifact["bytecode"]["object"] + cast("abi-encode", constructor, *args)[2:]

    def deploy_apps(self) -> None:
        """Deploys the example apps, each a contract on both chains that only
        accepts calls from its peer: a ping pong, and an ERC-20 bridge with an
        L1 token for the users. The L1 contracts come from the apps' key, the
        L2 ones through the CREATE2 factory, so each side names the other
        before either exists. The L2 ones wait until the key's deposit to
        itself gives it funds there."""
        a, c = self.args, self.contracts
        l2_code = lambda: (  # noqa: E731
            self.init_code("PingPong", "constructor(address,bool,address)", L2_MESSENGER, "true", c["pingPongL1"]),
            self.init_code("L2ERC20Bridge", "constructor(address,address)", L2_MESSENGER, c["l1Bridge"]),
        )
        if "l1Bridge" not in c:
            apps = cast("wallet", "address", "--private-key", APPS_KEY)
            nonce = int(cast("nonce", "--rpc-url", a.rpc, apps))
            c["pingPongL1"], c["l1Bridge"] = (cast("compute-address", "--nonce", str(nonce + k), apps).split()[-1] for k in (0, 1))
            c["pingPongL2"], c["l2Bridge"] = map(create2_address, l2_code())
            deploy = lambda code: json.loads(cast(  # noqa: E731
                "send", "--rpc-url", a.rpc, "--private-key", APPS_KEY, "--json", "--timeout", "120", "--create", code,
            ))["contractAddress"]
            deploy(self.init_code("PingPong", "constructor(address,bool,address)", c["rollup"], "false", c["pingPongL2"]))
            deploy(self.init_code("L1ERC20Bridge", "constructor(address,address)", c["rollup"], c["l2Bridge"]))
            holders = f"[{self.users['Alice']},{self.users['Bob']}]"
            c["demoToken"] = deploy(self.init_code("DemoToken", "constructor(address[],uint256)", holders, str(1000 * ETH)))
            c["apps"] = apps
            cast("send", "--rpc-url", a.rpc, "--private-key", APPS_KEY, "--timeout", "120", c["rollup"],
                 "sendMessage(address,uint256,uint256,bytes)", apps, "0", "0", "0x", "--value", str(ETH))
            self.session["contracts"] = c
        if not c.get("appsOnL2"):
            if int(json_rpc(self.l2_rpc, "eth_getBalance", c["apps"], "latest"), 16) == 0:
                return
            for code in l2_code():
                cast("send", "--rpc-url", self.l2_rpc, "--private-key", APPS_KEY, "--timeout", "60",
                     CREATE2_FACTORY, "0x" + "00" * 32 + code[2:])
            c["appsOnL2"] = True
            self.event("apps", "The example apps, a ping pong and an ERC-20 bridge, are on both chains",
                       contracts={k: c[k] for k in ("pingPongL1", "pingPongL2", "l1Bridge", "l2Bridge", "demoToken")})
            # Spamoor plays ping pong from now on.
            if self.spamoor:
                for p in self.spamoor:
                    p.terminate()
                    p.wait(timeout=30)
                self.spamoor = []
                self.start_spamoor()

    def start_node(self) -> None:
        a = self.args
        log = open(os.path.join(DATA, "l2_node.log"), "a")
        self.node = subprocess.Popen(
            ["uv", "run", "--project", a.zkevm_specs, "python", "script/l2_node.py", "serve",
             "--state", self.state, "--l1-rpc", a.rpc, "--rollup", self.contracts["rollup"], "--port", str(a.l2_port),
             "--beacon", a.beacon],
            cwd=CONTRACTS, stdout=log, stderr=subprocess.STDOUT,
        )
        for _ in range(120):
            try:
                json_rpc(self.l2_rpc, "eth_blockNumber")
                return
            except OSError:
                if self.node.poll() is not None:
                    break
                time.sleep(1)
        raise RuntimeError("the L2 node did not start")

    @property
    def l2_rpc(self) -> str:
        return f"http://127.0.0.1:{self.args.l2_port}"

    def start_follower(self) -> None:
        a = self.args
        # The follower resumes from its last snapshot in the explorer's data.
        log = open(os.path.join(DATA, "follower.log"), "a")
        self.follower = subprocess.Popen(
            ["uv", "run", "--project", a.zkevm_specs, "python", "script/l2_follower.py",
             "--l1-rpc", a.rpc, "--beacon", a.beacon, "--rollup", self.contracts["rollup"],
             "--genesis", self.state, "--watch", "--explorer", EXPLORER, "--abis", os.path.join(CONTRACTS, "out"),
             *([os.path.join(os.path.dirname(a.spamoor), "..", "scenarios")] if a.spamoor else []),
             *([PACKAGES] if os.path.isdir(PACKAGES) else [])],
            cwd=CONTRACTS, stdout=log, stderr=subprocess.STDOUT,
        )

    def start_spamoor(self) -> None:
        """Starts spamoor: about one L2 transaction per scenario and L2
        block, a deposit a minute per scenario, and an L2 to L1 message every
        five minutes per scenario, a pace the relayer and the claimer keep up
        with. Spamoor paces a whole
        process, so the L2 messages come from a process of their own, with
        its own funding wallet."""
        a = self.args
        c = self.contracts

        def pings(name: str, target: str) -> dict:
            """Pings to the ping pong on the other chain, each answered by a
            pong."""
            tasks = {"execution": [{"type": "call", "data": {
                "target": target, "call_abi": PING_ABI, "call_fn_name": "ping", "call_args": [],
                "amount": PING_VALUE // 10**9, "gas_limit": 400_000,
            }}]}
            return {"scenario": "taskrunner", "name": name, "config": {
                "seed": name, "throughput": 1, "max_pending": 2, "max_wallets": 2, "tasks_config": json.dumps(tasks),
            }}

        def messages(name: str, target: str, to: str, data: str, gwei: int, gas_limit: int = 0) -> dict:
            """Messages to `to`, whose recipient cannot claim them, so they
            carry a fee for whoever does. `gwei` includes it."""
            tasks = {"execution": [{"type": "call", "data": {
                "target": target, "call_abi": SEND_MESSAGE_ABI, "call_fn_name": "sendMessage",
                "call_args": [to, str(MESSAGE_FEE), str(gas_limit), data], "amount": gwei, "gas_limit": 400_000,
            }}]}
            return {"scenario": "taskrunner", "name": name, "config": {
                "seed": name, "throughput": 1, "max_pending": 2, "max_wallets": 2, "tasks_config": json.dumps(tasks),
            }}

        fees = {"base_fee": 1, "tip_fee_wei": "1000000", "refill_amount": 2 * ETH, "refill_balance": ETH // 2}
        l2 = [
            {"scenario": "erctx", "name": "ERC-20 transfers", "config": {"throughput": 1, "max_wallets": 3, "random_amount": True}},
            {"scenario": "uniswap-swaps", "name": "Uniswap swaps", "config": {"throughput": 1, "max_wallets": 3}},
            {"scenario": "setcodetx", "name": "EIP-7702 delegations", "config": {"throughput": 1, "max_wallets": 2, "max_authorizations": 3, "max_delegators": 20}},
            {"scenario": "frametx", "name": "EIP-8141 frame transactions", "config": {"throughput": 1, "max_wallets": 3, "envelope": "base"}},
        ]
        l2_messages = [messages("withdrawals", L2_MESSENGER, "{randomaddr}", "0x", 2 * MESSAGE_FEE // 10**9)]
        l1 = [messages("deposits", c["rollup"], DEPOSIT_RECIPIENTS, "0x", 10_000_000)]
        if c.get("appsOnL2"):
            l2_messages.append(pings("pings-to-l1", c["pingPongL2"]))
            l1.append(pings("pings-to-l2", c["pingPongL1"]))
        self.contracts["spamoorL2Messages"] = cast("wallet", "address", "--private-key", SPAMOOR_L2_MESSAGES_KEY)
        if int(json_rpc(self.l2_rpc, "eth_getBalance", self.contracts["spamoorL2Messages"], "latest"), 16) < 10 * ETH:
            nonce = json_rpc(self.l2_rpc, "eth_getTransactionCount", self.contracts["spamoorL2"], "pending")
            cast("send", "--rpc-url", self.l2_rpc, "--private-key", SPAMOOR_L2_KEY, "--nonce", str(int(nonce, 16)),
                 self.contracts["spamoorL2Messages"], "--value", str(20 * ETH))
        for name, rpc, key, slot, spammers in (
            ("l1", a.rpc, SPAMOOR_L1_KEY, "60s", l1),
            ("l2", self.l2_rpc, SPAMOOR_L2_KEY, f"{a.block_time}s", l2),
            # Each L2 to L1 message keeps a slot in the messenger's storage.
            ("l2-messages", self.l2_rpc, SPAMOOR_L2_MESSAGES_KEY, "300s", l2_messages),
        ):
            for spammer in spammers:
                spammer["config"] = {"seed": f"{name}-{spammer['name']}", **fees, "max_pending": 3, **spammer["config"]}
            config = os.path.join(DATA, f"spamoor-{name}.json")
            write_json(config, spammers)
            log = open(os.path.join(DATA, f"spamoor-{name}.log"), "a")
            self.spamoor.append(subprocess.Popen(
                [a.spamoor, "run", config, "-h", rpc, "-p", key, "--slot-duration", slot],
                stdout=log, stderr=subprocess.STDOUT,
            ))
        self.event("spamoor", "Spamoor starts generating activity on both chains")

    def start_operator(self) -> None:
        """Starts the sequencer, and files what it reports by block."""
        a = self.args
        log = os.path.join(DATA, "operator.jsonl")
        open(log, "a").close()
        # From where filing stopped, if resuming, so no report is lost.
        size = os.path.getsize(log)
        filed = self.session.get("filed", size)
        threading.Thread(target=self.file_blocks, args=(log, filed if filed <= size else 0), daemon=True).start()
        self.operator = subprocess.Popen(
            ["uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "sequence",
             "--rpc", a.rpc, *[x for url in a.submit_rpc for x in ("--submit-rpc", url)], "--rollup", self.contracts["rollup"],
             "--verifier", self.contracts["verifier"], "--sequencer-key", OPERATOR_KEY, "--prover-key", self.prover_key,
             "--l2-rpc", self.l2_rpc, "--block-time", str(a.block_time), "--proving-time", str(a.proving_time), "--log", log],
            cwd=CONTRACTS, stdout=open(os.path.join(DATA, "operator.log"), "a"), stderr=subprocess.STDOUT,
        )

    def file_blocks(self, log: str, offset: int) -> None:
        """Merges each preconfirmation and post the sequencer reports into
        its block's file, and keeps the session's summary of them: the
        latest preconfirmed and posted blocks, the preconfirmed blocks L1
        does not have yet, and the recent times from preconfirmation to L1."""
        blocks = os.path.join(DATA, "blocks")
        os.makedirs(blocks, exist_ok=True)
        with open(log, "rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line.endswith(b"\n"):
                    time.sleep(0.5)
                    f.seek(f.tell() - len(line))
                    if os.path.getsize(log) < f.tell():
                        f.seek(0)  # rotated: logrotate emptied it
                    continue
                entry = json.loads(line)
                n = entry["l2"]["number"]
                path = os.path.join(blocks, f"{n}.json")
                stored = json.load(open(path)) if os.path.exists(path) else {}
                # Another block preconfirmed at this height, which never
                # reached L1 and was dropped, stays as evidence.
                old = stored.get("preconfirmation")
                if entry["type"] == "preconfirm" and old and old["blockHash"] != entry["preconfirmation"]["blockHash"]:
                    stored.setdefault("broken", []).append({"preconfirmation": old, "l2": stored.get("l2")})
                write_json(path, {**stored, **{k: v for k, v in entry.items() if k != "type"}})
                p = entry["preconfirmation"]
                with self.lock:
                    head = self.session.setdefault("head", {"preconfirmed": 0, "posted": 0})
                    waiting = self.session.setdefault("waiting", [])
                    if entry["type"] == "preconfirm":
                        # A block preconfirmed at a height means the node
                        # dropped any it had preconfirmed there and after.
                        head["preconfirmed"] = n
                        waiting = self.session["waiting"] = [w for w in waiting if w["number"] < n]
                        waiting.append({
                            **p, "transactions": entry["l2"]["transactions"], "gasUsed": entry["l2"]["gasUsed"],
                            "txs": entry["l2"].get("txs", []),
                        })
                    else:
                        head["posted"] = max(head["posted"], n)
                        self.session["waiting"] = [w for w in waiting if w["number"] > head["posted"]]
                        timings = self.session.setdefault("timings", [])
                        timings.append({"number": n, "preconfirmed": p["time"], "posted": entry["l1"]["timestamp"]})
                        del timings[:-30]
                    self.session["filed"] = f.tell()
                    write_json(os.path.join(DATA, "session.json"), self.session)
                print(f"[episode {self.number}] L2 block {n} is {'preconfirmed' if entry['type'] == 'preconfirm' else 'on L1'}", flush=True)

    def stop(self) -> None:
        for process in (*self.spamoor, self.operator, self.follower, self.node):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=30)

    # Story steps

    def deposit(self, user: str, amount: str, key: str | None = None, to: str | None = None, fee: int = 0) -> None:
        """A deposit from `user`'s L1 account to its L2 account, or to `to`,
        with a fee for whoever claims it, if the recipient does not."""
        key = key or USER_KEYS[user]
        address = cast("wallet", "address", "--private-key", key)
        to = to or address
        receipt = json.loads(cast(
            "send", "--rpc-url", self.args.rpc, "--private-key", key, "--json", "--timeout", "120",
            self.contracts["rollup"], "sendMessage(address,uint256,uint256,bytes)", to, str(fee), "0", "0x", "--value", amount,
        ))
        self.event(
            "deposit", f"{user[0].upper() + user[1:]} deposits {amount.replace('ether', ' ETH')} from L1",
            amount=amount, **{"from": address}, to=to,
            l1={"txHash": receipt["transactionHash"], "block": int(receipt["blockNumber"], 16),
                "gasUsed": int(receipt["gasUsed"], 16)},
        )

    def fund_spamoor(self) -> None:
        """Tops up spamoor's L2 funding wallets before they run dry. Spamoor
        sends from them, so the deposits carry a fee, and the relayer claims
        them instead of the wallets themselves."""
        for wallet, low, amount in (("spamoorL2", 50 * ETH, "200ether"), ("spamoorL2Messages", 5 * ETH, "20ether")):
            address = self.contracts.get(wallet)
            if address and int(json_rpc(self.l2_rpc, "eth_getBalance", address, "latest"), 16) < low:
                self.deposit("spamoor", amount, SPAMOOR_L2_KEY, to=address, fee=MESSAGE_FEE)

    def spendable(self, user: str) -> int:
        return max(0, int(json_rpc(self.l2_rpc, "eth_getBalance", self.users[user], "latest"), 16) - RESERVE)

    def send_l2(self, user: str, *args: str) -> str:
        """Sends a transaction from `user` through the L2 RPC, for the next
        block. `cast send` takes the latest nonce, not the pending one."""
        nonce = int(json_rpc(self.l2_rpc, "eth_getTransactionCount", self.users[user], "pending"), 16)
        return cast("send", "--rpc-url", self.l2_rpc, "--private-key", USER_KEYS[user], "--async", "--nonce", str(nonce), *args)

    def pay(self, sender: str, to: str, amount: int) -> None:
        tx = self.send_l2(sender, self.users[to], "--value", str(amount))
        self.event("payment", f"{sender} pays {amount / ETH:g} ETH to {to} on L2", l2Tx=tx)

    def withdraw(self, user: str, amount: int) -> None:
        tx = self.send_l2(user, L2_MESSENGER, "sendMessage(address,uint256,uint256,bytes)", self.users[user], "0", "0", "0x", "--value", str(amount))
        self.event("withdrawal", f"{user} withdraws {amount / ETH:g} ETH to L1", l2Tx=tx)

    def demo_balance(self, user: str, on_l2: bool) -> int:
        """`user`'s DEMO on L1, or of its L2 token on L2."""
        c = self.contracts
        if on_l2 and not c.get("demoTokenL2"):
            token = cast("call", "--rpc-url", self.l2_rpc, c["l2Bridge"], "l2TokenOf(address)(address)", c["demoToken"])
            if int(token, 16) == 0:
                return 0
            c["demoTokenL2"] = token
        rpc, token = (self.l2_rpc, c["demoTokenL2"]) if on_l2 else (self.args.rpc, c["demoToken"])
        return int(cast("call", "--rpc-url", rpc, token, "balanceOf(address)(uint256)", self.users[user]).split()[0])

    def bridge(self, user: str, to_l2: bool, amount: int) -> None:
        """Moves `user`'s DEMO through the ERC-20 bridge, to L2 or back to L1.
        A bridge cannot claim, so its messages carry a fee for whoever does."""
        c = self.contracts
        if to_l2:
            send = lambda *args: json.loads(cast(  # noqa: E731
                "send", "--rpc-url", self.args.rpc, "--private-key", USER_KEYS[user], "--json", "--timeout", "120", *args,
            ))
            send(c["demoToken"], "approve(address,uint256)", c["l1Bridge"], str(amount))
            receipt = send(c["l1Bridge"], "deposit(address,address,uint256)", c["demoToken"], self.users[user], str(amount),
                           "--value", str(MESSAGE_FEE))
            index = next(int(log["topics"][1], 16) for log in receipt["logs"] if log["topics"][0] == L1_MESSAGE_SENT)
            self.event("message", f"{user} bridges {amount / ETH:g} DEMO to L2", to="l2", sender=user, index=index,
                       l1={"txHash": receipt["transactionHash"], "block": int(receipt["blockNumber"], 16)})
        else:
            tx = self.send_l2(user, c["l2Bridge"], "withdraw(address,address,uint256)", c["demoTokenL2"], self.users[user],
                              str(amount), "--value", str(MESSAGE_FEE))
            self.event("message", f"{user} bridges {amount / ETH:g} DEMO back to L1", to="l1", sender=user, l2Tx=tx)

    def ping(self, user: str, from_l1: bool) -> None:
        """A ping from `user` to the ping pong on the other chain, which
        answers it with a pong."""
        c = self.contracts
        if from_l1:
            receipt = json.loads(cast(
                "send", "--rpc-url", self.args.rpc, "--private-key", USER_KEYS[user], "--json", "--timeout", "120",
                c["pingPongL1"], "ping()", "--value", str(PING_VALUE),
            ))
            index = next(int(log["topics"][1], 16) for log in receipt["logs"] if log["topics"][0] == L1_MESSAGE_SENT)
            self.event("message", f"{user} sends a ping to L2", to="l2", sender=user, index=index,
                       l1={"txHash": receipt["transactionHash"], "block": int(receipt["blockNumber"], 16)})
        else:
            tx = self.send_l2(user, c["pingPongL2"], "ping()", "--value", str(PING_VALUE))
            self.event("message", f"{user} sends a ping to L1", to="l1", sender=user, l2Tx=tx)

    def random_payment(self, exclude: tuple) -> None:
        """A payment between users, a small part of what its sender can spend."""
        senders = [u for u in USER_KEYS if u not in exclude and self.spendable(u) > ETH // 100]
        if senders:
            sender = random.choice(senders)
            to = random.choice([u for u in USER_KEYS if u != sender])
            self.pay(sender, to, self.spendable(sender) * random.randint(2, 12) // 100 // 10**14 * 10**14)

    def withdrawals(self) -> list:
        """The withdrawals in L2 blocks the rollup has, with their index,
        recipient, fee and gas limit."""
        logs = json_rpc(self.l2_rpc, "eth_getLogs", {
            "address": L2_MESSENGER, "fromBlock": "0x0", "toBlock": "safe", "topics": [L2_MESSAGE_SENT],
        })
        return [
            (int(log["topics"][1], 16), "0x" + log["topics"][3][-40:], int(log["data"][66:130], 16), int(log["data"][130:194], 16))
            for log in logs
        ]

    def claim(self, index: int, key: str, claimant: str) -> None:
        """Claims an L2 to L1 message, unless anyone already did, such as
        this runner before it restarted, with a claim still on its way."""
        a = self.args
        if cast("call", "--rpc-url", a.rpc, self.contracts["rollup"], "claimedL2Messages(uint256)(bool)", str(index)) == "true":
            self.claimed.add(index)
            return
        run([
            "uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "claim-l2-message",
            "--rpc", a.rpc, "--rollup", self.contracts["rollup"], "--key", key,
            "--index", str(index), "--l2-rpc", self.l2_rpc, "--record", self.record + f".{claimant}",
        ])
        self.claimed.add(index)
        record = json.load(open(self.record + f".{claimant}"))
        value = record["message"]["value"] / ETH
        who = claimant if claimant in USER_KEYS else "The claimer"
        self.event("claimL2Message", f"{who} claims a {value:g} ETH withdrawal on L1",
                   **{k: v for k, v in record.items() if k != "type"})

    def claim_deposits(self) -> None:
        """Each user claims their deposits with their own key once an L2
        block anchors them, as a wallet would, and the relayer claims the
        messages to other addresses."""
        a = self.args
        out = run([
            "uv", "run", "--project", a.zkevm_specs, "python", "script/l2_claims.py",
            "--l1-rpc", a.rpc, "--rollup", self.contracts["rollup"], "--l2-rpc", self.l2_rpc, "--relayer", RELAYER_KEY,
            # Spamoor claims its first deposit itself, before it starts sending.
            *[x for key in [*USER_KEYS.values(), APPS_KEY, *([] if self.spamoor else [SPAMOOR_L2_KEY])] for x in ("--wallet", key)],
        ])
        names = {address.lower(): name for name, address in self.users.items()}
        story = {e["index"]: e["sender"] for e in self.session.get("events", []) if e["type"] == "message" and "index" in e}
        for line in out.splitlines():
            if line.startswith("{"):
                c = json.loads(line)
                if c.get("to") in names:
                    self.event("claim", f"{names[c['to']]} claims a {c['value'] / ETH:g} ETH deposit on L2", l2Tx=c["tx"], deposit=c["index"])
                elif c.get("index") in story and "tx" in c:  # not one it skipped
                    self.event("claim", f"The relayer delivers {story[c['index']]}'s message on L2", l2Tx=c["tx"], deposit=c["index"])

    def claim_withdrawals(self) -> None:
        """Claims on L1 the withdrawals to the story's users, each with the
        recipient's key, though anyone could claim."""
        failed = None
        for index, to, _, _ in self.withdrawals():
            user = next((u for u, address in self.users.items() if address.lower() == to), None)
            if user and index not in self.claimed:
                try:
                    self.claim(index, USER_KEYS[user], user)
                except Exception as e:  # the others still go ahead
                    failed = e
        if failed:
            raise failed

    def relay_withdrawals(self) -> None:
        """Claims on L1, with the claimer's key, the withdrawals to addresses
        outside the story whose fee covers the claim, as any claimer could."""
        users = {address.lower() for address in self.users.values()}
        while self.node is None or self.node.poll() is None:
            try:
                price = int(cast("gas-price", "--rpc-url", self.args.rpc))
                for index, to, fee, gas_limit in self.withdrawals():
                    if to not in users and index not in self.claimed and fee >= (L1_CLAIM_GAS + 2 * gas_limit) * price:
                        try:
                            self.claim(index, CLAIMER_KEY, "claimer")
                        except Exception:  # the others still go ahead
                            traceback.print_exc()
            except Exception:
                traceback.print_exc()
            time.sleep(10)

    def step(self, i: int) -> None:
        """The story: the first steps show each flow once, then they recur."""
        if self.node.poll() is not None:
            raise RuntimeError("the L2 node stopped")
        if self.operator.poll() is not None:
            raise RuntimeError("the sequencer stopped")
        # The follower and spamoor restart on their own, the follower from its
        # last snapshot.
        if self.follower.poll() is not None:
            self.event("restarted", "The follower stopped, and restarts")
            self.start_follower()
        if any(p.poll() is not None for p in self.spamoor):
            self.event("restarted", "Spamoor stopped, and restarts")
            for p in self.spamoor:
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=30)
            self.spamoor = []
            self.start_spamoor()
        # Claiming can fail, as can any transaction, but the block must go on.
        for claims in (self.claim_withdrawals, self.claim_deposits):
            try:
                claims()
            except Exception as e:
                self.event("error", "Claiming failed", message=str(e)[-400:])
        try:
            self.deploy_apps()
        except Exception as e:
            self.event("error", "Deploying the example apps failed", message=str(e)[-400:])
        if i == 0:
            self.deposit("Alice", "1ether")
            self.deposit("Bob", "0.5ether")
            # Spamoor funds its L2 account, the same address as on L1, and
            # the relayer the gas it pays up front for the claims it earns
            # fees with.
            self.deposit("spamoor", "200ether", SPAMOOR_L2_KEY)
            self.deposit("relayer", "1ether", RELAYER_KEY)
        elif i == 1:
            pass  # the next block anchors the deposits, and the users claim them
        elif i == 2:
            self.pay("Alice", "Charlie", 3 * ETH // 10)
        elif i == 3:
            self.withdraw("Charlie", 2 * ETH // 10)
            if self.args.spamoor:
                self.start_spamoor()
        else:
            if i % 12 == 0:
                self.deposit(random.choice(["Alice", "Bob"]), "0.5ether")
            if self.spamoor and i % 25 == 0:
                self.fund_spamoor()
            busy = ()
            # DEMO through the bridge, each way every 20 steps: to L2, and
            # half of what the richest holder there has back to L1.
            if self.contracts.get("appsOnL2") and i % 20 == 5:
                holders = [u for u in ("Alice", "Bob") if self.demo_balance(u, False) >= 100 * ETH]
                if holders:
                    self.bridge(random.choice(holders), True, 100 * ETH)
            # A ping to the other chain every 20 steps, from either side.
            if self.contracts.get("appsOnL2") and i % 20 == 10:
                user, from_l1 = ("Alice", True) if i // 20 % 2 == 0 else ("Bob", False)
                self.ping(user, from_l1)
                if not from_l1:
                    busy = (user,)
            if self.contracts.get("appsOnL2") and i % 20 == 15:
                holder = max(("Alice", "Bob"), key=lambda u: self.demo_balance(u, True))
                amount = self.demo_balance(holder, True) // 2 // ETH * ETH
                if amount:
                    self.bridge(holder, False, amount)
                    busy = (holder,)
            if i % 30 == 0:
                rich = max(USER_KEYS, key=self.spendable)
                if self.spendable(rich) > ETH // 10:
                    self.withdraw(rich, self.spendable(rich) // 4 // 10**15 * 10**15)
                    busy = (*busy, rich)
            for _ in range(random.randint(1, 2)):
                self.random_payment(busy)
        with self.lock:
            self.session["step"] = i
            write_json(os.path.join(DATA, "session.json"), self.session)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", help="an L1 RPC for reads and ordinary transactions, by default the devnet's Reth")
    parser.add_argument("--submit-rpc", nargs="+",
                        help="RPCs that accept blob-carrying frame transactions, by default the devnet's Nethermind and Reth")
    parser.add_argument("--beacon", help="a beacon API that serves blobs, by default the devnet's first Lighthouse")
    parser.add_argument("--zkevm-specs", default=os.path.expanduser("~/work/execution-specs-zkevm-frames"))
    parser.add_argument("--frames-specs", default=os.path.expanduser("~/work/execution-specs-frames"))
    parser.add_argument("--interval", type=float, default=12, help="seconds between steps of the story")
    parser.add_argument("--block-time", type=int, default=4, help="seconds between L2 blocks")
    parser.add_argument("--proving-time", type=int, default=20,
                        help="seconds the sequencer waits before posting a block, standing in for proving")
    parser.add_argument("--l2-port", type=int, default=8547, help="the port of the L2 node's RPC")
    parser.add_argument("--spamoor", default=os.path.expanduser("~/work/spamoor/bin/spamoor"),
                        help="the spamoor binary, or empty for the story alone")
    parser.add_argument("--new", action="store_true", help="deploy a new rollup instead of resuming the last one")
    args = parser.parse_args()
    # Reads go to Reth: Nethermind encodes frame transactions differently
    # from geth and Reth, with other field names and plain numbers.
    args.rpc = args.rpc or devnet_url("el-2-reth", 8545)
    args.submit_rpc = args.submit_rpc or [devnet_url("el-1-nethermind", 8545), devnet_url("el-2-reth", 8545)]
    args.beacon = args.beacon or devnet_url("cl-1-lighthouse", 4000)
    if not args.rpc or not all(args.submit_rpc) or not args.beacon:
        sys.exit("the devnet is not running: start it, or pass --rpc, --submit-rpc and --beacon")
    os.makedirs(DATA, exist_ok=True)

    number = 1
    new = args.new
    while True:
        episode = Episode(args, number)
        try:
            i = None if new else episode.resume()
            if i is None:
                episode.deploy()
                i = 0
            new = False
            while True:
                try:
                    episode.step(i)
                except Exception as e:  # keep the story going, and show what failed
                    if episode.node.poll() is not None or episode.operator.poll() is not None:
                        raise
                    episode.event("error", "A step failed", message=str(e)[-400:])
                    traceback.print_exc()
                i += 1
                time.sleep(args.interval)
        except KeyboardInterrupt:
            episode.stop()
            sys.exit(0)
        except Exception:
            traceback.print_exc()
            time.sleep(args.interval)
        episode.stop()
        number = episode.number + 1


if __name__ == "__main__":
    main()
