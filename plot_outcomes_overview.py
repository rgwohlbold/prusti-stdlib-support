"""Overview chart: number of tests by outcome (success / fail / timeout).

Reads the latest prusti-*.db (or a path passed on the command line) and
writes overview_outcomes.pdf for inclusion in LaTeX.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

import analysis


def main(db_path: Path, out_path: Path) -> None:
    print(f"Loading {db_path}")
    df = analysis.transform(analysis.load_dbs([db_path]))
    print(f"{len(df)} rows loaded")

    order = ["success", "fail", "timeout"]
    counts = (
        df["success"]
        .value_counts()
        .to_pandas()
        .set_index("success")
        .reindex(order)
        .fillna(0)
        .astype({"count": int})
        .reset_index()
    )
    total = int(counts["count"].sum())

    sns.set_theme(style="whitegrid", context="paper")
    fig, ax = plt.subplots(figsize=(4.5, 3.0))
    sns.barplot(
        data=counts,
        x="success",
        y="count",
        hue="success",
        order=order,
        palette={"success": "#4c9f70", "fail": "#c44e52", "timeout": "#dd8452"},
        legend=False,
        ax=ax,
    )

    for patch, n in zip(ax.patches, counts["count"]):
        ax.annotate(
            f"{n}\n({n / total:.1%})",
            (patch.get_x() + patch.get_width() / 2, patch.get_height()),
            ha="center", va="bottom", fontsize=9,
        )

    ax.set_xlabel("Outcome")
    ax.set_ylabel("Number of tests")
    ax.set_ylim(0, counts["count"].max() * 1.18)
    sns.despine()

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        db = Path(sys.argv[1])
    else:
        db = sorted(Path(".").glob("prusti-*.db"))[-1]
    main(db, Path("overview_outcomes.pdf"))
