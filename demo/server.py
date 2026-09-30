"""
Serves the demo site and what it reads: the runner's records, and
read-only access to the L1 node and beacon node, so that the page reads the
chain itself instead of trusting the records.

    python3 demo/server.py        # then open http://127.0.0.1:8088
"""

import argparse
import json
import os
import re
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEMO = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DEMO, "data")
ROOT = os.path.dirname(DEMO)

# The contracts the explorer shows code from.
SOURCES = [
    "src/NativeRollup.sol", "src/frames/FramesNativeRollup.sol", "src/frames/MockDependencyVerifier.sol", "src/frames/Frames.sol",
    "src/l2/L2Messenger.sol", "src/libs/Messages.sol", "src/libs/MessageTree.sol", "src/libs/MptProof.sol",
    "src/NativeRollupSsz.sol",
]

READ_METHODS = {
    "eth_blockNumber", "eth_chainId", "eth_call", "eth_getBalance", "eth_getBlockByNumber",
    "eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_getLogs", "eth_blobBaseFee", "eth_getCode",
    "eth_getTransactionCount",
}
BEACON_PATHS = ("/eth/v1/beacon/blobs/", "/eth/v1/beacon/genesis", "/eth/v1/config/spec")


def extract_snippets() -> dict:
    """Each function of SOURCES with its doc comment, keyed by
    `Contract.function`, and the other extracted functions it calls."""
    snippets = {}
    for rel in SOURCES:
        text = open(os.path.join(ROOT, "contracts", rel)).read()
        lines = text.split("\n")
        declarations = [(m.start(), m.group(1)) for m in re.finditer(r"^(?:abstract )?(?:contract|library) (\w+)", text, re.M)]
        for m in re.finditer(r"^    (?:function (\w+)|(fallback|receive))\s*\(", text, re.M):
            name = m.group(1) or m.group(2)
            contract = [d for pos, d in declarations if pos < m.start()][-1]
            # Find the body: the first brace outside the parameter list, or none.
            depth, i, body = 0, m.start(), None
            while i < len(text):
                c = text[i]
                depth += c == "("
                depth -= c == ")"
                if depth == 0 and c == ";":
                    break
                if depth == 0 and c == "{":
                    body = i
                    break
                i += 1
            if body is None:
                continue
            braces, j = 0, body
            while True:
                braces += text[j] == "{"
                braces -= text[j] == "}"
                if braces == 0:
                    break
                j += 1
            first = text.count("\n", 0, m.start())
            while first > 0 and lines[first - 1].strip().startswith("///"):
                first -= 1
            last = text.count("\n", 0, j)
            code = "\n".join(line[4:] if line.startswith("    ") else line for line in lines[first : last + 1])
            snippets[f"{contract}.{name}"] = {
                "contract": contract, "name": name, "file": f"contracts/{rel}",
                "startLine": first + 1, "endLine": last + 1, "code": code, "body": text[body:j],
            }
    for key, snippet in snippets.items():
        calls = []
        for lib, fn in re.findall(r"\b(?:(\w+)\.)?(\w+)\s*\(", snippet.pop("body")):
            candidates = [f"{lib}.{fn}"] if lib else [f"{snippet['contract']}.{fn}"] + [k for k in snippets if k.endswith(f".{fn}")]
            found = next((c for c in candidates if c in snippets and c != key), None)
            if found and found not in calls:
                calls.append(found)
        snippet["calls"] = calls
    return snippets


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(DEMO, "site"), **kwargs)

    def send_json(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/explorer/"):
            relative = self.path[len("/api/explorer/"):]
            path = os.path.join(DATA, "explorer", relative)
            if not re.fullmatch(r"[a-z0-9/]+(\.json)", relative) or ".." in relative or not os.path.exists(path):
                return self.send_json(b"null", 404)
            return self.send_json(open(path, "rb").read())
        if self.path == "/api/snippets":
            return self.send_json(json.dumps(self.server.snippets).encode())
        if self.path in ("/api/session", "/api/follower"):
            path = os.path.join(DATA, self.path.split("/")[-1] + ".json")
            return self.send_json(open(path, "rb").read() if os.path.exists(path) else b"null")
        if self.path.startswith("/beacon/"):
            path = self.path[len("/beacon"):]
            if not path.startswith(BEACON_PATHS):
                return self.send_json(b'{"error": "not allowed"}', 403)
            with urllib.request.urlopen(self.server.beacon + path, timeout=20) as r:
                return self.send_json(r.read())
        return super().do_GET()

    def do_POST(self):
        if self.path != "/rpc":
            return self.send_json(b'{"error": "not found"}', 404)
        body = self.rfile.read(int(self.headers["content-length"]))
        request = json.loads(body)
        if request.get("method") not in READ_METHODS:
            return self.send_json(b'{"error": "not allowed"}', 403)
        forward = urllib.request.Request(self.server.rpc, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(forward, timeout=20) as r:
            return self.send_json(r.read())

    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--rpc", default="http://127.0.0.1:65138")
    parser.add_argument("--beacon", default="http://127.0.0.1:65167")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.rpc, server.beacon = args.rpc, args.beacon
    server.snippets = extract_snippets()
    print(f"demo at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
