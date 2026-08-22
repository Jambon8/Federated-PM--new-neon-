"""Differential Privacy evaluation plots."""

import os
import sys
import argparse
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from eval.figures.style import apply_style, get_colors
from eval.utils import load_results, ensure_output_dir
import matplotlib.pyplot as plt

apply_style()


def plot_dp_overhead(results_file, output_dir=None):
    """Grouped bar chart: compile time + runtime with DP off vs on at each epsilon."""
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    perf = data.get("performance", data)
    entries = perf.get("results", [])
    if not entries:
        print("No performance data available")
        return

    labels = []
    compile_times = []
    runtimes = []

    for e in entries:
        if not e.get("enable_dp"):
            labels.append("No DP")
        else:
            labels.append(f"ε={e['epsilon']}")
        compile_times.append(e.get("compile_time_s", 0))
        runtimes.append(e.get("total_runtime_s", 0))

    x = np.arange(len(labels))
    width = 0.35
    colors = get_colors(3)

    fig, ax = plt.subplots()
    ax.bar(x - width / 2, compile_times, width, label="Compile time", color=colors[0])
    ax.bar(x + width / 2, runtimes, width, label="MPC runtime", color=colors[1])
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Time (s)")
    ax.set_title("DP Overhead: Compile Time and Runtime")
    ax.legend()
    fig.savefig(os.path.join(output_dir, "dp_overhead.pdf"))
    plt.close(fig)
    print("Saved dp_overhead.pdf")


def plot_dp_communication(results_file, output_dir=None):
    """Bar chart: communication (MB) with DP off vs on at each epsilon."""
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    perf = data.get("performance", data)
    entries = perf.get("results", [])
    if not entries:
        print("No performance data available")
        return

    labels = []
    data_sent = []

    for e in entries:
        if not e.get("enable_dp"):
            labels.append("No DP")
        else:
            labels.append(f"ε={e['epsilon']}")
        data_sent.append(e.get("global_data_sent_mb", 0))

    colors = get_colors(1)
    fig, ax = plt.subplots()
    ax.bar(range(len(labels)), data_sent, color=colors[0])
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Data Sent (MB)")
    ax.set_title("DP Communication Overhead")
    fig.savefig(os.path.join(output_dir, "dp_communication.pdf"))
    plt.close(fig)
    print("Saved dp_communication.pdf")


def plot_dp_noise_distribution(results_file, output_dir=None):
    """Histogram of observed noise overlaid with theoretical discrete Laplace PMF."""
    data = load_results(results_file)
    if output_dir is None:
        output_dir = ensure_output_dir("plots")

    stat = data.get("statistical", data)
    noise_values = stat.get("noise_values", [])
    epsilon = stat.get("epsilon", 1.0)

    if not noise_values:
        print("No noise data available")
        return

    noise_arr = np.array(noise_values)
    p = math.exp(-epsilon)

    # Theoretical PMF
    k_range = np.arange(int(noise_arr.min()) - 2, int(noise_arr.max()) + 3)
    pmf = [(1 - p) / (1 + p) * p ** abs(k) for k in k_range]

    colors = get_colors(2)
    fig, ax = plt.subplots()

    # Histogram (normalized to PMF)
    bins = np.arange(noise_arr.min() - 0.5, noise_arr.max() + 1.5, 1)
    ax.hist(noise_arr, bins=bins, density=True, alpha=0.7, color=colors[0],
            label="Observed", edgecolor="white")

    # Theoretical PMF
    ax.plot(k_range, pmf, "o-", color=colors[1], markersize=4, label=f"DLap(ε={epsilon})")

    ax.set_xlabel("Noise Value")
    ax.set_ylabel("Probability")
    ax.set_title(f"DP Noise Distribution (ε={epsilon})")
    ax.legend()
    fig.savefig(os.path.join(output_dir, "dp_noise_distribution.pdf"))
    plt.close(fig)
    print("Saved dp_noise_distribution.pdf")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results_file")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    data = load_results(args.results_file)

    if "performance" in data:
        plot_dp_overhead(args.results_file, args.output_dir)
        plot_dp_communication(args.results_file, args.output_dir)
    if "statistical" in data:
        plot_dp_noise_distribution(args.results_file, args.output_dir)
