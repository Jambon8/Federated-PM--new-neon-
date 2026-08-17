"""Audit the synthetic E4 party splits and persist their case statistics."""

from __future__ import annotations

import json
import gzip
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import import_xes
from eval.thesis_experiments import N2_DATASETS, N_WAY_DATASETS


OUTPUT = ROOT / "eval_results" / "e4_split_stats.json"
OVERLAP = {
    "bpi13_open": 0.5,
    "bpi13_closed": 0.5,
    "bpi13_incidents": 0.5,
    "sepsis": 1.0,
}
SEED = 42


def _encoded_cases(path: str) -> list[dict]:
    return import_xes.parse_xes(path)


def _trace_keys(path: str) -> list[tuple[tuple[str, str, str], ...]]:
    """Return trace-level attribute keys, including traces with zero events."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as stream:
        root = ET.parse(stream).getroot()
    traces = root.findall(".//{http://www.xes-standard.org/}trace") or root.findall(".//trace")
    keys = []
    for trace in traces:
        attributes = []
        for attribute in trace:
            if attribute.tag.rsplit("}", 1)[-1] == "event":
                continue
            attributes.append((attribute.tag.rsplit("}", 1)[-1], attribute.get("key", ""), attribute.get("value", "")))
        keys.append(tuple(attributes))
    return keys


def main() -> None:
    report = {
        "generator": "generate_test_data.py",
        "seed": SEED,
        "assignment": (
            "Shuffle source-case indices; put floor(overlap * cases) cases in "
            "every party; assign the remainder round-robin. Shared-case events "
            "are distributed round-robin by source event index."
        ),
        "datasets": {},
    }
    all_trace_assignments_match = True
    for dataset, overlap in OVERLAP.items():
        source_keys = _trace_keys(N2_DATASETS[dataset][0])
        indices = list(range(len(source_keys)))
        random.Random(SEED).shuffle(indices)
        overlap_count = max(1, int(len(source_keys) * overlap))
        shared = set(indices[:overlap_count])
        remaining = indices[overlap_count:]

        dataset_report = {
            "source_traces": len(source_keys),
            "overlap_fraction": overlap,
            "party_counts": {},
        }
        for parties in (3, 4, 5):
            paths = N_WAY_DATASETS[dataset][parties]
            raw_sets = [set(_trace_keys(path)) for path in paths]
            encoded_cases = [_encoded_cases(path) for path in paths]
            encoded_sets = [{case["id"] for case in cases} for cases in encoded_cases]
            expected_indices = [set(shared) for _ in range(parties)]
            for position, index in enumerate(remaining):
                expected_indices[position % parties].add(index)
            expected_sets = [
                {source_keys[index] for index in party_indices}
                for party_indices in expected_indices
            ]
            assignment_matches = raw_sets == expected_sets
            all_trace_assignments_match &= assignment_matches
            union = set.union(*encoded_sets)
            intersection = set.intersection(*encoded_sets)
            dataset_report["party_counts"][str(parties)] = {
                "file_traces_per_party": [len(trace_keys) for trace_keys in raw_sets],
                "encoded_nonempty_cases_per_party": [len(case_ids) for case_ids in encoded_sets],
                "encoded_partial_len": max(
                    len(case["events"])
                    for cases in encoded_cases
                    for case in cases
                ),
                "effective_shared_cases": len(intersection),
                "effective_union_cases": len(union),
                "raw_trace_assignment_matches_seed_42_generator": assignment_matches,
            }
        report["datasets"][dataset] = dataset_report
    report["all_raw_trace_assignments_match"] = all_trace_assignments_match
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not all_trace_assignments_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
