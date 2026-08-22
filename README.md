# Privacy-Aware Federated Process Mining

Secure multi-party computation of trace variants over event logs held by two or more organizations, built on the [NEON](vendor/NEON.md) framework on top of MP-SPDZ.

Each party holds a private event log (XES). The protocol jointly computes the trace variants the parties share and their frequencies, without any party learning another's raw log. The same parameterized pipeline runs for every party count N ≥ 2.

This is the implementation artifact of a master's thesis at the [PADS](https://www.pads.rwth-aachen.de/) group, RWTH Aachen University.

---

## Installation

Python 3.10 or newer, plus `zstd` on the system path (NEON compresses its run
archives with it).

```bash
python3 -m venv fed_env
source fed_env/bin/activate
pip install -r requirements.txt        # protocol + web UI
pip install -r requirements-eval.txt   # adds the evaluation stack
```

The virtual environment is not optional on distributions that mark the system
interpreter as externally managed. Pinned versions are the ones every
measurement in the thesis was taken with.

MP-SPDZ 0.4.2 is not vendored. Install it into `vendor/temp/MP/mp-spdz-0.4.2/`:

```bash
python3 vendor/setup.py install-mpspdz
```

Check the install by running the protocol on two of the included logs:

```bash
python3 pipeline/run.py \
  --log-a data/2parties/bpi13_open/party_0.xes.gz \
  --log-b data/2parties/bpi13_open/party_1.xes.gz \
  --threshold 5
```

This releases 16 trace variants from 635 joint cases. The first run compiles the
MPC program, which takes a few seconds; later runs with the same program reuse
the compiled binary.

---

## Running from the command line

**Always run from the repository root.**

```bash
python3 pipeline/run.py \
  --log-a data/2parties/bpi13_open/party_0.xes.gz \
  --log-b data/2parties/bpi13_open/party_1.xes.gz
```

For more than two parties, pass one log per party:

```bash
python3 pipeline/run.py --logs \
  data/3parties/bpi13_open/party_0.xes \
  data/3parties/bpi13_open/party_1.xes \
  data/3parties/bpi13_open/party_2.xes
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
python3 pipeline/run.py \
  --log-a data/2parties/sepsis/party_0.xes.gz \
  --log-b data/2parties/sepsis/party_1.xes.gz \
  --threshold 5 --k-anon 1

# (eps, delta)-differentially private release
python3 pipeline/run.py \
  --log-a data/2parties/sepsis/party_0.xes.gz \
  --log-b data/2parties/sepsis/party_1.xes.gz \
  --enable-dp 1 --epsilon 1.0 --dp-delta 0.001

# Simulated WAN
sudo -E python3 pipeline/run.py \
  --log-a data/2parties/requestforpayment/party_0.xes.gz \
  --log-b data/2parties/requestforpayment/party_1.xes.gz \
  --mode local-virtual --network wan-fast
```

---

## Running the web UI

```bash
python3 web/app.py
```

Open `http://localhost:8000`. The UI exposes the same options as the CLI, browses for log files, and streams output live.

---

## Output

The run prints the activity decoder ring mapping integer identifiers to activity names, one line per released variant with its count and activity sequence, and a benchmark summary with per-stage timings and bytes sent.

To decode raw MP-SPDZ output:

```bash
python3 pipeline/decode_output.py <output_file>
some_command | python3 pipeline/decode_output.py
```

---

## Datasets

`data/` is organized by party count: `data/2parties/`, `data/3parties/`, `data/4parties/`, and `data/5parties/`, each holding one directory per dataset with exactly one log per party (`party_0`, `party_1`, ...). Only the datasets the evaluation measures are kept.

The two-party logs are synthetic federations of public single-organization logs from the [4TU](https://data.4tu.nl/) and BPI Challenge archives: each log is split into two partitions on a case attribute, so both describe the same cases from different organizational perspectives. The three-, four-, and five-party logs derive from the same sources round-robin. The `e4b_*` directories hold the controlled scaling study, named for their cases per party: `e4b_sepsis_c500` runs at 2 to 5 parties, and `e4b_sepsis_c750`, `_c1000`, `_c1250` are its two-party controls, matched on total row count. `data/e4b_meta.json` records how they were built.

[data/PROVENANCE.md](data/PROVENANCE.md) maps every dataset directory to the archive entry it came from. Cite that entry when reusing a log.

Only logs the experiment registry reads are tracked. New logs are ignored by default, so add one explicitly:

```bash
git add -f data/<path>
```

---

## Evaluation

`eval/thesis_experiments.py` is the experiment registry: every measurement
reported in the thesis resolves to one run ID there. A run is executed by ID and
writes one JSON file per run under `eval_results/<experiment>/`.

```bash
python3 eval/thesis_experiments.py --count       # runs per experiment
python3 eval/thesis_experiments.py --list        # one shell command per run
python3 eval/thesis_experiments.py --list --experiment e2   # just one experiment
python3 eval/thesis_experiments.py --run e1__bpi13_open__default__rep0
python3 eval/thesis_experiments.py --aggregate   # collect the JSONs into one CSV per experiment
```

The full registry is 888 runs. `--list` prints them so a subset can be selected
or fed to a job scheduler; `--aggregate` then reduces whatever completed into
`eval_results/<experiment>.csv`, which is what the tables and figures read.

Correctness is checked independently of the harness, by reconstructing the
expected release from the party logs directly and comparing it against the
stored MPC output:

```bash
python3 eval/verify_e1_independent.py   # variants and counts vs the two local logs
python3 eval/verify_e4_splits.py        # N-party splits match their generator
python3 eval/verify_e4b_outputs.py      # every party count releases the same variants
python3 eval/verify_e10_outputs.py      # the three protocols release identically
python3 eval/verify_dp_calibration.py   # calibrated k against the sampler's grid
```

Figures are generated from the aggregated CSVs:

```bash
python3 eval/plotting/plot_performance.py
python3 eval/plotting/plot_scaling.py
python3 eval/plotting/plot_dp.py
python3 eval/plotting/plot_privacy.py
```

Standalone drivers exist for individual studies outside the registry:
`eval/correctness.py` (MPC output against the centralized baseline in
`eval/baseline.py`), `eval/dp_evaluation.py` (DP correctness, statistics, cost),
and `eval/privacy_utility.py` (privacy-utility trade-off over an epsilon grid).

### What is tracked

The aggregated results the thesis reports are in the repository: one CSV per
experiment, the verifier outputs, the generated figure data under
`eval_results/{scaling_plots,stage_breakdown}/`, and
`eval_results/ch6_provenance_manifest.json`, which records the SHA-256 of every
input log, run result, and verifier output behind the evaluation chapter.
Regenerate the manifest with `python3 eval/write_ch6_provenance.py`.

Every number in the evaluation tables and figures is reproducible from those
CSVs. The raw per-run JSONs they aggregate are not tracked; re-running an
experiment regenerates them.

---

## Tests

```bash
python3 -m unittest discover -s tests
```

---

## File structure

Our contribution and the vendored dependencies are separated at the top level:
everything under `vendor/` was written by someone else.

```
.
├── mpc/process_mining.mpc           # The MPC program: six-stage pipeline
├── pipeline/                        # One run, end to end
│   ├── run.py                       #   entry point: configure, compile, execute
│   ├── import_xes.py                #   XES parser + MPC input encoder
│   ├── decode_output.py             #   release decoder (CLI)
│   ├── dp_calibration.py            #   (eps, delta) -> threshold calibration
│   └── generate_test_data.py        #   N-party splits from one log
├── web/                             # Browser tool
│   ├── app.py, api_helper.py        #   Flask server and output parsing
│   └── templates/, static/
├── eval/                            # Experiment registry, runners, plotting
├── tests/                           # Unit tests
├── requirements.txt                 # Protocol + web UI
├── requirements-eval.txt            # Adds the evaluation stack
├── data/<n>parties/<dataset>/       # Event logs, one per party
├── docs/                            # Design notes behind Chapters 4 and 5
│
├── vendor/                          # Not ours — see vendor/NEON.md
│   ├── ProgramFiles/                #   NEON (Klinger et al., CODASPY 2024)
│   ├── config/                      #   NEON configuration
│   ├── setup.py                     #   NEON's MP-SPDZ installer
│   ├── temp/MP/mp-spdz-0.4.2/       #   MP-SPDZ (installed, not tracked)
│   └── logs/                        #   NEON run archives (not tracked)
│
└── Player-Data/                     # Runtime I/O (inputs, outputs, keys)
```

Nine of NEON's fifteen modules are byte-identical to the release we received;
the six that differ are documented patch by patch in [vendor/NEON.md](vendor/NEON.md).

---

## License

See [LICENSE.txt](LICENSE.txt). The event logs under `data/` are redistributed from their original archives under their own terms.
