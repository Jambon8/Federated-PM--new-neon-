"""Generate N-party test data by splitting a single XES event log.

Usage:
    python3 generate_test_data.py input.xes.gz --parties 3 --overlap 0.5 --output-dir data/split/
    python3 generate_test_data.py input.xes.gz --parties 2 --overlap 1.0

Simulates N organizations that share case IDs but each observe different events:
  - overlap fraction of cases appear in ALL parties
  - remaining cases are distributed round-robin (non-overlapping)
  - For shared cases, each party gets a different subset of the events
    (events are distributed round-robin across parties by index),
    simulating each org seeing a different part of the process.
"""

import argparse
import gzip
import os
import random
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy


def split_xes(input_path, n_parties, overlap_ratio, output_dir, seed=42):
    """Split a XES log into N partial logs with controlled overlap."""
    random.seed(seed)

    opener = gzip.open if input_path.endswith(".gz") else open
    with opener(input_path, 'rb') as f:
        tree = ET.parse(f)
        root = tree.getroot()

    ns = ''
    traces = root.findall('.//{http://www.xes-standard.org/}trace')
    if traces:
        ns = '{http://www.xes-standard.org/}'
    else:
        traces = root.findall('.//trace')

    n_traces = len(traces)
    if n_traces == 0:
        print("Error: No traces found in input log.")
        sys.exit(1)

    # Shuffle traces for random assignment
    indices = list(range(n_traces))
    random.shuffle(indices)

    # Split: overlap traces go to all parties, rest distributed round-robin
    n_overlap = max(1, int(n_traces * overlap_ratio))
    overlap_indices = set(indices[:n_overlap])
    remaining_indices = indices[n_overlap:]

    # Assign traces to parties
    party_indices = [set() for _ in range(n_parties)]
    for idx in overlap_indices:
        for p in range(n_parties):
            party_indices[p].add(idx)
    for i, idx in enumerate(remaining_indices):
        party_indices[i % n_parties].add(idx)

    print(f"Input: {n_traces} traces")
    print(f"Overlap: {n_overlap} traces in all {n_parties} parties ({overlap_ratio*100:.0f}%)")
    print(f"Non-overlap: {len(remaining_indices)} traces distributed round-robin")
    for p in range(n_parties):
        n_exclusive = len(party_indices[p]) - (n_overlap if True else 0)
        print(f"  Party {p}: {len(party_indices[p])} traces ({n_overlap} shared + {n_exclusive} exclusive)")

    # Write output XES files
    os.makedirs(output_dir, exist_ok=True)
    output_paths = []

    for p in range(n_parties):
        new_root = deepcopy(root)
        # Remove all traces from root
        for trace in new_root.findall(f'.//{ns}trace'):
            new_root.remove(trace)

        for idx in sorted(party_indices[p]):
            trace = traces[idx]
            events = trace.findall(f'{ns}event')

            if idx in overlap_indices and len(events) > 0:
                # Shared case: give this party only its subset of events.
                # Distribute events round-robin: party p gets events where event_index % n_parties == p.
                new_trace = deepcopy(trace)
                for ev in new_trace.findall(f'{ns}event'):
                    new_trace.remove(ev)
                for ev_idx, ev in enumerate(events):
                    if ev_idx % n_parties == p:
                        new_trace.append(deepcopy(ev))
                new_root.append(new_trace)
            else:
                # Exclusive case: party gets all events
                new_root.append(deepcopy(trace))

        out_path = os.path.join(output_dir, f"party_{p}.xes")
        tree_out = ET.ElementTree(new_root)
        ET.indent(tree_out, space="  ")
        tree_out.write(out_path, xml_declaration=True, encoding='unicode')
        output_paths.append(out_path)
        print(f"Written: {out_path} ({len(party_indices[p])} traces)")

    # Print event distribution stats for overlap cases
    total_events = 0
    per_party_events = [0] * n_parties
    for idx in overlap_indices:
        events = traces[idx].findall(f'{ns}event')
        n_ev = len(events)
        total_events += n_ev
        for p in range(n_parties):
            per_party_events[p] += len([e for i, e in enumerate(events) if i % n_parties == p])
    print(f"\nShared cases event distribution ({n_overlap} cases, {total_events} total events):")
    for p in range(n_parties):
        print(f"  Party {p}: {per_party_events[p]} events")

    return output_paths


def main():
    parser = argparse.ArgumentParser(description="Split XES log into N party logs for testing")
    parser.add_argument("input", help="Path to input XES/XES.GZ file")
    parser.add_argument("--parties", type=int, default=3, help="Number of parties (default: 3)")
    parser.add_argument("--overlap", type=float, default=0.5,
                        help="Fraction of cases in all parties (0.0-1.0, default: 0.5)")
    parser.add_argument("--output-dir", default="data/split/", help="Output directory (default: data/split/)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()

    if args.parties < 2:
        print("Error: At least 2 parties required.")
        sys.exit(1)
    if not 0.0 <= args.overlap <= 1.0:
        print("Error: Overlap must be between 0.0 and 1.0.")
        sys.exit(1)

    paths = split_xes(args.input, args.parties, args.overlap, args.output_dir, args.seed)

    print(f"\nTo run {args.parties}-party MPC:")
    print(f"  python3 examples/run_process_mining.py --logs {' '.join(paths)} --threads 16")


if __name__ == "__main__":
    main()
