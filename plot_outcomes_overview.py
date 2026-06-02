"""Overview chart: number of tests by outcome (success / fail / timeout).

With one DB path argument: shows a single bar per outcome.
With two DB paths: shows two bars side-by-side per outcome, labelled by
each DB's commit date (handy for an initial-vs-end comparison).

Writes overview_outcomes.pdf for inclusion in LaTeX.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

import analysis

ORDER = ["success", "fail", "timeout"]
PALETTE = {"success": "#4c9f70", "fail": "#c44e52", "timeout": "#dd8452"}
DB_RE = re.compile(r"^prusti-(?:.*-)?(\d{8})-(\d{6})-[0-9a-f]+$")


def db_label(path: Path) -> str:
    m = DB_RE.match(path.stem)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    return path.stem


def counts_for(db_path: Path) -> pd.DataFrame:
    print(f"Loading {db_path}")
    df = analysis.transform(analysis.load_dbs([db_path]))
    print(f"  {len(df)} rows")
    return (
        df["success"]
        .value_counts()
        .to_pandas()
        .set_index("success")
        .reindex(ORDER)
        .fillna(0)
        .astype({"count": int})
        .reset_index()
        .assign(run=db_label(db_path))
    )


def main(db_paths: list[Path], out_path: Path) -> None:
    frames = [counts_for(p) for p in db_paths]
    data = pd.concat(frames, ignore_index=True)
    run_order = [db_label(p) for p in db_paths]

    sns.set_theme(style="whitegrid", context="talk", font_scale=1.2)

    if len(db_paths) == 1:
        fig, ax = plt.subplots(figsize=(15, 10))
        sns.barplot(
            data=data, x="success", y="count",
            hue="success", order=ORDER,
            palette=PALETTE, legend=False, ax=ax,
        )
        total = int(data["count"].sum())
        for patch, n in zip(ax.patches, data["count"]):
            ax.annotate(
                f"{n}\n({n / total:.1%})",
                (patch.get_x() + patch.get_width() / 2, patch.get_height()),
                ha="center", va="bottom", fontsize=18,
            )
        ax.set_ylim(0, data["count"].max() * 1.18)
    else:
        fig, ax = plt.subplots(figsize=(15, 10))
        sns.barplot(
            data=data, x="run", y="count",
            hue="success", order=run_order, hue_order=ORDER,
            palette=PALETTE, ax=ax,
        )
        totals = {r: int(data.loc[data["run"] == r, "count"].sum()) for r in run_order}
        # seaborn draws patches hue-first: all success bars across runs, then fail, then timeout
        patch_iter = iter(ax.patches)
        for outcome in ORDER:
            for run in run_order:
                patch = next(patch_iter)
                n = int(data.loc[(data["run"] == run) & (data["success"] == outcome), "count"].iloc[0])
                t = totals[run]
                ax.annotate(
                    f"{n}\n({n / t:.1%})",
                    (patch.get_x() + patch.get_width() / 2, patch.get_height()),
                    ha="center", va="bottom", fontsize=18,
                )
        ax.set_ylim(0, data["count"].max() * 1.22)
        ax.legend(title="Outcome", frameon=False, loc="upper right")
        ax.set_xlabel("Commit date")

    if len(db_paths) == 1:
        ax.set_xlabel("Outcome")
    ax.set_ylabel("Number of tests")
    sns.despine()

    fig.tight_layout()
    fig.savefig(out_path.with_suffix(".png"), bbox_inches="tight", dpi=300)
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    print(f"Wrote {out_path.with_suffix('.png')} and {out_path.with_suffix('.pdf')}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        dbs = [Path(a) for a in sys.argv[1:]]
    else:
        dbs = [sorted(Path(".").glob("prusti-*.db"))[-1]]
    main(dbs, Path("overview_outcomes.png"))
    #main(dbs, Path("overview_outcomes.pdf"))
