"""
Differential Privacy evaluation for privacy-preserving process mining.

Three evaluation modes:
  1. Correctness: verify MPC DP output is structurally valid
  2. Statistical: verify noise distribution matches the truncated k-TSGD
  3. Performance: measure DP overhead (compile time, runtime, communication)
"""

import sys
import os
import argparse
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ProgramFiles.dp_calibration import compute_dp_k
from eval.correctness import run_mpc_pipeline
from eval.baseline import compute_baseline
from eval.performance import run_single_benchmark, _parse_metrics
from eval.privacy_utility import simulate_dp_noise, compute_emd, _to_distribution
from eval.utils import save_results, load_config, setup_logging
import import_xes

logger = setup_logging("dp_evaluation")


def _normalize_trace(trace):
    return tuple(tuple(sorted(step)) if len(step) > 1 else (step[0],) for step in trace)


# ---------------------------------------------------------------------------
# 1. Correctness
# ---------------------------------------------------------------------------

def validate_dp_output(mpc_parsed, baseline_variants, epsilon, dp_delta=0.01):
    """
    Check that MPC DP output is structurally valid.
    DP changes counts, not traces — so all revealed traces must exist in baseline.
    """
    issues = []
    expected_k = compute_dp_k(epsilon, dp_delta)

    # Check DP metadata
    dp_meta = mpc_parsed.get("benchmarks", {}).get("Differential Privacy")
    if dp_meta is None:
        issues.append("DP_APPLIED metadata not found in output")
    else:
        # Parse "epsilon=X, k=Y" format
        import re
        eps_match = re.search(r"epsilon=([\d.]+)", dp_meta)
        k_match = re.search(r"k=(\d+)", dp_meta)
        if eps_match:
            reported_eps = float(eps_match.group(1))
            if abs(reported_eps - epsilon) > 0.01:
                issues.append(f"Epsilon mismatch: expected {epsilon}, got {reported_eps}")
        if k_match:
            reported_k = int(k_match.group(1))
            if reported_k != expected_k:
                issues.append(f"K mismatch: expected {expected_k}, got {reported_k}")

    # Build baseline trace set
    baseline_traces = set()
    for v in baseline_variants:
        baseline_traces.add(_normalize_trace(v["trace"]))

    mpc_traces = []
    for r in mpc_parsed.get("results", []):
        if r["type"] != "trace":
            continue
        count = r["count"]
        trace_key = _normalize_trace(r["trace"])
        mpc_traces.append({"trace": trace_key, "count": count})

        if count < 0:
            issues.append(f"Negative count {count} for trace {trace_key}")

        if trace_key not in baseline_traces:
            issues.append(f"Trace not in baseline: {trace_key}")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "mpc_variant_count": len(mpc_traces),
        "baseline_variant_count": len(baseline_variants),
        "dp_metadata": dp_meta,
    }


def run_dp_correctness_test(log_a, log_b, epsilon=1.0, dp_delta=0.01, threads=16):
    """Single MPC run with DP, validate output."""
    k = compute_dp_k(epsilon, dp_delta)
    logger.info(f"DP Correctness Test: epsilon={epsilon}, delta={dp_delta}, k={k}")

    # Baseline: no DP, threshold=0 to get all variants
    cases_a = import_xes.parse_xes(log_a)
    cases_b = import_xes.parse_xes(log_b)
    baseline = compute_baseline(cases_a, cases_b, threshold=0)
    logger.info(f"Baseline: {len(baseline['variants'])} variants (threshold=0)")

    # MPC with DP (run_process_mining.py overrides the threshold to k + 1,
    # realizing the strict release rule "noisy count > k")
    logger.info(f"Running MPC with DP (epsilon={epsilon}, delta={dp_delta}, k={k})...")
    mpc_parsed = run_mpc_pipeline(log_a, log_b, threshold=k, threads=threads,
                                  enable_dp=1, epsilon=epsilon, dp_delta=dp_delta)
    if mpc_parsed is None:
        return {"valid": False, "error": "MPC pipeline failed"}

    validation = validate_dp_output(mpc_parsed, baseline["variants"], epsilon, dp_delta)

    if validation["valid"]:
        logger.info("PASS: DP output is structurally valid.")
    else:
        logger.warning(f"FAIL: {len(validation['issues'])} issues found.")
        for issue in validation["issues"]:
            logger.warning(f"  - {issue}")

    return {
        "test": "dp_correctness",
        "epsilon": epsilon,
        "dp_delta": dp_delta,
        "k": k,
        "validation": validation,
    }


# ---------------------------------------------------------------------------
# 2. Statistical Validation
# ---------------------------------------------------------------------------

def run_dp_statistical_test(log_a, log_b, epsilon=1.0, dp_delta=0.01, n_runs=10,
                            threads=16, **kwargs):
    """
    Run MPC with DP multiple times, collect noisy counts, compare against
    theoretical k-TSGD (truncated symmetric geometric) distribution.

    Note: MPC subprocess re-reads original log files, so baseline must be
    computed on full data to match.
    """
    k = compute_dp_k(epsilon, dp_delta)
    logger.info(f"DP Statistical Test: epsilon={epsilon}, delta={dp_delta}, k={k}, n_runs={n_runs}")

    # Baseline on full data (MPC subprocess re-reads original logs)
    cases_a = import_xes.parse_xes(log_a)
    cases_b = import_xes.parse_xes(log_b)

    baseline = compute_baseline(cases_a, cases_b, threshold=0)
    baseline_map = {}
    for v in baseline["variants"]:
        key = _normalize_trace(v["trace"])
        baseline_map[key] = v["count"]

    logger.info(f"Baseline: {len(baseline_map)} variants")

    # Run MPC with DP multiple times
    all_noise = []
    per_run_results = []

    for run_idx in range(n_runs):
        logger.info(f"  Run {run_idx + 1}/{n_runs}...")
        mpc_parsed = run_mpc_pipeline(log_a, log_b, threshold=k, threads=threads,
                                      enable_dp=1, epsilon=epsilon, dp_delta=dp_delta)
        if mpc_parsed is None:
            logger.error(f"  Run {run_idx + 1} failed, skipping")
            continue

        # Collect counts per variant
        mpc_map = {}
        for r in mpc_parsed.get("results", []):
            if r["type"] != "trace":
                continue
            key = _normalize_trace(r["trace"])
            mpc_map[key] = r["count"]

        # Compute noise only for variants with base_count >= 2k+1: those are
        # released for EVERY noise draw in [-k, k] (base - k > k), so the
        # collected samples are uncensored by the release threshold and
        # unaffected by the clamp at 0.
        run_noise = []
        for key, base_count in baseline_map.items():
            if base_count < 2 * k + 1:
                continue
            mpc_count = mpc_map.get(key, 0)
            noise = mpc_count - base_count
            run_noise.append(noise)
            all_noise.append(noise)

        per_run_results.append({
            "run": run_idx + 1,
            "mpc_variants": len(mpc_map),
            "noise_samples": len(run_noise),
            "mean_noise": float(np.mean(run_noise)) if run_noise else None,
        })

    if not all_noise:
        return {"test": "dp_statistical", "error": "No noise samples collected"}

    noise_arr = np.array(all_noise)

    # Theoretical k-TSGD(p, k) properties: pmf(x) = q^|x| / Z on [-k, k],
    # q = e^(-epsilon), Z = (1 + q - 2 q^(k+1)) / (1 - q). This is the
    # TRUNCATED distribution the sampler implements (Desfontaines Def. 6) —
    # the untruncated discrete Laplace has strictly larger variance and a
    # KS test against it cannot certify the truncation boundary.
    q = math.exp(-epsilon)
    Z = (1.0 + q - 2.0 * q ** (k + 1)) / (1.0 - q)
    ktsgd_pmf = {x: q ** abs(x) / Z for x in range(-k, k + 1)}
    theoretical_variance = sum(x * x * m for x, m in ktsgd_pmf.items())

    observed_mean = float(np.mean(noise_arr))
    observed_var = float(np.var(noise_arr, ddof=1))

    # Chi-square goodness of fit over the 2k+1 support bins. A KS test is
    # invalid here: the noise is integer-valued with heavy ties at 0, and
    # scipy's kstest assumes a continuous CDF, which manufactures a large
    # spurious D-statistic at every tie. Chi-square handles discrete bins
    # natively. Tail bins with expected count < 5 are merged inward
    # (standard validity rule; the pmf is unimodal, so only tails merge).
    from scipy.stats import chisquare

    n_samples = len(noise_arr)
    obs = [int(np.sum(noise_arr == x)) for x in range(-k, k + 1)]
    exp = [ktsgd_pmf[x] * n_samples for x in range(-k, k + 1)]

    while len(exp) > 1 and exp[0] < 5.0:
        exp[1] += exp[0]; obs[1] += obs[0]
        del exp[0]; del obs[0]
    while len(exp) > 1 and exp[-1] < 5.0:
        exp[-2] += exp[-1]; obs[-2] += obs[-1]
        del exp[-1]; del obs[-1]

    obs = np.array(obs, dtype=float)
    exp = np.array(exp, dtype=float)
    exp *= obs.sum() / exp.sum()  # remove floating-point drift

    gof_stat, gof_pvalue = chisquare(obs, f_exp=exp)

    result = {
        "test": "dp_statistical",
        "epsilon": epsilon,
        "dp_delta": dp_delta,
        "k": k,
        "noise_distribution": "k-TSGD (truncated symmetric geometric)",
        "n_runs": n_runs,
        "n_cases": "full",
        "total_noise_samples": len(all_noise),
        "observed_mean": observed_mean,
        "expected_mean": 0.0,
        "observed_variance": observed_var,
        "theoretical_variance": theoretical_variance,
        "variance_ratio": observed_var / theoretical_variance if theoretical_variance > 0 else None,
        "gof_test": "chi-square over k-TSGD support bins (min expected 5)",
        "gof_statistic": float(gof_stat),
        "gof_pvalue": float(gof_pvalue),
        "gof_pass": gof_pvalue > 0.01,
        "noise_values": all_noise,
        "per_run": per_run_results,
    }

    logger.info(f"  Noise samples: {len(all_noise)}")
    logger.info(f"  Mean noise: {observed_mean:.3f} (expected: 0.0)")
    logger.info(f"  Variance: {observed_var:.3f} (theoretical: {theoretical_variance:.3f})")
    logger.info(f"  Chi-square GoF: stat={gof_stat:.4f}, p={gof_pvalue:.4f} ({'PASS' if gof_pvalue > 0.01 else 'FAIL'})")

    return result


# ---------------------------------------------------------------------------
# 3. Performance
# ---------------------------------------------------------------------------

def run_dp_performance_test(log_a, log_b, epsilons=None, dp_delta=0.01, threads=16):
    """Compare compile time, runtime, and communication with DP on vs off."""
    if epsilons is None:
        epsilons = [0.1, 0.5, 1.0, 2.0, 5.0]

    logger.info(f"DP Performance Test: epsilons={epsilons}, delta={dp_delta}")
    results = []

    # Baseline: no DP
    logger.info("Running baseline (no DP)...")
    metrics = run_single_benchmark(log_a, log_b, threshold=1, threads=threads,
                                   enable_dp=0)
    if metrics:
        metrics["enable_dp"] = 0
        metrics["epsilon"] = None
        metrics["dp_delta"] = None
        results.append(metrics)
        logger.info(f"  Baseline: compile={metrics.get('compile_time_s', '?')}s, "
                     f"runtime={metrics.get('total_runtime_s', '?')}s, "
                     f"data={metrics.get('global_data_sent_mb', '?')}MB")

    # DP at each epsilon (threshold = k, derived from epsilon and delta)
    for eps in epsilons:
        k = compute_dp_k(eps, dp_delta)
        logger.info(f"Running DP epsilon={eps}, delta={dp_delta} (k={k})...")
        metrics = run_single_benchmark(log_a, log_b, threshold=k, threads=threads,
                                       enable_dp=1, epsilon=eps, dp_delta=dp_delta)
        if metrics:
            metrics["enable_dp"] = 1
            metrics["epsilon"] = eps
            metrics["dp_delta"] = dp_delta
            metrics["k"] = k
            results.append(metrics)
            logger.info(f"  eps={eps}, k={k}: compile={metrics.get('compile_time_s', '?')}s, "
                         f"runtime={metrics.get('total_runtime_s', '?')}s, "
                         f"data={metrics.get('global_data_sent_mb', '?')}MB")

    return {
        "test": "dp_performance",
        "dp_delta": dp_delta,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------

def run_full_dp_evaluation(log_a, log_b, config=None):
    """Run all three DP evaluation modes."""
    if config is None:
        config = load_config()

    dp_config = config.get("dp", {})
    epsilons = dp_config.get("epsilons", [0.1, 0.5, 1.0, 2.0, 5.0])
    dp_delta = dp_config.get("deltas", [0.01])[0]  # use first delta for correctness/statistical
    n_runs = dp_config.get("n_runs_statistical", 10)

    results = {}

    logger.info("=" * 60)
    logger.info("Phase 1: Correctness")
    logger.info("=" * 60)
    results["correctness"] = run_dp_correctness_test(log_a, log_b, epsilon=1.0, dp_delta=dp_delta)
    save_results(results, "dp", "dp_evaluation")

    logger.info("=" * 60)
    logger.info("Phase 2: Statistical Validation")
    logger.info("=" * 60)
    results["statistical"] = run_dp_statistical_test(log_a, log_b, epsilon=1.0,
                                                     dp_delta=dp_delta,
                                                     n_runs=n_runs)
    save_results(results, "dp", "dp_evaluation")

    logger.info("=" * 60)
    logger.info("Phase 3: Performance")
    logger.info("=" * 60)
    results["performance"] = run_dp_performance_test(log_a, log_b, epsilons=epsilons,
                                                     dp_delta=dp_delta)
    save_results(results, "dp", "dp_evaluation")

    logger.info("=" * 60)
    logger.info("DP Evaluation Complete")
    logger.info("=" * 60)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Differential Privacy evaluation")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--test", choices=["correctness", "statistical", "performance", "all"],
                        default="all")
    parser.add_argument("--epsilon", type=float, default=1.0)
    parser.add_argument("--dp-delta", type=float, default=0.01,
                        help="DP delta for (eps,delta)-DP partition selection (default: 0.01)")
    parser.add_argument("--n-runs", type=int, default=10)
    parser.add_argument("--threads", type=int, default=16)
    args = parser.parse_args()

    if args.test == "correctness":
        result = run_dp_correctness_test(args.log_a, args.log_b,
                                         epsilon=args.epsilon, dp_delta=args.dp_delta,
                                         threads=args.threads)
        save_results(result, "dp", "dp_correctness")
    elif args.test == "statistical":
        result = run_dp_statistical_test(args.log_a, args.log_b,
                                         epsilon=args.epsilon, dp_delta=args.dp_delta,
                                         n_runs=args.n_runs,
                                         threads=args.threads)
        save_results(result, "dp", "dp_statistical")
    elif args.test == "performance":
        result = run_dp_performance_test(args.log_a, args.log_b,
                                         dp_delta=args.dp_delta, threads=args.threads)
        save_results(result, "dp", "dp_performance")
    else:
        result = run_full_dp_evaluation(args.log_a, args.log_b)
