"""Thesis Chapter 6 experiment runner — single source of truth for E1..E8.

Usage:
    # List every run as a shell command (one per line) — feed to SLURM array.
    python3 eval/thesis_experiments.py --list

    # List only one experiment block.
    python3 eval/thesis_experiments.py --list --experiment e1

    # Execute exactly one run by ID; writes JSON to eval_results/<exp>/<run_id>.json.
    python3 eval/thesis_experiments.py --run e1__bpi13_incidents__default__rep0

    # Aggregate all completed JSONs into one CSV per experiment.
    python3 eval/thesis_experiments.py --aggregate
"""

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
RESULTS_ROOT = os.path.join(PROJECT_ROOT, "eval_results")
RUN_CMD = ["python3", "-u", "examples/run_process_mining.py"]

# Override via env to run on a cluster with a different data layout (e.g. CLAIX:
#   export NEON_DATA_ROOT=$HOME).
DATA_ROOT = os.environ.get("NEON_DATA_ROOT", "data")
# Override the --threads flag passed to every run (CLAIX nodes have 96 cores).
DEFAULT_THREADS = int(os.environ.get("NEON_THREADS", "16"))

# ---------------------------------------------------------------------------
# Dataset catalog (verified by /tmp/verify_v2.py against data/Master_Input)
# ---------------------------------------------------------------------------

MASTER = f"{DATA_ROOT}/Master_Input"

N2_DATASETS = {
    "bpi13_incidents":   (f"{MASTER}/OrgA/BPI_Challenge_2013_incidents.xes.gz",
                          f"{MASTER}/OrgB/BPI_Challenge_2013_incidents.xes.gz"),
    "bpi13_open":        (f"{MASTER}/OrgA/BPI_Challenge_2013_open_problems.xes.gz",
                          f"{MASTER}/OrgB/BPI_Challenge_2013_open_problems.xes.gz"),
    "bpi13_closed":      (f"{MASTER}/OrgA/BPI_Challenge_2013_closed_problems.xes.gz",
                          f"{MASTER}/OrgB/BPI_Challenge_2013_closed_problems.xes.gz"),
    "sepsis":            (f"{MASTER}/OrgA/Sepsis_Cases_OrgA.xes.gz",
                          f"{MASTER}/OrgB/Sepsis_Cases_OrgB.xes.gz"),
    "requestforpayment": (f"{MASTER}/OrgA/RequestForPayment_OrgA.xes.gz",
                          f"{MASTER}/OrgB/RequestForPayment_OrgB.xes.gz"),
    "bpi17_offer":       (f"{MASTER}/OrgA/BPIChallenge2017-Offerlog.xes",
                          f"{MASTER}/OrgB/BPIChallenge2017-Offerlog.xes"),
    "bpi12":             (f"{MASTER}/OrgA/BPI_Challenge_2012.xes.gz",
                          f"{MASTER}/OrgB/BPI_Challenge_2012.xes.gz"),
    "hospital":          (f"{MASTER}/OrgA/Hospital_log.xes.gz",
                          f"{MASTER}/OrgB/Hospital_log.xes.gz"),
    "domestic_decl":     (f"{MASTER}/OrgA/DomesticDeclarations_OrgA.xes.gz",
                          f"{MASTER}/OrgB/DomesticDeclarations_OrgB.xes.gz"),
    "international_decl":(f"{MASTER}/OrgA/InternationalDeclarations_OrgA.xes.gz",
                          f"{MASTER}/OrgB/InternationalDeclarations_OrgB.xes.gz"),
    "permit":            (f"{MASTER}/OrgA/PermitLog_OrgA.xes.gz",
                          f"{MASTER}/OrgB/PermitLog_OrgB.xes.gz"),
}

# N-way splits for E4 (scaling in number of parties)
N_WAY_DATASETS = {
    "bpi13_open": {
        2: [f"{MASTER}/OrgA/BPI_Challenge_2013_open_problems.xes.gz",
            f"{MASTER}/OrgB/BPI_Challenge_2013_open_problems.xes.gz"],
        3: [f"{DATA_ROOT}/split/party_{i}.xes" for i in range(3)],
        4: [f"{DATA_ROOT}/split_4/party_{i}.xes" for i in range(4)],
        5: [f"{DATA_ROOT}/split_5/party_{i}.xes" for i in range(5)],
    },
    "bpi13_closed": {
        2: [f"{MASTER}/OrgA/BPI_Challenge_2013_closed_problems.xes.gz",
            f"{MASTER}/OrgB/BPI_Challenge_2013_closed_problems.xes.gz"],
        3: [f"{DATA_ROOT}/bpi13_closed/split_3/party_{i}.xes" for i in range(3)],
        4: [f"{DATA_ROOT}/bpi13_closed/split_4/party_{i}.xes" for i in range(4)],
        5: [f"{DATA_ROOT}/bpi13_closed/split_5/party_{i}.xes" for i in range(5)],
    },
    "bpi13_incidents": {
        2: list(N2_DATASETS["bpi13_incidents"]),
        3: [f"{DATA_ROOT}/bpi13_incidents/split_3/party_{i}.xes" for i in range(3)],
        4: [f"{DATA_ROOT}/bpi13_incidents/split_4/party_{i}.xes" for i in range(4)],
        5: [f"{DATA_ROOT}/bpi13_incidents/split_5/party_{i}.xes" for i in range(5)],
    },
    "sepsis": {
        2: list(N2_DATASETS["sepsis"]),
        3: [f"{DATA_ROOT}/sepsis/split_3/party_{i}.xes" for i in range(3)],
        4: [f"{DATA_ROOT}/sepsis/split_4/party_{i}.xes" for i in range(4)],
        5: [f"{DATA_ROOT}/sepsis/split_5/party_{i}.xes" for i in range(5)],
    },
}

# ---------------------------------------------------------------------------
# Experiment plan
# Each entry returns a list of (run_id, mpc_args, n_parties, log_paths, meta)
# ---------------------------------------------------------------------------

def _runs_e1_correctness():
    """E1: exact-match vs centralized baseline. 1 run per dataset (deterministic).

    Expanded from 3 to 8 logs to give correctness coverage comparable to
    Rennert's DFG paper (10-log mega-table). Cheap: one rep per log, no MPC
    cost beyond a single pipeline run."""
    out = []
    datasets = ("bpi13_incidents", "sepsis", "requestforpayment",
                "bpi17_offer", "bpi12", "domestic_decl", "hospital", "permit")
    for dset in datasets:
        logs = list(N2_DATASETS[dset])
        rid = f"e1__{dset}__default__rep0"
        out.append((rid, ["--threshold", "1", "--k-anon", "0"], 2, logs,
                    {"experiment": "e1_correctness", "dataset": dset, "compare_baseline": True}))
    return out


def _runs_e2_performance(reps=5):
    out = []
    for dset, rep in itertools.product(("bpi13_incidents", "sepsis", "requestforpayment"), range(reps)):
        logs = list(N2_DATASETS[dset])
        rid = f"e2__{dset}__default__rep{rep}"
        out.append((rid, ["--threshold", "1", "--k-anon", "0"], 2, logs,
                    {"experiment": "e2_performance", "dataset": dset, "rep": rep}))
    return out


def _runs_e3_scaling_input(reps=3):
    """E3: scaling in input size. Uses run_process_mining.py --n-per-party-cap to
    subsample case IDs deterministically before encoding."""
    out = []
    # Three datasets per the locked plan; bpi17_offer added as the large anchor.
    datasets = ("sepsis", "bpi13_incidents", "bpi17_offer")
    case_caps = [10, 20, 50, 100, 200, 500, 1000]
    for dset, cap, rep in itertools.product(datasets, case_caps, range(reps)):
        logs = list(N2_DATASETS[dset])
        # Seed varies by rep so subsamples differ across repetitions.
        seed = 42 + rep
        args = ["--threshold", "1", "--k-anon", "0",
                "--n-per-party-cap", str(cap), "--seed", str(seed)]
        rid = f"e3__{dset}__n{cap}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e3_scaling_input", "dataset": dset,
                     "n_per_party_cap": cap, "seed": seed, "rep": rep}))
    return out


def _runs_e4_scaling_n(reps=3):
    out = []
    for dset, n, rep in itertools.product(N_WAY_DATASETS.keys(), (2, 3, 4, 5), range(reps)):
        logs = N_WAY_DATASETS[dset][n]
        rid = f"e4__{dset}__N{n}__rep{rep}"
        out.append((rid, ["--threshold", "1", "--k-anon", "0"], n, logs,
                    {"experiment": "e4_scaling_n", "dataset": dset, "n_parties": n, "rep": rep}))
    return out


def _runs_e5_handovers(reps=3):
    out = []
    for dset, ho, rep in itertools.product(
            ("domestic_decl", "international_decl", "permit"), (False, True), range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "0"]
        if ho:
            args.append("--use-handovers")
        rid = f"e5__{dset}__ho{int(ho)}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e5_handovers", "dataset": dset, "use_handovers": ho, "rep": rep}))
    return out


def _runs_e6_partial_orders(reps=3):
    """E6: PO on/off + sweep the concurrency window delta when on.
    Baseline (po=0) ignores delta; PO runs vary delta across 4 magnitudes."""
    out = []
    # Baseline: PO disabled (delta is irrelevant — flag set to 0 for record-keeping)
    for dset, rep in itertools.product(
            ("sepsis", "bpi12", "hospital"), range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "0", "--partial-orders", "0"]
        rid = f"e6__{dset}__po0__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e6_partial_orders", "dataset": dset,
                     "partial_orders": 0, "delta": "0", "rep": rep,
                     "compare_baseline": True}))
    # PO on — sweep delta. "0" = exact-equality merge (formal PO semantics);
    # higher values relax to near-simultaneous events.
    deltas = ["0", "1s", "1m", "1h"]
    for dset, delta, rep in itertools.product(
            ("sepsis", "bpi12", "hospital"), deltas, range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "0", "--partial-orders", "1",
                "--delta", delta]
        delta_tag = delta.replace(".", "p")
        rid = f"e6__{dset}__po1_delta{delta_tag}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e6_partial_orders", "dataset": dset,
                     "partial_orders": 1, "delta": delta, "rep": rep,
                     "compare_baseline": True}))
    return out


def _runs_e10_protocols(reps=3):
    """E10: MPC protocol comparison at N=3. Isolates protocol cost (different
    trust models) by reusing the E4 N=3 splits and varying only --protocol.
    All runs use the same default k-anon config so the only varying axis is
    the protocol."""
    out = []
    # Three protocols spanning the relevant trust models at N=3:
    #   semi    — passive, dishonest majority (tolerate N-1 corrupt)
    #   rep-bin — passive, honest majority (replicated 3PC)
    #   ccd     — passive, honest majority (CCD-based, generalises to N>3)
    protocols = ["semi", "rep-bin", "ccd"]
    for dset, proto, rep in itertools.product(
            ("bpi13_open", "bpi13_closed", "bpi13_incidents", "sepsis"),
            protocols, range(reps)):
        logs = N_WAY_DATASETS[dset][3]
        args = ["--threshold", "1", "--k-anon", "0", "--protocol", proto]
        proto_tag = proto.replace("-", "_")
        rid = f"e10__{dset}__{proto_tag}__rep{rep}"
        out.append((rid, args, 3, logs,
                    {"experiment": "e10_protocols", "dataset": dset,
                     "protocol": proto, "rep": rep}))
    return out


def _runs_e9_kanon(reps=3):
    """E9: k-anonymity in MPC — confirms the filter works correctly and that
    MPC cost is invariant to k (k-anon is a public post-grouping filter).
    Three k values suffice for the cost-flatness claim; the full release-profile
    curve over k is reconstructed from the centralized baseline at aggregation
    time. The k=1 baseline is already covered by E1 (k-anon=0)."""
    out = []
    ks = [2, 5, 20]
    for dset, k, rep in itertools.product(
            ("sepsis", "bpi13_incidents", "requestforpayment"), ks, range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", str(k), "--k-anon", "1"]
        rid = f"e9__{dset}__k{k}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e9_kanonymity", "dataset": dset,
                     "k": k, "rep": rep, "compare_baseline": True}))
    return out


def _runs_e7_network(reps=3):
    # Smallest three N=2 logs by MPC round count: bpi13_open (~1.2M rounds),
    # bpi13_closed (~2.8M), requestforpayment (~14.5M). Larger logs are infeasible
    # to run locally under sudo at wan-ent latency (5ms × millions of rounds).
    # E7 measures relative network-preset shape, not absolute throughput, so
    # smaller logs are fine.
    out = []
    for dset, net, rep in itertools.product(
            ("bpi13_open", "bpi13_closed", "requestforpayment"),
            ("unlimited", "lan", "wan-ent"), range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "0",
                "--mode", "local-virtual", "--network", net]
        rid = f"e7__{dset}__{net}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e7_network", "dataset": dset, "network": net, "rep": rep}))
    return out


def _runs_e8_dp(reps=5):
    out = []
    epsilons = [0.1, 0.5, 1.0, 2.0]
    delta = 0.01
    # Baseline: ENABLE_DP=0 (k-anon threshold filter only)
    for dset, rep in itertools.product(("sepsis", "bpi12", "international_decl"), range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "1"]
        rid = f"e8__{dset}__epsINF__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e8_dp", "dataset": dset,
                     "epsilon": None, "dp_delta": delta, "rep": rep}))
    # DP runs
    for dset, eps, rep in itertools.product(
            ("sepsis", "bpi12", "international_decl"), epsilons, range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "1", "--enable-dp", "1",
                "--epsilon", str(eps), "--dp-delta", str(delta)]
        eps_tag = f"eps{eps}".replace(".", "p")
        rid = f"e8__{dset}__{eps_tag}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e8_dp", "dataset": dset,
                     "epsilon": eps, "dp_delta": delta, "rep": rep}))
    return out


EXPERIMENTS = {
    "e1": _runs_e1_correctness,
    "e2": _runs_e2_performance,
    "e3": _runs_e3_scaling_input,
    "e4": _runs_e4_scaling_n,
    "e5": _runs_e5_handovers,
    "e6": _runs_e6_partial_orders,
    "e7": _runs_e7_network,
    "e8": _runs_e8_dp,
    "e9": _runs_e9_kanon,
    "e10": _runs_e10_protocols,
}


def all_runs(only=None):
    """Yield (run_id, mpc_args, n_parties, log_paths, meta) tuples."""
    keys = [only] if only else list(EXPERIMENTS.keys())
    for k in keys:
        for r in EXPERIMENTS[k]():
            yield r


# ---------------------------------------------------------------------------
# Output parsing — reuse eval/performance.py logic
# ---------------------------------------------------------------------------

_TIMER_RE   = re.compile(r"Stopped timer (\d+) at ([\d\.]+) \(([\d\.]+) MB, (\d+) rounds\)")
_TIME_RE    = re.compile(r"Time = ([\d\.]+) seconds")
_GLOBAL_RE  = re.compile(r"Global data sent = ([\d\.]+) MB")
_PARTY0_RE  = re.compile(r"Data sent = ([\d\.]+) MB")
_RAW_RE     = re.compile(r"RAW_RESULT Count:\s*(\d+)\s*Trace:")
_END_RE     = re.compile(r"END_TRACE")
_OTHERS_RE  = re.compile(r"Others count \(below threshold\):\s*(\d+)")
_DP_RE      = re.compile(r"DP_APPLIED Epsilon:(\d+)/(\d+)(?: K:(\d+))?")


def parse_run_output(text):
    """Extract metrics + variant set from a single MPC run's combined stdout/stderr."""
    metrics = {}
    timers = {}
    for m in _TIMER_RE.finditer(text):
        tid = int(m.group(1))
        timers[tid] = {"time_s": float(m.group(2)), "data_mb": float(m.group(3)),
                       "rounds": int(m.group(4))}
    if timers:
        metrics["timers"] = timers
        metrics["total_rounds"] = sum(t["rounds"] for t in timers.values())
    for r, key in [(_TIME_RE, "total_runtime_s"),
                   (_GLOBAL_RE, "global_data_sent_mb"),
                   (_PARTY0_RE, "data_sent_party0_mb")]:
        m = r.search(text)
        if m:
            metrics[key] = float(m.group(1))
    others_m = _OTHERS_RE.search(text)
    if others_m:
        metrics["others_below_threshold"] = int(others_m.group(1))
    dp_m = _DP_RE.search(text)
    if dp_m:
        metrics["dp"] = {"epsilon_num": int(dp_m.group(1)),
                         "epsilon_den": int(dp_m.group(2)),
                         "k": int(dp_m.group(3)) if dp_m.group(3) else None}

    variants = []
    current_trace = []
    current_count = None
    in_trace = False
    log_prefix = re.compile(r'^(?:INFO|DEBUG|WARNING|ERROR)\s*\([^)]*\):\s*')
    for line in text.split("\n"):
        line = log_prefix.sub("", line.strip())
        if (m := _RAW_RE.search(line)):
            current_count = int(m.group(1))
            current_trace = []
            in_trace = True
            continue
        if _END_RE.search(line):
            if in_trace:
                variants.append({"count": current_count, "raw": current_trace[:]})
            in_trace = False
            continue
        if in_trace:
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                current_trace.append((int(parts[0]), int(parts[1])))
            elif len(parts) == 1 and parts[0].isdigit():
                current_trace.append((int(parts[0]), 0))
    metrics["variants_released"] = len(variants)
    return metrics, variants


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def build_command(mpc_args, n_parties, log_paths):
    cmd = list(RUN_CMD)
    if n_parties == 2:
        cmd += ["--log-a", log_paths[0], "--log-b", log_paths[1]]
    else:
        cmd += ["--logs", *log_paths]
    cmd += list(mpc_args)
    # Inject --threads unless the per-experiment args already set it.
    if "--threads" not in mpc_args:
        cmd += ["--threads", str(DEFAULT_THREADS)]
    return cmd


def run_one(run_id, write_results=True):
    target = None
    for rid, mpc_args, n, logs, meta in all_runs():
        if rid == run_id:
            target = (mpc_args, n, logs, meta)
            break
    if target is None:
        print(f"ERROR: unknown run_id {run_id}", file=sys.stderr)
        return 1
    mpc_args, n_parties, logs, meta = target

    cmd = build_command(mpc_args, n_parties, logs)
    print(f"[{run_id}] cmd: {' '.join(cmd)}")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    wall = time.time() - t0
    combined = proc.stdout + "\n" + proc.stderr

    metrics, variants = parse_run_output(combined)
    metrics["wall_time_s"] = wall
    metrics["return_code"] = proc.returncode
    metrics["timestamp"] = datetime.utcnow().isoformat() + "Z"

    record = {
        "run_id": run_id,
        "meta": meta,
        "n_parties": n_parties,
        "log_paths": logs,
        "mpc_args": mpc_args,
        "metrics": metrics,
        "variants": variants if proc.returncode == 0 else [],
    }
    if proc.returncode != 0:
        record["stderr_tail"] = proc.stderr[-2000:]

    # Snapshot Player-Data/activity_map.json so variant IDs can be resolved later.
    amap_path = os.path.join(PROJECT_ROOT, "Player-Data", "activity_map.json")
    if os.path.exists(amap_path):
        try:
            with open(amap_path) as f:
                record["activity_map"] = json.load(f)
        except Exception:
            pass

    if write_results:
        exp = meta["experiment"]
        out_dir = os.path.join(RESULTS_ROOT, exp)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{run_id}.json")
        with open(out, "w") as f:
            json.dump(record, f, indent=2, default=str)
        print(f"[{run_id}] -> {out}  rc={proc.returncode}  wall={wall:.1f}s")
    return 0 if proc.returncode == 0 else 2


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate():
    if not os.path.isdir(RESULTS_ROOT):
        print(f"No results in {RESULTS_ROOT}")
        return
    summary = {}
    # Only aggregate directories that follow the e1..e8 schema; skip legacy result folders.
    for exp in sorted(os.listdir(RESULTS_ROOT)):
        exp_dir = os.path.join(RESULTS_ROOT, exp)
        if not os.path.isdir(exp_dir):
            continue
        if not re.match(r"e\d+_", exp):
            continue
        rows = []
        for fn in sorted(os.listdir(exp_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(exp_dir, fn)) as f:
                rec = json.load(f)
            if "run_id" not in rec:
                continue
            row = {"run_id": rec["run_id"], **rec.get("meta", {}),
                   "n_parties": rec.get("n_parties"),
                   "rc": rec["metrics"].get("return_code"),
                   "wall_s": rec["metrics"].get("wall_time_s"),
                   "runtime_s": rec["metrics"].get("total_runtime_s"),
                   "global_data_mb": rec["metrics"].get("global_data_sent_mb"),
                   "party0_data_mb": rec["metrics"].get("data_sent_party0_mb"),
                   "total_rounds": rec["metrics"].get("total_rounds"),
                   "variants_released": rec["metrics"].get("variants_released"),
                   "others_below_threshold": rec["metrics"].get("others_below_threshold")}
            rows.append(row)
        if not rows:
            continue
        # Write CSV
        import csv
        cols = sorted({k for r in rows for k in r.keys()})
        csv_path = os.path.join(RESULTS_ROOT, f"{exp}.csv")
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        summary[exp] = (len(rows), csv_path)
        print(f"{exp}: {len(rows)} runs -> {csv_path}")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="Print shell commands for every run, one per line.")
    ap.add_argument("--experiment", choices=list(EXPERIMENTS.keys()),
                    help="Filter --list to one experiment block.")
    ap.add_argument("--run", help="Execute one run by ID.")
    ap.add_argument("--aggregate", action="store_true",
                    help="Walk eval_results/ and emit one CSV per experiment.")
    ap.add_argument("--count", action="store_true",
                    help="Print per-experiment run counts.")
    args = ap.parse_args()

    if args.count:
        for k in EXPERIMENTS:
            n = sum(1 for _ in EXPERIMENTS[k]())
            print(f"{k}: {n} runs")
        return

    if args.list:
        for rid, _, _, _, _ in all_runs(only=args.experiment):
            print(f"python3 eval/thesis_experiments.py --run {rid}")
        return

    if args.run:
        sys.exit(run_one(args.run))

    if args.aggregate:
        aggregate()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
