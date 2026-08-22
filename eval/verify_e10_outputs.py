"""Verify exact stored-output equality for the complete E10 protocol cohorts."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.utils import find_run, load_run  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval_results" / "performance_backends"
OUTPUT = ROOT / "eval_results" / "e10_output_equivalence.json"
DATASETS = ("bpi13_open", "bpi13_closed", "sepsis", "bpi13_incidents")
PROTOCOLS = ("semi", "rep_bin")


def _release(path: Path) -> Counter[tuple[tuple[int, int], ...]]:
    record = load_run(path)
    return Counter({
        tuple(tuple(pair) for pair in variant["raw"] if pair[0] != 0): variant["count"]
        for variant in record["variants"]
    })


def main() -> None:
    report = {"datasets": {}}
    all_match = True
    for dataset in DATASETS:
        releases = {}
        files = {}
        for protocol in PROTOCOLS:
            releases[protocol] = []
            files[protocol] = []
            for repetition in range(3):
                path = Path(find_run(RESULTS, f"e10__{dataset}__{protocol}__rep{repetition}"))
                releases[protocol].append(_release(path))
                files[protocol].append(str(path.relative_to(ROOT)))
        within_protocol = {
            protocol: all(release == releases[protocol][0] for release in releases[protocol][1:])
            for protocol in PROTOCOLS
        }
        between_protocols = releases["semi"][0] == releases["rep_bin"][0]
        match = all(within_protocol.values()) and between_protocols
        all_match &= match
        report["datasets"][dataset] = {
            "files": files,
            "variants": len(releases["semi"][0]),
            "within_protocol_repetitions_match": within_protocol,
            "semi_equals_rep_bin": between_protocols,
            "match": match,
        }
    report["all_match"] = all_match
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not all_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
