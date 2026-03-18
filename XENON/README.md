# XENON - eXecution time Estimator with Network consideratiON

XENON (eXecution time Estimator with Network consideratiON) allows to estimate SMPC protocols written in [MP-SPDZ](https://github.com/data61/MP-SPDZ) while also considering network traffic.

Given an SMPC protocol, number of parties, and a network setting (delay, outgoing and incoming bandwidth limitations), XENON estimates the total execution time in the specified network setting, as well as the overall global network traffic generated. XENON can produce detailed reports consisting of estimations of the individual instructions involved, and thus allows to determine possible bottlenecks. The protocol itself is not executed in order to obtain the estimations.
MP-SPDZ also outputs a custom global network traffic which does not reflect the actual global data sent, however, XENON is able to estimate this MP-SPDZ output, too.

## Compatibility and Limitations

XENON has been tested with Debian 12, and it is developed for MP-SPDZ version 0.3.6 and the SMPC primitive ``Shamir``.

XENON supports to benchmark and estimate 154 different MP-SPDZ instructions for the SMPC primitive `Shamir`. In addition, XENON can estimate **simple** for-loops that have a fixed and publicly known number of iterations and increments of exactly `1` (e.g., `@for_range(10)`, `@for_range(10, 20)`, or `@for_range_parallel(10, 100)`).

However, XENON does **not** support other MP-SPDZ versions, and does **not** support other SMPC primitives. In addition, XENON does **not** support all MP-SPDZ instructions, and does **not** support for-loops with increments different from `1` (e.g., `@for_range(10, 20, 2)`). In addition, any other for-loop like structures (e.g., `@for_range_opt_multithread`, or `@foreach_enumerate`) are **not** tested and/or are **not** supported. Furthermore, XENON does **not** support other control structures such as while-loops (e.g., `@while_do`, or `@do_while`), if/else-structures (e.g., `@if_(`, or `@if_e`/`@else_`), and so on.

## Setup
In order to use XENON, you first need to make sure that NEON is working.
Then, you can continue with executing the required benchmarks and the generation of the estimation functions. Note, the actual hardware requirements depend on the benchmark parameters specified in `xenonConfig.py`. The default parameters require hardware suitable to run at least 10 parties.

The benchmarks will probably require root rights in order to set up a virtual network bridge and apply latency.

First, you have to "install" NEON and MP-SPDZ:

1. Download/Clone the **complete** git repo of NEON including XENON
2. Execute `python3 setup.py install-mpspdz --version 0.3.6`

Verify that NEON and MP-SPDZ work with `python3 examples/example_simple.py` (should output some timings and data).

The benchmarks and the generation of the estimation functions can be done by simply executing the provided scripts in the `XENON` folder (more details below) in the following order:

0. (Only if you have done previous benchmarks, then delete (or rename) the folder `NEON/temp/xenon_benchmarks_*/` (the one corresponding to the MP-SPDZ version used) to remove all old benchmarks.)
1. Execute `python3 1_generate_code_and_check.py` and wait till it is finished.
2. Then execute `python3 2_generate_benchmarks.py` and wait till it is finished.
3. Then execute `python3 3_generate_network_model.py` and wait till it is finished. 
4. Then execute `python3 4_generate_estimation_functions.py` and wait till it is finished.

You can verify that it is working by executing `python3 example_estimation.py`, which performs a simple example estimation.


## Further Details

We use the following naming convention in generated files and folder:
* `bch` for stuff related to batched-instructions
* `net` for stuff related to non-batched-instructions that generate network traffic
* `cpu` for stuff that is related to local computation time
Note, filenames consist typically of the MP-SPDZ asm instruction name (`adds`, `muls`, ...) and is extended with further information like number of parties `n`, vector length `v` or batch size `b` as necessary.

XENON is currently a collection of scripts:
* `xenonConfig.py`: Defines the basic parameters. Most interesting are the `*_TEST_PARAMETERS` which specify the parameters of the benchmarks, and the `REGRESSION Parameters` section, which specifies the parameters of the regression.
* `1_generate_code_and_check.py`: This classifies instructions and generates code snippets required for the benchmarks. The generated code snippets are stored in `temp/xenon_benchmarks_*/instruction_tests_*`.
* `2_generate_benchmarks.py`: Executes the benchmarks, and has options to execute only `bch`, `net`, `cpu` or `init+ack`. `bch`, `net` and `init+ack` benchmarks store pcaps and MP-SPDZ global data in `temp/xenon_benchmarks_*/network_captures`. `cpu` benchmark timings are stored in `temp/xenon_benchmarks_*/cpu_timings`. If you change the CPU, then you have to re-run the `cpu` benchmarks. Also, the corresponding asm instructions are stored in `temp/xenon_benchmarks_*/asm_instructions_*`. Note, this will probably require root rights in order to set up a virtual network bridge and apply latency.
* `3_generate_network_model.py`: Extract the network models and stores them in JSON format in `temp/xenon_benchmarks_*/network_models`. If you want to see more details of the network traffic (not required/used for the regression later), then enable the `STORE_INDIVIDUAL_PACKETS_IN_NET_MODEL` and `STORE_LIST_OF_MESSAGES_IN_NET_MODEL` setting in `xenonConfig.py`. Needs to be re-run whenever `2_generate_benchmarks.py` is repeated. 
* `4_generate_estimation_functions.py`: Generate the estimation functions itself, i.e., perform the regressions. It also plots the points for training (and verification if used, see `additional_*` options in test parameters in `xenonConfig.py`). The output is stored in `XENON/estimation_functions_*`. Note, this script has the same options as `2_generate_benchmarks.py`, so only parts, e.g., `cpu` can be redone separately. This script needs to be re-run whenever `2_generate_benchmarks.py` (and `3_generate_network_model.py`)  is repeated. 
* `xenonMetadata.py`: Defines some classes required at multiple places.
* `external_signal_client.py`: Required by `2_generate_benchmarks.py`
* `xenon.py`: A simple class/implementation that allows to estimate an SMPC protocol. If you want to use it, see `example_estimation.py`.
* `example_estimation.py`: An example that shows how to use XENON to estimate a single SMPC protocol.

Note, in `../temp/xenon_benchmarks_*` are the code snippets, benchmarks, and abstract network traffic models stored generated by `1_generate_code_and_check.py`, `2_generate_benchmarks.py`, and `3_generate_network_model.py.` The final estimation functions generated by `4_generate_estimation_functions.py` are store in `XENON/estimation_functions_*`.



### Tip

If you change the CPU, it is enough to re-run only the CPU benchmarks and corresponding regressions (assuming you have already completed the setup above):

1. Set `DO_ONLY_MISSING_BENCHMARKS = False` in `xenonConfig.py` or delete (or rename) in `NEON/temp/xenon_benchmarks_*/` the folders `cpu_timings` and `asm_instructions_cpu`.
2. Then execute `python3 2_generate_benchmarks.py cpu` and wait till it is finished.
3. Then execute `python3 4_generate_estimation_functions.py cpu` and wait till it is finished.

You can verify it with a manual estimation, e.g., `python3 example_estimation.py`.


# Disclaimer
Use at your own risk.

# Licenses
NEON and XENON use and rely on the secure multi-party computation benchmarking framework MP-SPDZ listed in the following:
- MP-SPDZ: copyright (c) 2023, Commonwealth Scientific and Industrial Research Organisation (CSIRO) ABN 41 687 119 230. See https://github.com/data61/MP-SPDZ/blob/v0.3.6/License.txt for details.