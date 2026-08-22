#!/usr/bin/env python3
"""Verify finite-grid DP calibration on the Chapter 6 E8/E8b grid."""

import argparse
import hashlib
import json
import math
import os
import sys
from decimal import Decimal, localcontext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.dp_calibration import (  # noqa: E402
    calibrate_dp,
    cdf_thresholds,
    ideal_dp_k,
    implemented_mass_counts,
    implemented_tv_distance,
)


EPSILONS = (Decimal("0.1"), Decimal("0.5"), Decimal("1"), Decimal("2"))
DELTAS = (Decimal("1e-2"), Decimal("1e-3"), Decimal("1e-4"), Decimal("1e-5"))


def _legacy_thresholds(epsilon, k, grid_bits=32):
    """Reproduce the binary64 table used for the stored E8/E8b runs."""
    epsilon = float(epsilon)
    q = math.exp(-epsilon)
    z = (1.0 + q - 2.0 * q ** (k + 1)) / (1.0 - q)
    scale = 2 ** grid_bits
    acc = 0.0
    thresholds = []
    for x in range(-k, k):
        acc += q ** abs(x) / z
        threshold = int(round(acc * scale))
        thresholds.append(min(scale - 1, max(1, threshold)))
    return thresholds


def _digest(values):
    encoded = ",".join(map(str, values)).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_report():
    rows = []
    with localcontext() as ctx:
        ctx.prec = 90
        for epsilon in EPSILONS:
            for delta in DELTAS:
                calibration = calibrate_dp(epsilon, delta)
                thresholds = cdf_thresholds(epsilon, calibration.k)
                legacy = _legacy_thresholds(epsilon, calibration.k)
                actual_tv = implemented_tv_distance(epsilon, calibration.k)
                exp_epsilon = epsilon.exp()
                actual_effective_delta_bound = (
                    calibration.ideal_delta + (1 + exp_epsilon) * actual_tv
                )
                rows.append({
                    "epsilon": str(epsilon),
                    "requested_delta": str(delta),
                    "uncorrected_k": ideal_dp_k(epsilon, delta),
                    "corrected_k": calibration.k,
                    "ideal_delta": str(calibration.ideal_delta),
                    "grid_tv_bound": str(calibration.grid_tv_bound),
                    "implemented_tv_distance": str(actual_tv),
                    "grid_delta_reserve": str(calibration.grid_delta_reserve),
                    "actual_effective_delta_upper_bound": str(actual_effective_delta_bound),
                    "mass_total": sum(implemented_mass_counts(thresholds)),
                    "threshold_count": len(thresholds),
                    "threshold_table_sha256": _digest(thresholds),
                    "matches_legacy_thresholds": thresholds == legacy,
                })
    return {
        "method": "finite-grid k-TSGD calibration verification",
        "grid_bits": 32,
        "cells": rows,
        "all_k_unchanged": all(row["uncorrected_k"] == row["corrected_k"] for row in rows),
        "all_threshold_tables_match_legacy": all(row["matches_legacy_thresholds"] for row in rows),
        "all_mass_totals_exact": all(row["mass_total"] == 2 ** 32 for row in rows),
        "all_actual_tv_within_bound": all(
            Decimal(row["implemented_tv_distance"]) <= Decimal(row["grid_tv_bound"])
            for row in rows
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
