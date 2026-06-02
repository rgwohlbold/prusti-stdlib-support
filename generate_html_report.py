#!/usr/bin/env python3
"""Generate static HTML for Prusti analysis results.

Usage:
    browse.py [--db <path> [<path> ...]] [--output <dir>]

Defaults to all prusti-*.db files in the current directory and output to ./static.
"""
import argparse
import html
import re
import shutil
import urllib.parse
from pathlib import Path

_NO_BRANCH_RE = re.compile(r'^prusti-\d{8}-\d{6}-[0-9a-f]+$')

import polars as pl
import analysis

_VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'

_COMMON_STYLE = """
  body { font-family: sans-serif; margin: 2em; color: #222; }
  h1   { font-size: 1.4em; }
  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 6px 14px; text-align: left; }
  th { background: #f3f3f3; }
  tr:nth-child(even) { background: #fafafa; }
  .no-issue { color: #888; }
  a { color: #1a6eb5; }
  @media (max-width: 600px) {
    body { margin: 1em; }
    th, td { padding: 4px 8px; font-size: 0.9em; }
  }
"""


def _db_list_page(dbs: dict[str, pl.DataFrame]) -> str:
    names = sorted(dbs)

    # Summary table
    summary_rows = []
    for name in names:
        df      = dbs[name]
        total   = len(df)
        success = len(df.filter(df["success"] == "success"))
        fail    = len(df.filter(df["success"] == "fail"))
        timeout = len(df.filter(df["success"] == "timeout"))
        stem = Path(name).stem
        link = f'<a href="/db/{urllib.parse.quote(stem)}/">{html.escape(name)}</a>'
        summary_rows.append(
            f"<tr><td>{link}</td>"
            f"<td style='text-align:right'>{total}</td>"
            f"<td style='text-align:right'>{success}</td>"
            f"<td style='text-align:right'>{fail}</td>"
            f"<td style='text-align:right'>{timeout}</td></tr>"
        )
    summary_html = "\n".join(summary_rows)

    # Category comparison table
    db_cat_maps: dict[str, dict[str, int]] = {}
    all_cats: set[str] = set()
    for name, df in dbs.items():
        cats = (
            df.filter(df["category"].is_not_null() & (df["category"] != "") & (df["success"] == "fail"))
              .group_by("category")
              .agg(pl.len().alias("count"))
        )
        cat_map = {row["category"]: row["count"] for row in cats.iter_rows(named=True)}
        db_cat_maps[name] = cat_map
        all_cats.update(cat_map.keys())

    no_branch = [n for n in names if _NO_BRANCH_RE.match(Path(n).stem)]
    sort_ref = no_branch[-1] if no_branch else names[-1]
    sorted_cats = sorted(all_cats, key=lambda c: -db_cat_maps[sort_ref].get(c, 0))
    col_headers = "".join(f"<th>{html.escape(Path(n).stem)}</th>" for n in names)
    ref_stem = urllib.parse.quote(Path(sort_ref).stem)
    compare_rows = []
    for cat in sorted_cats:
        slug = _cat_slug(cat)
        cat_cell = f'<a href="/db/{ref_stem}/category/{urllib.parse.quote(slug)}/">{html.escape(cat)}</a>'
        cells = "".join(
            f"<td style='text-align:right'>{db_cat_maps[n].get(cat, 0)}</td>"
            for n in names
        )
        compare_rows.append(f"<tr><td>{cat_cell}</td>{cells}</tr>")
    compare_html = "\n".join(compare_rows)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">{_VIEWPORT}
<title>Prusti Analysis</title>
<style>{_COMMON_STYLE}</style>
</head>
<body>
<h1>Prusti Analysis — Databases</h1>
<div class="table-wrap"><table>
<tr><th>Database</th><th>Total</th><th>Success</th><th>Fail</th><th>Timeout</th></tr>
{summary_html}
</table></div>
<h2>Category comparison</h2>
<div class="table-wrap"><table>
<tr><th>Category</th>{col_headers}</tr>
{compare_html}
</table></div>
</body>
</html>"""


def _cat_slug(cat: str) -> str:
    """Turn a category string into a URL-safe slug."""
    import hashlib
    slug = re.sub(r'[^a-z0-9]+', '-', cat.lower()).strip('-')
    if not slug:
        slug = 'unknown'
    if len(slug) > 80:
        h = hashlib.sha1(cat.encode()).hexdigest()[:12]
        slug = slug[:80].rstrip('-') + '-' + h
    return slug


def _index_page(db_name: str, df: pl.DataFrame) -> str:
    total   = len(df)
    success = len(df.filter(df["success"] == "success"))
    fail    = len(df.filter(df["success"] == "fail"))
    timeout = len(df.filter(df["success"] == "timeout"))

    cats = (
        df.filter(df["category"].is_not_null() & (df["category"] != "") & (df["success"] == "fail"))
          .group_by("category")
          .agg(pl.len().alias("count"))
          .sort("count", descending=True)
    )

    rows = []
    for row in cats.iter_rows(named=True):
        cat   = row["category"]
        count = row["count"]
        slug = _cat_slug(cat)
        link = f'<a href="category/{urllib.parse.quote(slug)}/">{html.escape(cat)}</a>'
        rows.append(f"<tr><td>{link}</td><td style='text-align:right'>{count}</td></tr>")

    rows_html = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">{_VIEWPORT}
<title>Prusti Analysis — {html.escape(db_name)}</title>
<style>{_COMMON_STYLE}
  .summary {{ margin-bottom: 1.5em; color: #555; }}
</style>
</head>
<body>
<h1>Prusti Analysis — {html.escape(db_name)}</h1>
<p class="summary">
  Total: <b>{total}</b> &nbsp;|&nbsp;
  Success: <b>{success}</b> &nbsp;|&nbsp;
  Fail: <b>{fail}</b> &nbsp;|&nbsp;
  Timeout: <b>{timeout}</b>
</p>
<h2>Failure categories</h2>
<div class="table-wrap"><table>
<tr><th>Category</th><th>Count</th></tr>
{rows_html}
</table></div>
</body>
</html>"""


def _category_page(db_name: str, category: str, df: pl.DataFrame) -> str:
    files = (
        df.filter((pl.col("category") == category) & (pl.col("success") == "fail"))
          .select("file_name")
          .sort("file_name")
    )
    rows = "\n".join(
        f"<tr><td><a href=\"/snippets/{urllib.parse.quote(row['file_name'])}.txt\">{html.escape(row['file_name'])}</a></td></tr>"
        for row in files.iter_rows(named=True)
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">{_VIEWPORT}
<title>{html.escape(category)} — {html.escape(db_name)}</title>
<style>{_COMMON_STYLE}</style>
</head>
<body>
<h1>{html.escape(category)}</h1>
<p>Database: <b>{html.escape(db_name)}</b></p>
<p>{len(files)} file(s) affected</p>
<div class="table-wrap"><table>
<tr><th>File</th></tr>
{rows}
</table></div>
</body>
</html>"""


def generate(dbs: dict[str, pl.DataFrame], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    multi = len(dbs) > 1

    # Root index
    if multi:
        (output_dir / "index.html").write_text(_db_list_page(dbs), encoding="utf-8")
    else:
        name = next(iter(dbs))
        stem = urllib.parse.quote(Path(name).stem)
        redirect = (
            f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0; url=/db/{stem}/">'
            f'</head><body><a href="/db/{stem}/">Go to analysis</a></body></html>'
        )
        (output_dir / "index.html").write_text(redirect, encoding="utf-8")

    # Per-db pages
    for name, df in dbs.items():
        db_dir = output_dir / "db" / Path(name).stem
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "index.html").write_text(_index_page(name, df), encoding="utf-8")

        # Per-category pages
        cats = (
            df.filter(pl.col("category").is_not_null() & (pl.col("category") != "") & (pl.col("success") == "fail"))
              .get_column("category")
              .unique()
        )
        for cat in cats:
            slug = _cat_slug(cat)
            cat_dir = db_dir / "category" / slug
            cat_dir.mkdir(parents=True, exist_ok=True)
            (cat_dir / "index.html").write_text(
                _category_page(name, cat, df), encoding="utf-8"
            )

    # Copy .rs snippet files
    snippet_dirs = [Path("core/snippets"), Path("alloc/snippets")]
    snippets_out = output_dir / "snippets"
    snippets_out.mkdir(parents=True, exist_ok=True)
    found_any = False
    for src_dir in snippet_dirs:
        if not src_dir.is_dir():
            continue
        for rs_file in src_dir.glob("*.rs"):
            shutil.copy2(rs_file, snippets_out / (rs_file.name + ".txt"))
            found_any = True
    if not found_any:
        raise FileNotFoundError("No .rs snippet files found in core/snippets or alloc/snippets")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db",     nargs="*", help="Path(s) to .db files (default: all prusti-*.db)")
    parser.add_argument("--output", default="static", help="Output directory (default: static)")
    args = parser.parse_args()

    import sys

    if args.db:
        db_paths = [Path(p) for p in args.db]
    else:
        db_paths = sorted(Path(".").glob("prusti-*.db"))
        if not db_paths:
            print("Error: no prusti-*.db files found", file=sys.stderr)
            sys.exit(1)

    dbs: dict[str, pl.DataFrame] = {}
    for db_path in db_paths:
        if not db_path.exists():
            print(f"Warning: {db_path} not found, skipping", file=sys.stderr)
            continue
        print(f"Loading {db_path.name} …")
        dbs[db_path.name] = analysis.transform(analysis.load_dbs([db_path]))

    if not dbs:
        print("Error: no valid database files loaded", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)
    generate(dbs, output_dir)
    print(f"Generated {len(dbs)} database(s) → {output_dir}/")


if __name__ == "__main__":
    main()
