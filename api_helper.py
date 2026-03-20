import re
import os
import json
import sys

# Regex Patterns (compiled once)
START_PATTERN = re.compile(r"RAW_RESULT Count:\s*(\d+)\s*Trace:")
END_PATTERN = re.compile(r"END_TRACE")
TIME_PATTERN = re.compile(r"Time = ([\d\.]+) seconds")
TIMER_PATTERN = re.compile(r"Time(\d+)\s*=\s*([\d\.e\-\+]+)\s*seconds")
DATA_PATTERN = re.compile(r"Data sent = ([\d\.]+) MB")
GLOBAL_PATTERN = re.compile(r"Global data sent = ([\d\.]+) MB")
OTHERS_PATTERN = re.compile(r"Others count \(below threshold\):\s*(\d+)")
DP_PATTERN = re.compile(r"DP_APPLIED Epsilon:(\d+)/(\d+)")

def load_activity_map(map_file="Player-Data/activity_map.json"):
    """Loads the activity map from JSON file."""
    if not os.path.exists(map_file):
        return {}
    
    try:
        with open(map_file, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def parse_output(output_lines, map_file="Player-Data/activity_map.json"):
    """
    Parses the stdout lines from the process mining execution.
    Returns a dictionary with 'benchmarks' and 'results'.
    """
    act_map = load_activity_map(map_file)
    
    benchmarks = {}
    step_times = {}
    results = []
    
    current_trace = []  # list of (act_id, conc_bit)
    current_count = 0
    in_trace = False

    for line in output_lines:
        line = line.strip()

        # --- Benchmarks ---
        t_match = TIME_PATTERN.search(line)
        if t_match:
            benchmarks['Total Time'] = t_match.group(1) + "s"
            continue

        timer_match = TIMER_PATTERN.search(line)
        if timer_match:
            step_id = int(timer_match.group(1))
            duration = timer_match.group(2)
            step_times[step_id] = duration + "s"
            continue

        d_match = DATA_PATTERN.search(line)
        if d_match:
            benchmarks['Data Sent (Party 0)'] = d_match.group(1) + " MB"
            continue

        g_match = GLOBAL_PATTERN.search(line)
        if g_match:
            benchmarks['Global Data Sent'] = g_match.group(1) + " MB"
            continue

        # --- Results ---

        # Start of trace
        match = START_PATTERN.search(line)
        if match:
            current_count = match.group(1)
            current_trace = []
            in_trace = True
            continue

        # End of trace
        if END_PATTERN.search(line):
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

                results.append({
                    "count": int(current_count),
                    "trace": steps,
                    "type": "trace"
                })

            in_trace = False
            continue

        # Collect Activity IDs (two-column: "act_id conc_bit" or single: "act_id")
        if in_trace:
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                current_trace.append((parts[0], parts[1]))
            elif len(parts) == 1 and parts[0].isdigit():
                current_trace.append((parts[0], "0"))

        # Others count
        others_match = OTHERS_PATTERN.search(line)
        if others_match:
            count = others_match.group(1)
            results.append({
                "count": int(count),
                "trace": [["<Others (Below Threshold)>"]],
                "type": "others"
            })

        # DP metadata
        dp_match = DP_PATTERN.search(line)
        if dp_match:
            eps = int(dp_match.group(1)) / int(dp_match.group(2))
            benchmarks['Differential Privacy'] = f"epsilon={eps}"

    # Format step times
    if step_times:
        sorted_steps = []
        for step_id in sorted(step_times.keys()):
            sorted_steps.append({"step": step_id, "time": step_times[step_id]})
        benchmarks['Step Timings'] = sorted_steps

    return {
        "benchmarks": benchmarks,
        "results": results
    }
