# NEON and XENON

This repository contains the source code of **NEON** (NEtwork simulatiON and benchmarking wrapper) and **XENON** (eXecution time Estimator with Network consideratiON) presented in the paper:
*Estimating the Runtime and Global Network Traffic of SMPC Protocols. (Andreas Klinger, Vincent Ehrmanntraut, and Ulrike Meyer. 2024. CODASPY 2024.)*

NEON allows to easily execute and benchmark SMPC protocols written in [MP-SPDZ](https://github.com/data61/MP-SPDZ) in different network settings.

XENON allows to estimate the execution time and network traffic of SMPC protocols written in [MP-SPDZ](https://github.com/data61/MP-SPDZ) while considering different network settings.


## NEON - NEtwork simulatiON and benchmarking wrapper

NEON (NEtwork simulatiON and benchmarking wrapper) is a framework that acts as a wrapper for [MP-SPDZ](https://github.com/data61/MP-SPDZ).
It allows to perform benchmarks of protocol executions. Main features are:

- Simulate different network settings
- Set protocol, program, inputs, secrets, number of parties, batch size, ...
- Get measurements like runtime, or MP-SPDZ global data (with extensive reports)
- Named Timers for MP-SPDZ programs
- Variable substitution for MP-SPDZ programs

You can find a **quick start guide** and other information about NEON in the [documentation](docs/build/html/index.html) (see below).

**WARNING:** NEON changes some (default) MP-SPDZ settings, most notably we have enabled `bits-from-squares`, and we set a prime explicitly. Check the [documentation](docs/build/html/index.html) and also `ProgramFiles/neonconfig.py` for **important** details and default settings.

You can view the documentation locally in your browser by just opening the file `docs/build/html/index.html` in your browser. Alternatively, you can navigate to `docs/build/html/`, execute `python3 -m http.server`, and then open `localhost:8000/index.html` in your browser. You can generate/update the [documentation](docs/build/html/index.html) by executing `make html && make html` (yes twice) in the `docs` folder.


**Compatibility:** NEON has been tested with Debian 12, and it is developed primarily for MP-SPDZ version 0.3.6 and the SMPC primitive ``Shamir``. NEON has options to use other MP-SPDZ versions and other SMPC primitives. However, compatibility with other MP-SPDZ versions as well as other SMPC primitives has only been partially tested and may be very limited, so consider them untested and experimental.


## XENON - eXecution time Estimator with Network consideratiON

XENON allows to estimate SMPC protocols written in [MP-SPDZ](https://github.com/data61/MP-SPDZ) while also considering network traffic.

Given an SMPC protocol, number of parties, and a network setting (delay, outgoing and incoming bandwidth limitations), XENON estimates the total execution time in the specified network setting, as well as the overall global network traffic generated. XENON can produce detailed reports consisting of estimations of the individual instructions involved, and thus allows to determine possible bottlenecks. The protocol itself is not executed in order to obtain the estimations.

More details can be found in [XENON/README.md](XENON/README.md).


## Requirements (NEON and XENON)

For just running and evaluating MP-SPDZ programs with NEON in different network settings install:

* `Python 3.10 >=`
  * Python modules: `matplotlib`, `requests`, `sympy` and `tqdm`
* `zstd`

If you want to test your network with NEON
* `iperf3`

If you also want to estimate the runtime with XENON and perform the required benchmarks:
* working `NEON`
* `tshark`
* `tcpdump`
* Additional Python modules: `numpy`


If you want to use the MP-SPDZ git (non-release) version which requires compilation of the source code, make sure to install the following first (not tested, might require additional packages)
  * build tools from your distribution in order to compile MP-SPDZ
  * `cmake`, `git`






# Disclaimer
Use at your own risk.



# Licenses
NEON and XENON use and rely on the secure multi-party computation benchmarking framework MP-SPDZ listed in the following:
- MP-SPDZ: copyright (c) 2023, Commonwealth Scientific and Industrial Research Organisation (CSIRO) ABN 41 687 119 230. See https://github.com/data61/MP-SPDZ/blob/v0.3.6/License.txt for details.
