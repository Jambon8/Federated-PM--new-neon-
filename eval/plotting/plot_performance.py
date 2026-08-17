"""Thesis scaling figures: E3 (runtime vs input size) and E4 (runtime vs party count).

Reads the aggregated CSVs produced by `thesis_experiments.py --aggregate`
(current-code runs only) and writes log-log PDFs for the evaluation chapter.

    python3 eval/plotting/plot_performance.py --out Thesis/thesis-main/figures
"""

import os
import sys
import csv
import argparse
import statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from eval.plotting.style import apply_style, get_colors  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

apply_style()
RESULTS = os.path.join(os.path.dirname(__file__), "..", "..", "eval_results")


def _load(name):
    with open(os.path.join(RESULTS, name)) as f:
        return list(csv.DictReader(f))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _n_from_runid(run_id):
    # e3__<ds>__n<N>__rep<r>  or  e4__<ds>__N<n>__rep<r>
    tok = run_id.split("__")[2]
    return int(tok.lstrip("nN"))


def _curve(rows, x_of):
    """Return sorted (x, mean_runtime, standard_deviation) points."""
    g = defaultdict(list)
    for r in rows:
        rt = _f(r["runtime_s"])
        if rt is not None:
            g[x_of(r)].append(rt)
    xs = sorted(g)
    return xs, [(st.mean(g[x]), st.stdev(g[x]) if len(g[x]) > 1 else 0.0)
                for x in xs]


def plot_input_scaling(out_dir):
    rows = _load("e3_scaling_input.csv")
    datasets = ["bpi13_incidents", "bpi17_offer", "sepsis"]
    colors = get_colors(len(datasets))
    fig, ax = plt.subplots()
    for ds, c in zip(datasets, colors):
        xs, points = _curve([r for r in rows if r["dataset"] == ds],
                            lambda r: _n_from_runid(r["run_id"]))
        means = [p[0] for p in points]
        deviations = [p[1] for p in points]
        ax.errorbar(xs, means, yerr=deviations, marker="o", capsize=2,
                    color=c, label=ds.replace("_", r"\_")
                    if plt.rcParams["text.usetex"] else ds)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(False)
    ax.set_xlabel(r"Cases per party $N_{\mathrm{per\,party}}$")
    ax.set_ylabel("Runtime (s)")
    ax.legend(frameon=False, loc="upper left")
    path = os.path.join(out_dir, "e3_input_scaling.pdf")
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


def plot_n_scaling(out_dir):
    rows = _load("e4_scaling_n.csv")
    datasets = ["bpi13_open", "bpi13_closed", "bpi13_incidents", "sepsis"]
    colors = get_colors(len(datasets))
    fig, ax = plt.subplots()
    for ds, c in zip(datasets, colors):
        xs, points = _curve([r for r in rows if r["dataset"] == ds],
                            lambda r: int(r["n_parties"]))
        means = [p[0] for p in points]
        deviations = [p[1] for p in points]
        ax.errorbar(xs, means, yerr=deviations, marker="o", capsize=2,
                    color=c, label=ds)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(False)
    ax.set_xticks([2, 3, 4, 5])
    ax.set_xticklabels(["2", "3", "4", "5"])
    ax.set_xlabel(r"Number of parties $n$")
    ax.set_ylabel("Runtime (s)")
    ax.legend(frameon=False, loc="upper left")
    path = os.path.join(out_dir, "e4_n_scaling.pdf")
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="Thesis/thesis-main/figures")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    plot_input_scaling(args.out)
    plot_n_scaling(args.out)
