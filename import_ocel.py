import sys
import json
import os
import struct
import hashlib
from datetime import datetime

# --- Configuration ---
BASE_DIR = os.getcwd()
if not os.path.exists("Player-Data"):
    os.makedirs("Player-Data")

def party_file(p):
    return os.path.join(BASE_DIR, f"Player-Data/Input-P{p}-0")

def party_file_bin(p):
    return os.path.join(BASE_DIR, f"Player-Data/Input-Binary-P{p}-0")

PAD_TIME = 2**60
PAD_ACT = 0
PAD_ID = 2**60

GRANULARITY_MS = {'ms': 1, 's': 1000, 'm': 60_000, 'h': 3_600_000}

def parse_ocel(filepath, flatten_type="Container", use_handovers=False, timestamp_granularity=1):
    print(f"Reading OCEL {filepath}... (Flatten on: '{flatten_type}', Use Handovers: {use_handovers})")
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return []

    # Find IDs of the target flatten type
    valid_ids = {obj['id'] for obj in data.get('objects', []) if obj['type'] == flatten_type}
    
    cases_dict = {vid: [] for vid in valid_ids}
    
    # Map events to cases
    for ev in data.get('events', []):
        act = ev.get('type', 'Unknown')
        
        timestamp = 0
        ts_str = ev.get('time', '')
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                timestamp = int(dt.timestamp() * 1000)
                timestamp = (timestamp // timestamp_granularity) * timestamp_granularity
            except ValueError:
                pass
                
        # Handover check
        is_handover = False
        if use_handovers:
            for attr in ev.get('attributes', []):
                if attr.get('name') == 'handover' and attr.get('value') == 'true':
                    is_handover = True
                    break

        # Check relationships and add to relevant cases
        for rel in ev.get('relationships', []):
            obj_id = rel.get('objectId')
            if obj_id in cases_dict:
                cases_dict[obj_id].append({
                    'timestamp': timestamp,
                    'activity': act,
                    'is_handover': is_handover
                })

    cases = []
    # Post-process cases
    for cid, ev_list in cases_dict.items():
        if not ev_list:
            continue
            
        ev_list.sort(key=lambda x: x['timestamp'])
        
        final_events = []
        if use_handovers:
            for ev in ev_list:
                if ev['is_handover']:
                    final_events.append((ev['timestamp'], ev['activity']))
            
            # Hash Fingerprinting
            hash_input = "".join([ev['activity'] for ev in ev_list]).encode('utf-8')
            fingerprint_hash = hashlib.md5(hash_input).hexdigest()[:8]
            fingerprint_act = f"Fingerprint_{fingerprint_hash}"
            
            max_time = max([ev['timestamp'] for ev in ev_list])
            final_events.append((max_time + 1, fingerprint_act))
        else:
            final_events = [(ev['timestamp'], ev['activity']) for ev in ev_list]
            
        # Ensure locally sorted
        final_events.sort(key=lambda x: x[0])
        
        if final_events:
            cases.append({'id': cid, 'events': final_events})

    return cases

def encode_and_save(cases_list):
    """Aligns dictionaries and writes MPC input files for N parties."""
    n_parties = len(cases_list)

    # P0: ascending. P1..Pk-1: descending (for iterative bitonic merge).
    cases_list[0].sort(key=lambda c: c['id'])
    for p in range(1, n_parties):
        cases_list[p].sort(key=lambda c: c['id'], reverse=True)

    all_cases = [c for party in cases_list for c in party]
    all_case_ids = set(c['id'] for c in all_cases)
    all_activities = set(e[1] for c in all_cases for e in c['events'])

    case_id_map = {cid: i+1 for i, cid in enumerate(sorted(all_case_ids))}
    act_map = {act: i+1 for i, act in enumerate(sorted(all_activities))}

    id_to_act = {v: k for k, v in act_map.items()}
    with open("Player-Data/activity_map.json", "w") as f:
        json.dump(id_to_act, f)

    print("\n--- ACTIVITY DECODER RING ---")
    sorted_acts = sorted(act_map.items(), key=lambda item: item[1])
    for name, id in sorted_acts:
        print(f"ID {id}: '{name}'")
    print("-----------------------------\n")

    max_len = max((max((len(c['events']) for c in party), default=0) for party in cases_list), default=0)
    n_max = max(len(party) for party in cases_list)

    print(f"Configuration for MPC: N_PER_PARTY={n_max}, PARTIAL_LEN={max_len}, N_PARTIES={n_parties}")

    def write_party_file(filename, filename_bin, cases, target_n, target_len, pad_at_start=False):
        with open(filename, 'w') as f:
            all_rows = []
            for c in cases:
                row = [case_id_map.get(c['id'], 0)]
                for j in range(target_len):
                    if j < len(c['events']):
                        t, act = c['events'][j]
                        row.extend([t, act_map.get(act, 0)])
                    else:
                        row.extend([PAD_TIME, PAD_ACT])
                all_rows.append(row)

            pad_needed = target_n - len(cases)
            padding_row = [PAD_ID] + [PAD_TIME, PAD_ACT] * target_len
            final_rows = [padding_row] * pad_needed + all_rows if pad_at_start else all_rows + [padding_row] * pad_needed

            for row in final_rows:
                 f.write(" ".join(map(str, row)) + "\n")

        with open(filename_bin, 'wb') as f:
            for row in final_rows:
                for value in row:
                    f.write(struct.pack('<q', value))

    for p in range(n_parties):
        pad_at_start = (p > 0)
        write_party_file(party_file(p), party_file_bin(p), cases_list[p],
                         n_max, max_len, pad_at_start=pad_at_start)

    print(f"Input files for {n_parties} parties generated successfully.")
    return n_max, max_len

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs='+', help="Paths to OCEL log files (one per party, minimum 2)")
    parser.add_argument("--use-handovers", action="store_true")
    parser.add_argument("--flatten-type", default="Container")
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). All parties must use the same value.")
    args = parser.parse_args()

    if len(args.logs) < 2:
        print("Error: At least 2 log files required.")
        sys.exit(1)

    granularity = GRANULARITY_MS[args.timestamp_granularity]
    cases_list = []
    for path in args.logs:
        cases = parse_ocel(path, args.flatten_type, args.use_handovers, timestamp_granularity=granularity)
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
