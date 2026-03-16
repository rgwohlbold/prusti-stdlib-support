#!/bin/bash
# Creates a self-contained snapshot of the current debug build of Prusti.
# Output: ./prusti-<timestamp>-<hash>/

set -euo pipefail

PRUSTI="/root/prusti-dev"
TIMESTAMP=$(git -C "$PRUSTI" log -1 --format="%cd" --date=format:"%Y%m%d-%H%M%S")
HASH=$(git -C "$PRUSTI" rev-parse --short=9 HEAD)
NAME="$TIMESTAMP-$HASH"
DEST="./prusti-$NAME"

# Abort if no tracked files have been modified
if ! git -C "$PRUSTI" diff --quiet -- HEAD; then
    echo "Tracked files modified in $PRUSTI; aborting." >&2
    exit 1
fi

# Force a rebuild and build debug
touch "$PRUSTI/prusti/src/driver.rs"
(cd "$PRUSTI" && ./x.py build)

mkdir -p "$DEST/deps"

# Copy executables
for bin in prusti-rustc cargo-prusti prusti-driver prusti-server prusti-smt-solver; do
    cp "$PRUSTI/target/debug/$bin" "$DEST/"
done

# Copy contracts library to root so get_prusti_contracts_dir finds it
cp "$PRUSTI/target/verify/debug"/libprusti_contracts*.rlib "$DEST/"

# Copy deps (proc macro .so and other rlibs)
cp "$PRUSTI/target/verify/debug/deps"/* "$DEST/deps/"

# Symlink viper_tools so Viper/Z3 are found without setting env vars
ln -s "$PRUSTI/viper_tools" "$DEST/viper_tools"

echo "Snapshot created at $DEST"
