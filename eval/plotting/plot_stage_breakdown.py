"""Stage-wise runtime / communication / round breakdown of the MPC pipeline.

Reads metrics.timers[2..10] from existing E2 JSONs (no cluster reruns) and
emits one stacked-bar figure per metric plus a CSV summary.

Stage map (auto-assigned by ProgramHandler from #neon_timer comments):
    2  PSI bitonic merge                NEON_TIMER_PSI
    3  Reconstruction                   NEON_TIMER_RECON
    4  Hashing                          NEON_TIMER_HASH
    5  Grouping (outer, = 6+7+8)        NEON_TIMER_GROUP        skipped
    6  Grouping setup                   NEON_TIMER_GROUP_SETUP
    7  Grouping sort                    NEON_TIMER_GROUP_SORT
    8  Grouping count                   NEON_TIMER_GROUP_COUNT
    9  DP noise (only with --enable-dp) NEON_TIMER_DP_NOISE
   10  Output reveal                    NEON_TIMER_OUTPUT

Six-stage logical view (matches Chapter 4): PSI / Reconstruction / Hashing /
Grouping (6+7+8) / Output. Stage 1 (input encoding) is Python-side and not
captured by MP-SPDZ timers.
"""

import csv
import json
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import apply_style, COLORS

# Disjoint subset for stacked-bar (skip 5; it is the OUTER group timer).
TIMER_TO_STAGE = {
    2:  "PSI",
    3:  "Reconstruction",
    4:  "Hashing",
    6:  "Group setup",
    7:  "Group sort",
    8:  "Group count",
    10: "Output reveal",
}

# Six-stage logical view (collapses 6+7+8 into one Grouping bar).
LOGICAL_STAGES = ["PSI", "Reconstruction", "Hashing", "Grouping", "Output reveal"]
LOGICAL_MAP = {
    2: "PSI",
    3: "Reconstruction",
    4: "Hashing",
    6: "Grouping",
    7: "Grouping",
    8: "Grouping",
    10: "Output reveal",
}

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_DIR = os.path.join(ROOT, "eval_results", "e2_performance")
OUT_DIR     = os.path.join(ROOT, "eval_results", "stage_breakdown")


def aggregate(view="logical"):
    """Return {dataset: {stage: {time_s, data_mb, rounds}}} averaged over reps.

    Per-rep totals first (so multi-timer stages like Grouping = 6+7+8 fold
    correctly), then mean across reps.
    """
    mapping = LOGICAL_MAP if view == "logical" else dict(TIMER_TO_STAGE)
    # per_rep[dset][rep_key][stage][metric] = float (summed across folded timers)
    per_rep = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))
    for fn in sorted(os.listdir(RESULTS_DIR)):
        if not fn.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(RESULTS_DIR, fn)))
        dset = rec["meta"]["dataset"]
        rep_key = rec.get("run_id", fn)
        for tid_str, t in rec["metrics"].get("timers", {}).items():
            tid = int(tid_str)
            stage = mapping.get(tid)
            if stage is None:
                continue
            per_rep[dset][rep_key][stage]["time_s"]  += t["time_s"]
            per_rep[dset][rep_key][stage]["data_mb"] += t["data_mb"]
            per_rep[dset][rep_key][stage]["rounds"]  += t["rounds"]

    out = {}
    for dset, reps in per_rep.items():
        out[dset] = {}
        stage_acc = defaultdict(lambda: defaultdict(list))
        for rep_key, stages in reps.items():
            for stage, metrics in stages.items():
                for k, v in metrics.items():
                    stage_acc[stage][k].append(v)
        for stage, metrics in stage_acc.items():
            out[dset][stage] = {k: sum(xs) / len(xs) for k, xs in metrics.items()}
    return out


def write_csv(agg, path):
    stages = LOGICAL_STAGES
    fields = ["dataset", "stage", "time_s", "data_mb", "rounds"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for dset, stages_data in agg.items():
            for stage in stages:
                m = stages_data.get(stage, {"time_s": 0, "data_mb": 0, "rounds": 0})
                w.writerow({"dataset": dset, "stage": stage, **m})


def plot_stacked(agg, metric, ylabel, fname, ylog=False):
    apply_style()
    datasets = sorted(agg.keys())
    stages = LOGICAL_STAGES
    fig, ax = plt.subplots()
    x = list(range(len(datasets)))
    bottoms = [0.0] * len(datasets)
    for i, stage in enumerate(stages):
        values = [agg[d].get(stage, {}).get(metric, 0) for d in datasets]
        ax.bar(x, values, bottom=bottoms, label=stage, color=COLORS[i % len(COLORS)])
        bottoms = [b + v for b, v in zip(bottoms, values)]
    ax.set_xticks(x)
    ax.set_xticklabels(datasets, rotation=15, ha="right")
    ax.set_ylabel(ylabel)
    if ylog:
        ax.set_yscale("log")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    plt.savefig(os.path.join(OUT_DIR, fname))
    plt.close(fig)


def report_table(agg):
    """Print a human-readable per-stage breakdown table."""
    stages = LOGICAL_STAGES
    print(f"\n{'dataset':<22} | " + " | ".join(f"{s:>14}" for s in stages))
    for metric, unit in [("time_s", "s"), ("data_mb", "MB"), ("rounds", "rds")]:
        print(f"\n--- {metric} ({unit}) ---")
        for dset in sorted(agg.keys()):
            row = [agg[dset].get(s, {}).get(metric, 0) for s in stages]
            total = sum(row)
            fracs = [f"{v:>8.1f} ({100*v/total if total else 0:>4.1f}%)" for v in row]
            print(f"{dset:<22} | " + " | ".join(fracs))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    agg = aggregate(view="logical")
    write_csv(agg, os.path.join(OUT_DIR, "stage_breakdown.csv"))
    plot_stacked(agg, "time_s", "Runtime (s)",
                 "stage_breakdown_runtime.pdf")
    plot_stacked(agg, "data_mb", "Communication (MB, party 0)",
                 "stage_breakdown_comm.pdf", ylog=True)
    plot_stacked(agg, "rounds", "Rounds",
                 "stage_breakdown_rounds.pdf", ylog=True)
    report_table(agg)
    print(f"\nWrote stage_breakdown.csv + 3 PDFs to {OUT_DIR}")


if __name__ == "__main__":
    main()
