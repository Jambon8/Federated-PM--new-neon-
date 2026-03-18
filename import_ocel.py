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

OUTPUT_A = os.path.join(BASE_DIR, "Player-Data/Input-P0-0")
OUTPUT_B = os.path.join(BASE_DIR, "Player-Data/Input-P1-0")
OUTPUT_A_BIN = os.path.join(BASE_DIR, "Player-Data/Input-Binary-P0-0")
OUTPUT_B_BIN = os.path.join(BASE_DIR, "Player-Data/Input-Binary-P1-0")

PAD_TIME = 2**60
PAD_ACT = 0
PAD_ID = 2**60

def parse_ocel(filepath, flatten_type="Container", use_handovers=False):
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
                timestamp = int(dt.timestamp())
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

def encode_and_save(cases_a, cases_b):
    cases_a.sort(key=lambda c: c['id'])
    cases_b.sort(key=lambda c: c['id'], reverse=True)

    all_case_ids = set(c['id'] for c in cases_a + cases_b)
    all_activities = set(e[1] for c in cases_a + cases_b for e in c['events'])
    
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

    max_len_a = max(len(c['events']) for c in cases_a) if cases_a else 0
    max_len_b = max(len(c['events']) for c in cases_b) if cases_b else 0
    max_len = max(max_len_a, max_len_b)
    
    n_a = len(cases_a)
    n_b = len(cases_b)
    n_max = max(n_a, n_b)
    
    print(f"Configuration for MPC: N_PER_PARTY={n_max}, PARTIAL_LEN={max_len}")
    
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
            
    write_party_file(OUTPUT_A, OUTPUT_A_BIN, cases_a, n_max, max_len, pad_at_start=False)
    write_party_file(OUTPUT_B, OUTPUT_B_BIN, cases_b, n_max, max_len, pad_at_start=True)
    
    print("Input files (text and binary) generated successfully.")
    return n_max, max_len

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("file_a")
    parser.add_argument("file_b")
    parser.add_argument("--use-handovers", action="store_true")
    # By default, flatten on "Container"
    parser.add_argument("--flatten-type", default="Container")
    args = parser.parse_args()

    log_a = parse_ocel(args.file_a, args.flatten_type, args.use_handovers)
    log_b = parse_ocel(args.file_b, args.flatten_type, args.use_handovers)
    
    if not log_a or not log_b:
        print("Failed to load logs.")
        sys.exit(1)

    n, length = encode_and_save(log_a, log_b)
    
    print("\n!!! UPDATE YOUR .mpc FILE WITH THESE VALUES !!!")
    print(f"N_PER_PARTY = {n}")
    print(f"PARTIAL_LEN = {length}")
    print(f"FULL_LEN = {length * 2}")
