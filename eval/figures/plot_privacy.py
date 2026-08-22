"""Privacy-utility tradeoff plots."""

import os
import sys
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from eval.figures.style import apply_style, get_colors
from eval.utils import load_results, ensure_output_dir
import matplotlib.pyplot as plt

apply_style()


def plot_emd_vs_epsilon(results_file, output_dir=None):
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    # Group DP evaluations by threshold
    by_threshold = {}
    for ev in data["evaluations"]:
        if ev.get("epsilon") is None:
            continue
        t = ev["threshold"]
        by_threshold.setdefault(t, {"epsilons": [], "emd_means": [], "emd_stds": []})
        by_threshold[t]["epsilons"].append(ev["epsilon"])
        by_threshold[t]["emd_means"].append(ev.get("emd_mean", 0))
        by_threshold[t]["emd_stds"].append(ev.get("emd_std", 0))

    fig, ax = plt.subplots()
    colors = get_colors(len(by_threshold))

    for idx, (t, vals) in enumerate(sorted(by_threshold.items())):
        epsilons = vals["epsilons"]
        means = vals["emd_means"]
        stds = vals["emd_stds"]
        ax.errorbar(epsilons, means, yerr=stds, marker="o", capsize=3,
                     label=f"threshold={t}", color=colors[idx % len(colors)])

    ax.set_xlabel("Epsilon (ε)")
    ax.set_ylabel("EMD")
    ax.set_title("Privacy-Utility Tradeoff: EMD vs Epsilon")
    ax.legend()
    fig.savefig(os.path.join(output_dir, "emd_vs_epsilon.pdf"))
    plt.close(fig)
    print("Saved emd_vs_epsilon.pdf")


def plot_emd_vs_threshold(results_file, output_dir=None):
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    # Threshold-only evaluations (no DP)
    thresholds = []
    emds = []
    for ev in data["evaluations"]:
        if ev.get("epsilon") is not None:
            continue
        thresholds.append(ev["threshold"])
        emds.append(ev.get("emd", 0))

    fig, ax = plt.subplots()
    ax.plot(thresholds, emds, marker="s", color=get_colors(1)[0])
    ax.set_xlabel("Threshold")
    ax.set_ylabel("EMD")
    ax.set_title("Utility Loss: EMD vs Frequency Threshold")
    fig.savefig(os.path.join(output_dir, "emd_vs_threshold.pdf"))
    plt.close(fig)
    print("Saved emd_vs_threshold.pdf")


def plot_fitness_precision(results_file, output_dir=None):
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    # Threshold-only with fitness/precision
    configs = []
    fitness_vals = []
    precision_vals = []
    for ev in data["evaluations"]:
        if ev.get("epsilon") is not None:
            continue
        f = ev.get("fitness")
        p = ev.get("precision")
        if f is not None and p is not None:
            configs.append(f"t={ev['threshold']}")
            fitness_vals.append(f)
            precision_vals.append(p)

    if not configs:
        print("No fitness/precision data available")
        return

    x = np.arange(len(configs))
    width = 0.35

    fig, ax = plt.subplots()
    colors = get_colors(2)
    ax.bar(x - width / 2, fitness_vals, width, label="Fitness", color=colors[0])
    ax.bar(x + width / 2, precision_vals, width, label="Precision", color=colors[1])
    ax.set_xticks(x)
    ax.set_xticklabels(configs)
    ax.set_ylabel("Score")
    ax.set_title("Process Model Quality")
    ax.legend()
    ax.set_ylim(0, 1.05)
    fig.savefig(os.path.join(output_dir, "fitness_precision.pdf"))
    plt.close(fig)
    print("Saved fitness_precision.pdf")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results_file")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    plot_emd_vs_threshold(args.results_file, args.output_dir)
    plot_emd_vs_epsilon(args.results_file, args.output_dir)
    plot_fitness_precision(args.results_file, args.output_dir)
