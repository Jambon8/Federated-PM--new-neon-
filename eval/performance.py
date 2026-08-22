"""
Performance benchmark automation.
Runs the full pipeline with varying parameters and collects metrics.
"""

import sys
import os
import argparse
import subprocess
import re
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eval.utils import save_results, load_config, subsample_cases, truncate_traces, setup_logging
from pipeline import import_xes

logger = setup_logging("performance")


def _disjoint_total_rounds(timers):
    """Sum stage rounds without double-counting nested grouping timers."""
    total = sum(timers[timer_id]["rounds"]
                for timer_id in (2, 3, 4, 9, 10)
                if timer_id in timers)
    if 5 in timers:
        total += timers[5]["rounds"]
    else:
        total += sum(timers[timer_id]["rounds"]
                     for timer_id in (6, 7, 8)
                     if timer_id in timers)
    return total


def _parse_metrics(output_text):
    """Extract runtime and communication metrics from MPC output."""
    metrics = {}

    m = re.search(r"Compile time: ([\d\.]+)s", output_text)
    if m:
        metrics["compile_time_s"] = float(m.group(1))

    m = re.search(r"Time = ([\d\.]+) seconds", output_text)
    if m:
        metrics["total_runtime_s"] = float(m.group(1))

    m = re.search(r"Global data sent = ([\d\.]+) MB", output_text)
    if m:
        metrics["global_data_sent_mb"] = float(m.group(1))

    m = re.search(r"Data sent = ([\d\.]+) MB", output_text)
    if m:
        metrics["data_sent_party0_mb"] = float(m.group(1))

    # Per-timer breakdown
    timers = {}
    for m in re.finditer(r"Stopped timer (\d+) at ([\d\.]+) \(([\d\.]+) MB, (\d+) rounds\)", output_text):
        timer_id = int(m.group(1))
        timers[timer_id] = {
            "time_s": float(m.group(2)),
            "data_mb": float(m.group(3)),
            "rounds": int(m.group(4)),
        }
    if timers:
        metrics["timer_breakdown"] = timers

    metrics["total_rounds"] = _disjoint_total_rounds(timers)

    return metrics


def run_single_benchmark(log_a, log_b, n_cases=None, max_trace_len=None,
                         threshold=1, threads=16, partial_orders=0,
                         use_handovers=False, mode="local", seed=42,
                         enable_dp=0, epsilon=1.0, dp_delta=0.01):
    """Run one benchmark configuration and return metrics."""
    cases_a = import_xes.parse_xes(log_a, use_handovers=use_handovers)
    cases_b = import_xes.parse_xes(log_b, use_handovers=use_handovers)

    if n_cases is not None:
        cases_a = subsample_cases(cases_a, n_cases, seed=seed)
        cases_b = subsample_cases(cases_b, n_cases, seed=seed + 1)

    if max_trace_len is not None:
        cases_a = truncate_traces(cases_a, max_trace_len)
        cases_b = truncate_traces(cases_b, max_trace_len)

    n_per_party, partial_len = import_xes.encode_and_save(cases_a, cases_b)

    cmd = [
        "python3", "-u", "pipeline/run.py",
        "--log-a", log_a,
        "--log-b", log_b,
        "--threshold", str(threshold),
        "--threads", str(threads),
        "--partial-orders", str(partial_orders),
        "--mode", mode,
    ]
    if use_handovers:
        cmd.append("--use-handovers")
    if enable_dp:
        cmd.extend(["--enable-dp", str(enable_dp), "--epsilon", str(epsilon),
                     "--dp-delta", str(dp_delta)])

    project_root = os.path.dirname(os.path.dirname(__file__))

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)
    wall_time = time.time() - start

    output = result.stdout + result.stderr

    if result.returncode != 0:
        logger.error(f"Run failed (n={n_cases}, tl={max_trace_len}):\n{output[-500:]}")
        return None

    metrics = _parse_metrics(output)
    metrics["wall_time_s"] = wall_time
    metrics["n_per_party"] = n_per_party
    metrics["partial_len"] = partial_len
    metrics["n_cases_requested"] = n_cases
    metrics["max_trace_len_requested"] = max_trace_len

    return metrics


def run_scaling_benchmark(log_a, log_b, config=None, use_handovers=False):
    """Run full scaling benchmark grid."""
    if config is None:
        config = load_config()

    case_counts = config["performance"]["case_counts"]
    repetitions = config["performance"]["repetitions"]
    threads = config["performance"]["threads"]

    results = {"experiment": "scaling_cases", "results": []}

    for n in case_counts:
        for rep in range(repetitions):
            logger.info(f"  n_cases={n}, rep={rep + 1}/{repetitions}")
            metrics = run_single_benchmark(
                log_a, log_b, n_cases=n, threads=threads,
                use_handovers=use_handovers, seed=42 + rep,
            )
            if metrics:
                metrics["repetition"] = rep + 1
                results["results"].append(metrics)
                # Checkpoint
                save_results(results, "performance", "scaling_cases")

    return results


def run_handover_comparison(log_a, log_b, config=None):
    """Compare with vs without handover optimization."""
    if config is None:
        config = load_config()
    repetitions = config["performance"]["repetitions"]
    threads = config["performance"]["threads"]

    results = {"experiment": "handover_comparison", "results": []}

    for use_ho in [False, True]:
        for rep in range(repetitions):
            label = "with_handovers" if use_ho else "without_handovers"
            logger.info(f"  {label}, rep={rep + 1}/{repetitions}")
            metrics = run_single_benchmark(
                log_a, log_b, threads=threads,
                use_handovers=use_ho, seed=42 + rep,
            )
            if metrics:
                metrics["use_handovers"] = use_ho
                metrics["repetition"] = rep + 1
                results["results"].append(metrics)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Performance benchmarks")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--experiment", choices=["scaling", "handover"], default="scaling")
    parser.add_argument("--use-handovers", action="store_true")
    args = parser.parse_args()

    if args.experiment == "scaling":
        results = run_scaling_benchmark(args.log_a, args.log_b, use_handovers=args.use_handovers)
    else:
        results = run_handover_comparison(args.log_a, args.log_b)

    save_results(results, "performance", args.experiment)
