"""Plot the stage-wise composition of the E2 baseline cost metrics."""

from __future__ import annotations

import json
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.utils import load_run, run_files  # noqa: E402

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval_results" / "performance_default"
OUTPUT = ROOT / "Thesis" / "thesis-main" / "figures" / "stage_profile.pdf"

STAGES = [
    (2, "Log merging"),
    (3, "Reconstruction"),
    (4, "Hashing"),
    (5, "Grouping"),
    (10, "Output reveal"),
]
METRICS = [
    ("time_s", "Runtime"),
    ("data_mb", "Communication"),
    ("rounds", "Aggregate communication rounds"),
]
DATASETS = [
    ("bpi13_open", "BPI 13 Open"),
    ("bpi13_closed", "BPI 13 Closed"),
    ("sepsis", "Sepsis"),
    ("requestforpayment", "Request for Payment"),
    ("international_decl", "International Declarations"),
    ("bpi13_incidents", "BPI 13 Incidents"),
    ("domestic_decl", "Domestic Declarations"),
    ("permit", "Travel Permit"),
    ("bpi12", "BPI 12"),
    ("hospital", "Hospital"),
    ("bpi17_offer", "BPI 17 Offer"),
]
COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#8E6C8A"]


def load_shares(results: Path = RESULTS) -> np.ndarray:
    """Return five-run mean shares as [metric, dataset, stage]."""
    values = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    successful = 0
    for path in run_files(results):
        record = load_run(path)
        if record["metrics"].get("return_code") != 0:
            continue
        successful += 1
        dataset = record["meta"]["dataset"]
        timers = record["metrics"]["timers"]
        for timer_id, stage in STAGES:
            measurement = timers[str(timer_id)]
            for metric, _ in METRICS:
                values[dataset][stage][metric].append(float(measurement[metric]))

    assert successful == 55, f"Expected 55 successful E2 records, found {successful}"
    for dataset, _ in DATASETS:
        for _, stage in STAGES:
            for metric, _ in METRICS:
                count = len(values[dataset][stage][metric])
                assert count == 5, f"{dataset}/{stage}/{metric}: expected 5, found {count}"

    means = np.array([
        [
            [np.mean(values[dataset][stage][metric]) for _, stage in STAGES]
            for dataset, _ in DATASETS
        ]
        for metric, _ in METRICS
    ])
    return 100 * means / means.sum(axis=2, keepdims=True)


def plot(shares: np.ndarray, output: Path = OUTPUT) -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 9.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.6,
        "pdf.fonttype": 42,
    })
    figure, axes = plt.subplots(3, 1, figsize=(4.9, 7.4), sharex=True)
    y = np.arange(len(DATASETS))

    for metric_index, (axis, (_, title)) in enumerate(zip(axes, METRICS)):
        matrix = shares[metric_index]
        left = np.zeros(len(DATASETS))
        for stage_index, (_, stage) in enumerate(STAGES):
            axis.barh(
                y,
                matrix[:, stage_index],
                left=left,
                height=0.72,
                color=COLORS[stage_index],
                label=stage,
                linewidth=0,
            )
            left += matrix[:, stage_index]

        for row in range(len(DATASETS)):
            for stage_index in (0, 1, 3, 4):
                share = matrix[row, stage_index]
                if share < 5:
                    continue
                center = matrix[row, :stage_index].sum() + share / 2
                axis.text(
                    center,
                    row,
                    f"{share:.0f}%",
                    ha="center",
                    va="center",
                    color="white" if stage_index in (0, 3, 4) else "black",
                    fontsize=7.0,
                    fontweight="bold",
                )

        axis.set_title(title, loc="left", fontsize=10.0, fontweight="bold", pad=3)
        axis.set_yticks(y, [label for _, label in DATASETS])
        axis.tick_params(axis="y", labelsize=9.0, length=2)
        axis.tick_params(axis="x", labelsize=9.0, length=2)
        axis.invert_yaxis()
        axis.set_xlim(0, 100)
        axis.set_xticks(np.arange(0, 101, 20))
        axis.grid(axis="x", color="#D8D8D8", linewidth=0.45)
        axis.set_axisbelow(True)

    axes[-1].set_xlabel("Share of the five-stage total (%)", fontsize=9.0)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        ncol=5,
        frameon=False,
        fontsize=9.0,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.005),
        columnspacing=0.75,
        handlelength=1.4,
    )
    figure.subplots_adjust(left=0.33, right=0.995, top=0.915, bottom=0.06, hspace=0.25)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, bbox_inches="tight", pad_inches=0.02)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()
    plot(load_shares(args.results), args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
