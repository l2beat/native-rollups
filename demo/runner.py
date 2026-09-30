"""
Runs the native rollup demo on the local frames devnet (`contracts/frames/`).

It deploys a fresh rollup, starts the L2 node with its RPC and a follower
that rebuilds the L2 chain from L1 on its own, then plays a scripted story,
adding one L2 block per step: Alice and Bob deposit from L1, and each
deposit pays for its own claim on L2. The users pay each other through the
L2 RPC, and Charlie, who never deposits, withdraws ETH received on L2 to L1
and claims it there. Every step is recorded in `demo/data/session.json`,
which `demo/server.py` serves to the site. If the L2 node stops, the runner
starts over with a new rollup.

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
import time
import traceback
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(ROOT, "contracts")
DATA = os.path.join(ROOT, "demo", "data")

# ethereum-package's prefunded development keys.
OPERATOR_KEY = "0xbcdf20249abf0ed6d944c0288fad489e33f66b3960d9e6229c1cd214ed3bbe31"
# Each user has one key, so one address, on both chains.
USER_KEYS = {
    "Alice": "0x39725efee3fb28614de3bacaffe4cc4bd8c436257e2c8bb887c4b5c4be45e76d",
    "Bob": "0x53321db7c1e331d93a11a41d16f004d7ff63972ec8ec7c25db329728ceeb1710",
    "Charlie": "0xab63b23eb7941c1251757e24b3d2350d2bc05c3c388d06f8fe6feafefb1e8c70",
}
L2_MESSENGER = "0x8079000000000000000000000000000000000001"
# keccak256("L2MessageSent(uint256,address,address,uint256,bytes)")
L2_MESSAGE_SENT = "0xb10e5e7445000e46a527775d169ada0d864e3b30788afd9cae13ce3552e2b14e"
ETH = 10**18
# What a user keeps on L2 for fees.
RESERVE = ETH // 20


def run(cmd: list, cwd: str = CONTRACTS) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, "L2_USER_KEYS": ",".join(USER_KEYS.values())})
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}...: {(out.stderr or out.stdout).strip()[-600:]}")
    return out.stdout


def cast(*args: str) -> str:
    return run(["cast", *args]).strip()


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
        self.claimed = 0  # L2 to L1 messages claimed on L1
        self.node = None
        self.follower = None

    # Recording

    def event(self, kind: str, title: str, **fields) -> None:
        self.session["events"].append({"type": kind, "title": title, "time": int(time.time()), **fields})
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
            cwd=CONTRACTS, capture_output=True, text=True,
            env={**os.environ, "PRIVATE_KEY": OPERATOR_KEY, "PROVER": prover["address"], "ROLLUP": rollup,
                 "GENESIS_HASH": genesis["genesisHash"], "GENESIS_STATE_ROOT": genesis["genesisStateRoot"]},
        )
        found = dict(re.findall(r"^\s+(registry|verifier|rollup|helper)\s+(0x[0-9a-fA-F]{40})", out.stdout, re.M))
        if out.returncode != 0 or found.get("rollup", "").lower() != rollup.lower():
            raise RuntimeError(f"deployment failed: {out.stdout[-600:]} {out.stderr[-600:]}")
        self.users = {name: cast("wallet", "address", "--private-key", key) for name, key in USER_KEYS.items()}
        self.contracts = {
            "rollup": found["rollup"], "verifier": found["verifier"], "registry": found["registry"],
            "framesHelper": found["helper"], "l2Messenger": L2_MESSENGER, "prover": prover["address"],
            "operator": deployer, "users": self.users, "l2GenesisHash": genesis["genesisHash"],
        }
        self.session["contracts"] = self.contracts
        self.event("deployed", "Deployed a new rollup on L1", contracts=self.contracts)
        self.start_node()
        self.start_follower()

    def start_node(self) -> None:
        a = self.args
        log = open(os.path.join(DATA, "l2_node.log"), "a")
        self.node = subprocess.Popen(
            ["uv", "run", "--project", a.zkevm_specs, "python", "script/l2_node.py", "serve",
             "--state", self.state, "--l1-rpc", a.rpc, "--rollup", self.contracts["rollup"], "--port", str(a.l2_port)],
            cwd=CONTRACTS, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "L2_USER_KEYS": ",".join(USER_KEYS.values())},
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
        explorer = os.path.join(DATA, "explorer")
        shutil.rmtree(explorer, ignore_errors=True)
        log = open(os.path.join(DATA, "follower.log"), "a")
        self.follower = subprocess.Popen(
            ["uv", "run", "--project", a.zkevm_specs, "python", "script/l2_follower.py",
             "--l1-rpc", a.rpc, "--beacon", a.beacon, "--rollup", self.contracts["rollup"],
             "--genesis", self.state, "--watch", "--record", os.path.join(DATA, "follower.json"),
             "--explorer", explorer],
            cwd=CONTRACTS, stdout=log, stderr=subprocess.STDOUT,
        )

    def stop(self) -> None:
        for process in (self.follower, self.node):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=30)

    # Story steps

    def deposit(self, user: str, amount: str) -> None:
        receipt = json.loads(cast(
            "send", "--rpc-url", self.args.rpc, "--private-key", USER_KEYS[user], "--json", "--timeout", "120",
            self.contracts["rollup"], "sendMessage(address,bytes)", self.users[user], "0x", "--value", amount,
        ))
        self.event(
            "deposit", f"{user} deposits {amount.replace('ether', ' ETH')} from L1",
            amount=amount, **{"from": self.users[user]}, to=self.users[user],
            l1={"txHash": receipt["transactionHash"], "block": int(receipt["blockNumber"], 16),
                "gasUsed": int(receipt["gasUsed"], 16)},
        )

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
        tx = self.send_l2(user, L2_MESSENGER, "sendMessage(address,bytes)", self.users[user], "0x", "--value", str(amount))
        self.event("withdrawal", f"{user} withdraws {amount / ETH:g} ETH to L1", l2Tx=tx)

    def random_payment(self, exclude: tuple) -> None:
        """A payment between users, a small part of what its sender can spend."""
        senders = [u for u in USER_KEYS if u not in exclude and self.spendable(u) > ETH // 100]
        if senders:
            sender = random.choice(senders)
            to = random.choice([u for u in USER_KEYS if u != sender])
            self.pay(sender, to, self.spendable(sender) * random.randint(2, 12) // 100 // 10**14 * 10**14)

    def advance(self) -> None:
        a = self.args
        run([
            "uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "advance",
            "--rpc", a.rpc, "--submit-rpc", a.submit_rpc, "--rollup", self.contracts["rollup"],
            "--verifier", self.contracts["verifier"], "--operator-key", OPERATOR_KEY,
            "--prover-key", self.prover_key, "--l2-rpc", self.l2_rpc, "--record", self.record,
        ])
        record = json.load(open(self.record))
        n = record["l2"]["number"]
        self.event("advance", f"L2 block {n} is on L1", **{k: v for k, v in record.items() if k != "type"})

    def claim_withdrawals(self) -> None:
        """Claims on L1 the withdrawals in L2 blocks the rollup has."""
        a = self.args
        logs = json_rpc(self.l2_rpc, "eth_getLogs", {
            "address": L2_MESSENGER, "fromBlock": "0x0", "toBlock": "latest", "topics": [L2_MESSAGE_SENT],
        })
        for log in logs[self.claimed:]:
            to = "0x" + log["topics"][3][-40:]
            # The recipient claims, though anyone could.
            user = next(u for u, address in self.users.items() if address.lower() == to)
            run([
                "uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "claim-l2-message",
                "--rpc", a.rpc, "--rollup", self.contracts["rollup"], "--key", USER_KEYS[user],
                "--index", str(self.claimed), "--l2-rpc", self.l2_rpc, "--record", self.record,
            ])
            record = json.load(open(self.record))
            self.claimed += 1
            value = record["message"]["value"] / ETH
            self.event("claimL2Message", f"{user} claims a {value:g} ETH withdrawal on L1",
                       **{k: v for k, v in record.items() if k != "type"})

    def step(self, i: int) -> None:
        """The story: the first steps show each flow once, then they recur.
        Each step ends with an L2 block."""
        if self.node.poll() is not None:
            raise RuntimeError("the L2 node stopped")
        self.claim_withdrawals()
        if i == 0:
            self.deposit("Alice", "1ether")
            self.deposit("Bob", "0.5ether")
        elif i == 1:
            self.pay("Alice", "Charlie", 3 * ETH // 10)
        elif i == 2:
            self.withdraw("Charlie", 2 * ETH // 10)
        else:
            # The node signs a deposit's claim with the recipient's key, at
            # the recipient's nonce, so the recipient does not send in the
            # same block.
            busy = ()
            if i % 6 == 0:
                depositor = random.choice(["Alice", "Bob"])
                self.deposit(depositor, "0.5ether")
                busy = (depositor,)
            if i % 9 == 0:
                rich = max(USER_KEYS, key=self.spendable)
                if rich not in busy and self.spendable(rich) > ETH // 10:
                    self.withdraw(rich, self.spendable(rich) // 4 // 10**15 * 10**15)
                    busy += (rich,)
            for _ in range(random.randint(1, 2)):
                self.random_payment(busy)
        self.advance()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default="http://127.0.0.1:65138", help="an L1 RPC for reads and ordinary transactions")
    parser.add_argument("--submit-rpc", default="http://127.0.0.1:65154",
                        help="a Nethermind or Reth RPC, which accept blob-carrying frame transactions")
    parser.add_argument("--beacon", default="http://127.0.0.1:65167")
    parser.add_argument("--zkevm-specs", default=os.path.expanduser("~/work/execution-specs-zkevm-frames"))
    parser.add_argument("--frames-specs", default=os.path.expanduser("~/work/execution-specs-frames"))
    parser.add_argument("--interval", type=float, default=12, help="seconds between steps")
    parser.add_argument("--l2-port", type=int, default=8547, help="the port of the L2 node's RPC")
    args = parser.parse_args()
    os.makedirs(DATA, exist_ok=True)

    number = 1
    while True:
        episode = Episode(args, number)
        try:
            episode.deploy()
            i = 0
            while True:
                try:
                    episode.step(i)
                except Exception as e:  # keep the story going, and show what failed
                    if episode.node.poll() is not None:
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
        number += 1


if __name__ == "__main__":
    main()
