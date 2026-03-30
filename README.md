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


### 2. Extract and verify

> The path to the Prusti source tree is hard-coded in `extract.py` (`PRUSTI=...`); edit it before first use.

```bash
python extract.py full
```

Runs the full pipeline for `core` and `alloc`:
1. **Snapshot**: compiles Prusti and copies it into a subdirectory
    - Rebuilds the Prusti development tree and copies the resulting debug binaries into a timestamped directory `prusti-<date>-<hash>/`.
    - If the path of the Prusti repository is not `~/prusti-dev`, you need to provide the `--prusti` flag.
    - Aborts if the working tree has uncommitted changes
    - Use the `--branch` flag to have the snapshot (and later the database) named `prusti-{name}-<date>-<hash>/`
2. **Extract**: scrapes doctests out of the source into `{lib}/snippets/*.rs`
3. **Compile**: filters snippets that compile successfully (with Prusti's rustc) into `{lib}/bin/`
4. **Copy**: collects passing snippets into `tests/`
5. **Verify**: runs Prusti over all `tests/*.rs` and stores results in a `prusti-*.db` SQLite database

### 3. Analyse results

Open `analyze.ipynb` in Jupyter for a breakdown of the current database (success/fail counts, failure categories). 

### 4. Browse results

`browse.py` generates some HTML files that show the failure category table and renders the issue write-ups from `issues/*.md`:

```bash
python browse.py
```

Or run it in Docker:

```bash
docker build -t prusti-stdlib-support .
docker run -p 8080:80 prusti-stdlib-support
```

Then open http://localhost:8080. 
