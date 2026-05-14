"""Outcome counts (success / fail / timeout) over time as a line chart.

Loads every DB whose filename matches `prusti-YYYYMMDD-HHMMSS-<hash>.db`
(i.e. no branch name embedded) and plots one line per outcome, with
runs equally spaced along the x-axis.
Writes outcomes_over_time.pdf for inclusion in LaTeX.
"""

import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns

import analysis

DB_RE = re.compile(r"^prusti-(\d{8})-(\d{6})-[0-9a-f]+\.db$")

OUTCOME_ORDER = ["success", "fail", "timeout"]
PALETTE = {"success": "#4c9f70", "fail": "#c44e52", "timeout": "#dd8452"}


def find_dbs(root: Path) -> list[tuple[datetime, Path]]:
    found = []
    for p in sorted(root.glob("prusti-*.db")):
        m = DB_RE.match(p.name)
        if m:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            found.append((ts, p))
    return found


def main(out_path: Path) -> None:
    dbs = find_dbs(Path("."))
    print(f"Loading {len(dbs)} databases")

    rows = []
    for ts, path in dbs:
        df = analysis.transform(analysis.load_dbs([path]))
        vc = dict(df["success"].value_counts().iter_rows())
        rows.append({
            "timestamp": ts,
            "success": vc.get("success", 0),
            "fail": vc.get("fail", 0),
            "timeout": vc.get("timeout", 0),
        })

    summary = pl.DataFrame(rows).sort("timestamp").with_row_index("idx")
    print(summary)

    timestamps = summary["timestamp"].to_list()
    date_labels = [t.strftime("%Y-%m-%d") for t in timestamps]
    xs = list(summary["idx"])

    long = summary.unpivot(
        index=["idx", "timestamp"],
        on=OUTCOME_ORDER,
        variable_name="outcome",
        value_name="count",
    ).to_pandas()

    sns.set_theme(style="whitegrid", context="paper")
    fig, ax = plt.subplots(figsize=(7.5, 3.5))
    sns.lineplot(
        data=long,
        x="idx",
        y="count",
        hue="outcome",
        hue_order=OUTCOME_ORDER,
        palette=PALETTE,
        marker="o",
        ax=ax,
    )

    ax.set_xlabel("Commit date")
    ax.set_ylabel("Number of tests")
    ax.set_xticks(xs)
    ax.set_xticklabels(date_labels, rotation=45, ha="right")
    ax.legend(title="Outcome", frameon=False)
    sns.despine()

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main(Path("outcomes_over_time.pdf"))
