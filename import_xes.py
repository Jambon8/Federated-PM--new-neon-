import gzip
import xml.etree.ElementTree as ET
from datetime import datetime
import sys
import json
import os
import struct
import hashlib

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

def parse_xes(filepath, use_handovers=False, timestamp_granularity=1):
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
        
        events = []
        
        # Try to find events with namespace first, then without
        event_list = trace.findall('{http://www.xes-standard.org/}event')
        if not event_list:
            event_list = trace.findall('event')
            
        full_events = []
            
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
            
            # Keep track of the full sequence for fingerprinting
            full_events.append((timestamp, activity))
            
            # Ensure handover logic
            if use_handovers:
                # Find boolean handover flag if present (default to false if missing)
                is_handover = False
                bool_attrs = event.findall('{http://www.xes-standard.org/}boolean')
                if not bool_attrs:
                    bool_attrs = event.findall('boolean')
                    
                for b_attr in bool_attrs:
                    if b_attr.get('key') == 'handover' and b_attr.get('value') == 'true':
                        is_handover = True
                        break
                        
                if not is_handover:
                    # Skip this event completely because it is not a handover point
                    continue

            events.append((timestamp, activity))
            
        if use_handovers and full_events:
            # Generate a Secure Hash Fingerprint of the original local sequence
            # This ensures that traces with identical handovers but different internal logic do not collide
            hash_input = "".join([act for t, act in full_events]).encode('utf-8')
            fingerprint_hash = hashlib.md5(hash_input).hexdigest()[:8]
            fingerprint_act = f"Fingerprint_{fingerprint_hash}"
            
            # Assign a timestamp + 1s to ensure it sorts strictly to the end of the local case
            max_time = max(t for t, act in full_events)
            events.append((max_time + 1, fingerprint_act))
        
        # Sort events by (timestamp, activity_name) to ensure local sorted invariant.
        # Tiebreaking by activity_name makes the composite key (ts << 20 | act_id)
        # monotonic within each party, which the bitonic merge requires.
        events.sort(key=lambda x: (x[0], x[1]))

        if events:  # Only add traces that have events
            cases.append({'id': case_id, 'events': events})
        
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
    for path in args.logs:
        cases = parse_xes(path, use_handovers=args.use_handovers, timestamp_granularity=granularity)
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