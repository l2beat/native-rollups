"""
Serves the demo site and what it reads: the runner's records, and
read-only access to the L1 node and beacon node, so that the page reads the
chain itself instead of trusting the records.

    python3 demo/server.py        # then open http://127.0.0.1:8088
"""

import argparse
import json
import os
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEMO = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DEMO, "data")

READ_METHODS = {
    "eth_blockNumber", "eth_chainId", "eth_call", "eth_getBalance", "eth_getBlockByNumber",
    "eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_getLogs", "eth_blobBaseFee",
}
BEACON_PATHS = ("/eth/v1/beacon/blobs/", "/eth/v1/beacon/genesis", "/eth/v1/config/spec")


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
    print(f"demo at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
