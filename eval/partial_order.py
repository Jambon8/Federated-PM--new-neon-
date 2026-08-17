"""
Partial order evaluation: determinism, variant comparison, concurrency prevalence.
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eval.baseline import compute_baseline
from eval.correctness import run_determinism_test
from eval.utils import save_results, setup_logging
import import_xes

logger = setup_logging("partial_order")


def analyze_concurrent_events(log_a, log_b, use_handovers=False):
    """Count cases with same-timestamp events (concurrency prevalence)."""
    cases_a = import_xes.parse_xes(log_a, use_handovers=use_handovers)
    cases_b = import_xes.parse_xes(log_b, use_handovers=use_handovers)

    def count_concurrent(cases):
        total = len(cases)
        concurrent = 0
        total_ties = 0
        for c in cases:
            timestamps = [t for t, _ in c["events"]]
            if len(timestamps) != len(set(timestamps)):
                concurrent += 1
                total_ties += len(timestamps) - len(set(timestamps))
        return {"total_cases": total, "cases_with_ties": concurrent, "total_tie_events": total_ties}

    stats_a = count_concurrent(cases_a)
    stats_b = count_concurrent(cases_b)

    logger.info(f"Party A: {stats_a['cases_with_ties']}/{stats_a['total_cases']} cases have timestamp ties")
    logger.info(f"Party B: {stats_b['cases_with_ties']}/{stats_b['total_cases']} cases have timestamp ties")

    return {"party_a": stats_a, "party_b": stats_b}


def compare_variant_modes(log_a, log_b, threshold=1, use_handovers=False):
    """Compare standard vs partial-order variant detection."""
    cases_a = import_xes.parse_xes(log_a, use_handovers=use_handovers)
    cases_b = import_xes.parse_xes(log_b, use_handovers=use_handovers)

    logger.info("Computing standard (total order) baseline...")
    standard = compute_baseline(cases_a, cases_b, threshold=threshold, enable_partial_orders=0)

    logger.info("Computing partial order baseline...")
    partial = compute_baseline(cases_a, cases_b, threshold=threshold, enable_partial_orders=1)

    result = {
        "standard_variants": len(standard["variants"]),
        "partial_order_variants": len(partial["variants"]),
        "standard_total_cases": sum(v["count"] for v in standard["variants"]),
        "partial_order_total_cases": sum(v["count"] for v in partial["variants"]),
    }

    logger.info(f"Standard: {result['standard_variants']} variants, "
                f"PO: {result['partial_order_variants']} variants")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Partial order evaluation")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--threshold", type=int, default=1)
    parser.add_argument("--use-handovers", action="store_true")
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--test", choices=["concurrency", "compare", "determinism", "all"], default="all")
    args = parser.parse_args()

    results = {}

    if args.test in ("concurrency", "all"):
        results["concurrency"] = analyze_concurrent_events(
            args.log_a, args.log_b, use_handovers=args.use_handovers)

    if args.test in ("compare", "all"):
        results["variant_comparison"] = compare_variant_modes(
            args.log_a, args.log_b, threshold=args.threshold,
            use_handovers=args.use_handovers)

    if args.test in ("determinism", "all"):
        results["determinism"] = run_determinism_test(
            args.log_a, args.log_b, runs=3,
            threshold=args.threshold, partial_orders=1,
            threads=args.threads)

    save_results(results, "partial_order", "partial_order")
