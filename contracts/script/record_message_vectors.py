"""
Records the message vectors that `test/L2Messenger.t.sol` and
`test/NativeRollupMessages.t.sol` replay, from a rollup running on a local
frames network with its L2 node.

- `test/l1_message_vectors.json`: for three L2 blocks whose anchors each
  have new L1 messages, the anchor, the call proving the L1 message root
  against it, and the calls claiming the new messages with their paths to
  that root.
- `test/l2_message_vectors.json`: L2 to L1 messages, two without a gas limit
  and two with one, with proofs of their queue entries against the latest L2
  block's state root.

    uv run --project <execution-specs> python script/record_message_vectors.py \
        --l1-rpc <url> --rollup <address> --l2-rpc <url>
"""

import argparse
import json

from ethereum.crypto.hash import keccak256

from l2_claims import CLAIM_SIGNATURE, L1_MESSAGE_ROOT_SLOT, PROVE_ROOT_SIGNATURE, cast, l1_messages, leaf, message_tree
from l2_node import L2_MESSAGE_SENT, L2_MESSENGER, hx, json_rpc

SENT_SLOT = 2  # L2Messenger.sentMessages
# Receives the fees of the recorded claims, as in the tests.
CLAIMER = "0x000000000000000000000000000000000000c1a1"


def l1_vectors(args: argparse.Namespace) -> dict:
    head = int(json_rpc(args.l2_rpc, "eth_blockNumber"), 16)
    blocks, claimed = [], 0
    for number in range(1, head + 1):
        block = json_rpc(args.l2_rpc, "eth_getBlockByNumber", hex(number), False)
        anchor = int(json_rpc(args.l1_rpc, "eth_getBlockByHash", block["parentBeaconBlockRoot"], False)["number"], 16)
        messages = l1_messages(args.l1_rpc, args.rollup, anchor)
        # The last block needs two new messages, one of them to a contract,
        # with a gas limit, for the gas measurement.
        new = messages[claimed:]
        if not new or (len(blocks) == 2 and (len(new) < 2 or not any(m["gasLimit"] for m in new))):
            continue
        leaves = [leaf(m) for m in messages]
        root, paths = message_tree(leaves)
        height = (len(leaves) - 1).bit_length()
        proof = json_rpc(args.l1_rpc, "eth_getProof", args.rollup, [f"0x{L1_MESSAGE_ROOT_SLOT:064x}"], hex(anchor))
        assert int(proof["storageProof"][0]["value"], 16) == int.from_bytes(root, "big"), "L1 message root"
        blocks.append({
            "anchorHash": block["parentBeaconBlockRoot"],
            "proveRoot": cast(
                "calldata", PROVE_ROOT_SIGNATURE, str(int(block["timestamp"], 16)),
                json_rpc(args.l1_rpc, "debug_getRawHeader", hex(anchor)),
                "[" + ",".join(proof["accountProof"]) + "]", "[" + ",".join(proof["storageProof"][0]["proof"]) + "]",
            ),
            "claims": [
                cast(
                    "calldata", CLAIM_SIGNATURE,
                    f"({m['sender']},{m['to']},{m['value']},{m['fee']},{m['gasLimit']},{m['data']},{m['index']})",
                    "[" + ",".join(hx(p) for p in paths[m["index"]][:height]) + "]", CLAIMER,
                )
                for m in messages[claimed:]
            ],
        })
        claimed = len(messages)
        if len(blocks) == 3:
            return {"l1Rollup": args.rollup.lower(), "blocks": blocks}
    raise SystemExit("the rollup needs three L2 blocks whose anchors have new L1 messages")


def l2_vectors(args: argparse.Namespace) -> dict:
    head = json_rpc(args.l2_rpc, "eth_getBlockByNumber", "latest", False)
    logs = json_rpc(args.l2_rpc, "eth_getLogs", {
        "address": L2_MESSENGER, "fromBlock": "0x0", "toBlock": "latest", "topics": [hx(L2_MESSAGE_SENT)],
    })
    plain = [log for log in logs if not int(log["data"][130:194], 16)][:2]
    called = [log for log in logs if int(log["data"][130:194], 16)][:2]
    claims = []
    for log in sorted(plain + called, key=lambda log: int(log["topics"][1], 16)):
        data = bytes.fromhex(log["data"][2:])
        offset = int.from_bytes(data[96:128], "big")
        index = int(log["topics"][1], 16)
        slot = int.from_bytes(keccak256(SENT_SLOT.to_bytes(32, "big")), "big") + index
        proof = json_rpc(args.l2_rpc, "eth_getProof", L2_MESSENGER, [f"0x{slot:064x}"], "latest")
        claims.append({
            "message": {
                "sender": "0x" + log["topics"][2][-40:], "to": "0x" + log["topics"][3][-40:],
                "value": int.from_bytes(data[0:32], "big"), "fee": int.from_bytes(data[32:64], "big"),
                "gasLimit": int.from_bytes(data[64:96], "big"), "data": hx(data[offset + 32 : offset + 32 + int.from_bytes(data[offset : offset + 32], "big")]), "index": index,
            },
            "blockNumber": int(head["number"], 16),
            "stateRoot": head["stateRoot"],
            "accountProof": proof["accountProof"],
            "storageProof": proof["storageProof"][0]["proof"],
        })
    if len(plain) < 2 or not called:
        raise SystemExit("the rollup needs two L2 to L1 messages without a gas limit and one with")
    return {"l2Messenger": L2_MESSENGER, "claims": claims}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l1-rpc", required=True)
    parser.add_argument("--rollup", required=True)
    parser.add_argument("--l2-rpc", required=True)
    args = parser.parse_args()
    for path, vectors in (("test/l1_message_vectors.json", l1_vectors(args)), ("test/l2_message_vectors.json", l2_vectors(args))):
        with open(path, "w") as f:
            json.dump(vectors, f, indent=2)
            f.write("\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
