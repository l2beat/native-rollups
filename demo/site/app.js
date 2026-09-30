// An annotated explorer for the native rollup demo.
//
// L2 blocks and transactions come from the follower, which rebuilds them
// from L1 alone (/api/explorer). L1 transactions are decoded by the same
// follower from the L1 node. Live chain state is read directly through /rpc
// and /beacon. The operator's own records (/api/session) only add what never
// reaches L1, such as its run of the validation program.

const BLOB_USABLE_BYTES = 4096 * 31;
const PAGE = 25;
const BOOK = "http://127.0.0.1:3000";
const SELECTORS = {
  blockNumber: "0x57e871e7", blockHash: "0xf22a195e", stateRoot: "0x9588eca2", l1MessageCount: "0x1214990d",
  l1MessageRoot: "0xdf06c677", anchorBlockNumber: "0x3cad82ff", chainId: "0x9a8a0592", gasLimit: "0xf68016b7",
  l2Messenger: "0xf5730a72", evmVkRegistry: "0x369e4ac9", prover: "0x32a8f30f",
};

const state = { index: null, session: null, l1Head: null, rollupHead: null, cache: {}, blobs: {}, beacon: null, snippets: null, sources: null, flat: null };

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
  const [index, session, head] = await Promise.all([getJSON("/api/explorer/index.json"), getJSON("/api/session"), rpc("eth_blockNumber")]);
  if (index && state.index && index.rollup !== state.index.rollup) state.cache = {}; // a new episode
  state.index = index;
  state.session = session;
  state.l1Head = head ? parseInt(head, 16) : null;
  if (index) {
    const n = await rpc("eth_call", [{ to: index.rollup, data: SELECTORS.blockNumber }, "latest"]);
    state.rollupHead = n ? parseInt(n, 16) : null;
  }
  renderStatus();
  const route = parseRoute();
  if (["home", "blocks", "l1list", "messages", "address"].includes(route.page)) render();
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const short = (h) => (h && h.length > 18 ? `${h.slice(0, 10)}…${h.slice(-6)}` : h || "");
const num = (n) => Number(n).toLocaleString("en-US");
// Amounts in ETH, or in wei when too small to show in ETH.
const eth = (wei) => {
  const n = Number(wei);
  return n > 0 && n < 1e12 ? `${n.toLocaleString("en-US")} wei` : `${(n / 1e18).toLocaleString("en-US", { maximumFractionDigits: 6 })} ETH`;
};
const badge = (kind, text) => `<span class="badge ${kind}">${text || kind}</span>`;
const pct = (x) => `${(100 * x).toFixed(x < 0.1 ? 1 : 0)}%`;
const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
const hash = (h, full = false) => `<span class="mono" title="${esc(h)}">${esc(full ? h : short(h))}</span>`;
// Every link to a block, transaction or address says which chain it is on.
const net = (chain) => `<span class="net ${chain}">${chain.toUpperCase()}</span>`;
const l2BlockLink = (n) => `<span class="nowrap">${net("l2")}<a href="#/l2/block/${n}">#${n}</a></span>`;
const l2TxLink = (h) => `<span class="nowrap">${net("l2")}<a class="mono" href="#/l2/tx/${h}" title="${h}">${short(h)}</a></span>`;
const l1TxLink = (h) => `<span class="nowrap">${net("l1")}<a class="mono" href="#/l1/tx/${h}" title="${h}">${short(h)}</a></span>`;
const fill = (bytes) => {
  const x = bytes / BLOB_USABLE_BYTES;
  return `<span class="minifill"><span style="width:${Math.max(2, 100 * x)}%"></span></span>${pct(x)}`;
};

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
  };
  const add = (a, name, kind) => a && (l[a.toLowerCase()] = [name, kind]);
  add(c.rollup, "Rollup contract", "real");
  add(c.l2Messenger, "L2 messenger", "real");
  add(c.verifier, "Proof checker", "mock");
  add(c.registry, "Key registry", "mock");
  add(c.prover, "Trusted prover key", "mock");
  add(c.operator, "Operator", "");
  add(c.framesHelper, "Frames helper", "");
  // Alice uses one key, so one address, on both chains.
  add(c.aliceL1, c.aliceL1 && c.aliceL2 && c.aliceL1.toLowerCase() === c.aliceL2.toLowerCase() ? "Alice" : "Alice on L1", "");
  if (c.aliceL2 && c.aliceL2.toLowerCase() !== (c.aliceL1 || "").toLowerCase()) add(c.aliceL2, "Alice on L2", "");
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
  };
  const m = ends[ctx.event] || ends[ctx.fn];
  return (m && m[key]) || c;
}

const L2_KINDS = { "deposit claim": "deposit", withdrawal: "withdrawal" };
const chip = (kind, n) => `<span class="chip ${L2_KINDS[kind] || ""}">${n ? `${n} ` : ""}${kind}${n > 1 ? "s" : ""}</span>`;
const contents = (kinds) =>
  ["deposit claim", "withdrawal", "transfer", "call"].filter((k) => kinds[k]).map((k) => chip(k, kinds[k])).join("") || '<span class="muted">empty</span>';
const L1_KINDS = { advance: "adds an L2 block", deposit: "deposit", "withdrawal claim": "withdrawal claim" };

function fields(rows) {
  return `<table class="fields"><tbody>${rows
    .map(([label, value, note]) => `<tr><td>${label}</td><td class="value">${value}</td><td class="note">${note || ""}</td></tr>`)
    .join("")}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!parts.length) return { page: "home" };
  if (parts[0] === "blocks") return { page: "blocks", n: Number(parts[1] || 1) };
  if (parts[0] === "l1" && parts[1] === "tx") return { page: "l1tx", hash: parts[2] };
  if (parts[0] === "l1") return { page: "l1list", n: Number(parts[1] || 1) };
  if (parts[0] === "l2" && parts[1] === "block") return { page: "block", n: Number(parts[2]) };
  if (parts[0] === "l2" && parts[1] === "tx") return { page: "l2tx", hash: parts[2] };
  if (parts[0] === "messages") return { page: "messages" };
  if (parts[0] === "blob") return { page: "blob", n: Number(parts[1]) };
  if (parts[0] === "address") return { page: "address", chain: parts[1], address: (parts[2] || "").toLowerCase() };
  if (parts[0] === "about") return { page: "about" };
  return { page: "missing" };
}

async function render() {
  const app = document.getElementById("app");
  const route = parseRoute();
  if (!state.index && route.page !== "about") {
    app.innerHTML = `<p class="note">Waiting for the follower's first data. The demo starts by deploying a rollup.</p>`;
    return;
  }
  const pages = { home, blocks: blockList, l1list: l1List, block: blockPage, l2tx: l2TxPage, l1tx: l1TxPage, messages, about, address: addressPage, blob: blobPage };
  const html = await (pages[route.page] || (() => `<h1>Not found</h1>`))(route);
  if (parseRoute().page === route.page) app.innerHTML = html;
}

function renderStatus() {
  const pills = [];
  if (state.l1Head != null) pills.push(`<span class="pill"><span class="dot"></span>L1 block ${num(state.l1Head)}</span>`);
  if (state.rollupHead != null) pills.push(`<span class="pill">L2 block ${num(state.rollupHead)} on L1</span>`);
  if (state.index && state.rollupHead != null) {
    const n = state.index.l2Blocks.length;
    pills.push(n >= state.rollupHead
      ? `<span class="pill ok">follower rebuilt all ${num(n)} from L1</span>`
      : `<span class="pill warn">follower rebuilt ${num(n)} of ${num(state.rollupHead)}</span>`);
  }
  if (state.session) pills.push(`<a class="pill" href="#/about" title="The demo deploys a fresh rollup every 150 L2 blocks">rollup deployed ${ago(state.session.startedAt)}</a>`);
  document.getElementById("status").innerHTML = pills.join("");
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

function l1Rows(txs) {
  return `<table><thead><tr><th>Transaction</th><th>What it does</th><th class="num">L1 block</th><th class="hide-narrow">Age</th>
    <th class="num">Gas</th><th>Related</th></tr></thead><tbody>
    ${txs.map((t) => `<tr><td>${l1TxLink(t.hash)}</td><td>${esc(L1_KINDS[t.kind] || t.kind)}</td><td class="num">${num(t.block)}</td>
      <td class="hide-narrow">${ago(t.timestamp)}</td><td class="num">${num(t.gasUsed)}</td>
      <td>${t.l2Block ? `L2 block ${l2BlockLink(t.l2Block)}` : ""}</td></tr>`).join("")}</tbody></table>`;
}

function messageRows(kind) {
  const entries = Object.values(state.index[kind]).sort((a, b) => b.index - a.index);
  if (!entries.length) return `<p class="note">None yet.</p>`;
  if (kind === "deposits") {
    return `<table><thead><tr><th>#</th><th class="num">Amount</th><th>From (L1)</th><th>To (L2)</th><th>Sent on L1</th><th>Claimed on L2</th></tr></thead><tbody>
      ${entries.map((d) => `<tr><td>${d.index}</td><td class="num">${d.value ? eth(d.value) : ""}</td><td>${addr(d.from, "l1")}</td><td>${addr(d.to, "l2")}</td>
        <td>${d.l1Tx ? l1TxLink(d.l1Tx) : ""}</td><td>${d.l2Tx ? `${l2TxLink(d.l2Tx)} in ${l2BlockLink(d.l2Block)}` : '<span class="muted">waiting for an L2 block that anchors it</span>'}</td></tr>`).join("")}</tbody></table>`;
  }
  return `<table><thead><tr><th>#</th><th class="num">Amount</th><th>From (L2)</th><th>To (L1)</th><th>Sent on L2</th><th>Claimed on L1</th></tr></thead><tbody>
    ${entries.map((w) => `<tr><td>${w.index}</td><td class="num">${w.value ? eth(w.value) : ""}</td><td>${addr(w.from, "l2")}</td><td>${addr(w.to, "l1")}</td>
      <td>${w.l2Tx ? `${l2TxLink(w.l2Tx)} in ${l2BlockLink(w.l2Block)}` : ""}</td><td>${w.l1Tx ? l1TxLink(w.l1Tx) : '<span class="muted">not claimed yet</span>'}</td></tr>`).join("")}</tbody></table>`;
}

function pager(route, total, base) {
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const n = Math.min(Math.max(route.n, 1), pages);
  return `<div class="pager">${n > 1 ? `<a href="#/${base}/${n - 1}">← Newer</a>` : ""}<span class="muted">Page ${n} of ${pages}</span>${n < pages ? `<a href="#/${base}/${n + 1}">Older →</a>` : ""}</div>`;
}

function home() {
  const ix = state.index;
  const blocks = [...ix.l2Blocks].reverse();
  const l1 = [...ix.l1Txs].sort((a, b) => b.block - a.block);
  return `
    <h1>A native rollup, explained as it runs</h1>
    <p class="lead">A native rollup is an L2 whose blocks Ethereum checks with its own proof program, the one it will use
      for its own blocks. This explorer shows one running on a local copy of frames-devnet-0, with notes on every field.
      Its L2 data is rebuilt from L1 alone by an independent node, so everything here is what anyone could reconstruct
      from Ethereum.</p>
    <div class="legend">${badge("real")} runs as specified ${badge("mock")} stands in for an L1 feature that does not exist yet
      ${badge("shortcut")} a simplification of this demo · <a href="#/about">details</a></div>
    <div class="stats">
      <div class="stat"><div class="label">L2 blocks on L1</div><div class="value">${num(state.rollupHead ?? blocks.length)}</div></div>
      <div class="stat"><div class="label">Rebuilt from L1</div><div class="value">${num(blocks.length)}</div></div>
      <div class="stat"><div class="label">Deposits</div><div class="value">${num(Object.keys(ix.deposits).length)}</div></div>
      <div class="stat"><div class="label">Withdrawals</div><div class="value">${num(Object.keys(ix.withdrawals).length)}</div></div>
    </div>
    <h2>Latest L2 blocks</h2>
    <div class="row-links"><a href="#/blocks">All blocks →</a></div>
    ${blockRows(blocks.slice(0, 8))}
    <h2>Latest rollup transactions on L1</h2>
    <div class="row-links"><a href="#/l1">All L1 transactions →</a></div>
    ${l1Rows(l1.slice(0, 6))}`;
}

function blockList(route) {
  const blocks = [...state.index.l2Blocks].reverse();
  const n = Math.max(route.n, 1);
  return `<h1>L2 blocks</h1>
    <p class="section-lead">Every L2 block the rollup contract accepted, newest first. Each row has the same columns: what
      the block contains, the L1 transaction that carries it, how much of its blob it fills, and whether the follower rebuilt
      it from L1 with the same hash.</p>
    ${pager(route, blocks.length, "blocks")}${blockRows(blocks.slice((n - 1) * PAGE, n * PAGE))}${pager(route, blocks.length, "blocks")}`;
}

function l1List(route) {
  const txs = [...state.index.l1Txs].sort((a, b) => b.block - a.block);
  const n = Math.max(route.n, 1);
  return `<h1>Rollup transactions on L1</h1>
    <p class="section-lead">The L1 transactions that involve the rollup contract: one per L2 block, and the deposits and
      withdrawal claims.</p>
    ${pager(route, txs.length, "l1")}${l1Rows(txs.slice((n - 1) * PAGE, n * PAGE))}${pager(route, txs.length, "l1")}`;
}

function messages() {
  return `<h1>Deposits and withdrawals</h1>
    <p class="lead">All L2 ETH comes from deposits: the L2 starts with no ETH outside a pre-minted supply held by the L2
      messenger, which only L1 deposits release, so the L1 escrow backs every L2 ETH.</p>
    <h2>Deposits, L1 to L2</h2>
    <p class="section-lead">The rollup contract adds each deposit to a Merkle tree of messages. On L2, a claim proves it
      against the tree's root in an L1 block the L2 anchored, and the deposit pays for its own claim.</p>
    ${messageRows("deposits")}
    <h2>Withdrawals, L2 to L1</h2>
    <p class="section-lead">The L2 messenger records each withdrawal in its storage. Once the block is on L1, the rollup
      contract pays it from its escrow, against a storage proof of the message in a recent L2 state root.</p>
    ${messageRows("withdrawals")}`;
}

function about() {
  const c = (state.session && state.session.contracts) || {};
  return document.getElementById("about").innerHTML + (c.rollup ? `
    <h2>Check it yourself</h2>
    <table><thead><tr><th>Contract or account</th><th>Chain</th><th>Address</th><th></th></tr></thead><tbody>
      ${[
        ["Rollup contract", "l1", c.rollup, "real"], ["L2 messenger", "l2", c.l2Messenger, "real"],
        ["Proof checker", "l1", c.verifier, "mock"], ["Key registry", "l1", c.registry, "mock"],
        ["Trusted prover key", "l1", c.prover, "mock"], ["Operator", "l1", c.operator, "shortcut"],
        ["Alice", "l1", c.aliceL1, ""], ["Alice", "l2", c.aliceL2, ""],
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

async function blockPage(route) {
  const b = await object(`l2/blocks/${route.n}`);
  if (!b) return `<h1>L2 block #${route.n}</h1><p class="note">The follower has not rebuilt this block yet.</p>`;
  const rec = state.session && state.session.events.find((e) => e.type === "advance" && e.l2.number === b.number);
  const txs = await Promise.all(b.transactions.map((t) => object(`l2/txs/${t.hash}`)));
  const kinds = {};
  b.transactions.forEach((t) => (kinds[t.kind] = (kinds[t.kind] || 0) + 1));
  const matches = b.hash === b.recordedHash;
  const last = state.index.l2Blocks.length;
  return `
    <div class="row-links">${b.number > 1 ? `<a href="#/l2/block/${b.number - 1}">← Block ${b.number - 1}</a>` : ""}
      ${b.number < last ? `<a href="#/l2/block/${b.number + 1}">Block ${b.number + 1} →</a>` : ""}</div>
    <h1>L2 block #${b.number}</h1>
    <div class="callout"><p>This block holds ${contents(kinds)}. It reached L1 in transaction ${l1TxLink(b.l1.tx)}, in L1 block
      ${num(b.l1.block)}, whose blob carries its transactions. An independent follower rebuilt it from that L1 data alone and
      got ${matches ? `the hash the rollup contract recorded <span class="check">✓</span>` : `<span class="bad">a different hash</span>`}.</p></div>

    <h2>How it got to L1</h2>
    <ol class="journey">
      <li><b>1. Build</b>${badge("real")} ${badge("shortcut")}<br>The operator executed ${b.transactions.length} transactions with Ethereum's rules.</li>
      <li><b>2. Check</b>${badge("real")}<br>${rec ? `The operator ran Ethereum's validation program: accepted <span class="check">✓</span>` : "The operator ran Ethereum's validation program."}</li>
      <li><b>3. Prove</b>${badge("mock")}<br>A trusted key signed the result, in place of a zk proof.</li>
      <li><b>4. Post</b>${badge("real")}<br>${l1TxLink(b.l1.tx)} carried it, using ${pct(b.payloadBytes / BLOB_USABLE_BYTES)} of a blob.</li>
      <li><b>5. Verify</b>${badge("real")}<br>The rollup contract checked the proof's input and accepted it.</li>
      <li><b>6. Follow</b>${badge("real")}<br>Rebuilt from L1 alone, ${matches ? `same hash <span class="check">✓</span>` : `<span class="bad">different hash</span>`}</li>
    </ol>

    <h2>Transactions</h2>
    <table><thead><tr><th>#</th><th>Hash</th><th>What it does</th><th>Linked on L1</th><th>From</th><th class="num">Gas used</th><th class="num">Bytes</th></tr></thead><tbody>
      ${txs.map((t, i) => `<tr><td>${i}</td><td>${l2TxLink(t.hash)}</td><td>${chip(t.kind)}</td><td>${counterpart(t)}</td><td>${addr(t.from, "l2")}</td>
        <td class="num">${num(t.gasUsed)}</td><td class="num">${num(t.bytes)}</td></tr>`).join("")}</tbody></table>

    <h2>Header</h2>
    <p class="section-lead">Ethereum's block header, as the follower rebuilt it. The notes say where each field comes from and what checks it.</p>
    ${fields([
      ["Block hash", hash(b.hash, true), "Rebuilt by the follower. The rollup contract recorded " + (matches ? "the same hash." : "a different one.")],
      ["Number", b.number, "From the rollup contract's storage: one more than the last block it accepted."],
      ["Parent hash", hash(b.parentHash, true), "From the contract's storage, so a block can only extend the chain the contract has."],
      ["Timestamp", `${b.timestamp} <span class="muted">(${new Date(b.timestamp * 1000).toLocaleTimeString()})</span>`, "Chosen by the operator. It must increase, and the contract keeps it at or below L1 time: a block at the maximum timestamp would halt the chain."],
      ["State root", hash(b.stateRoot, true), "Claimed by the operator, checked by the proof. The contract keeps the last 8,191 state roots, for withdrawals."],
      ["Receipts root", hash(b.receiptsRoot, true), "Claimed by the operator, checked by the proof."],
      ["Transactions root", hash(b.transactionsRoot, true), `The header's trie root. The contract gets the transactions' SSZ root, ${hash(b.sszRoots.transactionsRoot)}, and the transactions are in the blob.`],
      ["Gas used / limit", `${num(b.gasUsed)} / ${num(b.gasLimit)}`, "The limit is fixed when the rollup contract is deployed."],
      ["Base fee", `${num(b.baseFeePerGas)} wei`, "EIP-1559 applies on L2 as on L1."],
      ["L1 anchor", `${hash(b.parentBeaconBlockRoot, true)}<br><span class="muted">L1 block ${num(b.anchorBlockNumber)}</span>`, "The parent_beacon_block_root field, repurposed: the hash of an L1 block the operator picked, which the contract checks with BLOCKHASH. L2 contracts read it through the EIP-4788 contract and prove L1 state against it, as deposit claims do."],
      ["Fee recipient", addr(b.feeRecipient, "l2"), "A free input of the operator, set by the consensus layer on L1."],
      ["prev_randao", hash(b.prevRandao, true), "A free input of the operator, so not randomness on L2. The demo uses the hash of the anchor."],
      ["Extra data", `${hash(b.extraData)} <span class="muted">"${esc(hexToText(b.extraData))}"</span>`, "A free input of the operator."],
      ["Withdrawals root", hash(b.withdrawalsRoot, true), "Always empty: the L2 has no beacon chain, and fake withdrawals would mint ETH."],
      ["Blob gas used, excess", `${b.blobGasUsed}, ${b.excessBlobGas}`, "Always 0: the L2 has no blob transactions."],
      ["Requests hash", hash(b.requestsHash, true), "EIP-7685 requests. L2 users can create them, but they do nothing on L2, so the contract accepts any."],
      ["Access list hash", hash(b.blockAccessListHash, true), `EIP-7928's block access list. Its ${num(b.balBytes)} bytes travel in the blob, and the contract gets its SSZ root.`],
      ["Slot number", b.slotNumber, "Fixed to 0 for now. The L2 value is not defined yet."],
    ])}

    <h2>Data on L1</h2>
    ${fields([
      ["L1 transaction", `${l1TxLink(b.l1.tx)}`, "One frame transaction per L2 block: it carries the blob, the proof, and the call to the rollup contract."],
      ["Blob", hash(b.l1.blobVersionedHashes[0], true), "Its versioned hash. The contract reads it with BLOBHASH and binds it into the proof's input."],
      ["Blob use", `${num(b.payloadBytes)} of ${num(BLOB_USABLE_BYTES)} bytes<div class="fill"><span style="width:${Math.max(0.4, (100 * b.payloadBytes) / BLOB_USABLE_BYTES)}%"></span></div>`, "EIP-8142 gives every block its own blobs, however small the block."],
    ])}
    <p><a class="button" href="#/blob/${b.number}">View the blob: decoded and raw →</a></p>

    ${rec ? `<h2>On the operator's side</h2>
    <div class="callout"><p>What the operator's node did before posting the block. None of it is on L1, and the follower's
      rebuild does not rely on it.</p></div>
    ${fields([
      ["Validation program", `<code>verify_stateless_new_payload</code> ${badge("real")}`, "Ethereum's stateless validation program from execution-specs, with EIP-8141, run as ordinary code instead of inside a zkVM."],
      ["Result", `successful_validation = ${rec.l2.validation.successful}`, "The prover only signs blocks the program accepted."],
      ["Chain ID, schema ID", `${rec.l2.validation.chainId}, 0x${rec.l2.validation.schemaId.toString(16)}`, "Part of what the proof commits to, so a block cannot be proven under another chain's rules."],
      ["public_input_root", hash(rec.l2.publicInputRoot, true), "What the proof commits to. It is the data hash in the L1 transaction's proof frame."],
      ["Blob check", `node-side ${badge("mock")}`, "The node checked that the blob encodes the block, which the L1 program does not do yet (EIP-8142)."],
    ])}` : ""}`;
}

function hexToText(hex) {
  const bytes = hex.slice(2).match(/../g) || [];
  return bytes.map((b) => parseInt(b, 16)).map((c) => (c >= 32 && c < 127 ? String.fromCharCode(c) : ".")).join("");
}

// ---------------------------------------------------------------------------
// Addresses
// ---------------------------------------------------------------------------

const ROLES = {
  "Rollup contract": ["real", "The native rollup's contract on L1. It adds each L2 block whose proof is for exactly that block, stores the L2 chain's head and its last 8,191 state roots, keeps the tree of L1 to L2 messages, holds the ETH deposits escrow, and pays withdrawals."],
  "L2 messenger": ["real", "An L2 contract in the genesis that holds the pre-minted supply of L2 ETH. It releases ETH for deposits, which it proves against L1's message tree, and records withdrawals for L1. Its balance is the ETH that deposits have not released yet."],
  "Proof checker": ["mock", "Stands in for EIP-8288. A frame of each L1 transaction that adds a block calls it, and it checks that the trusted prover signed the dependency. With EIP-8288, Ethereum's own proof would cover the dependency instead."],
  "Key registry": ["mock", "Stands in for the EIP-8357 registry of EVM verification keys. An admin registered the key, where a fork would."],
  "Trusted prover key": ["mock", "The key that signs the blocks Ethereum's validation program accepted, in place of a zk proof. It never sends transactions."],
  "Operator": ["shortcut", "The account that posts L2 blocks to L1. The rollup contract accepts a valid block from anyone, but this demo has one operator."],
  "Frames helper": ["shortcut", "Lets the rollup contract use EIP-8141's FRAMEPARAM and FRAMEDATACOPY instructions, which Solidity cannot emit yet. The rollup contract deploys it and calls it to read the proof frame. Written in assembly with geas."],
  Alice: ["", "The demo's user. She uses the same key on L1 and L2, so she has the same address on both."],
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
      await Promise.all(["blockNumber", "blockHash", "stateRoot", "l1MessageCount", "l1MessageRoot", "anchorBlockNumber", "chainId", "gasLimit", "l2Messenger", "evmVkRegistry"].map(async (k) => (r[k] = await call(a, SELECTORS[k]))));
      live = [
        ["L2 head", `block ${l2BlockLink(wordNumber(r.blockNumber))}, ${hash(r.blockHash)}`, "The last L2 block the contract accepted. The next one must build on it."],
        ["Latest state root", hash(r.stateRoot, true), "Of the head block. The contract keeps the last 8,191, for withdrawals."],
        ["L1 anchor", `L1 block ${num(wordNumber(r.anchorBlockNumber))}`, "The last anchor. The next block's anchor cannot be older."],
        ["Messages sent to L2", num(wordNumber(r.l1MessageCount)), "Deposits and other L1 to L2 messages, in the message tree."],
        ["Message tree root", hash(r.l1MessageRoot, true), "What L2 proves deposits against, through its L1 anchor."],
        ["Chain ID, gas limit", `${wordNumber(r.chainId)}, ${num(wordNumber(r.gasLimit))}`, "Fixed at deployment."],
        ["L2 messenger", addr(wordAddress(r.l2Messenger), "l2"), "Whose storage the contract proves withdrawals against."],
        ["Key registry", addr(wordAddress(r.evmVkRegistry), "l1"), "Where the contract reads the verification key."],
      ];
    } else if (a === (c.registry || "").toLowerCase()) {
      const entry = await call(a, "0x" + "0".repeat(64));
      live = [
        ["Current key hash", hash(word(entry), true), "The EVM verification key for the current fork. Proofs must be under it."],
        ["Schema ID", `0x${wordNumber(word(entry, 1)).toString(16)}`, "The input schema the key's program expects: Amsterdam, revision 1."],
      ];
    } else if (a === (c.verifier || "").toLowerCase()) {
      live = [["Prover", addr(wordAddress(await call(a, SELECTORS.prover)), "l1"), "The only key whose signatures it accepts."]];
    }
    const txs = state.index.l1Txs.filter((t) => (t.addresses || []).includes(a)).sort((x, y) => y.block - x.block);
    history = `<h2>Rollup transactions on L1</h2>${txs.length ? l1Rows(txs.slice(0, 50)) : '<p class="note">None.</p>'}`;
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
          ["L1 rollup contract", addr(acc.state.l1Rollup, "l1"), "Whose message tree deposits are proven against. Set in the genesis."],
          ["Withdrawals sent", num(acc.state.sentMessages), "Messages in its queue, which L1 proves against L2 state roots."],
          ["Last proven message root", hash(acc.state.provenL1MessageRoot, true), `Proven against the anchor of the L2 block with timestamp ${acc.state.provenAnchorTimestamp}.`],
        ];
      }
      const txs = [...acc.txs].reverse();
      history = `<h2>L2 transactions</h2>${txs.length ? `<table><thead><tr><th>Transaction</th><th>Block</th><th>What it does</th></tr></thead><tbody>
        ${txs.slice(0, 50).map((t) => `<tr><td>${l2TxLink(t.hash)}</td><td>${l2BlockLink(t.block)}</td><td>${chip(t.kind)}</td></tr>`).join("")}</tbody></table>` : '<p class="note">None.</p>'}`;
    }
  }
  const other = a === (c.aliceL1 || "").toLowerCase() || a === (c.aliceL2 || "").toLowerCase()
    ? `<p class="note">The same address on <a href="#/address/${OTHER[chain]}/${a}">${OTHER[chain].toUpperCase()}</a>.</p>` : "";
  const bytecode = chain === "l1" ? l1Code : acc && acc.code;
  const code = sourceSection(a, name, bytecode, chain);
  return `
    <h1>${esc(name)} <span class="muted">on ${chain.toUpperCase()}</span> ${kind ? badge(kind) : ""}</h1>
    <p class="mono">${a}</p>
    ${role ? `<div class="callout ${role[0] === "mock" ? "mock" : ""}"><p>${role[1]}</p></div>` : ""}
    ${other}
    ${fields(rows)}
    ${live.length ? `<h2>State</h2><p class="section-lead">${chain === "l1" ? "Read from the contract on L1 now." : "From the follower's rebuilt L2 state."}</p>${fields(live)}` : ""}
    ${history}
    ${code}`;
}

// The source file each contract of the demo is built from.
function mainSource(a) {
  const c = (state.session && state.session.contracts) || {};
  const is = (x) => x && a === x.toLowerCase();
  if (is(c.rollup)) return "contracts/src/frames/FramesNativeRollup.sol";
  if (is(c.l2Messenger)) return "contracts/src/l2/L2Messenger.sol";
  if (is(c.verifier)) return "contracts/src/frames/MockDependencyVerifier.sol";
  if (is(c.framesHelper)) return "contracts/frames/frame_introspection.eas";
  if (is(c.registry)) return "sys-asm/src/verification_key_registry/main.eas";
  return null;
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

function sourceSection(a, name, bytecode, chain) {
  const main = state.sources && mainSource(a);
  const notes = {
    "sys-asm/src/verification_key_registry/main.eas": "The EIP-8357 registry, from ethereum/sys-asm. The deployed runtime is this program with the admin's address in place of the system address, since no fork on this devnet performs the system call.",
    "contracts/frames/frame_introspection.eas": "Built with geas into the runtime the rollup contract deploys.",
  };
  let html = "";
  // Solidity contracts, flattened by L2BEAT's flattener.
  const flatName = main && main.endsWith(".sol") && main.split("/").pop().replace(".sol", "");
  if (flatName && state.flat && state.flat[flatName]) {
    html += `<h2>Source</h2>
      <p class="section-lead">Flattened with <a href="https://github.com/l2beat/l2beat/tree/main/packages/discovery/src/flatten">L2BEAT's flattener</a>,
        as L2BEAT shows the contracts it tracks: the contract and everything it inherits and uses, in one file. The separate files are in
        the repository's <code>contracts/src/</code>.</p>
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
  } else if (bytecode && /EIP-(7002|7251|8282|2935|4788|8141)|deposit contract/.test(name)) {
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
function highlight(code) {
  return code.split("\n").map((line) => {
    const i = line.indexOf("//");
    const src = i >= 0 ? line.slice(0, i) : line;
    const body = escCode(src).replace(TOKENS, (m, str, n, kw, ty) =>
      str ? `<span class="tk-s">${str}</span>` : n ? `<span class="tk-n">${n}</span>` : kw ? `<span class="tk-k">${kw}</span>` : `<span class="tk-t">${ty}</span>`);
    return body + (i >= 0 ? `<span class="tk-c">${escCode(line.slice(i))}</span>` : "");
  }).join("\n");
}

// The contracts deployed at an address, most derived first.
function contractsAt(target) {
  const c = (state.session && state.session.contracts) || {};
  const t = (target || "").toLowerCase();
  if (t === (c.rollup || "").toLowerCase()) return ["FramesNativeRollup", "NativeRollup"];
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

function codeFor(target, call) {
  const key = call && snippetKey(target, call.function);
  if (key) return codeBlock(key);
  if (!contractsAt(target).length) return `<p class="note">No contract code runs: the target is an account without code.</p>`;
  return "";
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

function value(v, key, ctx = {}) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.length ? `${v.length} items: ${v.map((x) => hash(x)).join(" ")}` : "empty";
  if (typeof v === "object") return argTable(v, {}, ctx);
  const s = String(v);
  if (/^0x[0-9a-f]{40}$/i.test(s)) return addr(s, chainFor(key, ctx));
  if (key === "value" && /^\d+$/.test(s)) return eth(s);
  if (/^0x[0-9a-f]*$/i.test(s)) return s.length > 70 ? hash(s) + ` <span class="muted">(${(s.length - 2) / 2} bytes)</span>` : `<span class="mono">${s}</span>`;
  return esc(typeof v === "number" ? num(v) : s);
}

function argTable(args, notes = {}, ctx = {}) {
  // A struct takes the value and note columns, so its own table has room.
  const nested = (v) => v && typeof v === "object" && !Array.isArray(v);
  return `<table class="fields"><tbody>${Object.entries(args)
    .map(([k, v]) => nested(v)
      ? `<tr><td>${esc(k)}</td><td class="value nested" colspan="2">${value(v, k, ctx)}</td></tr>`
      : `<tr><td>${esc(k)}</td><td class="value">${value(v, k, ctx)}</td><td class="note">${notes[k] || ""}</td></tr>`)
    .join("")}</tbody></table>`;
}

const EVENT_NOTES = {
  Transfer: "An EIP-7708 log: Ethereum logs every ETH transfer from this system address.",
  L1MessageRootProven: "The L2 messenger proved and cached the root of L1's message tree.",
  L1MessageClaimed: "The deposit was delivered.",
  L2MessageSent: "The withdrawal was recorded, to be claimed on L1.",
  L1MessageSent: "The deposit was added to the message tree.",
  BlockAdded: "The rollup contract accepted the L2 block.",
  L2MessageClaimed: "The withdrawal was paid on L1.",
};

function events(logs, chain) {
  if (!logs || !logs.length) return `<p class="note">No events.</p>`;
  return `<table><thead><tr><th>Event</th><th>From</th><th>Arguments</th></tr></thead><tbody>
    ${logs.map((l) => {
      const e = l.event;
      return `<tr><td>${e ? `<b>${e.name}</b><br><span class="note">${EVENT_NOTES[e.name] || ""}</span>` : hash(l.topics[0])}</td><td>${addr(l.address, chain)}</td>
        <td>${e ? Object.entries(e.args).map(([k, v]) => `${esc(k)}: ${value(v, k, { chain, event: e.name })}`).join("<br>") : hash(l.data)}</td></tr>`;
    }).join("")}</tbody></table>`;
}

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

function frameExplain(f, i, layer) {
  const fn = f.call && f.call.function;
  if (f.mode === "VERIFY") {
    const pays = f.flags.includes("APPROVE_PAYMENT");
    return {
      text: `The sender's account ${pays ? "approves the transaction and pays for it" : "approves the transaction"}. It has no code, so EIP-8141's default code checks the transaction's signature. ${
        layer === "L2" && pays && i > 0 ? "The fee is taken now, from the ETH the claim above just delivered: that is how a deposit pays for its own claim." : ""}`,
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
        { dataHash: "The public_input_root: what the proof commits to, which the rollup contract rebuilds.", verificationKeyHash: "The key the registry holds for the current fork.", signature: "The trusted prover's signature, in place of a proof." },
      ),
    };
  }
  const texts = {
    advance: "Calls advance on the rollup contract. It reads the dependency from the proof frame, rebuilds the proof's input from its storage, these arguments, the blob and the L1 anchor, and requires them to match.",
    proveL1MessageRoot: "Proves the root of L1's message tree against this L2 block's L1 anchor: the L1 block header, then the rollup contract's account and the root's storage slot in that block's state. The messenger caches the root, so only the first claim of a block carries this proof.",
    claimL1Message: "Checks the deposit's path to the proven root, marks it claimed, and releases its value from the pre-minted supply. It runs before any payment is approved: the recipient has no ETH yet.",
    sendMessage: layer === "L1" ? "Adds a message to the rollup contract's message tree, with the ETH it carries held in escrow." : "Records a withdrawal in the L2 messenger's storage, locking its ETH back into the pre-minted supply.",
    claimL2Message: "Proves the withdrawal against a recent L2 state root and pays it from the escrow.",
  };
  const ctx = { chain: layer.toLowerCase(), fn };
  return { text: texts[fn] || "", badges: [badge("real")], extra: f.call ? argTable(f.call.args.params || f.call.args, fn === "advance" ? ADVANCE_NOTES : {}, ctx) : "" };
}

function frameCards(tx, layer) {
  return `<div class="frames">${tx.frames.map((f, i) => {
    const e = frameExplain(f, i, layer);
    const status = f.status === undefined ? "" : f.status === 1 ? `<span class="check">succeeded</span>` : `<span class="bad">failed</span>`;
    return `<div class="frame"><div class="frame-head"><span class="idx">Frame ${i}</span><span class="mode">${f.mode}</span>
        ${f.call ? `<code>${f.call.function}</code>` : ""} → ${addr(f.target, layer.toLowerCase(), false)} ${e.badges.join(" ")}<span class="status">${status}</span></div>
      <div class="frame-body"><p class="explain">${e.text}</p>
        <div class="gasbar"><span>Flags: ${f.flags.length ? f.flags.join(", ") : "none"}</span>
          <span>Execution gas: ${f.executionGasUsed !== undefined ? num(f.executionGasUsed) + " of " : ""}${num(f.executionGasLimit)}</span>
          <span>State gas: ${f.stateGasUsed !== undefined ? num(f.stateGasUsed) + " of " : ""}${num(f.stateGasLimit)}</span>
          ${f.value ? `<span>Value: ${eth(f.value)}</span>` : ""}</div>
        ${e.extra || ""}
        ${f.mode === "VERIFY" && !contractsAt(f.target).length ? defaultCodeBlock() : f.dependency ? codeBlock(snippetKey(f.target, "fallback")) : codeFor(f.target, f.call)}
        ${f.logs && f.logs.length ? `<h3>Events</h3>${events(f.logs, layer.toLowerCase())}` : ""}</div></div>`;
  }).join("")}</div>`;
}

// The deposit an L2 claim delivers, or the withdrawal an L2 transaction sends.
function depositOf(tx) {
  const frame = (tx.frames || []).find((f) => f.call && f.call.function === "claimL1Message");
  return frame ? state.index.deposits[String(frame.call.args.message.index)] : null;
}
const withdrawalOf = (tx) => Object.values(state.index.withdrawals).find((w) => w.l2Tx === tx.hash);

function counterpart(tx) {
  if (tx.kind === "deposit claim") {
    const d = depositOf(tx);
    return d && d.l1Tx ? `deposited in ${l1TxLink(d.l1Tx)}` : "";
  }
  if (tx.kind === "withdrawal") {
    const w = withdrawalOf(tx);
    return w && w.l1Tx ? `claimed in ${l1TxLink(w.l1Tx)}` : '<span class="muted">not claimed on L1 yet</span>';
  }
  return "";
}

// The targets of a frame transaction, with the frames that call each.
function frameTargets(tx, chain) {
  const targets = {};
  tx.frames.forEach((f, i) => (targets[f.target] = targets[f.target] || []).push(`${i} ${f.mode}`));
  return Object.entries(targets).map(([t, frames]) => `${addr(t, chain)} <span class="muted">frame ${frames.join(", ")}</span>`).join("<br>");
}

const L2_SUMMARIES = {
  "deposit claim": (tx) => {
    const claim = tx.frames.find((f) => f.call && f.call.function === "claimL1Message").call.args.message;
    const proves = tx.frames.some((f) => f.call && f.call.function === "proveL1MessageRoot");
    return `${addr(claim.to, "l2")} claims a deposit of ${eth(claim.value)} and pays the fee from it. It is an EIP-8141 frame
      transaction: the claim frames run first, then the VERIFY frame approves the payment from the balance the claim
      just delivered.${proves ? " As the first claim of its block, it also proves the root of L1's message tree." : ""}`;
  },
  withdrawal: (tx) => `${addr(tx.from, "l2")} withdraws ${eth(tx.value)} to ${addr(tx.call.args.to, "l1")} on L1. The L2 messenger records the
    message, which can be claimed on L1 once this block is on L1.`,
  transfer: (tx) => `A plain ETH transfer of ${eth(tx.value)}, background activity of the demo's story.`,
};

async function l2TxPage(route) {
  const tx = await object(`l2/txs/${route.hash}`);
  if (!tx) return `<h1>L2 transaction</h1><p class="note">Not found. The follower may not have rebuilt its block yet.</p>`;
  const block = await object(`l2/blocks/${tx.block}`);
  const w = tx.kind === "withdrawal" && withdrawalOf(tx);
  const d = tx.kind === "deposit claim" && depositOf(tx);
  const frame = tx.type === 6;
  const linked = [];
  if (d) {
    linked.push(["Deposit on L1", d.l1Tx ? `${l1TxLink(d.l1Tx)} <span class="muted">in L1 block ${num(d.l1Block)}</span>` : "",
      `The L1 transaction that sent deposit #${d.index}. The rollup contract added its hash to the message tree, which this claim proves against.`]);
  }
  if (w) {
    linked.push(["Claim on L1", w.l1Tx ? `${l1TxLink(w.l1Tx)} <span class="muted">in L1 block ${num(w.l1Block)}</span>` : '<span class="muted">not claimed yet</span>',
      "The L1 transaction that paid this withdrawal from the escrow, against an L2 state root that includes it."]);
  }
  return `
    <h1>L2 transaction ${chip(tx.kind)}</h1>
    <div class="callout"><p>${(L2_SUMMARIES[tx.kind] || (() => ""))(tx)}${d && d.l1Tx ? ` The deposit was sent on L1 in ${l1TxLink(d.l1Tx)}.` : ""}${w && w.l1Tx ? ` It was claimed on L1 in ${l1TxLink(w.l1Tx)}.` : ""}</p></div>
    ${fields([
      ["Hash", hash(tx.hash, true), "Rebuilt by the follower from the L1 blob that carried its block."],
      ["Block", l2BlockLink(tx.block), ""],
      ["Posted on L1", block ? `${l1TxLink(block.l1.tx)} <span class="muted">in L1 block ${num(block.l1.block)}</span>` : "",
        "The L1 transaction that added this transaction's block. Its blob carries this transaction's bytes, which is where the follower read them from."],
      ...linked,
      ["Type", frame ? "0x06, frame transaction" : `0x0${tx.type}, EIP-1559`, frame ? "EIP-8141: a list of frames, each a call with its own mode and gas." : ""],
      [frame ? "Sender" : "From", addr(tx.from, "l2"), frame ? "The account the transaction acts for." : ""],
      ...(frame ? [["To", frameTargets(tx, "l2"), "A frame transaction has no single recipient: each frame calls its own target."],
        ["Payer", addr(tx.payer, "l2"), "The account whose VERIFY frame approved the payment."]] : [["To", addr(tx.to, "l2"), ""], ["Value", eth(tx.value), ""]]),
      ["Nonce", tx.nonce, ""],
      ["Gas used", num(tx.gasUsed), ""],
      ["Size", `${num(tx.bytes)} bytes`, "Its share of the block's blob."],
    ])}
    ${frame ? `<h2>Frames</h2>${frameCards(tx, "L2")}` : tx.call
      ? `<h2>Call: <code>${tx.call.function}</code></h2>${argTable(tx.call.args, {}, { chain: "l2", fn: tx.call.function })}${codeFor(tx.to, tx.call)}<h2>Events</h2>${events(tx.logs, "l2")}`
      : `<h2>Code</h2>${codeFor(tx.to, null)}<h2>Events</h2>${events(tx.logs, "l2")}`}`;
}

async function l1TxPage(route) {
  const tx = await object(`l1/txs/${route.hash}`);
  if (!tx) return `<h1>L1 transaction</h1><p class="note">Not found.</p>`;
  let summary = "";
  if (tx.kind === "advance") {
    summary = `The operator adds L2 block ${l2BlockLink(tx.l2Block)} to the rollup. One EIP-8141 frame transaction carries the
      block's data in a blob, the proof in a frame, and the call to the rollup contract, which accepts the block only if the
      proof is for exactly this block.`;
  } else if (tx.kind === "deposit") {
    const d = Object.values(state.index.deposits).find((x) => x.l1Tx === tx.hash);
    summary = `${addr(tx.from, "l1")} deposits ${eth(tx.value)} to ${addr(tx.call.args.to, "l2")} on L2. The rollup contract adds the message
      to its Merkle tree and keeps the ETH in escrow.${d && d.l2Tx ? ` It was claimed on L2 in ${l2TxLink(d.l2Tx)}, in block ${l2BlockLink(d.l2Block)}.` : ""}`;
  } else if (tx.kind === "withdrawal claim") {
    const m = tx.call.args.message;
    const w = state.index.withdrawals[String(m.index)];
    summary = `${addr(tx.from, "l1")} claims a withdrawal of ${eth(m.value)}. The rollup contract checks the message against the
      state root of L2 block ${l2BlockLink(tx.call.args.l2BlockNumber)} with a storage proof, and pays it from the escrow.${w && w.l2Tx ? ` It was sent on L2 in ${l2TxLink(w.l2Tx)}.` : ""}`;
  }
  const frame = !!tx.frames;
  const linked = [];
  if (tx.kind === "deposit") {
    const d = Object.values(state.index.deposits).find((x) => x.l1Tx === tx.hash);
    if (d) linked.push(["Claim on L2", d.l2Tx ? `${l2TxLink(d.l2Tx)} <span class="muted">in L2 block</span> ${l2BlockLink(d.l2Block)}` : '<span class="muted">waiting for an L2 block that anchors it</span>',
      `The L2 transaction that delivered deposit #${d.index}.`]);
  }
  if (tx.kind === "withdrawal claim") {
    const w = state.index.withdrawals[String(tx.call.args.message.index)];
    if (w && w.l2Tx) linked.push(["Withdrawal on L2", `${l2TxLink(w.l2Tx)} <span class="muted">in L2 block</span> ${l2BlockLink(w.l2Block)}`, `The L2 transaction that sent withdrawal #${w.index}.`]);
  }
  if (tx.kind === "advance") linked.push(["L2 block", l2BlockLink(tx.l2Block), "The L2 block this transaction adds."]);
  return `
    <h1>L1 transaction <span class="chip">${esc(L1_KINDS[tx.kind] || tx.kind)}</span></h1>
    <div class="callout"><p>${summary}</p></div>
    ${fields([
      ["Hash", hash(tx.hash, true), "Read from the L1 node."],
      ...linked,
      ["L1 block", `${num(tx.block)} <span class="muted">built by ${esc(tx.builder)}</span>`, frame && tx.blobVersionedHashes.length ? "Only Nethermind and Reth accept blob-carrying frame transactions on this devnet." : ""],
      ["Type", frame ? "0x06, frame transaction" : `0x0${tx.type}`, frame ? "EIP-8141." : ""],
      [frame ? "Sender" : "From", addr(tx.from, "l1"), ""],
      ...(frame ? [["To", frameTargets(tx, "l1"), "A frame transaction has no single recipient: each frame calls its own target."]] : [["To", addr(tx.to, "l1"), ""], ["Value", eth(tx.value), ""]]),
      ["Status", tx.status ? `<span class="check">succeeded</span>` : `<span class="bad">failed</span>`, ""],
      ["Gas used", `${num(tx.gasUsed)} at ${num(tx.effectiveGasPrice)} wei`, "Includes EIP-8037 state gas for new storage."],
      ...(tx.blobVersionedHashes.length ? [["Blob", `${hash(tx.blobVersionedHashes[0], true)}<br><span class="muted">${num(tx.blobGasUsed)} blob gas at ${num(tx.blobGasPrice)} wei</span>${tx.l2Block ? `<br><a href="#/blob/${tx.l2Block}">view it decoded and raw →</a>` : ""}`, "The L2 block's data, in EIP-8142's encoding."]] : []),
    ])}
    ${frame ? `<h2>Frames</h2>${frameCards(tx, "L1")}` : tx.call ? `<h2>Call: <code>${tx.call.function}</code></h2>${argTable(tx.call.args, {}, { chain: "l1", fn: tx.call.function })}${codeFor(tx.to, tx.call)}` : ""}
    <h2>Events</h2>${events(tx.logs, "l1")}`;
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
  else if (/^0x[0-9a-f]{64}$/.test(q)) {
    if (state.index.l1Txs.some((t) => t.hash === q)) location.hash = `#/l1/tx/${q}`;
    else if (await object(`l2/txs/${q}`)) location.hash = `#/l2/tx/${q}`;
    else document.getElementById("app").innerHTML = `<h1>Not found</h1><p class="note">No rollup transaction with hash ${esc(q)}.</p>`;
  }
  e.target.q.value = "";
});

window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
refresh().then(render);
setInterval(refresh, 4000);
