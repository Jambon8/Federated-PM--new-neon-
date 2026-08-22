"""
Correctness evaluation: compare MPC output against centralized baseline.
"""

import sys
import os
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eval.baseline import compute_baseline
from eval.utils import save_results, setup_logging
from pipeline import import_xes
from web import api_helper

logger = setup_logging("correctness")


def _normalize_trace(trace):
    """Convert trace (list of steps, each step a list of activity names) to a hashable key."""
    return tuple(tuple(sorted(step)) if len(step) > 1 else (step[0],) for step in trace)


def run_mpc_pipeline(log_a, log_b, threshold=1, k_anon=0, partial_orders=0,
                     threads=16, enable_dp=0, epsilon=1.0, dp_delta=0.01):
    """Run the MPC pipeline via subprocess and parse output."""
    cmd = [
        "python3", "-u", "pipeline/run.py",
        "--log-a", log_a,
        "--log-b", log_b,
        "--threshold", str(threshold),
        "--threads", str(threads),
        "--k-anon", str(k_anon),
        "--partial-orders", str(partial_orders),
    ]
    if enable_dp:
        cmd.extend(["--enable-dp", str(enable_dp), "--epsilon", str(epsilon),
                     "--dp-delta", str(dp_delta)])
    logger.info(f"Running MPC: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))

    if result.returncode != 0:
        logger.error(f"MPC failed:\n{result.stdout}\n{result.stderr}")
        return None

    output_lines = (result.stdout + result.stderr).split("\n")
    parsed = api_helper.parse_output(output_lines)
    return parsed


def compare_results(baseline_variants, mpc_results):
    """Compare baseline variants against MPC output."""
    # Build lookup from normalized trace -> count
    baseline_map = {}
    for v in baseline_variants:
        key = _normalize_trace(v["trace"])
        baseline_map[key] = v["count"]

    mpc_map = {}
    for r in mpc_results:
        if r["type"] != "trace":
            continue
        key = _normalize_trace(r["trace"])
        mpc_map[key] = r["count"]

    all_keys = set(baseline_map.keys()) | set(mpc_map.keys())

    matched = 0
    mismatched = []
    missing_from_mpc = []
    extra_in_mpc = []

    for key in all_keys:
        b_count = baseline_map.get(key)
        m_count = mpc_map.get(key)

        if b_count is not None and m_count is not None:
            if b_count == m_count:
                matched += 1
            else:
                mismatched.append({"trace": key, "baseline": b_count, "mpc": m_count})
        elif b_count is not None:
            missing_from_mpc.append({"trace": key, "count": b_count})
        else:
            extra_in_mpc.append({"trace": key, "count": m_count})

    total = len(all_keys)
    return {
        "match": len(mismatched) == 0 and len(missing_from_mpc) == 0 and len(extra_in_mpc) == 0,
        "total_variants": total,
        "matched": matched,
        "mismatched": mismatched,
        "missing_from_mpc": missing_from_mpc,
        "extra_in_mpc": extra_in_mpc,
    }


def run_correctness_test(log_a, log_b, threshold=1, k_anon=0, partial_orders=0,
                         use_handovers=False, threads=16, enable_dp=0, epsilon=1.0):
    """Run both baseline and MPC, compare results."""
    logger.info("Computing centralized baseline...")
    cases_a = import_xes.parse_xes(log_a, use_handovers=use_handovers)
    cases_b = import_xes.parse_xes(log_b, use_handovers=use_handovers)

    baseline = compute_baseline(
        cases_a, cases_b,
        threshold=threshold,
        enable_k_anon=k_anon,
        enable_partial_orders=partial_orders,
    )

    logger.info("Running MPC pipeline...")
    mpc_parsed = run_mpc_pipeline(log_a, log_b, threshold, k_anon, partial_orders, threads,
                                  enable_dp=enable_dp, epsilon=epsilon)

    if mpc_parsed is None:
        return {"match": False, "error": "MPC pipeline failed"}

    logger.info("Comparing results...")
    comparison = compare_results(baseline["variants"], mpc_parsed["results"])

    result = {
        "log_a": log_a,
        "log_b": log_b,
        "threshold": threshold,
        "k_anon": k_anon,
        "partial_orders": partial_orders,
        "enable_dp": enable_dp,
        "epsilon": epsilon,
        "baseline_variant_count": len(baseline["variants"]),
        "mpc_variant_count": sum(1 for r in mpc_parsed["results"] if r["type"] == "trace"),
        "comparison": comparison,
    }

    if comparison["match"]:
        logger.info("PASS: All variants match exactly.")
    else:
        logger.warning(f"FAIL: {len(comparison['mismatched'])} mismatched, "
                       f"{len(comparison['missing_from_mpc'])} missing, "
                       f"{len(comparison['extra_in_mpc'])} extra.")

    return result


def run_determinism_test(log_a, log_b, runs=3, threshold=1, partial_orders=0, threads=16):
    """Run MPC multiple times and check outputs are identical."""
    logger.info(f"Running determinism test ({runs} runs)...")
    outputs = []
    for run_idx in range(runs):
        logger.info(f"  Run {run_idx + 1}/{runs}...")
        parsed = run_mpc_pipeline(log_a, log_b, threshold, partial_orders=partial_orders, threads=threads)
        if parsed is None:
            return {"deterministic": False, "error": f"Run {run_idx + 1} failed"}
        trace_set = set()
        for r in parsed["results"]:
            if r["type"] == "trace":
                key = (_normalize_trace(r["trace"]), r["count"])
                trace_set.add(key)
        outputs.append(trace_set)

    # Compare all pairs
    deterministic = all(outputs[i] == outputs[0] for i in range(1, runs))
    if deterministic:
        logger.info("PASS: All runs produced identical output.")
    else:
        logger.warning("FAIL: Outputs differ between runs.")

    return {
        "deterministic": deterministic,
        "runs": runs,
        "variant_counts": [len(o) for o in outputs],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Correctness evaluation")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--threshold", type=int, default=1)
    parser.add_argument("--k-anon", type=int, default=0)
    parser.add_argument("--partial-orders", type=int, default=0)
    parser.add_argument("--use-handovers", action="store_true")
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--enable-dp", type=int, default=0, help="Enable DP (0/1)")
    parser.add_argument("--epsilon", type=float, default=1.0, help="DP epsilon")
    parser.add_argument("--determinism", action="store_true", help="Run determinism test instead")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs for determinism test")
    args = parser.parse_args()

    if args.determinism:
        result = run_determinism_test(
            args.log_a, args.log_b, runs=args.runs,
            threshold=args.threshold, partial_orders=args.partial_orders,
            threads=args.threads,
        )
    else:
        result = run_correctness_test(
            args.log_a, args.log_b,
            threshold=args.threshold, k_anon=args.k_anon,
            partial_orders=args.partial_orders,
            use_handovers=args.use_handovers, threads=args.threads,
            enable_dp=args.enable_dp, epsilon=args.epsilon,
        )

    save_results(result, "correctness", "correctness")
