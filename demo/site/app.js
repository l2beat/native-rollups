// An annotated explorer for the native rollup demo.
//
// L2 blocks and transactions come from the follower, which rebuilds them
// from L1 alone (/api/explorer). L1 transactions are decoded by the same
// follower from the L1 node. Live chain state is read directly through /rpc
// and /beacon. The operator's own records (/api/session) only add what never
// reaches L1, such as its run of the validation program.

const BLOB_USABLE_BYTES = 4096 * 31;
const PAGE = 25;
const BOOK = "/book";
const SELECTORS = {
  blockNumber: "0x57e871e7", blockHash: "0xf22a195e", stateRoot: "0x9588eca2", l1MessageCount: "0x1214990d",
  l1MessageRoot: "0xdf06c677", anchorBlockNumber: "0x3cad82ff", chainId: "0x9a8a0592", l2GasLimit: "0xcf6e65b7",
  l2Messenger: "0xf5730a72", evmVkRegistry: "0x369e4ac9", prover: "0x32a8f30f", sequencer: "0x5c1bba38", bond: "0x64c9ec6f",
};

const state = { index: null, session: null, l1Head: null, rollupHead: null, cache: {}, records: {}, blobs: {}, beacon: null, snippets: null, sources: null, flat: null, messages: { deposits: {}, withdrawals: {} } };

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  return r.ok ? r.json() : null;
}

async function rpc(method, params = []) {
  const r = await fetch("/rpc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return (await r.json()).result;
}

async function object(path) {
  if (!(path in state.cache)) state.cache[path] = await getJSON(`/api/explorer/${path}.json`);
  return state.cache[path];
}

async function refresh() {
  if (!state.snippets) state.snippets = await getJSON("/api/snippets");
  if (!state.sources) state.sources = await getJSON("/api/sources");
  if (!state.flat) state.flat = await getJSON("/api/flat");
  if (state.ethUsd === undefined) state.ethUsd = ((await getJSON("/api/eth-price")) || {}).usd ?? null;
  const [index, session, head] = await Promise.all([getJSON("/api/explorer/index.json"), getJSON("/api/session"), rpc("eth_blockNumber")]);
  if (index && state.index && index.rollup !== state.index.rollup) [state.cache, state.records] = [{}, {}]; // a new episode
  state.index = index;
  state.session = session;
  state.messages = { deposits: {}, withdrawals: {} }; // older messages fetched for this render
  state.l1Head = head ? parseInt(head, 16) : null;
  if (index) {
    const [n, bond] = await Promise.all(["blockNumber", "bond"].map((k) => rpc("eth_call", [{ to: index.rollup, data: SELECTORS[k] }, "latest"])));
    state.rollupHead = n ? parseInt(n, 16) : null;
    state.bond = bond ? BigInt(bond).toString() : null;
  }
  const route = parseRoute();
  if (["home", "blocks", "txs", "l1list", "messages", "message", "address"].includes(route.page)) render(true);
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const short = (h) => (h && h.length > 18 ? `${h.slice(0, 10)}…${h.slice(-6)}` : h || "");
const num = (n) => Number(n).toLocaleString("en-US");
// Amounts in ETH, or in gwei below a millionth of an ETH, or in wei for dust.
const eth = (wei) => {
  const n = Number(wei);
  if (n === 0 || n >= 1e12) return `${(n / 1e18).toLocaleString("en-US", { maximumFractionDigits: 6 })} ETH`;
  if (n >= 1e3) return `${(n / 1e9).toLocaleString("en-US", { maximumSignificantDigits: 6 })} gwei`;
  return `${n} wei`;
};
// Fees in lists, as Etherscan shows them.
const ethShort = (wei) => `${(Number(wei) / 1e18).toLocaleString("en-US", { maximumSignificantDigits: 3 })} ETH`;
const badge = (kind, text) => `<span class="badge ${kind}">${text || kind}</span>`;
const pct = (x) => `${(100 * x).toFixed(x < 0.1 ? 1 : 0)}%`;
const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  // A no-break space, so "ago" never wraps away from its number.
  return s < 60 ? `${s}s\u00a0ago` : s < 3600 ? `${Math.round(s / 60)}min\u00a0ago` : `${Math.round(s / 3600)}h\u00a0ago`;
};
// The median, which a restart's gap does not skew.
const median = (xs) => (xs.length ? [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)] : null);
const secs = (s) => (s < 120 ? `${Math.round(s)}s` : `${Math.round(s / 60)}min`);
// Hashes of nothing, which could pass for arbitrary values.
const EMPTY_HASHES = {
  "0xe3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855": "the SHA-256 of nothing: no requests (EIP-7685)",
  "0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421": "the root of an empty trie",
  "0x87b69a306c8e430d0857f7c4ac5e27cecffa1108d43c2e5df7388056fea7a423": "the SSZ root of no execution requests",
};
// Values that stand in for something the demo cannot have yet.
const MOCKED = {
  // keccak256("frames-devnet mock EVM verification key"), in contracts/script/DeployFrames.s.sol
  "0xf0cc70b85867f9592b138c7425d60e0bed9b7034cac36a485dd8a06ba68c88f1": "a placeholder EVM verification key hash",
};
// What a value is, when the explorer knows: the hash of nothing, or a mock.
const tags = (h) => (EMPTY_HASHES[h] ? ` <span class="empty-hash">empty: ${EMPTY_HASHES[h]}</span>` : "")
  + (MOCKED[h] ? ` <span class="nowrap">${badge("mock")} <span class="tag-note">${MOCKED[h]}</span></span>` : "");
const hash = (h, full = false) => `<span class="mono" title="${esc(h)}">${esc(full ? h : short(h))}</span>${tags(h)}`;
// Every link to a block, transaction or address says which chain it is on.
const net = (chain) => `<span class="net ${chain}">${chain.toUpperCase()}</span>`;
const l2BlockLink = (n) => `<span class="nowrap">${net("l2")}<a href="#/l2/block/${n}">#${n}</a></span>`;
const l2TxLink = (h) => `<span class="nowrap">${net("l2")}<a class="mono" href="#/l2/tx/${h}" title="${h}">${short(h)}</a></span>`;
const l1TxLink = (h) => `<span class="nowrap">${net("l1")}<a class="mono" href="#/l1/tx/${h}" title="${h}">${short(h)}</a></span>`;
const fill = (bytes) => {
  const x = bytes / BLOB_USABLE_BYTES;
  return `<span class="minifill"><span style="width:${Math.max(2, 100 * x)}%"></span></span>${pct(x)}`;
};

// The caller of EIP-8141's DEFAULT and VERIFY frames. SENDER frames run as
// the transaction's sender.
const ENTRY_POINT = "0x00000000000000000000000000000000000000aa";
const frameCaller = (tx, f) => (f.mode === "SENDER" ? tx.from : ENTRY_POINT);

function labels() {
  const c = (state.session && state.session.contracts) || {};
  const l = {
    "0xfffffffffffffffffffffffffffffffffffffffe": ["ETH transfer log (EIP-7708)", ""],
    "0x000f3df6d732807ef1319fb7b8bb8522d0beac02": ["Beacon roots contract (EIP-4788)", ""],
    "0x0000000000000000000000000000000000000fee": ["Fee recipient", ""],
    "0x00000961ef480eb55e80d19ad83579a64c007002": ["Withdrawal requests (EIP-7002)", ""],
    "0x0000bbddc7ce488642fb579f8b00f3a590007251": ["Consolidation requests (EIP-7251)", ""],
    "0x0000bff46984e3725691fa540a8c7589300d8282": ["Builder requests (EIP-8282)", ""],
    "0x000064d678505ad48f8ccb093bc65613800e8282": ["Builder requests (EIP-8282)", ""],
    "0x0000f90827f1c53a10cb7a02335b175320002935": ["Block hash history (EIP-2935)", ""],
    "0x00000000219ab540356cbb839cbe05303d7705fa": ["Beacon deposit contract", ""],
    "0x0000000000000000000000000000000000008141": ["Expiry verifier (EIP-8141)", ""],
    [ENTRY_POINT]: ["Entry point (EIP-8141)", ""],
  };
  const add = (a, name, kind) => a && (l[a.toLowerCase()] = [name, kind]);
  Object.entries((state.index && state.index.contracts) || {}).forEach(([a, x]) => x.name && !l[a] && add(a, x.name, ""));
  add(c.rollup, "Rollup contract", "real");
  add(c.l2Messenger, "L2 messenger", "real");
  add(c.verifier, "Proof checker", "mock");
  add(c.registry, "Key registry", "mock");
  add(c.prover, "Trusted prover key", "mock");
  add(c.operator, "Operator", "");
  add(c.framesHelper, "Frames helper", "");
  // Each user has one key, so one address, on both chains.
  Object.entries(c.users || {}).forEach(([name, a]) => add(a, name, ""));
  add(c.relayer, "Relayer", "");
  add(c.claimer, "Claimer", "");
  add(c.spamoorL1, "Spamoor", "");
  add(c.spamoorL2, "Spamoor", "");
  add(c.spamoorL2Messages, "Spamoor", "");
  add(c.apps, "App developer", "");
  add(c.pingPongL1, "Ping pong", "");
  add(c.pingPongL2, "Ping pong", "");
  add(c.l1Bridge, "ERC-20 bridge", "");
  add(c.l2Bridge, "ERC-20 bridge", "");
  add(c.demoToken, "Demo token", "");
  add(c.demoTokenL2, "Demo token", "");
  return l;
}

// Labels known addresses and links them to their page on `chain`. Only
// mocked contracts get a badge, since most are real.
function addr(a, chain, showBadge = true) {
  if (!a) return "";
  const l = labels()[a.toLowerCase()];
  const text = l
    ? `${esc(l[0])} <span class="mono muted">${short(a)}</span>`
    : `<span class="mono">${short(a)}</span>`;
  return `<span class="nowrap">${net(chain)}<a class="addr" href="#/address/${chain}/${a.toLowerCase()}" title="${esc(a)} on ${chain.toUpperCase()}">${text}</a></span>${showBadge && l && l[1] === "mock" ? " " + badge("mock") : ""}`;
}

// The chain of an address field: messages have one end on each chain.
const OTHER = { l1: "l2", l2: "l1" };
function chainFor(key, ctx = {}) {
  const c = ctx.chain || "l1";
  const ends = {
    L1MessageSent: { sender: "l1", to: "l2" }, L1MessageClaimed: { sender: "l1", to: "l2" },
    L2MessageSent: { sender: "l2", to: "l1" }, L2MessageClaimed: { sender: "l2", to: "l1" },
    claimL1Message: { sender: "l1", to: "l2" }, claimL2Message: { sender: "l2", to: "l1" },
    sendMessage: { to: OTHER[c] }, advance: { feeRecipient: "l2" },
    // The example apps' calls and events name accounts on the other chain.
    receivePing: { player: OTHER[c] }, PingReceived: { player: OTHER[c] },
    deposit: { to: "l2" }, DepositSent: { to: "l2" }, finalizeDeposit: { l1Token: "l1", from: "l1" },
    DepositFinalized: { l1Token: "l1", from: "l1" }, TokenDeployed: { l1Token: "l1" },
    withdraw: { to: "l1" }, WithdrawalSent: { l1Token: "l1", to: "l1" }, finalizeWithdrawal: { from: "l2" },
    WithdrawalFinalized: { from: "l2" },
  };
  const m = ends[ctx.event] || ends[ctx.fn];
  return (m && m[key]) || c;
}

// Messages either way are messages, whether they carry ETH, a call or both.
const KIND_NAMES = { "deposit claim": ["message claim", "message claims"], withdrawal: ["message to L1", "messages to L1"] };
const kindText = (kind, n) => {
  const [one, many] = KIND_NAMES[kind] || [kind, `${kind}s`];
  return `${n ? `${n} ` : ""}${n > 1 ? many : one}`;
};
const contents = (kinds) =>
  ["deposit claim", "withdrawal", "transfer", "call", "deploy", "delegation", "frame transaction"].filter((k) => kinds[k]).map((k) => kindText(k, kinds[k])).join(", ") || '<span class="muted">empty</span>';
const TX_TYPES = { 0: "legacy", 1: "EIP-2930 access list", 2: "EIP-1559", 4: "EIP-7702 set code", 6: "EIP-8141 frame transaction" };
const L1_KINDS = { advance: "adds an L2 block", deposit: "message to L2", "withdrawal claim": "message claim" };
// The function a message's call runs, as transaction lists show it: its
// name if the explorer knows it, or its selector.
const hasData = (d) => !!d && d !== "0x";
const callOf = (method, data) => (method && method !== data.slice(0, 10) ? `<code>${esc(method)}</code>` : `<code class="muted">${data.slice(0, 10)}</code>`);
const withValue = (value) => (BigInt(value || 0) > 0n ? ` with ${eth(value)}` : "");

// Tabs, as on Etherscan. All panels render with the page, so switching only
// changes which one shows, at once, and the URL, so each tab stays linkable.
// Items are [key, label, count, html].
function tabs(base, items, active) {
  const list = items.filter(Boolean);
  const current = list.some(([key]) => key === active) ? active : list[0][0];
  return `<nav class="tabs">${list.map(([key, label, count]) =>
    `<a class="${key === current ? "active" : ""}" data-tab="${key}" href="#${base}${key ? "/" + key : ""}">${label}${count !== undefined ? ` <span class="muted">(${num(count)})</span>` : ""}</a>`).join("")}</nav>
    ${list.map(([key, , , html]) => `<div class="tab-panel" data-panel="${key}"${key === current ? "" : " hidden"}>${html}</div>`).join("")}`;
}

document.addEventListener("click", (e) => {
  const a = e.target.closest(".tabs a[data-tab]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  const nav = a.parentElement;
  nav.querySelectorAll("a").forEach((x) => x.classList.toggle("active", x === a));
  nav.parentElement.querySelectorAll(":scope > .tab-panel").forEach((panel) => (panel.hidden = panel.dataset.panel !== a.dataset.tab));
  history.pushState(null, "", a.getAttribute("href"));
  // The next refresh renders this tab, and keeps the scroll as on the same page.
  shown.key = location.hash;
});

const more = (rows) => (rows.length ? `<details class="more"><summary>More details</summary>${fields(rows)}</details>` : "");

function fields(rows) {
  return `<table class="fields"><tbody>${rows
    .map(([label, value, note]) => `<tr><td>${label}</td><td class="value">${value}</td><td class="note">${note || ""}</td></tr>`)
    .join("")}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

function parseRoute() {
  const route = pageRoute();
  route.at = location.hash.split("@")[1] || "";
  return route;
}

function pageRoute() {
  const parts = location.hash.split("@")[0].replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!parts.length) return { page: "home" };
  if (parts[0] === "blocks") return { page: "blocks", n: Number(parts[1] || 1) };
  if (parts[0] === "txs") return { page: "txs", n: Number(parts[1] || 1) };
  if (parts[0] === "l1" && parts[1] === "tx") return { page: "l1tx", hash: parts[2], tab: parts[3] || "" };
  if (parts[0] === "l1") return { page: "l1list", n: Number(parts[1] || 1) };
  if (parts[0] === "l2" && parts[1] === "block") return { page: "block", n: Number(parts[2]), tab: parts[3] || "" };
  if (parts[0] === "l2" && parts[1] === "tx") return { page: "l2tx", hash: parts[2], tab: parts[3] || "" };
  if (parts[0] === "messages") return { page: "messages", tab: parts[1] || "", n: Number(parts[2] || 1) };
  if (parts[0] === "deposit" || parts[0] === "withdrawal") return { page: "message", kind: parts[0], n: Number(parts[1]) };
  if (parts[0] === "blob") return { page: "blob", n: Number(parts[1]) };
  if (parts[0] === "address") return { page: "address", chain: parts[1], address: (parts[2] || "").toLowerCase(), tab: parts[3] || "" };
  if (parts[0] === "about") return { page: "about" };
  return { page: "missing" };
}

let renders = 0;
// `live` renders refresh the current page quietly; navigation shows when a
// page takes a moment to load.
async function render(live = false) {
  const app = document.getElementById("app");
  const route = parseRoute();
  const seq = ++renders;
  const loading = live ? null : setTimeout(() => document.body.classList.add("loading"), 120);
  if (!state.index && route.page !== "about") {
    app.innerHTML = `<p class="note">Waiting for the follower to rebuild the chain from L1.</p>`;
    return;
  }
  const pages = { home, blocks: blockList, txs: txList, l1list: l1List, block: blockPage, l2tx: l2TxPage, l1tx: l1TxPage, messages, message: messagePage, about, address: addressPage, blob: blobPage };
  try {
    const html = await (pages[route.page] || (() => `<h1>Not found</h1>`))(route);
    if (seq === renders) update(app, html, location.hash);
  } finally {
    clearTimeout(loading);
    if (seq === renders) document.body.classList.remove("loading");
  }
}

// Live pages re-render on every refresh. Only touch the page when it changed,
// and keep the scroll of inner blocks, such as sources, and which sections
// are open.
let shown = { key: null, html: null };
function update(app, html, key) {
  if (shown.key === key && shown.html === html) return;
  const same = shown.key === key;
  const scrolls = same ? [...app.querySelectorAll("pre")].map((e) => [e.scrollTop, e.scrollLeft]) : [];
  const open = same ? [...app.querySelectorAll("details")].map((d) => d.open) : [];
  app.innerHTML = html;
  shown = { key, html };
  [...app.querySelectorAll("pre")].forEach((e, i) => i < scrolls.length && ([e.scrollTop, e.scrollLeft] = scrolls[i]));
  [...app.querySelectorAll("details")].forEach((d, i) => i < open.length && (d.open = open[i]));
}

// ---------------------------------------------------------------------------
// Lists
// ---------------------------------------------------------------------------

function blockRows(blocks) {
  return `<table><thead><tr><th>Block</th><th class="hide-narrow">Age</th><th class="num">Txs</th><th>Contents</th>
    <th class="num hide-narrow">Gas used</th><th>Posted in L1 tx</th><th class="hide-narrow">Blob use</th><th>Rebuilt from L1</th></tr></thead><tbody>
    ${blocks.map((b) => `<tr><td>${l2BlockLink(b.number)}</td><td class="hide-narrow">${ago(b.timestamp)}</td>
      <td class="num">${b.transactions}</td><td>${contents(b.kinds)}</td><td class="num hide-narrow">${num(b.gasUsed)}</td>
      <td>${l1TxLink(b.l1Tx)} <span class="muted">in ${num(b.l1Block)}</span></td><td class="hide-narrow"><a href="#/blob/${b.number}">${fill(b.payloadBytes)}</a></td>
      <td><span class="check">✓</span></td></tr>`).join("")}</tbody></table>`;
}

function waitingRows(blocks) {
  return `<table><thead><tr><th>Block</th><th>Preconfirmed</th><th class="num">Txs</th><th>Hash</th><th>Must reach L1 by</th></tr></thead><tbody>
    ${blocks.map((p) => `<tr><td>${l2BlockLink(p.number)}</td><td>${ago(p.time)}</td><td class="num">${num(p.transactions)}</td>
      <td>${hash(p.blockHash)}</td><td>L1 block ${num(deadline(p.anchorBlockNumber))}</td></tr>`).join("")}</tbody></table>`;
}

// Etherscan's columns: the method, or what the transaction does when it calls none.
function l2TxRows(txs, self) {
  const party = (x) => (!x ? "" : x === self ? '<span class="muted">this address</span>' : addr(x, "l2"));
  return `<table class="txs"><thead><tr><th>Transaction hash</th><th>Method</th><th>Block</th><th class="hide-narrow">Age</th>
    <th>From</th><th>To</th><th class="num">Value</th><th class="num">Fee</th></tr></thead><tbody>
    ${txs.map((t) => `<tr><td>${l2TxLink(t.hash)}</td><td>${t.method ? `<code>${esc(t.method)}</code>` : kindText(t.kind)}</td>
      <td>${l2BlockLink(t.block)}</td><td class="hide-narrow nowrap">${t.time ? ago(t.time) : ""}</td>
      <td>${party(t.from)}</td><td>${party(t.to)}</td>
      <td class="num">${t.value ? eth(t.value) : ""}</td><td class="num">${t.fee !== undefined && t.fee !== null ? ethShort(t.fee) : ""}</td></tr>`).join("")}</tbody></table>`;
}

function l1Rows(txs) {
  return `<table><thead><tr><th>Transaction</th><th>What it does</th><th class="num">L1 block</th><th class="hide-narrow">Age</th>
    <th class="num">Gas</th><th>Related</th></tr></thead><tbody>
    ${txs.map((t) => `<tr><td>${l1TxLink(t.hash)}</td><td>${esc(L1_KINDS[t.kind] || t.kind)}</td><td class="num">${num(t.block)}</td>
      <td class="hide-narrow">${ago(t.timestamp)}</td><td class="num">${num(t.gasUsed)}</td>
      <td>${t.l2Block ? `L2 block ${l2BlockLink(t.l2Block)}` : ""}</td></tr>`).join("")}</tbody></table>`;
}

function messageRows(kind, entries) {
  if (!entries.length) return `<p class="note">None yet.</p>`;
  const one = kind === "deposits" ? "deposit" : "withdrawal";
  if (kind === "deposits") {
    return `<table class="txs"><thead><tr><th>#</th><th class="num">Value</th><th>Method</th><th class="num">Fee</th><th>From (L1)</th><th>To (L2)</th><th>Sent on L1</th><th>Claimed on L2</th></tr></thead><tbody>
      ${entries.map((d) => `<tr><td><a href="#/${one}/${d.index}">${d.index}</a></td><td class="num">${d.value !== undefined ? eth(d.value) : ""}</td><td>${hasData(d.data) ? callOf(d.method, d.data) : ""}</td><td class="num">${d.fee ? eth(d.fee) : ""}</td><td>${addr(d.from, "l1")}</td><td>${addr(d.to, "l2")}</td>
        <td>${d.l1Tx ? l1TxLink(d.l1Tx) : ""}</td><td>${d.l2Tx ? `${l2TxLink(d.l2Tx)} in ${l2BlockLink(d.l2Block)}` : '<span class="muted">waiting for an L2 block that anchors it</span>'}</td></tr>`).join("")}</tbody></table>`;
  }
  return `<table class="txs"><thead><tr><th>#</th><th class="num">Value</th><th>Method</th><th class="num">Fee</th><th>From (L2)</th><th>To (L1)</th><th>Sent on L2</th><th>Claimed on L1</th></tr></thead><tbody>
    ${entries.map((w) => `<tr><td><a href="#/${one}/${w.index}">${w.index}</a></td><td class="num">${w.value !== undefined ? eth(w.value) : ""}</td><td>${hasData(w.data) ? callOf(w.method, w.data) : ""}</td><td class="num">${w.fee ? eth(w.fee) : ""}</td><td>${addr(w.from, "l2")}</td><td>${addr(w.to, "l1")}</td>
      <td>${w.l2Tx ? `${l2TxLink(w.l2Tx)} in ${l2BlockLink(w.l2Block)}` : ""}</td><td>${w.l1Tx ? l1TxLink(w.l1Tx) : '<span class="muted">not claimed yet</span>'}</td></tr>`).join("")}</tbody></table>`;
}

function pager(route, total, base) {
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const n = Math.min(Math.max(route.n, 1), pages);
  return `<div class="pager">${n > 1 ? `<a href="#/${base}/${n - 1}">← Newer</a>` : ""}<span class="muted">Page ${n} of ${pages}</span>${n < pages ? `<a href="#/${base}/${n + 1}">Older →</a>` : ""}</div>`;
}

async function home() {
  const ix = state.index;
  const blocks = [...ix.l2Blocks].reverse();
  // The latest transactions, newest first: the preconfirmed ones, then
  // those in blocks rebuilt from L1. Most blocks may be empty, so only those
  // with transactions.
  const latest = waitingTxs().map((t) => ({ ...t, timestamp: t.time }));
  for (const b of blocks.slice(0, 100).filter((b) => b.transactions)) {
    if (latest.length >= 6) break;
    const block = await object(`l2/blocks/${b.number}`);
    if (block) latest.push(...[...block.transactions].reverse().map((t) => ({ ...t, block: b.number, timestamp: b.timestamp })));
  }
  const txs = await Promise.all(latest.slice(0, 6).map((t) => t.preconfirmed ? t : object(`l2/txs/${t.hash}`).then((x) => ({ ...t, ...(x || {}) }))));
  const head = blocks.length ? await object(`l2/blocks/${blocks[0].number}`) : null;
  const rec = head && (await blockRecord(head.number));
  await loadStoryMessages();
  const between = await crossings();
  // What the status strip does not already say: activity, value, and cost on L1.
  const escrow = await rpc("eth_getBalance", [ix.rollup, "latest"]);
  const recent = blocks.slice(0, 20);
  const { deposits, withdrawals, transactions: total } = ix.totals;
  const interval = median(recent.slice(1).map((b, i) => recent[i].timestamp - b.timestamp));
  const stats = [
    ["L2 transactions", num(total)],
    ["ETH on L2", escrow ? `${(Number(BigInt(escrow)) / 1e18).toLocaleString("en-US", { maximumFractionDigits: 2 })} <span class="unit">ETH</span>` : ""],
    ["Messages to L2", num(deposits.count)],
    ["Messages to L1", num(withdrawals.count)],
    ["Block time", interval ? `${Math.round(interval)}s` : ""],
  ];
  return `
    <div class="stats">${stats.map(([label, value]) => `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("")}</div>
    <div class="columns">
      <div class="panel"><div class="panel-head"><b>Latest L2 blocks</b><a href="#/blocks">View all blocks →</a></div>
        ${(() => {
          const waiting = waitingBlocks().slice(0, 3), posted = blocks.slice(0, 6 - waiting.length);
          // The blocks between the two, which the panel has no room for.
          const from = posted.length && posted[0].number + 1, to = waiting.length && waiting[waiting.length - 1].number - 1;
          const between = from && to >= from ? `<div class="item-more">${to > from ? `#${num(from)} to #${num(to)}` : `#${num(from)}`} also preconfirmed</div>` : "";
          return waiting.map((p) => `<div class="item"><div><div class="line">${l2BlockLink(p.number)}</div><div class="line muted">${ago(p.time)}</div></div>
              <div><div class="line">${num(p.transactions)} transactions</div><div class="line muted">preconfirmed by the sequencer</div></div>
              <div class="num"><div class="line muted">${num(p.gasUsed)} gas</div></div></div>`).join("")
            + between + posted.map((b) => `<div class="item"><div><div class="line">${l2BlockLink(b.number)}</div><div class="line muted">${ago(b.timestamp)}</div></div>
              <div><div class="line">${num(b.transactions)} transactions</div><div class="line"><span class="muted">posted in</span> ${l1TxLink(b.l1Tx)}</div></div>
              <div class="num"><div class="line muted">${num(b.gasUsed)} gas</div><div class="line" title="Its share of a blob">${fill(b.payloadBytes)}</div></div></div>`).join("");
        })()}</div>
      <div class="panel"><div class="panel-head"><b>Latest L2 transactions</b><a href="#/txs">View all transactions →</a></div>
        ${txs.map((t) => `<div class="item tx"><div><div class="line">${l2TxLink(t.hash)}</div><div class="line muted">${ago(t.timestamp)}</div></div>
          <div><div class="line"><span class="muted">From</span> ${addr(t.from, "l2")}</div>
            <div class="line">${t.to ? `<span class="muted">To</span> ${addr(t.to, "l2")}` : t.frames ? `<span class="muted">${t.frames.length ?? t.frames} frames</span>` : '<span class="muted">contract creation</span>'}</div></div>
          <div class="num"><div class="line">${t.value ? eth(t.value) : ""}</div></div></div>`).join("")}</div>
    </div>
    ${between}
    ${head ? `<h2>How block ${l2BlockLink(head.number)} reached L1</h2>${journey(head, rec)}
      <p class="section-lead">Each field its proof covers: where the rollup contract gets it on L1, and what it is on L2.</p>
      ${boundaryView(head, rec)}` : ""}`;
}

// Whether a story event sent a message from L1 to L2, or from L2 to L1.
const toL2 = (e) => e.type === "deposit" || (e.type === "message" && e.to === "l2");
const toL1 = (e) => e.type === "withdrawal" || (e.type === "message" && e.to === "l1");

// The message a deposit, withdrawal or call of the runner's story sent.
function storyMessage(e) {
  const find = (kind, key, value) => [...Object.values(state.index[kind]), ...Object.values(state.messages[kind])].find((m) => m[key] === value);
  if (toL2(e)) return ["deposit", find("deposits", "l1Tx", e.l1.txHash)];
  if (toL1(e)) return ["withdrawal", find("withdrawals", "l2Tx", e.l2Tx)];
  return [null, null];
}

// Fetches the messages of the story's latest deposits, withdrawals and calls
// that the index does not carry.
async function loadStoryMessages() {
  const events = (state.session && state.session.events) || [];
  const missing = (type, test, tx) => events.filter(test).map(tx).filter((h) => h && !storyMessage({ type, l1: { txHash: h }, l2Tx: h })[1]);
  const deposits = missing("deposit", toL2, (e) => e.l1 && e.l1.txHash);
  const withdrawals = missing("withdrawal", toL1, (e) => e.l2Tx);
  if (!deposits.length && !withdrawals.length) return;
  const found = await getJSON(`/api/explorer/messages.json?depositsBy=${deposits.join(",")}&withdrawalsBy=${withdrawals.join(",")}`);
  if (found) for (const kind of ["deposits", "withdrawals"]) Object.assign(state.messages[kind], found[kind]);
}

// The function a transaction calls, or those its frames call, by name.
const methodName = (tx) => (tx.frames ? tx.frames.map((f) => f.call) : [tx.call]).filter(Boolean).map((c) => c.function).join(", ");

// What a message does, in a few words, from the message and the transaction
// that sends it: tokens it bridges, ETH it moves, or the call that sends it.
async function crossingTitle(m, tx, deposit) {
  const name = (a) => (a ? (labels()[a.toLowerCase()] || [short(a)])[0] : "");
  const [from, to] = deposit ? ["l1", "l2"] : ["l2", "l1"];
  const who = name(tx ? tx.from : m.from);
  // Tokens locked in the sender of the message, or burned for it: a bridge.
  const moved = tx && transfersOf(tx).find(([, f, t]) => t === m.from.toLowerCase() || (t === ZERO_ADDRESS && f === tx.from.toLowerCase()));
  if (moved) {
    const t = await tokenInfo(from, moved[0]);
    if (t) return `${who} bridges ${units(moved[3], t)} to ${to.toUpperCase()}`;
  }
  if (!hasData(m.data)) return `${who} ${deposit ? "deposits" : "withdraws"} ${eth(m.value)}`;
  if (tx && m.from.toLowerCase() !== tx.from.toLowerCase() && methodName(tx)) return `${who} calls ${methodName(tx)} on ${name(tx.to)}`;
  return `${who} calls ${m.method} on ${name(m.to)} on ${to.toUpperCase()}`;
}

// The messages of the demo's users, newest first, each sent on one chain and
// claimed on the other, with how long the crossing took, or what it waits
// for. The runner's records say which messages are theirs; what each says
// comes from the chains.
async function crossings() {
  const events = (state.session && state.session.events) || [];
  const story = events.filter((e) => toL2(e) || toL1(e)).slice(-5).reverse();
  if (!story.length) return "";
  const waiting = waitingTxs();
  const txLink = { l1: l1TxLink, l2: l2TxLink };
  const l2Time = async (n) => (state.index.l2Blocks.find((b) => b.number === n) || (await object(`l2/blocks/${n}`)) || {}).timestamp;
  const l1TxTime = async (h) => l1Time(h) || ((await object(`l1/txs/${h}`)) || {}).timestamp;
  // The newest L1 block an L2 block anchored: deposits up to it can be claimed.
  const anchored = Math.max(0, ...state.index.l2Blocks.map((b) => b.anchor), ...waitingBlocks().map((p) => p.anchorBlockNumber));
  const rows = await Promise.all(story.map(async (e) => {
    const [kind, m] = storyMessage(e);
    const deposit = toL2(e);
    const [from, to] = deposit ? ["l1", "l2"] : ["l2", "l1"];
    // Sent: a withdrawal's block may still be preconfirmed.
    const sentTx = deposit ? e.l1.txHash : e.l2Tx;
    const sentPre = !deposit && !m && waiting.find((t) => t.hash === sentTx);
    const sentBlock = deposit ? e.l1.block : m ? m.l2Block : sentPre && sentPre.block;
    const sentTime = (deposit ? await l1TxTime(sentTx) : m && (await l2Time(m.l2Block))) || e.time;
    // Claimed: a deposit's claim may still be preconfirmed.
    let claimTx = m && (deposit ? m.l2Tx : m.l1Tx), claimBlock = m && (deposit ? m.l2Block : m.l1Block), claimPre = null, claimTime;
    if (deposit && !claimTx && m) {
      const event = events.find((c) => c.type === "claim" && c.deposit === m.index);
      claimPre = event && waiting.find((t) => t.hash === event.l2Tx);
      if (claimPre) [claimTx, claimBlock, claimTime] = [claimPre.hash, claimPre.block, claimPre.time];
    } else if (claimTx) {
      claimTime = deposit ? await l2Time(claimBlock) : await l1TxTime(claimTx);
    }
    const sentDoc = m && (await object(`${from}/txs/${sentTx}`));
    const title = m ? await crossingTitle(m, sentDoc, deposit)
      : sentPre ? `${(labels()[sentPre.from.toLowerCase()] || [short(sentPre.from)])[0]} sends a message to L1` : "A message";
    const reason = deposit
      ? anchored >= sentBlock ? "an L2 block anchored it, so it can be claimed" : `waiting for an L2 block to anchor L1 block ${num(sentBlock)}`
      : sentBlock && state.rollupHead >= sentBlock ? "its block is on L1, so it can be claimed" : "waiting for its block to reach L1";
    return `<div class="item crossing">
      <div><div class="line"><a href="${m ? `#/${kind}/${m.index}` : `#/${from}/tx/${sentTx}`}">${esc(title)}</a></div>
        <div class="line muted">${ago(sentTime)}</div></div>
      <div><div class="line"><span class="muted">Sent</span> ${txLink[from](sentTx)}</div>
        <div class="line muted">${sentBlock ? `block ${num(sentBlock)}` : ""}${sentPre ? ", preconfirmed" : ""}</div></div>
      ${claimTx
        ? `<div><div class="line"><span class="muted">Claimed</span> ${txLink[to](claimTx)}</div>
          <div class="line">${claimTime ? `${secs(claimTime - sentTime)} later` : ""}<span class="muted">${claimBlock ? `, block ${num(claimBlock)}` : ""}${claimPre ? ", preconfirmed" : ""}</span></div></div>`
        : `<div><div class="line muted">Not claimed yet</div><div class="line muted">${reason}</div></div>`}</div>`;
  }));
  return `<div class="panel crossings"><div class="panel-head"><b>Between the chains</b><a href="#/messages">All messages →</a></div>
    ${rows.join("")}</div>`;
}

// A page of one of the explorer's lists, newest first, and their total.
async function listPage(kind, n, extra = "") {
  return (await getJSON(`/api/explorer/list/${kind}.json?page=${n}${extra}`)) || { total: 0, entries: [] };
}

async function blockList(route) {
  const n = Math.max(route.n, 1);
  const list = await listPage("blocks", n);
  const waiting = n === 1 ? waitingBlocks() : [];
  return `<h1>L2 blocks</h1>
    ${waiting.length ? `<h2>Waiting for L1</h2>
      <p class="section-lead">Preconfirmed by the sequencer, which must post each before its deadline.</p>${waitingRows(waiting)}
      <h2>On L1</h2>` : ""}
    <p class="section-lead">Every L2 block the rollup contract accepted, newest first.</p>
    ${pager(route, list.total, "blocks")}${blockRows(list.entries)}${pager(route, list.total, "blocks")}`;
}

async function txList(route) {
  const list = await listPage("txs", Math.max(route.n, 1));
  const waiting = route.n > 1 ? [] : waitingTxs();
  return `<h1>L2 transactions</h1>
    ${waiting.length ? `<h2>Waiting for L1</h2>
      <p class="section-lead">In blocks the sequencer preconfirmed, which L1 does not have yet.</p>${l2TxRows(waiting)}
      <h2>On L1</h2>` : ""}
    <p class="section-lead">Every transaction in the L2 blocks, newest first.</p>
    ${pager(route, list.total, "txs")}${l2TxRows(list.entries)}${pager(route, list.total, "txs")}`;
}

async function l1List(route) {
  const list = await listPage("l1", Math.max(route.n, 1));
  return `<h1>Rollup transactions on L1</h1>
    <p class="section-lead">The L1 transactions that involve the rollup contract: one per L2 block, and the messages to
      L2 and the claims of messages from it.</p>
    ${pager(route, list.total, "l1")}${l1Rows(list.entries)}${pager(route, list.total, "l1")}`;
}

async function messages(route) {
  const panel = async (kind, key, lead) => {
    const page = { n: route.tab === key ? route.n : 1 };
    const list = await listPage(kind, Math.max(page.n, 1));
    return [key, kind === "deposits" ? "L1 to L2" : "L2 to L1", list.total, `<p class="section-lead">${lead}</p>
      ${pager(page, list.total, `messages/${kind}`)}${messageRows(kind, list.entries)}`];
  };
  return `<h1>Messages</h1>
    ${tabs("/messages", await Promise.all([
      panel("deposits", "", `The rollup contract adds each message to a Merkle tree. On L2, a claim proves it against the
        tree's root in an L1 block the L2 anchored, and the L2 messenger delivers it.`),
      panel("withdrawals", "withdrawals", `The L2 messenger records each message in its storage. Once its block is on L1, a claim
        proves it against the block's state root, and the rollup contract delivers it, its ETH from the escrow.`),
    ]), route.tab === "deposits" ? "" : route.tab)}`;
}

function about() {
  const c = (state.session && state.session.contracts) || {};
  return document.getElementById("about").innerHTML + (c.rollup ? `
    <h2>Check it yourself</h2>
    <table><thead><tr><th>Contract or account</th><th>Chain</th><th>Address</th><th></th></tr></thead><tbody>
      ${[
        ["Rollup contract", "l1", c.rollup, "real"], ["L2 messenger", "l2", c.l2Messenger, "real"],
        ["Proof checker", "l1", c.verifier, "mock"], ["Key registry", "l1", c.registry, "mock"],
        ["Trusted prover key", "l1", c.prover, "mock"], ["Operator", "l1", c.operator, ""],
        ...Object.entries(c.users || {}).flatMap(([name, a]) => [[name, "l1", a, ""], [name, "l2", a, ""]]),
      ].map(([name, chain, a, kind]) => `<tr><td>${name}</td><td>${chain.toUpperCase()}</td>
        <td><a class="mono" href="#/address/${chain}/${a.toLowerCase()}">${a}</a></td><td>${kind ? badge(kind) : ""}</td></tr>`).join("")}
    </tbody></table>
    <p>Rebuild the chain from L1 yourself, with the follower this explorer runs on:</p>
    <pre class="code">uv run --project &lt;execution-specs projects/zkevm + EIP-8141&gt; python contracts/script/l2_follower.py \\
    --l1-rpc &lt;L1 RPC&gt; --beacon &lt;beacon API&gt; --rollup ${c.rollup} \\
    --genesis demo/data/l2_state.json</pre>` : "");
}

// ---------------------------------------------------------------------------
// L2 block
// ---------------------------------------------------------------------------

// Terms the steppers use, defined on hover.
const TERMS = {
  witness: "The parts of the state a block reads, with proofs that they belong to the previous state root. Enough to check the block without the state.",
  blob: "Data an L1 transaction carries for about 18 days, cheaper than calldata. The rollup contract sees only its hash.",
  anchored: "Each L2 block names a recent L1 block, its anchor. L2 contracts read the anchor's hash, and prove L1 state against it.",
  bond: "ETH the sequencer locks in the rollup contract. If the rollup ever holds another block at a height the sequencer preconfirmed, anyone can have it burned.",
};
const term = (word, key = word) => `<span class="term" title="${esc(TERMS[key])}">${word}</span>`;

// What the sequencer reported about block `n`: its preconfirmation, and its
// post once on L1, which no longer changes.
async function blockRecord(n) {
  if (state.records[n]) return state.records[n];
  const r = await getJSON(`/api/blocks/${n}`);
  if (r && r.l1) state.records[n] = r;
  return r;
}
// A record's preconfirmation, with what the block held.
const preconfirmationIn = (r) => r && r.preconfirmation && { ...r.preconfirmation, transactions: r.l2.transactions, gasUsed: r.l2.gasUsed };
// When an L1 transaction was included.
const l1Time = (h) => ((state.index && state.index.l1Txs.find((t) => t.hash === h)) || {}).timestamp;
// The L1 block a block anchored to L1 block `anchor` must be posted by: the
// anchor must still be in the BLOCKHASH window.
const deadline = (anchor) => anchor + 256;
// The transactions of those blocks, newest first.
const waitingTxs = () => waitingBlocks().flatMap((p) => [...(p.txs || [])].reverse().map((t) => ({ ...t, block: p.number, time: p.time, preconfirmed: true })));
// Preconfirmed blocks the follower has not rebuilt from L1 yet, newest first.
function waitingBlocks() {
  const blocks = state.index ? state.index.l2Blocks : [];
  const rebuilt = blocks.length ? blocks[blocks.length - 1].number : 0;
  return ((state.session && state.session.waiting) || []).filter((p) => p.number > rebuilt).reverse();
}

// Where each field of a block comes from on L1, and what it is on L2. The
// proof's public input is the boundary: the rollup contract rebuilds it from
// L1, and the L2 program checks the block against it.
const SOURCE_LABELS = {
  storage: "storage", l1: "L1 opcode", checked: "calldata, proven", free: "calldata, free", fixed: "fixed", registry: "registry",
  computed: "computed",
};
// The details start in one column, after the longest label.
const SOURCE_WIDTH = 6.1 * Math.max(...Object.values(SOURCE_LABELS).map((l) => l.length)) + 10;
function boundaryView(b, rec) {
  const pi = b.proofInput, ssz = b.sszRoots, anchor = b.anchorBlockNumber, ok = pi.publicInputRoot === pi.dataHash;
  const count = (key) => b.transactions.filter((t) => t[key] !== undefined && t[key] !== null).length;
  const inBlock = (n, what) => (n ? `${n} ${what} in this block` : `none ${what} in this block`);
  const claims = inBlock(count("deposit"), "claimed"), sent = inBlock(count("withdrawal"), "sent");
  const ascii = (h) => String.fromCharCode(...(h.slice(2).match(/../g) || []).map((x) => parseInt(x, 16))).replace(/[^ -~]/g, "");
  const history = `stateRootHistory[${num(b.number % 8191)}]`;
  // [field, value, source, on L1, on L2, back to L1]; a lone string starts a
  // group, and the payload's fields nest inside the request, as in SSZ.
  const NEST = 16;
  const rows = [
    "NEW PAYLOAD REQUEST",
    ["execution_payload", short(pi.executionPayloadRoot), "computed", "the root of its 19 fields", "the block's header"],
    ["parent_hash", short(b.parentHash), "storage", "blockHash, its last block", "the parent, which BLOCKHASH reads"],
    ["fee_recipient", short(b.feeRecipient), "free", "", "COINBASE, paid the priority fees"],
    ["state_root", short(b.stateRoot), "checked", "", `stored on L1: ${history}, for messages`, true],
    ["receipts_root", short(b.receiptsRoot), "checked", "", "the receipts and their logs"],
    ["logs_bloom", "256 bytes", "checked", "", "a filter over the logs"],
    ["prev_randao", short(b.prevRandao), "free", "", "PREVRANDAO, here keccak256 of the anchor"],
    ["block_number", num(b.number), "storage", "blockNumber + 1", "NUMBER"],
    ["gas_limit", num(b.gasLimit), "fixed", "immutable l2GasLimit", "GASLIMIT"],
    ["gas_used", num(b.gasUsed), "checked", "", ""],
    ["timestamp", num(b.timestamp), "checked", "≤ L1 time, ≤ 1h behind it", "TIMESTAMP, and the anchor's key in EIP-4788"],
    ["extra_data", `"${ascii(b.extraData)}"`, "free", "", ""],
    ["base_fee_per_gas", `${num(b.baseFeePerGas)} wei`, "checked", "", "BASEFEE"],
    ["block_hash", short(b.hash), "checked", "", "stored on L1: blockHash, the next parent", true],
    ["transactions_root", short(ssz.transactionsRoot), "checked", "the transactions: in the blob", `${b.transactions.length} transactions`],
    ["withdrawals_root", "empty", "fixed", "constant", "no beacon withdrawals, so no minted ETH"],
    ["blob_gas_used", "0", "fixed", "constant", "no blob transactions on L2"],
    ["excess_blob_gas", "0", "fixed", "constant", ""],
    ["block_access_list_root", short(ssz.blockAccessListRoot), "checked", "the list: in the blob", `EIP-7928 access list, ${num(b.balBytes)} bytes`],
    ["slot_number", String(b.slotNumber), "fixed", "constant, to be defined", "SLOTNUM"],
    ["versioned_hashes", short(b.l1.blobVersionedHashes[0]), "l1", "BLOBHASH(0)", `the blob: ${num(b.payloadBytes)} bytes of block data`],
    ["parent_beacon_block_root", short(b.parentBeaconBlockRoot), "l1", `BLOCKHASH(${num(anchor)}), the anchor`, "kept by EIP-4788: see messages, below"],
    ["execution_requests", short(ssz.executionRequestsRoot), "checked", "any accepted", "no effect on L2"],
    "EIP-8025 PUBLIC INPUT, AS FOR L1'S OWN PROOFS",
    ["new_payload_request_root", short(pi.newPayloadRequestRoot), "computed", "the root of the request above", "the block the program validates"],
    ["successful_validation", "true", "fixed", "only valid blocks", "the program's verdict on it"],
    ["chain_id", String(pi.chainId), "fixed", "immutable chainId", "CHAINID"],
    ["schema_id", `0x${pi.schemaId.toString(16)}`, "registry", "its current entry", "the input format, so the fork's rules"],
  ];
  const W = 1124, L = [0, 336], M = [372, 752], R = [788, 1124], RH = 21, GH = 22, TOP = 50;
  const parts = [];
  const t = (x, y, cls, content, anchorAt = "start") => `<text class="${cls}" x="${x}" y="${y}" text-anchor="${anchorAt}">${esc(content)}</text>`;
  const arrow = (x1, y1, x2, y2) => `<path class="bd-arrow" d="M${x1},${y1} L${x2},${y2}" marker-end="url(#bd-head)"/>`;
  let y = TOP;
  const rowY = [];
  const payload = new Set(["parent_hash", "fee_recipient", "state_root", "receipts_root", "logs_bloom", "prev_randao", "block_number",
    "gas_limit", "gas_used", "timestamp", "extra_data", "base_fee_per_gas", "block_hash", "transactions_root", "withdrawals_root",
    "blob_gas_used", "excess_blob_gas", "block_access_list_root", "slot_number"]);
  let nestTop = null, nestBottom = null;
  for (const row of rows) {
    if (typeof row === "string") {
      parts.push(t(M[0] + 12, y + 15, "bd-group", row));
      y += GH;
      continue;
    }
    const [field, value, source, onL1, onL2, back] = row;
    const mid = y + RH / 2;
    const indent = payload.has(field) ? NEST : 0;
    if (indent && nestTop === null) nestTop = y;
    if (indent) nestBottom = y + RH;
    rowY.push([field, mid]);
    const label = SOURCE_LABELS[source];
    parts.push(`<g class="bd-row"><title>${esc(`${field}: ${value}`)}</title>
      <rect class="bd-hit" x="0" y="${y}" width="${W}" height="${RH}"/>
      ${t(L[0] + 4, mid + 4, `bd-source ${source}`, label)}
      ${onL1 ? t(L[0] + 4 + SOURCE_WIDTH, mid + 4, "bd-detail", onL1) : ""}
      ${arrow(L[1] + 2, mid, M[0] - 3, mid)}
      ${t(M[0] + 12 + indent, mid + 4, "bd-field", field)}${t(M[1] - 12 - indent, mid + 4, "bd-value", value, "end")}
      ${onL2 ? arrow(M[1] + 3, mid, R[0] - 3, mid) + t(R[0] + 6, mid + 4, back ? "bd-back" : "bd-l2", onL2) : ""}</g>`);
    y += RH;
  }
  const rowsEnd = y;
  // The check: the root of all of the above against the proof's.
  y += 12;
  parts.push(`<g class="bd-check ${ok ? "ok" : "bad"}"><rect x="${M[0]}" y="${y}" width="${M[1] - M[0]}" height="48" rx="8"/>
    ${t((M[0] + M[1]) / 2, y + 19, "bd-field", `hash_tree_root(PublicInput) ${short(pi.publicInputRoot)}`, "middle")}
    ${t((M[0] + M[1]) / 2, y + 36, ok ? "bd-ok" : "bd-bad", ok ? "= the proof frame's data_hash ✓" : "≠ the proof frame's data_hash", "middle")}</g>
    ${arrow(L[1] + 2, y + 24, M[0] - 3, y + 24)}
    ${t(L[1] - 8, y + 19, "bd-detail", "EIP-8288 proof frame: data_hash,", "end")}${t(L[1] - 8, y + 35, "bd-detail", `key ${short(pi.verificationKeyHash)}, the registry's ✓`, "end")}
    ${t(R[0] + 6, y + 19, "bd-l2", "the commitment L1's own execution proofs")}${t(R[0] + 6, y + 35, "bd-l2", "use; here a mock signs it, not a zkVM")}`);
  y += 48 + 28;
  // The messages, which cross the boundary through two of the fields.
  const box = (x0, x1, top, lines, cls = "") => `<g class="bd-box ${cls}"><rect x="${x0}" y="${top}" width="${x1 - x0}" height="${16 * lines.length + 14}" rx="8"/>
    ${lines.map((line, i) => t(x0 + 10, top + 20 + 16 * i, i ? "bd-l2" : "bd-title", line)).join("")}</g>`;
  const lane = (title, top, cells, rightwards) => {
    const h = 16 * 3 + 14, mid = top + h / 2;
    return t(L[0], top - 8, "bd-group", title)
      + box(L[0], L[1], top, cells[0]) + box(M[0], M[1], top, cells[1], "boundary") + box(R[0], R[1], top, cells[2])
      + (rightwards ? arrow(L[1] + 2, mid, M[0] - 3, mid) + arrow(M[1] + 3, mid, R[0] - 3, mid)
        : arrow(R[0] - 2, mid, M[1] + 3, mid) + arrow(M[0] - 2, mid, L[1] + 3, mid));
  };
  parts.push(lane("MESSAGES, L1 TO L2", y, [
    ["The rollup contract", "adds each message to a Merkle tree,", "its root in storage slot 3"],
    [`parent_beacon_block_root = L1 block ${num(anchor)}`, "a block hash commits to that block's state,", "and so to the root in slot 3"],
    [`EIP-4788 keeps it under ${num(b.timestamp)}`, "the messenger proves slot 3 against it, and", `each claim against that root: ${claims}`],
  ], true));
  y += 62 + 34;
  parts.push(lane("MESSAGES, L2 TO L1", y, [
    [history, "keeps this state root, and a claim", "proves its message against it"],
    ["state_root", "commits to the messenger's storage,", "so to every message sent so far"],
    ["The L2 messenger", "records each message in its storage,", `slot 2: ${sent}`],
  ], false));
  y += 62 + 8;
  const head = `${t(L[0], 16, "bd-title", "L1: where the rollup contract gets it")}${t(L[0], 33, "bd-sub", `advance, in L1 block ${num(b.l1.block)}`)}
    ${t((M[0] + M[1]) / 2, 16, "bd-title", "The boundary: the proof's public input", "middle")}${t((M[0] + M[1]) / 2, 33, "bd-sub", "EIP-8025's, exactly as for L1 blocks", "middle")}
    ${t(R[0] + 6, 16, "bd-title", `L2: what it is in block ${num(b.number)}`)}${t(R[0] + 6, 33, "bd-sub", "inside the L2 program's execution")}`;
  return `<div class="boundary-wrap"><svg class="boundary" viewBox="0 0 ${W} ${y}" role="img" aria-label="Where each field of L2 block ${b.number} comes from on L1 and what it is on L2">
    <defs><marker id="bd-head" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path class="bd-headpath" d="M0,0 L6,3 L0,6 z"/></marker></defs>
    <rect class="bd-band" x="${M[0]}" y="${TOP - 6}" width="${M[1] - M[0]}" height="${rowsEnd - TOP + 12}" rx="10"/>
    <rect class="bd-nest" x="${M[0] + NEST - 4}" y="${nestTop - 2}" width="${M[1] - M[0] - 2 * NEST + 8}" height="${nestBottom - nestTop + 4}" rx="7"/>
    ${head}${parts.join("")}</svg></div>`;
}

// The steps every L2 block takes to L1, each linked to what shows it.
function journey(b, rec) {
  const matches = b.hash === b.recordedHash;
  const pre = preconfirmationIn(rec);
  const posted = (rec && rec.l1 && rec.l1.timestamp) || l1Time(b.l1.tx);
  const steps = [
    ["Build", "real", "The operator's node, holding the L2 state, built the block.",
      `<a href="#/l2/block/${b.number}/txs">${num(b.transactions.length)} transactions</a>`],
    ["Preconfirm", "real", `The sequencer signed its hash at once, backed by a ${term("bond")}.`,
      pre ? `<a href="#/l2/block/${b.number}/l1">signature</a> · ${pre.blockHash === b.recordedHash ? `kept <span class="check">✓</span>` : `<span class="bad">broken</span>`}` : ""],
    // The stateless check is real; only the proof of it is a stand-in.
    ["Prove", "mixed", `Ethereum's stateless program checked it, given a ${term("witness")}.`,
      `<a href="#/l2/block/${b.number}/l1">${rec && rec.l2.validation.successful ? `accepted <span class="check">✓</span>` : "the run"}</a> ·
      <a href="#/l1/tx/${b.l1.tx}/frames" title="A trusted key signs the result in place of a zk proof">proof</a> ${badge("mock")}`],
    ["Post", "real", `One L1 transaction carried the block's data in a ${term("blob")}${pre && posted ? `, ${secs(posted - pre.time)} later` : ""}.`,
      `<a href="#/l1/tx/${b.l1.tx}">the transaction</a> · <a href="#/blob/${b.number}">its blob</a>`],
    ["Verify", "real", "The rollup contract checked that the proof is for exactly this block.",
      `<a href="#/l1/tx/${b.l1.tx}">the check</a>`],
    ["Follow", "real", "An independent node rebuilt the block from L1 data alone.",
      matches ? `<a href="#/about">same hash <span class="check">✓</span></a>` : `<span class="bad">different hash</span>`],
  ];
  return stepper(steps);
}

// Steps on a line of numbered dots: [title, kind, text, evidence], where kind
// is real, mock, mixed, or pending for a step not reached yet.
function stepper(steps) {
  return `<ol class="journey" style="--steps: ${steps.length}">${steps.map(([title, kind, text, link], i) =>
    `<li class="${kind}"><span class="step">${i + 1}</span><div><b>${title}</b></div><p>${text}</p><div class="evidence">${link}</div></li>`).join("")}</ol>`;
}

const msgLink = (kind, i) => `<a href="#/${kind}/${i}">message #${i}</a>`;

// A deposit or withdrawal the index carries or this render fetched, by index.
const cachedMessage = (kind, i) =>
  i === undefined || i === null ? null : (state.index && state.index[kind][String(i)]) || state.messages[kind][String(i)] || null;
// Fetches the messages of `txs` that the index does not carry, so that
// depositOf and withdrawalOf find them.
async function loadMessages(txs) {
  const missing = { deposits: [], withdrawals: [] };
  for (const t of txs) {
    const claim = (t.frames || []).find((f) => f.call && f.call.function === "claimL1Message");
    const deposit = t.deposit ?? (claim ? claim.call.args.m.index : null);
    if (deposit !== null && !cachedMessage("deposits", deposit)) missing.deposits.push(deposit);
    if (t.withdrawal !== undefined && t.withdrawal !== null && !cachedMessage("withdrawals", t.withdrawal)) missing.withdrawals.push(t.withdrawal);
  }
  if (!missing.deposits.length && !missing.withdrawals.length) return;
  const found = await getJSON(`/api/explorer/messages.json?deposits=${missing.deposits.join(",")}&withdrawals=${missing.withdrawals.join(",")}`);
  if (found) for (const kind of ["deposits", "withdrawals"]) Object.assign(state.messages[kind], found[kind]);
}

// A message's journey between the chains, with what it carries.
// The keccak256 of slot 2, where the L2 messenger's queue of sent messages, a
// Solidity array, keeps its entries: entry i is at this slot plus i.
const QUEUE_BASE = 0x405787fa12a823e0f2b7631cc41b3ba8828b3321ca811111fa75cd3aa3bb5acen;

// A message's way between the chains, with its values: how it is sent and
// committed on one chain, how the other learns the commitment, and how a
// claim unpacks and delivers it. Steps not taken yet are dashed.
function messageFlow({ m, deposit, sent, claim, anchoring, posted }) {
  const [from, to] = deposit ? ["l1", "l2"] : ["l2", "l1"];
  const [FROM, TO] = [from.toUpperCase(), to.toUpperCase()];
  const W = 1124, S = [0, 470], X = [327, 797], D = [654, 1124], GAP = 34, LINE = 17;
  const name = (a) => (a ? (labels()[a.toLowerCase()] || [short(a)])[0] : "");
  // A call is named by its function, or by its selector if the explorer does
  // not know it; data too short for a selector is just data.
  const method = !hasData(m.data) ? "" : m.call ? m.method : m.data.length >= 10 ? `the function ${m.data.slice(0, 10)}` : "";
  const call = !hasData(m.data) ? "" : method ? `a call to ${method}` : `a call with ${(m.data.length - 2) / 2} bytes of data`;
  const value = BigInt(m.value || 0) > 0n;
  const what = call ? `${value ? `${eth(m.value)} and ` : ""}${call}` : eth(m.value);
  const fee = BigInt(m.fee || 0) > 0n ? `with a fee of ${eth(m.fee)} for whoever claims it` : "with no fee, for its recipient to claim";
  // The claim's call, which a frame of a frame transaction may make.
  const calls = claim ? (claim.frames ? claim.frames.map((f) => f.call) : [claim.call]).filter(Boolean) : [];
  const claimCall = calls.find((c) => c.function === (deposit ? "claimL1Message" : "claimL2Message"));
  const feeRecipient = claimCall && claimCall.args.feeRecipient;
  const delivered = claim && [
    `the ${deposit ? "L2 messenger" : "rollup contract"} ${call ? `makes ${call} on ${name(m.to)}${value ? ` with ${eth(m.value)}` : ""}` : `sends ${eth(m.value)} to ${name(m.to)}`},`,
    `answering ${deposit ? "l1Sender()" : "l2Sender()"} with ${name(m.from)} meanwhile${BigInt(m.fee || 0) > 0n ? ", and pays" : "."}`,
    BigInt(m.fee || 0) > 0n ? `the fee of ${eth(m.fee)} to ${name(feeRecipient || claim.from)}` : "The message carries no fee",
    { text: `${TO} tx ${short(claim.hash)}`, href: `#/${to}/tx/${claim.hash}` },
  ];
  // Who made the message's sender send it: an account's call to it, or a
  // message from the other chain it received.
  const delivering = sent && logsOf(sent).map((l) => l.event).find((e) => e && /MessageClaimed$/.test(e.name) && e.args.to.toLowerCase() === m.from.toLowerCase());
  const via = !sent || sent.from.toLowerCase() === m.from.toLowerCase() ? null
    : delivering ? `as it receives message #${num(delivering.args.index)} from ${TO}` : `as ${name(sent.from)} calls ${methodName(sent) || "it"} on it`;
  const sentLines = [
    `${name(m.from)} sends ${what}`,
    `to ${name(m.to)} on ${TO}, ${fee}`,
    ...(via ? [via] : []),
    deposit ? { text: `L1 tx ${short(m.l1Tx)}, L1 block ${num(m.l1Block)}`, href: `#/l1/tx/${m.l1Tx}` }
      : { text: `L2 tx ${short(m.l2Tx)}, L2 block #${num(m.l2Block)}`, href: `#/l2/tx/${m.l2Tx}` },
  ];
  let steps;
  if (deposit) {
    const prove = calls.find((c) => c.function === "proveL1MessageRoot");
    const path = claimCall && claimCall.pathToRoot;
    steps = [
      ["S", "Sent on L1", sentLines],
      ["S", "Committed", [
        `keccak256 of its fields: ${short(m.hash)}, leaf ${num(m.index)}`,
        "of the rollup contract's Merkle tree of messages,",
        "whose root it keeps in storage slot 3",
      ]],
      ["X", "Anchored", anchoring && [
        `L2 block #${num(anchoring.number)} anchors L1 block ${num(anchoring.anchor)}: its parent_beacon_block_root`,
        "is that block's hash, which commits to L1's state and so to",
        "slot 3. EIP-4788 keeps it in a contract on L2",
        { text: `L2 block #${num(anchoring.number)}`, href: `#/l2/block/${anchoring.number}` },
      ], `An L2 block that anchors L1 block ${num(m.l1Block)} or later`],
      ["D", "Root proven on L2", path && (prove ? [
        `the claim proves L1 block ${num(anchoring ? anchoring.anchor : m.l1Block)}'s header against the anchor,`,
        "then the rollup contract's account and slot 3 in its state:",
        `the root ${short(path.root)}`,
        { text: "the proofs", href: `#/l2/tx/${claim.hash}/frames@proveL1MessageRoot.accountProof` },
      ] : [
        `the L2 messenger had proven the root ${short(path.root)}`,
        "for an earlier claim, against the anchor of a recent",
        "L2 block, and keeps it for the claims that follow",
      ]), "A claim proves slot 3 against the anchor, once per root"],
      ["D", "Unpacked", path && [
        `claimL1Message hashes the fields into the leaf ${short(path.leaf)},`,
        `then up ${path.levels.length} levels with the claim's siblings: the root ✓,`,
        `and marks message #${num(m.index)} claimed`,
        { text: "the path", href: `#/l2/tx/${claim.hash}/frames@claimL1Message.path` },
      ], "Its recipient claims it for free, or anyone for its fee"],
      ["D", "Delivered", delivered, ""],
    ];
  } else {
    const slot = `0x${(QUEUE_BASE + BigInt(m.index)).toString(16).padStart(64, "0")}`;
    const n = m.l2Block;
    steps = [
      ["S", "Sent on L2", sentLines],
      ["S", "Committed", [
        `keccak256 of its fields: ${short(m.hash)}, entry ${num(m.index)}`,
        "of the L2 messenger's queue of sent messages,",
        `in its storage slot ${short(slot)}`,
      ]],
      ["X", "Posted", posted && [
        `L2 block #${num(n)}'s state root commits to that slot. The block reached`,
        "L1 with its proof, and the rollup contract keeps the root",
        `in stateRootHistory[${num(n % 8191)}]`,
        { text: `L1 tx ${short(posted)}`, href: `#/l1/tx/${posted}` },
      ], `L2 block #${num(n)} reaching L1`],
      ["D", "Proven on L1", claimCall && [
        `claimL2Message proves the L2 messenger's account and slot ${short(slot)}`,
        `against the state root of L2 block #${num(claimCall.args.l2BlockNumber)}: the entry is the hash ✓,`,
        `and marks message #${num(m.index)} claimed`,
        { text: "the proofs", href: `#/l1/tx/${claim.hash}@claimL2Message.accountProof` },
      ], "Its recipient claims it for free, or anyone for its fee"],
      ["D", "Delivered", delivered, ""],
    ];
  }
  const cols = { S, X, D };
  const t = (x, y, cls, content) => `<text class="${cls}" x="${x}" y="${y}">${esc(content)}</text>`;
  const parts = [];
  let y = 46, prev = null;
  steps.forEach(([col, title, lines, waiting], i) => {
    const [x0, x1] = cols[col];
    const done = !!lines;
    const body = done ? lines : [waiting].filter(Boolean);
    const h = LINE * body.length + 34;
    if (prev) {
      // Down from the last box, across to this one's column if it moves.
      const [px, py] = prev, cx = (x0 + x1) / 2, mid = (py + y) / 2;
      parts.push(`<path class="bd-arrow" d="M${px},${py + 2} L${px},${mid} L${cx},${mid} L${cx},${y - 3}" marker-end="url(#mf-head)"/>`);
    }
    parts.push(`<g class="bd-box ${col === "X" ? "boundary" : ""} ${done ? "" : "pending"}"><rect x="${x0}" y="${y}" width="${x1 - x0}" height="${h}" rx="8"/>
      ${t(x0 + 12, y + 21, "bd-title", `${i + 1}  ${title}`)}
      ${body.map((line, j) => (typeof line === "string" ? t(x0 + 12, y + 40 + LINE * j, "bd-l2", line)
        : `<a href="${line.href}">${t(x0 + 12, y + 40 + LINE * j, "bd-link", line.text)}</a>`)).join("")}</g>`);
    prev = [(x0 + x1) / 2, y + h];
    y += h + GAP;
  });
  const head = `${t(S[0], 16, "bd-title", `${FROM}: where it is sent`)}${t(D[1], 16, "bd-title mf-end", `${TO}: where it is delivered`)}`;
  return `<div class="boundary-wrap"><svg class="boundary flow" viewBox="0 0 ${W} ${y - GAP + 4}" role="img" aria-label="How message #${m.index} goes from ${FROM} to ${TO}">
    <defs><marker id="mf-head" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path class="bd-headpath" d="M0,0 L6,3 L0,6 z"/></marker></defs>
    ${head}${parts.join("")}</svg></div>`;
}

async function messagePage(route) {
  const deposit = route.kind === "deposit";
  const kind = deposit ? "deposits" : "withdrawals";
  if (!cachedMessage(kind, route.n)) await loadMessages([deposit ? { deposit: route.n } : { withdrawal: route.n }]);
  const m = cachedMessage(kind, route.n);
  const title = `Message #${route.n} <span class="route">${net(deposit ? "l1" : "l2")}<span class="route-arrow">→</span>${net(deposit ? "l2" : "l1")}</span>`;
  if (!m) return `<h1>${title}</h1><p class="note">Not found.</p>`;
  const [sent, claim] = await Promise.all([
    object(deposit ? `l1/txs/${m.l1Tx}` : `l2/txs/${m.l2Tx}`),
    deposit ? m.l2Tx && object(`l2/txs/${m.l2Tx}`) : m.l1Tx && object(`l1/txs/${m.l1Tx}`),
  ]);
  const [from, to] = deposit ? ["l1", "l2"] : ["l2", "l1"];
  const anchoring = deposit ? await getJSON(`/api/explorer/anchoring/${m.l1Block}.json`) : null;
  const block = !deposit && m.l2Block && state.rollupHead >= m.l2Block ? await object(`l2/blocks/${m.l2Block}`) : null;
  const data = m.data || (sent && sent.call && sent.call.args.data);
  // When it was sent and claimed: an L1 transaction's time, or its L2 block's.
  const blockTime = async (n) => n && ((state.index.l2Blocks.find((x) => x.number === n) || (await object(`l2/blocks/${n}`)) || {}).timestamp);
  const [sentAt, claimedAt] = await Promise.all([
    deposit ? sent && sent.timestamp : blockTime(m.l2Block),
    !claim ? null : deposit ? blockTime(m.l2Block) : claim.timestamp,
  ]);
  const when = (t) => `${ago(t)} <span class="muted">(${new Date(t * 1000).toLocaleString()})</span>`;
  return `<h1>${title}</h1>
    ${messageFlow({ m, deposit, sent, claim, anchoring, posted: block && block.l1.tx })}
    ${fields([
      ...(sentAt ? [["Sent", when(sentAt), deposit ? "Its L1 transaction's time." : "Its L2 block's time."]] : []),
      ["Claimed", claimedAt ? `${when(claimedAt)}${sentAt ? `, ${secs(claimedAt - sentAt)} after it was sent` : ""}` : '<span class="muted">not yet</span>',
        claimedAt ? (deposit ? "Its claim's L2 block's time." : "Its claim's L1 transaction's time.") : ""],
      ["Value", m.value !== undefined ? eth(m.value) : "", ""],
      ["Fee", m.fee ? eth(m.fee) : "none", "For whoever claims it. Its recipient claims it for free."],
      ...(Number(m.gasLimit) ? [["Gas limit", num(m.gasLimit), "The gas its call gets, whoever claims it."]] : []),
      ["From", addr(m.from, from), ""],
      ["To", addr(m.to, to), ""],
    ])}
    ${hasData(data) ? `<h2>Call</h2><p class="section-lead">What the claim calls on the recipient.</p>${inputData(data, m.call, { chain: to, fn: m.call && m.call.function })}` : ""}`;
}

async function blockPage(route) {
  const [b, rec] = await Promise.all([object(`l2/blocks/${route.n}`), blockRecord(route.n)]);
  if (!b) {
    const p = preconfirmationIn(rec);
    return p ? waitingPage(p) : `<h1>Block #${route.n}</h1><p class="note">The follower has not rebuilt this block yet.</p>`;
  }
  const kinds = {};
  b.transactions.forEach((t) => (kinds[t.kind] = (kinds[t.kind] || 0) + 1));
  const matches = b.hash === b.recordedHash;
  const pre = preconfirmationIn(rec);
  const posted = (rec && rec.l1 && rec.l1.timestamp) || l1Time(b.l1.tx);
  const last = state.index.totals.blocks;
  await loadMessages(b.transactions);
  const base = `/l2/block/${b.number}`;
  const tab = route.tab;
  const txsPanel = `<table><thead><tr><th>#</th><th>Transaction hash</th><th>What it does</th><th>Method</th><th>Status</th><th>Linked on L1</th><th>From</th><th class="num">Gas used</th><th class="num">Fee</th></tr></thead><tbody>
    ${b.transactions.map((t, i) => `<tr><td>${i}</td><td>${l2TxLink(t.hash)}</td><td>${kindText(t.kind)}</td><td>${method(t)}</td><td>${statusText(t.status)}</td><td>${counterpart(t)}</td><td>${addr(t.from, "l2")}</td>
      <td class="num">${num(t.gasUsed)}</td><td class="num">${t.fee !== undefined && t.fee !== null ? eth(t.fee) : ""}</td></tr>`).join("")}</tbody></table>`;
  const l1Panel = `
      <h2>Data on L1</h2>
      ${fields([
        ["L1 transaction", `${l1TxLink(b.l1.tx)} <span class="muted">in L1 block ${num(b.l1.block)}</span>`, "One frame transaction per L2 block: it carries the blob, the proof, and the call to the rollup contract."],
        ["Blob", hash(b.l1.blobVersionedHashes[0], true), "Its versioned hash. The contract reads it with BLOBHASH and binds it into the proof's input."],
        ["Blob use", `${num(b.payloadBytes)} of ${num(BLOB_USABLE_BYTES)} bytes<div class="fill"><span style="width:${Math.max(0.4, (100 * b.payloadBytes) / BLOB_USABLE_BYTES)}%"></span></div>`, "EIP-8142 gives every block its own blobs, however small the block."],
      ])}
      <p><a class="button" href="#/blob/${b.number}">View the blob: decoded and raw →</a></p>
      ${b.proofInput ? proofSection(b, rec) : ""}
      ${pre ? `<h2>Preconfirmation</h2>
      ${preconfirmationFields(pre)}` : ""}
      ${rec && rec.broken ? `<h2>Broken preconfirmations</h2>
      <div class="callout"><p>The sequencer also signed ${rec.broken.length > 1 ? "other blocks" : "another block"} at this height,
        which never reached L1 in time and which its node dropped. Each signature, with the block the rollup holds here, proves a
        divergence, for which anyone can have the sequencer's bond burned.</p></div>
      ${rec.broken.map(({ preconfirmation: p }) => fields([
        ["Signed", `block ${num(p.number)}, hash ${hash(p.blockHash)}, anchor L1 block ${num(p.anchorBlockNumber)}`, `${ago(p.time)}.`],
        ["Signature", hash(p.signature), ""],
      ])).join("")}` : ""}
      ${rec ? `<h2>On the operator's side</h2>
      <div class="callout"><p>What the operator's node did before posting the block. None of it is on L1, and the follower's
        rebuild does not rely on it.</p></div>
      ${fields([
        ["Stateless program", `<code>verify_stateless_new_payload</code> ${badge("real")}`, "Ethereum's stateless validation program from execution-specs, with EIP-8141, run as ordinary code instead of inside a zkVM. It gets the block and a witness of the state it reads, not the state itself."],
        ["Result", `successful_validation = ${rec.l2.validation.successful}`, "The prover only signs blocks the program accepted."],
        ["Chain ID, schema ID", `${rec.l2.validation.chainId}, 0x${rec.l2.validation.schemaId.toString(16)}`, "Part of what the proof commits to, so a block cannot be proven under another chain's rules."],
        ["public_input_root", hash(rec.l2.publicInputRoot, true), "What the proof commits to. It is the data hash in the L1 transaction's proof frame."],
        ["Blob check", `node-side ${badge("mock")}`, "The node checked that the blob encodes the block, which the L1 program does not do yet (EIP-8142)."],
      ])}` : ""}`;
  const overview = `${fields([
      ["Block height", `${b.number} <span class="nowrap">${b.number > 1 ? `<a href="#/l2/block/${b.number - 1}">‹</a>` : ""} ${b.number < last ? `<a href="#/l2/block/${b.number + 1}">›</a>` : ""}</span>`,
        "From the rollup contract's storage: one more than the last block it accepted."],
      ["Status", matches ? `<span class="check">on L1, rebuilt from L1 with the same hash ✓</span>` : `<span class="bad">rebuilt with a different hash</span>`,
        "An independent follower rebuilt the block from L1 data alone and compared its hash with the one the rollup contract recorded."],
      ...(pre ? [
        ["Preconfirmed", `${ago(pre.time)}${posted ? ` <span class="muted">· ${secs(posted - pre.time)} before it reached L1</span>` : ""}`,
          "The sequencer signed the block's number, hash and L1 anchor as soon as it built it, so users could rely on it at once. Posting waits for the proof."],
        ["Posting deadline", `L1 block ${num(deadline(pre.anchorBlockNumber))} <span class="muted">· met by ${num(deadline(pre.anchorBlockNumber) - b.l1.block)} blocks</span>`,
          "The block's anchor must still be in the BLOCKHASH window, 256 L1 blocks, when the block is posted. A preconfirmed block that misses it can never be posted, which costs the sequencer its bond."],
      ] : []),
      ["Timestamp", `${ago(b.timestamp)} <span class="muted">(${new Date(b.timestamp * 1000).toLocaleString()})</span>`,
        "Chosen by the operator. It must increase, and the contract keeps it at or below L1 time: a block at the maximum timestamp would halt the chain."],
      ["Posted on L1", `${l1TxLink(b.l1.tx)} <span class="muted">in L1 block ${num(b.l1.block)}</span>`, "Its blob carries the block's transactions."],
      ["Transactions", `<a href="#${base}/txs">${num(b.transactions.length)} transactions</a>${b.transactions.length ? `: ${contents(kinds)}` : ""}`, ""],
      ["Fee recipient", addr(b.feeRecipient, "l2"), "A free input of the operator, set by the consensus layer on L1."],
      ["Size in blob", `${num(b.payloadBytes)} bytes <span class="muted">(${pct(b.payloadBytes / BLOB_USABLE_BYTES)} of a blob)</span>`, "Its transactions and access list, in EIP-8142's encoding."],
      ["Gas used", `${num(b.gasUsed)} <span class="muted">(${pct(b.gasUsed / b.gasLimit)})</span>`, ""],
      ["Gas limit", num(b.gasLimit), "Fixed when the rollup contract is deployed."],
      ["Base fee per gas", perGas(b.baseFeePerGas), "EIP-1559 applies on L2 as on L1."],
      ["Burnt fees", eth((BigInt(b.baseFeePerGas) * BigInt(b.gasUsed)).toString()), "The base fee times the gas used, burned on L2. Its ETH stays in the L1 escrow."],
      ["Extra data", `${hash(b.extraData)} <span class="muted">"${esc(hexToText(b.extraData))}"</span>`, "A free input of the operator."],
    ])}
    ${more([
      ["Hash", hash(b.hash, true), "Rebuilt by the follower. The rollup contract recorded " + (matches ? "the same hash." : "a different one.")],
      ["Parent hash", hash(b.parentHash, true), "From the contract's storage, so a block can only extend the chain the contract has."],
      ["State root", hash(b.stateRoot, true), "Claimed by the operator, checked by the proof. The contract keeps the last 8,191 state roots, for withdrawals."],
      ["Receipts root", hash(b.receiptsRoot, true), "Claimed by the operator, checked by the proof."],
      ["Transactions root", hash(b.transactionsRoot, true), `The header's trie root. The contract gets the transactions' SSZ root, ${hash(b.sszRoots.transactionsRoot)}, and the transactions are in the blob.`],
      ["L1 anchor", `${hash(b.parentBeaconBlockRoot, true)}<br><span class="muted">L1 block ${num(b.anchorBlockNumber)}</span>`, "The parent_beacon_block_root field, repurposed: the hash of an L1 block the operator picked, which the contract checks with BLOCKHASH. L2 contracts read it through the EIP-4788 contract and prove L1 state against it, as deposit claims do."],
      ["prev_randao", hash(b.prevRandao, true), "A free input of the operator, so not randomness on L2. The demo uses the hash of the anchor."],
      ["Withdrawals root", hash(b.withdrawalsRoot, true), "Always empty: the L2 has no beacon chain, and fake withdrawals would mint ETH."],
      ["Blob gas used, excess", `${b.blobGasUsed}, ${b.excessBlobGas}`, "Always 0: the L2 has no blob transactions."],
      ["Requests hash", hash(b.requestsHash, true), "EIP-7685 requests. L2 users can create them, but they do nothing on L2, so the contract accepts any."],
      ["Access list hash", hash(b.blockAccessListHash, true), `EIP-7928's block access list. Its ${num(b.balBytes)} bytes travel in the blob, and the contract gets its SSZ root.`],
      ["Slot number", b.slotNumber, "Fixed to 0 for now. The L2 value is not defined yet."],
    ])}`;
  return `
    <h1>Block #${b.number} <span class="net l2">L2</span></h1>
    ${journey(b, rec)}
    ${tabs(base, [["", "Overview", undefined, overview], ["txs", "Transactions", b.transactions.length, txsPanel], ["l1", "On L1", undefined, l1Panel]], tab)}`;
}

// What the sequencer signed, and what holds it to it.
function preconfirmationFields(p) {
  const c = (state.session && state.session.contracts) || {};
  return fields([
    ["Signed", `block ${num(p.number)}, hash ${hash(p.blockHash)}, anchor L1 block ${num(p.anchorBlockNumber)}`,
      "The block's number, hash and L1 anchor, for this rollup contract. The hash fixes everything the contract checks when the block is posted."],
    ["Signature", hash(p.signature), "By the sequencer's key. Anyone can show the rollup contract two signatures for one height, or one for a block the rollup does not hold at that height, and have the sequencer's bond burned."],
    ["Sequencer", addr(c.operator, "l1"), "The only account the rollup contract takes blocks from, until it posts nothing for two hours."],
    ...(state.bond ? [["Bond", eth(state.bond), "What the sequencer loses if it breaks a preconfirmation. It caps what the preconfirmations can be trusted with."]] : []),
  ]);
}

// A block the sequencer preconfirmed, before L1 has it.
function waitingPage(p) {
  return `<h1>Block #${p.number} <span class="net l2">L2</span></h1>
    ${stepper([
      ["Build", "real", "The operator's node, holding the L2 state, built the block.", `${num(p.transactions)} transactions`],
      ["Preconfirm", "real", `The sequencer signed its hash at once, backed by a ${term("bond")}.`, `${ago(p.time)}`],
      ["Prove", "pending", "Posting waits for the proof. Here, a fixed wait stands in for proving time.", ""],
      ["Post", "pending", `Must reach L1 by L1 block ${num(deadline(p.anchorBlockNumber))}, while its anchor is in the BLOCKHASH window.`, ""],
      ["Verify", "pending", "The rollup contract will check that the proof is for exactly this block.", ""],
      ["Follow", "pending", "An independent node will rebuild it from L1 data alone.", ""],
    ])}
    <div class="callout"><p>The follower only shows blocks it rebuilt from L1, so this block's transactions appear here once it is
      posted. Until then, the sequencer's signature is what users rely on.</p></div>
    ${fields([
      ["Preconfirmed", `${ago(p.time)} <span class="muted">(${new Date(p.time * 1000).toLocaleString()})</span>`, ""],
      ...(state.l1Head != null ? [["Posting deadline", `L1 block ${num(deadline(p.anchorBlockNumber))} <span class="muted">· ${num(deadline(p.anchorBlockNumber) - state.l1Head)} blocks left, about ${secs(12 * (deadline(p.anchorBlockNumber) - state.l1Head))}</span>`,
        "Its anchor must still be in the BLOCKHASH window, 256 L1 blocks, when it is posted."]] : []),
      ["Transactions", num(p.transactions), ""],
      ["Gas used", num(p.gasUsed), ""],
    ])}
    <h2>Preconfirmation</h2>
    ${preconfirmationFields(p)}`;
}

function hexToText(hex) {
  const bytes = hex.slice(2).match(/../g) || [];
  return bytes.map((b) => parseInt(b, 16)).map((c) => (c >= 32 && c < 127 ? String.fromCharCode(c) : ".")).join("");
}

// ---------------------------------------------------------------------------
// Addresses
// ---------------------------------------------------------------------------

const USER_ROLE = `One of the demo's three users. Each uses the same key on L1 and L2, so has the same address on both. Alice and Bob
  deposit from L1, the three pay each other on L2, and any of them withdraws to L1, Charlie with ETH received only on L2.
  Alice and Bob also bridge DEMO tokens to L2 and back.`;
const ROLES = {
  "Rollup contract": ["real", "The native rollup's contract on L1. It adds each L2 block whose proof is for exactly that block, stores the L2 chain's head and its last 8,191 state roots, keeps the tree of L1 to L2 messages, holds the ETH deposits escrow, and pays withdrawals. With the preconfirmations customization, it takes blocks only from its sequencer, and holds the sequencer's bond."],
  "L2 messenger": ["real", "An L2 contract in the genesis that holds the pre-minted supply of L2 ETH. It releases ETH for deposits, which it proves against L1's message tree, and records withdrawals for L1. Its balance is the ETH that deposits have not released yet."],
  "Proof checker": ["mock", "Stands in for EIP-8288. A frame of each L1 transaction that adds a block calls it, and it checks that the trusted prover signed the dependency. With EIP-8288, Ethereum's own proof would cover the dependency instead."],
  "Key registry": ["mock", "Stands in for the EIP-8357 registry of EVM verification keys. An admin registered the key, where a fork would."],
  "Trusted prover key": ["mock", "The key that signs the blocks Ethereum's validation program accepted, in place of a zk proof. It never sends transactions."],
  "Operator": ["", "The rollup's sequencer, the only account that posts L2 blocks to L1. Its L2 node builds them from the transactions users send, and holds no user keys. It preconfirms each block when it builds it, against a bond in the rollup contract, and posts it later."],
  "Frames helper": ["", "Lets the rollup contract use EIP-8141's FRAMEPARAM and FRAMEDATACOPY instructions, which Solidity cannot emit yet. The rollup contract deploys it and calls it to read the proof frame. Written in assembly with geas."],
  Relayer: ["", "Claims L1 to L2 messages to other addresses, such as contracts, when their fee covers the claim. The claim pays it the fee before it approves payment, so it started with no ETH. Anyone can do the same."],
  Claimer: ["", "Claims on L1 the L2 to L1 messages to other addresses, when their fee covers the L1 gas. Anyone can claim a message: the ETH goes to its recipient and the fee to the claimer."],
  Spamoor: ["", "The funding wallet of spamoor, ethPandaOps' transaction generator, which funds child wallets that send ERC-20 transfers, Uniswap swaps, EIP-7702 delegations, EIP-8141 frame transactions and messages between the chains."],
  "Ping pong": ["", "An example app with a contract on each chain. A ping sent to one is delivered to the other, which answers with a pong at once. Each accepts only the calls that its messenger delivers from the other, which it checks with the sender the messenger exposes while it calls."],
  "ERC-20 bridge": ["", "An example token bridge with a contract on each chain, as rollups run them. The L1 one holds deposited tokens and has the L2 one mint as many of the token's L2 version, which it deploys at the token's first deposit. Withdrawals burn them on L2, and the L1 one releases them. Each accepts only the calls that its messenger delivers from the other."],
  "Demo token": ["", "An ERC-20 for the demo's users to bridge: on L1 the token Alice and Bob start with, on L2 its bridged version, which only the L2 bridge mints and burns."],
  "App developer": ["", "The account that deployed the example apps, the ping pong and the ERC-20 bridge, on both chains."],
  Alice: ["", USER_ROLE],
  Bob: ["", USER_ROLE],
  Charlie: ["", USER_ROLE],
  "Fee recipient": ["", "Receives the L2's priority fees. The operator picks it, as the consensus layer does on L1."],
  "ETH transfer log (EIP-7708)": ["", "Not an account: EIP-7708 makes every ETH transfer emit a log from this address."],
  "Beacon roots contract (EIP-4788)": ["", "On L2, it stores each block's L1 anchor: the hash of an L1 block, which deposit claims prove L1 state against."],
  "Withdrawal requests (EIP-7002)": ["", "A system contract Ethereum's rules require, so the L2 genesis has it too. The system call after each block reads its queue. Requests do nothing on L2, so the rollup contract accepts any."],
  "Consolidation requests (EIP-7251)": ["", "A system contract Ethereum's rules require, so the L2 genesis has it too. Requests do nothing on L2, so the rollup contract accepts any."],
  "Builder requests (EIP-8282)": ["", "One of EIP-8282's two system contracts for builder deposits and exits, which Ethereum's rules require, so the L2 genesis has them too. Requests do nothing on L2."],
  "Block hash history (EIP-2935)": ["", "Stores recent block hashes. The system call before each block writes the parent's hash, as on L1."],
  "Beacon deposit contract": ["", "Ethereum's deposit contract for validators, in the L2 genesis for parity with L1. It does nothing on L2."],
  "Expiry verifier (EIP-8141)": ["", "EIP-8141's contract that a frame can call to give a transaction an expiry. The L2 has it because its rules include EIP-8141."],
};

// An ERC-20's name, symbol, decimals and supply, and `holder`'s balance: on
// L1 now, on L2 as of its latest block on L1, as L2 pages show it. Null if
// the address is not a token.
async function tokenInfo(chain, token, holder) {
  const key = `token/${chain}/${token.toLowerCase()}/${holder || ""}`;
  if (!(key in state.cache)) state.cache[key] = await getJSON(`/api/token/${chain}/${token.toLowerCase()}${holder ? `?holder=${holder}` : ""}`);
  return state.cache[key];
}
const units = (value, t) => {
  const v = BigInt(value), d = 10n ** BigInt(t.decimals);
  const frac = (v % d).toString().padStart(t.decimals, "0").slice(0, 4).replace(/0+$/, "");
  return `${(v / d).toLocaleString("en-US")}${frac ? `.${frac}` : ""} ${esc(t.symbol)}`;
};
const ZERO_ADDRESS = "0x" + "0".repeat(40);

// keccak256("Transfer(address,address,uint256)"), which ERC-20 tokens log.
const TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";

// A transaction's token transfers, read from the logs themselves: [token,
// from, to, value, ERC-721 token ID]. An ERC-721 transfer indexes the token
// as a fourth topic.
const transfersOf = (tx, nfts = false) => logsOf(tx)
  .filter((l) => l.topics[0] === TRANSFER && l.address.toLowerCase() !== ETH_TRANSFER_LOG
    && (l.topics.length === 3 ? l.data.length === 66 : nfts && l.topics.length === 4))
  .map((l) => [l.address.toLowerCase(), "0x" + l.topics[1].slice(-40), "0x" + l.topics[2].slice(-40),
    l.topics.length === 3 ? BigInt(l.data) : null, l.topics.length === 4 ? BigInt(l.topics[3]) : null]);

// The contracts that send a message in a transaction, and those a message
// it claims is delivered to: tokens moving into the first are locked behind
// the message, and out of the second, released by it.
function messageEnds(tx) {
  const events = logsOf(tx).map((l) => l.event).filter(Boolean);
  const of = (names, key) => new Set(events.filter((e) => names.includes(e.name)).map((e) => String(e.args[key]).toLowerCase()));
  return { senders: of(["L1MessageSent", "L2MessageSent"], "sender"), recipients: of(["L1MessageClaimed", "L2MessageClaimed"], "to") };
}

// What a transaction does with tokens: locks, releases, mints, burns or
// moves them.
async function tokenRow(tx, chain) {
  const transfers = transfersOf(tx, true);
  if (!transfers.length) return null;
  const tokens = {};
  await Promise.all([...new Set(transfers.filter((x) => x[4] === null).map(([t]) => t))].map(async (t) => (tokens[t] = await tokenInfo(chain, t))));
  const { senders, recipients } = messageEnds(tx);
  const lines = transfers.map(([token, from, to, value, id]) => {
    const t = tokens[token];
    const amount = id !== null ? `token #${id} of ${addr(token, chain)}`
      : t ? `<a class="nowrap" href="#/address/${chain}/${token}">${units(value, t)}</a>` : `${value} units of ${addr(token, chain)}`;
    if (from === ZERO_ADDRESS) return `Minted ${amount} to ${addr(to, chain)}`;
    if (to === ZERO_ADDRESS) return `Burned ${amount} of ${addr(from, chain)}`;
    if (senders.has(to)) return `Locked ${amount} of ${addr(from, chain)} in ${addr(to, chain)}, which sends a message`;
    if (recipients.has(from)) return `Released ${amount} from ${addr(from, chain)} to ${addr(to, chain)}, for a message it received`;
    return `Moved ${amount} from ${addr(from, chain)} to ${addr(to, chain)}`;
  }).filter(Boolean);
  return lines.length ? ["Tokens", lines.join("<br>"), "From the tokens' Transfer logs, ERC-20 and ERC-721."] : null;
}

// The tokens an address holds, and for a token, what it is.
async function tokenFields(a, chain, contract) {
  const rows = [];
  const [self, held] = await Promise.all([contract ? tokenInfo(chain, a) : null, getJSON(`/api/explorer/holdings/${chain}/${a}.json`)]);
  if (self) {
    rows.push(["Token", `${esc(self.name)}, ${esc(self.symbol)}, ${self.decimals} decimals`, ""]);
    rows.push(["Total supply", units(self.totalSupply, self), ""]);
  }
  const balances = (await Promise.all((held || []).map(async (h) => {
    const t = await tokenInfo(chain, h.token, chain === "l1" ? a : undefined);
    const balance = t && (chain === "l1" ? t.balance : h.balance);
    return balance && BigInt(balance) > 0n ? `<a class="nowrap" href="#/address/${chain}/${h.token}">${units(balance, t)}</a>` : "";
  }))).filter(Boolean);
  if (balances.length) {
    rows.push(["Tokens", balances.join("<br>"), chain === "l2"
      ? "The sum of its tokens' transfers, as the follower rebuilt them from L1."
      : "Read from L1, for the tokens that the rollup's transactions moved."]);
  }
  return rows;
}

async function call(to, selector) {
  return rpc("eth_call", [{ to, data: selector }, "latest"]);
}
const word = (hex, i = 0) => "0x" + hex.slice(2 + 64 * i, 2 + 64 * (i + 1));
const wordAddress = (hex) => "0x" + hex.slice(-40);
const wordNumber = (hex) => parseInt(hex, 16);

async function addressPage(route) {
  const a = route.address;
  const chain = route.chain === "l2" ? "l2" : "l1";
  const c = (state.session && state.session.contracts) || {};
  const label = labels()[a];
  const [name, kind] = label || ["Address", ""];
  const role = ROLES[name];
  const rows = [];
  let live = [];
  let history = "";
  let count;
  let emitted = [];
  let l1Code = null, acc = null;
  if (chain === "l1") {
    const [balance, nonce, code] = await Promise.all([
      rpc("eth_getBalance", [a, "latest"]), rpc("eth_getTransactionCount", [a, "latest"]), rpc("eth_getCode", [a, "latest"]),
    ]);
    rows.push(
      ["Balance", eth(BigInt(balance).toString()), a === (c.rollup || "").toLowerCase() ? "The escrow: the ETH that deposits locked and withdrawals have not paid out." : ""],
      ["Nonce", parseInt(nonce, 16), ""],
      ["Code", code && code !== "0x" ? `${num((code.length - 2) / 2)} bytes` : "none", code && code !== "0x" ? "A contract." : "An account controlled by a key."],
    );
    l1Code = code && code !== "0x" ? code : null;
    if (a === (c.rollup || "").toLowerCase()) {
      const r = {};
      await Promise.all(["blockNumber", "blockHash", "stateRoot", "l1MessageCount", "l1MessageRoot", "anchorBlockNumber", "chainId", "l2GasLimit", "l2Messenger", "evmVkRegistry", "sequencer", "bond"].map(async (k) => (r[k] = await call(a, SELECTORS[k]))));
      live = [
        ["L2 head", `block ${l2BlockLink(wordNumber(r.blockNumber))}, ${hash(r.blockHash)}`, "The last L2 block the contract accepted. The next one must build on it."],
        ["Latest state root", hash(r.stateRoot, true), "Of the head block. The contract keeps the last 8,191, for withdrawals."],
        ["L1 anchor", `L1 block ${num(wordNumber(r.anchorBlockNumber))}`, "The last anchor. The next block's anchor cannot be older."],
        ["Messages sent to L2", num(wordNumber(r.l1MessageCount)), "Deposits and other L1 to L2 messages, in the message tree."],
        ["Message tree root", hash(r.l1MessageRoot, true), "What L2 proves deposits against, through its L1 anchor."],
        ["Chain ID, gas limit", `${wordNumber(r.chainId)}, ${num(wordNumber(r.l2GasLimit))}`, "Fixed at deployment."],
        ["L2 messenger", addr(wordAddress(r.l2Messenger), "l2"), "Whose storage the contract proves withdrawals against."],
        ["Key registry", addr(wordAddress(r.evmVkRegistry), "l1"), "Where the contract reads the verification key."],
        ["Sequencer", addr(wordAddress(r.sequencer), "l1"), "The only account the contract takes blocks from, until it posts nothing for two hours."],
        ["Sequencer's bond", eth(BigInt(r.bond).toString()), "Burned if the rollup ever holds another block at a height the sequencer preconfirmed."],
      ];
    } else if (a === (c.registry || "").toLowerCase()) {
      const entry = await call(a, "0x" + "0".repeat(64));
      live = [
        ["Current key hash", hash(word(entry), true), "The EVM verification key hash for the current fork, which proofs must be under. Here a placeholder, the hash of \"frames-devnet mock EVM verification key\", since the proofs are mock signatures: with EIP-8357, a fork registers the hash of the zkVM key of Ethereum's validation program."],
        ["Schema ID", `0x${wordNumber(word(entry, 1)).toString(16)}`, "The input schema the key's program expects: Amsterdam, revision 1."],
      ];
    } else if (a === (c.verifier || "").toLowerCase()) {
      live = [["Prover", addr(wordAddress(await call(a, SELECTORS.prover)), "l1"), "The only key whose signatures it accepts."]];
    }
    const list = await listPage("l1", 1, `&address=${a}`);
    const txs = list.entries;
    emitted = [...((await object(`l1/events/${a}`)) || [])].reverse();
    count = list.total;
    history = `<p class="section-lead">The rollup's L1 transactions that involve this address${list.total > txs.length ? `, the latest ${txs.length}` : ""}.</p>${txs.length ? l1Rows(txs) : '<p class="note">None.</p>'}`;
  } else {
    acc = await getJSON(`/api/explorer/l2/accounts/${a}.json`);
    if (!acc) {
      rows.push(["State", "not seen yet", "The follower writes an L2 account once a transaction touches it."]);
    } else {
      rows.push(
        ["Balance", acc.exists ? eth(acc.balance) : "0 ETH", "After L2 block " + acc.block + ", as the follower rebuilt it from L1."],
        ["Nonce", acc.exists ? acc.nonce : 0, ""],
        ["Code", acc.exists && acc.codeSize ? `${num(acc.codeSize)} bytes` : "none", acc.exists && acc.codeSize ? "A contract." : "An account controlled by a key, or none at all."],
      );
      if (acc.state) {
        live = [
          ["L1 rollup contract", addr(acc.state.l1Rollup, "l1"), "Whose message tree messages from L1 are proven against. Set in the genesis."],
          ["Messages sent to L1", num(acc.state.sentMessages), "Messages in its queue, which L1 proves against L2 state roots."],
          ["Last proven message root", hash(acc.state.provenL1MessageRoot, true), `Proven against the anchor of the L2 block with timestamp ${acc.state.provenAnchorTimestamp}.`],
        ];
      }
      const txs = [...acc.txs].reverse();
      emitted = [...(acc.events || [])].reverse();
      count = txs.length;
      history = txs.length ? l2TxRows(txs.slice(0, 50), a) : '<p class="note">None.</p>';
      const created = (state.index.contracts || {})[a];
      if (created) rows.push(["Contract creator", `created in ${l2TxLink(created.tx)}`, created.name ? `Named from its creation code.` : ""]);
    }
  }
  const other = Object.values(c.users || {}).some((u) => u.toLowerCase() === a)
    ? `<p class="note">The same address on <a href="#/address/${OTHER[chain]}/${a}">${OTHER[chain].toUpperCase()}</a>.</p>` : "";
  const bytecode = chain === "l1" ? l1Code : acc && acc.code;
  const created = chain === "l2" && (state.index.contracts || {})[a];
  const verified = created && created.source ? await object(`l2/sources/${a}`) : null;
  const code = sourceSection(a, name, bytecode, chain, verified);
  const base = `/address/${chain}/${a}`;
  const tab = route.tab;
  return `
    <h1>${esc(name)} ${net(chain)} ${kind ? badge(kind) : ""}</h1>
    <p class="mono">${a}</p>
    ${role ? `<div class="callout ${role[0] === "mock" ? "mock" : ""}"><p>${role[1]}</p></div>` : ""}
    ${other}
    ${fields([...rows, ...await tokenFields(a, chain, chain === "l1" ? !!l1Code : !!(acc && acc.codeSize)), ...live])}
    ${tabs(base, [
      ["", "Transactions", count, history],
      code && ["contract", "Contract", undefined, code],
      (code || emitted.length) && ["events", "Events", emitted.length, eventRows(emitted, chain)],
    ], tab)}`;
}

// The latest events an address emitted, newest first.
function eventRows(emitted, chain) {
  if (!emitted.length) return '<p class="note">No events yet.</p>';
  return `<p class="section-lead">Its latest events, newest first.</p>
    <table><thead><tr><th>Transaction hash</th><th>Block</th><th>Event</th><th>Arguments</th></tr></thead><tbody>
    ${emitted.map(({ hash: tx, block, log }) => {
      const e = log.event;
      return `<tr><td>${chain === "l1" ? l1TxLink(tx) : l2TxLink(tx)}</td><td>${chain === "l1" ? num(block) : l2BlockLink(block)}</td>
        <td>${e ? `<b title="${esc(e.signature || "")}">${e.name}</b>` : `<span class="muted">unknown</span><br>${hash(log.topics[0])}`}</td>
        <td>${e ? Object.entries(e.args).map(([k, v]) => `${esc(k)}: ${value(v, k, { chain, event: e.name })}`).join("<br>") : hash(log.data)}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// The source file each contract of the demo is built from.
function mainSource(a) {
  const c = (state.session && state.session.contracts) || {};
  const is = (x) => x && a === x.toLowerCase();
  if (is(c.rollup)) return "contracts/src/frames/FramesSequencedRollup.sol";
  if (is(c.l2Messenger)) return "contracts/src/l2/L2Messenger.sol";
  if (is(c.verifier)) return "contracts/src/frames/MockDependencyVerifier.sol";
  if (is(c.framesHelper)) return "contracts/frames/frame_introspection.eas";
  if (is(c.registry)) return "sys-asm/src/verification_key_registry/main.eas";
  if (is(c.pingPongL1) || is(c.pingPongL2)) return "contracts/src/examples/PingPong.sol";
  if (is(c.l1Bridge)) return "contracts/src/examples/L1ERC20Bridge.sol";
  if (is(c.l2Bridge)) return "contracts/src/examples/L2ERC20Bridge.sol";
  if (is(c.demoToken)) return "contracts/src/examples/DemoToken.sol";
  if (is(c.demoTokenL2)) return "contracts/src/examples/BridgedERC20.sol";
  return null;
}

// The system contracts of Ethereum's specification, by label: their source
// is in ethereum/sys-asm.
const SYSTEM_CONTRACT = /EIP-(7002|7251|8282|2935|4788|8141)|deposit contract/;

// Whether an address is a key's account, one that EIP-7702 delegated to a
// contract's code, or a contract with or without a source the explorer has.
async function accountKind(a, chain) {
  let code;
  if (chain === "l1") code = (await rpc("eth_getCode", [a, "latest"])) || "0x";
  else {
    const acc = await object(`l2/accounts/${a}`);
    if (!acc) return null;
    code = acc.exists && acc.codeSize ? acc.code || "0x00" : "0x";
  }
  if (code === "0x") return "eoa";
  if (code.startsWith("0xef0100") && code.length === 48) return "delegated";
  const created = chain === "l2" && (state.index.contracts || {})[a];
  const label = labels()[a];
  return (created && created.source) || mainSource(a) || (label && SYSTEM_CONTRACT.test(label[0])) ? "verified" : "unverified";
}

const KINDS = {
  eoa: ["EOA", "An externally owned account, which a key controls."],
  delegated: ["EOA · delegated", "A key's account that runs a contract's code, delegated with EIP-7702."],
  verified: ["Contract · verified", "A contract whose source the explorer shows."],
  unverified: ["Contract · unverified", "A contract whose source the explorer does not have."],
};
const kindTag = (k) => (k ? ` <span class="kind ${k}" title="${KINDS[k][1]}">${KINDS[k][0]}</span>` : "");

// The kinds of a transaction's sender and recipients.
async function kindsOf(tx, chain) {
  const addresses = [...new Set([tx.from, tx.to, ...(tx.frames || []).map((f) => f.target)].filter(Boolean))];
  const kinds = await Promise.all(addresses.map((a) => accountKind(a, chain)));
  return Object.fromEntries(addresses.map((a, i) => [a, kinds[i]]));
}

// A file and the files it imports, transitively.
function sourceClosure(main) {
  const out = [];
  const visit = (path) => {
    if (out.includes(path) || !state.sources[path]) return;
    out.push(path);
    state.sources[path].imports.forEach(visit);
  };
  visit(main);
  return out;
}

function sourceFile(path, open) {
  return sourceView(path, state.sources[path].content, path.endsWith(".eas"), open);
}

function sourceView(title, content, assembly, open) {
  const lines = content.replace(/\n$/, "").split("\n");
  const width = String(lines.length).length;
  const body = lines.map((line, i) => {
    const text = assembly
      ? (line.includes(";") ? escCode(line.slice(0, line.indexOf(";"))) + `<span class="tk-c">${escCode(line.slice(line.indexOf(";")))}</span>` : escCode(line))
      : highlight(line);
    return `<span class="ln">${String(i + 1).padStart(width, " ")}</span>${text}`;
  }).join("\n");
  return `<details class="code" ${open ? "open" : ""}><summary><b>${esc(title)}</b> <span class="muted">${lines.length} lines</span></summary><pre class="code src">${body}</pre></details>`;
}

// `verified` is the source of a contract a transaction created, which the
// follower found on disk compiling to the code it was created with.
function sourceSection(a, name, bytecode, chain, verified) {
  const main = state.sources && mainSource(a);
  const notes = {
    "sys-asm/src/verification_key_registry/main.eas": "The EIP-8357 registry, from ethereum/sys-asm. The deployed runtime is this program with the admin's address in place of the system address, since no fork on this devnet performs the system call.",
    "contracts/frames/frame_introspection.eas": "Built with geas into the runtime the rollup contract deploys.",
  };
  let html = "";
  if (verified) {
    html += `<h2>Source</h2>
      <p class="section-lead">From <code>${esc(verified.file)}</code>, which compiles to the creation code it was deployed with${
        verified.flattened ? `. Flattened with <a href="https://github.com/l2beat/l2beat/tree/main/packages/discovery/src/flatten">L2BEAT's flattener</a>` : ""}.</p>
      ${sourceView(`${verified.name}.sol${verified.flattened ? ", flattened" : ""}`, verified.flat, false, true)}`;
  }
  // Solidity contracts, flattened by L2BEAT's flattener.
  const flatName = main && main.endsWith(".sol") && main.split("/").pop().replace(".sol", "");
  if (flatName && state.flat && state.flat[flatName]) {
    html += `<h2>Source</h2>
      <p class="section-lead">From <code>contracts/src/</code>, flattened with <a href="https://github.com/l2beat/l2beat/tree/main/packages/discovery/src/flatten">L2BEAT's flattener</a>.</p>
      ${sourceView(`${flatName}.sol, flattened`, state.flat[flatName], false, true)}`;
  } else if (main && state.sources[main]) {
    const files = sourceClosure(main);
    const external = [...new Set(files.flatMap((p) => state.sources[p].external))];
    // Contracts open, libraries closed.
    const isContract = (p) => p.endsWith(".eas") || /^(abstract )?contract /m.test(state.sources[p].content);
    html += `<h2>Source</h2>
      <p class="section-lead">${notes[main] || `The contract's source and the files it imports, as in the repository's <code>contracts/</code>.`}${
        external.length ? ` It also imports ${external.map((e) => `<code>${esc(e)}</code>`).join(", ")}, not shown.` : ""}</p>
      ${files.map((p) => sourceFile(p, isContract(p))).join("")}`;
  } else if (bytecode && SYSTEM_CONTRACT.test(name)) {
    html += `<h2>Source</h2><p class="section-lead">A system contract from Ethereum's specification, whose source is in
      <a href="https://github.com/ethereum/sys-asm">ethereum/sys-asm</a>. The L2 genesis holds the bytecode EEST uses for L1.</p>`;
  }
  if (bytecode) {
    html += `<details class="code"><summary><b>Runtime bytecode</b> <span class="muted">${num((bytecode.length - 2) / 2)} bytes, read from ${chain === "l1" ? "the L1 node" : "the follower's L2 state"}</span></summary>
      <pre class="code bytecode">${esc(bytecode)}</pre></details>`;
  }
  return html;
}

// ---------------------------------------------------------------------------
// Code
// ---------------------------------------------------------------------------

const escCode = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const KEYWORDS = "function|returns|return|require|revert|if|else|for|while|emit|external|internal|public|private|view|pure|payable|memory|calldata|storage|override|virtual|constant|immutable|transient|mapping|assembly|unchecked|new|true|false|fallback";
const TOKENS = new RegExp(`("[^"]*")|\\b(0x[0-9a-fA-F]+|\\d[\\d_]*)\\b|\\b(${KEYWORDS})\\b|\\b(uint\\d*|int\\d*|bytes\\d*|address|bool|string)\\b`, "g");

// A small Solidity highlighter: comments, strings, numbers, keywords, types.
const highlighted = new Map();
function highlight(code) {
  if (!highlighted.has(code)) highlighted.set(code, highlightLines(code));
  return highlighted.get(code);
}

function highlightLines(code) {
  return code.split("\n").map((line) => {
    const i = line.indexOf("//");
    const src = i >= 0 ? line.slice(0, i) : line;
    const body = escCode(src).replace(TOKENS, (m, str, n, kw, ty) =>
      str ? `<span class="tk-s">${str}</span>` : n ? `<span class="tk-n">${n}</span>` : kw ? `<span class="tk-k">${kw}</span>` : `<span class="tk-t">${ty}</span>`);
    return body + (i >= 0 ? `<span class="tk-c">${escCode(line.slice(i))}</span>` : "");
  }).join("\n");
}

// The contracts deployed at an address, in the order the explorer looks for
// a function's code: the rollup's core before the customization that wraps it.
function contractsAt(target) {
  const c = (state.session && state.session.contracts) || {};
  const t = (target || "").toLowerCase();
  if (t === (c.rollup || "").toLowerCase()) return ["FramesNativeRollup", "NativeRollup", "SequencedNativeRollup", "FramesSequencedRollup"];
  if (t === (c.l2Messenger || "").toLowerCase()) return ["L2Messenger"];
  if (t === (c.verifier || "").toLowerCase()) return ["MockDependencyVerifier"];
  return [];
}

function snippetKey(target, fn) {
  return contractsAt(target).map((c) => `${c}.${fn}`).find((k) => state.snippets && state.snippets[k]);
}

// The code of a function, and collapsed below it the functions it calls,
// one level deep.
function codeBlock(key, open = true, nested = false) {
  const s = state.snippets && state.snippets[key];
  if (!s) return "";
  const calls = nested ? [] : s.calls.filter((k) => state.snippets[k]);
  return `<details class="code" ${open ? "open" : ""}><summary>Code: <b>${esc(key)}</b>
      <span class="muted">${esc(s.file)}, lines ${s.startLine} to ${s.endLine}</span></summary>
    <pre class="code">${highlight(s.code)}</pre>
    ${calls.length ? `<div class="callees"><span class="muted">It calls:</span>${calls.map((k) => codeBlock(k, false, true)).join("")}</div>` : ""}
  </details>`;
}

// EIP-8141's default code, which runs for a frame that targets an account without code.
const DEFAULT_CODE = `- If mode is VERIFY:
  - Read the allowed approval scope from the flags field:
    allowed_scope = frame.flags & APPROVE_SCOPE_MASK.
  - If allowed_scope == APPROVE_SCOPE_NONE, revert.
  - Let sig_index = 0 if allowed_scope & APPROVE_EXECUTION != 0, else sig_index = 1.
  - If there is not a SECP256K1 signature sig at index sig_index such that
    resolved_signer == resolved_target and sig.msg == Bytes(), revert.
  - Call APPROVE(allowed_scope).
- If mode is SENDER or DEFAULT:
  - Return successfully as if calling empty code.`;

function defaultCodeBlock() {
  return `<details class="code" open><summary>Code: <b>EIP-8141 default code</b>
      <span class="muted">the protocol's code for an account without code, from the EIP's Behavior section</span></summary>
    <pre class="code">${escCode(DEFAULT_CODE)}</pre></details>`;
}

// The source a call runs, when the explorer has it: it only has the sources
// of the rollup's contracts. Contract pages show the bytecode of the others.
function codeFor(target, call) {
  const key = call && snippetKey(target, call.function);
  if (key) return codeBlock(key);
  if (contractsAt(target).length) return "";
  return `<p class="note">The explorer has no source for ${addr(target, "l2")}, only for the rollup's contracts. Its page shows its bytecode.</p>`;
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

function value(v, key, ctx = {}) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) {
    if (!v.length) return "empty";
    if (v.every((x) => x && typeof x === "object")) return v.map((x, i) => `<div class="muted">[${i}]</div>${argTable(x, {}, ctx)}`).join("");
    return v.map((x) => value(x, key, ctx)).join("<br>");
  }
  if (typeof v === "object") return argTable(v, {}, ctx);
  const s = String(v);
  if (/^0x[0-9a-f]{40}$/i.test(s)) return addr(s, chainFor(key, ctx));
  if ((key === "value" || key === "fee") && /^\d+$/.test(s)) return eth(s);
  if (/^0x[0-9a-f]*$/i.test(s)) return s.length > 70 ? hash(s) + ` <span class="muted">(${(s.length - 2) / 2} bytes)</span>` : `<span class="mono">${s}</span>${tags(s)}`;
  return esc(typeof v === "number" ? num(v) : s);
}

function argTable(args, notes = {}, ctx = {}, views = {}) {
  // A struct takes the value and note columns, so its own table has room,
  // as does an argument with its own view.
  const nested = (v) => v && typeof v === "object" && (!Array.isArray(v) || v.some((x) => x && typeof x === "object"));
  return `<table class="fields"><tbody>${Object.entries(args)
    .map(([k, v]) => views[k] || nested(v)
      ? `<tr${views[k] && ctx.fn ? ` data-at="${ctx.fn}.${k}"` : ""}><td>${esc(k)}</td><td class="value nested" colspan="2">${views[k] || value(v, k, ctx)}</td></tr>`
      : `<tr><td>${esc(k)}</td><td class="value">${value(v, k, ctx)}</td><td class="note">${notes[k] || ""}</td></tr>`)
    .join("")}</tbody></table>`;
}

const EVENT_NOTES = {
  Transfer: "An EIP-7708 log: Ethereum logs every ETH transfer from this system address.",
  L1MessageRootProven: "The L2 messenger proved and cached the root of L1's message tree.",
  L1MessageClaimed: "The message was delivered.",
  L2MessageSent: "The message was recorded, to be claimed on L1.",
  L1MessageSent: "The message was added to the message tree.",
  BlockAdded: "The rollup contract accepted the L2 block.",
  L2MessageClaimed: "The message was delivered on L1.",
};

function events(logs, chain) {
  if (!logs || !logs.length) return `<p class="note">No events.</p>`;
  return `<table><thead><tr><th>Event</th><th>From</th><th>Arguments</th></tr></thead><tbody>
    ${logs.map((l) => {
      const e = l.event;
      // EIP-7708 logs ETH transfers with ERC-20's Transfer signature.
      const note = e && (e.name !== "Transfer" || l.address === ETH_TRANSFER_LOG) ? EVENT_NOTES[e.name] || "" : "";
      const raw = [...l.topics.slice(1).map((t, i) => `topic ${i + 1}: ${hash(t)}`),
        ...(l.data.length > 2 ? l.data.slice(2).match(/.{1,64}/g).map((w, i) => `data ${i}: <span class="mono">${w}</span>`) : [])];
      return `<tr><td>${e ? `<b title="${esc(e.signature || "")}">${e.name}</b><br><span class="note">${note}</span>`
        : `<span class="muted">unknown</span><br>${hash(l.topics[0])}`}</td><td>${addr(l.address, chain)}</td>
        <td>${e ? Object.entries(e.args).map(([k, v]) => `${esc(k)}: ${value(v, k, { chain, event: e.name })}`).join("<br>") : raw.join("<br>")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

const ETH_TRANSFER_LOG = "0xfffffffffffffffffffffffffffffffffffffffe";

const ADVANCE_NOTES = {
  stateRoot: "Claimed by the operator, checked by the proof.",
  receiptsRoot: "Claimed by the operator, checked by the proof.",
  logsBloom: "Claimed by the operator, checked by the proof.",
  gasUsed: "Claimed by the operator, checked by the proof.",
  timestamp: "Must not exceed L1 time.",
  baseFeePerGas: "Checked by the proof against EIP-1559.",
  blockHash: "Checked by the proof. The contract stores it as the new head.",
  transactionsRoot: "SSZ root of the transactions in the blob, checked by the proof.",
  blockAccessListRoot: "SSZ root of the access list in the blob, checked by the proof.",
  payloadBlobCount: "How many blob hashes the contract reads with BLOBHASH.",
  executionRequestsRoot: "The contract accepts any requests: they do nothing on L2.",
  anchorBlockNumber: "An L1 block within the last 256, not older than the previous anchor.",
  feeRecipient: "A free input of the operator.",
  prevRandao: "A free input of the operator.",
  extraData: "A free input of the operator.",
};

// Whether a frame before frame `i` claims a deposit to `to`.
const claimsBefore = (tx, i, to) => tx.frames.slice(0, i).some((f) => f.call && f.call.function === "claimL1Message" && f.call.args.m.to === to);

function frameExplain(tx, f, i, layer) {
  const fn = f.call && f.call.function;
  const noCode = f.targetHasCode === undefined || f.targetHasCode === null ? !contractsAt(f.target).length : !f.targetHasCode;
  if (f.mode === "VERIFY" && !f.call && noCode) {
    const pays = f.flags.includes("APPROVE_PAYMENT");
    const approves = f.flags.filter((x) => x.startsWith("APPROVE"));
    return {
      text: `${approves.length ? `The sender's account, the frame's target, ${pays ? "approves the transaction and pays for it" : "approves the transaction"}.` : "A read-only check that must not revert."}
        Without code at the target, EIP-8141's default code checks the transaction's signature. ${
        pays && claimsBefore(tx, i, tx.from) ? "The fee is taken now, from the ETH the claim above just delivered: that is how a deposit pays for its own claim." : ""}`,
      badges: [badge("real")],
    };
  }
  if (f.dependency) {
    return {
      text: `Stands in for an EIP-8288 dependency frame: a contract checks that the trusted prover signed the dependency below.
        With EIP-8288, this frame would only declare the dependency, and Ethereum's own proof would cover it.`,
      badges: [badge("mock")],
      extra: argTable(
        { scheme: `0x${f.dependency.scheme.toString(16)} (LeanSTARK)`, dataHash: f.dependency.dataHash, verificationKeyHash: f.dependency.verificationKeyHash, signature: f.dependency.signature },
        { dataHash: "The public_input_root: what the proof commits to, which the rollup contract rebuilds.", verificationKeyHash: "The key hash the registry holds for the current fork. Here a placeholder, the hash of \"frames-devnet mock EVM verification key\", which the admin registered at deployment: with EIP-8357, a fork registers the hash of the zkVM key of Ethereum's validation program.", signature: "The trusted prover's signature, in place of a proof." },
      ),
    };
  }
  const texts = {
    advance: "Calls advance on the rollup contract. It reads the dependency from the proof frame, rebuilds the proof's input from its storage, these arguments, the blob and the L1 anchor, and requires them to match.",
    proveL1MessageRoot: "Proves the root of L1's message tree against this L2 block's L1 anchor: the L1 block header, then the rollup contract's account and the root's storage slot in that block's state. The messenger caches the root, so only the first claim of a block carries this proof.",
    claimL1Message: "Checks the message's path to the proven root, marks it claimed, and releases its value from the pre-minted supply. It runs before any payment is approved: the recipient has no ETH yet.",
    sendMessage: layer === "L1" ? "Adds a message to the rollup contract's message tree, with the ETH it carries held in escrow." : "Records a message in the L2 messenger's storage, locking its ETH back into the pre-minted supply.",
    claimL2Message: "Proves the message against a recent L2 state root and delivers it, its ETH from the escrow.",
  };
  const ctx = { chain: layer.toLowerCase(), fn };
  const known = f.call && texts[fn];
  return {
    text: texts[fn] || {
      VERIFY: "A VERIFY frame: read-only, it must not revert, and it approves what its flags allow.",
      SENDER: "Calls the target as the sender, which is the caller the target sees.",
      DEFAULT: "Calls the target from EIP-8141's entry point address, not as the sender.",
    }[f.mode] + (noCode && (!f.data || f.data === "0x") ? " The target has no code, so the frame only moves its value." : ""),
    badges: known ? [badge("real")] : [],
    extra: known ? argTable(f.call.args.params || f.call.args, fn === "advance" ? ADVANCE_NOTES : {}, ctx, callViews(f.call, f.status === 1))
      + rawInput(f.data) : inputData(f.data, f.call, ctx),
  };
}

// A message's Merkle path, drawn in the tree of messages. From the root
// down, the path branches left (0) or right (1) by the index's bits, and
// each sibling the path gives is the root of a subtree of other messages.
// MessageTree.rootFromPath hashes the other way, from the message up.
function pathView(p, index, proven) {
  const n = p.levels.length;
  const W = 136, H = 24, D = 92, STEP = 72, GUTTER = 64;
  const bit = (h) => (p.levels[h].right ? 1 : 0);
  // Centers of the path's node and its sibling at each height.
  const pathX = { [n]: 0 }, sibX = {};
  for (let h = n - 1; h >= 0; h--) {
    pathX[h] = pathX[h + 1] + (bit(h) ? D : -D);
    sibX[h] = pathX[h + 1] - (bit(h) ? D : -D);
  }
  const xs = [...Object.values(pathX), ...Object.values(sibX)];
  const minX = Math.min(...xs) - W / 2, maxX = Math.max(...xs, W / 2 + 230) + W / 2;
  const X = (x) => GUTTER + x - minX;
  const top = n < 32 ? 84 : 0;
  const Y = (h) => top + (n - h) * STEP;
  const box = (x, y, h, cls, title) => `<g class="${cls}"><title>${esc(title)}</title><rect x="${X(x) - W / 2}" y="${y}" width="${W}" height="${H}" rx="5"/>
    <text x="${X(x)}" y="${y + H / 2 + 4}" text-anchor="middle">${esc(short(h))}</text></g>`;
  const text = (x, y, s, cls = "mt-label", anchor = "middle") => `<text class="${cls}" x="${x}" y="${y}" text-anchor="${anchor}">${s}</text>`;
  const node = (h) => (h === n ? (n ? p.levels[n - 1].node : p.leaf) : h ? p.levels[h - 1].node : p.leaf);
  const parts = [];
  if (n < 32) {
    parts.push(box(0, 0, p.root, "mt-root", `Root: ${p.root}`),
      `<line class="mt-path mt-dashed" x1="${X(0)}" y1="${H}" x2="${X(0)}" y2="${top}"/>`,
      text(X(0) + 10, H + 36, `heights ${n} to 31: always left (0), beside empty subtrees`, "mt-label", "start"));
  }
  if (proven) parts.push(text(X(0) + W / 2 + 12, H / 2 + 4, "✓ the root the messenger proved", "mt-ok", "start"));
  parts.push(box(0, Y(n), node(n), n < 32 ? "mt-node" : "mt-root", `Node at height ${n}: ${node(n)}`), text(4, Y(n) + H / 2 + 4, `height ${n}`, "mt-label", "start"));
  for (let h = n - 1; h >= 0; h--) {
    const l = p.levels[h], y = Y(h), parentY = Y(h + 1) + H;
    const edge = (x, cls, b) => `<line class="${cls}" x1="${X(pathX[h + 1])}" y1="${parentY}" x2="${X(x)}" y2="${y}"/>`
      + text((X(pathX[h + 1]) + X(x)) / 2 + (x > pathX[h + 1] ? 12 : -12), (parentY + y) / 2, b, cls === "mt-path" ? "mt-bit" : "mt-bit off");
    const start = ((index >> h) ^ 1) << h;
    const covers = h ? `#${start}–#${start + 2 ** h - 1}` : `message #${start}`;
    parts.push(edge(pathX[h], "mt-path", bit(h)), edge(sibX[h], "mt-edge", 1 - bit(h)),
      box(pathX[h], y, node(h), h ? "mt-node" : "mt-leaf", h ? `Node at height ${h}: ${node(h)}` : `Hash of message #${index}: ${node(h)}`),
      box(sibX[h], y, l.sibling, `mt-sib${l.empty ? " empty" : ""}`, `Sibling from the path, at height ${h}: ${l.sibling}${l.empty ? " (an empty subtree)" : ""}`),
      h ? `<path class="mt-subtree${l.empty ? " empty" : ""}" d="M${X(sibX[h])} ${y + H + 2} l-22 16 h44 z"/>` : "",
      text(X(sibX[h]), y + H + (h ? 30 : 14), `${covers}${l.empty ? ", none yet" : ""}`),
      text(4, y + H / 2 + 4, `height ${h}`, "mt-label", "start"));
  }
  parts.push(text(X(pathX[0]), Y(0) + H + 14, `message #${index}`, "mt-label strong"));
  return `<p class="tag-note">The tree of messages, with only this claim's path drawn. From the root down, left edges are 0 and right edges
      are 1, so the bold path spells #${index} in binary, ${Number(index).toString(2)}. The claim carries the blue siblings, each the root of
      a subtree of other messages, and hashes from the message up to the root. Hover a node for its full hash.</p>
    <svg class="merkle" viewBox="0 0 ${GUTTER + maxX - minX + 10} ${Y(0) + H + 24}" width="${GUTTER + maxX - minX + 10}">${parts.join("")}</svg>`;
}

// A Merkle Patricia proof, drawn from its root down: a branch node has a
// child per hex digit, and the proof follows the key's next digit; an
// extension shares digits; the leaf holds the rest of the key and the value.
function trieView(p, keyOf, rootLabel, valueLabel, ok) {
  const CW = 22, CH = 22, X0 = 110, BW = 16 * CW, STEP = 62, MID = X0 + BW / 2;
  const text = (x, y, s, cls = "mt-label", anchor = "start") => `<text class="${cls}" x="${x}" y="${y}" text-anchor="${anchor}">${s}</text>`;
  const parts = [`<g class="mt-sib"><title>${esc(rootLabel)}: ${p.root}</title><rect x="${MID - 75}" y="0" width="150" height="${CH + 2}" rx="5"/>
      ${text(MID, CH / 2 + 5, esc(short(p.root)), "", "middle")}</g>`, text(MID + 85, CH / 2 + 5, esc(rootLabel))];
  const key = [];
  let from = [MID, CH + 2], y = 0;
  p.nodes.forEach((nd, i) => {
    y = 46 + i * STEP;
    parts.push(`<line class="mt-path" x1="${from[0]}" y1="${from[1]}" x2="${nd.kind === "branch" ? MID : MID}" y2="${y}"/>`,
      text(4, y + CH / 2 + 4, `${nd.kind}`));
    if (nd.kind === "branch") {
      nd.children.forEach((has, d) => {
        const x = X0 + d * CW, taken = d === nd.nibble, digit = d.toString(16);
        parts.push(`<g class="mt-cell${taken ? " taken" : has ? " child" : ""}"><title>${taken ? `Followed: the key's next digit is ${digit}` : has ? `Another subtree, under ${digit}` : `Nothing under ${digit}`}</title>
          <rect x="${x}" y="${y}" width="${CW}" height="${CH}"/>${text(x + CW / 2, y + CH / 2 + 4, digit, "", "middle")}</g>`);
        if (has && !taken) parts.push(`<line class="mt-edge" x1="${x + CW / 2}" y1="${y + CH}" x2="${x + CW / 2 + (d - 7.5) * 0.8}" y2="${y + CH + 9}"/>`);
      });
      from = nd.nibble === null ? [MID, y + CH] : [X0 + nd.nibble * CW + CW / 2, y + CH];
      key.push([nd.nibble === null ? "?" : nd.nibble.toString(16), "mt-k"]);
    } else {
      parts.push(`<g class="mt-node"><title>${nd.kind}: ${nd.hash}</title><rect x="${X0}" y="${y}" width="${BW}" height="${CH}" rx="5"/>
        ${text(MID, y + CH / 2 + 4, nd.kind === "leaf" ? `the rest of the key, ${nd.path.length} digits` : `shared digits ${nd.path}`, "", "middle")}</g>`);
      from = [MID, y + CH];
      key.push([nd.path, nd.kind === "leaf" ? "mt-rest" : "mt-k"]);
    }
  });
  const d = p.nodes[p.nodes.length - 1].decoded || {};
  y += 50;
  const lines = d.storageRoot ? [`nonce ${num(d.nonce)}, balance ${eth(d.balance)}`, `storage root ${short(d.storageRoot)}`] : [short(d.word || "")];
  const VH = lines.length * 16 + 8;
  parts.push(`<line class="mt-path" x1="${from[0]}" y1="${from[1]}" x2="${MID}" y2="${y}"/>`, text(4, y + VH / 2 + 4, "value"),
    `<g class="${ok ? "mt-root" : "mt-node"}"><title>${esc(valueLabel)}: ${esc(d.word || JSON.stringify(d))}</title><rect x="${X0}" y="${y}" width="${BW}" height="${VH}" rx="5"/>
      ${lines.map((l, i) => text(MID, y + 16 + 16 * i, esc(l), "", "middle")).join("")}</g>`,
    text(MID, y + VH + 16, `${esc(valueLabel)}${ok ? " ✓" : ""}`, ok ? "mt-ok" : "mt-label", "middle"));
  y += VH - CH;
  return `<p class="tag-note">Key <span class="mono">${key.map(([s, c]) => `<span class="${c}">${s}</span>`).join("")}</span>, the hash of
      ${keyOf}. Each branch node has a child per hex digit, and the proof follows the key's next digit, in blue. The leaf holds the
      rest of the key.</p>
    <svg class="merkle" viewBox="0 0 ${MID + 85 + 7 * rootLabel.length} ${y + CH + 24}" width="${MID + 85 + 7 * rootLabel.length}">${parts.join("")}</svg>`;
}

// Arguments drawn rather than listed: a deposit's Merkle path, and the
// Merkle Patricia proofs of an account and one of its storage slots.
function callViews(call, ok) {
  const views = {};
  if (!call) return views;
  if (call.pathToRoot) views.path = pathView(call.pathToRoot, call.args.m.index, ok);
  if (call.proofs) {
    const l2 = call.function === "claimL2Message";
    const storageRoot = call.proofs.storageProof.root;
    views.accountProof = trieView(call.proofs.accountProof, `the ${l2 ? "L2 messenger's" : "rollup contract's"} address`,
      l2 ? `state root of L2 block #${call.args.l2BlockNumber}` : "state root of the L1 block in l1Header",
      `the ${l2 ? "L2 messenger's" : "rollup contract's"} account, whose storage root ${short(storageRoot)} starts the storage proof`, ok);
    views.storageProof = trieView(call.proofs.storageProof, l2 ? `the slot of message #${call.args.m.index} in the messenger's queue` : "the slot of the message tree's root",
      "storage root, from the account", l2 ? "the message's hash" : "the root of L1's message tree", ok);
  }
  return views;
}

function frameCards(tx, layer) {
  return `<div class="frames">${tx.frames.map((f, i) => {
    const e = frameExplain(tx, f, i, layer);
    const status = f.status === undefined ? "" : f.status === 1 ? `<span class="check">succeeded</span>` : `<span class="bad">failed</span>`;
    return `<div class="frame"><div class="frame-head"><span class="idx">Frame ${i}</span><span class="mode">${f.mode}</span>
        ${f.call ? `<code>${f.call.function}</code>` : ""} → ${addr(f.target, layer.toLowerCase(), false)} ${e.badges.join(" ")}<span class="status">${status}</span></div>
      <div class="frame-body"><p class="explain">${e.text}</p>
        <div class="gasbar"><span>Called by ${addr(frameCaller(tx, f), layer.toLowerCase(), false)}</span><span>Flags: ${f.flags.length ? f.flags.map((x) => `<span class="flag">${x}</span>`).join(" ") : "none"}</span>
          <span>Execution gas: ${f.executionGasUsed !== undefined ? num(f.executionGasUsed) + " of " : ""}${num(f.executionGasLimit)}</span>
          <span>State gas: ${f.stateGasUsed !== undefined ? num(f.stateGasUsed) + " of " : ""}${num(f.stateGasLimit)}</span>
          ${f.value ? `<span>Value: ${eth(f.value)}</span>` : ""}</div>
        ${e.extra || ""}
        ${f.mode === "VERIFY" && !f.call && (f.targetHasCode === undefined || f.targetHasCode === null ? !contractsAt(f.target).length : !f.targetHasCode) ? defaultCodeBlock() : f.dependency ? codeBlock(snippetKey(f.target, "fallback"))
          : f.data && f.data !== "0x" ? codeFor(f.target, f.call) : ""}
        ${f.logs && f.logs.length ? `<h3>Events</h3>${events(f.logs, layer.toLowerCase())}` : ""}</div></div>`;
  }).join("")}</div>`;
}

const statusText = (s) => (s === 0 ? `<span class="bad">failed</span>` : `<span class="check">succeeded</span>`);

// Calldata as the selector and 32-byte words, or init code in 32-byte lines.
function rawInput(data, create = false) {
  if (!data || data === "0x") return "";
  const truncated = data.endsWith("…");
  const h = data.replace("…", "").slice(2);
  const lines = [];
  const body = create ? h : h.slice(8);
  if (!create) lines.push(`<span class="ln">selector</span>${h.slice(0, 8)}`);
  for (let i = 0; i * 64 < body.length; i++) lines.push(`<span class="ln">${String(i).padStart(3, " ")}</span>${body.slice(64 * i, 64 * i + 64)}`);
  if (truncated) lines.push(`<span class="muted">…</span>`);
  return `<details class="code"><summary>Raw ${create ? "init code" : "input"}, ${num(h.length / 2)} bytes${truncated ? ", shortened" : ""}</summary><pre class="code src">${lines.join("\n")}</pre></details>`;
}

// A call's input: the function and its arguments when the explorer knows the
// signature, from the ABIs it loaded, and the raw bytes.
function inputData(data, call, ctx) {
  if (!data || data === "0x") return "";
  if (!call) return `<p class="note">Function selector <code>${data.slice(0, 10)}</code>: none of the ABIs the explorer loaded has it.</p>${rawInput(data)}`;
  return `<p><code>${esc(call.signature)}</code></p>${argTable(call.args, {}, ctx, callViews(call, ctx.ok))}${rawInput(data)}`;
}

// The function a transaction calls, for tables: its name, its selector, or nothing.
function method(tx) {
  if (tx.kind === "deploy") return '<span class="muted">create</span>';
  if ("method" in tx) return tx.method ? `<code>${esc(tx.method)}</code>` : "";
  const calls = tx.frames ? tx.frames.filter((f) => f.data && f.data !== "0x") : tx.to && tx.data !== "0x" ? [tx] : [];
  return calls.map((c) => (c.call ? `<code>${c.call.function}</code>` : `<code class="muted">${c.data.slice(0, 10)}</code>`)).join(", ");
}

// A price per gas, in wei, with gwei when large enough to read.
const perGas = (wei) => {
  const w = Number(wei);
  return `${num(w)} wei${w >= 1e6 ? ` <span class="muted">(${(w / 1e9).toLocaleString("en-US", { maximumFractionDigits: 9 })} gwei)</span>` : ""}`;
};

// What a transaction paid, for the overview, and how, for the details.
// What the gas would cost on mainnet at a 1 gwei base fee, at ETH's
// current price.
function mainnetCost(gas) {
  if (!state.ethUsd) return "";
  const usd = (gas * 1e-9) * state.ethUsd;
  const shown = usd < 0.01 ? "<$0.01" : `$${usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `<br><span class="muted">≈ ${shown} on mainnet at a 1 gwei base fee, with ETH at $${Math.round(state.ethUsd).toLocaleString("en-US")}</span>`;
}

function feeRows(tx, chain) {
  if (tx.effectiveGasPrice === undefined || tx.fee === undefined) return { overview: [], details: [] };
  const offered = tx.maxFeePerGas !== undefined
    ? [["Max fee per gas", perGas(tx.maxFeePerGas), "The most the sender pays per gas, base fee included."],
      ["Max priority fee per gas", perGas(tx.maxPriorityFeePerGas), "The most the sender tips per gas, on top of the base fee."]]
    : [];
  return {
    overview: [
      ["Transaction fee", `${eth(tx.fee)} <span class="muted">= ${num(tx.gasUsed)} gas × ${num(tx.effectiveGasPrice)} wei</span>${mainnetCost(tx.gasUsed)}`,
        tx.payer && tx.payer !== tx.from ? `Paid by ${addr(tx.payer, chain)}, whose VERIFY frame approved the payment.` : ""],
      ["Gas price", perGas(tx.effectiveGasPrice), "The base fee plus the tip that the sender's limits allow."],
    ],
    details: [
      ["Base fee per gas", perGas(tx.baseFeePerGas), `The ${chain.toUpperCase()} block's EIP-1559 base fee.`],
      ...offered,
      ["Burned", eth(tx.burned), chain === "l2" ? "The base fee is burned on L2 as on L1. The ETH that backs it stays in the L1 escrow, which no L2 account can withdraw." : "The base fee, burned."],
      ["Tip", eth(tx.tip), chain === "l2" ? "Goes to the L2 block's fee recipient." : "Goes to the L1 block's proposer."],
      ...(tx.blobFee ? [["Blob fee", `${eth(tx.blobFee)} <span class="muted">= ${num(tx.blobGasUsed)} blob gas × ${num(tx.blobGasPrice)} wei</span>`, "Burned, as all blob gas is."]] : []),
    ],
  };
}

// Gas used, against the limit when the transaction has one. A frame
// transaction has limits per frame instead, which split execution gas from
// EIP-8037 state gas.
function gasRow(tx) {
  const state = tx.frames ? tx.frames.reduce((s, f) => s + (f.stateGasUsed || 0), 0) : 0;
  return [tx.gasLimit ? "Gas limit & usage" : "Gas usage",
    `${num(tx.gasUsed)}${tx.gasLimit ? ` of ${num(tx.gasLimit)} <span class="muted">(${pct(tx.gasUsed / tx.gasLimit)})</span>` : ""}${state ? ` <span class="muted">· ${num(state)} of it state gas</span>` : ""}`,
    tx.frames ? "Each frame has its own execution and state gas limits, in the Frames tab. State gas pays for new accounts and storage (EIP-8037)."
      : "Includes EIP-8037 state gas for new accounts and storage."];
}

// The logs of a transaction, in all its frames.
const logsOf = (tx) => tx.logs || (tx.frames || []).flatMap((f) => f.logs || []);

function authorizationRows(tx) {
  return `<table><thead><tr><th>#</th><th>Authority</th><th>Delegates to</th><th>Chain ID</th><th class="num">Nonce</th></tr></thead><tbody>
    ${tx.authorizations.map((a, i) => `<tr><td>${i}</td><td>${a.authority ? addr(a.authority, "l2") : '<span class="bad">invalid signature</span>'}</td>
      <td>${addr(a.address, "l2")}</td><td>${a.chainId === 0 ? "0, any chain" : a.chainId}</td><td class="num">${a.nonce}</td></tr>`).join("")}</tbody></table>`;
}

// The deposit an L2 claim delivers, or the withdrawal an L2 transaction sends.
function depositOf(tx) {
  if (tx.deposit !== undefined && tx.deposit !== null) return cachedMessage("deposits", tx.deposit);
  const frame = (tx.frames || []).find((f) => f.call && f.call.function === "claimL1Message");
  return frame ? cachedMessage("deposits", frame.call.args.m.index) : null;
}
const withdrawalOf = (tx) => cachedMessage("withdrawals", tx.withdrawal);

function counterpart(tx) {
  if (tx.kind === "deposit claim") {
    const d = depositOf(tx);
    return d && d.l1Tx ? `sent in ${l1TxLink(d.l1Tx)}` : "";
  }
  if (tx.kind === "withdrawal") {
    const w = withdrawalOf(tx);
    return w && w.l1Tx ? `claimed in ${l1TxLink(w.l1Tx)}` : '<span class="muted">not claimed on L1 yet</span>';
  }
  return "";
}

// The targets of a frame transaction, with the frames that call each.
// One row per frame: its index and mode, then its target.
function frameTargets(tx, chain, kinds = {}) {
  return `<div class="targets">${tx.frames.map((f, i) => `<span class="muted">${i} ${f.mode}</span>
    <span>${addr(f.target, chain)}${kindTag(kinds[f.target])}</span>`).join("")}</div>`;
}

const L2_SUMMARIES = {
  "deposit claim": (tx) => {
    const claim = tx.frames.find((f) => f.call && f.call.function === "claimL1Message").call.args.m;
    const proves = tx.frames.some((f) => f.call && f.call.function === "proveL1MessageRoot");
    const own = claim.to === tx.from;
    return `${own ? `${addr(claim.to, "l2")} claims a deposit of ${eth(claim.value)} and pays the fee from it. It is an EIP-8141 frame
      transaction: the claim frames run first, then the VERIFY frame approves the payment from the balance the claim
      just delivered.` : `${addr(tx.from, "l2")} claims ${hasData(claim.data) ? `a message from L1 that calls ${callOf((depositOf(tx) || {}).method, claim.data)} on ${addr(claim.to, "l2")}${withValue(claim.value)}`
      : `a deposit of ${eth(claim.value)} for ${addr(claim.to, "l2")}`}.
      ${claim.fee > 0 ? `The claimer earns the message's fee of ${eth(claim.fee)}, which covers the claim's gas, but pays the gas from its own funds: only a message to the claimer can pay for its own claim.` : "The message carries no fee, so the claimer pays the gas itself."}`}${proves ? " It also proves the root of L1's message tree, against the anchor of a recent L2 block." : ""}`;
  },
  withdrawal: (tx) => {
    const fee = Number(tx.call.args.fee || 0);
    const value = (BigInt(tx.value) - BigInt(tx.call.args.fee || 0)).toString(), data = tx.call.args.data;
    return `${addr(tx.from, "l2")} ${hasData(data) ? `sends a message to ${addr(tx.call.args.to, "l1")} on L1 that calls ${callOf((withdrawalOf(tx) || {}).method, data)}${withValue(value)}` : `withdraws ${eth(value)} to ${addr(tx.call.args.to, "l1")} on L1`}${
      fee ? `, with a fee of ${eth(tx.call.args.fee)} for whoever claims it there` : ", to claim there itself"}. The L2 messenger records the
      message, which can be claimed on L1 once this block is on L1.`;
  },
  transfer: (tx) => tx.status
    ? `${addr(tx.from, "l2")} pays ${eth(tx.value)} to ${addr(tx.to, "l2")}, in a plain ETH transfer.`
    : `A plain ETH transfer of ${eth(tx.value)} that <b>failed</b>, so no ETH moved. The transaction is still in the block and paid its fee.`,
  call: (tx) => `${addr(tx.from, "l2")} calls ${addr(tx.to, "l2")}${tx.call ? `'s <code>${tx.call.function}</code>` : ""}.`,
  deploy: (tx) => (tx.created || []).length
    ? `${addr(tx.from, "l2")} deploys ${tx.created.map((c) => `${c.name ? `a <b>${esc(c.name)}</b> contract` : "a contract"} at ${addr(c.address, "l2")}`).join(", ")}${
      tx.to ? `, through the CREATE2 factory of EIP-7997, at an address that only depends on the factory, a salt and the code` : ""}.`
    : `${addr(tx.from, "l2")} tries to deploy a contract, which <b>failed</b>.`,
  delegation: (tx) => `${addr(tx.from, "l2")} sends an EIP-7702 transaction with ${tx.authorizations.length}
    authorization${tx.authorizations.length > 1 ? "s" : ""}: each sets the code of the account that signed it to delegate to a contract,
    here ${[...new Set(tx.authorizations.map((a) => a.address))].map((a) => addr(a, "l2")).join(", ")}.`,
  "frame transaction": (tx) => `An EIP-8141 frame transaction from ${addr(tx.from, "l2")} with ${tx.frames.length} frames:
    ${tx.frames.map((f) => f.mode).join(", ")}. Its VERIFY frames validate it and approve execution and payment.`,
};

async function l2TxPage(route) {
  const tx = await object(`l2/txs/${route.hash}`);
  if (!tx) {
    const t = waitingTxs().find((t) => t.hash === route.hash);
    return `<h1>Transaction details</h1><p class="note">${t
      ? `Preconfirmed in block ${l2BlockLink(t.block)} ${ago(t.time)}, which is waiting for L1. Its details show here once the follower rebuilds the block from L1.`
      : "Not in an L2 block the follower rebuilt. A transaction shows here once its block is on L1."}</p>`;
  }
  const block = await object(`l2/blocks/${tx.block}`);
  await loadMessages([tx]);
  // The message it sends to L1 and the one it claims from L1, which apps'
  // calls may do too.
  const w = withdrawalOf(tx);
  const d = depositOf(tx);
  const frame = tx.type === 6;
  const logs = logsOf(tx);
  const fees = feeRows(tx, "l2");
  const tokens = await tokenRow(tx, "l2");
  const kinds = await kindsOf(tx, "l2");
  const position = block ? block.transactions.findIndex((t) => t.hash === tx.hash) : -1;
  const key = !frame && tx.to && tx.call && snippetKey(tx.to, tx.call.function);
  const code = key ? codeBlock(key) : "";
  const linked = [];
  if (d) {
    linked.push(["Sent on L1", d.l1Tx ? `${l1TxLink(d.l1Tx)} <span class="muted">in L1 block ${num(d.l1Block)}</span>` : "",
      `The L1 transaction that sent ${msgLink("deposit", d.index)}. The rollup contract added its hash to the message tree, which this claim proves against.`]);
  }
  if (w) {
    linked.push(["Claim on L1", w.l1Tx ? `${l1TxLink(w.l1Tx)} <span class="muted">in L1 block ${num(w.l1Block)}</span>` : '<span class="muted">not claimed yet</span>',
      `The L1 transaction that delivered ${msgLink("withdrawal", w.index)}, against an L2 state root that includes it.`]);
  }
  const base = `/l2/tx/${tx.hash}`;
  const tab = route.tab;
  const overview = `${fields([
      ["Transaction hash", hash(tx.hash, true), "Rebuilt by the follower from the L1 blob that carried its block."],
      ["Status", statusText(tx.status), frame ? "A frame transaction is included once a frame approves payment. Each frame then has its own status." : ""],
      ["Block", `${l2BlockLink(tx.block)}${block ? ` <span class="muted">posted on L1 in</span> ${l1TxLink(block.l1.tx)} <span class="muted">in L1 block ${num(block.l1.block)}</span>` : ""}`,
        "The L1 transaction's blob carries this transaction's bytes, which is where the follower read them from."],
      ["Timestamp", block ? `${ago(block.timestamp)} <span class="muted">(${new Date(block.timestamp * 1000).toLocaleString()})</span>` : "", ""],
      ["Transaction action", `<span class="prose">${(L2_SUMMARIES[tx.kind] || (() => ""))(tx)}${w && tx.kind !== "withdrawal"
        ? ` ${tx.kind === "deposit claim" ? "Delivering it" : "The call"} sent ${msgLink("withdrawal", w.index)} to L1.` : ""}</span>`, ""],
      ...(tokens ? [tokens] : []),
      [frame ? "Sender" : "From", addr(tx.from, "l2") + kindTag(kinds[tx.from]), frame ? "The account the transaction acts for." : ""],
      frame ? ["To", frameTargets(tx, "l2", kinds), "A frame transaction has no single recipient: each frame calls its own target, as the entry point, or as the sender in SENDER frames."]
        : ["To", tx.to ? addr(tx.to, "l2") + kindTag(kinds[tx.to]) : "contract creation", ""],
      ...((tx.created || []).length ? [["Created", tx.created.map((c) => addr(c.address, "l2")).join("<br>"), "Named when the explorer knows the creation code, from the ABIs it loaded."]] : []),
      ...linked,
      ...(frame ? [] : [["Value", eth(tx.value), ""]]),
      ...fees.overview,
      gasRow(tx),
    ])}
    ${more([
      ...fees.details,
      ["Transaction type", `0x0${tx.type}, ${TX_TYPES[tx.type] || "unknown"}`, frame ? "EIP-8141: a list of frames, each a call with its own mode and gas." : ""],
      ["Nonce", tx.nonce, ""],
      ["Position in block", position >= 0 ? position : "", ""],
      ["Size", `${num(tx.bytes)} bytes`, "Its share of the block's blob."],
    ])}
    ${frame ? "" : tx.to ? (tx.data !== "0x" ? `<h2>Input data</h2>${inputData(tx.data, tx.call, { chain: "l2", fn: tx.call && tx.call.function })}` : "")
      : `<h2>Init code</h2>${rawInput(tx.data, true)}`}`;
  return `
    <h1>Transaction details <span class="net l2">L2</span></h1>
    ${tabs(base, [
      ["", "Overview", undefined, overview],
      ["logs", "Logs", logs.length, events(logs, "l2")],
      frame && ["frames", "Frames", tx.frames.length, frameCards(tx, "L2")],
      tx.authorizations && ["authorizations", "Authorizations", tx.authorizations.length,
        `<p class="section-lead">EIP-7702: each authority signed that its account runs the code of the address it delegates to.</p>${authorizationRows(tx)}`],
      code && ["code", "Code", undefined, code],
    ], tab)}`;
}

// What the rollup contract checks a block's proof against, as
// NativeRollup.advance rebuilds it: SSZ roots whose leaves come from its
// storage, the calldata, BLOBHASH, BLOCKHASH and the EIP-8357 registry.
// What the proof of block `b` is checked against, field by field, for the
// pages of the block and of the L1 transaction that posted it.
const proofSection = (b, rec) => `<h2>What the proof is checked against</h2>
  <p class="section-lead">Each field its proof covers: where the rollup contract gets it on L1, and what it is on L2.</p>
  ${boundaryView(b, rec)}`;

async function l1TxPage(route) {
  const tx = await object(`l1/txs/${route.hash}`);
  if (!tx) return `<h1>L1 transaction</h1><p class="note">Not found.</p>`;
  const kinds = await kindsOf(tx, "l1");
  // The deposit this transaction sends, or the withdrawal it claims.
  const found = ["deposit", "withdrawal claim"].includes(tx.kind) ? (await getJSON(`/api/explorer/find.json?l1Tx=${tx.hash}`)) || {} : {};
  // A block post: the block, whose proof the contract checked.
  const advance = tx.kind === "advance" && tx.frames.find((f) => f.call && f.call.function === "advance");
  const checked = advance && tx.l2Block ? await object(`l2/blocks/${tx.l2Block}`) : null;
  let summary = "";
  if (tx.kind === "advance") {
    const pre = preconfirmationIn(await blockRecord(tx.l2Block));
    summary = `The operator adds L2 block ${l2BlockLink(tx.l2Block)} to the rollup${pre ? `, which it preconfirmed ${secs(tx.timestamp - pre.time)} earlier` : ""}.
      One EIP-8141 frame transaction carries the block's data in a blob, the proof in a frame, and the call to the rollup
      contract, which accepts the block only if the proof is for exactly this block.`;
  } else if (tx.kind === "deposit") {
    const d = found.deposit;
    const verb = d && hasData(d.data) ? "sends" : "deposits";
    const what = !d ? "a message to L2" : hasData(d.data)
      ? `a message to ${addr(d.to, "l2")} on L2 that calls ${callOf(d.method, d.data)}${withValue(d.value)}`
      : `${eth(d.value)} to ${addr(d.to, "l2")} on L2`;
    // An app the transaction calls may send the message itself.
    const via = d && d.from.toLowerCase() !== tx.from.toLowerCase();
    summary = `${addr(tx.from, "l1")} ${via ? `calls ${method(tx)} on ${addr(tx.to, "l1")}, which ${verb}` : verb} ${what}${
      d && Number(d.fee) ? `, with a fee of ${eth(d.fee)} for whoever claims it there` : ""}. The rollup contract adds the message
      to its Merkle tree${d && BigInt(d.value || 0) > 0n ? " and keeps the ETH in escrow" : ""}.${d && d.l2Tx ? ` It was claimed on L2 in ${l2TxLink(d.l2Tx)}, in block ${l2BlockLink(d.l2Block)}.` : ""}`;
  } else if (tx.kind === "withdrawal claim") {
    const m = tx.call.args.m;
    const w = found.withdrawal;
    summary = `${addr(tx.from, "l1")} claims ${hasData(m.data) ? `a message that calls ${callOf((w || {}).method, m.data)} on ${addr(m.to, "l1")}${withValue(m.value)}` : `a withdrawal of ${eth(m.value)} to ${addr(m.to, "l1")}`}${
      Number(m.fee) ? ` and earns its fee of ${eth(m.fee)}` : ""}. The rollup contract checks the message against the
      state root of L2 block ${l2BlockLink(tx.call.args.l2BlockNumber)} with a storage proof, and delivers it${BigInt(m.value || 0) > 0n ? ", its ETH from the escrow" : ""}.${w && w.l2Tx ? ` It was sent on L2 in ${l2TxLink(w.l2Tx)}.` : ""}${
      found.deposit ? ` Delivering it sent ${msgLink("deposit", found.deposit.index)} to L2.` : ""}`;
  }
  const frame = !!tx.frames;
  const linked = [];
  {
    const d = found.deposit;
    if (d) linked.push(["Claim on L2", d.l2Tx ? `${l2TxLink(d.l2Tx)} <span class="muted">in L2 block</span> ${l2BlockLink(d.l2Block)}` : '<span class="muted">waiting for an L2 block that anchors it</span>',
      `The L2 transaction that delivered ${msgLink("deposit", d.index)}.`]);
  }
  {
    const w = found.withdrawal;
    if (w && w.l2Tx) linked.push(["Sent on L2", `${l2TxLink(w.l2Tx)} <span class="muted">in L2 block</span> ${l2BlockLink(w.l2Block)}`, `The L2 transaction that sent ${msgLink("withdrawal", w.index)}.`]);
  }
  if (tx.kind === "advance") linked.push(["L2 block", l2BlockLink(tx.l2Block), "The L2 block this transaction adds."]);
  const logs = tx.logs || [];
  const fees = feeRows(tx, "l1");
  const tokens = await tokenRow(tx, "l1");
  const base = `/l1/tx/${tx.hash}`;
  const tab = route.tab;
  const overview = `${fields([
      ["Transaction hash", hash(tx.hash, true), "Read from the L1 node."],
      ["Status", tx.status ? `<span class="check">succeeded</span>` : `<span class="bad">failed</span>`, ""],
      ["Block", `${num(tx.block)} <span class="muted">built by ${esc(tx.builder)}</span>`, frame && tx.blobVersionedHashes.length ? "Only Nethermind and Reth accept blob-carrying frame transactions on this devnet." : ""],
      ["Timestamp", `${ago(tx.timestamp)} <span class="muted">(${new Date(tx.timestamp * 1000).toLocaleString()})</span>`, ""],
      ["Transaction action", `<span class="prose">${summary}</span>`, ""],
      ...(tokens ? [tokens] : []),
      [frame ? "Sender" : "From", addr(tx.from, "l1") + kindTag(kinds[tx.from]), ""],
      frame ? ["To", frameTargets(tx, "l1", kinds), "A frame transaction has no single recipient: each frame calls its own target, as the entry point, or as the sender in SENDER frames."]
        : ["To", addr(tx.to, "l1") + kindTag(kinds[tx.to]), ""],
      ...linked,
      ...(frame ? [] : [["Value", eth(tx.value), ""]]),
      ...fees.overview,
      gasRow(tx),
    ])}
    ${checked && checked.proofInput ? proofSection(checked, await blockRecord(tx.l2Block)) : ""}
    ${more([
      ...fees.details,
      ...(tx.blobVersionedHashes.length ? [["Blob", `${hash(tx.blobVersionedHashes[0], true)}${tx.l2Block ? `<br><a href="#/blob/${tx.l2Block}">view it decoded and raw →</a>` : ""}`, "The L2 block's data, in EIP-8142's encoding."]] : []),
      ["Transaction type", frame ? "0x06, EIP-8141 frame transaction" : `0x0${tx.type}, ${TX_TYPES[tx.type] || "unknown"}`, ""],
      ["Nonce", tx.nonce, ""],
    ])}
    ${!frame && tx.data && tx.data !== "0x" ? `<h2>Input data</h2>${inputData(tx.data, tx.call, { chain: "l1", fn: tx.call && tx.call.function, ok: tx.status === 1 })}${codeFor(tx.to, tx.call)}` : ""}`;
  return `
    <h1>Transaction details <span class="net l1">L1</span></h1>
    ${tabs(base, [
      ["", "Overview", undefined, overview],
      ["logs", "Logs", logs.length, events(logs, "l1")],
      frame && ["frames", "Frames", tx.frames.length, frameCards(tx, "L1")],
    ], tab)}`;
}

// ---------------------------------------------------------------------------
// Blobs, fetched from the beacon node and decoded here
// ---------------------------------------------------------------------------

function hexToBytes(hex) {
  const h = hex.startsWith("0x") ? hex.slice(2) : hex;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(2 * i, 2), 16);
  return out;
}
const toHex = (bytes) => "0x" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const toInt = (bytes) => (bytes.length ? BigInt(toHex(bytes)) : 0n);

// Content start, content length, and next offset of the RLP item at `offset`.
function rlpItem(bytes, offset) {
  const p = bytes[offset];
  const be = (start, n) => { let v = 0; for (let i = 0; i < n; i++) v = v * 256 + bytes[start + i]; return v; };
  if (p < 0x80) return [offset, 1, offset + 1];
  if (p < 0xb8) return [offset + 1, p - 0x80, offset + 1 + p - 0x80];
  if (p < 0xc0) { const n = p - 0xb7, len = be(offset + 1, n); return [offset + 1 + n, len, offset + 1 + n + len]; }
  if (p < 0xf8) return [offset + 1, p - 0xc0, offset + 1 + p - 0xc0];
  const n = p - 0xf7, len = be(offset + 1, n);
  return [offset + 1 + n, len, offset + 1 + n + len];
}

// An RLP item as nested arrays of byte strings.
function rlpDecode(bytes, offset = 0) {
  const [start, len, next] = rlpItem(bytes, offset);
  if (bytes[offset] >= 0xc0) {
    const items = [];
    for (let o = start; o < start + len;) {
      const r = rlpDecode(bytes, o);
      items.push(r.value);
      o = r.next;
    }
    return { value: items, next };
  }
  return { value: bytes.subarray(start, start + len), next };
}

async function loadBlob(number) {
  if (state.blobs[number]) return state.blobs[number];
  const b = await object(`l2/blocks/${number}`);
  const versionedHash = b.l1.blobVersionedHashes[0];
  if (!state.beacon) {
    const [g, spec] = await Promise.all([getJSON("/beacon/eth/v1/beacon/genesis"), getJSON("/beacon/eth/v1/config/spec")]);
    state.beacon = { genesis: Number(g.data.genesis_time), secondsPerSlot: Number(spec.data.SECONDS_PER_SLOT) };
  }
  const l1Block = await rpc("eth_getBlockByNumber", ["0x" + b.l1.block.toString(16), false]);
  const slot = Math.floor((parseInt(l1Block.timestamp, 16) - state.beacon.genesis) / state.beacon.secondsPerSlot);
  const url = `/beacon/eth/v1/beacon/blobs/${slot}?versioned_hashes=${versionedHash}`;
  const res = await getJSON(url);
  const blob = hexToBytes(res.data[0]);
  // EIP-8142: 31 bytes in each 32-byte field element, whose first byte is zero.
  const raw = new Uint8Array(4096 * 31);
  for (let i = 0; i < 4096; i++) raw.set(blob.subarray(32 * i + 1, 32 * i + 32), 31 * i);
  const u32 = (o) => ((raw[o] << 24) | (raw[o + 1] << 16) | (raw[o + 2] << 8) | raw[o + 3]) >>> 0;
  const balLength = u32(0), txLength = u32(4);
  const bal = raw.subarray(8, 8 + balLength);
  const txs = [];
  const [start, length] = rlpItem(raw, 8 + balLength);
  for (let o = start; o < start + length;) {
    const [s0, len, next] = rlpItem(raw, o);
    txs.push(raw.subarray(s0, s0 + len));
    o = next;
  }
  return (state.blobs[number] = { block: b, versionedHash, slot, url, blob, raw, balLength, txLength, bal, txs });
}

const TX_FIELDS = {
  2: ["chainId", "nonce", "maxPriorityFeePerGas", "maxFeePerGas", "gasLimit", "to", "value", "data", "accessList", "yParity", "r", "s"],
  6: ["chainId", "nonce", "sender", "frames", "signatures", "fees", "blobVersionedHashes"],
};
const INT_FIELDS = new Set(["chainId", "nonce", "maxPriorityFeePerGas", "maxFeePerGas", "gasLimit", "value", "yParity", "mode", "flags", "execution", "state", "scheme", "maxFeePerBlobGas"]);
const FRAME_FIELDS = ["mode", "flags", "target", "limits", "value", "data"];
const SIGNATURE_FIELDS = ["scheme", "signer", "msg", "signature"];
const FEE_FIELDS = ["maxPriorityFeePerGas", "maxFeePerGas", "maxFeePerBlobGas"];

// A decoded RLP value, with field names where the transaction type defines them.
function rlpTree(v, names, open = false) {
  if (!Array.isArray(v)) return "";
  const item = (x, name) => {
    if (Array.isArray(x)) {
      const sub = name === "frames" ? x.map((f, i) => [f, `frame ${i}`, FRAME_FIELDS]) : name === "signatures" ? x.map((f, i) => [f, `signature ${i}`, SIGNATURE_FIELDS])
        : name === "fees" ? null : name === "limits" ? null : x.map((f, i) => [f, String(i), null]);
      if (sub === null) return `<details><summary>${esc(name)} <span class="muted">(${x.length} items)</span></summary><div class="rlp">${rlpTree(x, name === "fees" ? FEE_FIELDS : ["execution", "state"], true)}</div></details>`;
      return `<details ${name === "frames" ? "open" : ""}><summary>${esc(name)} <span class="muted">(list of ${x.length})</span></summary><div class="rlp">${sub.map(([f, n, fields]) => Array.isArray(f) ? `<details><summary>${esc(n)}</summary><div class="rlp">${rlpTree(f, fields, true)}</div></details>` : item(f, n)).join("")}</div></details>`;
    }
    const shown = INT_FIELDS.has(name) ? toInt(x).toLocaleString("en-US") : x.length ? toHex(x) : "(empty)";
    return `<div class="rlp-row"><span class="rlp-name">${esc(name)}</span><span class="mono rlp-value">${shown.length > 140 ? shown.slice(0, 140) + `… (${x.length} bytes)` : shown}</span></div>`;
  };
  return v.map((x, i) => item(x, (names && names[i]) || String(i))).join("");
}

function balIndex(i, block) {
  const n = block.transactions.length;
  if (i === 0) return "before the transactions";
  if (i === n + 1) return "after the transactions";
  const t = block.transactions[i - 1];
  return t ? `after tx ${l2TxLink(t.hash)}` : `index ${i}`;
}

function balTable(bal, block) {
  const accounts = rlpDecode(bal).value;
  return `<table><thead><tr><th>Account</th><th>What the block changed or read</th></tr></thead><tbody>
    ${accounts.map(([address, storage, reads, balances, nonces, codes]) => {
      const items = [];
      storage.forEach(([slot, changes]) => changes.forEach(([i, value]) =>
        items.push(`storage slot <span class="mono">${short(toHex(slot))}</span> set to <span class="mono">${short(toHex(value)) || "0"}</span>, ${balIndex(Number(toInt(i)), block)}`)));
      reads.forEach((slot) => items.push(`storage slot <span class="mono">${short(toHex(slot))}</span> read`));
      balances.forEach(([i, value]) => items.push(`balance ${eth(toInt(value).toString())}, ${balIndex(Number(toInt(i)), block)}`));
      nonces.forEach(([i, value]) => items.push(`nonce ${toInt(value)}, ${balIndex(Number(toInt(i)), block)}`));
      codes.forEach(([i, code]) => items.push(`code set, ${num(code.length)} bytes, ${balIndex(Number(toInt(i)), block)}`));
      return `<tr><td>${addr(toHex(address), "l2")}</td><td>${items.join("<br>") || '<span class="muted">accessed, unchanged</span>'}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// Each 32-byte field element, colored by what its bytes hold.
function rawBlob(d) {
  const regions = [[0, 8, "hdr"], [8, 8 + d.balLength, "bal"], [8 + d.balLength, 8 + d.balLength + d.txLength, "txs"]];
  const used = 8 + d.balLength + d.txLength;
  const elements = Math.ceil(used / 31);
  const lines = [];
  for (let e = 0; e < elements; e++) {
    let line = `<span class="fe-index">${String(e).padStart(4, " ")}</span> <span class="fe-zero">00</span>`;
    for (let k = 0; k < 31; k++) {
      const p = 31 * e + k;
      const region = (regions.find(([a, b]) => p >= a && p < b) || [0, 0, "pad"])[2];
      line += `<span class="fe-${region}">${d.raw[p].toString(16).padStart(2, "0")}</span>`;
    }
    lines.push(line);
  }
  return `<pre class="code raw">${lines.join("\n")}\n<span class="muted">… ${num(4096 - elements)} more field elements, all zero: the rest of the blob.</span></pre>`;
}

async function blobPage(route) {
  let d;
  try {
    d = await loadBlob(route.n);
  } catch (e) {
    return `<h1>Blob of L2 block #${route.n}</h1><p class="note">Could not fetch the blob from the beacon node: ${esc(e)}</p>`;
  }
  const b = d.block;
  const used = 8 + d.balLength + d.txLength;
  return `
    <div class="row-links"><a href="#/l2/block/${b.number}">← L2 block #${b.number}</a></div>
    <h1>Blob of L2 block #${b.number} ${badge("real")}</h1>
    <div class="callout"><p>L1 transaction ${l1TxLink(b.l1.tx)} carried this blob, fetched just now from the beacon node. It
      holds the block's transactions and block access list in EIP-8142's encoding: 31 bytes of data in each 32-byte field
      element, whose first byte stays zero so that every element is a valid field element for KZG.</p></div>
    ${fields([
      ["Versioned hash", hash(d.versionedHash, true), "Commits to the blob. The rollup contract reads it with BLOBHASH and binds it into the proof's input."],
      ["L1 transaction", l1TxLink(b.l1.tx), ""],
      ["Where", `L1 block ${num(b.l1.block)}, beacon slot ${num(d.slot)}`, "The consensus layer serves blobs by slot. Nodes keep them for about 18 days."],
      ["Data", `${num(used)} of ${num(BLOB_USABLE_BYTES)} usable bytes<div class="fill"><span style="width:${Math.max(0.4, (100 * used) / BLOB_USABLE_BYTES)}%"></span></div>`, "The rest is zero padding: every L2 block takes a whole blob."],
      ["Raw", `<a href="${d.url}" target="_blank">the blob as the beacon node returns it</a>`, "131,072 bytes, as JSON."],
    ])}

    <h2>Decoded</h2>
    <h3>EIP-8142 header</h3>
    ${fields([
      ["Access list length", `${num(d.balLength)} bytes`, "The first 4 bytes."],
      ["Transactions length", `${num(d.txLength)} bytes`, "The next 4. The access list comes first so that a node can read it without the transactions."],
    ])}
    <h3>Block access list</h3>
    <p class="section-lead">EIP-7928's list of every account the block touched, and how. Indexes 1 to ${b.transactions.length} are the transactions; 0 and ${b.transactions.length + 1} are the system calls before and after them, such as the one that stores the L1 anchor.</p>
    ${balTable(d.bal, b)}
    <h3>Transactions</h3>
    <p class="section-lead">An RLP list of the block's transactions, each in its EIP-2718 encoding: a type byte, then its fields.</p>
    ${d.txs.map((tx, i) => {
      const t = b.transactions[i];
      const decoded = rlpDecode(tx, 1).value;
      return `<div class="frame"><div class="frame-head"><span class="idx">Transaction ${i}</span>
          <span class="mode">type 0x${tx[0].toString(16).padStart(2, "0")}</span>${tx[0] === 6 ? "frame transaction" : tx[0] === 2 ? "EIP-1559" : ""}
          <span class="muted">${num(tx.length)} bytes</span><span class="status">${t ? l2TxLink(t.hash) : ""}</span></div>
        <div class="frame-body rlp">${rlpTree(decoded, TX_FIELDS[tx[0]])}</div></div>`;
    }).join("")}

    <h2>Raw bytes</h2>
    <p class="section-lead">One line per 32-byte field element. <span class="fe-zero">00</span> is the zero byte that starts each element,
      then <span class="fe-hdr">header</span>, <span class="fe-bal">access list</span> and <span class="fe-txs">transactions</span>.</p>
    ${rawBlob(d)}`;
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

document.getElementById("search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = e.target.q.value.trim().toLowerCase();
  if (/^#?\d+$/.test(q)) location.hash = `#/l2/block/${q.replace("#", "")}`;
  else if (/^0x[0-9a-f]{40}$/.test(q)) location.hash = `#/address/${(await object(`l2/accounts/${q}`)) ? "l2" : "l1"}/${q}`;
  else if (/^0x[0-9a-f]{64}$/.test(q)) {
    if (await object(`l1/txs/${q}`)) location.hash = `#/l1/tx/${q}`;
    else if (await object(`l2/txs/${q}`)) location.hash = `#/l2/tx/${q}`;
    else document.getElementById("app").innerHTML = `<h1>Not found</h1><p class="note">No rollup transaction with hash ${esc(q)}.</p>`;
  }
  e.target.q.value = "";
});

// After navigating, the top of the page, or the part a link points at,
// below the sticky header, for a moment highlighted.
function scrollToTarget() {
  const at = parseRoute().at;
  const target = at && document.querySelector(`[data-at="${CSS.escape(at)}"]`);
  if (!target) return window.scrollTo(0, 0);
  window.scrollTo(0, target.getBoundingClientRect().top + window.scrollY - document.querySelector(".top").offsetHeight - 12);
  target.classList.add("target");
  setTimeout(() => target.classList.remove("target"), 2000);
}
window.addEventListener("hashchange", () => render().then(scrollToTarget));
refresh().then(render).then(scrollToTarget);
setInterval(refresh, 4000);
