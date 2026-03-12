#!/bin/bash
# Fetches core and alloc source for a given Rust nightly toolchain.
# Usage: fetch_stdlib.sh [toolchain]
# Default toolchain: nightly-2025-09-04-x86_64-unknown-linux-gnu

set -euo pipefail

TOOLCHAIN="${1:-nightly-2025-09-04-x86_64-unknown-linux-gnu}"

rustup toolchain install "$TOOLCHAIN"
rustup component add --toolchain "$TOOLCHAIN" rust-src

SRC="$(rustup run "$TOOLCHAIN" rustc --print sysroot)/lib/rustlib/src/rust/library"

cp -r "$SRC/core" ./core
cp -r "$SRC/alloc" ./alloc

echo "Copied core and alloc from $SRC"
