# NEON

`ProgramFiles/` contains the source code of **NEON** (NEtwork simulatiON and benchmarking wrapper), presented in the paper:
*Estimating the Runtime and Global Network Traffic of SMPC Protocols. (Andreas Klinger, Vincent Ehrmanntraut, and Ulrike Meyer. 2024. CODASPY 2024.)*

NEON allows to easily execute and benchmark SMPC protocols written in [MP-SPDZ](https://github.com/data61/MP-SPDZ) in different network settings.

The companion runtime estimator XENON from the same paper is not part of this repository; the evaluation in `eval/` measures executions rather than estimating them. Obtain XENON from the authors' original release.


## NEON - NEtwork simulatiON and benchmarking wrapper

NEON (NEtwork simulatiON and benchmarking wrapper) is a framework that acts as a wrapper for [MP-SPDZ](https://github.com/data61/MP-SPDZ).
It allows to perform benchmarks of protocol executions. Main features are:

- Simulate different network settings
- Set protocol, program, inputs, secrets, number of parties, batch size, ...
- Get measurements like runtime, or MP-SPDZ global data (with extensive reports)
- Named Timers for MP-SPDZ programs
- Variable substitution for MP-SPDZ programs

**WARNING:** NEON changes some (default) MP-SPDZ settings, most notably we have enabled `bits-from-squares`, and we set a prime explicitly. See `ProgramFiles/neonconfig.py` for the details and default settings.

NEON's own Sphinx documentation ships with the authors' original release, not with this repository.

**Compatibility:** NEON has been tested with Debian 12, and it is developed primarily for MP-SPDZ version 0.3.6 and the SMPC primitive ``Shamir``. NEON has options to use other MP-SPDZ versions and other SMPC primitives. However, compatibility with other MP-SPDZ versions as well as other SMPC primitives has only been partially tested and may be very limited, so consider them untested and experimental.


## Requirements

For running and evaluating MP-SPDZ programs with NEON in different network settings install:

* `Python 3.10 >=`
  * Python modules: `matplotlib`, `requests`, `sympy` and `tqdm`
* `zstd`

If you want to test your network with NEON
* `iperf3`

If you want to use the MP-SPDZ git (non-release) version which requires compilation of the source code, make sure to install the following first (not tested, might require additional packages)
  * build tools from your distribution in order to compile MP-SPDZ
  * `cmake`, `git`


---

# Changes Made to the NEON Library

The following patches were applied to the NEON library (`ProgramFiles/`) to support the process mining application.

## `ProgramFiles/protocol.py` — Restored `Semi` domain to `Domain.BINARY`

The new library changed `Semi`'s domain from `Domain.BINARY` to `Domain.PRIME`. This was reverted:

```python
Semi = Protocol(executable="semi-party.x",
                domain=Domain.BINARY,   # was incorrectly Domain.PRIME
                ...)
```

**Why:** The `process_mining.mpc` program uses `sbitint.Matrix.sort()`, which internally calls `loopy_odd_even_merge_sort`. This path is only taken when the program is compiled under binary domain (`-B 64` flag). Under prime domain, the compiler routes to `radix_sort`, which raises an `AssertionError` for `sbitint` inputs. Additionally, the domain name is included in the program hash, so changing it invalidates all previously compiled binaries.

## `ProgramFiles/neonhandler.py` — Restored `compile_looping` and `direct` arguments

The new library removed `compile_looping` and `direct` from `smpc()`. They were re-added:

- `smpc()` signature: added `compile_looping=False` and `direct=False`.
- Inside `smpc()`: stores `direct` as `self.__direct`, passes `compile_looping` to `__compile()`.
- `__compile()` signature: added `compile_looping=False`, passes it to `compile_program_if_necessary()`.
- `func_start_client_computations` inside `__execute_smpc()`: passes `self.__direct` to `client.start_computation()`.

## `ProgramFiles/programhandler.py` — Restored `compile_looping` pass-through

- `compile_program_if_necessary()`: added `compile_looping=False`, passes it to `compile_program_locally()`.
- `compile_program_locally()`: added `compile_looping=False`; inserts `-l` flag into the compiler args before `program_hash` when `compile_looping=True`.

**Why:** The `-l` flag enables loop optimisation in the MP-SPDZ compiler, which significantly speeds up compilation of loop-heavy programs like `process_mining.mpc`.

## `ProgramFiles/MPSPDZClient.py` — Restored `direct` flag support

- `start_computation()`: added `direct=False`; inserts `--direct` into the MP-SPDZ runtime argument string before `--output-file` when `direct=True`.

**Why:** `--direct` enables direct party-to-party communication instead of routing through a coordinator, reducing latency in LAN environments.

---

# Disclaimer
Use at your own risk.


# Licenses
NEON uses and relies on the secure multi-party computation benchmarking framework MP-SPDZ listed in the following:
- MP-SPDZ: copyright (c) 2023, Commonwealth Scientific and Industrial Research Organisation (CSIRO) ABN 41 687 119 230. See https://github.com/data61/MP-SPDZ/blob/v0.3.6/License.txt for details.
