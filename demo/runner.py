"""
Runs the native rollup demo on the local frames devnet (`contracts/frames/`).

Each episode deploys a fresh rollup, starts a follower that rebuilds the L2
chain from L1 on its own, then advances the rollup with a scripted story:
Alice and Bob deposit from L1, and each deposit pays for its own claim on
L2. The users pay each other on L2, and Charlie, who never deposits,
withdraws ETH received on L2 to L1 and claims it there. Every step is
recorded in `demo/data/session.json`, which `demo/server.py` serves to the
site. After `--blocks` L2 blocks the
episode ends and a new one starts, since the L2 node rebuilds its chain from
genesis for every block.

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
ETH = 10**18
# What a user keeps on L2 for fees, above the L2 node's own reserve.
RESERVE = ETH // 20


def run(cmd: list, cwd: str = CONTRACTS) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, "L2_USER_KEYS": ",".join(USER_KEYS.values())})
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:6])}...: {(out.stderr or out.stdout).strip()[-600:]}")
    return out.stdout


def cast(*args: str) -> str:
    return run(["cast", *args]).strip()


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
        self.withdrawals = []  # the recipient of each withdrawal, in order
        self.claimed = 0
        self.balances = {}  # each user's L2 balance after the last L2 block
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
        self.start_follower()

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
        if self.follower and self.follower.poll() is None:
            self.follower.terminate()
            self.follower.wait(timeout=30)

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
        return max(0, self.balances.get(self.users[user].lower(), 0) - RESERVE)

    def payments(self, count: int, exclude: str | None = None) -> list:
        """Up to `count` payments between users, each a small part of what
        its sender can spend."""
        out = []
        for _ in range(count):
            senders = [u for u in USER_KEYS if u != exclude and self.spendable(u) > ETH // 100]
            if not senders:
                break
            sender = random.choice(senders)
            to = random.choice([u for u in USER_KEYS if u != sender])
            out.append((sender, to, self.spendable(sender) * random.randint(2, 12) // 100 // 10**14 * 10**14))
        return out

    def advance(self, withdraw: tuple | None = None, pay: list | None = None) -> None:
        a = self.args
        cmd = [
            "uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "advance",
            "--rpc", a.rpc, "--submit-rpc", a.submit_rpc, "--rollup", self.contracts["rollup"],
            "--verifier", self.contracts["verifier"], "--operator-key", OPERATOR_KEY,
            "--prover-key", self.prover_key, "--l2-state", self.state, "--zkevm-specs", a.zkevm_specs,
            "--record", self.record,
        ]
        if withdraw:
            user, amount = withdraw
            cmd += ["--withdraw", f"{self.users[user]}:{self.users[user]}:{amount}"]
        for sender, to, amount in pay or self.payments(1 if withdraw else 2, exclude=withdraw and withdraw[0]):
            cmd += ["--transfer", f"{self.users[sender]}:{self.users[to]}:{amount}"]
        run(cmd)
        record = json.load(open(self.record))
        self.withdrawals += [m["to"] for m in record["l2"]["l2Messages"]]
        self.balances = {a.lower(): v for a, v in record["l2"]["balances"].items()}
        n = record["l2"]["number"]
        self.event("advance", f"L2 block {n} is on L1", **{k: v for k, v in record.items() if k != "type"})

    def claim(self) -> None:
        a = self.args
        # The recipient claims, though anyone could.
        user = next(u for u, address in self.users.items() if address.lower() == self.withdrawals[self.claimed].lower())
        run([
            "uv", "run", "--project", a.frames_specs, "python", "script/frames_operator.py", "claim-l2-message",
            "--rpc", a.rpc, "--rollup", self.contracts["rollup"], "--key", USER_KEYS[user],
            "--index", str(self.claimed), "--l2-state", self.state, "--zkevm-specs", a.zkevm_specs,
            "--record", self.record,
        ])
        record = json.load(open(self.record))
        self.claimed += 1
        value = record["message"]["value"] / 10**18
        self.event("claimL2Message", f"{user} claims a {value:g} ETH withdrawal on L1",
                   **{k: v for k, v in record.items() if k != "type"})

    def step(self, i: int) -> None:
        """The story: the first steps show each flow once, then they recur."""
        opening = [
            lambda: self.deposit("Alice", "1ether"),
            lambda: self.deposit("Bob", "0.5ether"),
            lambda: self.advance(),
            lambda: self.advance(pay=[("Alice", "Charlie", 3 * ETH // 10)]),
            lambda: self.advance(withdraw=("Charlie", 2 * ETH // 10)),
            lambda: self.claim(),
        ]
        if i < len(opening):
            return opening[i]()
        if self.claimed < len(self.withdrawals):
            return self.claim()
        if i % 6 == 0:
            return self.deposit(random.choice(["Alice", "Bob"]), "0.5ether")
        if i % 9 == 0:
            rich = max(USER_KEYS, key=self.spendable)
            if self.spendable(rich) > ETH // 10:
                return self.advance(withdraw=(rich, self.spendable(rich) // 4 // 10**15 * 10**15))
        return self.advance()

    def l2_blocks(self) -> int:
        return sum(1 for e in self.session["events"] if e["type"] == "advance")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default="http://127.0.0.1:65138", help="an L1 RPC for reads and ordinary transactions")
    parser.add_argument("--submit-rpc", default="http://127.0.0.1:65154",
                        help="a Nethermind or Reth RPC, which accept blob-carrying frame transactions")
    parser.add_argument("--beacon", default="http://127.0.0.1:65167")
    parser.add_argument("--zkevm-specs", default=os.path.expanduser("~/work/execution-specs-zkevm-frames"))
    parser.add_argument("--frames-specs", default=os.path.expanduser("~/work/execution-specs-frames"))
    parser.add_argument("--interval", type=float, default=20, help="seconds between steps")
    parser.add_argument("--blocks", type=int, default=150, help="L2 blocks per episode")
    args = parser.parse_args()
    os.makedirs(DATA, exist_ok=True)

    number = 1
    while True:
        episode = Episode(args, number)
        try:
            episode.deploy()
            i = 0
            while episode.l2_blocks() < args.blocks:
                try:
                    episode.step(i)
                except Exception as e:  # keep the story going, and show what failed
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
