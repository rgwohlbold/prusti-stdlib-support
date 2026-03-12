#!/usr/bin/env python3
"""Print full row details for a given Prusti failure category.

Usage:
    show_category.py <category> [--db <path>] [--limit N]

<category> can be a substring; rows whose category contains it are shown.
Defaults to the most recent prusti-*.db file and a limit of 3 rows.
"""
import argparse
import sys
from pathlib import Path

import prusti_analysis as pa


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("category", help="Category string (or substring) to filter on")
    parser.add_argument("--db", help="Path to .db file (default: most recent prusti-*.db)")
    parser.add_argument("--limit", type=int, default=3, help="Max rows to show (default: 3)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else sorted(Path(".").glob("prusti-*.db"))[-1]
    if not db_path.exists():
        print(f"Error: {db_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {db_path.name} ...\n")
    df = pa.transform(pa.load_dbs([db_path]))

    matches = df.filter(df["category"].str.contains(args.category, literal=True))
    total = len(matches)
    if total == 0:
        print(f"No rows found for category containing: {args.category!r}")
        sys.exit(0)

    shown = min(args.limit, total)
    print(f"Found {total} row(s) matching {args.category!r} — showing {shown}:\n")
    print("=" * 80)

    for row in matches.head(shown).iter_rows(named=True):
        print(f"file:            {row['file_name']}")
        print(f"category:        {row['category']}")
        print(f"first_prusti_frame: {row['first_prusti_frame']}")
        print(f"\n--- output ---\n{row['output']}")
        print("=" * 80)


if __name__ == "__main__":
    main()
