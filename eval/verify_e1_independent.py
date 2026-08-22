"""Verify stored E1 MPC releases against a direct centralized reconstruction.

This audit intentionally does not reuse ``eval.baseline``: that module mirrors
the MPC pipeline.  Instead, it joins the two public party-local logs by their
shared case identifier, sorts the union of each shared case by the public
``(timestamp, activity)`` order, and counts the resulting traces.  It then
compares those trace--count pairs with the decoded variants stored in the E1
result JSON files.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import import_xes
from eval.thesis_experiments import N2_DATASETS
from eval.utils import find_run, load_run


RESULTS = ROOT / "eval_results" / "correctness"
OUTPUT = ROOT / "eval_results" / "e1_independent_correctness.json"
DATASETS = (
    "bpi13_incidents",
    "bpi13_open",
    "bpi13_closed",
    "sepsis",
    "requestforpayment",
    "bpi17_offer",
    "bpi12",
    "domestic_decl",
    "international_decl",
    "hospital",
    "permit",
)


def _centralized_release(log_a: str, log_b: str) -> Counter[tuple[int, ...]]:
    """Return direct joint-trace counts for shared cases at the default mode."""
    cases_a = import_xes.parse_xes(log_a)
    cases_b = import_xes.parse_xes(log_b)
    by_case_a = {case["id"]: case["events"] for case in cases_a}
    by_case_b = {case["id"]: case["events"] for case in cases_b}
    activities = sorted(
        {activity for events in by_case_a.values() for _, activity in events}
        | {activity for events in by_case_b.values() for _, activity in events}
    )
    activity_id = {activity: index for index, activity in enumerate(activities, start=1)}

    release: Counter[tuple[int, ...]] = Counter()
    for case_id in by_case_a.keys() & by_case_b.keys():
        events = by_case_a[case_id] + by_case_b[case_id]
        trace = tuple(activity_id[activity] for _, activity in sorted(
            events, key=lambda event: (event[0], activity_id[event[1]])
        ))
        release[trace] += 1
    return release


def _stored_release(path: Path) -> Counter[tuple[int, ...]]:
    """Decode the Stage-6 E1 output representation stored in ``path``."""
    record = load_run(path)
    release: Counter[tuple[int, ...]] = Counter()
    for variant in record["variants"]:
        trace = tuple(activity for activity, _marker in variant["raw"] if activity != 0)
        release[trace] += variant["count"]
    return release


def main() -> None:
    report = {
        "method": (
            "Direct centralized reconstruction from the two local XES logs: "
            "join shared case identifiers, sort each joint case by (timestamp, "
            "public activity identifier), and compare trace--count pairs with "
            "the stored E1 Stage-6 output."
        ),
        "datasets": {},
    }
    all_match = True
    for dataset in DATASETS:
        stored_path = Path(find_run(RESULTS, f"e1__{dataset}__default__rep0"))
        expected = _centralized_release(*N2_DATASETS[dataset])
        observed = _stored_release(stored_path)
        missing = expected - observed
        extra = observed - expected
        match = not missing and not extra
        all_match &= match
        report["datasets"][dataset] = {
            "stored_result": str(stored_path.relative_to(ROOT)),
            "centralized_variants": len(expected),
            "stored_variants": len(observed),
            "centralized_cases": sum(expected.values()),
            "stored_cases": sum(observed.values()),
            "match": match,
            "missing_variant_count": len(missing),
            "extra_variant_count": len(extra),
        }
    report["all_match"] = all_match
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not all_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
