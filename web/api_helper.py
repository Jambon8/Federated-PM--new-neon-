"""Structured parser for the process-mining MPC output.

This module is the single place that turns a run's stdout into structured
results. The Flask UI (``app.py``) and the correctness checker
(``eval/correctness.py``) both call :func:`parse_output`; ``decode_output.py``
stays the CLI pretty-printer over the same output.

Only party 0's live output reaches the driver's stdout (see
``vendor/ProgramFiles/MPSPDZClient.py``), so the communication figures parsed here are
party-0 send volumes.
"""

import functools
import json
import os
import re

#: Separates the streamed run log from the JSON payload appended by /api/run.
RESULT_JSON_SENTINEL = "===NEON_RESULT_JSON==="

DEFAULT_MAP_FILE = "Player-Data/activity_map.json"
PROGRAM_NAME = "process_mining"

# Regex Patterns (compiled once)
START_PATTERN = re.compile(r"RAW_RESULT Count:\s*(\d+)\s*Trace:")
END_PATTERN = re.compile(r"END_TRACE")
TIME_PATTERN = re.compile(r"Time = ([\d\.]+) seconds")
TIMER_PATTERN = re.compile(r"Time(\d+)\s*=\s*([\d\.e\-\+]+)\s*seconds")
TIMER_STOP_PATTERN = re.compile(
    r"Stopped timer (\d+) at ([\d\.e\-\+]+) \(([\d\.e\-\+]+) MB, (\d+) rounds\)")
NAMED_TIMER_PATTERN = re.compile(
    r'Timer "([^"]+)" took on average ([\d\.e\-\+]+) seconds')
DATA_PATTERN = re.compile(r"Data sent = ([\d\.]+) MB")
GLOBAL_PATTERN = re.compile(r"Global data sent = ([\d\.]+) MB")
ROUNDS_PATTERN = re.compile(r"^Rounds:\s*(\d+)")
OTHERS_PATTERN = re.compile(r"Others count \(below threshold\):\s*(\d+)")
DP_PATTERN = re.compile(r"DP_APPLIED Epsilon:(\d+)/(\d+)(?: K:(\d+))?")
DP_CALIBRATION_PATTERN = re.compile(r"DP partition selection:\s*(.+)$")
FINAL_CONFIG_PATTERN = re.compile(r"Final Config:\s*(.+)$")
PROTOCOL_PATTERN = re.compile(r"^Protocol:\s*(\S+)")
NETWORK_PATTERN = re.compile(r"^Applying Network Preset:\s*(\S+)")
HANDOVER_PATTERN = re.compile(
    r"(?:Derived|Loaded) global handover list H[^:]*:\s*(\d+) activities")
RUNTIME_PATTERN = re.compile(r"^Runtime:\s*([\d\.]+)")
WALL_PATTERN = re.compile(r"^Total \(compile \+ run\):\s*([\d\.]+)")

# Activity decoder ring emitted by import_xes.py
RING_BEGIN = "--- ACTIVITY DECODER RING ---"
RING_END = "-----------------------------"
RING_ENTRY_PATTERN = re.compile(r"^ID (\d+): '(.*)'$")

# MP-SPDZ log prefixes such as "INFO (Local Client): "
_LOG_PREFIX_PATTERN = re.compile(r'^(?:INFO|DEBUG|WARNING|ERROR)\s*\([^)]*\):\s*')


def _as_float(value):
    """Parse a measurement, treating an unreadable one as zero."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_lines(output):
    """Accept either a list of lines or one blob of text."""
    if isinstance(output, str):
        return output.split("\n")
    return output


def load_activity_map(map_file=DEFAULT_MAP_FILE):
    """Loads the activity map from JSON file."""
    if not os.path.exists(map_file):
        return {}

    try:
        with open(map_file, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def extract_activity_map(output_lines):
    """Recover the activity map from the decoder ring inside the output.

    Preferred over the JSON file because it belongs to the run being parsed:
    re-decoding an earlier run stays correct after a later run overwrites
    ``Player-Data/activity_map.json``.
    """
    act_map = {}
    in_ring = False
    for line in _as_lines(output_lines):
        line = _LOG_PREFIX_PATTERN.sub('', line.strip())
        if line == RING_BEGIN:
            in_ring = True
            continue
        if in_ring:
            if line.startswith(RING_END):
                in_ring = False
                continue
            match = RING_ENTRY_PATTERN.match(line)
            if match:
                act_map[match.group(1)] = match.group(2)
    return act_map


def load_reversal_map(party_indices=None, reveal_all=False):
    """Load the private per-party fingerprint reversal tables.

    Delegates to ``decode_output`` so the UI reveals exactly what
    ``decode_output.py --reveal-from P`` reveals. Hidden by default: with no
    party selected the map is empty and fingerprint labels stay opaque.
    """
    if not party_indices and not reveal_all:
        return {}
    try:
        from pipeline import decode_output
    except Exception:
        return {}
    return decode_output.load_reversal_map(party_indices, reveal_all)


def _parse_timer_comment(line):
    """Split a ``#neon_timer:`` comment into (timer token, display name).

    NEON's own parser expects ``#neon_timer: <timer> <display name>``, while
    ``process_mining.mpc`` writes the display name first, which is why NEON
    logs the bare variable name. Locating the numeric/``NEON_`` token instead
    of trusting the position accepts either order.
    """
    parts = [p for p in line.split(' ')[1:] if p]
    for i, token in enumerate(parts):
        if token.isnumeric() or token.startswith('NEON_'):
            return token, ' '.join(parts[:i] + parts[i + 1:]).strip()
    return None, None


def _timer_names_from_source(program=PROGRAM_NAME):
    """Map timer id to its variable name and display name from the .mpc source.

    Reproduces the auto-timer rule of
    ``ProgramHandler.determine_program_timers``: explicitly numbered timers
    keep their id, and every ``NEON_TIMER_*`` variable is assigned an id in
    source order starting after the highest explicit one. Parsing the source
    keeps output decoding independent of the MPC toolchain being installed, and
    recovers the display names as well as the variable names NEON logs.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mpc_path = os.path.join(project_root, "mpc", program + ".mpc")
    explicit = {}
    variable_to_display = {}
    autotimers = []
    used_timers = []

    try:
        with open(mpc_path) as f:
            source_lines = f.readlines()
    except OSError:
        return {}

    for line in source_lines:
        line = line.lstrip('\t ').rstrip('\n\r')

        if line.lower().startswith('#neon_timer'):
            token, display = _parse_timer_comment(line)
            if token is None:
                continue
            if token.isnumeric():
                explicit[int(token)] = display
                used_timers.append(int(token))
            else:
                variable_to_display[token[5:]] = display

        elif "start_timer(" in line:
            parameter = line.split("start_timer(")[1]
            parameter = parameter[:parameter.index(')')]
            if parameter.isnumeric():
                used_timers.append(int(parameter))
            elif parameter.startswith('NEON_'):
                autotimers.append(parameter[5:])

    timers = {}
    for tid, display in explicit.items():
        timers[tid] = {"variable": str(tid), "display": display or f"Timer {tid}"}

    auto_start = max(used_timers) + 1 if used_timers else 1
    for i, variable in enumerate(autotimers):
        timers[auto_start + i] = {
            "variable": variable,
            "display": variable_to_display.get(variable) or variable,
        }
    return timers


@functools.lru_cache(maxsize=4)
def load_timer_names(program=PROGRAM_NAME):
    """Timer id to ``{"variable", "display"}``, following NEON's numbering."""
    return _timer_names_from_source(program)


# The thesis names the six pipeline stages in Chapter 4; the MPC program's timer
# labels are implementation names. Reporting the thesis name keeps the tool and
# the write-up in one vocabulary. Unmapped timers keep their program label.
THESIS_STAGE_NAMES = {
    "Reading Inputs": (1, "Input Encoding"),
    "PSI Bitonic Merge": (2, "Log Merging on Case Identifiers"),
    "Reconstruction": (3, "Trace Reconstruction"),
    "Hashing": (4, "Trace Hashing"),
    "Grouping": (5, "Grouping and Frequency Counting"),
    "DP Noise": (6, "Noisy Counts"),
    "Output Reveal": (6, "Thresholded Release"),
}


def _build_steps(stop_data, legacy_times, named_times, timers, instrumented=True):
    """Merge the per-timer measurements into one ordered stage list.

    ``instrumented`` is False for protocols whose VM reports no per-timer
    communication (rep-bin reports 0 rounds and 0 MB for every timer). Those
    columns are then reported as unavailable rather than as a measured zero.
    """
    steps = []
    all_ids = set(stop_data) | set(legacy_times)
    labels = {tid: timers.get(tid, {}).get("display", f"Timer {tid}")
              for tid in all_ids}

    for tid in sorted(all_ids):
        name = labels[tid]
        variable = timers.get(tid, {}).get("variable")
        measured = stop_data.get(tid, {})
        # NEON reports averages by timer name; MP-SPDZ reports per-stop values.
        time = (named_times.get(variable) or named_times.get(name)
                or legacy_times.get(tid) or measured.get("time"))
        # A timer whose name extends another timer's name measures a part of it
        # (for example "Grouping Sort" inside "Grouping").
        nested = any(other != name and name.startswith(other + " ")
                     for other in labels.values())
        stage, thesis_name = THESIS_STAGE_NAMES.get(name, (None, name))
        steps.append({
            "id": tid,
            "name": thesis_name,
            "stage": stage,
            "timer": name,
            "time": (time + "s") if time else None,
            "rounds": measured.get("rounds") if instrumented else None,
            "data": (measured.get("data") + " MB")
                    if (instrumented and measured.get("data")) else None,
            "nested": nested,
        })
    return steps


def parse_output(output_lines, map_file=DEFAULT_MAP_FILE, reversal_map=None):
    """
    Parses the stdout lines from the process mining execution.

    Returns a dictionary with 'benchmarks', 'results', 'steps', 'config',
    'dp' and 'handover'. ``reversal_map`` maps a fingerprint label to the
    internal subtrace it replaced; each revealed label is spliced back in as
    sequential steps, matching ``decode_output.decode_stream``.
    """
    lines = _as_lines(output_lines)
    act_map = extract_activity_map(lines) or load_activity_map(map_file)
    reversal_map = reversal_map or {}

    benchmarks = {}
    config = {}
    dp = {}
    handover = {}
    stop_data = {}
    legacy_times = {}
    named_times = {}
    results = []
    total_rounds = None

    current_trace = []  # list of (act_id, conc_bit)
    current_count = 0
    in_trace = False

    for line in lines:
        line = line.strip()
        line = _LOG_PREFIX_PATTERN.sub('', line)

        # --- Benchmarks ---
        t_match = TIME_PATTERN.search(line)
        if t_match:
            benchmarks['Total Time'] = t_match.group(1) + "s"
            continue

        stop_match = TIMER_STOP_PATTERN.search(line)
        if stop_match:
            stop_data[int(stop_match.group(1))] = {
                "time": stop_match.group(2),
                "data": stop_match.group(3),
                "rounds": int(stop_match.group(4)),
            }
            continue

        named_match = NAMED_TIMER_PATTERN.search(line)
        if named_match:
            named_times[named_match.group(1)] = named_match.group(2)
            continue

        timer_match = TIMER_PATTERN.search(line)
        if timer_match:
            legacy_times[int(timer_match.group(1))] = timer_match.group(2)
            continue

        d_match = DATA_PATTERN.search(line)
        if d_match:
            benchmarks['Data Sent (Party 0)'] = d_match.group(1) + " MB"
            continue

        g_match = GLOBAL_PATTERN.search(line)
        if g_match:
            benchmarks['Global Data Sent'] = g_match.group(1) + " MB"
            continue

        r_match = ROUNDS_PATTERN.search(line)
        if r_match:
            total_rounds = int(r_match.group(1))
            continue

        # --- Run provenance ---
        cfg_match = FINAL_CONFIG_PATTERN.search(line)
        if cfg_match:
            for part in cfg_match.group(1).split(","):
                if "=" in part:
                    key, _, value = part.partition("=")
                    config[key.strip()] = value.strip()
            continue

        proto_match = PROTOCOL_PATTERN.search(line)
        if proto_match:
            config['Protocol'] = proto_match.group(1)
            continue

        net_match = NETWORK_PATTERN.search(line)
        if net_match:
            config['Network'] = net_match.group(1)
            continue

        run_match = RUNTIME_PATTERN.search(line)
        if run_match:
            config['Runtime'] = run_match.group(1) + "s"
            continue

        wall_match = WALL_PATTERN.search(line)
        if wall_match:
            config['Total (compile + run)'] = wall_match.group(1) + "s"
            continue

        h_match = HANDOVER_PATTERN.search(line)
        if h_match:
            handover['activities'] = int(h_match.group(1))
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
                # Build structured trace: list of steps, each step is a list of
                # activity names. Concurrent events share a step.
                steps = []
                current_set = []
                for act_id, conc_bit in current_trace:
                    if act_id == "0":
                        continue
                    name = act_map.get(act_id, f"Unknown({act_id})")
                    expanded = reversal_map.get(name)
                    if expanded is not None:
                        # Reveal: splice the internal subtrace back in as
                        # sequential (non-concurrent) steps in place of the label.
                        if current_set:
                            steps.append(current_set)
                            current_set = []
                        for internal_act in expanded:
                            steps.append([internal_act])
                        continue
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
            eps_num, eps_den = int(dp_match.group(1)), int(dp_match.group(2))
            eps = eps_num / eps_den
            k = int(dp_match.group(3)) if dp_match.group(3) else None
            dp_str = f"epsilon={eps}"
            if k is not None:
                dp_str += f", k={k}"
            benchmarks['Differential Privacy'] = dp_str
            dp.update({
                "epsilon": eps,
                "epsilon_exact": f"{eps_num}/{eps_den}",
                "k": k,
            })

        # DP calibration echoed by the driver before the run
        calib_match = DP_CALIBRATION_PATTERN.search(line)
        if calib_match:
            for part in calib_match.group(1).split(","):
                if "=" in part:
                    key, _, value = part.partition("=")
                    dp[key.strip()] = value.strip()

    # A protocol that reports 0 rounds and 0 MB for every timer does not
    # instrument per-stage communication; a genuine zero coexists with nonzero
    # measurements elsewhere (Stage 4 is local-only under semi).
    instrumented = any(entry["rounds"] or _as_float(entry["data"])
                       for entry in stop_data.values())

    steps = _build_steps(stop_data, legacy_times, named_times, load_timer_names(),
                         instrumented)

    if total_rounds is None and stop_data and instrumented:
        total_rounds = sum(entry["rounds"] for entry in stop_data.values())
    if total_rounds is not None:
        benchmarks['Total Rounds'] = total_rounds

    if dp.get("k") is not None:
        # The (eps,delta)-DP release rule is "noisy count > k", so the program
        # runs with threshold k+1.
        dp["threshold"] = dp["k"] + 1

    return {
        "benchmarks": benchmarks,
        "results": results,
        "steps": steps,
        "config": config,
        "dp": dp or None,
        "handover": handover or None,
    }
