"""Verify the E4b multiparty releases against direct reconstruction.

For each dataset and party count, this audit joins the case identifiers shared
by every generated party log, unions each case's events, sorts them by the
public timestamp--activity order, and compares the resulting trace--count map
with every stored E4b protocol repetition.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.utils import find_run, load_run  # noqa: E402

from pipeline import import_xes


DATA = ROOT / "data"
C_CASES = 500
RESULTS = ROOT / "eval_results" / "scaling_party_count_controlled"
OUTPUT = ROOT / "eval_results" / "e4b_output_equivalence.json"
DATASETS = ("bpi13_incidents", "sepsis")
PARTY_COUNTS = (2, 3, 4, 5)
REPETITIONS = range(3)


def centralized_release(paths: list[Path]) -> Counter[tuple[int, ...]]:
    """Return the direct joint trace counts for cases shared by every party."""
    party_cases = []
    activities = set()
    for party, path in enumerate(paths):
        cases = import_xes.parse_xes(str(path), party_index=party)
        by_case = {case["id"]: case["events"] for case in cases}
        party_cases.append(by_case)
        activities.update(activity for events in by_case.values()
                          for _, activity in events)

    activity_id = {
        activity: index
        for index, activity in enumerate(sorted(activities), start=1)
    }
    shared_cases = set.intersection(*(set(cases) for cases in party_cases))
    release: Counter[tuple[int, ...]] = Counter()
    for case_id in shared_cases:
        events = [
            event
            for cases in party_cases
            for event in cases[case_id]
        ]
        trace = tuple(
            activity_id[activity]
            for _, activity in sorted(
                events,
                key=lambda event: (event[0], activity_id[event[1]]),
            )
        )
        release[trace] += 1
    return release


def stored_release(path: Path) -> Counter[tuple[int, ...]]:
    """Decode a stored Stage-6 release into its complete trace--count map."""
    record = load_run(path)
    release: Counter[tuple[int, ...]] = Counter()
    for variant in record["variants"]:
        trace = tuple(
            activity
            for activity, _marker in variant["raw"]
            if activity != 0
        )
        release[trace] += variant["count"]
    return release


def main() -> None:
    report = {
        "method": (
            "Direct reconstruction from every generated party log: intersect "
            "case identifiers, union each shared case's events, sort by "
            "(timestamp, public activity identifier), and compare the complete "
            "trace--count map with every stored E4b release."
        ),
        "datasets": {},
    }
    all_match = True

    for dataset in DATASETS:
        dataset_report = {}
        reference = None
        for n in PARTY_COUNTS:
            paths = [DATA / f"{n}parties" / f"e4b_{dataset}_c{C_CASES}" / f"party_{party}.xes"
                     for party in range(n)]
            expected = centralized_release(paths)
            if reference is None:
                reference = expected
            same_joint_release = expected == reference

            repetitions = []
            for repetition in REPETITIONS:
                result_path = Path(find_run(RESULTS, f"e4b__{dataset}__N{n}__rep{repetition}"))
                observed = stored_release(result_path)
                match = observed == expected
                all_match &= match
                repetitions.append({
                    "result": str(result_path.relative_to(ROOT)),
                    "match": match,
                    "stored_variants": len(observed),
                    "stored_cases": sum(observed.values()),
                })

            all_match &= same_joint_release
            dataset_report[str(n)] = {
                "centralized_variants": len(expected),
                "centralized_cases": sum(expected.values()),
                "same_joint_release_as_n2": same_joint_release,
                "repetitions": repetitions,
            }
        report["datasets"][dataset] = dataset_report

    report["all_match"] = all_match
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not all_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
