"""Plot PM-quality results from e11_pm_quality.py.

Reads eval_results/pm_quality/{e8_dp,e9_kanonymity}_pm_quality.csv and emits:
  - fig_e9_kanon_quality.pdf : 3-panel (fitness, precision, EMD) vs k, per dataset
  - fig_e8_dp_quality.pdf    : 3-panel (fitness, precision, EMD) vs epsilon, per dataset
"""

import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import apply_style, COLORS

ROOT     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PM_DIR   = os.path.join(ROOT, "eval_results", "pm_quality")
OUT_DIR  = PM_DIR


def load(path, key_field):
    """Return {dataset: [(key, fitness, precision, emd), ...]} sorted by numeric key."""
    out = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                k = float(r[key_field]) if r[key_field] not in ("", "None") else None
            except ValueError:
                k = r[key_field]
            fit  = float(r["fitness"])   if r.get("fitness", "") not in ("", "None") else None
            prec = float(r["precision"]) if r.get("precision","") not in ("", "None") else None
            emd  = float(r["emd"])       if r.get("emd",       "") not in ("", "None") else None
            out[r["dataset"]].append((k, fit, prec, emd))
    for ds in out:
        out[ds].sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0))
    return out


def plot_panel(ax, data, idx_col, ylabel, xlabel, xscale_log=False):
    for i, (ds, rows) in enumerate(sorted(data.items())):
        xs = [r[0] for r in rows if r[idx_col] is not None and r[0] is not None]
        ys = [r[idx_col] for r in rows if r[idx_col] is not None and r[0] is not None]
        ax.plot(xs, ys, marker="o", label=ds, color=COLORS[i % len(COLORS)])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if xscale_log:
        ax.set_xscale("log")


def plot_e9():
    path = os.path.join(PM_DIR, "e9_kanonymity_pm_quality.csv")
    if not os.path.isfile(path):
        print(f"skip e9 (no {path})")
        return
    data = load(path, "k")
    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    plot_panel(axes[0], data, 1, "Token-replay fitness", "k")
    plot_panel(axes[1], data, 2, "ETC precision",        "k")
    plot_panel(axes[2], data, 3, "EMD (Wasserstein-1)",  "k")
    axes[0].legend(frameon=False, fontsize=8, loc="lower left")
    plt.tight_layout()
    out = os.path.join(OUT_DIR, "fig_e9_kanon_quality.pdf")
    plt.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_e8():
    path = os.path.join(PM_DIR, "e8_dp_pm_quality.csv")
    if not os.path.isfile(path):
        print(f"skip e8 (no {path})")
        return
    data = load(path, "epsilon")
    # Drop the no-DP baseline rows (epsilon is None) — they belong on the k-anon plot.
    data = {ds: [(k, *rest) for k, *rest in rows if k is not None] for ds, rows in data.items()}
    data = {ds: rows for ds, rows in data.items() if rows}
    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    plot_panel(axes[0], data, 1, "Token-replay fitness", r"$\varepsilon$", xscale_log=True)
    plot_panel(axes[1], data, 2, "ETC precision",        r"$\varepsilon$", xscale_log=True)
    plot_panel(axes[2], data, 3, "EMD (Wasserstein-1)",  r"$\varepsilon$", xscale_log=True)
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")
    plt.tight_layout()
    out = os.path.join(OUT_DIR, "fig_e8_dp_quality.pdf")
    plt.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    plot_e9()
    plot_e8()
