"""Create the Chapter 6 dataset-size figure from the pinned statistics CSV.

Usage:
    python3 eval/plotting/plot_dataset_sizes.py --out Thesis/thesis-main/figures
"""

import argparse
import csv
import math
import os
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import apply_style, COLORS


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATS = os.path.join(ROOT, "eval_results", "dataset_stats.csv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="Thesis/thesis-main/figures")
    parser.add_argument("--stats", default=STATS)
    args = parser.parse_args()

    with open(args.stats, newline="") as handle:
        rows = list(csv.DictReader(handle))

    apply_style()
    fig, ax = plt.subplots()
    label_offsets = {
        "domestic_decl": (5, -11),
        "international_decl": (5, 5),
        "requestforpayment": (5, -11),
        "permit": (-5, 7),
    }
    labels = {
        "bpi13_open": "BPI 13 Open Problems",
        "bpi13_closed": "BPI 13 Closed Problems",
        "bpi13_incidents": "BPI 13 Incidents",
        "bpi17_offer": "BPI 17 Offer Log",
        "bpi12": "BPI 12",
        "sepsis": "Sepsis Cases",
        "hospital": "Hospital Log",
        "requestforpayment": "Request for Payment",
        "domestic_decl": "Domestic Declarations",
        "international_decl": "International Declarations",
        "permit": "Travel Permit Data",
    }
    for row in rows:
        cases = float(row["N_per_party"])
        trace_length = float(row["partial_len"])
        events = float(row["events"])
        marker_size = 18 + 10 * math.sqrt(events / 1000)
        ax.scatter(cases, trace_length, s=marker_size,
                   color=COLORS[0], edgecolors="none")
        offset = label_offsets.get(row["dataset"], (4, 4))
        alignment = "right" if row["dataset"] == "permit" else "left"
        ax.annotate(labels[row["dataset"]], (cases, trace_length),
                    xytext=offset, textcoords="offset points", fontsize=9,
                    horizontalalignment=alignment)

    for events, label in ((2_500, "2.5k"), (25_000, "25k"), (250_000, "250k")):
        marker_size = 18 + 10 * math.sqrt(events / 1000)
        ax.scatter([], [], s=marker_size, color=COLORS[0],
                   edgecolors="none", label=label)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(r"Trace-count cap, $N$")
    ax.set_ylabel(r"Trace-length cap, $\ell$")
    ax.grid(False)
    ax.legend(title="Events", loc="upper right", frameon=False,
              fontsize=9, title_fontsize=9, labelspacing=0.8)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "dataset_sizes.pdf")
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    main()
