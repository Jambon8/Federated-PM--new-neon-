import json
import os
import re
import subprocess
import sys
from fractions import Fraction

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
# The repository root for our own packages, and vendor/ for the NEON package.
sys.path[:0] = [PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'vendor')]

import api_helper  # noqa: E402

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Configuration
HOME_DIR = os.path.expanduser("~")

# Event logs, activity maps and handover lists (H is one activity per line).
BROWSABLE_SUFFIXES = ('.xes', '.xes.gz', '.json', '.txt')

# Where the file browser opens: the project's own log collection when present.
DEFAULT_BROWSE_PATH = (os.path.join(PROJECT_ROOT, "data")
                       if os.path.isdir(os.path.join(PROJECT_ROOT, "data")) else HOME_DIR)

# The delta grammar of pipeline/run.py (_parse_delta): exact
# timestamp equality, or a duration carrying an explicit unit.
DELTA_PATTERN = re.compile(r'^(?:0|[1-9]\d*(?:ms|s|m|h))$')
DELTA_UNITS = ['ms', 's', 'm', 'h']
GRANULARITIES = ['ms', 's', 'm', 'h']

# The protocols offered in the browser: a subset of the runner's --protocol
# choices, spanning the trust models the evaluation compares. Party limits are
# read from NEON below when it is importable; these are the documented defaults.
PROTOCOLS = [
    {"value": "semi", "label": "semi", "trust": "semi-honest, dishonest majority",
     "min_parties": 2, "max_parties": None},
    {"value": "rep-bin", "label": "rep-bin", "trust": "semi-honest, honest majority",
     "min_parties": 3, "max_parties": 3},
    {"value": "ccd", "label": "ccd", "trust": "semi-honest, honest majority",
     "min_parties": 3, "max_parties": None},
]

NETWORKS = [
    {"value": "unlimited", "label": "Unlimited (localhost)"},
    {"value": "lan", "label": "LAN"},
    {"value": "wan-ent", "label": "WAN Enterprise"},
    {"value": "wan-fast", "label": "WAN Fast"},
    {"value": "wan-slow", "label": "WAN Slow"},
    {"value": "5g-avg", "label": "5G Average"},
    {"value": "5g-slow", "label": "5G Slow"},
]

# NEON is optional at import time: parsing an output file and serving the form
# must work even where the MPC toolchain's dependencies are missing.
try:
    from ProgramFiles import protocol as neon_protocol
except Exception:
    neon_protocol = None

_NEON_PROTOCOL_ATTRS = {
    "semi": "Semi",
    "rep-bin": "ReplicatedBin",
    "ccd": "CCD",
}


def protocol_choices():
    """The offered protocols, with party limits taken from NEON when available."""
    choices = []
    for entry in PROTOCOLS:
        choice = dict(entry)
        neon_entry = getattr(neon_protocol, _NEON_PROTOCOL_ATTRS[entry["value"]], None)
        if neon_entry is not None:
            minimum = neon_entry.min_number_of_parties
            maximum = neon_entry.max_number_of_parties
            choice["min_parties"] = int(minimum)
            choice["max_parties"] = None if maximum == float('inf') else int(maximum)
        choices.append(choice)
    return choices


def check_party_count(protocol_value, n_parties):
    """Return an error message when the protocol rejects this party count."""
    limits = next((c for c in protocol_choices() if c["value"] == protocol_value), None)
    if limits is None:
        return f"Unknown protocol '{protocol_value}'."
    minimum, maximum = limits["min_parties"], limits["max_parties"]
    if n_parties < minimum:
        return (f"Protocol '{protocol_value}' needs at least {minimum} parties, "
                f"{n_parties} given.")
    if maximum is not None and n_parties > maximum:
        return (f"Protocol '{protocol_value}' supports exactly {maximum} parties, "
                f"{n_parties} given.")
    return None


def build_command(data):
    """Translate a run request into the driver's command line.

    Returns (command, error message). The error is a readable message for the
    browser, so a rejected combination never reaches argparse or MP-SPDZ.
    """
    logs = data.get('logs')
    log_a = data.get('log_a')
    log_b = data.get('log_b')

    if logs and len(logs) >= 2:
        log_paths = [p for p in logs if p]
    elif log_a and log_b:
        log_paths = [log_a, log_b]
    else:
        return None, "At least 2 log files required. Use 'logs' list or 'log_a'/'log_b'."

    if len(log_paths) < 2:
        return None, "At least 2 log files required (one per party)."

    missing = [p for p in log_paths if not os.path.exists(p)]
    if missing:
        return None, "Log file not found: " + ", ".join(missing)

    try:
        threshold = int(data.get('threshold', 1))
        threads = int(data.get('threads', 16))
        k_anon = int(data.get('k_anon', 0))
    except (TypeError, ValueError):
        return None, "Release threshold, threads and suppression counter must be integers."

    if threshold < 1 or threads < 1:
        return None, "Release threshold and threads must be at least 1."

    # The suppression counter belongs to the default release regime: under DP it
    # would publish an exact un-noised aggregate of the suppressed counts. The
    # circuit already gates it on `not ENABLE_DP`; dropping it here keeps the
    # echoed command honest about what runs.
    if data.get('enable_dp'):
        k_anon = 0

    mode = data.get('mode', 'local')
    if mode not in ('local', 'local-virtual'):
        return None, f"Unknown operation mode '{mode}'."

    protocol_value = data.get('protocol', 'semi')
    party_error = check_party_count(protocol_value, len(log_paths))
    if party_error:
        return None, party_error

    granularity = str(data.get('timestamp_granularity', 'ms'))
    if granularity not in GRANULARITIES:
        return None, f"Timestamp granularity must be one of {', '.join(GRANULARITIES)}."

    cmd = [
        "python3", "-u", "pipeline/run.py",
        "--logs", *log_paths,
        "--threshold", str(threshold),
        "--threads", str(threads),
        "--k-anon", str(k_anon),
        "--mode", mode,
        "--protocol", protocol_value,
        "--timestamp-granularity", granularity,
    ]

    if data.get('use_handovers'):
        cmd.append("--use-handovers")
        handover_list = data.get('handover_activities')
        if handover_list:
            if not os.path.exists(handover_list):
                return None, f"Handover list not found: {handover_list}"
            cmd.extend(["--handover-activities", handover_list])

    # Kept for API callers; the browser has no control for it.
    if not data.get('direct', True):
        cmd.append("--no-direct")

    if data.get('partial_orders'):
        delta = str(data.get('delta', '0')).strip()
        if not DELTA_PATTERN.match(delta):
            return None, (f"Invalid concurrency tolerance '{delta}'. Use 0 for exact "
                          f"timestamp equality, or a duration with a unit: "
                          f"500ms, 10s, 1m, 2h.")
        cmd.extend(["--partial-orders", "1", "--delta", delta])

    if data.get('enable_dp'):
        try:
            epsilon = float(data.get('epsilon', 1.0))
            dp_delta = float(data.get('dp_delta', 0.01))
        except (TypeError, ValueError):
            return None, "Epsilon and DP delta must be numbers."
        if epsilon <= 0:
            return None, "Epsilon must be positive."
        if not 0 < dp_delta < 1:
            return None, "DP delta must lie strictly between 0 and 1."
        cmd.extend(["--enable-dp", "1", "--epsilon", str(epsilon),
                    "--dp-delta", str(dp_delta)])

    network = data.get('network')
    if mode == "local-virtual" and network:
        if network not in [n["value"] for n in NETWORKS]:
            return None, f"Unknown network preset '{network}'."
        cmd.extend(["--network", network])

    return cmd, None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config', methods=['GET'])
def get_config():
    """Form options: protocols with their party limits, presets, stage names."""
    timers = api_helper.load_timer_names()
    return jsonify({
        "protocols": protocol_choices(),
        "networks": NETWORKS,
        "delta_units": DELTA_UNITS,
        "granularities": GRANULARITIES,
        "stages": [{"id": tid, "name": timers[tid]["display"]}
                   for tid in sorted(timers)],
        "result_sentinel": api_helper.RESULT_JSON_SENTINEL,
        "default_browse_path": DEFAULT_BROWSE_PATH,
    })


@app.route('/api/dp-preview', methods=['GET'])
def dp_preview():
    """Calibrate k exactly as the driver does, for display before a run."""
    try:
        epsilon = Fraction(str(request.args.get('epsilon', '1.0')))
        dp_delta = float(request.args.get('delta', 0.01))
    except (TypeError, ValueError, ZeroDivisionError):
        return jsonify({"error": "Epsilon and delta must be numbers."}), 400

    try:
        from pipeline.dp_calibration import calibrate_dp
        calibration = calibrate_dp(epsilon, dp_delta)
    except ImportError:
        return jsonify({"error": "DP calibration module unavailable."}), 503
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    return jsonify({
        "epsilon": f"{epsilon.numerator}/{epsilon.denominator}",
        "requested_delta": dp_delta,
        "ideal_delta": float(calibration.ideal_delta),
        "k": calibration.k,
        "threshold": calibration.k + 1,
        "grid_bits": calibration.grid_bits,
        "grid_delta_reserve": float(calibration.grid_delta_reserve),
    })


@app.route('/api/decode', methods=['POST'])
def decode_output_api():
    """Re-decode a finished run, optionally revealing party fingerprints."""
    data = request.json or {}
    raw = data.get('raw', '')
    reveal_from = data.get('reveal_from') or []

    try:
        parties = [int(p) for p in reveal_from]
    except (TypeError, ValueError):
        return jsonify({"error": "reveal_from must be a list of party indices."}), 400

    reversal = api_helper.load_reversal_map(parties)
    parsed = api_helper.parse_output(raw, reversal_map=reversal)
    parsed["revealed_parties"] = parties
    parsed["revealed_labels"] = len(reversal)
    return jsonify(parsed)


@app.route('/api/browse', methods=['GET'])
def browse_files():
    """Returns contents of a directory."""
    path = request.args.get('path', HOME_DIR)

    # Security check: prevent going above root (simple check)
    if not os.path.exists(path):
        return jsonify({"error": "Path does not exist"}), 404

    try:
        items = []
        # Add parent directory entry
        parent = os.path.dirname(path)
        if parent and parent != path:
            items.append({"name": "..", "path": parent, "type": "directory"})

        with os.scandir(path) as it:
            for entry in it:
                if entry.name.startswith('.'):
                    continue  # Skip hidden

                if entry.is_dir():
                    items.append({"name": entry.name, "path": entry.path, "type": "directory"})
                elif entry.is_file() and entry.name.endswith(BROWSABLE_SUFFIXES):
                    items.append({"name": entry.name, "path": entry.path, "type": "file"})

        # Sort: Directories first, then files
        items.sort(key=lambda x: (x['type'] != 'directory', x['name']))

        return jsonify({
            "current_path": path,
            "items": items
        })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/run', methods=['POST'])
def run_process_mining():
    data = request.json or {}

    cmd, error = build_command(data)
    if error:
        return jsonify({"error": error}), 400

    print(f"Running command: {' '.join(cmd)}")

    def generate():
        collected = []
        try:
            # We must use Popen to stream stdout
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                cwd=PROJECT_ROOT,
                bufsize=1  # Line buffered
            )

            # Yield output line by line
            for line in process.stdout:
                collected.append(line)
                yield line

            process.wait()
            if process.returncode != 0:
                yield f"\n[ERROR] Process exited with code {process.returncode}"

        except Exception as e:
            yield f"\n[EXCEPTION] {str(e)}"

        # The browser renders from this payload instead of parsing the log.
        parsed = api_helper.parse_output("".join(collected))
        parsed["command"] = cmd
        yield f"\n{api_helper.RESULT_JSON_SENTINEL}\n"
        yield json.dumps(parsed)

    return Response(stream_with_context(generate()), mimetype='text/plain')


if __name__ == '__main__':
    app.run(debug=False, port=8000)
