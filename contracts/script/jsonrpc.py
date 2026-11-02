"""
JSON-RPC calls and HTTP GETs over kept-alive connections, with retries.

Each thread keeps one connection per endpoint, so a process that polls
reuses it instead of opening one per call. On the devnet the L1 RPCs sit
behind Docker's proxy, whose connections to the container stay in TIME-WAIT
for a minute after they close: with a connection per call they filled the
ephemeral ports, and the proxy then reset new connections. A call that fails
in transit, as with a reset, a refused or dropped connection or a timeout, is
retried with backoff. A JSON-RPC error is not: it raises a RuntimeError that
names the method, as does an HTTP error status.

Standard library only, so that the demo's runner can use it too.
"""

import http.client
import json
import threading
import time
import urllib.parse

ATTEMPTS = 5
local = threading.local()


def request(url: str, method: str, body: bytes | None, timeout: float, attempts: int) -> tuple[int, bytes]:
    """The status and body of an HTTP request on this thread's connection
    to `url`'s host."""
    parts = urllib.parse.urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    pool = local.__dict__.setdefault("pool", {})
    key = (parts.scheme, parts.netloc, timeout)
    for attempt in range(attempts):
        connection = pool.get(key)
        if connection is None:
            kind = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            connection = pool[key] = kind(parts.hostname, parts.port, timeout=timeout)
        try:
            connection.request(method, path, body, {"Content-Type": "application/json"} if body is not None else {})
            response = connection.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException):
            connection.close()
            del pool[key]
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.25 * 2**attempt, 4))
    raise AssertionError("unreachable")


def post(url: str, payload, timeout: float, attempts: int):
    status, data = request(url, "POST", json.dumps(payload).encode(), timeout, attempts)
    try:
        return json.loads(data)
    except ValueError:
        raise RuntimeError(f"{url}: HTTP {status}: {data[:200]!r}")


def call(url: str, method: str, *params, timeout: float = 30, attempts: int = ATTEMPTS):
    """The result of one JSON-RPC call."""
    out = post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}, timeout, attempts)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def batch(url: str, calls: list, timeout: float = 30, attempts: int = ATTEMPTS) -> list:
    """The results of `calls`, (method, params) pairs, sent as one JSON-RPC
    batch, in order."""
    if not calls:
        return []
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": list(p)} for i, (m, p) in enumerate(calls)]
    out = sorted(post(url, payload, timeout, attempts), key=lambda r: r["id"])
    for (method, _), r in zip(calls, out):
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
    return [r["result"] for r in out]


def get(url: str, timeout: float = 60, attempts: int = ATTEMPTS):
    """The JSON body of a GET, such as a beacon API's."""
    status, data = request(url, "GET", None, timeout, attempts)
    if status != 200:
        raise RuntimeError(f"GET {url}: HTTP {status}")
    return json.loads(data)
