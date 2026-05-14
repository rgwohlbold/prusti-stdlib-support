"""Top-N failure categories over time.

Loads every DB whose filename matches `prusti-YYYYMMDD-HHMMSS-<hash>.db`
and draws a small-multiples grid of bar charts: one panel per category
(top N ranked by total occurrences across all runs), bars per run on the
x-axis. Each panel has its own y-scale so trends within rare categories
remain readable next to the dominant ones.

Writes categories_over_time.pdf for inclusion in LaTeX.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns

import analysis

DB_RE = re.compile(r"^prusti-(\d{8})-(\d{6})-[0-9a-f]+\.db$")

TOP_N = 20


def find_dbs(root: Path) -> list[tuple[datetime, Path]]:
    found = []
    for p in sorted(root.glob("prusti-*.db")):
        m = DB_RE.match(p.name)
        if m:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            found.append((ts, p))
    return found


def main(out_path: Path, top_n: int) -> None:
    dbs = find_dbs(Path("."))
    print(f"Loading {len(dbs)} databases")

    rows = []
    for ts, path in dbs:
        df = analysis.transform(analysis.load_dbs([path]))
        fails = df.filter(pl.col("success") == "fail")
        vc = fails["category"].value_counts()
        for cat, n in vc.iter_rows():
            rows.append({"timestamp": ts, "category": cat, "count": int(n)})

    long = pl.DataFrame(rows)

    totals = (
        long.group_by("category")
        .agg(pl.col("count").sum().alias("total"))
        .sort("total", descending=True)
    )
    print("Category totals (all runs):")
    print(totals)

    top = totals.head(top_n)["category"].to_list()
    print(f"\nTop {top_n}: {top}")

    timestamps = sorted({r["timestamp"] for r in rows})
    ts_to_idx = {t: i for i, t in enumerate(timestamps)}
    grid = pl.DataFrame(
        [{"timestamp": t, "category": c} for t in timestamps for c in top]
    )
    plot_df = (
        grid.join(long, on=["timestamp", "category"], how="left")
        .with_columns(
            pl.col("count").fill_null(0),
            pl.col("timestamp").replace_strict(ts_to_idx, return_dtype=pl.Int64).alias("idx"),
        )
        .sort(["category", "idx"])
        .to_pandas()
    )

    sns.set_theme(style="whitegrid", context="paper")
    palette = sns.color_palette("tab20", n_colors=len(top))
    date_labels = [t.strftime("%Y-%m-%d") for t in timestamps]
    xs = list(range(len(timestamps)))

    wide = (
        plot_df.pivot_table(
            index="idx", columns="category", values="count", fill_value=0
        )
        .reindex(columns=top)
        .reindex(index=xs, fill_value=0)
    )

    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    bottom = [0.0] * len(xs)
    for i, cat in enumerate(top):
        values = wide[cat].tolist()
        ax.bar(xs, values, bottom=bottom, color=palette[i], label=cat, width=0.85, edgecolor="none")
        bottom = [b + v for b, v in zip(bottom, values)]

    ax.set_xlabel("Commit date")
    ax.set_ylabel(f"Number of failing tests (top {len(top)} categories)")
    ax.set_xticks(xs)
    ax.set_xticklabels(date_labels, rotation=45, ha="right")
    ax.margins(x=0.01)

    handles, labels = ax.get_legend_handles_labels()
    ax.legend(
        handles[::-1], labels[::-1],
        title="Category",
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=2,
        fontsize=8,
        title_fontsize=9,
    )
    sns.despine()

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N
    main(Path("categories_over_time.pdf"), n)
