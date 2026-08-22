"""Compute the effective two-party input statistics used in Chapter 6.

The experiment importer discards XES traces with no party-local event.  The
statistics therefore use ``import_xes.parse_xes`` instead of counting raw
``<trace>`` elements, so ``N_per_party`` matches the encoded matrix cap.

Usage:
    python3 eval/figures/dataset_stats.py
"""

from __future__ import annotations

import csv
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from pipeline import import_xes  # noqa: E402
from eval.registry import N2_DATASETS  # noqa: E402


OUTPUT = os.path.join(ROOT, "eval_results", "dataset_stats.csv")
DATASETS = (
    "bpi13_incidents",
    "bpi13_open",
    "bpi13_closed",
    "bpi12",
    "bpi17_offer",
    "sepsis",
    "hospital",
    "requestforpayment",
    "domestic_decl",
    "international_decl",
    "permit",
)
FIELDS = (
    "dataset",
    "cases_A",
    "cases_B",
    "cases_union",
    "max_trace_A",
    "max_trace_B",
    "partial_len",
    "events",
    "activities",
    "N_per_party",
)


def _stats(dataset: str) -> dict[str, int | str]:
    cases_a = import_xes.parse_xes(N2_DATASETS[dataset][0])
    cases_b = import_xes.parse_xes(N2_DATASETS[dataset][1])
    ids_a = {case["id"] for case in cases_a}
    ids_b = {case["id"] for case in cases_b}
    max_a = max((len(case["events"]) for case in cases_a), default=0)
    max_b = max((len(case["events"]) for case in cases_b), default=0)
    activities = {
        activity
        for case in cases_a + cases_b
        for _, activity in case["events"]
    }
    return {
        "dataset": dataset,
        "cases_A": len(cases_a),
        "cases_B": len(cases_b),
        "cases_union": len(ids_a | ids_b),
        "max_trace_A": max_a,
        "max_trace_B": max_b,
        "partial_len": max(max_a, max_b),
        "events": sum(len(case["events"]) for case in cases_a + cases_b),
        "activities": len(activities),
        "N_per_party": max(len(cases_a), len(cases_b)),
    }


def main() -> None:
    rows = [_stats(dataset) for dataset in DATASETS]
    with open(OUTPUT, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUTPUT}")
    for row in rows:
        print(
            row["dataset"],
            f"cases={row['cases_A']}/{row['cases_B']}",
            f"partial_len={row['partial_len']}",
            f"events={row['events']}",
            f"activities={row['activities']}",
        )


if __name__ == "__main__":
    main()
