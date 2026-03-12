# prusti-stdlib

Extracts, compiles, and verifies Rust standard library doctests using Prusti, and browses the results.

## Workflow

### 1. Fetch standard library source

```bash
./fetch_stdlib.sh [toolchain]
```

Copies `core/` and `alloc/` from a Rust nightly toolchain (default: `nightly-2025-09-04-x86_64-unknown-linux-gnu`) into the working directory.

### 2. Snapshot a Prusti build

```bash
./snapshot.sh
```

Rebuilds the Prusti development tree and copies the resulting debug binaries into a timestamped directory `prusti-<date>-<hash>/`. Aborts if the working tree has uncommitted changes. The snapshot directory is what `extract.py` uses to run verification.

> The path to the Prusti source tree is hard-coded in `snapshot.sh` (`PRUSTI=...`); edit it before first use.

### 3. Extract and verify

```bash
python extract.py full prusti-<date>-<hash>/prusti-rustc
```

Runs the full pipeline for `core` and `alloc`:
1. **Extract** — scrapes doctests out of the source into `{lib}/snippets/*.rs`
2. **Compile** — filters snippets that compile successfully (with Prusti's rustc) into `{lib}/bin/`
3. **Copy** — collects passing snippets into `tests/`
4. **Verify** — runs Prusti over all `tests/*.rs` and stores results in a `prusti-*.db` SQLite database

Asks for confirmation before the time-intensive verification step; skip with `--noconfirm`.

### 4. Analyse results

Open `analyze.ipynb` in Jupyter for a breakdown of the current database (success/fail counts, failure categories). Open `evolution.ipynb` to compare results across multiple database snapshots over time.

### 5. Browse results

`browse.py` is a small web server that shows the failure category table and renders the issue write-ups from `issues/*.md`:

```bash
python browse.py [--db prusti-*.db] [--port 8765]
```

Or run it in Docker:

```bash
docker build --build-arg DB_FILE=prusti-20260309-165527-9eba9fcdc.db -t prusti-stdlib-support .
docker run -p 8765:8765 prusti-stdlib-support
```

Then open http://localhost:8765. Replace the `DB_FILE` value with whichever `prusti-*.db` file you want to serve.
