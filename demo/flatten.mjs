// Flattens Solidity contracts with L2BEAT's flattener (`flattenStartingFrom`
// of @l2beat/discovery), with the options L2BEAT uses for the contracts it
// discovers. Without arguments, it prints the flat sources of the demo's
// contracts as JSON, keyed by contract name. With --file and --name, it prints
// the flat source of one contract, whose imports are relative or, as in an npm
// package, paths under the nearest node_modules.
//
//     node demo/flatten.mjs [path to an l2beat checkout with packages/discovery built]
//     node demo/flatten.mjs <l2beat> --file <path to a .sol file> --name <contract>

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, normalize, relative, sep } from "node:path";

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
    for (const [, target] of source.matchAll(IMPORT)) {
      const mapping = REMAPPINGS.map((r) => r.split("=")).find(([prefix]) => target.startsWith(prefix));
      visit(mapping ? mapping[1] + target.slice(mapping[0].length) : normalize(join(dirname(path), target)));
    }
  };
  visit(root);
  return [...seen];
}

const IMPORT = /^import\s+(?:[^"';]*\s+from\s+)?["']([^"']+)["'];/gm;

// One contract's file and the files it reaches, relative to a base: this
// Foundry project with its remappings, the nearest node_modules, whose paths
// are the import paths, or the file's directory.
function flattenFile(file, name) {
  const parts = file.split(sep);
  const modules = parts.lastIndexOf("node_modules");
  const project = file.startsWith(contracts + sep);
  const base = project ? contracts : modules >= 0 ? parts.slice(0, modules + 1).join(sep) : dirname(file);
  const remappings = project ? REMAPPINGS : [];
  const seen = new Set();
  const visit = (path) => {
    if (seen.has(path)) return;
    seen.add(path);
    for (const [, target] of readFileSync(join(base, path), "utf8").matchAll(IMPORT)) {
      const mapping = remappings.map((r) => r.split("=")).find(([prefix]) => target.startsWith(prefix));
      visit(target.startsWith(".") ? normalize(join(dirname(path), target)) : mapping ? mapping[1] + target.slice(mapping[0].length) : target);
    }
  };
  const root = relative(base, file);
  visit(root);
  const files = [...seen].map((path) => ({ path, content: readFileSync(join(base, path), "utf8") }));
  return flattenStartingFrom(name, root, files, remappings, { includeAll: true });
}

const flag = (name) => process.argv[process.argv.indexOf(name) + 1];
if (process.argv.includes("--file")) {
  console.log(flattenFile(flag("--file"), flag("--name")));
} else {
  const out = {};
  for (const [name, rootFile] of Object.entries(ROOTS)) {
    const files = reachable(rootFile).map((path) => ({ path, content: readFileSync(join(contracts, path), "utf8") }));
    out[name] = flattenStartingFrom(name, rootFile, files, REMAPPINGS, { includeAll: true });
  }
  console.log(JSON.stringify(out));
}
