# Privacy-Aware Federated Process Mining

Secure multi-party computation of trace variants over event logs held by two or more organizations, built on the [NEON](README_NEON.md) framework on top of MP-SPDZ.

Each party holds a private event log (XES). The protocol jointly computes the trace variants the parties share and their frequencies, without any party learning another's raw log. The same parameterized pipeline runs for every party count N ≥ 2.

This is the implementation artifact of a master's thesis at the [PADS](https://www.pads.rwth-aachen.de/) group, RWTH Aachen University.

---

## Requirements

- Python 3.10+
- `tqdm` — required by the NEON runtime
- `flask` — web UI only
- `pm4py`, `scipy`, `pandas`, `matplotlib` — evaluation harness and figures only
- MP-SPDZ 0.4.2

MP-SPDZ is not vendored. Install it into `temp/MP/mp-spdz-0.4.2/` with:

```bash
python3 setup.py install-mpspdz
```

---

## Running from the command line

**Always run from the repository root.**

```bash
python3 examples/run_process_mining.py \
  --log-a data/Master_Input/OrgA/BPI_Challenge_2013_open_problems.xes.gz \
  --log-b data/Master_Input/OrgB/BPI_Challenge_2013_open_problems.xes.gz
```

For more than two parties, pass one log per party:

```bash
python3 examples/run_process_mining.py --logs \
  data/bpi13_open/split_3/party_0.xes \
  data/bpi13_open/split_3/party_1.xes \
  data/bpi13_open/split_3/party_2.xes
```

### Inputs

| Flag | Default | Description |
|---|---|---|
| `--log-a`, `--log-b` | — | Two-party form: one event log per party (`.xes` or `.xes.gz`) |
| `--logs` | — | N-party form: one log path per party, minimum 2 |
| `--timestamp-granularity` | `ms` | Round timestamps to `ms` (no rounding), `s`, `m`, or `h` before encoding. Every party must pass the same value |
| `--n-per-party-cap` | — | Subsample to at most N case identifiers shared across parties |
| `--force-partial-len` | — | Truncate every party-local trace to P events and pad rows to exactly `PARTIAL_LEN=P`, pinning the circuit width |
| `--seed` | `42` | Seed for subsampling |

### Release regime

The default regime filters on frequency. Differential privacy is an optional strengthening of the release step.

| Flag | Default | Description |
|---|---|---|
| `--threshold` | `1` | Minimum count for a variant to be released |
| `--k-anon` | `0` | Enable k-anonymity (`1` to enable) |
| `--enable-dp` | `0` | Enable the (ε, δ)-DP release (`1` to enable) |
| `--epsilon` | `1.0` | DP privacy budget ε |
| `--dp-delta` | `0.01` | DP failure probability δ; the threshold k is calibrated from (ε, δ) |

### Protocol and execution

| Flag | Default | Description |
|---|---|---|
| `--protocol` | `semi` | `semi`, `rep-bin`, `mal-rep-bin`, `ps-rep-bin`, `ccd`, `mal-ccd`. The `rep-bin` family is three-party only; `ccd` requires an honest majority |
| `--threads` | `16` | Threads for the MPC computation |
| `--mode` | `local` | `local` (single machine) or `local-virtual` (simulated network via namespaces; needs `CAP_NET_ADMIN`, so run under `sudo`) |
| `--network` | — | Preset: `unlimited`, `lan`, `wan-ent`, `wan-fast`, `wan-slow`, `5g-avg`, `5g-slow` |
| `--delay` | — | Manual latency override, e.g. `20ms`; overrides `--network` |
| `--no-direct` | — | Disable MP-SPDZ `--direct`; direct party-to-party communication is on by default |
| `--compile-only` | off | Compile the MPC program and exit without running it |

### Optional features

| Flag | Default | Description |
|---|---|---|
| `--use-handovers` | off | Collapse each party's maximal runs of internal activities into keyed fingerprint events before secret sharing. Handover activities are kept verbatim, so the variant set is preserved while the encoded rows shrink |
| `--handover-activities` | — | Path to the global handover list H, one activity per line, applied identically by every party. Defaults to the union of activities flagged in the logs |
| `--partial-orders` | `0` | Treat events within a time window as concurrent instead of imposing the timestamp order (`1` to enable) |
| `--delta` | `0` | Concurrency window for `--partial-orders`: `0`, or e.g. `500ms`, `10s`, `1m`, `2h` |

### Examples

```bash
# k-anonymity at k = 5
python3 examples/run_process_mining.py \
  --log-a data/Master_Input/OrgA/Sepsis_Cases_OrgA.xes.gz \
  --log-b data/Master_Input/OrgB/Sepsis_Cases_OrgB.xes.gz \
  --threshold 5 --k-anon 1

# (eps, delta)-differentially private release
python3 examples/run_process_mining.py \
  --log-a data/Master_Input/OrgA/Sepsis_Cases_OrgA.xes.gz \
  --log-b data/Master_Input/OrgB/Sepsis_Cases_OrgB.xes.gz \
  --enable-dp 1 --epsilon 1.0 --dp-delta 0.001

# Simulated WAN
sudo -E python3 examples/run_process_mining.py \
  --log-a data/Master_Input/OrgA/RequestForPayment_OrgA.xes.gz \
  --log-b data/Master_Input/OrgB/RequestForPayment_OrgB.xes.gz \
  --mode local-virtual --network wan-fast
```

---

## Running the web UI

```bash
python3 app.py
```

Open `http://localhost:8000`. The UI exposes the same options as the CLI, browses for log files, and streams output live.

---

## Output

The run prints the activity decoder ring mapping integer identifiers to activity names, one line per released variant with its count and activity sequence, and a benchmark summary with per-stage timings and bytes sent.

To decode raw MP-SPDZ output:

```bash
python3 decode_output.py <output_file>
some_command | python3 decode_output.py
```

---

## Datasets

`data/` holds every event log the evaluation measures. The two-party logs are synthetic federations of public single-organization logs from the [4TU](https://data.4tu.nl/) and BPI Challenge archives: each log is split into an `OrgA` and an `OrgB` partition on a case attribute, so both partitions describe the same cases from different organizational perspectives. The three-, four-, and five-party splits under `data/bpi13_open/`, `data/bpi13_closed/`, `data/bpi13_incidents/`, `data/sepsis/`, and `data/e4b/` are derived from the same logs round-robin. Cite the original archive entry when reusing a log.

Logs the experiment registry never reads are not tracked. New logs are ignored by default, so add one explicitly:

```bash
git add -f data/<path>
```

---

## Evaluation

`eval/thesis_experiments.py` is the experiment registry; every reported measurement resolves to an entry there.

```bash
python3 eval/thesis_experiments.py --list   # registered experiments
python3 eval/thesis_experiments.py --experiment e2   # run one experiment
python3 eval/correctness.py                 # MPC output vs centralized pm4py baseline
python3 eval/dp_evaluation.py               # DP correctness, statistics, cost
python3 eval/privacy_utility.py             # privacy-utility trade-off over an epsilon grid
python3 eval/plotting/plot_performance.py   # figures
```

Results are written to `eval_results/` and are not tracked. The harness targets local execution.

---

## Tests

```bash
python3 -m unittest discover -s tests
```

---

## File structure

```
.
├── examples/run_process_mining.py   # Main entry point
├── Programs/process_mining.mpc      # MPC program (6-stage pipeline)
├── import_xes.py                    # XES parser + MPC input encoder
├── decode_output.py                 # CLI output decoder
├── app.py, api_helper.py            # Flask web server and output parsing
├── templates/, static/              # Web UI
├── ProgramFiles/                    # NEON library (see README_NEON.md)
│   └── dp_calibration.py            # (eps, delta) -> threshold calibration
├── eval/                            # Experiment registry, runners, plotting
├── tests/                           # Unit tests
├── data/                            # Event logs
├── setup.py                         # MP-SPDZ installation
├── temp/MP/mp-spdz-0.4.2/           # MP-SPDZ (installed, not tracked)
└── Player-Data/                     # Runtime I/O (inputs, outputs, keys)
```

---

## License

See [LICENSE.txt](LICENSE.txt). The event logs under `data/` are redistributed from their original archives under their own terms.
