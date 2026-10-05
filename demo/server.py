"""
Serves the demo site and what it reads: the runner's records, and
read-only access to the L1 node and beacon node, so that the page reads the
chain itself instead of trusting the records.

    python3 demo/server.py        # then open http://127.0.0.1:8088
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from runner import EXPLORER, devnet_url

DEMO = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DEMO, "data")
ROOT = os.path.dirname(DEMO)

# The contracts the explorer shows code from.
SOURCES = [
    "src/NativeRollup.sol", "src/SequencedNativeRollup.sol", "src/frames/FramesNativeRollup.sol",
    "src/frames/FramesSequencedRollup.sol", "src/frames/MockDependencyVerifier.sol", "src/frames/Frames.sol",
    "src/l2/L2Messenger.sol", "src/libs/Messages.sol", "src/libs/MessageTree.sol", "src/libs/MptProof.sol",
    "src/NativeRollupSsz.sol",
]

# The L1 reads the page makes, and nothing else: anyone can reach them.
READ_METHODS = {
    "eth_blockNumber", "eth_call", "eth_getBalance", "eth_getBlockByNumber", "eth_getCode", "eth_getTransactionCount",
}
MAX_REQUEST_BYTES = 4096
# The ERC-20 reads of token pages: name(), symbol(), decimals(), totalSupply()
# and balanceOf(address).
TOKEN_SELECTORS = {"name": "0x06fdde03", "symbol": "0x95d89b41", "decimals": "0x313ce567", "totalSupply": "0x18160ddd"}
BALANCE_OF = "0x70a08231"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
BEACON_PATHS = ("/eth/v1/beacon/blobs/", "/eth/v1/beacon/genesis", "/eth/v1/config/spec")
# ETH's price in USD, for the explorer's mainnet fee estimates.
PRICE_URL = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
PRICE_TTL = 600  # seconds


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


def load_sources(sys_asm: str) -> dict:
    """The full source files the explorer shows for its contracts: the
    Solidity sources with the files they import, the frames helper's geas
    source, and the EIP-8357 registry's source from sys-asm if available."""
    files = {}
    src = os.path.join(ROOT, "contracts", "src")
    for directory, _, names in os.walk(src):
        for name in names:
            if name.endswith(".sol"):
                path = os.path.join(directory, name)
                text = open(path).read()
                imports, external = [], []
                for target in re.findall(r'^import\s*\{[^}]*\}\s*from\s*"([^"]+)";', text, re.M):
                    if target.startswith("."):
                        imports.append(os.path.relpath(os.path.normpath(os.path.join(directory, target)), ROOT))
                    else:
                        external.append(target)
                files[os.path.relpath(path, ROOT)] = {"content": text, "imports": imports, "external": external}
    helper = os.path.join(ROOT, "contracts", "frames", "frame_introspection.eas")
    files["contracts/frames/frame_introspection.eas"] = {"content": open(helper).read(), "imports": [], "external": []}
    registry = os.path.join(sys_asm, "src", "verification_key_registry", "main.eas")
    if os.path.exists(registry):
        files["sys-asm/src/verification_key_registry/main.eas"] = {"content": open(registry).read(), "imports": [], "external": []}
    return files


def flatten(l2beat: str) -> dict:
    """The Solidity contracts flattened by L2BEAT's flattener, if an l2beat
    checkout is available."""
    out = subprocess.run(["node", os.path.join(DEMO, "flatten.mjs"), l2beat], capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        print(f"flattening failed, showing the separate files: {out.stderr.strip()[-300:]}", flush=True)
        return {}
    return json.loads(out.stdout)


def eth_price(server) -> float | None:
    """The last ETH price fetched, refreshed after `PRICE_TTL`, or None if
    it could not be fetched."""
    if time.time() - server.price_time > PRICE_TTL:
        try:
            request = urllib.request.Request(PRICE_URL, headers={"User-Agent": "native-rollups-demo"})
            with urllib.request.urlopen(request, timeout=10) as response:
                server.price = json.load(response)["ethereum"]["usd"]
            server.price_time = time.time()
        except Exception:
            pass
    return server.price


PAGE = 25  # entries per page of a list, as the site shows them
RECENT = 25  # recent entries of each kind the index carries, more than the home page shows
LISTS = {
    "blocks": ("SELECT COALESCE(MAX(number), 0) FROM blocks", "SELECT summary FROM blocks ORDER BY number DESC"),
    "txs": ("SELECT COUNT(*) FROM l2_txs", "SELECT summary FROM l2_txs ORDER BY block DESC, position DESC"),
    "l1": ("SELECT COUNT(*) FROM l1_txs", "SELECT summary FROM l1_txs ORDER BY block DESC"),
    "deposits": ("SELECT COUNT(*) FROM deposits", "SELECT entry FROM deposits ORDER BY idx DESC"),
    "withdrawals": ("SELECT COUNT(*) FROM withdrawals", "SELECT entry FROM withdrawals ORDER BY idx DESC"),
}
HASH = re.compile(r"0x[0-9a-f]{64}")


def rows(db, query: str, *args) -> list:
    return [json.loads(r[0]) for r in db.execute(query, args)]


def explorer_index(db) -> dict:
    """What every page needs: the totals, and the recent blocks, L1
    transactions and messages."""
    meta = {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}
    totals = {}
    for kind, claim in (("deposits", "l2_tx"), ("withdrawals", "l1_tx")):
        count, value, claimed = db.execute(f"SELECT COUNT(*), TOTAL(value), COUNT({claim}) FROM {kind}").fetchone()
        totals[kind] = {"count": count, "value": str(int(value)), "claimed": claimed}
    return {
        "rollup": meta.get("rollup"), "messenger": meta.get("messenger"), "updatedAt": meta.get("updatedAt"),
        "contracts": {a: json.loads(e) for a, e in db.execute("SELECT address, entry FROM contracts")},
        "l2Blocks": rows(db, "SELECT summary FROM blocks ORDER BY number DESC LIMIT ?", RECENT)[::-1],
        "l1Txs": [{k: v for k, v in t.items() if k != "addresses"} for t in rows(db, "SELECT summary FROM l1_txs ORDER BY block DESC LIMIT ?", RECENT)][::-1],
        "deposits": {str(e["index"]): e for e in rows(db, "SELECT entry FROM deposits ORDER BY idx DESC LIMIT ?", RECENT)},
        "withdrawals": {str(e["index"]): e for e in rows(db, "SELECT entry FROM withdrawals ORDER BY idx DESC LIMIT ?", RECENT)},
        "totals": {
            **totals,
            "blocks": db.execute("SELECT COALESCE(MAX(number), 0) FROM blocks").fetchone()[0],
            "transactions": db.execute("SELECT COUNT(*) FROM l2_txs").fetchone()[0],
        },
    }


def explorer_query(db, path: str, query: dict):
    """The answer to a site request under /api/explorer/, or None for one it
    does not make."""
    if path == "index":
        return explorer_index(db)
    if m := re.fullmatch(r"list/(blocks|txs|l1|deposits|withdrawals)", path):
        kind, page = m.group(1), int(query.get("page", "1"))
        if not 1 <= page <= 10**6:
            return None
        count, entries = LISTS[kind]
        if kind == "l1" and "address" in query:
            if not re.fullmatch(r"0x[0-9a-f]{40}", query["address"]):
                return None
            count = "SELECT COUNT(*) FROM l1_tx_addresses WHERE address = ?"
            entries = "SELECT t.summary FROM l1_tx_addresses a JOIN l1_txs t ON t.hash = a.hash WHERE a.address = ? ORDER BY a.block DESC"
            args = (query["address"],)
        else:
            args = ()
        return {
            "total": db.execute(count, args).fetchone()[0],
            "entries": rows(db, entries + " LIMIT ? OFFSET ?", *args, PAGE, (page - 1) * PAGE),
        }
    if m := re.fullmatch(r"messages", path):
        # By index, or by the transaction that sent them: deposits by their
        # L1 transaction, withdrawals by their L2 one.
        out = {}
        for kind, sent in (("deposits", "l1_tx"), ("withdrawals", "l2_tx")):
            ids = [int(i) for i in query.get(kind, "").split(",") if i.isdigit()][:RECENT]
            hashes = [h for h in query.get(f"{kind}By", "").split(",") if HASH.fullmatch(h)][:RECENT]
            found = rows(db, f"SELECT entry FROM {kind} WHERE idx IN ({','.join('?' * len(ids))})", *ids)
            found += rows(db, f"SELECT entry FROM {kind} WHERE {sent} IN ({','.join('?' * len(hashes))})", *hashes)
            out[kind] = {str(e["index"]): e for e in found}
        return out
    if m := re.fullmatch(r"find", path):
        out = {}
        for key, column in (("l1Tx", "l1_tx"), ("l2Tx", "l2_tx")):
            if HASH.fullmatch(query.get(key, "")):
                for kind in ("deposits", "withdrawals"):
                    found = rows(db, f"SELECT entry FROM {kind} WHERE {column} = ?", query[key])
                    out[kind[:-1]] = found[0] if found else out.get(kind[:-1])
        return out
    if m := re.fullmatch(r"holdings/(l1|l2)/(0x[0-9a-f]{40})", path):
        # On L2, an address's tokens: the sum of all their transfers. On L1,
        # where the explorer sees only the rollup's transactions, the tokens
        # these moved, whose balances the page reads from L1.
        chain, a = m.groups()
        if chain == "l1":
            return [{"token": t} for (t,) in db.execute("SELECT DISTINCT token FROM token_transfers WHERE chain = 'l1'")]
        balances = {}
        for token, sender, recipient, value in db.execute(
            "SELECT token, sender, recipient, value FROM token_transfers WHERE chain = 'l2' AND (sender = ? OR recipient = ?)", (a, a),
        ):
            balances[token] = balances.get(token, 0) + (int(value) if recipient == a else 0) - (int(value) if sender == a else 0)
        return [{"token": t, "balance": str(b)} for t, b in balances.items() if b]
    if m := re.fullmatch(r"anchoring/([0-9]+)", path):
        found = rows(db, "SELECT summary FROM blocks WHERE anchor >= ? ORDER BY number LIMIT 1", int(m.group(1)))
        return found[0] if found else None
    if re.fullmatch(r"(l1|l2)/[a-z]+/(0x[0-9a-f]+|[0-9]+)", path):
        found = db.execute("SELECT json FROM docs WHERE path = ?", (path,)).fetchone()
        return json.loads(found[0]) if found else None
    return None


class ExplorerData:
    """The follower's data, read-only, with answers cached until it writes
    again, so that many visitors cost the same as one."""

    def __init__(self, path: str):
        self.path, self.lock, self.cache = path, threading.Lock(), OrderedDict()

    def get(self, path: str, query: dict) -> bytes:
        if not os.path.exists(self.path):
            return b"null"
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            updated = db.execute("SELECT value FROM meta WHERE key = 'updatedAt'").fetchone()
            key = (path, tuple(sorted(query.items())), updated)
            with self.lock:
                if key in self.cache:
                    self.cache.move_to_end(key)
                    return self.cache[key]
            body = json.dumps(explorer_query(db, path, query)).encode()
        except (sqlite3.Error, ValueError):
            return b"null"  # being created, or a query it does not make
        finally:
            db.close()
        with self.lock:
            self.cache[key] = body
            while len(self.cache) > 512:
                self.cache.popitem(last=False)
        return body


class RateLimit:
    """Requests per visitor to what reaches the devnet, a token bucket each:
    `rate` a second, in bursts of `burst`. Behind Cloudflare, the visitor is
    the address it forwards; locally, the client's."""

    def __init__(self, rate: float, burst: int):
        self.rate, self.burst, self.buckets, self.lock = rate, burst, {}, threading.Lock()

    def allow(self, visitor: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, then = self.buckets.get(visitor, (self.burst, now))
            tokens = min(self.burst, tokens + (now - then) * self.rate)
            if len(self.buckets) > 100_000:
                self.buckets.clear()
            self.buckets[visitor] = (tokens - 1, now) if tokens >= 1 else (tokens, now)
            return tokens >= 1


def eth_call(url: str, to: str, data: str, tag: str) -> str | None:
    """A call's return data, or None if it fails."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, tag]}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"content-type": "application/json"}), timeout=20) as r:
            out = json.loads(r.read()).get("result")
    except (OSError, ValueError):
        return None
    return out if isinstance(out, str) and len(out) > 2 else None


def token(server, chain: str, address: str, holder: str | None) -> dict | None:
    """An ERC-20's name, symbol, decimals and supply, and `holder`'s balance:
    on L1 now, on L2 as of its latest block on L1, as the explorer shows L2.
    None if the address is not a token."""
    url, tag = (server.rpc, "latest") if chain == "l1" else (server.l2_rpc, "safe")
    key = (chain, address.lower())
    if key not in server.tokens:
        out = {k: eth_call(url, address, selector, tag) for k, selector in TOKEN_SELECTORS.items() if k != "totalSupply"}
        if not all(out.values()):
            return None
        text = lambda h: bytes.fromhex(h[2 + 128:2 + 128 + 2 * int(h[66:130], 16)]).decode(errors="replace")  # noqa: E731
        server.tokens[key] = {"name": text(out["name"]), "symbol": text(out["symbol"]), "decimals": int(out["decimals"], 16)}
    supply = eth_call(url, address, TOKEN_SELECTORS["totalSupply"], tag)
    balance = holder and eth_call(url, address, BALANCE_OF + "0" * 24 + holder[2:].lower(), tag)
    return {**server.tokens[key], "totalSupply": str(int(supply, 16)) if supply else None,
            **({"balance": str(int(balance, 16)) if balance else "0"} if holder else {})}


def allowed(request) -> bool:
    """Whether a JSON-RPC request is one the page makes. Its calls are view
    functions without arguments, so `eth_call` takes a target and a selector
    only, which keeps arbitrary code off the L1 node, or the 32 zero bytes
    that ask the EIP-8357 registry for its current entry."""
    if not isinstance(request, dict) or request.get("method") not in READ_METHODS:
        return False
    if request["method"] != "eth_call":
        return True
    params = request.get("params")
    return (
        isinstance(params, list) and len(params) == 2 and params[1] == "latest" and isinstance(params[0], dict)
        and set(params[0]) == {"to", "data"} and re.fullmatch(r"0x([0-9a-fA-F]{8}|0{64})", str(params[0]["data"])) is not None
    )


class Handler(SimpleHTTPRequestHandler):
    # Seconds a connection may stay idle, so slow clients do not hold threads.
    timeout = 30
    server_version, sys_version = "native-rollup-explorer", ""

    def visitor(self) -> str:
        return self.headers.get("cf-connecting-ip") or self.client_address[0]

    def end_headers(self):
        # The page is checked on every visit, and names its script and styles
        # by their content, which caches may then keep for good. The book and
        # the site's other files change only when the demo is updated, so
        # caches in front of the server may keep them for a few minutes.
        if self.command == "GET" and not self.path.startswith(("/api/", "/beacon/", "/rpc")):
            page = urllib.parse.urlsplit(self.path).path in ("/", "/index.html")
            versioned = "?v=" in self.path
            self.send_header("cache-control", "no-cache" if page else "public, max-age=31536000, immutable" if versioned else "public, max-age=300")
        super().end_headers()

    def send_page(self) -> None:
        """The site's page, with its script and styles named by a hash of their
        content, so that a browser never runs an old script with a new page,
        or a new one with an old page."""
        site = os.path.join(DEMO, "site")
        page = open(os.path.join(site, "index.html")).read()
        for name in ("app.js", "style.css"):
            digest = hashlib.sha256(open(os.path.join(site, name), "rb").read()).hexdigest()[:12]
            page = page.replace(f'"{name}"', f'"{name}?v={digest}"')
        body = page.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(DEMO, "site"), **kwargs)

    def send_json(self, body: bytes, status: int = 200) -> None:
        # Pages poll several of these every few seconds.
        compress = len(body) > 1024 and "gzip" in self.headers.get("accept-encoding", "")
        if compress:
            body = gzip.compress(body, compresslevel=5)
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.send_header("vary", "accept-encoding")
            if compress:
                self.send_header("content-encoding", "gzip")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client left

    def forward(self, request: urllib.request.Request) -> None:
        """Answers with what the L1 or beacon node answers, or 502."""
        try:
            with urllib.request.urlopen(request, timeout=20) as r:
                return self.send_json(r.read())
        except urllib.error.HTTPError as e:
            return self.send_json(e.read() or b'{"error": "upstream error"}', e.code)
        except OSError:
            return self.send_json(b'{"error": "the chain is not reachable"}', 502)

    def do_GET(self):
        if '"scheme":"http"' in self.headers.get("cf-visitor", "").replace(" ", ""):
            # A visitor through Cloudflare on plain HTTP: to HTTPS.
            self.send_response(301)
            self.send_header("location", f"https://{self.headers.get('host', '')}{self.path}")
            self.end_headers()
            return
        if self.path == "/book":
            self.send_response(301)
            self.send_header("location", "/book/")
            self.end_headers()
            return
        if self.path.startswith("/book/"):
            # The book, as `mdbook build` builds it from this checkout.
            self.directory = os.path.join(ROOT, "book")
            self.path = self.path[len("/book"):]
            return super().do_GET()
        if urllib.parse.urlsplit(self.path).path in ("/", "/index.html"):
            return self.send_page()
        if self.path.startswith("/api/explorer/"):
            url = urllib.parse.urlsplit(self.path)
            path = url.path[len("/api/explorer/"):].removesuffix(".json")
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            body = self.server.explorer.get(path, query)
            return self.send_json(body, 200 if body != b"null" else 404)
        if self.path == "/api/flat":
            return self.send_json(json.dumps(self.server.flat).encode())
        if self.path == "/api/sources":
            return self.send_json(json.dumps(self.server.sources).encode())
        if self.path == "/api/snippets":
            return self.send_json(json.dumps(self.server.snippets).encode())
        if re.fullmatch(r"/api/blocks/[0-9]+", self.path):
            # What the sequencer reported about the block: its preconfirmation and post.
            path = os.path.join(DATA, "blocks", self.path.split("/")[-1] + ".json")
            return self.send_json(open(path, "rb").read() if os.path.exists(path) else b"null")
        if self.path == "/api/session":
            path = os.path.join(DATA, self.path.split("/")[-1] + ".json")
            return self.send_json(open(path, "rb").read() if os.path.exists(path) else b"null")
        if self.path == "/api/eth-price":
            return self.send_json(json.dumps({"usd": eth_price(self.server)}).encode())
        if self.path.startswith("/api/token/"):
            url = urllib.parse.urlsplit(self.path)
            parts = url.path.split("/")[3:]
            holder = urllib.parse.parse_qs(url.query).get("holder", [None])[0]
            if len(parts) != 2 or parts[0] not in ("l1", "l2") or not ADDRESS.fullmatch(parts[1]) or (holder and not ADDRESS.fullmatch(holder)):
                return self.send_json(b'{"error": "not found"}', 404)
            if not self.server.limit.allow(self.visitor()):
                return self.send_json(b'{"error": "too many requests"}', 429)
            found = token(self.server, parts[0], parts[1], holder)
            return self.send_json(json.dumps(found).encode(), 200 if found else 404)
        if self.path.startswith("/beacon/"):
            if not self.server.limit.allow(self.visitor()):
                return self.send_json(b'{"error": "too many requests"}', 429)
            path = self.path[len("/beacon"):]
            if not path.startswith(BEACON_PATHS):
                return self.send_json(b'{"error": "not allowed"}', 403)
            return self.forward(urllib.request.Request(self.server.beacon + path))
        return super().do_GET()

    def do_POST(self):
        if self.path != "/rpc":
            return self.send_json(b'{"error": "not found"}', 404)
        if not self.server.limit.allow(self.visitor()):
            return self.send_json(b'{"error": "too many requests"}', 429)
        length = self.headers.get("content-length", "")
        if not length.isdigit() or int(length) > MAX_REQUEST_BYTES:
            return self.send_json(b'{"error": "too large"}', 413)
        body = self.rfile.read(int(length))
        try:
            request = json.loads(body)
        except ValueError:
            return self.send_json(b'{"error": "not JSON"}', 400)
        if not allowed(request):
            return self.send_json(b'{"error": "not allowed"}', 403)
        return self.forward(urllib.request.Request(self.server.rpc, data=body, headers={"content-type": "application/json"}))

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # Pages fetch many small files at once.
    request_queue_size = 256

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            super().handle_error(request, client_address)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--rpc", help="an L1 RPC, by default the devnet's Reth")
    parser.add_argument("--beacon", help="a beacon API, by default the devnet's first Lighthouse")
    parser.add_argument("--l2-rpc", default="http://127.0.0.1:8547", help="the L2 node's RPC, for the tokens of L2 pages")
    parser.add_argument("--sys-asm", default=os.path.expanduser("~/work/sys-asm"), help="for the EIP-8357 registry's source")
    parser.add_argument("--l2beat", default=os.path.expanduser("~/work/l2beat"), help="for L2BEAT's flattener")
    args = parser.parse_args()
    # Reads go to Reth: Nethermind encodes frame transactions differently
    # from geth and Reth, with other field names and plain numbers.
    args.rpc = args.rpc or devnet_url("el-2-reth", 8545)
    args.beacon = args.beacon or devnet_url("cl-1-lighthouse", 4000)
    if not args.rpc or not args.beacon:
        sys.exit("the devnet is not running: start it, or pass --rpc and --beacon")
    server = Server(("127.0.0.1", args.port), Handler)
    server.rpc, server.beacon, server.l2_rpc = args.rpc, args.beacon, args.l2_rpc
    server.tokens = {}  # token metadata, which does not change
    server.explorer = ExplorerData(EXPLORER)
    # A page makes about one L1 call a second, and a few at once on load.
    server.limit = RateLimit(rate=5, burst=40)
    server.snippets = extract_snippets()
    server.price, server.price_time = None, 0.0
    server.sources = load_sources(args.sys_asm)
    server.flat = flatten(args.l2beat)
    print(f"demo at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
