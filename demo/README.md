# Native rollup demo

A live page that shows the native rollup of `contracts/` running on a local copy of frames-devnet-0: each L2 block from its transactions to an independent node rebuilding it from L1, the ETH moving between L1 and L2, the costs, and what is real and what is mocked.

- `runner.py` deploys a rollup and advances it with a scripted story, recording every step in `data/session.json`. A follower rebuilds the chain from L1 on its own and records what it verified in `data/follower.json`. After 150 L2 blocks, it starts a new episode.
- `server.py` serves `site/`, the records, and read-only access to the L1 node and the beacon node, so that the page reads the chain itself.
- `site/` is a static page with no build step.

Start the local network as in `contracts/frames/README.md`, then:

```shell
python3 demo/runner.py --rpc <geth rpc> --submit-rpc <nethermind rpc> --beacon <beacon api>
python3 demo/server.py --rpc <geth rpc> --beacon <beacon api>
```

and open http://127.0.0.1:8088. Kurtosis maps the ports at random, for example `kurtosis port print frames el-1-geth-lighthouse rpc`. The runner takes the paths of the two execution-specs environments with `--zkevm-specs` and `--frames-specs`. The finding cards link to the book served locally by `mdbook serve` on port 3000.
