#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import protocol
from ProgramFiles import network
from ProgramFiles.helper import get_path_to_mpspdz, join_path_abs, get_logger, get_mpspdz_version_from_path
from xenonConfig import NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS, PATH_TO_ESTIMATIONS, USE_SYMPY, REGRESSION_INIT_GLOBAL_DATA, \
                        TCP_INIT_CWND_SIZE_IN_BYTES, TCP_CUBIC_MAX_GROWTH_RATE
from xenonMetadata import ASMInstruction, EstimationComputation, EstimationNetwork, EstimationInit, \
                              compute_r_squared, compute_mse

import os
import time
import math
import json

import matplotlib.pyplot as plt
import numpy

import logging
logger = get_logger("XENON")



def load_estimations(estimation_dir_suffix, EstimationClass):
    """
    Load the estimation values/parameters from the regression
    """
    logger.debug(f"Loading estimations from {estimation_dir_suffix}")
    estimation_dir = join_path_abs(PATH_TO_ESTIMATIONS, estimation_dir_suffix)
    estimation_dir_files = os.listdir(estimation_dir)

    estimations = {}
    for est_file in estimation_dir_files:
        if est_file.endswith(".json"):
            isntruction_name = est_file[:-5]
            logger.debug(f"Loading single file: {isntruction_name}")
            estimations[isntruction_name] = EstimationClass.from_json_file(join_path_abs(estimation_dir, est_file))

    return estimations

# The actual required paramters
net_estimations = load_estimations(NETWORK_TEST_PARAMETERS.suffix, EstimationNetwork)
bch_estimations = load_estimations(BATCHSIZE_TEST_PARAMETERS.suffix, EstimationNetwork)
cpu_estimations = load_estimations(COMPUTATION_TEST_PARAMETERS.suffix, EstimationComputation)
logger.debug("Loading Init file")
# For compensation of connection and closing phase global traffic
init_estimation = EstimationInit.from_json_file(join_path_abs(PATH_TO_ESTIMATIONS, f"global_data_init_compensation.json"))
# Get ack size (should be 66 bytes)
with open(join_path_abs(PATH_TO_ESTIMATIONS, "ack_size.txt"), 'r') as f:
    ack_size = int(f.readline())
                                    

def estimate_single_net_or_bch_instruction(asm_name, n_parties, dimension, net_bch_est, network_setting):
    """
    Estimate a single network/batch instruction
    Compute time required for transmitting the data (max sending or receiving)
    Compute global traffic, real and the MP-SPDZ output (global data)
    """
    global ack_size

    network_delay = network_setting.get_delay_in_seconds()
    network_incoming_bandwidth = network_setting.get_incoming_bandwidth_in_bits_per_second()
    network_outgoing_bandwidth = network_setting.get_outgoing_bandwidth_in_bits_per_second()
    # NETWORK estimation for that individual line
    # Set time and traffic to 0
    # If no network traffix, already correct, otherwise alows +=
    tmp_network_time = 0.0
    tmp_global_network_traffic = 0.0
    tmp_global_mpspdz_traffic = 0.0
    tmp_number_of_steps = 0
    tmp_max_single_message = 0
    if asm_name in net_bch_est:
        tmp_global_mpspdz_traffic = net_bch_est[asm_name].get_mpspdz_global_data(n_parties, dimension)
        tmp_number_of_steps = len(net_bch_est[asm_name].get_steps())
        for step in net_bch_est[asm_name].get_steps():
            # Add delay for each step
            tmp_network_time += network_delay
            tmp_send_data, tmp_rec_data, tmp_global_data, tmp_num_msg, tmp_max_single = step.get_network_data(n_parties, dimension)
            # *8 as bandwidth is given in bits, but traffix is given in bytes
            tmp_send_time = (tmp_send_data * 8) / network_outgoing_bandwidth
            tmp_rec_time = (tmp_rec_data * 8) / network_incoming_bandwidth
            # Max trasmitting time is max time for sending or receiving data
            tmp_network_time += max(tmp_send_time, tmp_rec_time)
            tmp_global_network_traffic += tmp_global_data + ack_size * tmp_num_msg
            # overall max single message size
            tmp_max_single_message = max(tmp_max_single_message, tmp_max_single)
    
    return tmp_network_time, tmp_global_network_traffic, tmp_global_mpspdz_traffic, tmp_number_of_steps, tmp_max_single_message


def anotate_individual_time_and_network_estimations(asm_instructions, n_parties, network_setting):
    """
    Go through the asm_instructions and annotate it with individual timings and traffic
    """
    global net_estimations
    global bch_estimations
    global cpu_estimations

    for asm in asm_instructions:
        asm_name = asm.instruction_name

        # Set the multiplier array to 1
        asm.multiplier.append(1)

        # Skip batched instructions
        if asm_name not in bch_estimations:

            # Do only known instructions
            if asm_name in cpu_estimations or asm_name in net_estimations:
                # Get vector length
                tmp_vector_length = asm.get_vector_length()

                # CPU estimation for that individual line
                if asm_name in cpu_estimations:    
                    asm.computation_time = cpu_estimations[asm_name].get_computation_time(n_parties, tmp_vector_length)

                # NETWORK estimation for that individual line
                if asm_name in net_estimations:
                    asm.network_time, asm.global_network_traffic, asm.global_mpspdz_traffic, asm.number_of_steps, asm.max_single_message = estimate_single_net_or_bch_instruction(asm_name, n_parties, tmp_vector_length, net_estimations, network_setting)

        

def anotate_for_loop(asm_instructions):
    """
    Add the multiplier for for loops here
    In other words, run this once to consider for loops
    """

    for instruction_number, asm in enumerate(asm_instructions):
        if asm.instruction_name == "jmpnz":
            # currently we only support simple for loops!!!!!
           
            # For simple for loops we have two values to get, i.e., start and stop:
            #
            # High level code:
            # @for_range(start, stop)
            #
            # Assume we have start = 11, stop=17
            #
            # ASM code:
            # 6: ldint ci0, 11 # 3 <- START VALUE
            # 7: # 2dd30ac5c77aa6fe629ea512fc00206c37c7b63e373fbe8fcf785af30e23edc3-0-begin-loop-3
            # 8: muls 3, s2, s0, s1 # 4
            # 9: ldint ci2, 1 # 5
            # 10: addint ci0, ci0, ci2 # 6
            # 11: ldint ci2, 17 # 7 <- STOP VALUE
            # 12: ltc ci1, ci0, ci2 # 8
            # 13: jmpnz ci1, -6 # 9 <- JUMP INSTRUCTION
            # the "jmpnz" indicates the for loop and has instruction_number = 9
            # So start is at instruction_number + jumpdistanze = 9 + (-6) = 3 -> ldint with 11
            # And stop is at instruction_number - 2 = 9 - 2 = 7 -> ldint with 17
            # Total number of iterations is then stop - start = 17 - 11 = 6 -> [11, 12, 13, 14, 15, 16]
            # WARNING: This works only with simple loops, where start/stop are known already at compile time !!!!!

            jump_distance = asm.second_parameter             
            asm_number_of_iterations_start = asm_instructions[instruction_number + jump_distance]
            asm_number_of_iterations_stop = asm_instructions[instruction_number - 2]

            # Important, check both places
            if asm_number_of_iterations_start.instruction_name == "ldint" and asm_number_of_iterations_stop.instruction_name == "ldint":
                number_of_iterations = asm_number_of_iterations_stop.second_parameter - asm_number_of_iterations_start.second_parameter
                # jump distance is added to next index (so +1)
                # see MP-SPDZ docu: https://mp-spdz.readthedocs.io/en/latest/instructions.html#Compiler.instructions.jmpnz
                start_index_of_loop = instruction_number + jump_distance + 1
                for i in range(start_index_of_loop, instruction_number + 1):
                    asm_instructions[i].multiplier.append(number_of_iterations)
            else:
                logger.critical(f"UNSUPPORTED Loop: {asm}")
                exit(1)


        
def count_batchsizes(asm_instructions):
    """
    Determine the number of individual "values" are required w.r.t. to batch commands
    """
    batch_counter = { k:0 for k in bch_estimations.keys()}

    for asm in asm_instructions:
        asm_name = asm.instruction_name

        if asm_name in bch_estimations:
            batch_counter[asm_name] += asm.get_vector_length() * math.prod(asm.multiplier)

    # Get only the vectorized ones
    # Better combined both into one counter
    # batch_counter_compressed = {k:v for k,v in batch_counter.items() if k.startswith("v")}
    # for k, v in batch_counter.items():
    #     if k.startswith("v"):
    #         continue
    #     if f"v{k}" in batch_counter_compressed:
    #         batch_counter_compressed[f"v{k}"] += v
    #     else:
    #         batch_counter_compressed[k] = v
    
    # return batch_counter_compressed
    return batch_counter


def estimate_program(decompiled_file, n_parties, network_setting, batch_size):
    global net_estimations
    global bch_estimations
    global cpu_estimations
    global init_estimation
    global ack_size
    
    # Parse the file
    with open(decompiled_file) as f:
        # Ignore comments (start with '#')
        list_of_asm_instructions = [ASMInstruction(l) for l in f.readlines() if not l.startswith("#")]
                

    # Estimate each individual instruciton
    anotate_individual_time_and_network_estimations(list_of_asm_instructions, n_parties, network_setting)

    # Check for loops
    anotate_for_loop(list_of_asm_instructions)

    # Compute the overall estimation
    # First the simple network and Computation part without batches
    # Essentially sum up the individual values while also considering loop iterations (as a multiplier)
    # Note we get individual estimation per instructions type
    # Already add the bch_estimation instruction names
    estimated_time_net = { a:0 for a in list(net_estimations.keys()) + list(bch_estimations.keys()) }
    estimated_time_cpu = { a:0 for a in cpu_estimations.keys() }
    estimated_global_traffic = { a:0 for a in list(net_estimations.keys()) + list(bch_estimations.keys())}
    estimated_global_mpspdz_traffic = { a:0 for a in list(net_estimations.keys()) + list(bch_estimations.keys())}
    estimated_number_of_steps = { a:0 for a in list(net_estimations.keys()) + list(bch_estimations.keys())}
    estimated_max_single_message = 0
    for asm in list_of_asm_instructions:
        asm_name = asm.instruction_name
        asm_multiplier = math.prod(asm.multiplier)

        # Skipp batched instructions
        if asm_name not in bch_estimations:

            if asm_name in cpu_estimations:
                estimated_time_cpu[asm_name] += asm.computation_time * asm_multiplier

            if asm_name in net_estimations:
                estimated_time_net[asm_name] += asm.network_time * asm_multiplier
                estimated_global_traffic[asm_name] += asm.global_network_traffic * asm_multiplier
                estimated_global_mpspdz_traffic[asm_name] += asm.global_mpspdz_traffic * asm_multiplier
                estimated_number_of_steps[asm_name] += asm.number_of_steps * asm_multiplier
                estimated_max_single_message = max(estimated_max_single_message, asm.max_single_message)
    
    # Now, do it for all batched instructions
    # For each batchsize operation add it number of batches required (roughly total number of values / batch_size)
    batchsize_counters = count_batchsizes(list_of_asm_instructions)
    for asm_name, counts in batchsize_counters.items():
        if counts != 0:
            # Get time for single batch
            tmp_cpu = cpu_estimations[asm_name].get_computation_time(n_parties, batch_size)

            # Get traffic for single batch
            tmp_network_time, tmp_global_network_traffic, tmp_global_mpspdz_traffic, tmp_number_of_steps, tmp_max_single_message = estimate_single_net_or_bch_instruction(asm_name, n_parties, batch_size, bch_estimations, network_setting)

            # Use number of batches
            number_of_batches = math.ceil(counts / batch_size)
            estimated_time_cpu[asm_name] += tmp_cpu * number_of_batches
            estimated_time_net[asm_name] += tmp_network_time * number_of_batches
            estimated_global_traffic[asm_name] += tmp_global_network_traffic * number_of_batches
            estimated_global_mpspdz_traffic[asm_name] += tmp_global_mpspdz_traffic * number_of_batches
            estimated_number_of_steps[asm_name] += tmp_number_of_steps * number_of_batches
            estimated_max_single_message = max(estimated_max_single_message, tmp_max_single_message)
    
    
    # Compensate for TCP Congestion Control Effects (like Slow Start)
    # Note, linux uses default CUBIC with sshtresh=0, so no slow start
    # Note, we ignore the TCP header size influence
    maximal_body_size = estimated_max_single_message
    """ Iterative solution
    # Count number of CWND updates
    counter_cwnd_changes = 0    
    # Determine the minimal number of window changes in order to send the largest message in one shot
    # Note, as we keep track of the total data message length, we need to subtrack the the header once
    current_cwnd_size = TCP_INIT_CWND_SIZE_IN_BYTES
    while current_cwnd_size < maximal_body_size:
        current_cwnd_size *= TCP_CUBIC_MAX_GROWTH_RATE
        counter_cwnd_changes += 1
    """
    # direkt computation
    if maximal_body_size <= TCP_INIT_CWND_SIZE_IN_BYTES:
        # if message size is smaller than initial window, no changes required
        counter_cwnd_changes = 0
    else:
        # Otherwise determine the minimal amount of CWND changes until it can cover the largest message
        counter_cwnd_changes = math.ceil(math.log(maximal_body_size/TCP_INIT_CWND_SIZE_IN_BYTES) / math.log(TCP_CUBIC_MAX_GROWTH_RATE))
    # Note, if counter_cwnd_changes == 0, then this has no effect
    # Here we need to add the Round Trip Time (RTT == 2 * delay), as we alsways need to wait for the ack (one delay), and then send again the second part (again one delay)
    # Note, the first delay is already captured in the normal estimation
    cwnd_time = counter_cwnd_changes * 2 * network_setting.get_delay_in_seconds()
    # Now addtional acks are sent, we assume this is symmetric for all parties
    cwnd_ack_size = counter_cwnd_changes * ack_size * n_parties


    # Get Totals/sums
    estimated_time_cpu_total = sum(estimated_time_cpu.values())
    estimated_time_net_total = sum(estimated_time_net.values()) + cwnd_time
    estimated_init_global_data = init_estimation.get_init_global_data(n_parties)
    estimated_global_traffic_total = sum(estimated_global_traffic.values()) + estimated_init_global_data + cwnd_ack_size
    estimated_global_mpspdz_traffic_total = sum(estimated_global_mpspdz_traffic.values())
    estimated_number_of_steps_total = sum(estimated_number_of_steps.values())
    estimated_time_total = estimated_time_net_total + estimated_time_cpu_total

    # Find unknown instruction and count occurance
    unknown_instructions = {}
    for asm in list_of_asm_instructions:
        asm_name = asm.instruction_name

        if asm_name not in cpu_estimations and asm_name != "jmpnz":
            if asm_name not in unknown_instructions:
                unknown_instructions[asm_name] = 0
            unknown_instructions[asm_name] += math.prod(asm.multiplier)

    # Prepare output
    totals = {"all-time": estimated_time_total, 
              "cpu-time": estimated_time_cpu_total, 
              "net-time": estimated_time_net_total, 
              "global-data": estimated_global_traffic_total,
              "global-mpspdz-data": estimated_global_mpspdz_traffic_total,
              "number-of-steps": estimated_number_of_steps_total,
              "counter-cwnd-changes": counter_cwnd_changes,
              "max-single-message": estimated_max_single_message}
    individuals = {"cpu-time": estimated_time_cpu,
                   "net-time": estimated_time_net,
                   "global-datas": estimated_global_traffic,
                   "init-global-data": estimated_init_global_data,
                   "global-mpspdz-datas": estimated_global_mpspdz_traffic,
                   "number-of-steps": estimated_number_of_steps}

    return {"totals": totals,
            "individuals": individuals,
            "unknown": unknown_instructions}


def print_estimation(estimation, print_details=False):
    if print_details:
        print("\n>>> CPU TIME")
        for v, k in sorted([(v, k) for k,v in estimation["individuals"]["cpu-time"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v}')
        print("\n>>> Net TIME")
        for v, k in sorted([(v, k) for k,v in estimation["individuals"]["net-time"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v}')
        print("\n>>> Net Global Traffic")
        for v, k in sorted([(v, k) for k,v in estimation["individuals"]["global-datas"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v/10**6} MB')
        print(f'   INIT = {estimation["individuals"]["init-global-data"]/10**6} MB')
        print("\n>>> MP-SPDZ Global Traffic")
        for v, k in sorted([(v, k) for k,v in estimation["individuals"]["global-mpspdz-datas"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v/10**6} MB')
        print("\n>>> Steps (Rounds)")
        for v, k in sorted([(v, k) for k,v in estimation["individuals"]["number-of-steps"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v}')
        print("\n>>> UNKNOWN INSTRUCTIONS<<<")
        for v, k in sorted([(v, k) for k,v in estimation["unknown"].items()], reverse=True):
            if v != 0:
                print(f'   {k} = {v}')

    print("\n>>> SUMMARY")
    print(f'       Total: {estimation["totals"]["all-time"]} s')
    print(f'         CPU: {estimation["totals"]["cpu-time"]} s')
    print(f'         NET: {estimation["totals"]["net-time"]} s')
    print(f'        Data: {estimation["totals"]["global-data"]/10**6} MB')
    print(f'Steps/Rounds: {estimation["totals"]["number-of-steps"]}')
    print(f'Data MP-SPDZ: {estimation["totals"]["global-mpspdz-data"]/10**6} MB')
    print()

