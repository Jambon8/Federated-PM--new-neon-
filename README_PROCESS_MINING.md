# Privacy-Aware Federated Process Mining

Secure multi-party computation of process mining over event logs from two organisations, using the [NEON](README.md) framework on top of MP-SPDZ.

Two parties each hold a private event log (XES or OCEL format). The protocol jointly computes the set of common process traces and their frequencies without either party learning the other's raw data.

---

## Requirements

- Python 3.10+
- Python packages: `flask` (for the web UI only)
- MP-SPDZ 0.4.2 — already installed at `temp/MP/mp-spdz-0.4.2/`

---

## Running from the command line

**Always run from the `neon_new/neon/` directory.**

```bash
cd /home/jamil/Documents/neon_new/neon

python3 examples/run_process_mining.py \
  --log-a /path/to/OrgA/log.xes.gz \
  --log-b /path/to/OrgB/log.xes.gz
```

### All options

| Flag | Default | Description |
|---|---|---|
| `--log-a` | *(BPI 2013 OrgA)* | Path to Organisation A's event log (`.xes`, `.xes.gz`, or `.json` for OCEL) |
| `--log-b` | *(BPI 2013 OrgB)* | Path to Organisation B's event log |
| `--threshold` | `1` | Minimum count for a trace to appear in the output |
| `--threads` | `16` | Number of threads for the MPC computation |
| `--k-anon` | `0` | Enable k-anonymity (`1` to enable) |
| `--mode` | `local` | `local` (single machine) or `local-virtual` (simulated network) |
| `--network` | — | Network preset: `unlimited`, `lan`, `wan-ent`, `wan-fast`, `wan-slow`, `5g-avg`, `5g-slow` |
| `--delay` | — | Manual latency override, e.g. `20ms` (overrides `--network` if both set) |
| `--compile-looping` | off | Pass `-l` to MP-SPDZ compiler — speeds up compilation of loop-heavy programs |
| `--direct` | off | Pass `--direct` to MP-SPDZ runtime — direct party-to-party communication (faster on LAN) |
| `--use-handovers` | off | Only include handover synchronisation events, not all events |
| `--is-ocel` | off | Force OCEL parsing (auto-detected from `.json` extension) |
| `--flatten-type` | `Container` | Object type to flatten an OCEL log on |

### Examples

```bash
# XES logs, default settings
python3 examples/run_process_mining.py \
  --log-a /data/OrgA/log.xes.gz \
  --log-b /data/OrgB/log.xes.gz

# With k-anonymity threshold of 5
python3 examples/run_process_mining.py \
  --log-a /data/OrgA/log.xes.gz \
  --log-b /data/OrgB/log.xes.gz \
  --threshold 5 --k-anon 1

# Simulated WAN network, faster compilation
python3 examples/run_process_mining.py \
  --log-a /data/OrgA/log.xes.gz \
  --log-b /data/OrgB/log.xes.gz \
  --mode local-virtual --network wan-fast \
  --compile-looping --direct

# OCEL log
python3 examples/run_process_mining.py \
  --log-a /data/OrgA/ocel.json \
  --log-b /data/OrgB/ocel.json \
  --flatten-type Container
```

---

## Running the web UI

```bash
cd /home/jamil/Documents/neon_new/neon
python3 app.py
```

Open `http://localhost:8000` in a browser. The UI lets you browse for log files, set all the same options as the CLI, and streams output live.

---

## Output

The script prints:

- **Activity decoder ring** — mapping of integer IDs to activity names
- **Per-trace results** — count and sequence of activities for each common trace above the threshold
- **Benchmarks** — total runtime, per-step timings, data sent

To decode raw MP-SPDZ output manually:

```bash
python3 decode_output.py <output_file>
# or pipe:
some_command | python3 decode_output.py
```

---

## Input log formats

### XES / XES.GZ

Standard XES event log format. Both plain `.xes` and gzip-compressed `.xes.gz` are supported.

### OCEL (JSON)

OCEL 2.0 JSON format. Use `--flatten-type` to choose which object type defines the cases (default: `Container`).

---

## File structure

```
neon_new/neon/
├── examples/run_process_mining.py   # Main entry point
├── Programs/process_mining.mpc      # MPC program (MP-SPDZ)
├── import_xes.py                    # XES log parser + input encoder
├── import_ocel.py                   # OCEL log parser + input encoder
├── decode_output.py                 # CLI output decoder
├── app.py                           # Flask web server
├── api_helper.py                    # Output parsing for the web UI
├── templates/index.html             # Web UI
├── static/css/style.css             # Web UI styles
├── ProgramFiles/                    # NEON library
├── temp/MP/mp-spdz-0.4.2/          # MP-SPDZ installation
└── Player-Data/                     # Runtime I/O (inputs, outputs, keys)
```
