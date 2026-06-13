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
# Each party replaces every maximal run of internal (non-handover) activities
# with one keyed fingerprint and keeps a PRIVATE reversal table mapping the
# fingerprint back to its internal activity sequence. The key is a persisted
# per-party secret, so (a) the label is stable across runs (determinism) and
# (b) the peer cannot brute-force the run from the public label -- expansion is
# possible only by the owning party, which makes any disclosure opt-in.

def _fp_key_path(party_index):
    return os.path.join(BASE_DIR, f"Player-Data/fp_key_P{party_index}.bin")

def _load_or_create_fp_key(party_index):
    path = _fp_key_path(party_index)
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

def parse_xes(filepath, use_handovers=False, timestamp_granularity=1, party_index=0):
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
    fp_key = _load_or_create_fp_key(party_index) if use_handovers else None
    fp_map = {}  # fingerprint label -> internal activity subtrace (private reversal table)

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
            timestamp = 0
            activity = "Unknown"

            # Extract Timestamp - try with namespace first
            date_attrs = event.findall('{http://www.xes-standard.org/}date')
            if not date_attrs:
                date_attrs = event.findall('date')

            for date in date_attrs:
                if date.get('key') == 'time:timestamp':
                    # Parse ISO 8601 (e.g., 2012-03-01T00:00:00.000+01:00)
                    ts_str = date.get('value')
                    try:
                        # Simplified parser (strips timezone for integer conversion)
                        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        timestamp = int(dt.timestamp() * 1000)
                        timestamp = (timestamp // timestamp_granularity) * timestamp_granularity
                    except ValueError:
                        pass # Handle format errors if needed

            # Extract Activity Name - try with namespace first
            string_attrs = event.findall('{http://www.xes-standard.org/}string')
            if not string_attrs:
                string_attrs = event.findall('string')

            for string in string_attrs:
                if string.get('key') == 'concept:name':
                    activity = string.get('value')

            # Find boolean handover flag if present (default to false if missing)
            is_handover = False
            if use_handovers:
                bool_attrs = event.findall('{http://www.xes-standard.org/}boolean')
                if not bool_attrs:
                    bool_attrs = event.findall('boolean')
                for b_attr in bool_attrs:
                    if b_attr.get('key') == 'handover' and b_attr.get('value') == 'true':
                        is_handover = True
                        break

            raw_events.append((timestamp, activity, is_handover))

        if use_handovers:
            # Per-run handover collapse (def:handover-collapse): order events in
            # time, then replace every maximal run of internal (non-handover)
            # events with a single keyed fingerprint placed at the run's last
            # timestamp; handover events are kept unchanged.
            raw_events.sort(key=lambda x: (x[0], x[1]))
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
        # Tiebreaking by activity_name makes the composite key (ts << 20 | act_id)
        # monotonic within each party, which the bitonic merge requires.
        events.sort(key=lambda x: (x[0], x[1]))

        if events:  # Only add traces that have events
            cases.append({'id': case_id, 'events': events})

    if use_handovers:
        _save_fingerprint_map(party_index, fp_map)

    return cases

def encode_and_save(cases_list):
    """Aligns dictionaries and writes MPC input files for N parties.

    Args:
        cases_list: list of N case lists, one per party.
                    Each element is a list of {'id': str, 'events': [(time, act), ...]}.
    Returns:
        (n_max, max_len) — dimensions for MPC configuration.
    """
    n_parties = len(cases_list)

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
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). All parties must use the same value.")
    args = parser.parse_args()

    if len(args.logs) < 2:
        print("Error: At least 2 log files required.")
        sys.exit(1)

    granularity = GRANULARITY_MS[args.timestamp_granularity]
    cases_list = []
    for p, path in enumerate(args.logs):
        cases = parse_xes(path, use_handovers=args.use_handovers, timestamp_granularity=granularity, party_index=p)
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