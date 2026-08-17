"""Scaling plots: E3 (input dimensions) and E4 (party count).

Reads aggregated CSVs from eval_results and emits thesis-ready scaling plots.
"""

import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import apply_style, COLORS

ROOT    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL    = os.path.join(ROOT, "eval_results")
OUT_DIR = os.path.join(EVAL, "scaling_plots")

DISPLAY_NAMES = {
    "bpi13_open": "BPI 13 Open",
    "bpi13_closed": "BPI 13 Closed",
    "bpi13_incidents": "BPI 13 Incidents",
    "sepsis": "Sepsis",
}


def load_scaling(path, x_key):
    """Return {dataset: sorted [(x, mean_wall_s)]}, averaged over reps."""
    rows = list(csv.DictReader(open(path)))
    bucket = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("rc") not in ("0", 0, ""):  # only successful runs
            try:
                if int(r["rc"]) != 0:
                    continue
            except ValueError:
                pass
        try:
            x = float(r[x_key])
            y = float(r["wall_s"])
        except (ValueError, KeyError, TypeError):
            continue
        bucket[r["dataset"]][x].append(y)
    out = {}
    for ds, xs in bucket.items():
        out[ds] = sorted(((x, sum(ys) / len(ys)) for x, ys in xs.items()),
                         key=lambda t: t[0])
    return out


def plot(data, xlabel, ylabel, title, fname, xlog=True, ylog=True):
    apply_style()
    fig, ax = plt.subplots()
    for i, (ds, pts) in enumerate(sorted(data.items())):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, marker="o", label=DISPLAY_NAMES.get(ds, ds),
                color=COLORS[i % len(COLORS)])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if xlog:
        ax.set_xscale("log")
    if ylog:
        ax.set_yscale("log")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    os.makedirs(OUT_DIR, exist_ok=True)
    plt.savefig(os.path.join(OUT_DIR, fname))
    plt.close(fig)
    print(f"wrote {os.path.join(OUT_DIR, fname)}")


def load_e3_grid(path):
    """Return successful E3-grid observations grouped by dataset, N, and P."""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["rc"]) != 0:
                continue
            key = (int(row["n_per_party_cap"]), int(row["partial_len"]))
            out[row["dataset"]][key]["runtime"].append(float(row["runtime_s"]))
            out[row["dataset"]][key]["communication"].append(float(row["party0_data_mb"]))
            out[row["dataset"]][key]["rounds"].append(float(row["total_rounds"]))
    return out


def plot_e3_grid(data, fname):
    """Plot the controlled input grid for the representative dataset."""
    apply_style()
    plt.rcParams.update({"font.size": 11.5, "axes.titlesize": 11.5,
                         "axes.labelsize": 11, "legend.fontsize": 10.5})
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.65), sharex=True)
    ns = sorted({n for cells in data.values() for n, _ in cells})
    ps = sorted({p for cells in data.values() for _, p in cells})
    dataset = "bpi13_incidents"

    for i, p in enumerate(ps):
        runtime_means = np.asarray([
            np.mean(data[dataset][(n, p)]["runtime"])
            for n in ns
        ])
        runtime_stds = np.asarray([
            np.std(data[dataset][(n, p)]["runtime"], ddof=1)
            for n in ns
        ])
        axes[0].errorbar(
            ns, runtime_means, yerr=runtime_stds, marker="o", markersize=3.2,
            capsize=2.5, elinewidth=0.9, linewidth=1.1, color=COLORS[i],
            label=fr"$\ell = {p}$")

        comm = [np.mean(data[dataset][(n, p)]["communication"]) for n in ns]
        axes[1].plot(ns, comm, marker="o", markersize=3.2, linewidth=1.1,
                     color=COLORS[i])
        rounds = [np.mean(data[dataset][(n, p)]["rounds"]) / 1e6
                  for n in ns]
        axes[2].plot(ns, rounds, marker="o", markersize=3.2, linewidth=1.1,
                     color=COLORS[i])

    titles = ("(a)", "(b)", "(c)")
    ylabels = ("Runtime (s)", "Sent payload (MB)", "Rounds (millions)")
    for ax, title, ylabel in zip(axes, titles, ylabels):
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_ylabel(ylabel)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(ns, [str(n) for n in ns])
        ax.set_xlabel("Trace-count cap, $N$")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=4, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 1.005))
    fig.tight_layout(rect=(0, 0, 1, 0.90), w_pad=1.05)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, fname)
    fig.savefig(path)
    plt.close(fig)
    print(f"wrote {path}")


def load_e4b(path):
    """Return successful party-count observations, including stored controls."""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["rc"]) != 0:
                continue
            control = row["control"].lower() == "true"
            matched_n = int(row["matched_n"] or row["n_parties"])
            cell = out[row["dataset"]][control][matched_n]
            cell["runtime"].append(float(row["runtime_s"]))
            cell["communication"].append(float(row["party0_data_mb"]))
            cell["rounds"].append(float(row["total_rounds"]))
    return out


def plot_e4b(data, fname):
    """Plot direct multiparty scaling for the representative dataset."""
    apply_style()
    plt.rcParams.update({"font.size": 11.5, "axes.titlesize": 11.5,
                         "axes.labelsize": 11})
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.5), sharex=True)
    ns = (2, 3, 4, 5)
    dataset = "bpi13_incidents"
    color = COLORS[0]

    runtime = [np.mean(data[dataset][False][n]["runtime"]) for n in ns]
    runtime_sd = [np.std(data[dataset][False][n]["runtime"], ddof=1) for n in ns]
    axes[0].errorbar(ns, runtime, yerr=runtime_sd, marker="o", markersize=4,
                     capsize=2.5, elinewidth=0.9, color=color, linewidth=1.2)

    communication = [np.mean(data[dataset][False][n]["communication"]) for n in ns]
    axes[1].plot(ns, communication, marker="o", markersize=4,
                 color=color, linewidth=1.2)

    rounds = [np.mean(data[dataset][False][n]["rounds"]) / 1e6 for n in ns]
    axes[2].plot(ns, rounds, marker="o", markersize=4,
                 color=color, linewidth=1.2)

    titles = ("(a)", "(b)", "(c)")
    ylabels = ("Runtime (s)", "Sent payload (MB)", "Rounds (millions)")
    for ax, title, ylabel in zip(axes, titles, ylabels):
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_ylabel(ylabel)
        ax.set_yscale("log")
        ax.set_xticks(ns)
        ax.set_xlabel("Number of parties, $n$")

    fig.tight_layout(w_pad=1.05)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, fname)
    fig.savefig(path)
    plt.close(fig)
    print(f"wrote {path}")


def main():
    e3_grid = load_e3_grid(os.path.join(EVAL, "e3_grid.csv"))
    plot_e3_grid(e3_grid, "fig_e3_controlled_grid.pdf")
    e4b = load_e4b(os.path.join(EVAL, "e4b_scaling_n.csv"))
    plot_e4b(e4b, "fig_e4b_party_scaling.pdf")
    e3 = load_scaling(os.path.join(EVAL, "e3_scaling_input.csv"), "n_per_party_cap")
    plot(e3, "Cases per party",  "Wall time (s)", "E3 input scaling",
         "fig_e3_input_scaling.pdf")
    e4 = load_scaling(os.path.join(EVAL, "e4_scaling_n.csv"), "n_parties")
    plot(e4, "Number of parties $N$", "Wall time (s)", "E4 party scaling",
         "fig_e4_n_scaling.pdf", xlog=False)


if __name__ == "__main__":
    main()
