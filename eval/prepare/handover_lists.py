"""Generate the public handover list H for each prepared two-party federation.

The handover optimization needs one public list of activities at which a case
crosses the organizational interface. Every party applies the same list, so the
list is prepared once, before any run, and shipped alongside the logs.

Construction per dataset:
  1. Boundary activities. Merge both parties' events of a case, order them by
     timestamp, and mark every event whose neighbor in that order belongs to the
     other party. H0 collects the activities of the marked events.
  2. Tie closure. An activity of H0 splits a party-local trace at every one of
     its occurrences, including occurrences that share a timestamp with an
     intra-party activity. Such a tie leaves the split position undetermined by
     the timestamps: the fingerprint replacing the intra-party run orders against
     the tied activity by its own label. Adding every activity that shares a
     timestamp with a member of H removes the ambiguity, so the list grows to the
     smallest superset in which no party-local tie group is split. The growth is
     iterated because adding an activity brings its own tie groups into scope.

Step 2 is what makes the shipped list satisfy the phase-separation premise of
def:handover-collapse: between two consecutive handover events of a case exactly
one party is active, and every intra-party run lies strictly between the
handover events that delimit it.

Only the five federations whose source logs record an organizational interface
are listed here. The remaining prepared logs split a single organization's log by
case, so no activity marks a handover for them.

Usage:
    python3 eval/prepare/handover_lists.py            # writes data/2parties/*/handover.txt
    python3 eval/prepare/handover_lists.py --check    # verify the files on disk, write nothing
"""

import argparse
import glob
import gzip
import os
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

XES_NS = "{http://www.xes-standard.org/}"

# The federations built from a source log that records two organizations.
DATASETS = (
    "domestic_decl",
    "international_decl",
    "permit",
    "requestforpayment",
    "sepsis",
)


def _find(element, tag):
    """Children of one XES tag, with and without the namespace."""
    return element.findall(XES_NS + tag) or element.findall(tag)


def read_log(path):
    """Return {case id: [(timestamp, activity), ...]} in document order."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as stream:
        root = ET.parse(stream).getroot()
    traces = root.findall(".//" + XES_NS + "trace") or root.findall(".//trace")
    cases = {}
    for trace in traces:
        case_id = None
        for attribute in _find(trace, "string"):
            if attribute.get("key") == "concept:name":
                case_id = attribute.get("value")
        events = []
        for event in _find(trace, "event"):
            activity = timestamp = None
            for attribute in _find(event, "string"):
                if attribute.get("key") == "concept:name":
                    activity = attribute.get("value")
            for attribute in _find(event, "date"):
                if attribute.get("key") == "time:timestamp":
                    timestamp = attribute.get("value")
            if activity is not None and timestamp is not None:
                events.append((timestamp, activity))
        cases.setdefault(case_id, []).extend(events)
    return cases


def boundary_activities(parties):
    """Activities of events whose neighbor in the joint case order is another party.

    `parties` is one read_log mapping per party. Ordering the joint events by
    timestamp alone keeps a tied group in document order, which is the order in
    which the source log records the interface crossing.
    """
    every_case = set()
    for party in parties:
        every_case |= set(party)
    activities = set()
    for case_id in every_case:
        joint = [
            (timestamp, activity, index)
            for index, party in enumerate(parties)
            for timestamp, activity in party.get(case_id, [])
        ]
        joint.sort(key=lambda event: event[0])
        for position, (_timestamp, activity, owner) in enumerate(joint):
            before = position > 0 and joint[position - 1][2] != owner
            after = position + 1 < len(joint) and joint[position + 1][2] != owner
            if before or after:
                activities.add(activity)
    return activities


def tie_groups(parties):
    """Sets of two or more activities a party records for one case at one timestamp."""
    groups = []
    for party in parties:
        for events in party.values():
            at_timestamp = {}
            for timestamp, activity in events:
                at_timestamp.setdefault(timestamp, set()).add(activity)
            groups.extend(group for group in at_timestamp.values() if len(group) > 1)
    return groups


def close_under_ties(activities, groups):
    """Smallest superset of `activities` that splits no tie group of `groups`."""
    closed = set(activities)
    while True:
        grown = set()
        for group in groups:
            if group & closed:
                grown |= group
        if grown <= closed:
            return closed
        closed |= grown


def handover_list(log_paths):
    """The public handover list H of one prepared federation."""
    parties = [read_log(path) for path in log_paths]
    return close_under_ties(boundary_activities(parties), tie_groups(parties))


def party_logs(dataset):
    """The party logs of one two-party dataset, in party order."""
    directory = os.path.join(ROOT, "data", "2parties", dataset)
    logs = sorted(glob.glob(os.path.join(directory, "party_*.xes.gz")))
    return logs or sorted(glob.glob(os.path.join(directory, "party_*.xes")))


def list_path(dataset):
    return os.path.join(ROOT, "data", "2parties", dataset, "handover.txt")


def render(dataset, activities):
    """The file contents: a provenance header, then one activity per line."""
    lines = [
        f"# Public handover list H for the {dataset} federation.",
        f"# {len(activities)} activities, closed under tied timestamps.",
        "# Generated by eval/prepare/handover_lists.py; every party applies this list.",
    ]
    lines.extend(sorted(activities))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Compare the files on disk against a fresh derivation.")
    parser.add_argument("--datasets", nargs="+", default=list(DATASETS))
    args = parser.parse_args()

    from pipeline.import_xes import load_handover_list

    failures = 0
    for dataset in args.datasets:
        logs = party_logs(dataset)
        if len(logs) < 2:
            print(f"{dataset:22s} SKIP  no party logs under data/2parties/{dataset}/")
            continue
        activities = handover_list(logs)
        path = list_path(dataset)
        if args.check:
            if not os.path.exists(path):
                print(f"{dataset:22s} FAIL  missing {os.path.relpath(path, ROOT)}")
                failures += 1
                continue
            stored = load_handover_list(path)
            status = "OK  " if stored == activities else "FAIL"
            failures += stored != activities
            print(f"{dataset:22s} {status}  |H| = {len(activities)}")
            if stored != activities:
                print(f"{'':22s}       on disk only: {sorted(stored - activities)}")
                print(f"{'':22s}       derived only: {sorted(activities - stored)}")
        else:
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(render(dataset, activities))
            print(f"{dataset:22s} wrote {os.path.relpath(path, ROOT)}  |H| = {len(activities)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
