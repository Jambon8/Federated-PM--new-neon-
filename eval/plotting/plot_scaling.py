"""Scaling plots: E3 (input size) and E4 (party count).

Reads aggregated CSVs from eval_results/{e3_scaling_input,e4_scaling_n}.csv and
emits one log-log line plot per experiment.
"""

import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import apply_style, COLORS

ROOT    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL    = os.path.join(ROOT, "eval_results")
OUT_DIR = os.path.join(EVAL, "scaling_plots")


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
        ax.plot(xs, ys, marker="o", label=ds, color=COLORS[i % len(COLORS)])
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


def main():
    e3 = load_scaling(os.path.join(EVAL, "e3_scaling_input.csv"), "n_per_party_cap")
    plot(e3, "Cases per party",  "Wall time (s)", "E3 input scaling",
         "fig_e3_input_scaling.pdf")
    e4 = load_scaling(os.path.join(EVAL, "e4_scaling_n.csv"), "n_parties")
    plot(e4, "Number of parties $N$", "Wall time (s)", "E4 party scaling",
         "fig_e4_n_scaling.pdf", xlog=False)


if __name__ == "__main__":
    main()
