#!/usr/bin/env python3
"""Browse Prusti analysis results in a web browser.

Usage:
    browse.py [--db <path> [<path> ...]] [--port N]

Defaults to all prusti-*.db files found in the current directory and port 8765.
"""
import argparse
import html
import http.server
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
  body { font-family: sans-serif; max-width: 960px; margin: 2em auto; color: #222; }
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
    rows = []
    for name in sorted(dbs):
        df      = dbs[name]
        total   = len(df)
        success = len(df.filter(df["success"] == "success"))
        fail    = len(df.filter(df["success"] == "fail"))
        timeout = len(df.filter(df["success"] == "timeout"))
        link = f'<a href="/db/{urllib.parse.quote(name)}">{html.escape(name)}</a>'
        rows.append(
            f"<tr><td>{link}</td>"
            f"<td style='text-align:right'>{total}</td>"
            f"<td style='text-align:right'>{success}</td>"
            f"<td style='text-align:right'>{fail}</td>"
            f"<td style='text-align:right'>{timeout}</td></tr>"
        )
    rows_html = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Prusti Analysis</title>
<style>{_COMMON_STYLE}
  .summary {{ margin-bottom: 1.5em; color: #555; }}
</style>
</head>
<body>
<h1>Prusti Analysis — Databases</h1>
<table>
<tr><th>Database</th><th>Total</th><th>Success</th><th>Fail</th><th>Timeout</th></tr>
{rows_html}
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

    issue_files = {f.stem: f.name for f in ISSUES_DIR.glob("*.md")} if ISSUES_DIR.exists() else {}

    rows = []
    for row in cats.iter_rows(named=True):
        cat   = row["category"]
        count = row["count"]
        fname = issue_files.get(cat)
        if fname:
            link = f'<a href="/issue/{urllib.parse.quote(fname)}">{html.escape(cat)}</a>'
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


def _issue_page(filename: str) -> tuple[str, int]:
    path = ISSUES_DIR / filename
    if not path.exists() or not path.is_file() or path.suffix != ".md":
        return f"<h1>Not found</h1><p>{html.escape(filename)}</p>", 404
    body = _render_markdown(path.read_text())
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(path.stem)}</title>
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
</html>""", 200


def make_handler(dbs: dict[str, pl.DataFrame]):
    multi = len(dbs) > 1
    root_html    = _db_list_page(dbs)
    index_pages  = {name: _index_page(name, df, multi) for name, df in dbs.items()}
    # If single db, redirect root straight to that db page
    single_name  = next(iter(dbs)) if not multi else None

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path   = urllib.parse.unquote(parsed.path)

            if path == "/":
                if single_name:
                    self._redirect(f"/db/{urllib.parse.quote(single_name)}")
                else:
                    self._respond(200, root_html)
            elif path.startswith("/db/"):
                name = path[len("/db/"):]
                if name in index_pages:
                    self._respond(200, index_pages[name])
                else:
                    self._respond(404, "<h1>Database not found</h1>")
            elif path.startswith("/issue/"):
                filename = path[len("/issue/"):]
                body, status = _issue_page(filename)
                self._respond(status, body)
            else:
                self._respond(404, "<h1>Not found</h1>")

        def _respond(self, status: int, body: str):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location: str):
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, fmt, *args):  # suppress per-request noise
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db",   nargs="*", help="Path(s) to .db files (default: all prusti-*.db)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
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

    Handler = make_handler(dbs)
    with http.server.HTTPServer(("", args.port), Handler) as server:
        print(f"Loaded {len(dbs)} database(s).")
        print(f"Serving at http://localhost:{args.port}  (Ctrl-C to stop)")
        server.serve_forever()


if __name__ == "__main__":
    main()
