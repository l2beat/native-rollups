#!/bin/sh
set -e
export RUSTUP_HOME=/rust
export CARGO_HOME=/rust
curl https://sh.rustup.rs -sSf | sh -s -- -y
. "/rust/env"
cargo install mdbook
mdbook build
