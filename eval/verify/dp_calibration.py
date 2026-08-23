#!/usr/bin/env python3
"""Verify finite-grid DP calibration on the Chapter 6 E8/E8b grid.

The reported grid is checked cell by cell: the calibrated cutoff, the sampler's
threshold table against the binary64 table the stored runs used, the integer
mass table, and the total-variation error against its calibration bound. Two
further checks cover calibration behavior the reported grid never reaches -- the
fixed point that raises the cutoff once the grid reserve crosses a boundary, and
the rejection of a delta the finite grid cannot afford.
"""

import hashlib
import json
import math
import os
import sys
from decimal import Decimal, localcontext

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from pipeline.dp_calibration import (  # noqa: E402
    calibrate_dp,
    cdf_thresholds,
    grid_tv_bound,
    ideal_dp_k,
    implemented_mass_counts,
    implemented_tv_distance,
)


OUTPUT = os.path.join(ROOT, "eval_results", "dp_calibration_verification.json")
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


def build_cells():
    """One record per reported (epsilon, delta) cell."""
    rows = []
    for epsilon in EPSILONS:
        for delta in DELTAS:
            calibration = calibrate_dp(epsilon, delta)
            thresholds = cdf_thresholds(epsilon, calibration.k)
            masses = implemented_mass_counts(thresholds)
            legacy = _legacy_thresholds(epsilon, calibration.k)
            actual_tv = implemented_tv_distance(epsilon, calibration.k)
            exp_epsilon = epsilon.exp()
            actual_effective_delta_bound = (
                calibration.ideal_delta + (1 + exp_epsilon) * actual_tv
            )
            spent = calibration.ideal_delta + calibration.grid_delta_reserve
            rows.append({
                "epsilon": str(epsilon),
                "requested_delta": str(delta),
                "uncorrected_k": ideal_dp_k(epsilon, delta),
                "corrected_k": calibration.k,
                "ideal_delta": str(calibration.ideal_delta),
                "grid_tv_bound": str(calibration.grid_tv_bound),
                "implemented_tv_distance": str(actual_tv),
                "grid_delta_reserve": str(calibration.grid_delta_reserve),
                "delta_spent": str(spent),
                "actual_effective_delta_upper_bound": str(actual_effective_delta_bound),
                "mass_total": sum(masses),
                "threshold_count": len(thresholds),
                "threshold_table_sha256": _digest(thresholds),
                "matches_legacy_thresholds": thresholds == legacy,
                "spent_delta_within_request": spent <= delta,
                "grid_tv_bound_follows_k": calibration.grid_tv_bound == grid_tv_bound(calibration.k),
                "thresholds_strictly_monotone": all(a < b for a, b in zip(thresholds, thresholds[1:])),
                "threshold_count_is_twice_k": len(thresholds) == 2 * calibration.k,
                "mass_table_symmetric": masses == list(reversed(masses)),
                "mass_table_positive": all(mass > 0 for mass in masses),
            })
    return rows


def build_guards():
    """Calibration behavior at two points outside the reported grid."""
    epsilon, delta = Decimal("0.1"), Decimal("1e-6")
    calibration = calibrate_dp(epsilon, delta)
    uncorrected = ideal_dp_k(epsilon, delta)
    fixed_point = {
        "epsilon": str(epsilon),
        "requested_delta": str(delta),
        "uncorrected_k": uncorrected,
        "corrected_k": calibration.k,
        "reserve_raised_k": calibration.k > uncorrected,
        "recalibration_is_a_fixed_point":
            ideal_dp_k(epsilon, calibration.ideal_delta) == calibration.k,
    }

    epsilon, delta = Decimal("1"), Decimal("1e-20")
    rejection = {"epsilon": str(epsilon), "requested_delta": str(delta)}
    try:
        calibrate_dp(epsilon, delta)
    except ValueError as error:
        rejection["rejected"] = "too small" in str(error)
        rejection["message"] = str(error)
    else:
        rejection["rejected"] = False
    return {
        "grid_reserve_fixed_point": fixed_point,
        "delta_below_grid_reserve": rejection,
    }


def build_report():
    with localcontext() as ctx:
        ctx.prec = 90
        cells = build_cells()
        guards = build_guards()
    return {
        "method": "finite-grid k-TSGD calibration verification",
        "grid_bits": 32,
        "cells": cells,
        "guards": guards,
        "all_k_unchanged": all(row["uncorrected_k"] == row["corrected_k"] for row in cells),
        "all_threshold_tables_match_legacy": all(row["matches_legacy_thresholds"] for row in cells),
        "all_mass_totals_exact": all(row["mass_total"] == 2 ** 32 for row in cells),
        "all_actual_tv_within_bound": all(
            Decimal(row["implemented_tv_distance"]) <= Decimal(row["grid_tv_bound"])
            for row in cells
        ),
        "all_spent_deltas_within_request": all(row["spent_delta_within_request"] for row in cells),
        "all_grid_tv_bounds_follow_k": all(row["grid_tv_bound_follows_k"] for row in cells),
        "all_threshold_tables_strictly_monotone": all(
            row["thresholds_strictly_monotone"] and row["threshold_count_is_twice_k"]
            for row in cells
        ),
        "all_mass_tables_symmetric_and_positive": all(
            row["mass_table_symmetric"] and row["mass_table_positive"] for row in cells
        ),
        "grid_reserve_fixed_point_reached": (
            guards["grid_reserve_fixed_point"]["reserve_raised_k"]
            and guards["grid_reserve_fixed_point"]["recalibration_is_a_fixed_point"]
        ),
        "delta_below_grid_reserve_rejected": guards["delta_below_grid_reserve"]["rejected"],
    }


def main():
    report = build_report()
    checks = {key: value for key, value in report.items()
              if key.startswith("all_") or key in (
                  "grid_reserve_fixed_point_reached", "delta_below_grid_reserve_rejected")}
    report["all_checks_pass"] = all(checks.values())
    rendered = json.dumps(report, indent=2) + "\n"
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    print(rendered, end="")
    if not report["all_checks_pass"]:
        failed = sorted(key for key, value in checks.items() if not value)
        print("FAILED: " + ", ".join(failed), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
