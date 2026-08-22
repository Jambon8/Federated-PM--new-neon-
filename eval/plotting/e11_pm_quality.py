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
import signal
from contextlib import contextmanager
from datetime import datetime, timedelta


class TimeoutError_(Exception):
    pass


@contextmanager
def timeout(seconds):
    """Raise TimeoutError_ if the block runs longer than `seconds`.

    pm4py's Inductive Miner / token-replay can hang indefinitely on
    pathological inputs (e.g. Hospital_log.xes.gz with hundreds of activities
    + non-conformant tokens). SIGALRM gives us a hard stop."""
    def _handler(signum, frame):
        raise TimeoutError_(f"exceeded {seconds}s")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

import pandas as pd
from scipy.stats import wasserstein_distance

import pm4py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_ROOT = os.path.join(ROOT, "eval_results")
OUT_DIR = os.path.join(RESULTS_ROOT, "pm_quality")
sys.path.insert(0, ROOT)

from eval.utils import load_run, run_files  # noqa: E402


DATASET_LOG_PATH = {
    "bpi13_incidents":   "data/2parties/bpi13_incidents/party_0.xes.gz",
    "sepsis":            "data/2parties/sepsis/party_0.xes.gz",
    "requestforpayment": "data/2parties/requestforpayment/party_0.xes.gz",
    "bpi12":             "data/2parties/bpi12/party_0.xes.gz",
    "international_decl":"data/2parties/international_decl/party_0.xes.gz",
    "domestic_decl":     "data/2parties/domestic_decl/party_0.xes.gz",
    "hospital":          "data/2parties/hospital/party_0.xes.gz",
    "permit":            "data/2parties/permit/party_0.xes.gz",
    "bpi17_offer":       "data/2parties/bpi17_offer/party_0.xes",
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


PM_BUDGET_S = int(os.environ.get("NEON_PM_BUDGET", "300"))


def mine_and_score(released_log, original_log):
    """Run Inductive Miner on released_log, evaluate against original_log.

    Each pm4py stage is bounded by NEON_PM_BUDGET (default 300 s) to prevent
    runaway hangs on logs that defeat the miner (e.g. Hospital_log)."""
    if released_log is None or len(released_log) == 0:
        return {"fitness": None, "precision": None}
    with timeout(PM_BUDGET_S):
        net, im, fm = pm4py.discover_petri_net_inductive(released_log)
    fitness = None
    try:
        with timeout(PM_BUDGET_S):
            fit_res = pm4py.fitness_token_based_replay(original_log, net, im, fm)
        fitness = fit_res.get("average_trace_fitness")
    except Exception as ex:
        print(f"      fitness failed: {type(ex).__name__}: {ex}")
    prec = None
    try:
        with timeout(PM_BUDGET_S):
            prec = pm4py.precision_token_based_replay(original_log, net, im, fm)
    except Exception as ex:
        print(f"      precision failed: {type(ex).__name__}: {ex}")
    return {"fitness": fitness, "precision": prec}


def process_experiment(exp, datasets_filter):
    """Walk eval_results/<exp>/*.json, score every release, write CSV.

    Writes the CSV incrementally so a per-file crash (pm4py OOM on a big log,
    Inductive Miner stuck on a degenerate input) does not lose earlier work.
    Per-file exceptions are caught and logged with row['error'] set."""
    exp_dir = os.path.join(RESULTS_ROOT, exp)
    if not os.path.isdir(exp_dir):
        print(f"skip {exp} (no dir)")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, f"{exp}_pm_quality.csv")

    original_log_cache = {}
    original_dist_cache = {}

    rows = []
    files = run_files(exp_dir)
    print(f"=== {exp}: {len(files)} files ===")
    for i, path in enumerate(files):
        rec = load_run(path)
        meta = rec["meta"]
        dset = meta["dataset"]
        if datasets_filter and dset not in datasets_filter:
            continue

        row = {
            "experiment": exp,
            "run_id": rec["run_id"],
            "dataset": dset,
            **{k: v for k, v in meta.items() if k not in ("experiment", "dataset")},
            "variants_released": 0,
            "fitness": None,
            "precision": None,
            "emd": None,
            "error": None,
        }
        try:
            if dset not in original_log_cache:
                path = os.path.join(ROOT, DATASET_LOG_PATH[dset])
                print(f"  loading reference log: {path}")
                with timeout(PM_BUDGET_S):
                    log = pm4py.read_xes(path)
                original_log_cache[dset] = log
                with timeout(PM_BUDGET_S):
                    variants_dict = pm4py.get_variants(log)
                original_dist_cache[dset] = {
                    (k if isinstance(k, tuple) else tuple(k.split(","))):
                    (len(v) if isinstance(v, list) else int(v))
                    for k, v in variants_dict.items()
                }
            orig_log = original_log_cache[dset]
            orig_dist = original_dist_cache[dset]

            released = decode_variants(rec)
            released_dist = variant_freq(released)
            row["variants_released"] = len(released)

            if released_dist:
                row["emd"] = emd_variants(orig_dist, released_dist)
            released_log = variants_to_log(released) if released else None
            scores = mine_and_score(released_log, orig_log)
            row["fitness"]   = scores["fitness"]
            row["precision"] = scores["precision"]

            f_str = f"{row['fitness']:.3f}" if row["fitness"]   is not None else "n/a"
            p_str = f"{row['precision']:.3f}" if row["precision"] is not None else "n/a"
            e_str = f"{row['emd']:.4f}"     if row["emd"]       is not None else "n/a"
            print(f"  [{i+1}/{len(files)}] {rec['run_id']}: fit={f_str} prec={p_str} emd={e_str}")
        except Exception as ex:
            row["error"] = f"{type(ex).__name__}: {ex}"
            print(f"  [{i+1}/{len(files)}] {rec['run_id']}: FAILED — {row['error']}")
        rows.append(row)

        # Incremental rewrite so partial progress survives.
        cols = sorted({k for r in rows for k in r})
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print(f"Wrote {csv_path}")


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
