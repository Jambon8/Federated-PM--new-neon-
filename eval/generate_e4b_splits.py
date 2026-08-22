"""Generate the controlled E4b party-count inputs (see session log 2026-07-10).

E4b evaluates direct multiparty scaling: every primary cell holds the same C
joint cases, the same 100% overlap, and the same pinned per-party row width as
n and the encoded row count n*C increase.

Construction per dataset:
  1. Build the joint log by merging the two prepared partial logs per case ID
     (union of events, sorted by public (timestamp, activity) order).
  2. Filter to cases with MIN_LEN <= |sigma| <= MAX_LEN events. The lower bound
     guarantees every party receives at least one event of every case at
     n <= N_MAX (100% effective overlap, no discarded local traces); the upper
     bound caps the n=2 local trace length at MAX_LEN/2 = P.
  3. Sample C cases (seed 42) once; the same cases feed every n.
  4. Deal each case's events round-robin: joint event j -> party j mod n.
     Write party XES files for n in {2, 3, 4, 5}.
  5. Supplementary size-matched two-party controls: duplicate sampled cases
     (fresh case IDs) until each party holds m = n*C/2 cases for n in
     {3, 4, 5}, dealt to two parties. These stored diagnostic cells match the
     primary cell's total row count and per-party width.

Runs pass --force-partial-len 20 so the encoded width is pinned identically in
every cell (no truncation occurs: the filter caps local traces at 20 events).

Usage:
    python3 eval/generate_e4b_splits.py            # writes data/<n>parties/e4b_*/
"""

import json
import os
import random
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from pipeline import import_xes  # noqa: E402

SEED = 42
C_CASES = 500
MIN_LEN = 5     # >= n_max, so round-robin dealing starves no party
MAX_LEN = 40    # caps the n=2 local trace length at MAX_LEN/2 = 20
PARTY_COUNTS = (2, 3, 4, 5)
CONTROL_NS = (3, 4, 5)
DATA_ROOT = os.environ.get("NEON_DATA_ROOT", "data")
# Cells are written as data/<n>parties/e4b_<dataset>_c<cases per party>/.
def cell_dir(dset, n, cases):
    return os.path.join(DATA_ROOT, f"{n}parties", f"e4b_{dset}_c{cases}")

DATASETS = {
    "sepsis": (f"{DATA_ROOT}/2parties/sepsis/party_0.xes.gz",
               f"{DATA_ROOT}/2parties/sepsis/party_1.xes.gz"),
    "bpi13_incidents": (f"{DATA_ROOT}/2parties/bpi13_incidents/party_0.xes.gz",
                        f"{DATA_ROOT}/2parties/bpi13_incidents/party_1.xes.gz"),
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _iso(ms):
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


def build_joint_cases(paths):
    """Merge the partial logs into joint cases: id -> [(t_ms, activity), ...]."""
    joint = {}
    for p, path in enumerate(paths):
        for case in import_xes.parse_xes(path, party_index=p):
            joint.setdefault(case["id"], []).extend(case["events"])
    for cid in joint:
        joint[cid].sort(key=lambda e: (e[0], e[1]))
    return joint


def write_party_xes(path, cases):
    """cases: list of (case_id, [(t_ms, activity), ...]) with at least one event each."""
    root = ET.Element("log", {"xes.version": "1.0", "xmlns": "http://www.xes-standard.org/"})
    for cid, events in cases:
        trace = ET.SubElement(root, "trace")
        ET.SubElement(trace, "string", {"key": "concept:name", "value": str(cid)})
        for t, act in events:
            ev = ET.SubElement(trace, "event")
            ET.SubElement(ev, "string", {"key": "concept:name", "value": str(act)})
            ET.SubElement(ev, "date", {"key": "time:timestamp", "value": _iso(t)})
    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tree.write(path, xml_declaration=True, encoding="unicode")


def deal(events, n, party):
    return [ev for j, ev in enumerate(events) if j % n == party]


def main():
    meta = {"seed": SEED, "c_cases": C_CASES, "trace_len_filter": [MIN_LEN, MAX_LEN],
            "pinned_partial_len": MAX_LEN // 2, "datasets": {}}
    for dset, paths in DATASETS.items():
        joint = build_joint_cases(paths)
        eligible = sorted(cid for cid, evs in joint.items() if MIN_LEN <= len(evs) <= MAX_LEN)
        if len(eligible) < C_CASES:
            raise SystemExit(f"{dset}: only {len(eligible)} eligible cases, need {C_CASES}")
        rng = random.Random(SEED)
        sample = sorted(rng.sample(eligible, C_CASES))
        lengths = [len(joint[cid]) for cid in sample]
        dmeta = {"joint_cases": len(joint), "eligible_cases": len(eligible),
                 "sampled_cases": C_CASES, "sampled_events": sum(lengths),
                 "max_joint_len": max(lengths), "cells": {}}

        for n in PARTY_COUNTS:
            max_local = 0
            for p in range(n):
                rows = [(cid, deal(joint[cid], n, p)) for cid in sample]
                assert all(evs for _, evs in rows), f"{dset} n={n}: empty local trace"
                max_local = max(max_local, max(len(evs) for _, evs in rows))
                write_party_xes(os.path.join(cell_dir(dset, n, C_CASES), f"party_{p}.xes"), rows)
            assert max_local <= MAX_LEN // 2
            dmeta["cells"][f"n{n}"] = {"cases_per_party": C_CASES, "max_local_len": max_local,
                                       "total_rows": n * C_CASES}
            print(f"{dset} n={n}: {C_CASES} cases/party, max local len {max_local}")

        for n in CONTROL_NS:
            m = n * C_CASES // 2
            ctrl_ids = list(sample)
            k = 1
            while len(ctrl_ids) < m:
                ctrl_ids.extend(f"{cid}#dup{k}" for cid in sample[:m - len(ctrl_ids)])
                k += 1
            max_local = 0
            for p in range(2):
                rows = [(cid, deal(joint[cid.split("#dup")[0]], 2, p)) for cid in ctrl_ids]
                assert all(evs for _, evs in rows)
                max_local = max(max_local, max(len(evs) for _, evs in rows))
                write_party_xes(os.path.join(cell_dir(dset, 2, m), f"party_{p}.xes"), rows)
            assert max_local <= MAX_LEN // 2
            dmeta["cells"][f"ctrl{m}"] = {"cases_per_party": m, "max_local_len": max_local,
                                          "total_rows": 2 * m, "matched_n": n}
            print(f"{dset} ctrl{m} (matches n={n}): {m} cases/party, max local len {max_local}")

        meta["datasets"][dset] = dmeta

    meta_path = os.path.join(DATA_ROOT, "e4b_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
