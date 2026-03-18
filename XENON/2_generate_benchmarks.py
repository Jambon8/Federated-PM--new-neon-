#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.neonconfig import NeonConfig, ReportVerbosityLevel
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import protocol
from ProgramFiles import network
from ProgramFiles import trafficCapture
from ProgramFiles.helper import get_path_to_mpspdz, join_path_abs, get_logger, get_path_to_temp
from xenonMetadata import InstructionTestSet, get_relevant_asm_instructions
from xenonConfig import NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS, \
                            NUMBER_OF_CPU_TEST_REPETITIONS, NETWORK_DELAY, DO_ONLY_MISSING_BENCHMARKS, PATH_TO_TEMP, \
                            INIT_TEST_PARAMETERS, NUMBER_OF_INIT_TEST_REPETITIONS
                            

import time
import subprocess
import os

import logging
logger = get_logger("XENON Benchmarks")


def start_external_client(*list_of_ips):
    """
    Starts the external client for network tests used to syncronize parties and send ICMP messages
    """
    script_directory = os.path.dirname(os.path.realpath(__file__))
    args = "python3 " + join_path_abs(script_directory, "external_signal_client.py")
    for ip in list_of_ips:
        args += f" {ip}"
        
    # Execute script from MP-SPDZ folder
    p = subprocess.Popen(args, shell=True, cwd=get_path_to_mpspdz(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (out, _) = p.communicate()


def execute_benchmark(neon, instruction_name, instruction_config, batch_size, list_of_party_ips, asm_instruction_dir, extracted_asm_files):
    """
    Execute a single benchmark
    """

    # Select the Programm to be executed
    # Only if list of ips is specified, we use the network test
    if list_of_party_ips:
        neon.set_program("xenon_net_benchmark")
        add_extra_space = 0
        number_of_test_repetitions = 0
    else:
        neon.set_program("xenon_cpu_benchmark")
        add_extra_space = 4
        number_of_test_repetitions = NUMBER_OF_CPU_TEST_REPETITIONS

    neon.set_substitution("NEON_INITIALIZATION", instruction_config.get_init_string())
    # Note the network benchmarks contains "NEON_MAIN_TEST" twice (one pre test and one normal test)
    # The Pre test is done for the following reasons
    # 1) it might has an initialization message (send only once during the complete program executuion), so we want to ignore it
    # 2) if traffic gets large, messages will be split (congestion window?), to avoid, first do the actual test once before, so window is large enough
    # Note, no extra space required as init is not in a for loop
    neon.set_substitution("NEON_MAIN_TEST", instruction_config.get_instructions_string(add_extra_space=add_extra_space))
    neon.set_substitution("NEON_NUM_REPEATS", number_of_test_repetitions)

    # Set the batch size
    neon.set_batch_size(batch_size)

    if list_of_party_ips:
        # Introduce an artificial delay of (currently) 20 milliseconds only, no bandwidth restrictions.
        neon.set_network(network.Network(delay=f"{NETWORK_DELAY}ms"))

        # Perform the computation with external client
        report = neon.smpc(post_launch_hook=start_external_client, post_launch_hook_arguments=list_of_party_ips, compile_debug=True)
    else:
        # Ensure no network restrictions
        neon.set_network(network.Unlimited)

        # Perform the computation
        report = neon.smpc(compile_debug=True)

    # Check if the SMPC run was successfull
    if report.run_was_successfull():
        logger.info("Execution was as intended")
    else:
        logger.error("Something went wrong, please check the logs")
        exit(1)

    # Get corresponding ASM instructions
    # But only for the first time
    asm_file_name = f"{instruction_name}_[v={instruction_config.vector_length}].asm"
    if asm_file_name not in extracted_asm_files:
        asm_instructions = get_relevant_asm_instructions(report.program_hash, as_asm_object=False)

        with open(join_path_abs(asm_instruction_dir, asm_file_name), 'w') as f:
            for a in asm_instructions:
                f.write(a + "\n")
        
        extracted_asm_files.append(asm_file_name)
    return report

def execute_network_benchmark(neon, list_of_party_ips, instruction_name, instruction_config, n_parties, batch_size, network_captures_dir, asm_instruction_dir, extracted_asm_files):
    """
    Do a network benchmark with external client
    """

    # File for network capture
    network_capture_file = join_path_abs(network_captures_dir, f"{instruction_name}_[n={n_parties},v={instruction_config.vector_length},b={batch_size}].pcap")
    # File for MP-SPDZ data
    mpspdz_global_data_file = join_path_abs(network_captures_dir, f"{instruction_name}_[n={n_parties},v={instruction_config.vector_length},b={batch_size}].data")

    # Check if the test is actually required
    if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(network_capture_file) and os.path.exists(mpspdz_global_data_file):
        logger.info(f"Network Benchmark (n={n_parties},v={instruction_config.vector_length},b={batch_size}) already done. Skipping.")
        return

    # -n no name lookup
    # -B buffersize in KiB (needs to be large enough to capture all packets)
    # -f capture filter, select only ICMP or PUSH packets: icmp || (tcp[tcpflags] & tcp-push != 0) and restrict to possible IP adresses
    # -i interface
    # -w output-file
    # Note, we noticed tcpdump performes in general better than tshark or dumpcap
    # However, tshark should work here, too. Just replace tcpdump with tshark (or dumpcap (untested)). Note, tshark speciefies the buffersize in MiB!
    # Warning -> The check for dropped packets needs to be adapted
    # Note, do not use SafeTcpdump here, as we use the ICMP messages here to identify the traffic of interest. In addition, we later check for the number of ICMP messages, and thus check later that the capture was indeed complete
    tcpdump_proc = subprocess.Popen(['tcpdump', '-n', '-B', f'{1024**2}', '-f', 'net 172.16.1.0/24 && ( icmp || (tcp && (tcp[tcpflags] & tcp-push != 0)) )', '-i', 'neon_bridge', '-w', network_capture_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Ensure it captures from the very beginning (maybe not required, but only small impact)
    time.sleep(1)

    # Execute Benchmark (and set parameters)
    report = execute_benchmark(neon, instruction_name, instruction_config, batch_size, list_of_party_ips=list_of_party_ips,
                               asm_instruction_dir=asm_instruction_dir, extracted_asm_files=extracted_asm_files)

    timer_global_data = report.get_timer_global_data_sent(3333)

    # Make sure to get final packets (maybe not required, but only small impact)
    time.sleep(1)
    tcpdump_proc.terminate()

    # Check for dropped packets
    output, error = tcpdump_proc.communicate()
    logger.debug(f"TCPDUMP STDOUT: {output}")
    logger.debug(f"TCPDUMP ERROR: {error}")
    # Tcpdump output looks like:
    # 28220 packets captured
    # 28221 packets received by filter
    # 0 packets dropped by kernel
    # (one empty line)
    # -> So we you use a simple/hacky way to check for errors
    if error.decode('utf-8').split("\n")[-2] != "0 packets dropped by kernel":
        logger.critical("Packets dropped during capture. Try increasing buffer size.")
        logger.critical(f"TCPDUMP OUTPUT:\n{output.decode('utf-8')}")
        logger.critical(f"TCPDUMP ERROR:\n{error.decode('utf-8')}")
        exit(1)

    # sort the stream based on time (tcpdump might save packets out of order)
    reorder_proc = subprocess.Popen(['reordercap', network_capture_file, f'{network_capture_file}.ordered'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = reorder_proc.communicate()
    logger.debug(f"Packet Reordering: {output.decode('utf-8').strip()}")
    os.replace(f'{network_capture_file}.ordered', network_capture_file)

    # Write MP-SPDZ global_data to file
    logger.debug(f"MP-SPDZ Global data is: {timer_global_data}")
    with open(mpspdz_global_data_file, 'w') as f:
        f.write(f"{timer_global_data}")


def execute_computation_benchmark(neon, instruction_name, instruction_config, n_parties, batch_size, cpu_timings_dir, asm_instruction_dir, extracted_asm_files):
    """
    Do a single CPU benchmark
    """

    # File for CPU Timings
    cpu_timings_file = join_path_abs(cpu_timings_dir, f"{instruction_name}_[n={n_parties},v={instruction_config.vector_length},b={batch_size}].txt")

    # Check if the test is actually required
    # we skipp it if we have more or equal number of benchmark timings
    # we do not do only partial tests, as this can negatively affect the timings obtained
    # so either we do the complete benchmark or nothing at all (w.r.t to each individual benchmark)
    if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(cpu_timings_file):
        with open(cpu_timings_file, 'r') as f:
            if len(f.readlines()) >= NUMBER_OF_CPU_TEST_REPETITIONS:
                logger.info(f"CPU Benchmark (n={n_parties},v={instruction_config.vector_length},b={batch_size}) already done. Skipping.")
                return

    # Execute Benchmark (and set parameters)
    report = execute_benchmark(neon, instruction_name, instruction_config, batch_size, list_of_party_ips=None, 
                               asm_instruction_dir=asm_instruction_dir, extracted_asm_files=extracted_asm_files)

    # Extract individual timers
    tmp_client_timings = []
    for cr in report.client_reports:
        tmp_client = []
        for l in cr.stdout.decode('utf-8').split('\n'):
            if l.startswith("Starting timer 3333 at "):
                last_start = float(l.split(" ")[4])
            elif l.startswith("Stopped timer 3333 at "):
                tmp_end = float(l.split(" ")[4])
                tmp_client.append(tmp_end - last_start)
        tmp_client_timings.append(tmp_client)

    # Compute the average over the parties
    tmp_timer_avgs = []
    for i in range(NUMBER_OF_CPU_TEST_REPETITIONS):
        tmp_avgs = [c_times[i] for c_times in tmp_client_timings]
        tmp_timer_avgs.append(sum(tmp_avgs)/len(tmp_avgs))

    # Write timings to file
    logger.debug(f"CPU times are: {tmp_timer_avgs}")
    with open(cpu_timings_file, 'w') as f:
        for cpu_time in tmp_timer_avgs:
            f.write(f"{cpu_time}\n")



def execute_init_benchmark(neon, n_parties, network_captures_dir):
    """
    Execute and measure the real global data of the connection and closing phase (init)
    """

    # Make sure network is unlimited
    neon.set_network(network.Unlimited)
    # Set Batchsize, but should have no influence
    neon.set_batch_size(0)
    # Set Program
    neon.set_program("xenon_hack")

    for i in range(NUMBER_OF_INIT_TEST_REPETITIONS):
        # File for Global Data
        init_global_data_capture_file = join_path_abs(network_captures_dir, f"init_global_data_[n={n_parties}]_{i+1}.pcap")

        # Check if the test is actually required
        if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(init_global_data_capture_file):
            logger.info(f"INIT Benchmark (n={n_parties}, repetition {i+1}/{NUMBER_OF_INIT_TEST_REPETITIONS}) already done. Skipping.")
            continue

        logger.info(f"DOING INIT Benchmark (n={n_parties}) repetition {i+1} of {NUMBER_OF_INIT_TEST_REPETITIONS}")
        
        # Start Safe Traffic Capture
        # IMPORTANT: This adds ICMP messages to the capture which need to be removed later !!!!
        safe_tcpdump = trafficCapture.SafeTcpdump(['-n', '-B', f'{1024**1}', '-i', 'neon_bridge', '-w', init_global_data_capture_file])
        safe_tcpdump.start_capture_with_ping("172.16.1.11", init_global_data_capture_file)

        # Execute Empty Protocol
        report = neon.smpc()
        
        # Check if the SMPC run was successfull
        if report.run_was_successfull():
            logger.info("Execution was as intended")
        else:
            # Note, you can enforce an failure by providing not enough inputs to the protocol
            logger.error("Something went wrong, please check the logs")
            exit(1)

        try:
            # Stop the capture
            safe_tcpdump.stop_capture()
        except trafficCapture.SafeTcpdumpException as e:
            logger.critical(f"Capture problem: {e}")
            exit(1)

##############################
# Simple commandline options #
##############################

# To select only one test and not all
create_net_dir = False
create_cpu_dir = False
if len(sys.argv) == 1:
    LIST_OF_TEST_PARAMS = [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS, INIT_TEST_PARAMETERS]
    create_net_dir = True
    create_cpu_dir = True
elif len(sys.argv) == 2 and sys.argv[1] == "cpu":
    LIST_OF_TEST_PARAMS = [COMPUTATION_TEST_PARAMETERS]
    create_cpu_dir = True
elif len(sys.argv) == 2 and sys.argv[1] == "net":
    LIST_OF_TEST_PARAMS = [NETWORK_TEST_PARAMETERS]
    create_net_dir = True
elif len(sys.argv) == 2 and sys.argv[1] == "bch":
    LIST_OF_TEST_PARAMS = [BATCHSIZE_TEST_PARAMETERS]
    create_net_dir = True
elif len(sys.argv) == 2 and sys.argv[1] == "init+ack":
    LIST_OF_TEST_PARAMS = [INIT_TEST_PARAMETERS]
    create_net_dir = True
else:
    logger.critical("Invalid Argument passed. Use no argument for performing network and computation time test. And use 'net', 'bch', 'cpu' or 'init+ack' for network, batchsize, computation time, or init-phase with ack size, respectively.")


##########################
# Execute the Benchmarks #
##########################

# Create Basic NEON
config = NeonConfig.from_config_files()
neon = NeonHandler(OperationMode.LOCAL_VIRTUAL, config)

# Further Config
neon.set_report_verbosity(ReportVerbosityLevel.STDOUT)
neon.set_print_timers_upon_deletion(False)
neon.set_read_secrets(False)
neon.set_bits_from_squares(True)

# set the protocol for the execution
neon.set_protocol(protocol.Shamir)

#### Prepare Folders
if create_net_dir:
    network_captures_dir = join_path_abs(PATH_TO_TEMP, "network_captures")
    if not os.path.exists(network_captures_dir):
        os.makedirs(network_captures_dir)

if create_cpu_dir:
    cpu_timings_dir = join_path_abs(PATH_TO_TEMP, "cpu_timings")
    if not os.path.exists(cpu_timings_dir):
        os.makedirs(cpu_timings_dir)

# Iterate over test parameters
for test_params in LIST_OF_TEST_PARAMS:

    if test_params in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]:
        # Prepare folders
        asm_instruction_dir = join_path_abs(PATH_TO_TEMP, f"asm_instructions_{test_params.suffix}")
        if not os.path.exists(asm_instruction_dir):
            os.makedirs(asm_instruction_dir)
        
        # Keep track of already extracted files
        extracted_asm_files = []

        # Get folder for instruction test
        instructions_dir = join_path_abs(PATH_TO_TEMP, f"instruction_tests_{test_params.suffix}")
        instructions_dir_files = os.listdir(instructions_dir)
        instructions_dir_files.sort()

    #### Execute Tests
    # Iterate over number of parties
    for n_parties in sorted(set(test_params.parties + test_params.additional_parties)):
        if n_parties > 200:
            raise Exception("Too many parties required")
        elif n_parties < 3:
            raise Exception("Too few parties requested")

        logger.info(f"Performing Tests with {n_parties} parties")

        # Set number of parties
        neon.set_number_of_parties(n_parties)
        
        if test_params in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]:
            # Set enough dummy inpuys
            # We do this here to save some time
            # +1, as init contains one main_test
            neon.set_all_inputs("3 " * max(test_params.vector_lengthes + test_params.additional_vector_lengthes) * (NUMBER_OF_CPU_TEST_REPETITIONS + 1))

        if test_params in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS]:
            # Generate Client certs for external client communication
            p = subprocess.Popen(f"Scripts/setup-clients.sh {n_parties}", shell=True, cwd=get_path_to_mpspdz(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p.communicate()

            # Construct IP list (we assume the number of parties is below 200)
            # Check is done above
            list_of_party_ips = [f"172.16.1.{i}" for i in range(11, 11+n_parties)]

        # Hack to ensure bridge exists (Do always)
        neon.set_network(network.Unlimited)
        neon.set_program("xenon_hack")
        logger.info("EXECUTE test program to setup network")
        neon.smpc()

        if test_params in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]:
            # Iterate over the instructions to benchmark
            for instruction_file in instructions_dir_files:
                if not instruction_file.endswith(".json"):
                    continue

                # Remove ".conf"
                instruction_name = instruction_file[:-5] 
                logger.info(f"Executing {instruction_name}")

                ### Get Instruction parameters
                instruction_test_set = InstructionTestSet.from_json_file(join_path_abs(instructions_dir, instruction_file))

                # Iterate over each individual test
                for instruction_config in instruction_test_set.test_set:
                    if instruction_test_set.test_type == "network-batch":
                        # For Batch Size instructions, use vector lenght as batch size
                        batch_size = instruction_config.vector_length
                    elif instruction_test_set.test_type in ["empty", "local-single", "local-vector", "network-only"]:
                        # Other instructions should not need any batches, so we set it to 0
                        # if it would be required, this will fail
                        batch_size = 0
                    else:
                        raise Exception("UNKNOWN benchmark configuration")

                    if test_params in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS]:
                        # NETWORK Benchmark
                        execute_network_benchmark(neon, list_of_party_ips, instruction_name, instruction_config, n_parties, batch_size, network_captures_dir, asm_instruction_dir, extracted_asm_files)
                    else:
                        # COMPUTATION Benchmark              
                        execute_computation_benchmark(neon, instruction_name, instruction_config, n_parties, batch_size, cpu_timings_dir, asm_instruction_dir, extracted_asm_files)

                    print()

        elif test_params in [INIT_TEST_PARAMETERS]:
            # Connection and closing phase (init) benchmarks
            execute_init_benchmark(neon, n_parties, network_captures_dir)
            
        else:
            logger.critical("UNKNOWN Test Parameter.")
            exit(1)
