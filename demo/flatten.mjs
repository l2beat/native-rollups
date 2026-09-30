// Flattens the demo's Solidity contracts with L2BEAT's flattener
// (`flattenStartingFrom` of @l2beat/discovery), with the options L2BEAT uses
// for the contracts it discovers. Prints the flat sources as JSON, keyed by
// contract name.
//
//     node demo/flatten.mjs [path to an l2beat checkout with packages/discovery built]

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, normalize } from "node:path";

const require = createRequire(import.meta.url);
const l2beat = process.argv[2] || join(homedir(), "work", "l2beat");
const { flattenStartingFrom } = require(join(l2beat, "packages", "discovery", "dist", "index.js"));

const contracts = join(import.meta.dirname, "..", "contracts");
const REMAPPINGS = ["@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/"];
const ROOTS = {
  FramesNativeRollup: "src/frames/FramesNativeRollup.sol",
  L2Messenger: "src/l2/L2Messenger.sol",
  MockDependencyVerifier: "src/frames/MockDependencyVerifier.sol",
};

// The files a root reaches through its imports, relative to contracts/.
function reachable(root) {
  const seen = new Set();
  const visit = (path) => {
    if (seen.has(path)) return;
    seen.add(path);
    const source = readFileSync(join(contracts, path), "utf8");
    for (const [, target] of source.matchAll(/^import\s+(?:[^"';]*\s+from\s+)?["']([^"']+)["'];/gm)) {
      const mapping = REMAPPINGS.map((r) => r.split("=")).find(([prefix]) => target.startsWith(prefix));
      visit(mapping ? mapping[1] + target.slice(mapping[0].length) : normalize(join(dirname(path), target)));
    }
  };
  visit(root);
  return [...seen];
}

const out = {};
for (const [name, rootFile] of Object.entries(ROOTS)) {
  const files = reachable(rootFile).map((path) => ({ path, content: readFileSync(join(contracts, path), "utf8") }));
  out[name] = flattenStartingFrom(name, rootFile, files, REMAPPINGS, { includeAll: true });
}
console.log(JSON.stringify(out));
