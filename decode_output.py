import json
import sys
import re
import os

# --- Configuration ---
MAP_FILE = "Player-Data/activity_map.json"

def load_activity_map():
    if not os.path.exists(MAP_FILE):
        print(f"Error: {MAP_FILE} not found. Run import_xes.py first.", file=sys.stderr)
        sys.exit(1)
    
    with open(MAP_FILE, 'r') as f:
        # JSON keys are strings ("1", "2"), we verify this structure
        return json.load(f)

def decode_stream(input_stream):
    act_map = load_activity_map()
    
    # Regex to find the start of a result block
    # Matches: "RAW_RESULT Count:5 Trace:"
    # Regex to find the start of a result block
    start_pattern = re.compile(r"RAW_RESULT Count:\s*(\d+)\s*Trace:")
    end_pattern = re.compile(r"END_TRACE")

    # Regex for Benchmarks
    time_pattern = re.compile(r"Time = ([\d\.]+) seconds")
    timer_pattern = re.compile(r"Time(\d+)\s*=\s*([\d\.e\-\+]+)\s*seconds")
    data_pattern = re.compile(r"Data sent = ([\d\.]+) MB")
    global_pattern = re.compile(r"Global data sent = ([\d\.]+) MB")
    others_pattern = re.compile(r"Others count \(below threshold\):\s*(\d+)")
    
    benchmarks = {}
    step_times = {}

    current_trace = []
    current_count = 0
    in_trace = False

    print(f"{'COUNT':<8} | {'TRACE'}")
    print("-" * 60)

    for line in input_stream:
        line = line.strip()
        
        # Capture Benchmarks
        t_match = time_pattern.search(line)
        if t_match:
            benchmarks['Total Time'] = t_match.group(1) + "s"
            continue

        timer_match = timer_pattern.search(line)
        if timer_match:
            step_id = int(timer_match.group(1))
            duration = timer_match.group(2)
            step_times[step_id] = duration + "s"
            continue
            
        d_match = data_pattern.search(line)
        if d_match:
            benchmarks['Data Sent (Party 0)'] = d_match.group(1) + " MB"
            continue
            
        g_match = global_pattern.search(line)
        if g_match:
            benchmarks['Global Data Sent'] = g_match.group(1) + " MB"
            continue
        
        # Check for start of trace
        match = start_pattern.search(line)
        if match:
            current_count = match.group(1)
            current_trace = []
            in_trace = True
            continue
            
        # Check for end of trace
        if end_pattern.search(line):
            if in_trace:
                # Filter out padding (0s) and decode
                readable_trace = []
                for act_id in current_trace:
                    if act_id == "0":
                        continue # Skip padding
                    
                    # Lookup ID in map, default to "Unknown(ID)" if missing
                    name = act_map.get(act_id, f"Unknown({act_id})")
                    readable_trace.append(name)
                
                # Print formatted result
                trace_str = " -> ".join(readable_trace)
                print(f"{current_count:<8} | {trace_str}")
                
            in_trace = False
            continue
            
        # Collect Activity IDs
        if in_trace:
            if line.isdigit():
                current_trace.append(line)

        # Check for Others count
        others_match = others_pattern.search(line)
        if others_match:
            count = others_match.group(1)
            print(f"{count:<8} | {'<Others (Below Threshold)>'}")

    # Print Benchmarks Footer
    if benchmarks or step_times:
        print("-" * 60)
        print("BENCHMARKS:")
        for k, v in benchmarks.items():
            print(f"{k:<25}: {v}")
        
        if step_times:
            print("-" * 30)
            print("STEP TIMINGS:")
            for step_id in sorted(step_times.keys()):
                print(f"  Step {step_id:<20}: {step_times[step_id]}")
    print("-" * 60)

if __name__ == "__main__":
    # Read from File if provided, otherwise Standard Input (Pipe)
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            decode_stream(f)
    else:
        decode_stream(sys.stdin)