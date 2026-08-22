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
import gzip
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
RESULTS_ROOT = os.environ.get("NEON_RESULTS_ROOT", os.path.join(PROJECT_ROOT, "eval_results"))

from eval.utils import load_run, run_files  # noqa: E402

# Result directories carry the vocabulary of the evaluation chapter. The
# ``experiment`` field inside a stored record keeps its original value, so the
# mapping is explicit rather than derived.
RESULT_DIR = {
    "e1_correctness":     "correctness",
    "e2_performance":     "performance_default",
    "e10_protocols":      "performance_backends",
    "e3_grid":            "scaling_input_size",
    "e3_scaling_input":   "scaling_input_size_superseded",
    "e4_scaling_n":       "scaling_party_count",
    "e4b_scaling_n":      "scaling_party_count_controlled",
    "e7_network":         "scaling_network_latency",
    "e5_handovers":       "modes_handover",
    "e6_partial_orders":  "modes_partial_order",
    "e9_kanonymity":      "protection_k_anonymity",
    "e8_dp":              "protection_dp",
    "e8b_dp_delta":       "protection_dp_epsilon_delta",
}


def result_dir(experiment):
    """Directory holding one experiment's records."""
    return os.path.join(RESULTS_ROOT, RESULT_DIR.get(experiment, experiment))

RUN_CMD = ["python3", "-u", "pipeline/run.py"]

# Results written before this UTC instant predate the 2026-06-23 Stage-6 reveal
# changes (commits 533bde9 at 09:52Z and 70e5de9 at 10:09Z); their timings are
# stale and are dropped from aggregation. The boundary is set just after the
# later commit: every run from 10:10Z onward (including the morning e7 local
# rerun) used the new binary. e8b is exempt — its DP variant counts are
# reveal-independent. Override via NEON_NEW_CODE_CUTOFF if a later change needs
# a new boundary.
NEW_CODE_CUTOFF = os.environ.get("NEON_NEW_CODE_CUTOFF", "2026-06-23T10:10:00Z")

# Override via env to run on a cluster with a different data layout (e.g. CLAIX:
#   export NEON_DATA_ROOT=$HOME).
DATA_ROOT = os.environ.get("NEON_DATA_ROOT", "data")
# Override the --threads flag passed to every run (CLAIX nodes have 96 cores).
DEFAULT_THREADS = int(os.environ.get("NEON_THREADS", "16"))

# ---------------------------------------------------------------------------
# Dataset catalog
# ---------------------------------------------------------------------------

# Every log lives at data/<n>parties/<dataset>/party_<i>.<ext>; the two-party
# logs are the OrgA/OrgB attribute split of a public single-organization log
# (see data/PROVENANCE.md for the archive each one came from).
N2 = f"{DATA_ROOT}/2parties"

N2_DATASETS = {
    "bpi13_incidents":   (f"{N2}/bpi13_incidents/party_0.xes.gz",
                          f"{N2}/bpi13_incidents/party_1.xes.gz"),
    "bpi13_open":        (f"{N2}/bpi13_open/party_0.xes.gz",
                          f"{N2}/bpi13_open/party_1.xes.gz"),
    "bpi13_closed":      (f"{N2}/bpi13_closed/party_0.xes.gz",
                          f"{N2}/bpi13_closed/party_1.xes.gz"),
    "sepsis":            (f"{N2}/sepsis/party_0.xes.gz",
                          f"{N2}/sepsis/party_1.xes.gz"),
    "requestforpayment": (f"{N2}/requestforpayment/party_0.xes.gz",
                          f"{N2}/requestforpayment/party_1.xes.gz"),
    "bpi17_offer":       (f"{N2}/bpi17_offer/party_0.xes",
                          f"{N2}/bpi17_offer/party_1.xes"),
    "bpi12":             (f"{N2}/bpi12/party_0.xes.gz",
                          f"{N2}/bpi12/party_1.xes.gz"),
    "hospital":          (f"{N2}/hospital/party_0.xes.gz",
                          f"{N2}/hospital/party_1.xes.gz"),
    "domestic_decl":     (f"{N2}/domestic_decl/party_0.xes.gz",
                          f"{N2}/domestic_decl/party_1.xes.gz"),
    "international_decl":(f"{N2}/international_decl/party_0.xes.gz",
                          f"{N2}/international_decl/party_1.xes.gz"),
    "permit":            (f"{N2}/permit/party_0.xes.gz",
                          f"{N2}/permit/party_1.xes.gz"),
}

# N-way splits for E4 (scaling in number of parties)
N_WAY_DATASETS = {
    name: {n: [f"{DATA_ROOT}/{n}parties/{name}/party_{i}" +
               (".xes.gz" if n == 2 else ".xes") for i in range(n)]
           for n in (2, 3, 4, 5)}
    for name in ("bpi13_open", "bpi13_closed", "bpi13_incidents", "sepsis")
}

# ---------------------------------------------------------------------------
# Experiment plan
# Each entry returns a list of (run_id, mpc_args, n_parties, log_paths, meta)
# ---------------------------------------------------------------------------

def _runs_e1_correctness():
    """E1: exact-match vs centralized baseline. 1 run per dataset (deterministic).

    Covers every dataset used anywhere in the Chapter 6 experiment registry.
    Cheap: one rep per log, no MPC cost beyond a single pipeline run."""
    out = []
    datasets = ("bpi13_incidents", "bpi13_open", "bpi13_closed", "sepsis", "requestforpayment",
                "bpi17_offer", "bpi12", "domestic_decl", "international_decl", "hospital", "permit")
    for dset in datasets:
        logs = list(N2_DATASETS[dset])
        rid = f"e1__{dset}__default__rep0"
        out.append((rid, ["--threshold", "1", "--k-anon", "0"], 2, logs,
                    {"experiment": "e1_correctness", "dataset": dset, "compare_baseline": True}))
    return out


def _runs_e2_performance(reps=5):
    """E2 baseline performance — same 11 datasets as E1 for one-to-one
    correctness-vs-performance row alignment in the chapter's headline table."""
    out = []
    datasets = ("bpi13_incidents", "bpi13_open", "bpi13_closed", "sepsis", "requestforpayment",
                "bpi17_offer", "bpi12", "domestic_decl", "international_decl", "hospital", "permit")
    for dset, rep in itertools.product(datasets, range(reps)):
        logs = list(N2_DATASETS[dset])
        rid = f"e2__{dset}__default__rep{rep}"
        out.append((rid, ["--threshold", "1", "--k-anon", "0"], 2, logs,
                    {"experiment": "e2_performance", "dataset": dset, "rep": rep}))
    return out


def _runs_e3_scaling_input(reps=3):
    """E3: scaling in input size. Uses pipeline/run.py --n-per-party-cap to
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


def _runs_e3_grid(reps=3):
    """E3 replacement: controlled 2D sweep of the two encoded input dimensions.

    Case count N and row width P vary independently on a geometric grid.
    --n-per-party-cap fixes N (seed 42 for every cell: with pinned dimensions
    the circuit is identical across repetitions, so repetitions measure
    execution variance only); --force-partial-len truncates longer traces and
    pads the encoding so every cell's circuit has width exactly P. The
    pipeline's cost is data-oblivious, so the dimensions determine cost;
    truncation only pins the width and does not affect cost validity.
    bpi17_offer is excluded (max trace length 5 cannot sweep P)."""
    out = []
    datasets = ("sepsis", "bpi13_incidents")
    n_cells = (10, 50, 200, 1000)
    p_cells = (8, 16, 32, 64)
    for dset, n, p, rep in itertools.product(datasets, n_cells, p_cells, range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "0",
                "--n-per-party-cap", str(n), "--seed", "42",
                "--force-partial-len", str(p)]
        rid = f"e3g__{dset}__n{n}__p{p}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e3_grid", "dataset": dset,
                     "n_per_party_cap": n, "partial_len": p, "seed": 42, "rep": rep}))
    return out


def _runs_e4b_scaling_n(reps=3):
    """E4b: multiparty scaling sweep on the e4b inputs (generate_e4b_splits.py).

    Every cell holds the same 500 joint cases at 100% overlap with the row
    width pinned to 20 via --force-partial-len as n and the total row count
    n*C increase. The registry retains supplementary two-party diagnostic
    cells whose total row count matches the corresponding n-party cell."""
    C = 500
    out = []
    datasets = ("sepsis", "bpi13_incidents")
    args = ["--threshold", "1", "--k-anon", "0", "--force-partial-len", "20"]
    for dset, n, rep in itertools.product(datasets, (2, 3, 4, 5), range(reps)):
        logs = [f"{DATA_ROOT}/{n}parties/e4b_{dset}_c{C}/party_{i}.xes" for i in range(n)]
        rid = f"e4b__{dset}__N{n}__rep{rep}"
        out.append((rid, list(args), n, logs,
                    {"experiment": "e4b_scaling_n", "dataset": dset, "n_parties": n,
                     "c_cases": C, "partial_len": 20, "control": False, "rep": rep}))
    for dset, n, rep in itertools.product(datasets, (3, 4, 5), range(reps)):
        m = n * C // 2
        logs = [f"{DATA_ROOT}/2parties/e4b_{dset}_c{m}/party_{i}.xes" for i in range(2)]
        rid = f"e4b__{dset}__ctrlN{n}__rep{rep}"
        out.append((rid, list(args), 2, logs,
                    {"experiment": "e4b_scaling_n", "dataset": dset, "n_parties": 2,
                     "matched_n": n, "cases_per_party": m, "partial_len": 20,
                     "control": True, "rep": rep}))
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
    # Three smallest N=2 logs by MPC round count: bpi13_open (~1.2M rounds),
    # bpi13_closed (~2.8M), requestforpayment (~14.5M). E7 runs locally with
    # sudo (CLAIX denies CAP_NET_ADMIN); at wan-ent latency (5ms × millions of
    # rounds) larger logs are infeasible on a laptop. The latency multiplier
    # is dataset-invariant across the three.
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


def _runs_e8b_dp_delta(reps=5):
    """DP delta-sweep ablation at the four E8 epsilons on the three E8 datasets.

    Keeps the existing E8 grid (epsilon-sweep at delta=0.01) intact and adds
    three tighter delta values per (dataset, epsilon) cell so the (epsilon, delta)
    plane is sampled. The baseline (epsilon=None) is delta-independent and is
    not repeated here.
    """
    out = []
    epsilons = [0.1, 0.5, 1.0, 2.0]
    deltas = [1e-3, 1e-4, 1e-5]
    for dset, eps, delta, rep in itertools.product(
            ("sepsis", "bpi12", "international_decl"), epsilons, deltas, range(reps)):
        logs = list(N2_DATASETS[dset])
        args = ["--threshold", "1", "--k-anon", "1", "--enable-dp", "1",
                "--epsilon", str(eps), "--dp-delta", str(delta)]
        eps_tag = f"eps{eps}".replace(".", "p")
        delta_tag = f"d{delta:.0e}".replace("e-0", "e-").replace("e-", "em")
        rid = f"e8b__{dset}__{eps_tag}__{delta_tag}__rep{rep}"
        out.append((rid, args, 2, logs,
                    {"experiment": "e8b_dp_delta", "dataset": dset,
                     "epsilon": eps, "dp_delta": delta, "rep": rep}))
    return out


EXPERIMENTS = {
    "e1": _runs_e1_correctness,
    "e2": _runs_e2_performance,
    "e3": _runs_e3_scaling_input,   # superseded by e3g; records stay local, see .gitignore
    "e3g": _runs_e3_grid,
    "e4": _runs_e4_scaling_n,
    "e4b": _runs_e4b_scaling_n,
    "e5": _runs_e5_handovers,
    "e6": _runs_e6_partial_orders,
    "e7": _runs_e7_network,
    "e8": _runs_e8_dp,
    "e8b": _runs_e8b_dp_delta,
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
# Output parsing
# ---------------------------------------------------------------------------

_TIMER_RE   = re.compile(r"Stopped timer (\d+) at ([\d\.]+) \(([\d\.]+) MB, (\d+) rounds\)")
_TIME_RE    = re.compile(r"Time = ([\d\.]+) seconds")
_GLOBAL_RE  = re.compile(r"Global data sent = ([\d\.]+) MB")
_PARTY0_RE  = re.compile(r"Data sent = ([\d\.]+) MB")
_RAW_RE     = re.compile(r"RAW_RESULT Count:\s*(\d+)\s*Trace:")
_END_RE     = re.compile(r"END_TRACE")
_OTHERS_RE  = re.compile(r"Others count \(below threshold\):\s*(\d+)")
_DP_RE      = re.compile(r"DP_APPLIED Epsilon:(\d+)/(\d+)(?: K:(\d+))?")


def _disjoint_total_rounds(timers):
    """Sum a non-overlapping set of MP-SPDZ stage timers.

    Timer 5 encloses the grouping sub-timers 6--8, so summing every timer
    double-counts that stage.  Prefer the outer grouping timer when present;
    retain the sub-timer fallback for older outputs without timer 5.
    """
    normalized = {int(timer_id): timer for timer_id, timer in timers.items()}
    total = sum(normalized[timer_id]["rounds"]
                for timer_id in (2, 3, 4, 9, 10)
                if timer_id in normalized)
    if 5 in normalized:
        total += normalized[5]["rounds"]
    else:
        total += sum(normalized[timer_id]["rounds"]
                     for timer_id in (6, 7, 8)
                     if timer_id in normalized)
    return total


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
        metrics["total_rounds"] = _disjoint_total_rounds(timers)
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
        record["stdout_tail"] = proc.stdout[-6000:]

    # Snapshot Player-Data/activity_map.json so variant IDs can be resolved later.
    amap_path = os.path.join(PROJECT_ROOT, "Player-Data", "activity_map.json")
    if os.path.exists(amap_path):
        try:
            with open(amap_path) as f:
                record["activity_map"] = json.load(f)
        except Exception:
            pass

    if write_results:
        out_dir = result_dir(meta["experiment"])
        os.makedirs(out_dir, exist_ok=True)
        # Gzipped: the Stage-6 output matrix is mostly padding, so a record
        # compresses by about a hundredfold. eval.utils.load_run reads both forms.
        out = os.path.join(out_dir, f"{run_id}.json.gz")
        with gzip.open(out, "wt", encoding="utf-8") as f:
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
    for exp in sorted(set(RESULT_DIR.values())):
        exp_dir = os.path.join(RESULTS_ROOT, exp)
        if not os.path.isdir(exp_dir):
            continue
        rows = []
        dropped = 0
        for path in run_files(exp_dir):
            rec = load_run(path)
            if "run_id" not in rec:
                continue
            metrics = rec.get("metrics", {})
            # Drop failed runs and pre-change (old-code) rows so stale timings do
            # not pollute the aggregate. e8b is exempt: its DP variant counts are
            # reveal-independent, so its pre-change samples remain valid and keep
            # the (eps, delta) grid complete.
            if metrics.get("return_code") != 0:
                dropped += 1
                continue
            if not exp.startswith("e8b") and metrics.get("timestamp", "") < NEW_CODE_CUTOFF:
                dropped += 1
                continue
            row = {"run_id": rec["run_id"], **rec.get("meta", {}),
                   "n_parties": rec.get("n_parties"),
                   "rc": rec["metrics"].get("return_code"),
                   "wall_s": rec["metrics"].get("wall_time_s"),
                   "runtime_s": rec["metrics"].get("total_runtime_s"),
                   "global_data_mb": rec["metrics"].get("global_data_sent_mb"),
                   "party0_data_mb": rec["metrics"].get("data_sent_party0_mb"),
                   # Recompute from stored timer breakdowns so legacy records
                   # produced before the nested-timer fix aggregate correctly.
                   "total_rounds": (_disjoint_total_rounds(metrics["timers"])
                                    if metrics.get("timers")
                                    else metrics.get("total_rounds")),
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
        print(f"{exp}: {len(rows)} runs (dropped {dropped} old/failed) -> {csv_path}")
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
