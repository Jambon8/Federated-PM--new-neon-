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

from thesis_experiments import N2_DATASETS, N_WAY_DATASETS


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval_results" / "ch6_provenance_manifest.json"
RESULT_DIRS = (
    "e1_correctness",
    "e2_performance",
    "e3_scaling_input",
    "e4_scaling_n",
    "e5_handovers",
    "e6_partial_orders",
    "e7_network",
    "e8_dp",
    "e8b_dp_delta",
    "e9_kanonymity",
    "e10_protocols",
)
AUDIT_FILES = (
    "eval/verify_e1_independent.py",
    "eval/verify_e4_splits.py",
    "eval/verify_e10_outputs.py",
    "eval_results/e1_independent_correctness.json",
    "eval_results/e4_split_stats.json",
    "eval_results/e10_output_equivalence.json",
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
    input_paths = {Path(path) for pair in N2_DATASETS.values() for path in pair}
    input_paths.update(
        Path(path)
        for datasets in N_WAY_DATASETS.values()
        for paths in datasets.values()
        for path in paths
    )
    raw_results = {
        path
        for directory in RESULT_DIRS
        for path in (ROOT / "eval_results" / directory).glob("*.json")
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
