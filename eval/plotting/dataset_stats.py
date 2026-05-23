"""Compute dataset stats for the Chapter 6 dataset table.

For every log used in E1..E10, emit (name, cases, events, activities, variants).
Result is the source-of-truth for tab:datasets — pin the CSV at chapter freeze.
"""

import csv
import os
import sys

import pm4py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "eval_results", "stage_breakdown")  # share with other artefacts

# Use the OrgA halves as canonical (the full log is the union, but our N=2 split
# is also a fair representation since the parties carry the same cases).
DATASETS = [
    ("bpi13_incidents",   "data/Master_Input/OrgA/BPI_Challenge_2013_incidents.xes.gz"),
    ("bpi13_open",        "data/Master_Input/OrgA/BPI_Challenge_2013_open_problems.xes.gz"),
    ("bpi13_closed",      "data/Master_Input/OrgA/BPI_Challenge_2013_closed_problems.xes.gz"),
    ("sepsis",            "data/Master_Input/OrgA/Sepsis_Cases_OrgA.xes.gz"),
    ("bpi12",             "data/Master_Input/OrgA/BPI_Challenge_2012.xes.gz"),
    ("hospital",          "data/Master_Input/OrgA/Hospital_log.xes.gz"),
    ("bpi17_offer",       "data/Master_Input/OrgA/BPIChallenge2017-Offerlog.xes"),
    ("requestforpayment", "data/Master_Input/OrgA/RequestForPayment_OrgA.xes.gz"),
    ("domestic_decl",     "data/Master_Input/OrgA/DomesticDeclarations_OrgA.xes.gz"),
    ("international_decl","data/Master_Input/OrgA/InternationalDeclarations_OrgA.xes.gz"),
    ("permit",            "data/Master_Input/OrgA/PermitLog_OrgA.xes.gz"),
]


def stats(name, path):
    if not os.path.isfile(path):
        return {"dataset": name, "path": path, "cases": None, "events": None,
                "activities": None, "variants": None, "error": "missing"}
    try:
        log = pm4py.read_xes(path)
        cases = log["case:concept:name"].nunique()
        events = len(log)
        activities = log["concept:name"].nunique()
        variants = len(pm4py.get_variants(log))
        return {"dataset": name, "path": path, "cases": cases, "events": events,
                "activities": activities, "variants": variants, "error": None}
    except Exception as ex:
        return {"dataset": name, "path": path, "cases": None, "events": None,
                "activities": None, "variants": None, "error": str(ex)}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    print(f"{'dataset':<20} {'cases':>10} {'events':>10} {'activities':>11} {'variants':>10}")
    for name, rel in DATASETS:
        path = os.path.join(ROOT, rel)
        row = stats(name, path)
        rows.append(row)
        if row["error"]:
            print(f"{name:<20} ERROR: {row['error']}")
        else:
            print(f"{name:<20} {row['cases']:>10} {row['events']:>10} "
                  f"{row['activities']:>11} {row['variants']:>10}")
    out = os.path.join(OUT_DIR, "dataset_stats.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dataset", "path", "cases", "events",
                                          "activities", "variants", "error"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
