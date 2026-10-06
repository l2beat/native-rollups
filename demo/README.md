# Native rollup demo

An annotated explorer for the native rollup of `contracts/`, running on a local copy of frames-devnet-0. It lists every L2 block and the rollup's L1 transactions, breaks each transaction down frame by frame with decoded calls and events, links deposits and withdrawals across both chains, and notes on every field what it is, where it comes from, and whether it is real or mocked.

Its L2 data comes from the follower, which rebuilds every block from L1 alone, and it reads live chain state directly from the L1 and beacon nodes.

- `runner.py` deploys a rollup, starts the L2 node with its RPC on port 8547 and the sequencer, which builds a block every 4 seconds, and plays a scripted story, recording the operator's side in `data/session.json` and `data/blocks/`. The users send their L2 transactions through the node's RPC. A follower rebuilds the chain from L1 on its own and writes the decoded blocks and transactions to `data/explorer.db`, a SQLite database.
- Around the story, [spamoor](https://github.com/ethpandaops/spamoor) generates activity on both chains: ERC-20 transfers, Uniswap swaps, EIP-7702 delegations and EIP-8141 frame transactions on L2, and messages in both directions: ETH to random addresses, and pings to an example ping pong on the other chain, which answers each with a pong. Their recipients cannot claim, so a relayer claims those messages on L2 and a claimer on L1. The story also moves a demo token through an example ERC-20 bridge. Both apps, in `contracts/src/examples/`, have a contract on each chain that only accepts the calls its messenger delivers from the other; the runner deploys them, the L2 contracts through the CREATE2 factory so that each side names the other before either exists. Users, the relayer and spamoor claim deposits with transactions they sign, built by `contracts/script/l2_claims.py`; the L2 node holds no keys. Build spamoor with `go build -o bin/spamoor ./cmd/spamoor` and pass the binary with `--spamoor`, by default `~/work/spamoor/bin/spamoor`, or an empty value for the story alone.
- The follower decodes calls and events, and names the contracts transactions create, with the ABIs and creation codes it finds in `contracts/out`, in spamoor's scenarios, and in `data/packages`, as explorers use signature databases and verified sources. For each created contract it finds the source that compiles to its creation code and flattens it with L2BEAT's flattener. Spamoor ships some contracts only as artifacts of npm packages, whose sources come from installing them: `npm install --prefix demo/data/packages @account-abstraction/contracts@0.7.0 @uniswap/v2-core@1.0.1`.
- The runner resumes the last rollup when it starts, if L1 still has it: the L2 node and the follower start from their latest snapshots of the chain, taken every 300 blocks, and replay the blocks since, the node derives from L1 any it is missing, and the story goes on. `--new` deploys a new rollup. The runner restarts the follower and spamoor if they stop, and itself restarts with the rest if the node or the sequencer stops.
- `server.py` serves `site/`, the records, the follower's data as the pages query it, and read-only access to the L1 node and the beacon node, so that the page reads the chain itself.
- `site/` is a static page with no build step.

Start the local network as in `contracts/frames/README.md`, then:

```shell
python3 demo/runner.py
python3 demo/server.py
```

and open http://127.0.0.1:8088. Both find the devnet's Nethermind, Reth and beacon node through Docker, or take them with `--rpc`, `--submit-rpc` and `--beacon`. The runner takes the paths of the two execution-specs environments with `--zkevm-specs` and `--frames-specs`. The server also serves the book at `/book/`, as `mdbook build` builds it from the checkout, so that the explorer's links match the code it runs.

## Running unattended

The devnet's containers restart with Docker, after a crash or a reboot, once given a restart policy. The ports they publish stay the same:

```shell
docker update --restart unless-stopped $(docker ps -q --filter name=^el- --filter name=^cl- --filter name=^vc-)
```

`systemd/` has user services for the runner and the server, which restart them when they stop and start them at boot:

```shell
cp demo/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now native-rollup-demo native-rollup-explorer
sudo loginctl enable-linger $USER  # start them at boot, before anyone logs in
journalctl --user -u native-rollup-demo -f
```

They expect the checkout at `~/work/native-rollups`, as the runner expects execution-specs and spamoor under `~/work`. The runner logs to the journal, and the processes it starts write about 150 MB a day to `data/`, which logrotate can keep in check:

```shell
sudo tee /etc/logrotate.d/native-rollup-demo <<EOF
$HOME/work/native-rollups/demo/data/*.log $HOME/work/native-rollups/demo/data/*.jsonl {
    su $USER $USER
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

## Publishing

The server only listens on 127.0.0.1. A [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) publishes it: `cloudflared` connects out to Cloudflare, so no port opens and the hostname resolves to Cloudflare's addresses, not the machine's. Only the server goes through the tunnel, and it rate-limits each visitor, by the address Cloudflare forwards, on the calls that reach the devnet, and sends plain-HTTP visitors to HTTPS.
