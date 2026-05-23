"""E11-lite: process-mining quality of released variants under each privacy regime.

For every E8 (DP) and E9 (k-anonymity) output:
  1. decode released variant multiset using `activity_map` (id -> name);
  2. reconstruct an event log by repeating each variant `count` times;
  3. mine a Petri net via Inductive Miner;
  4. compare against the centralised non-private model with token-replay fitness
     and ETC precision.
Also report the EMD between the original variant-frequency distribution and the
released one (Wasserstein-1).

Output: per-experiment CSV in eval_results/pm_quality/.

This is *evaluation hygiene*, not a novel finding — the MPC pipeline returns by
construction the same multiset as the centralised mechanism, so PM quality
agrees with Rafiei et al. (TraVaS). We report the numbers anyway because the
supervisor's audience expects them on the chosen logs.

Limit the run with --datasets / --experiment to make iteration fast.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from scipy.stats import wasserstein_distance

import pm4py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_ROOT = os.path.join(ROOT, "eval_results")
OUT_DIR = os.path.join(RESULTS_ROOT, "pm_quality")
sys.path.insert(0, ROOT)


DATASET_LOG_PATH = {
    "bpi13_incidents":   "data/Master_Input/OrgA/BPI_Challenge_2013_incidents.xes.gz",
    "sepsis":            "data/Master_Input/OrgA/Sepsis_Cases_OrgA.xes.gz",
    "requestforpayment": "data/Master_Input/OrgA/RequestForPayment_OrgA.xes.gz",
    "bpi12":             "data/Master_Input/OrgA/BPI_Challenge_2012.xes.gz",
    "international_decl":"data/Master_Input/OrgA/InternationalDeclarations_OrgA.xes.gz",
    "domestic_decl":     "data/Master_Input/OrgA/DomesticDeclarations_OrgA.xes.gz",
    "hospital":          "data/Master_Input/OrgA/Hospital_log.xes.gz",
    "permit":            "data/Master_Input/OrgA/PermitLog_OrgA.xes.gz",
    "bpi17_offer":       "data/Master_Input/OrgA/BPIChallenge2017-Offerlog.xes",
}


def decode_variants(rec):
    """Return list of (activity_name_tuple, count) from a result JSON."""
    amap = {int(k): v for k, v in rec["activity_map"].items()}
    out = []
    for v in rec.get("variants", []):
        acts = tuple(amap[e[0]] for e in v["raw"]
                     if not (e[0] == 0 and e[1] == 0) and e[0] in amap)
        if acts:
            out.append((acts, v["count"]))
    return out


def variants_to_log(variants):
    """Reconstruct a pm4py EventLog DataFrame from (variant, count) pairs."""
    base_ts = datetime(2020, 1, 1)
    rows = []
    case_id = 0
    for acts, count in variants:
        for _ in range(count):
            for j, a in enumerate(acts):
                rows.append({
                    "case:concept:name": f"case_{case_id}",
                    "concept:name": a,
                    "time:timestamp": base_ts + timedelta(seconds=j),
                })
            case_id += 1
    df = pd.DataFrame(rows)
    return pm4py.format_dataframe(df, case_id="case:concept:name",
                                  activity_key="concept:name",
                                  timestamp_key="time:timestamp")


def variant_freq(variants):
    """Return {variant_tuple: count} dict."""
    return {acts: cnt for acts, cnt in variants}


def emd_variants(reference_dist, released_dist):
    """Wasserstein-1 over variant frequency vectors (variants flattened to index).

    Uses the union of variants as the support and normalised frequencies.
    """
    keys = sorted(set(reference_dist) | set(released_dist),
                  key=lambda k: (-reference_dist.get(k, 0), -released_dist.get(k, 0)))
    idx = list(range(len(keys)))
    sum_ref = sum(reference_dist.values()) or 1
    sum_rel = sum(released_dist.values()) or 1
    p = [reference_dist.get(k, 0) / sum_ref for k in keys]
    q = [released_dist.get(k, 0) / sum_rel for k in keys]
    return float(wasserstein_distance(idx, idx, u_weights=p, v_weights=q))


def mine_and_score(released_log, original_log):
    """Run Inductive Miner on released_log, evaluate against original_log."""
    if released_log is None or len(released_log) == 0:
        return {"fitness": None, "precision": None}
    net, im, fm = pm4py.discover_petri_net_inductive(released_log)
    try:
        fit_res = pm4py.fitness_token_based_replay(original_log, net, im, fm)
        fitness = fit_res.get("average_trace_fitness")
    except Exception:
        fitness = None
    try:
        prec = pm4py.precision_token_based_replay(original_log, net, im, fm)
    except Exception:
        prec = None
    return {"fitness": fitness, "precision": prec}


def process_experiment(exp, datasets_filter):
    """Walk eval_results/<exp>/*.json, score every release, write CSV."""
    exp_dir = os.path.join(RESULTS_ROOT, exp)
    if not os.path.isdir(exp_dir):
        print(f"skip {exp} (no dir)")
        return

    # Cache original logs + variant distributions per dataset.
    original_log_cache = {}
    original_dist_cache = {}

    rows = []
    files = sorted(f for f in os.listdir(exp_dir) if f.endswith(".json"))
    print(f"=== {exp}: {len(files)} files ===")
    for i, fn in enumerate(files):
        rec = json.load(open(os.path.join(exp_dir, fn)))
        meta = rec["meta"]
        dset = meta["dataset"]
        if datasets_filter and dset not in datasets_filter:
            continue

        if dset not in original_log_cache:
            path = os.path.join(ROOT, DATASET_LOG_PATH[dset])
            print(f"  loading reference log: {path}")
            log = pm4py.read_xes(path) if path.endswith(".xes") else pm4py.read_xes(path)
            original_log_cache[dset] = log
            variants_dict = pm4py.get_variants(log)
            # pm4py 2.7 returns {tuple-of-activity-names: int-count}; 2.5- returned lists.
            original_dist_cache[dset] = {
                (k if isinstance(k, tuple) else tuple(k.split(","))):
                (len(v) if isinstance(v, list) else int(v))
                for k, v in variants_dict.items()
            }
        orig_log = original_log_cache[dset]
        orig_dist = original_dist_cache[dset]

        released = decode_variants(rec)
        released_dist = variant_freq(released)

        emd = emd_variants(orig_dist, released_dist) if released_dist else None
        released_log = variants_to_log(released) if released else None
        scores = mine_and_score(released_log, orig_log)

        rows.append({
            "experiment": exp,
            "run_id": rec["run_id"],
            "dataset": dset,
            **{k: v for k, v in meta.items() if k not in ("experiment", "dataset")},
            "variants_released": len(released),
            "fitness": scores["fitness"],
            "precision": scores["precision"],
            "emd": emd,
        })
        print(f"  [{i+1}/{len(files)}] {rec['run_id']}: "
              f"fit={scores['fitness']:.3f} prec={scores['precision'] or 0:.3f} emd={emd:.4f}"
              if scores["fitness"] is not None and emd is not None
              else f"  [{i+1}/{len(files)}] {rec['run_id']}: <empty release>")

    if not rows:
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    cols = sorted({k for r in rows for k in r})
    path = os.path.join(OUT_DIR, f"{exp}_pm_quality.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="all",
                    help="One of {e1_correctness, e8_dp, e9_kanonymity, all}")
    ap.add_argument("--datasets", default=None,
                    help="Comma-separated list to filter datasets (default: all available).")
    args = ap.parse_args()

    datasets_filter = set(args.datasets.split(",")) if args.datasets else None
    experiments = (["e1_correctness", "e8_dp", "e9_kanonymity"]
                   if args.experiment == "all" else [args.experiment])
    for exp in experiments:
        process_experiment(exp, datasets_filter)


if __name__ == "__main__":
    main()
