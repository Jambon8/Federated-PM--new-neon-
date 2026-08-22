"""
Privacy-utility tradeoff evaluation.
Metrics: EMD, fitness, precision (via pm4py).
"""

import sys
import os
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eval.baseline import compute_baseline
from eval.utils import save_results, load_config, setup_logging
from pipeline.dp_calibration import compute_dp_k
from pipeline import import_xes

logger = setup_logging("privacy_utility")


def compute_emd(original_dist, filtered_dist):
    """
    Earth Mover's Distance between two variant frequency distributions.
    Both inputs are dicts: {normalized_trace_key: count}.
    """
    from scipy.stats import wasserstein_distance

    all_keys = sorted(set(original_dist.keys()) | set(filtered_dist.keys()))
    if not all_keys:
        return 0.0

    key_to_idx = {k: i for i, k in enumerate(all_keys)}

    orig_vals = np.zeros(len(all_keys))
    filt_vals = np.zeros(len(all_keys))

    for k, v in original_dist.items():
        orig_vals[key_to_idx[k]] = v
    for k, v in filtered_dist.items():
        filt_vals[key_to_idx[k]] = v

    # Normalize to probability distributions
    orig_sum = orig_vals.sum()
    filt_sum = filt_vals.sum()
    if orig_sum == 0 or filt_sum == 0:
        return float("inf")

    orig_vals /= orig_sum
    filt_vals /= filt_sum

    return wasserstein_distance(range(len(all_keys)), range(len(all_keys)), orig_vals, filt_vals)


def _normalize_trace(trace):
    return tuple(tuple(sorted(step)) if len(step) > 1 else (step[0],) for step in trace)


def _to_distribution(variants):
    dist = {}
    for v in variants:
        key = _normalize_trace(v["trace"])
        dist[key] = v["count"]
    return dist


def _sample_ktsgd(k, q, rng):
    """One exact k-TSGD(q, k) draw via inverse-CDF over a uniform variate.

    Mirrors the MPC sampler (mpc/process_mining.mpc, Step 5.5): the
    marginal is pmf(x) = q^|x| / Z on x in [-k, k] with
    Z = (1 + q - 2 q^(k+1)) / (1 - q), q = e^{-epsilon}. The MPC compares one
    secret 32-bit uniform draw against 2k public CDF thresholds; here a
    continuous uniform draw indexes the same CDF, which is the MPC marginal up
    to its 2^-32 grid (negligible).
    """
    Z = (1.0 + q - 2.0 * q ** (k + 1)) / (1.0 - q)
    r = rng.random()
    acc = 0.0
    for x in range(-k, k + 1):
        acc += (q ** abs(x)) / Z
        if r < acc:
            return x
    return k


def simulate_dp_noise(variants, epsilon, dp_delta=None, seed=42):
    """Add partition-selection noise to variant counts and apply the release
    rule, mirroring the MPC DP stage (mpc/process_mining.mpc, Step 5.5).

    For (epsilon, delta)-DP: k = compute_dp_k(epsilon, delta); each count gets
    one exact k-TSGD(q, k) draw (q = e^{-epsilon}); the noisy count is clamped
    at 0; a variant is RELEASED iff its noisy count > k. The strict "> k" rule
    (Desfontaines et al., Thm. 6) is what the MPC driver realizes by overriding
    THRESHOLD = k + 1 (pipeline/run.py).

    When dp_delta is None, falls back to an untruncated two-sided geometric
    (discrete Laplace) with release count > 0 — a legacy epsilon-DP path not
    used in the thesis grid and with no MPC counterpart.
    """
    rng = np.random.default_rng(seed)
    q = np.exp(-epsilon)  # geometric ratio q = e^{-epsilon} = 1 - p

    k = compute_dp_k(epsilon, dp_delta) if dp_delta is not None else None

    noised = []
    for v in variants:
        if k is not None:
            noise = _sample_ktsgd(k, q, rng)
        else:
            # Untruncated two-sided geometric (discrete Laplace).
            g1 = rng.geometric(1 - q) - 1  # numpy geometric is 1-based
            g2 = rng.geometric(1 - q) - 1
            noise = g1 - g2
        new_count = max(0, v["count"] + noise)
        released = new_count > k if k is not None else new_count > 0
        if released:
            noised.append({"count": int(new_count), "trace": v["trace"]})
    return noised


def compute_process_model_quality(variants, original_cases_a, original_cases_b):
    """
    Discover process model from variants, measure fitness and precision
    against original combined log using pm4py.
    """
    try:
        import pm4py
        from pm4py.objects.log.obj import EventLog, Trace, Event
    except ImportError:
        logger.warning("pm4py not installed, skipping process model quality metrics")
        return {"fitness": None, "precision": None, "error": "pm4py not installed"}

    # Build event log from variants
    variant_log = EventLog()
    for v in variants:
        for _ in range(v["count"]):
            trace = Trace()
            for step in v["trace"]:
                for act_name in step:
                    if act_name.startswith("<"):
                        continue
                    event = Event()
                    event["concept:name"] = act_name
                    trace.append(event)
            variant_log.append(trace)

    if len(variant_log) == 0:
        return {"fitness": 0.0, "precision": 0.0}

    # Build original combined log
    original_log = EventLog()
    for c in original_cases_a + original_cases_b:
        trace = Trace()
        for _, act_name in c["events"]:
            event = Event()
            event["concept:name"] = act_name
            trace.append(event)
        original_log.append(trace)

    try:
        # Discover model from variant log (pm4py 2.7 top-level API).
        net, im, fm = pm4py.discover_petri_net_inductive(variant_log)

        # Token-replay fitness and precision against the original log.
        fit_res = pm4py.fitness_token_based_replay(original_log, net, im, fm)
        avg_fitness = fit_res.get("average_trace_fitness")
        prec = pm4py.precision_token_based_replay(original_log, net, im, fm)

        return {
            "fitness": round(avg_fitness, 4) if avg_fitness is not None else None,
            "precision": round(prec, 4) if prec is not None else None,
        }
    except Exception as e:
        logger.error(f"pm4py analysis failed: {e}")
        return {"fitness": None, "precision": None, "error": str(e)}


def evaluate_privacy_utility(log_a, log_b, config=None, use_handovers=False):
    """Run full privacy-utility evaluation grid."""
    if config is None:
        config = load_config()

    cases_a = import_xes.parse_xes(log_a, use_handovers=use_handovers)
    cases_b = import_xes.parse_xes(log_b, use_handovers=use_handovers)

    thresholds = config["privacy_utility"]["thresholds"]
    epsilons = config["privacy_utility"]["epsilons"]
    deltas = config["privacy_utility"].get("deltas", [0.01])
    dp_reps = config["privacy_utility"]["dp_repetitions"]

    # Ground truth: no filtering
    logger.info("Computing ground truth (threshold=0, no DP)...")
    ground_truth = compute_baseline(cases_a, cases_b, threshold=0)
    gt_dist = _to_distribution(ground_truth["variants"])

    results = {"ground_truth_variants": len(ground_truth["variants"]), "evaluations": []}

    # Threshold-only evaluation
    for t in thresholds:
        logger.info(f"Evaluating threshold={t}...")
        filtered = compute_baseline(cases_a, cases_b, threshold=t)
        filt_dist = _to_distribution(filtered["variants"])
        emd = compute_emd(gt_dist, filt_dist)
        quality = compute_process_model_quality(filtered["variants"], cases_a, cases_b)

        results["evaluations"].append({
            "threshold": t, "epsilon": None, "dp_delta": None,
            "variant_count": len(filtered["variants"]),
            "emd": emd,
            "fitness": quality.get("fitness"),
            "precision": quality.get("precision"),
        })

    # DP evaluation with (epsilon, delta) pairs — k is derived, serves as threshold
    base = compute_baseline(cases_a, cases_b, threshold=0)
    for eps in epsilons:
        for delta in deltas:
            k = compute_dp_k(eps, delta)
            logger.info(f"Evaluating epsilon={eps}, delta={delta} (k={k})...")
            emd_values = []
            variant_counts = []

            for rep in range(dp_reps):
                noised = simulate_dp_noise(base["variants"], eps, dp_delta=delta, seed=42 + rep)
                noised_dist = _to_distribution(noised)
                emd_values.append(compute_emd(gt_dist, noised_dist))
                variant_counts.append(len(noised))

            results["evaluations"].append({
                "epsilon": eps, "dp_delta": delta, "k": k,
                "variant_count_mean": float(np.mean(variant_counts)),
                "variant_count_std": float(np.std(variant_counts)),
                "emd_mean": float(np.mean(emd_values)),
                "emd_std": float(np.std(emd_values)),
            })

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Privacy-utility evaluation")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--use-handovers", action="store_true")
    args = parser.parse_args()

    results = evaluate_privacy_utility(args.log_a, args.log_b, use_handovers=args.use_handovers)
    save_results(results, "privacy", "privacy_utility")
