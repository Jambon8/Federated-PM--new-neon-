"""Write a hash manifest for the local evidence used by Chapter 6.

The manifest does not retroactively establish the source revision of historical
benchmark runs.  It fixes the exact input, raw-result, verifier, and audit-output
files inspected during the Chapter 6 evidence review.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis_experiments import all_runs
from eval.utils import run_files


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval_results" / "ch6_provenance_manifest.json"
# The reported experiments, by their result-directory names. The superseded
# input-scaling records are excluded: the controlled grid replaced them and they
# stay local.
RESULT_DIRS = (
    "correctness",
    "performance_default",
    "performance_backends",
    "scaling_input_size",
    "scaling_party_count",
    "scaling_party_count_controlled",
    "scaling_network_latency",
    "modes_handover",
    "modes_partial_order",
    "protection_k_anonymity",
    "protection_dp",
    "protection_dp_epsilon_delta",
)
AUDIT_FILES = (
    "eval/verify_e1_independent.py",
    "eval/verify_e4_splits.py",
    "eval/verify_e10_outputs.py",
    "eval_results/e1_independent_correctness.json",
    "eval_results/e4_split_stats.json",
    "eval_results/e10_output_equivalence.json",
    "eval/verify_e4b_outputs.py",
    "eval_results/e4b_output_equivalence.json",
    "eval/verify_dp_calibration.py",
    "eval_results/dp_calibration_verification.json",
)


def _entry(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    # Every log any registered run reads, so a layout change cannot silently
    # drop a dataset from the manifest.
    input_paths = {
        Path(path) for _rid, _args, _n, logs, _meta in all_runs() for path in logs
    }
    raw_results = {
        path
        for directory in RESULT_DIRS
        for path in (Path(p) for d in [directory]
                     for p in run_files(ROOT / "eval_results" / d))
    }
    audit_paths = {ROOT / path for path in AUDIT_FILES}
    manifest = {
        "scope": (
            "Snapshot of files inspected for the Chapter 6 evidence audit; "
            "not proof of the source revision used by historical runs."
        ),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "audit_git_revision": _git("rev-parse", "HEAD"),
        "audit_worktree_dirty": bool(_git("status", "--porcelain")),
        "inputs": [_entry(ROOT / path) for path in sorted(input_paths)],
        "raw_results": [_entry(path) for path in sorted(raw_results)],
        "audit_files": [_entry(path) for path in sorted(audit_paths)],
    }
    OUTPUT.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: "
        f"{len(input_paths)} inputs, {len(raw_results)} raw results, "
        f"{len(audit_paths)} audit files"
    )


if __name__ == "__main__":
    main()
