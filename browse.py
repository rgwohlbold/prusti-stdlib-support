#!/usr/bin/env python3
"""Generate static HTML for Prusti analysis results.

Usage:
    browse.py [--db <path> [<path> ...]] [--output <dir>]

Defaults to all prusti-*.db files in the current directory and output to ./static.
"""
import argparse
import html
import urllib.parse
from pathlib import Path

import polars as pl
import prusti_analysis as pa

ISSUES_DIR = Path("issues")


def _render_markdown(text: str) -> str:
    try:
        import markdown
        return markdown.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        return f"<pre>{html.escape(text)}</pre>"


_COMMON_STYLE = """
  body { font-family: sans-serif; margin: 2em; color: #222; }
  h1   { font-size: 1.4em; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 6px 14px; text-align: left; }
  th { background: #f3f3f3; }
  tr:nth-child(even) { background: #fafafa; }
  .no-issue { color: #888; }
  a { color: #1a6eb5; }
  a.back { display: inline-block; margin-bottom: 1.5em; text-decoration: none; }
  a.back:hover { text-decoration: underline; }
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
    issue_stems = {f.stem for f in ISSUES_DIR.glob("*.md")} if ISSUES_DIR.exists() else set()
    db_cat_maps: dict[str, dict[str, int]] = {}
    all_cats: set[str] = set()
    for name, df in dbs.items():
        cats = (
            df.filter(df["category"].is_not_null() & (df["category"] != ""))
              .group_by("category")
              .agg(pl.len().alias("count"))
        )
        cat_map = {row["category"]: row["count"] for row in cats.iter_rows(named=True)}
        db_cat_maps[name] = cat_map
        all_cats.update(cat_map.keys())

    sorted_cats = sorted(all_cats, key=lambda c: -sum(db_cat_maps[n].get(c, 0) for n in names))
    col_headers = "".join(f"<th>{html.escape(Path(n).stem)}</th>" for n in names)
    compare_rows = []
    for cat in sorted_cats:
        if cat in issue_stems:
            cat_cell = f'<a href="/issue/{urllib.parse.quote(cat)}/">{html.escape(cat)}</a>'
        else:
            cat_cell = f'<span class="no-issue">{html.escape(cat)}</span>'
        cells = "".join(
            f"<td style='text-align:right'>{db_cat_maps[n].get(cat, 0)}</td>"
            for n in names
        )
        compare_rows.append(f"<tr><td>{cat_cell}</td>{cells}</tr>")
    compare_html = "\n".join(compare_rows)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Prusti Analysis</title>
<style>{_COMMON_STYLE}</style>
</head>
<body>
<h1>Prusti Analysis — Databases</h1>
<table>
<tr><th>Database</th><th>Total</th><th>Success</th><th>Fail</th><th>Timeout</th></tr>
{summary_html}
</table>
<h2>Category comparison</h2>
<table>
<tr><th>Category</th>{col_headers}</tr>
{compare_html}
</table>
</body>
</html>"""


def _index_page(db_name: str, df: pl.DataFrame, multi: bool) -> str:
    total   = len(df)
    success = len(df.filter(df["success"] == "success"))
    fail    = len(df.filter(df["success"] == "fail"))
    timeout = len(df.filter(df["success"] == "timeout"))

    cats = (
        df.filter(df["category"].is_not_null() & (df["category"] != ""))
          .group_by("category")
          .agg(pl.len().alias("count"))
          .sort("count", descending=True)
    )

    issue_stems = {f.stem for f in ISSUES_DIR.glob("*.md")} if ISSUES_DIR.exists() else set()

    rows = []
    for row in cats.iter_rows(named=True):
        cat   = row["category"]
        count = row["count"]
        if cat in issue_stems:
            link = f'<a href="/issue/{urllib.parse.quote(cat)}/">{html.escape(cat)}</a>'
        else:
            link = f'<span class="no-issue">{html.escape(cat)}</span>'
        rows.append(f"<tr><td>{link}</td><td style='text-align:right'>{count}</td></tr>")

    rows_html = "\n".join(rows)
    back = '<a class="back" href="/">← All databases</a>\n' if multi else ""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Prusti Analysis — {html.escape(db_name)}</title>
<style>{_COMMON_STYLE}
  .summary {{ margin-bottom: 1.5em; color: #555; }}
</style>
</head>
<body>
{back}<h1>Prusti Analysis — {html.escape(db_name)}</h1>
<p class="summary">
  Total: <b>{total}</b> &nbsp;|&nbsp;
  Success: <b>{success}</b> &nbsp;|&nbsp;
  Fail: <b>{fail}</b> &nbsp;|&nbsp;
  Timeout: <b>{timeout}</b>
</p>
<h2>Failure categories</h2>
<table>
<tr><th>Category</th><th>Count</th></tr>
{rows_html}
</table>
</body>
</html>"""


def _issue_page(md_file: Path) -> str:
    body = _render_markdown(md_file.read_text())
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(md_file.stem)}</title>
<style>
  body {{ font-family: sans-serif; max-width: 860px; margin: 2em auto; color: #222; line-height: 1.6; }}
  code {{ background: #f4f4f4; padding: 1px 5px; border-radius: 3px; font-size: .92em; }}
  pre  {{ background: #f4f4f4; padding: 1em; border-radius: 4px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  table {{ border-collapse: collapse; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 12px; }}
  th {{ background: #f3f3f3; }}
  a.back {{ display: inline-block; margin-bottom: 1.5em; color: #1a6eb5; text-decoration: none; }}
  a.back:hover {{ text-decoration: underline; }}
  h1 {{ font-size: 1.5em; }}
</style>
</head>
<body>
<a class="back" href="/">← Back to index</a>
{body}
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
        (db_dir / "index.html").write_text(_index_page(name, df, multi), encoding="utf-8")

    # Issue pages
    if ISSUES_DIR.exists():
        for md_file in ISSUES_DIR.glob("*.md"):
            issue_dir = output_dir / "issue" / md_file.stem
            issue_dir.mkdir(parents=True, exist_ok=True)
            (issue_dir / "index.html").write_text(_issue_page(md_file), encoding="utf-8")


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
        dbs[db_path.name] = pa.transform(pa.load_dbs([db_path]))

    if not dbs:
        print("Error: no valid database files loaded", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)
    generate(dbs, output_dir)
    print(f"Generated {len(dbs)} database(s) → {output_dir}/")


if __name__ == "__main__":
    main()
