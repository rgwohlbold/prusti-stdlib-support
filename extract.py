#!/usr/bin/python3
# /// script
# dependencies = [
#   "tqdm",
# ]
# ///
import os
import re
import signal
import argparse
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tqdm import tqdm

PRUSTI_SERVER_PORT = 27010


def process_file(library: str, file_path: Path, output_dir: Path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    in_code_block = False
    skip_current_block = False
    has_doc_attr_in_block = False
    doc_attr_depth = 0   # bracket depth for multiline #[doc = concat!(...\n...)] attrs
    current_block = []
    block_counter = 0

    for line in lines:
        stripped_line = line.strip()

        # Check if the line is a documentation comment
        if stripped_line.startswith('///') or stripped_line.startswith('//!'):
            # Remove the doc comment prefix (/// or //! and up to one space)
            doc_content = re.sub(r'^(///|//!)\s?', '', stripped_line)

            # Check for the start or end of a markdown code block
            if doc_content.startswith('```'):
                if not in_code_block:
                    # START OF BLOCK
                    # Always mark in_code_block so the closing fence is recognised
                    # even for blocks we're going to skip.
                    in_code_block = True
                    has_doc_attr_in_block = False
                    current_block = []
                    # Fence attributes can be comma- or space-separated (or both).
                    attributes = re.split(r'[,\s]+', doc_content[3:].strip())
                    # Ignore blocks that aren't valid, compiling Rust code
                    invalid_attrs = {
                        'ignore', 'compile_fail', 'should_panic', 'no_run',
                        'standalone_crate', 'text', 'test', 'no_rust', 'not_rust',
                        'asm', 'bash', 'sh', 'shell', 'console', 'c', 'c++', 'error',
                    }
                    skip_current_block = any(attr.strip() in invalid_attrs for attr in attributes)
                else:
                    # END OF BLOCK
                    in_code_block = False
                    # Only save if the block had no #[doc = ...] lines inside it.
                    # Such lines carry code we can't evaluate (e.g. concat!(...)),
                    # so the extracted snippet would be incomplete.
                    if not skip_current_block and not has_doc_attr_in_block and current_block:
                        save_snippet(library, file_path, block_counter, current_block, output_dir)
                        block_counter += 1
                        current_block = []
                    skip_current_block = False
                    has_doc_attr_in_block = False

            elif in_code_block and not skip_current_block:
                # Handle rustdoc hidden lines (lines starting with '# ' or just '#'),
                # which may be indented, so check the lstripped content.
                stripped_doc = doc_content.lstrip()
                leading = doc_content[:len(doc_content) - len(stripped_doc)]
                if stripped_doc.startswith('# '):
                    current_block.append(leading + stripped_doc[2:])
                elif stripped_doc == '#':
                    current_block.append("")
                else:
                    current_block.append(doc_content)
        else:
            # #[doc = ...] attributes are doc content written as attributes rather
            # than /// comments (common with concat! in macro-generated docs).
            # Don't reset block state for them — they often appear between ``` fences
            # and treating them as "normal code" turns the next closing ``` into a
            # spurious opener that scoops up prose.
            if re.match(r'\s*#\[doc\s*=', stripped_line) or doc_attr_depth > 0:
                # This is either the opening line of a #[doc = ...] attribute or a
                # continuation line of a multiline one. Track bracket depth so we
                # know when the attribute closes and don't reset block state for any
                # of these lines.
                if in_code_block:
                    has_doc_attr_in_block = True
                doc_attr_depth += stripped_line.count('[') - stripped_line.count(']')
                doc_attr_depth = max(doc_attr_depth, 0)
            else:
                # If we hit normal code, reset block state just in case of malformed docs
                in_code_block = False
                skip_current_block = False
                current_block = []


# Matches the start of a top-level item that must live outside fn main().
# Covers optional visibility (pub, pub(crate), …) and qualifiers (async, unsafe).
_OUTER_ITEM_RE = re.compile(
    r'^(?:pub(?:\s*\([^)]*\))?\s+)?(?:(?:async|unsafe|default)\s+)*'
    r'(?:extern "C" fn|fn|struct|enum|impl|trait)\b'
)


def _split_outer_items(lines: list) -> tuple[list, list]:
    """Split lines into (outer_lines, body_lines).

    Assumes outer items come first. Scans forward collecting blank lines,
    comments, attributes, use statements, and item definitions (tracking brace
    depth). Switches to body mode at the first line that is clearly a statement.
    Returns flat line lists; outer_lines preserves original whitespace/comments.
    """
    outer: list[str] = []
    i = 0
    depth = 0
    in_item = False
    seen_brace = False  # whether we've seen the opening '{' of the current item

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if in_item:
            outer.append(line)
            depth += s.count('{') - s.count('}')
            if depth > 0:
                seen_brace = True
            # Item is complete when braces balance AND we've seen the opening
            # brace (rules out where-clause lines which also have depth 0).
            if depth <= 0 and (seen_brace or s.endswith(';')):
                in_item = False
                seen_brace = False
                depth = 0
        elif not s or s.startswith('//') or (s.startswith('#[') and s.count('{') <= s.count('}')) or s.startswith('use ') or _OUTER_ITEM_RE.match(s):
            outer.append(line)
            if s.startswith('use ') or _OUTER_ITEM_RE.match(s):
                depth = s.count('{') - s.count('}')
                if depth > 0:
                    seen_brace = True
                # Enter multi-line mode unless the item is already complete
                # (depth back to 0 and ends with '}' or ';').
                if depth > 0 or not (s.endswith('}') or s.endswith(';')):
                    in_item = True
        else:
            break  # first real statement — switch to body
        i += 1

    return outer, lines[i:]


def _remove_prusti_injected_features(lines: list[str]) -> list[str]:
    """Strip feature flags that Prusti always injects via -Zcrate-attr.

    Prusti injects stmt_expr_attributes, so snippets that already declare it
    would trigger E0636 (feature already enabled) during the pre-check.
    """
    result = []
    for line in lines:
        if re.search(r'#!\[feature\(', line) and 'stmt_expr_attributes' in line:
            # Remove the entry together with any trailing ", " (when first/middle)
            new = re.sub(r'\bstmt_expr_attributes\s*,\s*', '', line)
            # …or any leading ", " (when last) or alone (no comma)
            new = re.sub(r',?\s*\bstmt_expr_attributes\b', '', new)
            # Drop the whole line if the feature list is now empty
            if re.search(r'#!\[feature\(\s*\)\]', new):
                continue
            line = new
        result.append(line)
    return result


def _has_top_level_question_op(body_content: str) -> bool:
    """Return True if body_content contains a ? operator at brace-depth 0.

    Strips double-quoted string literals first (e.g. "what?!!") and only
    counts ? at depth 0 so that ? inside nested fns or closures is ignored.
    Excludes format specifiers :? and :#?.
    """
    stripped = re.sub(r'"([^"\\]|\\.)*"', '""', body_content)
    depth = 0
    prev = ''
    for c in stripped:
        if c == '{':
            depth += 1
        elif c == '}':
            depth = max(depth - 1, 0)
        elif c == '?' and depth == 0 and prev not in (':', '#'):
            return True
        prev = c
    return False


def save_snippet(library: str, original_file: Path, index: int, lines: list, output_dir: Path):
    lines = _remove_prusti_injected_features(lines)
    content = "\n".join(lines)

    # Rustdoc auto-wraps code in a main function if fn main is not already defined.
    if not re.search(r'\bfn\s+main\s*\(', content):
        # #![...] inner attributes must stay at crate root, outside fn main.
        # They may span multiple lines (e.g. #![feature(\n    foo,\n    bar,\n)]),
        # so track bracket depth rather than checking each line in isolation.
        inner_attrs, body_lines = [], []
        depth = 0
        in_inner_attr = False
        for l in lines:
            s = l.strip()
            if in_inner_attr:
                inner_attrs.append(l)
                depth += s.count('[') - s.count(']')
                if depth <= 0:
                    in_inner_attr = False
            elif s.startswith('#!['):
                inner_attrs.append(l)
                depth = s.count('[') - s.count(']')
                if depth > 0:
                    in_inner_attr = True
            else:
                body_lines.append(l)
        prefix = "\n".join(inner_attrs) + "\n" if inner_attrs else ""

        # Hoist top-level items (fn, struct, enum, impl, trait) outside main().
        outer_lines, body_lines = _split_outer_items(body_lines)
        outer_prefix = "\n".join(outer_lines) + "\n" if outer_lines else ""

        indented = "\n".join("    " + l for l in body_lines)
        body_content = "\n".join(body_lines)
        # Detect a genuine top-level ? operator (not inside a nested fn/closure
        # or a string literal).  Strip double-quoted strings first, then check
        # for ? only at brace-depth 0 (excluding format specifiers :? / :#?).
        uses_question_op = _has_top_level_question_op(body_content)
        # Check if the last expression is already Ok/Err (with or without `?`).
        # Such snippets need a Result-returning main even without `?`.
        last_line = next((l.strip() for l in reversed(body_lines) if l.strip()), "")
        already_has_ok = (
            bool(re.search(r'\bOk\b|\bErr\b', last_line))
            and not last_line.endswith((';', '}'))
        )
        if uses_question_op or already_has_ok:
            if already_has_ok and uses_question_op:
                # Both: a genuine top-level ? constrains E, and the body already
                # ends with Ok/Err.  Use impl Debug so non-Error types (e.g.
                # Box<dyn Any + Send> from JoinHandle::join) are accepted.
                return_sig = "impl core::fmt::Debug"
                ok_suffix = ""
            elif already_has_ok:
                # No top-level ?: E is unconstrained; use a concrete return type
                # and normalise the last expression to plain Ok(()) to avoid
                # type-inference ambiguity.
                last_idx = next(i for i in range(len(body_lines) - 1, -1, -1) if body_lines[i].strip())
                body_lines = body_lines[:last_idx] + ["Ok(())"]
                indented = "\n".join("    " + l for l in body_lines)
                return_sig = "Box<dyn std::error::Error>"
                ok_suffix = ""
            else:
                # uses_question_op only: genuine top-level ? is present, body
                # does not end with Ok/Err.  Use impl Debug so non-Error types
                # are accepted; E is inferred from the ? usages.
                return_sig = "impl core::fmt::Debug"
                ok_suffix = "\n    Ok(())"
            content = (prefix + outer_prefix
                + f"fn main() -> Result<(), {return_sig}> {{\n"
                + indented + ok_suffix + "\n}")
        else:
            content = prefix + outer_prefix + "fn main() {\n" + indented + "\n}"

    # Create a safe filename based on the original file path
    safe_filename = f"{library}_{original_file.stem}_doctest_{index}.rs"
    out_path = output_dir / safe_filename

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)


def cmd_extract(args):
    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)

    if not source_dir.is_dir():
        print(f"Error: Source directory {source_dir} does not exist.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    rs_files = [
        Path(root) / file
        for root, _, files in os.walk(source_dir)
        for file in files
        if file.endswith('.rs')
    ]

    for file_path in tqdm(rs_files, desc="Extracting", unit="file"):
        process_file(args.library, file_path, output_dir)

    print(f"Done! Scanned {len(rs_files)} Rust files and extracted snippets to {output_dir}")


def compile_one(rs_file: Path, bin_dir: Path, prusti_rustc: Path) -> tuple[bool, str]:
    out_bin = bin_dir / rs_file.stem
    result = subprocess.run(
        ["rustc", "+nightly", "--edition", "2021", str(rs_file), "-o", str(out_bin)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr
    env = os.environ.copy()
    env.update(PRUSTI_NO_VERIFY_ENV)
    with tempfile.TemporaryDirectory() as tmp:
        pcheck = subprocess.run(
            [str(prusti_rustc), "--edition", "2021", "--crate-type=lib", "--out-dir", tmp, str(rs_file)],
            capture_output=True,
            text=True,
            env=env,
        )
    if pcheck.returncode != 0:
        out_bin.unlink(missing_ok=True)
        return False, pcheck.stderr
    return True, result.stderr


def cmd_compile(args):
    snippets_dir = Path(args.snippets_dir)
    bin_dir = Path(args.bin_dir)
    prusti_rustc = Path(args.prusti_rustc)

    if not snippets_dir.is_dir():
        print(f"Error: Snippets directory {snippets_dir} does not exist.")
        return
    if not prusti_rustc.is_file():
        print(f"Error: prusti-rustc not found at {prusti_rustc}")
        return

    bin_dir.mkdir(parents=True, exist_ok=True)

    rs_files = sorted(snippets_dir.glob("*.rs"))
    ok = fail = 0
    failures = []

    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(compile_one, f, bin_dir, prusti_rustc): f for f in rs_files}
        with tqdm(total=len(rs_files), desc="Compiling", unit="file") as pbar:
            for future in as_completed(futures):
                success, stderr = future.result()
                if success:
                    ok += 1
                else:
                    fail += 1
                    failures.append((futures[future], stderr))
                pbar.update(1)

    for rs_file, stderr in sorted(failures):
        print(f"\n--- {rs_file.name} ---")
        print(stderr.strip())

    print(f"\nDone! {ok} compiled OK, {fail} failed.")


def cmd_run(args):
    bin_dir = Path(args.bin_dir)

    if not bin_dir.is_dir():
        print(f"Error: Binary directory {bin_dir} does not exist.")
        return

    binaries = [p for p in sorted(bin_dir.iterdir()) if p.is_file() and os.access(p, os.X_OK)]
    ok = fail = 0
    failures = []

    for binary in tqdm(binaries, desc="Running", unit="binary"):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            ok += 1
        else:
            fail += 1
            output = (result.stdout + result.stderr).strip()
            failures.append((binary, result.returncode, output))

    for binary, returncode, output in failures:
        print(f"\n--- {binary.name} (exit {returncode}) ---")
        if output:
            print(output)

    print(f"\nDone! {ok} passed, {fail} failed.")


def cmd_copy_passing(args):
    snippets_dir = Path(args.snippets_dir)
    bin_dir      = Path(args.bin_dir)
    dest_dir     = Path(args.dest_dir)

    if not snippets_dir.is_dir():
        print(f"Error: Snippets directory {snippets_dir} does not exist.")
        return
    if not bin_dir.is_dir():
        print(f"Error: Binary directory {bin_dir} does not exist.")
        return

    dest_dir.mkdir(parents=True, exist_ok=True)

    binaries = [p for p in sorted(bin_dir.iterdir()) if p.is_file() and os.access(p, os.X_OK)]
    copied = skipped = 0

    for binary in tqdm(binaries, desc="Checking", unit="binary"):
        result = subprocess.run([str(binary)], capture_output=True, timeout=10)
        if result.returncode == 0:
            snippet = snippets_dir / (binary.name + ".rs")
            if snippet.exists():
                shutil.copy2(snippet, dest_dir / snippet.name)
                copied += 1
        else:
            skipped += 1

    print(f"\nDone! {copied} snippets copied, {skipped} skipped (failed at runtime).")


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT    NOT NULL,
            success   TEXT    NOT NULL,  -- 'success', 'fail', or 'timeout'
            output    TEXT,
            timestamp TEXT    DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn



PRUSTI_ENV = {
    "PRUSTI_SKIP_UNSUPPORTED_FEATURES": "true",
    "PRUSTI_INTERNAL_ERRORS_AS_WARNINGS": "true",
    "PRUSTI_QUIET": "true",
    "PRUSTI_FULL_COMPILATION": "true",
    "PRUSTI_CARGO": "",
}

PRUSTI_NO_VERIFY_ENV = {
    **PRUSTI_ENV,
    "PRUSTI_NO_VERIFY": "true",
}


def _run_prusti(rs_file: Path, prusti_rustc: Path, timeout: int, env: dict) -> tuple[str, str]:
    """Run prusti-rustc with the given env; return (status, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.Popen(
            [str(prusti_rustc), "--edition", "2021", "--crate-type=lib", "--out-dir", tmp, str(rs_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            _, stderr = proc.communicate(timeout=timeout)
            return ("success" if proc.returncode == 0 else "fail"), stderr
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return "timeout", ""


def _wait_for_server(port: int, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=0.5):
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"prusti-server did not start within {timeout:.0f}s")


def prusti_one(rs_file: Path, prusti_rustc: Path, timeout: int, server_address: str | None = None) -> tuple[str, str]:
    env = os.environ.copy()
    env.update(PRUSTI_ENV)
    if server_address:
        env["PRUSTI_SERVER_ADDRESS"] = server_address
    return _run_prusti(rs_file, prusti_rustc, timeout, env)


def cmd_prusti(args):
    prusti_rustc = Path(args.prusti_rustc)

    if not prusti_rustc.is_file():
        print(f"Error: prusti-rustc not found at {prusti_rustc}")
        return

    if args.file:
        rs_file = Path(args.file)
        if not rs_file.is_file():
            print(f"Error: {rs_file} does not exist.")
            return
        rs_files = [rs_file]
    else:
        dest_dir = Path(args.dest_dir)
        if not dest_dir.is_dir():
            print(f"Error: Directory {dest_dir} does not exist.")
            return
        rs_files = sorted(dest_dir.glob("*.rs"))
    if args.db:
        db_path = Path(args.db)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        db_path = Path(f"prusti_{timestamp}.db")
    conn = init_db(db_path)
    print(f"Writing results to {db_path}")

    timeout = args.timeout
    ok = fail = timed_out = 0
    failures = []

    prusti_server = prusti_rustc.parent / "prusti-server"
    server_proc = None
    server_address = None
    try:
        server_proc = subprocess.Popen(
            [str(prusti_server), "--port", str(PRUSTI_SERVER_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        _wait_for_server(PRUSTI_SERVER_PORT)
        server_address = f"localhost:{PRUSTI_SERVER_PORT}"
        print(f"prusti-server started on port {PRUSTI_SERVER_PORT}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(prusti_one, f, prusti_rustc, timeout, server_address): f for f in rs_files}
            with tqdm(total=len(rs_files), desc="Prusti", unit="file") as pbar:
                for future in as_completed(futures):
                    rs_file = futures[future]
                    status, stderr = future.result()
                    conn.execute(
                        "INSERT INTO results (file_name, success, output) VALUES (?, ?, ?)",
                        (rs_file.name, status, stderr),
                    )
                    conn.commit()
                    if status == "success":
                        ok += 1
                    elif status == "fail":
                        fail += 1
                        failures.append((rs_file, stderr))
                    else:
                        timed_out += 1
                    pbar.update(1)
    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()

    conn.close()

    for rs_file, stderr in sorted(failures):
        print(f"\n--- {rs_file.name} ---")
        print(stderr.strip())

    print(f"\nDone! {ok} passed, {fail} failed, {timed_out} timed out.")


def cmd_full(args):
    clean_passing = args.dest_dir is None
    dest_dir = args.dest_dir or "tests/"

    if clean_passing:
        p = Path(dest_dir)
        if p.is_dir():
            for f in p.glob("*.rs"):
                f.unlink()

    args.dest_dir = dest_dir

    for lib in ["alloc", "core"]:
        for d, pattern in [(Path(f"{lib}/snippets/"), "*.rs"), (Path(f"{lib}/bin/"), "*")]:
            if d.is_dir():
                for f in d.glob(pattern):
                    if f.is_file():
                        f.unlink()

    for lib in ["alloc", "core"]:
        print(f"=== {lib} ===")
        cmd_extract(argparse.Namespace(library=lib, source_dir=f"{lib}/src/", output_dir=f"{lib}/snippets/"))
        cmd_compile(argparse.Namespace(snippets_dir=f"{lib}/snippets/", bin_dir=f"{lib}/bin/", prusti_rustc=args.prusti_rustc))
        cmd_copy_passing(argparse.Namespace(snippets_dir=f"{lib}/snippets/", bin_dir=f"{lib}/bin/", dest_dir=args.dest_dir))

    if not args.noconfirm:
        print("\nReady to launch Prusti verification (time-intensive).")
        input("Press Enter to continue, or Ctrl+C to abort...")

    for lib in ["alloc", "core"]:
        for f in Path(f"{lib}/bin/").glob("*"):
            if f.is_file():
                f.unlink()

    if args.db:
        db = args.db
    else:
        r = subprocess.run([args.prusti_rustc, "--version"], capture_output=True, text=True)
        ver = r.stdout + r.stderr
        m = re.search(r'commit ([0-9a-f]+) (\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', ver)
        if m:
            h, Y, Mo, D, H, Mi, S = m.groups()
            db = f"prusti-{Y}{Mo}{D}-{H}{Mi}{S}-{h}.db"
        else:
            db = f"prusti_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db"
    cmd_prusti(argparse.Namespace(prusti_rustc=args.prusti_rustc, dest_dir=args.dest_dir, file=None, db=db, timeout=args.timeout))


def main():
    parser = argparse.ArgumentParser(description="Rust doctest extractor, compiler, and runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # extract subcommand
    p_extract = subparsers.add_parser("extract", help="Extract doctests from Rust source files.")
    p_extract.add_argument("--src-dir", required=True, dest="source_dir", help="Path to the Rust source directory (e.g., src/)")
    p_extract.add_argument("--snippets-dir", required=True, dest="output_dir", help="Directory to write extracted .rs snippets into")
    p_extract.add_argument("--library", required=True, dest="library", help="Name of the library, e.g. 'core' or 'alloc'")
    p_extract.set_defaults(func=cmd_extract)

    # compile subcommand
    p_compile = subparsers.add_parser("compile", help="Compile extracted .rs snippets with rustc.")
    p_compile.add_argument("--snippets-dir", required=True, dest="snippets_dir", help="Directory containing extracted .rs snippets")
    p_compile.add_argument("--bin-dir", required=True, dest="bin_dir", help="Directory to write compiled binaries into")
    p_compile.add_argument("--prusti", required=True, dest="prusti_rustc", help="Path to prusti-rustc; runs PRUSTI_NO_VERIFY pre-check and rejects snippets that fail it")
    p_compile.set_defaults(func=cmd_compile)

    # run subcommand
    p_run = subparsers.add_parser("run", help="Run compiled snippet binaries.")
    p_run.add_argument("--bin-dir", required=True, dest="bin_dir", help="Directory containing compiled binaries")
    p_run.set_defaults(func=cmd_run)

    # copy-passing subcommand
    p_copy = subparsers.add_parser("copy-passing", help="Copy snippets that compiled and ran successfully to a new directory.")
    p_copy.add_argument("--snippets-dir", required=True, dest="snippets_dir", help="Directory containing extracted .rs snippets")
    p_copy.add_argument("--bin-dir", required=True, dest="bin_dir", help="Directory containing compiled binaries")
    p_copy.add_argument("--passing-dir", required=True, dest="dest_dir", help="Directory to copy passing snippets into")
    p_copy.set_defaults(func=cmd_copy_passing)

    # prusti subcommand
    p_prusti = subparsers.add_parser("prusti", help="Run prusti-rustc on snippets.")
    p_prusti.add_argument("--prusti", required=True, dest="prusti_rustc", help="Path to the prusti-rustc executable")
    prusti_target = p_prusti.add_mutually_exclusive_group(required=True)
    prusti_target.add_argument("--passing-dir", dest="dest_dir", help="Directory containing .rs snippets to verify")
    prusti_target.add_argument("--file", help="Single .rs file to verify")
    p_prusti.add_argument("--timeout", type=int, default=60, help="Timeout per file in seconds (default: 60)")
    p_prusti.add_argument("--db", help="Path to SQLite database (default: prusti_<timestamp>.db)")
    p_prusti.set_defaults(func=cmd_prusti)

    # full subcommand
    p_full = subparsers.add_parser("full", help="Extract, compile, and copy passing snippets for alloc+core, then verify with Prusti.")
    p_full.add_argument("--prusti", required=True, dest="prusti_rustc", help="Path to the prusti-rustc executable")
    p_full.add_argument("--passing-dir", default=None, dest="dest_dir", help="Directory to copy passing snippets into (default: tests/, cleaned before use)")
    p_full.add_argument("--timeout", type=int, default=60, help="Timeout per file in seconds (default: 60)")
    p_full.add_argument("--db", help="Path to SQLite database (default: prusti_<timestamp>.db)")
    p_full.add_argument("--noconfirm", action="store_true", help="Skip confirmation prompt before Prusti step")
    p_full.set_defaults(func=cmd_full)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
