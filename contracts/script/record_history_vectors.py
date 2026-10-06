"""
Records `test/history_vectors.json`, which `test/SequencedNativeRollup.t.sol`
replays: an earlier L2 block's hash and header, with a proof of the L2
history contract's slot that holds the hash against the latest L2 block's
state root, from a rollup running on a local frames network with its L2 node.

    uv run --project <execution-specs> python script/record_history_vectors.py --l2-rpc <url>
"""

import argparse
import json

from l2_node import json_rpc

HISTORY = "0x0000F90827F1C53a10cb7A02335B175320002935"
HISTORY_WINDOW = 8191
# How far back from the latest block the recorded block is.
DEPTH = 3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2-rpc", required=True)
    args = parser.parse_args()
    head = json_rpc(args.l2_rpc, "eth_getBlockByNumber", "latest", False)
    number = int(head["number"], 16) - DEPTH
    block = json_rpc(args.l2_rpc, "eth_getBlockByNumber", hex(number), False)
    proof = json_rpc(args.l2_rpc, "eth_getProof", HISTORY, [f"0x{number % HISTORY_WINDOW:064x}"], "latest")
    assert int(proof["storageProof"][0]["value"], 16) == int(block["hash"], 16), "history slot"
    vectors = {
        "number": number,
        "blockHash": block["hash"],
        "header": json_rpc(args.l2_rpc, "debug_getRawHeader", hex(number)),
        "historyBlock": int(head["number"], 16),
        "stateRoot": head["stateRoot"],
        "accountProof": proof["accountProof"],
        "storageProof": proof["storageProof"][0]["proof"],
    }
    with open("test/history_vectors.json", "w") as f:
        json.dump(vectors, f, indent=2)
        f.write("\n")
    print("wrote test/history_vectors.json")


if __name__ == "__main__":
    main()
