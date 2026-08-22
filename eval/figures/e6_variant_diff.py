"""E6 variant-set diff: what partial-order grouping recovers vs the default.

For each (dataset, delta) tuple in E6, compare the variant multiset released
under PO-off (canonical timestamp ordering) against the variant multiset
released under PO-on. Reports:

    common              variants present in both regimes
    lost_by_PO          present without PO, missing once PO collapses ties
    recovered_by_PO     present only with PO (concurrent-event traces)

Interpretation: PO grouping replaces timestamp-ordered traces of concurrent
events with order-equivalent classes. `recovered_by_PO` quantifies the
concurrent-event variants the default regime cannot see; `lost_by_PO` are
spurious orderings that the default regime publishes but PO collapses.
"""

import csv
import json
import os
from collections import Counter, defaultdict

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from eval.utils import load_run, run_files, run_id_of  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
E6_DIR  = os.path.join(ROOT, "eval_results", "modes_partial_order")
OUT_DIR = os.path.join(ROOT, "eval_results", "stage_breakdown")


def variant_key_full(v):
    """Canonicalize a variant: keep both [activity_id, concurrency_flag] columns.

    PO output annotates each event with a flag indicating membership in a
    concurrent set; PO-off and PO-on therefore disagree even on traces with
    identical activity sequences."""
    return tuple(tuple(e) for e in v["raw"] if not (e[0] == 0 and e[1] == 0))


def variant_key_acts(v):
    """Activity-sequence only; ignores concurrency annotation."""
    return tuple(e[0] for e in v["raw"] if not (e[0] == 0 and e[1] == 0))


def load_e6(keyfn):
    """Return {(dataset, po, delta): {variant_key: count}} folded by `keyfn`."""
    # Reps are deterministic for the same config; rep0 alone is canonical.
    per_config = defaultdict(dict)
    for path in run_files(E6_DIR):
        if not run_id_of(path).endswith("_rep0"):
            continue
        rec = load_run(path)
        meta = rec["meta"]
        key = (meta["dataset"], int(meta["partial_orders"]), str(meta["delta"]))
        counter = Counter()
        for v in rec.get("variants", []):
            counter[keyfn(v)] += v["count"]
        per_config[key] = dict(counter)
    return per_config


def diff():
    cfg_full = load_e6(variant_key_full)
    cfg_acts = load_e6(variant_key_acts)
    datasets = sorted({k[0] for k in cfg_full})

    rows = []
    hdr = (f"{'dataset':<10} {'delta':<5}  "
           f"{'|po0|':>6} {'|po1|':>6}  "
           f"FULL: {'common':>6} {'lost':>6} {'rec':>6}  "
           f"ACTS: {'common':>6} {'lost':>6} {'rec':>6}")
    print(hdr)
    for dset in datasets:
        for delta in ["0", "1s", "1m", "1h"]:
            row = {"dataset": dset, "delta": delta}
            for label, cfg in [("full", cfg_full), ("acts", cfg_acts)]:
                po0 = cfg.get((dset, 0, "0"), {})
                po1 = cfg.get((dset, 1, delta), {})
                if not po0 or not po1:
                    continue
                k0, k1 = set(po0), set(po1)
                row[f"variants_po0_{label}"]    = len(po0)
                row[f"variants_po1_{label}"]    = len(po1)
                row[f"common_{label}"]          = len(k0 & k1)
                row[f"lost_by_PO_{label}"]      = len(k0 - k1)
                row[f"recovered_by_PO_{label}"] = len(k1 - k0)
            if "common_full" not in row:
                continue
            rows.append(row)
            print(f"{dset:<10} {delta:<5}  "
                  f"{row['variants_po0_full']:>6} {row['variants_po1_full']:>6}  "
                  f"FULL: {row['common_full']:>6} {row['lost_by_PO_full']:>6} {row['recovered_by_PO_full']:>6}  "
                  f"ACTS: {row['common_acts']:>6} {row['lost_by_PO_acts']:>6} {row['recovered_by_PO_acts']:>6}")

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "e6_variant_diff.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {csv_path}")


if __name__ == "__main__":
    diff()
