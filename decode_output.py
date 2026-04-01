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
    dp_pattern = re.compile(r"DP_APPLIED Epsilon:(\d+)/(\d+)(?: K:(\d+))?")
    
    benchmarks = {}
    step_times = {}

    current_trace = []  # list of (act_id, conc_bit)
    current_count = 0
    in_trace = False

    print(f"{'COUNT':<8} | {'TRACE'}")
    print("-" * 60)

    for line in input_stream:
        line = line.strip()
        # Strip common log prefixes from dump.log files
        for prefix in ("INFO (Local Client): ", "ERROR (Local Client): "):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break

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
                # Build structured trace: list of steps, each step is a list of activity names
                steps = []
                current_set = []
                for act_id, conc_bit in current_trace:
                    if act_id == "0":
                        continue
                    name = act_map.get(act_id, f"Unknown({act_id})")
                    if conc_bit == "1" and current_set:
                        current_set.append(name)
                    else:
                        if current_set:
                            steps.append(current_set)
                        current_set = [name]
                if current_set:
                    steps.append(current_set)

                def format_step(step):
                    if len(step) == 1:
                        return step[0]
                    from collections import Counter
                    counts = Counter(step)
                    parts = []
                    for name in sorted(counts):
                        if counts[name] > 1:
                            parts.append(f"{name}^{counts[name]}")
                        else:
                            parts.append(name)
                    return "[" + ", ".join(parts) + "]"

                trace_str = " -> ".join(format_step(s) for s in steps)
                print(f"{current_count:<8} | {trace_str}")

            in_trace = False
            continue

        # Collect Activity IDs (two-column: "act_id conc_bit" or single: "act_id")
        if in_trace:
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                current_trace.append((parts[0], parts[1]))
            elif len(parts) == 1 and parts[0].isdigit():
                current_trace.append((parts[0], "0"))

        # Check for Others count
        others_match = others_pattern.search(line)
        if others_match:
            count = others_match.group(1)
            print(f"{count:<8} | {'<Others (Below Threshold)>'}")

        # Check for DP metadata
        dp_match = dp_pattern.search(line)
        if dp_match:
            eps = int(dp_match.group(1)) / int(dp_match.group(2))
            k = int(dp_match.group(3)) if dp_match.group(3) else None
            dp_str = f"epsilon={eps}"
            if k is not None:
                dp_str += f", k={k}"
            benchmarks['Differential Privacy'] = dp_str

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