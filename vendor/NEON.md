# Vendored: NEON

`ProgramFiles/` is **not our code**. It is the source of **NEON** (NEtwork
simulatiON and benchmarking wrapper), presented in:

> *Estimating the Runtime and Global Network Traffic of SMPC Protocols.*
> Andreas Klinger, Vincent Ehrmanntraut, and Ulrike Meyer. CODASPY 2024.

NEON executes and benchmarks SMPC protocols written in
[MP-SPDZ](https://github.com/data61/MP-SPDZ) under simulated network settings.
Everything else in this directory belongs to it too: `config/` holds its
configuration files, `setup.py` is its MP-SPDZ installer, and `temp/` and
`logs/` are the directories it creates at runtime. NEON resolves all four
relative to its own package, which is why they live here rather than at the
repository root.

MP-SPDZ itself is a third dependency, installed into `temp/MP/` by
`python3 vendor/setup.py install-mpspdz` and never tracked.

## What we changed

Of NEON's 15 modules, **nine are byte-identical** to the release we received:
`helper.py`, `__init__.py`, `neonconfig.py`, `network.py`, `operationmode.py`,
`shamir.py`, `trafficCapture.py`, `virtualnet.py`, and `XIO.py`. Six files
carry the patches listed below. Nothing else in the repository is vendor code —
`dp_calibration.py` used to sit in this package and now lives in `pipeline/`,
where it belongs.

## About NEON

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

# Patches

Six files diverge from the release. Each patch is listed with what changed and
why.

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

## `ProgramFiles/programhandler.py` — Configurable program directory

Upstream resolves `.mpc` sources as `<package>/../Programs/<name>.mpc`, in eight
places. A new `ProgramHandler.program_dir()` method centralizes that resolution
and points it at `<repository root>/mpc` instead.

**Why:** vendoring this package one level deeper would otherwise drag the MPC
program into `vendor/`, and the MPC program is the thesis contribution, not
vendor code. The program hash is computed over the file's *contents* plus the
protocol domain, never its path, so relocating the source leaves every
previously compiled binary valid.

## `ProgramFiles/computationreport.py` — Robustness and formatting

Adds a `JSONDecodeError` import and corrects indentation on a mis-indented
region marker and a field annotation. Behavior is otherwise unchanged.

## `setup.py` — Prebuilt MP-SPDZ toolchain

`tools_to_compile` changed from `["cmake", "boost", "libote", "mpir", "tldr"]` to
`["setup"]`.

**Why:** the release we target ships prebuilt binaries, so compiling the
supporting tools from source is unnecessary and fails on some distributions.

A second change adds `sys.path.insert(0, <this directory>)` before the
`ProgramFiles` imports, so the installer runs as `python3 vendor/setup.py`
from the repository root.

---

# Disclaimer
Use at your own risk.


# Licenses

The release we received carried no license file; its own license note covers
only its MP-SPDZ dependency, reproduced here:

- MP-SPDZ: copyright (c) 2023, Commonwealth Scientific and Industrial Research Organisation (CSIRO) ABN 41 687 119 230. See https://github.com/data61/MP-SPDZ/blob/v0.3.6/License.txt for details.

The repository-wide [LICENSE.txt](../LICENSE.txt) (GPL-3.0) is ours and covers
our own code. The terms for the code in this directory are to be confirmed with
its authors.
