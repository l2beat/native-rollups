// Live view of the native rollup demo. Reads the runner's records from
// /api/session and the follower's from /api/follower, and reads the chain
// itself through /rpc and /beacon, so that what it shows about L1 does not
// rest on the records.

const BLOB_USABLE_BYTES = 4096 * 31;
const SELECTORS = { blockNumber: "0x57e871e7", blockHash: "0xf22a195e" };
const BOOK = "http://127.0.0.1:3000";

const state = {
  session: null,
  follower: null,
  l1Head: null,
  rollupHead: null,
  rollupHash: null,
  selected: null, // L2 block number, or null to follow the latest
  stage: "post",
  blobs: {}, // decoded blobs by L2 block number
  beacon: null,
};

// ---------------------------------------------------------------------------
// Data access
// ---------------------------------------------------------------------------

async function rpc(method, params = []) {
  const r = await fetch("/rpc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return (await r.json()).result;
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  return r.json();
}

async function refresh() {
  try {
    const [session, follower, head] = await Promise.all([
      getJSON("/api/session"),
      getJSON("/api/follower"),
      rpc("eth_blockNumber"),
    ]);
    state.session = session;
    state.follower = follower && session && follower.rollup === session.contracts.rollup ? follower : null;
    state.l1Head = head ? parseInt(head, 16) : null;
    const rollup = session && session.contracts.rollup;
    if (rollup) {
      const [n, h] = await Promise.all([
        rpc("eth_call", [{ to: rollup, data: SELECTORS.blockNumber }, "latest"]),
        rpc("eth_call", [{ to: rollup, data: SELECTORS.blockHash }, "latest"]),
      ]);
      state.rollupHead = n ? parseInt(n, 16) : null;
      state.rollupHash = h || null;
    }
    render();
  } catch (e) {
    document.getElementById("status").innerHTML = `<span class="pill warn">cannot reach the demo server</span>`;
  }
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const short = (h) => (h ? `${h.slice(0, 8)}…${h.slice(-6)}` : "");
const hash = (h) => `<span class="mono" title="${esc(h)}">${short(h)}</span>`;
const num = (n) => Number(n).toLocaleString("en-US");
const eth = (wei) => `${(Number(wei) / 1e18).toLocaleString("en-US", { maximumFractionDigits: 4 })} ETH`;
const badge = (kind, text) => `<span class="badge ${kind}">${text || kind}</span>`;
const pct = (x) => `${(100 * x).toFixed(x < 0.1 ? 1 : 0)}%`;
const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
const kv = (rows) =>
  `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;

const events = (type) => (state.session ? state.session.events.filter((e) => e.type === type) : []);
const blocks = () => events("advance").sort((a, b) => a.l2.number - b.l2.number);
const followed = (n) => state.follower && state.follower.verified.find((v) => v.number === n);

const TX_KINDS = {
  claim: "Claim of a deposit, paid from the deposit (frame transaction)",
  l2Message: "Withdrawal to L1",
  transfer: "Transfer",
};

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render() {
  renderStatus();
  const all = blocks();
  if (!all.length) {
    document.getElementById("pipeline").innerHTML = "";
    document.getElementById("stage-detail").innerHTML =
      `<p class="plain">Waiting for the first L2 block. ${state.session ? "The rollup is deployed." : "The demo is starting."}</p>`;
  } else {
    const block = all.find((b) => b.l2.number === state.selected) || all[all.length - 1];
    renderStrip(all, block);
    renderPipeline(block);
    renderDetail(block);
  }
  renderMessages();
  renderCosts(all);
  renderVerify();
}

function renderStatus() {
  const s = state.session;
  const pills = [];
  if (state.l1Head != null) pills.push(`<span class="pill"><span class="dot"></span>L1 block ${num(state.l1Head)}</span>`);
  if (state.rollupHead != null) pills.push(`<span class="pill">L2 block ${num(state.rollupHead)} on L1</span>`);
  const verified = state.follower ? state.follower.verified.length : 0;
  if (state.rollupHead) {
    pills.push(
      verified >= state.rollupHead
        ? `<span class="pill ok">follower rebuilt all ${num(verified)} blocks from L1</span>`
        : `<span class="pill warn">follower at ${num(verified)} of ${num(state.rollupHead)}</span>`,
    );
  }
  if (s) pills.push(`<span class="pill">episode ${s.episode}</span>`);
  document.getElementById("status").innerHTML = pills.join("");
}

function tag(b) {
  if (b.l2.claims.length) return "deposit";
  if (b.l2.l2Messages.length) return "withdrawal";
  return `${b.l2.transactions} txs`;
}

function renderStrip(all, block) {
  const recent = all.slice(-24);
  document.getElementById("block-strip").innerHTML = recent
    .map(
      (b) => `<button class="block-chip ${b === block ? "selected" : ""}" data-block="${b.l2.number}">
        #${b.l2.number}<span class="tag">${tag(b)}</span></button>`,
    )
    .join("");
}

function stages(b) {
  const f = followed(b.l2.number);
  const kinds = {};
  b.l2.transactionList.forEach((t) => (kinds[t.kind] = (kinds[t.kind] || 0) + 1));
  const summary = Object.entries(kinds)
    .map(([k, n]) => `${n} ${k === "l2Message" ? "withdrawal" : k}${n > 1 ? "s" : ""}`)
    .join(", ");
  const fill = b.l2.payloadBytes / BLOB_USABLE_BYTES;
  return [
    { id: "build", title: "Build", badges: ["real", "shortcut"], text: `${b.l2.transactions} transactions: ${summary}` },
    { id: "check", title: "Check", badges: ["real"], text: `Ethereum's validation program accepts it <span class="check">✓</span>` },
    { id: "prove", title: "Prove", badges: ["mock"], text: "A trusted key signs the result, in place of a zk proof" },
    { id: "post", title: "Post", badges: ["real", "mock"], text: `One L1 transaction. The block fills ${pct(fill)} of a blob` },
    { id: "verify", title: "Verify", badges: ["real"], text: `The L1 contract checks the proof's input <span class="check">✓</span>` },
    {
      id: "follow",
      title: "Follow",
      badges: ["real"],
      pending: !f,
      text: f ? `Rebuilt from L1 alone, same hash <span class="check">✓</span>` : "Waiting for the follower…",
    },
  ];
}

function renderPipeline(b) {
  document.getElementById("pipeline").innerHTML = stages(b)
    .map(
      (s, i) => `<li class="stage ${s.id === state.stage ? "selected" : ""} ${s.pending ? "pending" : ""}" data-stage="${s.id}">
        <div class="num">Step ${i + 1}</div>
        <h4>${s.title}</h4>
        ${s.badges.map((k) => badge(k)).join(" ")}
        <p>${s.text}</p>
      </li>`,
    )
    .join("");
}

function renderDetail(b) {
  const el = document.getElementById("stage-detail");
  const l2 = b.l2;
  const all = blocks();
  const parent = all.find((x) => x.l2.number === l2.number - 1);
  const c = state.session.contracts;
  const detail = {
    build: () => `
      <h3>Build block #${l2.number} ${badge("real")} ${badge("shortcut")}</h3>
      <p class="plain">The operator executes the block's transactions with Ethereum's own rules: execution-specs' Amsterdam
        with EIP-8141, the same fork the L1 runs. There is one operator, running a scripted story ${badge("shortcut")}.</p>
      <table><thead><tr><th>#</th><th>Transaction</th><th>Hash</th><th class="num">Bytes</th></tr></thead><tbody>
        ${l2.transactionList.map((t, i) => `<tr><td>${i}</td><td>${TX_KINDS[t.kind] || t.kind}</td><td>${hash(t.hash)}</td><td class="num">${num(t.bytes)}</td></tr>`).join("")}
      </tbody></table>
      ${kv([
        ["Block hash", hash(l2.blockHash)],
        ["State root", hash(l2.stateRoot)],
        ["Gas used", num(l2.gasUsed)],
        ["Timestamp", new Date(l2.timestamp * 1000).toLocaleString()],
        ["L1 anchor", `L1 block ${num(l2.anchor.number)}, ${hash(l2.anchor.hash)}`],
      ])}`,
    check: () => `
      <h3>Check with Ethereum's program ${badge("real")}</h3>
      <p class="plain">The operator runs the stateless validation program on the block and a witness of the state it
        touches. It is the same function Ethereum's provers will prove for L1 blocks, and it accepted this block. Here
        it runs as ordinary code: a prover would run the same program inside a zkVM.</p>
      ${kv([
        ["Program", `<code>verify_stateless_new_payload</code>, execution-specs <code>projects/zkevm</code> with EIP-8141`],
        ["Result", `<code>successful_validation = ${l2.validation.successful}</code>`],
        ["Chain ID", l2.validation.chainId],
        ["Schema ID", `<code>0x${l2.validation.schemaId.toString(16)}</code> (Amsterdam, revision 1)`],
      ])}
      <p class="note">Why chain and schema IDs? See <a href="${BOOK}/specification.html#proof-statement">Proof statement</a>.</p>`,
    prove: () => `
      <h3>Prove ${badge("mock")}</h3>
      <p class="plain">A zkVM would now prove that the program accepted the block. This demo has no prover, so a trusted
        key signs the same statement instead. It only signs blocks the program accepted, and the follower re-checks each
        one. The mock goes away with a zkVM proof of the same program.</p>
      ${kv([
        ["What the proof commits to", `<code>PublicInput</code> of EIP-8025: the request root, <code>successful_validation</code>, chain ID, schema ID`],
        ["new_payload_request_root", hash(l2.newPayloadRequestRoot)],
        ["public_input_root", hash(l2.publicInputRoot)],
        ["Dependency", `scheme <code>0x11</code> (LeanSTARK), data hash ${hash(l2.publicInputRoot)}, key ${hash("0x" + b.proof.triple.slice(130))}`],
        ["Signed by", `${hash(c.prover)} ${badge("mock")}`],
      ])}`,
    post: () => postDetail(b),
    verify: () => `
      <h3>Verify on L1 ${badge("real")}</h3>
      <p class="plain">The rollup contract rebuilds what the proof must commit to, from its own storage, the transaction's
        data and blob, and a recent L1 block. It then requires the transaction's proof to be for exactly that, so a
        wrong value anywhere gives a different root and the block is rejected.</p>
      <table><thead><tr><th>Input</th><th>Value</th><th>Comes from</th></tr></thead><tbody>
        <tr><td>Parent block hash</td><td>${hash(parent ? parent.l2.blockHash : c.l2GenesisHash)}</td><td>Contract storage</td></tr>
        <tr><td>Block number</td><td>${l2.number}</td><td>Contract storage</td></tr>
        <tr><td>Chain ID, gas limit</td><td>${l2.validation.chainId}, 60,000,000</td><td>Fixed at deployment</td></tr>
        <tr><td>Timestamp</td><td>${l2.timestamp}</td><td>Transaction data, at most L1 time</td></tr>
        <tr><td>State root, receipts, gas used…</td><td>${hash(l2.stateRoot)}</td><td>Transaction data, proven</td></tr>
        <tr><td>Blob</td><td>${hash(l2.versionedHashes[0])}</td><td><code>BLOBHASH</code></td></tr>
        <tr><td>L1 anchor</td><td>L1 block ${num(l2.anchor.number)}</td><td><code>BLOCKHASH</code>, block picked by the operator</td></tr>
        <tr><td>Key and schema</td><td><code>0x${l2.validation.schemaId.toString(16)}</code></td><td>EIP-8357 registry ${badge("mock")}</td></tr>
      </tbody></table>
      ${kv([
        ["Result", `the proof's input matches <span class="check">✓</span>, and the rollup moved to block ${num(b.l1.rollupHead)}`],
        ["How", `<a href="${BOOK}/specification.html#root-computation">Root computation</a>, about 28k gas with one blob`],
      ])}`,
    follow: () => {
      const f = followed(l2.number);
      if (!f) return `<h3>Follow ${badge("real")}</h3><p class="plain">The follower has not reached this block yet.</p>`;
      return `
      <h3>Rebuilt from L1 alone ${badge("real")}</h3>
      <p class="plain">An independent node rebuilt this block from L1 only. It took the header fields from the L1
        transaction's data and the transactions from the blob the beacon node serves, re-executed them, and got the
        hash the contract recorded. So L1 holds everything needed to rebuild the L2, and every block the contract
        accepted is valid.</p>
      ${kv([
        ["Rebuilt block hash", hash(f.blockHash)],
        ["Hash the contract recorded", hash(l2.blockHash)],
        ["Match", f.blockHash === l2.blockHash ? `<span class="check">✓</span>` : `<span style="color:var(--bad)">different</span>`],
        ["From", `L1 transaction ${hash(f.l1Tx)} in L1 block ${num(f.l1Block)}, ${f.blobs} blob, a ${num(f.balBytes)}-byte access list`],
        ["Re-executed with", `execution-specs' <code>state_transition</code>`],
      ])}`;
    },
  }[state.stage];
  el.innerHTML = detail();
}

function postDetail(b) {
  const l1 = b.l1;
  const fr = l1.frames;
  const fill = b.l2.payloadBytes / BLOB_USABLE_BYTES;
  const decoded = state.blobs[b.l2.number];
  return `
    <h3>Post to L1 ${badge("real")}</h3>
    <p class="plain">One L1 frame transaction (EIP-8141) carries the block. Its blob holds the block's transactions and
      access list in EIP-8142's encoding. Its frames approve the payment, check the proof, and call the rollup contract.</p>
    <table><thead><tr><th>Frame</th><th>What it does</th><th class="num">Execution gas</th><th class="num">State gas</th><th></th></tr></thead><tbody>
      <tr><td>0 VERIFY</td><td>The operator's account approves and pays</td><td class="num">${num(fr[0].executionGas)}</td><td class="num">${num(fr[0].stateGas)}</td><td>${badge("real")}</td></tr>
      <tr><td>1 DEFAULT</td><td>Checks the proof, in place of EIP-8288</td><td class="num">${num(fr[1].executionGas)}</td><td class="num">${num(fr[1].stateGas)}</td><td>${badge("mock")}</td></tr>
      <tr><td>2 SENDER</td><td>Calls <code>advance</code> on the rollup contract</td><td class="num">${num(fr[2].executionGas)}</td><td class="num">${num(fr[2].stateGas)}</td><td>${badge("real")}</td></tr>
    </tbody></table>
    <h3>Blob</h3>
    <div class="fill"><span style="width:${Math.max(0.4, 100 * fill)}%"></span></div>
    <p class="note">${num(b.l2.payloadBytes)} of ${num(BLOB_USABLE_BYTES)} usable bytes (${pct(fill)}). Every block takes a
      whole blob: see <a href="${BOOK}/specification.html#open-questions">blob granularity</a>.</p>
    ${kv([
      ["L1 transaction", hash(l1.txHash)],
      ["L1 block", `${num(l1.block)}, built by ${esc(l1.builder)}`],
      ["Gas", `${num(l1.gasUsed)} gas, plus ${num(l1.blobGasUsed)} blob gas at ${num(l1.blobGasPrice)} wei`],
      ["Versioned hash", hash(b.l2.versionedHashes[0])],
    ])}
    <p><button class="button" data-decode="${b.l2.number}">Fetch and decode this blob from the beacon node</button></p>
    ${decoded ? decodedBlob(decoded) : ""}`;
}

function decodedBlob(d) {
  if (d.error) return `<pre class="blob">${esc(d.error)}</pre>`;
  const types = { 2: "EIP-1559", 6: "frame" };
  return `<pre class="blob">Blob ${d.versionedHash} from beacon slot ${d.slot}
Header: access list ${num(d.balLength)} bytes, transactions ${num(d.txLength)} bytes
Transactions (RLP list of ${d.txs.length}):
${d.txs.map((t, i) => `  ${i}: type 0x${t.type.toString(16).padStart(2, "0")} (${types[t.type] || "?"}), ${num(t.length)} bytes, starts ${t.head}`).join("\n")}
First bytes of the blob: ${d.head}</pre>`;
}

function renderMessages() {
  const el = document.getElementById("message-list");
  const deposits = events("deposit");
  const all = blocks();
  const cards = [];
  deposits.forEach((d, index) => {
    const claimedIn = all.find((b) => b.l2.claims.some((c) => c.index === index));
    const claim = claimedIn && claimedIn.l2.claims.find((c) => c.index === index);
    cards.push({
      time: d.time,
      html: `<div class="message"><div><div class="kind">Deposit</div><div class="amount">${eth(Number(d.amount.replace("ether", "")) * 1e18)}</div><div class="note">${ago(d.time)}</div></div>
        <ul class="steps">
          <li class="done">Sent on L1 in block ${num(d.l1.block)}, ${num(d.l1.gasUsed)} gas</li>
          ${claimedIn
            ? `<li class="done">Claimed on L2 in block #${claimedIn.l2.number}, paying its own fee</li><li class="done">Alice's L2 balance: ${eth(claim.l2Balance)}</li>`
            : `<li class="waiting">Claimed on L2 once a block anchors an L1 block that includes it…</li>`}
        </ul></div>`,
    });
  });
  all.forEach((b) =>
    b.l2.l2Messages.forEach((m) => {
      const claimed = events("claimL2Message").find((c) => c.message.index === m.index);
      cards.push({
        time: b.time,
        html: `<div class="message"><div><div class="kind">Withdrawal</div><div class="amount">${eth(m.value)}</div><div class="note">${ago(b.time)}</div></div>
          <ul class="steps">
            <li class="done">Sent on L2 in block #${b.l2.number}</li>
            <li class="done">Block on L1, in L1 block ${num(b.l1.block)}</li>
            ${claimed
              ? `<li class="done">Claimed on L1 against a stored state root, ${num(claimed.l1.gasUsed)} gas</li>`
              : `<li class="waiting">Waiting to be claimed on L1…</li>`}
          </ul></div>`,
      });
    }),
  );
  cards.sort((a, b) => b.time - a.time);
  el.innerHTML = cards.length ? cards.slice(0, 8).map((c) => c.html).join("") : `<p class="note">No messages yet.</p>`;
}

function renderCosts(all) {
  const last = all[all.length - 1];
  const deposit = events("deposit").slice(-1)[0];
  const claim = events("claimL2Message").slice(-1)[0];
  const rows = [];
  if (last) {
    const f = last.l1.frames;
    rows.push([
      "Add an L2 block",
      `${num(last.l1.gasUsed)} gas + one blob (${num(last.l1.blobGasUsed)} blob gas)`,
      `<code>advance</code>: ${num(f[2].executionGas)} execution gas${f[2].stateGas ? ` + ${num(f[2].stateGas)} state gas for a new state root slot, until the history of 8,191 roots wraps` : ""}. Includes the mock proof frame (${num(f[1].executionGas)}).`,
    ]);
  }
  if (deposit) rows.push(["Deposit from L1", `${num(deposit.l1.gasUsed)} gas`, "Adds the message to the tree of L1 to L2 messages. New tree levels cost more, once per power of two"]);
  if (claim) rows.push(["Claim a withdrawal on L1", `${num(claim.l1.gasUsed)} gas`, `Proves the message against an L2 state root with ${claim.proofNodes} trie nodes, and pays it from the L1 escrow`]);
  document.getElementById("costs-table").innerHTML = rows.length
    ? `<table><thead><tr><th>Operation</th><th>Gas on L1</th><th>Notes</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join("")}</tbody></table>`
    : `<p class="note">No transactions yet.</p>`;

  const fills = all.slice(-60).map((b) => b.l2.payloadBytes / BLOB_USABLE_BYTES);
  const max = Math.max(...fills, 0.01);
  document.getElementById("blob-chart").innerHTML = fills
    .map((x, i) => `<div class="bar" title="block #${all.slice(-60)[i].l2.number}: ${pct(x)}" style="height:${(100 * x) / max}%"></div>`)
    .join("");
  const chartNote = document.getElementById("blob-chart-caption") || document.getElementById("blob-chart").insertAdjacentElement("afterend", Object.assign(document.createElement("p"), { id: "blob-chart-caption", className: "blob-chart-caption" }));
  chartNote.textContent = fills.length
    ? `Each bar is one block, scaled to the fullest. They range from ${pct(Math.min(...fills))} to ${pct(max)} of a blob.`
    : "";
}

function renderVerify() {
  const s = state.session;
  if (!s) return;
  const c = s.contracts;
  document.getElementById("verify-content").innerHTML = `
    <table><thead><tr><th>Contract or key</th><th>Address</th><th></th></tr></thead><tbody>
      <tr><td>Rollup contract (L1)</td><td class="mono">${c.rollup}</td><td>${badge("real")}</td></tr>
      <tr><td>L2 messenger (L2 predeploy)</td><td class="mono">${c.l2Messenger}</td><td>${badge("real")}</td></tr>
      <tr><td>Proof checker (L1)</td><td class="mono">${c.verifier}</td><td>${badge("mock")}</td></tr>
      <tr><td>Key registry (L1)</td><td class="mono">${c.registry}</td><td>${badge("mock")}</td></tr>
      <tr><td>Trusted prover key</td><td class="mono">${c.prover}</td><td>${badge("mock")}</td></tr>
    </tbody></table>
    <p>Rebuild the chain from L1 yourself, with the follower the page shows:</p>
    <pre class="blob">uv run --project &lt;execution-specs projects/zkevm + EIP-8141&gt; python contracts/script/l2_follower.py \\
    --l1-rpc &lt;L1 RPC&gt; --beacon &lt;beacon API&gt; --rollup ${c.rollup} \\
    --genesis demo/data/l2_state.json</pre>
    <p class="note">The genesis comes from the node's state file here. A public rollup would publish it as part of its
      configuration. The design is in the <a href="${BOOK}/specification.html">Specification</a>.</p>`;
}

// ---------------------------------------------------------------------------
// Blob decoding, from the beacon node
// ---------------------------------------------------------------------------

function hexToBytes(hex) {
  const h = hex.startsWith("0x") ? hex.slice(2) : hex;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(2 * i, 2), 16);
  return out;
}

const toHex = (bytes) => "0x" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

// Item at `offset` of an RLP string: [content start, content length, next offset].
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

async function decodeBlob(number) {
  const b = blocks().find((x) => x.l2.number === number);
  const versionedHash = b.l2.versionedHashes[0];
  try {
    if (!state.beacon) {
      const [g, spec] = await Promise.all([getJSON("/beacon/eth/v1/beacon/genesis"), getJSON("/beacon/eth/v1/config/spec")]);
      state.beacon = { genesis: Number(g.data.genesis_time), secondsPerSlot: Number(spec.data.SECONDS_PER_SLOT) };
    }
    const l1Block = await rpc("eth_getBlockByNumber", ["0x" + b.l1.block.toString(16), false]);
    const slot = Math.floor((parseInt(l1Block.timestamp, 16) - state.beacon.genesis) / state.beacon.secondsPerSlot);
    const res = await getJSON(`/beacon/eth/v1/beacon/blobs/${slot}?versioned_hashes=${versionedHash}`);
    const blob = hexToBytes(res.data[0]);
    // EIP-8142: 31 bytes per 32-byte field element, whose first byte is zero.
    const raw = new Uint8Array(4096 * 31);
    for (let i = 0; i < 4096; i++) raw.set(blob.subarray(32 * i + 1, 32 * i + 32), 31 * i);
    const u32 = (o) => ((raw[o] << 24) | (raw[o + 1] << 16) | (raw[o + 2] << 8) | raw[o + 3]) >>> 0;
    const balLength = u32(0), txLength = u32(4);
    const txs = [];
    const [start, length] = rlpItem(raw, 8 + balLength);
    for (let o = start; o < start + length; ) {
      const [s, len, next] = rlpItem(raw, o);
      txs.push({ type: raw[s], length: len, head: toHex(raw.subarray(s, s + 8)) + "…" });
      o = next;
    }
    state.blobs[number] = { versionedHash, slot, balLength, txLength, txs, head: toHex(blob.subarray(0, 40)) + "…" };
  } catch (e) {
    state.blobs[number] = { error: `Could not fetch or decode the blob: ${e}` };
  }
  render();
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-block]");
  if (chip) {
    const n = Number(chip.dataset.block);
    const all = blocks();
    state.selected = n === all[all.length - 1].l2.number ? null : n;
    return render();
  }
  const stage = e.target.closest("[data-stage]");
  if (stage) {
    state.stage = stage.dataset.stage;
    return render();
  }
  const decode = e.target.closest("[data-decode]");
  if (decode) {
    decode.textContent = "Fetching…";
    decodeBlob(Number(decode.dataset.decode));
  }
});

refresh();
setInterval(refresh, 4000);
