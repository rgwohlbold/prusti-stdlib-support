#!/usr/bin/env python3
"""Browse Prusti analysis results in a web browser.

Usage:
    browse.py [--db <path>] [--port N]

Defaults to the most recent prusti-*.db file and port 8765.
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


def _index_page(db_path: Path, df: pl.DataFrame) -> str:
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
        rows.append(f"<tr><td>{link}</td><td>{count}</td></tr>")

    rows_html = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Prusti Analysis</title>
<style>
  body {{ font-family: sans-serif; max-width: 960px; margin: 2em auto; color: #222; }}
  h1   {{ font-size: 1.4em; }}
  .summary {{ margin-bottom: 1.5em; color: #555; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 14px; text-align: left; }}
  th {{ background: #f3f3f3; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  td:last-child {{ text-align: right; width: 5em; }}
  .no-issue {{ color: #888; }}
  a {{ color: #1a6eb5; }}
</style>
</head>
<body>
<h1>Prusti Analysis — {html.escape(db_path.name)}</h1>
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


def make_handler(db_path: Path, df: pl.DataFrame):
    index_html = _index_page(db_path, df)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path   = urllib.parse.unquote(parsed.path)

            if path == "/":
                self._respond(200, index_html)
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

        def log_message(self, fmt, *args):  # suppress per-request noise
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db",   help="Path to .db file (default: most recent prusti-*.db)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else sorted(Path(".").glob("prusti-*.db"))[-1]
    if not db_path.exists():
        import sys
        print(f"Error: {db_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {db_path.name} …")
    df = pa.transform(pa.load_dbs([db_path]))

    Handler = make_handler(db_path, df)
    with http.server.HTTPServer(("", args.port), Handler) as server:
        print(f"Serving at http://localhost:{args.port}  (Ctrl-C to stop)")
        server.serve_forever()


if __name__ == "__main__":
    main()
