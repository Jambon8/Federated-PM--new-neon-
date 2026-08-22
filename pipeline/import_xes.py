import gzip
import xml.etree.ElementTree as ET
from datetime import datetime
import sys
import json
import os
import struct
import hashlib
import hmac

# --- Configuration ---

BASE_DIR = os.getcwd()
# Ensure Player-Data exists
if not os.path.exists("Player-Data"):
    os.makedirs("Player-Data")

def party_file(p):
    return os.path.join(BASE_DIR, f"Player-Data/Input-P{p}-0")

def party_file_bin(p):
    return os.path.join(BASE_DIR, f"Player-Data/Input-Binary-P{p}-0")

# Padding values for empty slots
PAD_TIME = 2**60
PAD_ACT = 0
PAD_ID = 2**60 # Use Max 64-bit value for ID padding to stay sorted at end

# Timestamp granularity options: name -> milliseconds
GRANULARITY_MS = {'ms': 1, 's': 1000, 'm': 60_000, 'h': 3_600_000}

# --- Keyed reversible fingerprints for the optional handover collapse ---
# The central preparation harness replaces every maximal run of internal
# activities with a keyed fingerprint and writes one reversal table per party.
# One persisted key makes equal runs map to equal labels across all parties.
# Private per-party preprocessing and key custody are not implemented.

def _fp_key_path():
    return os.path.join(BASE_DIR, "Player-Data/fp_key_global.bin")

def _load_or_create_fp_key():
    path = _fp_key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    key = os.urandom(32)
    with open(path, "wb") as f:
        f.write(key)
    return key

def _collapse_run(run, fp_key, fp_map):
    """Replace one maximal internal run with a keyed fingerprint event placed at
    the run's last timestamp; record the reversal mapping (asserting injectivity).
    `run` is a list of (timestamp, activity) in temporal order."""
    acts = [a for (t, a) in run]
    msg = "\x1f".join(acts).encode("utf-8")            # unambiguous run encoding
    digest = hmac.new(fp_key, msg, hashlib.sha256).hexdigest()[:16]  # 64-bit label
    label = f"Fingerprint_{digest}"
    if fp_map.get(label, acts) != acts:
        raise ValueError(f"Fingerprint collision on {label}: {fp_map[label]} vs {acts}")
    fp_map[label] = acts
    return (run[-1][0], label)

def _save_fingerprint_map(party_index, fp_map):
    path = os.path.join(BASE_DIR, f"Player-Data/fingerprint_map_P{party_index}.json")
    with open(path, "w") as f:
        json.dump(fp_map, f)
    print(f"Saved {len(fp_map)} fingerprint reversal entries to '{path}'")


def _validate_fingerprint_maps(n_parties):
    """Require every occurring label to expand to one sequence globally."""
    combined = {}
    for party_index in range(n_parties):
        path = os.path.join(BASE_DIR, f"Player-Data/fingerprint_map_P{party_index}.json")
        with open(path, encoding="utf-8") as stream:
            party_map = json.load(stream)
        for label, activities in party_map.items():
            previous = combined.get(label)
            if previous is not None and previous != activities:
                raise ValueError(
                    f"Fingerprint collision across parties on {label}: "
                    f"{previous} vs {activities}"
                )
            combined[label] = activities
    print(f"Validated {len(combined)} globally sequence-consistent fingerprint labels.")


def _validate_handover_contract(cases_list):
    """Validate phase separation and strict boundary times on joint traces."""
    events_by_case = {}
    for party_index, cases in enumerate(cases_list):
        for case in cases:
            joint_events = events_by_case.setdefault(case["id"], [])
            joint_events.extend(
                (timestamp, activity, party_index)
                for timestamp, activity in case["events"]
            )

    for case_id, joint_events in events_by_case.items():
        if not any(activity.startswith("Fingerprint_") for _, activity, _ in joint_events):
            continue
        ordered = sorted(joint_events, key=lambda item: (item[0], item[1], item[2]))
        for previous, current in zip(ordered, ordered[1:]):
            previous_is_fp = previous[1].startswith("Fingerprint_")
            current_is_fp = current[1].startswith("Fingerprint_")
            if previous_is_fp != current_is_fp and previous[0] == current[0]:
                raise ValueError(
                    f"Case {case_id} has a fingerprint and handover boundary "
                    "at the same joint timestamp."
                )

        phase_fingerprints = []
        for timestamp, activity, party_index in ordered:
            if activity.startswith("Fingerprint_"):
                phase_fingerprints.append((timestamp, activity, party_index))
                continue
            if len(phase_fingerprints) > 1:
                raise ValueError(
                    f"Case {case_id} has internal phases from multiple party rows "
                    "between handover boundaries."
                )
            phase_fingerprints = []
        if len(phase_fingerprints) > 1:
            raise ValueError(
                f"Case {case_id} has internal phases from multiple party rows "
                "after its final handover boundary."
            )

    print("Validated joint handover phase separation and boundary timestamps.")

# --- Global handover list H (public, applied identically by every party) ---
# An activity is a handover (boundary) event iff it belongs to the single
# global list H. Every party applies the same H, so a trace shared by several
# parties collapses identically on every side and the PSI match is preserved.
# The organizations declare H once, before Stage 1, and each party reads it from
# the same file; eval/prepare/handover_lists.py builds the lists shipped with
# the evaluation logs.

def load_handover_list(path):
    """Read a curated global handover list H: one activity name per line; blank
    lines and lines beginning with '#' are ignored."""
    H = set()
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                H.add(s)
    return H

def parse_xes(filepath, use_handovers=False, timestamp_granularity=1, party_index=0,
              handover_activities=None):
    """Parses .xes.gz and returns list of cases: {'id': str, 'events': [(time, act), ...]}"""
    print(f"Reading {filepath}... (Use Handovers: {use_handovers})")
    
    # Handle both .gz and plain .xes
    opener = gzip.open if filepath.endswith(".gz") else open
    
    try:
        with opener(filepath, 'rb') as f:
            tree = ET.parse(f)
            root = tree.getroot()
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        return []
    except Exception as e:
        print(f"Error parsing XML: {e}")
        return []

    cases = []

    # Per-party keyed-fingerprint state for the optional handover collapse.
    fp_key = _load_or_create_fp_key() if use_handovers else None
    fp_map = {}  # fingerprint label -> internal activity subtrace (private reversal table)

    # The global handover list H, shared by every party (empty when unused).
    H = handover_activities or set()
    if use_handovers and not H:
        raise ValueError(
            "Handover collapse requires a non-empty global list H. Every event is "
            "otherwise internal, collapsing each trace to a single fingerprint."
        )

    # Try to find traces with namespace first, then without
    traces = root.findall('.//{http://www.xes-standard.org/}trace')
    if not traces:
        traces = root.findall('.//trace')
    
    print(f"Found {len(traces)} traces in {filepath}")
    
    for trace in traces:
        # Extract Case ID
        case_id = "Unknown"
        
        # Try with namespace first
        for attr in trace.findall('{http://www.xes-standard.org/}string'):
            if attr.get('key') == 'concept:name':
                case_id = attr.get('value')
                break
        
        # Fallback without namespace
        if case_id == "Unknown":
            for attr in trace.findall('string'):
                if attr.get('key') == 'concept:name':
                    case_id = attr.get('value')
                    break
        
        # Collect every event with its handover flag, in document order.
        raw_events = []  # (timestamp, activity, is_handover)

        # Try to find events with namespace first, then without
        event_list = trace.findall('{http://www.xes-standard.org/}event')
        if not event_list:
            event_list = trace.findall('event')

        for event in event_list:
            timestamp = None
            activity = None

            # Extract Timestamp - try with namespace first
            date_attrs = event.findall('{http://www.xes-standard.org/}date')
            if not date_attrs:
                date_attrs = event.findall('date')

            for date in date_attrs:
                if date.get('key') == 'time:timestamp':
                    # Parse ISO 8601 (e.g., 2012-03-01T00:00:00.000+01:00)
                    ts_str = date.get('value')
                    if not ts_str:
                        raise ValueError(f"Case {case_id} contains an empty event timestamp.")
                    try:
                        # Simplified parser (strips timezone for integer conversion)
                        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        timestamp = int(dt.timestamp() * 1000)
                        timestamp = (timestamp // timestamp_granularity) * timestamp_granularity
                    except ValueError as exc:
                        raise ValueError(
                            f"Case {case_id} contains malformed timestamp {ts_str!r}."
                        ) from exc

            if timestamp is None:
                raise ValueError(f"Case {case_id} contains an event without a timestamp.")

            # Extract Activity Name - try with namespace first
            string_attrs = event.findall('{http://www.xes-standard.org/}string')
            if not string_attrs:
                string_attrs = event.findall('string')

            for string in string_attrs:
                if string.get('key') == 'concept:name':
                    activity = string.get('value')

            if not activity:
                raise ValueError(f"Case {case_id} contains an event without an activity name.")

            if activity.startswith("Fingerprint_"):
                raise ValueError(
                    "Input activity names must not use the reserved Fingerprint_ prefix."
                )

            # An activity is a handover (boundary) event iff it is in the global
            # list H. The same public H is applied by every party, so a shared
            # trace collapses identically on all sides.
            is_handover = use_handovers and activity in H

            raw_events.append((timestamp, activity, is_handover))

        if use_handovers:
            # Per-run handover collapse (def:handover-collapse): order events in
            # time, then replace every maximal run of internal (non-handover)
            # events with a single keyed fingerprint placed at the run's last
            # timestamp; handover events are kept unchanged.
            raw_events.sort(key=lambda x: (x[0], x[1]))
            for previous, current in zip(raw_events, raw_events[1:]):
                boundary_transition = previous[2] != current[2]
                if boundary_transition and previous[0] >= current[0]:
                    raise ValueError(
                        "Handover boundaries require strictly increasing timestamps."
                    )
            events = []
            run = []  # accumulated internal events (timestamp, activity)
            for (t, act, is_h) in raw_events:
                if is_h:
                    if run:
                        events.append(_collapse_run(run, fp_key, fp_map))
                        run = []
                    events.append((t, act))
                else:
                    run.append((t, act))
            if run:
                events.append(_collapse_run(run, fp_key, fp_map))
        else:
            events = [(t, act) for (t, act, _h) in raw_events]

        # Sort events by (timestamp, activity_name) to ensure local sorted invariant.
        # Tiebreaking by activity_name makes the composite key (ts << 17 | act_id)
        # monotonic within each party, which the bitonic merge requires.
        events.sort(key=lambda x: (x[0], x[1]))

        if events:  # Only add traces that have events
            cases.append({'id': case_id, 'events': events})

    if use_handovers:
        _save_fingerprint_map(party_index, fp_map)

    return cases

def encode_and_save(cases_list, force_partial_len=None):
    """Aligns dictionaries and writes MPC input files for N parties.

    Args:
        cases_list: list of N case lists, one per party.
                    Each element is a list of {'id': str, 'events': [(time, act), ...]}.
        force_partial_len: if set, pad every row to exactly this width instead of
                    the derived maximum trace length. Callers must have truncated
                    longer traces beforehand; used to pin the circuit width in
                    controlled scaling experiments.
    Returns:
        (n_max, max_len) — dimensions for MPC configuration.
    """
    n_parties = len(cases_list)

    for party_index, cases in enumerate(cases_list):
        case_ids = [case["id"] for case in cases]
        if "Unknown" in case_ids:
            raise ValueError(f"Party {party_index} contains a trace without a case identifier.")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError(f"Party {party_index} contains duplicate case identifiers.")

    # --- Sorting for iterative bitonic merge ---
    # P0: ascending by case_id. P1..Pk-1: descending (for bitonic merge).
    cases_list[0].sort(key=lambda c: c['id'])
    for p in range(1, n_parties):
        cases_list[p].sort(key=lambda c: c['id'], reverse=True)
    if n_parties == 2:
        print("Sorted inputs for Bitonic Merge: P0 (Asc), P1 (Desc)")
    else:
        print(f"Sorted inputs for Iterative Bitonic Merge: P0 (Asc), P1..P{n_parties-1} (Desc)")

    # 1. Build Global Dictionaries
    all_cases = [c for party in cases_list for c in party]
    all_case_ids = set(c['id'] for c in all_cases)
    all_activities = set(e[1] for c in all_cases for e in c['events'])

    if len(all_case_ids) >= PAD_ID:
        raise ValueError(f"At most {PAD_ID - 1} distinct case identifiers are supported.")
    if len(all_activities) >= 2**17:
        raise ValueError("The packed sort key supports at most 131071 activities.")
    invalid_timestamps = [
        t
        for case in all_cases
        for t, _activity in case["events"]
        if t < 0 or t >= 2**43
    ]
    if invalid_timestamps:
        raise ValueError("Timestamps must be nonnegative milliseconds below 2^43.")
    if any(activity.startswith("Fingerprint_") for activity in all_activities):
        _validate_fingerprint_maps(n_parties)
        _validate_handover_contract(cases_list)

    case_id_map = {cid: i+1 for i, cid in enumerate(sorted(all_case_ids))}
    act_map = {act: i+1 for i, act in enumerate(sorted(all_activities))}

    # Save mapping to JSON
    id_to_act = {v: k for k, v in act_map.items()}
    with open("Player-Data/activity_map.json", "w") as f:
        json.dump(id_to_act, f)
    print("Saved Activity Mapping to 'Player-Data/activity_map.json'")

    print(f"Mapped {len(all_case_ids)} Cases, {len(all_activities)} Activities.")

    print("\n--- ACTIVITY DECODER RING ---")
    sorted_acts = sorted(act_map.items(), key=lambda item: item[1])
    for name, id in sorted_acts:
        print(f"ID {id}: '{name}'")
    print("-----------------------------\n")

    print(f"Found {len(all_case_ids)} unique Cases and {len(all_activities)} unique Activities.")

    # 2. Determine Dimensions (max across all parties)
    max_len = max((max((len(c['events']) for c in party), default=0) for party in cases_list), default=0)
    if force_partial_len is not None:
        if max_len > force_partial_len:
            raise ValueError(f"force_partial_len={force_partial_len} but a trace has {max_len} events; "
                             "truncate traces before encoding.")
        max_len = force_partial_len
    n_max = max(len(party) for party in cases_list)

    print(f"Configuration for MPC: N_PER_PARTY={n_max}, PARTIAL_LEN={max_len}, N_PARTIES={n_parties}")

    # 3. Write Inputs
    def write_party_file(filename, filename_bin, cases, target_n, target_len, pad_at_start=False):
        """Write both text and binary input files for MP-SPDZ"""
        with open(filename, 'w') as f:
            all_rows = []

            for c in cases:
                row = [case_id_map.get(c['id'], 0)]
                for j in range(target_len):
                    if j < len(c['events']):
                        t, act = c['events'][j]
                        row.append(t)
                        row.append(act_map.get(act, 0))
                    else:
                        row.append(PAD_TIME)
                        row.append(PAD_ACT)
                all_rows.append(row)

            pad_needed = target_n - len(cases)
            padding_row = [PAD_ID] + [PAD_TIME, PAD_ACT] * target_len

            if pad_at_start:
                final_rows = [padding_row] * pad_needed + all_rows
            else:
                final_rows = all_rows + [padding_row] * pad_needed

            for row in final_rows:
                 f.write(" ".join(map(str, row)) + "\n")

        with open(filename_bin, 'wb') as f:
            for row in final_rows:
                for value in row:
                    f.write(struct.pack('<q', value))

    # P0: ascending, padding at end. P1..Pk-1: descending, padding at start.
    for p in range(n_parties):
        pad_at_start = (p > 0)
        write_party_file(party_file(p), party_file_bin(p), cases_list[p],
                         n_max, max_len, pad_at_start=pad_at_start)

    print(f"Input files for {n_parties} parties generated successfully.")
    return n_max, max_len

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs='+', help="Paths to XES log files (one per party, minimum 2)")
    parser.add_argument("--use-handovers", action="store_true")
    parser.add_argument("--handover-activities", type=str, default=None,
                        help="Path to the global handover list H (one activity per line). "
                             "Required with --use-handovers; every party reads the same file.")
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). All parties must use the same value.")
    args = parser.parse_args()

    if len(args.logs) < 2:
        print("Error: At least 2 log files required.")
        sys.exit(1)

    granularity = GRANULARITY_MS[args.timestamp_granularity]

    handover_set = None
    if args.use_handovers:
        if not args.handover_activities:
            print("Error: --use-handovers requires --handover-activities <file>, the "
                  "public handover list H every party applies.")
            sys.exit(1)
        handover_set = load_handover_list(args.handover_activities)
        print(f"Loaded {len(handover_set)} handover activities from '{args.handover_activities}'")

    cases_list = []
    for p, path in enumerate(args.logs):
        cases = parse_xes(path, use_handovers=args.use_handovers, timestamp_granularity=granularity,
                          party_index=p, handover_activities=handover_set)
        if not cases:
            print(f"Failed to load log: {path}")
            sys.exit(1)
        cases_list.append(cases)

    n, length = encode_and_save(cases_list)

    n_parties = len(cases_list)
    print("\n!!! UPDATE YOUR .mpc FILE WITH THESE VALUES !!!")
    print(f"N_PER_PARTY = {n}")
    print(f"PARTIAL_LEN = {length}")
    print(f"N_PARTIES = {n_parties}")
    print(f"FULL_LEN = {length * n_parties}")
