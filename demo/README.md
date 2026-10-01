# Native rollup demo

An annotated explorer for the native rollup of `contracts/`, running on a local copy of frames-devnet-0. It lists every L2 block and the rollup's L1 transactions, breaks each transaction down frame by frame with decoded calls and events, links deposits and withdrawals across both chains, and notes on every field what it is, where it comes from, and whether it is real, mocked, or a shortcut of the demo.

Its L2 data comes from the follower, which rebuilds every block from L1 alone, and it reads live chain state directly from the L1 and beacon nodes.

- `runner.py` deploys a rollup, starts the L2 node with its RPC on port 8547, and plays a scripted story with one L2 block per step, recording the operator's side of every step in `data/session.json`. The users send their L2 transactions through the node's RPC. A follower rebuilds the chain from L1 on its own and writes the decoded blocks and transactions to `data/explorer/`.
- Around the story, [spamoor](https://github.com/ethpandaops/spamoor) generates activity on both chains: ERC-20 transfers, Uniswap swaps, EIP-7702 delegations, ERC-4337 bundles and EIP-8141 frame transactions on L2, and messages in both directions, with ETH to random addresses or calls to a `MessageReceiver` on the other chain. Their recipients cannot claim, so the L2 node's relayer account claims deposits and a claimer account claims withdrawals on L1. Build spamoor with `go build -o bin/spamoor ./cmd/spamoor` and pass the binary with `--spamoor`, by default `~/work/spamoor/bin/spamoor`, or an empty value for the story alone.
- The follower decodes calls and events, and names the contracts transactions create, with the ABIs and creation codes it finds in `contracts/out`, in spamoor's scenarios, and in `data/packages`, as explorers use signature databases and verified sources. For each created contract it finds the source that compiles to its creation code and flattens it with L2BEAT's flattener. Spamoor ships some contracts only as artifacts of npm packages, whose sources come from installing them: `npm install --prefix demo/data/packages @account-abstraction/contracts@0.7.0 @uniswap/v2-core@1.0.1`.
- The runner resumes the last rollup when it starts, if L1 still has it: the L2 node replays its blocks and derives from L1 any it is missing, the follower rebuilds the chain from L1, and the story goes on. `--new` deploys a new rollup.
- `server.py` serves `site/`, the records, and read-only access to the L1 node and the beacon node, so that the page reads the chain itself.
- `site/` is a static page with no build step.

Start the local network as in `contracts/frames/README.md`, then:

```shell
python3 demo/runner.py --rpc <geth rpc> --submit-rpc <nethermind rpc> --beacon <beacon api>
python3 demo/server.py --rpc <geth rpc> --beacon <beacon api>
```

and open http://127.0.0.1:8088. Kurtosis maps the ports at random, for example `kurtosis port print frames el-1-geth-lighthouse rpc`. The runner takes the paths of the two execution-specs environments with `--zkevm-specs` and `--frames-specs`. The finding cards link to the book served locally by `mdbook serve` on port 3000.
