#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.helper import join_path_abs, get_logger
from xenonMetadata import InstructionTestSet, ASMInstruction, EstimationComputation, EstimationNetwork, EstimationNetworkStep, NetworkMetaTraffic, EstimationInit, \
                              compute_r_squared, compute_mse
from xenonConfig import USE_SYMPY, INSTRUCTIONS_TO_IGNORE, PATH_TO_TEMP, PATH_TO_ESTIMATIONS, \
                            NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, \
                            COMPUTATION_TEST_PARAMETERS, NUMBER_OF_CPU_TEST_REPETITIONS, \
                            INIT_TEST_PARAMETERS, NUMBER_OF_INIT_TEST_REPETITIONS, \
                            REGRESSION_COMPUTATION_BY_NAME, REGRESSION_COMPUTATION_VALUE_BY_NAME, \
                            REGRESSION_NETWORK_SEND_REC_MAX, REGRESSION_NETWORK_GLOBAL, \
                            REGRESSION_BATCHSIZE_SEND_REC_MAX, REGRESSION_BATCHSIZE_GLOBAL, \
                            REGRESSION_NET_BCH_SEND_VALUE, REGRESSION_NET_BCH_REC_VALUE, \
                            REGRESSION_NET_BCH_GLOBAL_VALUE, REGRESSION_NET_BCH_MPSPDZ_GLOBAL_VALUE, \
                            REGRESSION_NET_BCH_MAX_SINGLE_MESSAGE_VALUE, \
                            REGRESSION_NET_BCH_NUM_MESSAGES, REGRESSION_NET_BCH_NUM_MESSAGES_VALUE, \
                            REGRESSION_INIT_GLOBAL_DATA, REGRESSION_INIT_GLOBAL_DATA_VALUE \
                            

import os
import re
import math

import matplotlib.pyplot as plt
import numpy
import sympy
from tqdm import tqdm
from multiprocessing.pool import Pool


import logging
logger = get_logger("XENON Estimation Functions")

PLOT_LOGARITHMIC = False


class InstructionTestASMComputation:
    """
    This class represents the CPU timing
    """

    def __init__(self, asm_instructions, vector_length, number_of_parties, cpu_time):
        self.instruction_name = asm_instructions[0].instruction_name
        self.number_of_asm_instructions = len(asm_instructions)

        self.vector_length = vector_length
        self.number_of_parties = number_of_parties
        self.cpu_time = cpu_time

class EmptyInstructionTestASMComputation:
    """
    This class is used only for estimating the time a timer uses during a benchmark
    """

    def __init__(self, instruction_name, number_of_parties, cpu_time):
        self.instruction_name = instruction_name
        self.number_of_asm_instructions = 0

        self.vector_length = 0
        self.number_of_parties = number_of_parties
        self.cpu_time = cpu_time

class InstructionTestASMNetwork:
    """
    Represents one corresponding Test set
    """

    def __init__(self, asm, instructionConfig, networkMeta):
        self.instruction_name = asm.instruction_name
        # self.parameters = asm.parameters

        self.number_of_parties = networkMeta.n_parties
        self.vector_length = instructionConfig.vector_length
        
        self.list_of_steps = networkMeta.list_of_steps

        self.mpspdz_global_data = networkMeta.mpspdz_global_data

      
    def __str__(self):
        output =  f"ASM: {self.instruction_name}\n"
        # output += f"   parameters      = {self.parameters}\n"
        output += f"   n_parties       = {self.number_of_parties}\n"
        output += f"   vector_length   = {self.vector_length}\n"
        output += f"   mpspdz_data     = {self.mpspdz_global_data}\n"
        output += f"   list_of_steps   = {self.list_of_steps}"
        return output

class InitTestRepresentation:
    """
    This class is used to represent the global data measurements of the connection and closing phase (init)
    """

    def __init__(self, number_of_parties, global_data):
        self.number_of_parties = number_of_parties
        self.global_data = global_data

class AckTestRepresentation:
    """
    This class is used to represent the ack size
    """

    def __init__(self, number_of_parties, ack_size):
        self.number_of_parties = number_of_parties
        self.ack_size = ack_size

def multi_linear_regression(list_of_items, extract_points_and_value, logging_name=""):
    """
    Do Multilinear regression

    list_of_items: complete list of items
    extract_points_and_values: function that returns a tuple (array, value)
        which mapps an itam from the list, to the points for regression (e.g. [1, n, n*v]) and a value (e.g. y)
    """
    list_of_points = []
    list_of_values = []
    for itam in list_of_items:
        points, value = extract_points_and_value(itam)
        list_of_points.append(points)
        list_of_values.append(value)
    # logger.debug(f"List of Points for regression {logging_name}: {list_of_points}")
    # logger.debug(f"List of {logging_name} Z-Values for regression: {list_of_values}")

    # Perform the actual regression
    X = sympy.Matrix(list_of_points)
    X_combined = (X.T * X).inv() * X.T
    tmp_beta = X_combined * sympy.Matrix([list_of_values]).T

    # Save as sympy or numpy
    if USE_SYMPY:
        beta = tmp_beta.T.tolist()[0]
    else:
        beta = numpy.array(tmp_beta.T).astype(numpy.float64)[0].tolist()
    logger.debug(f"Found values with regression for Beta {logging_name}: {beta}")
    return beta


def plot_regression(list_of_items_used_for_regression, all_list_of_items, extract_x_y_z, get_z_for_plot, directory_for_plots, file_name_and_title, x_label="Number of Parties", y_label="Vector Length"):
    # Actual points
    x_y_z_actual_all = []
    x_y_z_verify_all = []
    for itam in all_list_of_items:
        txa, tya, tza = extract_x_y_z(itam)
        # Additionally add "0" for the empty test
        if itam in list_of_items_used_for_regression:
            x_y_z_actual_all.append((txa, tya, tza))
        else:
            x_y_z_verify_all.append((txa, tya, tza))

    max_x = max([x for x, _, _ in x_y_z_actual_all + x_y_z_verify_all])
    max_y = max([y for _, y, _ in x_y_z_actual_all + x_y_z_verify_all])

    # Store overall confidentialities
    conf_r2_actual_all = -math.inf
    conf_r2_verify_all = -math.inf
    conf_r2_both_all = -math.inf

    # Plot the max sending and receving values w.r.t. number of parties and vector length
    fig = plt.figure(figsize=(20, 15))
    num_plots = 6
    for i in range(num_plots):
        # y is vector length
        threshold_y = max_y / 10**i

        x_actual = []
        y_actual = []
        z_actual = []
        x_verify = []
        y_verify = []
        z_verify = []
        for x, y, z in x_y_z_actual_all:
            if y <= threshold_y:
                x_actual.append(x)
                y_actual.append(y)
                z_actual.append(z)
        for x, y, z in x_y_z_verify_all:
            if y <= threshold_y:
                x_verify.append(x)
                y_verify.append(y)
                z_verify.append(z)

        x_all = set(x_actual).union(set(x_verify))
        y_all = set(y_actual).union(set(y_verify))

        if not x_all or not y_all:
            logger.debug(f"Plot is empty. Skipping i={i} for{file_name_and_title}")
            continue

        # get max and 2nd max
        x_max = sorted(list(x_all))[-2:]
        y_max = sorted(list(y_all))[-2:]

        if len(y_max) == 1:
            y_max.append(y_max[0]+1)

        # generate Values for Plotting of the regression
        # x_plot = sorted(x_all.union(set(range(0,max(round(x_max[1]*1.5),10),x_max[1]-x_max[0]))))
        # y_plot = sorted(y_all.union(set(range(0,max(round(y_max[1]*1.5),10),y_max[1]-y_max[0]))))
        x_plot = sorted(set(x_all))
        y_plot = sorted(set(y_all))
        
        x_lookup = {x:i for i, x in enumerate(x_plot)}               
        y_lookup = {y:i for i, y in enumerate(y_plot)}
        
        # Make meshgrid
        X, Y = numpy.meshgrid(x_plot, y_plot)
        # Simple stupid way to generate Z grid
        # Regressions IMPORTANT: leave th .0 so that we can get floating point numbers
        R= X*0.0
        # Fill data
        for x in x_plot:
            for y in y_plot:
                tmp_x = x_lookup[x]
                tmp_y = y_lookup[y]
                R[tmp_y][tmp_x] = get_z_for_plot(x, y)

        # Confidentialities
        z_est_actual = []
        for x, y in zip(x_actual, y_actual, strict=True):
            z_est_actual.append(get_z_for_plot(x, y))
        z_est_verify = []
        for x, y in zip(x_verify, y_verify, strict=True):
            z_est_verify.append(get_z_for_plot(x, y))
        # R squared
        conf_r2_actual = compute_r_squared(z_actual, z_est_actual)
        conf_r2_verify = compute_r_squared(z_verify, z_est_verify)
        conf_r2_both = compute_r_squared(z_actual + z_verify, z_est_actual + z_est_verify)
        if i == 0:
            conf_r2_actual_all = conf_r2_actual
            conf_r2_verify_all = conf_r2_verify
            conf_r2_both_all = conf_r2_both

        # Mean squared Error
        conf_mse_actual = compute_mse(z_actual, z_est_actual)
        conf_mse_verify = compute_mse(z_verify, z_est_verify)
        conf_mse_both = compute_mse(z_actual + z_verify, z_est_actual + z_est_verify)
        # Root Mean squared error
        conf_rmse_actual = math.sqrt(conf_mse_actual)
        conf_rmse_verify = math.sqrt(conf_mse_verify)
        conf_rmse_both = math.sqrt(conf_mse_both)

        logger.debug(f"R2: Actual={conf_r2_actual}, Verify={conf_r2_verify}, Both={conf_r2_both}")
        logger.debug(f"MSE: Actual={conf_mse_actual}, Verify={conf_mse_verify}, Both={conf_mse_both}")
        logger.debug(f"RMSE: Actual={conf_rmse_actual}, Verify={conf_rmse_verify}, Both={conf_rmse_both}")


        # Plot
        ax = fig.add_subplot(231 + i, projection='3d')
        ax.xaxis.set_ticks(x_plot)
        ax.yaxis.set_ticks(y_plot)
        ax.set_xlim(min(x_plot), max(x_plot))
        ax.set_ylim(min(y_plot), max(y_plot))
        ax.set_zlim(min(z_actual+z_verify+z_est_actual+z_est_verify), max(z_actual+z_verify+z_est_actual+z_est_verify))

        # Make that the full value is shown
        ax.zaxis.set_major_formatter('{x:.1e}')

        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.title(f"{file_name_and_title}\n"
                f"R2: Train={conf_r2_actual}\n"
                f"     Verify={conf_r2_verify}\n"
                f"       Both={conf_r2_both}\n" 
                # f"MSE: Train={conf_mse_actual:.3e}, Verify={conf_mse_verify:.3e}, Both={conf_mse_both:.3e}\n"
                f"RMSE: Train={conf_rmse_actual:.3e}\n"
                f"         Verify={conf_rmse_verify:.3e}\n"
                f"           Both={conf_rmse_both:.3e}",
                loc='left')
        # plt.title(f"{file_name_and_title}\n"
        #            f"R2: Actual={conf_r2_actual}\n" 
        #            f"MSE: Actual={conf_mse_actual}\n"
        #            f"RMSE: Actual={conf_rmse_actual}")

        if PLOT_LOGARITHMIC:
            # Logarithmic scale
            ax.scatter(x_actual, y_actual, numpy.log10(z_actual), color="red", label="Train", marker="^")
            ax.scatter(x_verify, y_verify, numpy.log10(z_verify), color="blue", label="Verify", marker="o")
            ax.plot_wireframe(X, Y, numpy.log10(R), color="green", alpha=0.5, label="Regression")
        else:
            ax.scatter(x_actual, y_actual, z_actual, color="red", label="Train", marker="^")
            ax.scatter(x_verify, y_verify, z_verify, color="blue", label="Verify", marker="o")
            ax.plot_wireframe(X, Y, R, color="green", alpha=0.5, label="Regression")
    
        
        plt.legend()
        
        # Rotate a bit
        ax.view_init(30, -135)

    # Always save the figure
    plt.savefig(join_path_abs(directory_for_plots, file_name_and_title), dpi=100, bbox_inches='tight')
    
    # uncomment if you want to see the plots
    # if logger.level <= logging.DEBUG:
    #     manager = plt.get_current_fig_manager()
    #     manager.window.showMaximized()
    #     plt.show()

    plt.close()
    if conf_r2_actual_all == conf_r2_verify_all == conf_r2_both_all == 1:
        return {"perfect": True}
    elif not x_y_z_verify_all and conf_r2_actual_all == 1:
        return {"perfect": True}
    else:
        return {"perfect": False, "actual":conf_r2_actual_all, "verify": conf_r2_verify_all, "both": conf_r2_both_all}


def load_instruction_config_and_asm_instruction(instruction_tests_dir, instruction_file, asm_instruction_dir):
    """
    Read instruction config, and load corresponding asm instructions
    """
    # Remove ".conf"
    instruction_test_name = instruction_file[:-5] 
    logger.debug(f"Loading {instruction_test_name}")

    configs_and_asm = []

    ### Get Instruction parameters
    instruction_test_set = InstructionTestSet.from_json_file(join_path_abs(instruction_tests_dir, instruction_file))

    for instruction_config in instruction_test_set.test_set:
        # First get the ASM file
        asm_file_name = f"{instruction_test_name}_[v={instruction_config.vector_length}].asm"
        with open(join_path_abs(asm_instruction_dir, asm_file_name), 'r') as f:
            # Filter only the isntructions that should not be ignored (including possible comments)
            asm_instructions = [ASMInstruction(line) for line in f.readlines() if line.split(" ")[0] not in INSTRUCTIONS_TO_IGNORE and not line.startswith("#")]
            if not asm_instructions and not instruction_test_name == "empty":
                # If no instructions are present, then someting went wrong somewhere
                logger.critical(f"No instructions present for {instruction_test_name} with ID={instruction_config.test_id}")
                exit(1)
            for ai in asm_instructions[1:]:
                # if some are different, abort
                if asm_instructions[0].instruction_name != ai.instruction_name:
                    logger.error(f"Not all instructions are the same in {asm_file_name}")
                    exit(1)                        
            
            configs_and_asm.append((instruction_config, asm_instructions))
    return (instruction_test_name, instruction_test_set.test_type, configs_and_asm)


def create_basic_directories_and_get_files(test_parameters):
    """
    Get basic directoy names (instruction test set, asm isntructions)
    Create basic directoroes (estimation, plot)
    Get list of iles in instruction_test_dir
    """

    if test_parameters == COMPUTATION_TEST_PARAMETERS:
        # Additionally crate the directory for cpu timings
        cpu_timings_dir = join_path_abs(PATH_TO_TEMP, "cpu_timings")
    else:
        cpu_timings_dir = None

    if test_parameters in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]:
        instruction_tests_dir = join_path_abs(PATH_TO_TEMP, f"instruction_tests_{test_parameters.suffix}")
        asm_instructions_dir = join_path_abs(PATH_TO_TEMP, f"asm_instructions_{test_parameters.suffix}")
        estimation_dir = join_path_abs(PATH_TO_ESTIMATIONS, f"{test_parameters.suffix}")
        plot_dir = join_path_abs(PATH_TO_ESTIMATIONS, "plots", f"{test_parameters.suffix}")

        # Get files
        instruction_tests_files = [f for f in os.listdir(instruction_tests_dir) if f.endswith(".json")]
        instruction_tests_files.sort()
    else:
        estimation_dir = PATH_TO_ESTIMATIONS
        plot_dir = join_path_abs(PATH_TO_ESTIMATIONS, "plots")

    # Create directories
    if not os.path.exists(estimation_dir):
        os.makedirs(estimation_dir)

    if not os.path.exists(plot_dir):
        os.makedirs(plot_dir)
    
    if test_parameters in [NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]:
        return instruction_tests_dir, instruction_tests_files, cpu_timings_dir, asm_instructions_dir, estimation_dir, plot_dir
    else:
        return estimation_dir, plot_dir


def do_single_net_regression(instruction_file, instruction_tests_dir, test_parameters,
                             asm_instructions_dir, estimation_dir, plot_dir,
                             regression_type):
    """
    Perform the Network/Batch size regression for a single instruction
    """
    logger.debug(f"Doing {test_parameters.suffix.upper()}: {instruction_file[:-5]}")
    # Load config and asm isntructions
    instruction_test_name, ins_test_type, configs_and_asm = load_instruction_config_and_asm_instruction(instruction_tests_dir, instruction_file, asm_instructions_dir)

    if ins_test_type != regression_type:
        logger.critical(f"WRONG Test type for {instruction_test_name}: Expected '{regression_type}' but got '{ins_test_type}'")
        exit(1)

    network_tests = []
    for instruction_config, asm_instructions in configs_and_asm:
        # Get the network meta for each number of parties
        for n_parties in sorted(set(test_parameters.parties + test_parameters.additional_parties)):
            if regression_type == "network-only":
                tmp_batch_size = 0
            elif regression_type == "network-batch":
                tmp_batch_size = instruction_config.vector_length
            
            network_meta_name = f"{instruction_test_name}_[n={n_parties},v={instruction_config.vector_length},b={tmp_batch_size}].json"
            network_meta = NetworkMetaTraffic.from_json_file(join_path_abs(PATH_TO_TEMP, "network_models", network_meta_name))

            if len(asm_instructions) == 1:
                # single instruction (vectorized or parallel)
                instruction_test_asm_network = InstructionTestASMNetwork(asm_instructions[0], instruction_config, network_meta)
            elif len(asm_instructions) == instruction_config.vector_length:
                # Now we have multiple asm instructions
                # This can occur for non parallelizable commands
                # Here, the number of instrucions must equal the vector length
                instruction_test_asm_network = InstructionTestASMNetwork(asm_instructions[0], instruction_config, network_meta)
            else:
                # We assume that this should not occur, so this is not yet implemented, and only done if required later
                logger.critical(f"We do not support multiple Instructions with different number of instructions and vector_length")
                exit(1)
            network_tests.append(instruction_test_asm_network)

    
    # Check if all executed tests belong to the same command
    for itam in network_tests:
        if itam.instruction_name != instruction_test_name:
            logger.critical(f"Instructions in Test ({instruction_test_name}) do not match: Problem with {itam.instruction_name}")
            exit(1)

    logger.debug(f"Number of executed Network Tests: {len(network_tests)}")

    # Check that number of steps are all the same
    set_of_number_of_steps = set([len(nt.list_of_steps) for nt in network_tests])
    if len(set_of_number_of_steps) == 1:
        number_of_steps = set_of_number_of_steps.pop()
    else:
        logger.critical(f"We do not support different number of steps. Steps in '{instruction_test_name}' are: {set_of_number_of_steps}\nNote, this might be just a capture problem. If it occurs only once, try redoing that specific test. Otherwise, you might need to execute NEON_MAIN_TEST in the PRE TEST section in xenon_net_benchmark multiple times. See xenonConfig.py for some details.")
        for nt in network_tests:
            logger.debug(f"{test_parameters.suffix.upper()} test: {nt}")
        exit(1)

    # Get the points for regression
    # For Regression use only the defined testpoints (not the ones for verification)
    tmp_list_of_itams = [itam for itam in network_tests \
                                if itam.number_of_parties in test_parameters.parties \
                                and itam.vector_length in test_parameters.vector_lengthes]

    # To verify quality of regrssions
    # As network is typically perfect, we only store whether all or none are prefect
    # Used for output later
    all_r2s = []

    if regression_type == "network-only":
        regression_send_rec_max = REGRESSION_NETWORK_SEND_REC_MAX
        regression_global = REGRESSION_NETWORK_GLOBAL
    elif regression_type == "network-batch":
        regression_send_rec_max = REGRESSION_BATCHSIZE_SEND_REC_MAX
        regression_global = REGRESSION_BATCHSIZE_GLOBAL
    else:
        logger.critical(f"Unknown regression type: {regression_type}")
        exit(1)

    # Just do multi linear regression for each step
    # We assume the number of steps does not change with the number of parties or vector_length
    list_of_beta_send_and_rec = []
    for step_i in range(number_of_steps):
        # Note, we do not need to check whether no communication is used, as the for loop will not be executed, and thus list_of_beta_send_and_rec == []
        
        # Perform the actual regression
        tmp_beta_send = multi_linear_regression(tmp_list_of_itams, lambda x: (regression_send_rec_max(x.number_of_parties, x.vector_length),
                                                                              REGRESSION_NET_BCH_SEND_VALUE(x, step_i)),
                                                                              logging_name=f"Sending")
        tmp_beta_rec = multi_linear_regression(tmp_list_of_itams, lambda x: (regression_send_rec_max(x.number_of_parties, x.vector_length),
                                                                             REGRESSION_NET_BCH_REC_VALUE(x, step_i)),
                                                                             logging_name=f"Receiving")
        tmp_beta_global = multi_linear_regression(tmp_list_of_itams, lambda x: (regression_global(x.number_of_parties, x.vector_length),
                                                                                REGRESSION_NET_BCH_GLOBAL_VALUE(x, step_i)),
                                                                                logging_name=f"Global")
        tmp_beta_num_messages = multi_linear_regression(tmp_list_of_itams, lambda x: (REGRESSION_NET_BCH_NUM_MESSAGES(x.number_of_parties, x.vector_length),
                                                                                      REGRESSION_NET_BCH_NUM_MESSAGES_VALUE(x, step_i)),
                                                                                      logging_name=f"Num Msg")
        tmp_beta_max_single = multi_linear_regression(tmp_list_of_itams, lambda x: (regression_send_rec_max(x.number_of_parties, x.vector_length),
                                                                                    REGRESSION_NET_BCH_MAX_SINGLE_MESSAGE_VALUE(x, step_i)),
                                                                                    logging_name=f"Max Single")    
        beta_send_rec_estimation_steps = EstimationNetworkStep(tmp_beta_send, tmp_beta_rec, tmp_beta_global, tmp_beta_num_messages, tmp_beta_max_single, regression_type)
        list_of_beta_send_and_rec.append(beta_send_rec_estimation_steps)

        # Generate Plots and compute R2
        r2_send = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.list_of_steps[step_i].max_sending_data),
            lambda x, y: beta_send_rec_estimation_steps.get_network_data(x, y)[0],
            plot_dir, f"{instruction_test_name} [snd, step={step_i + 1} (of {len(network_tests[0].list_of_steps)})]")
        all_r2s.append(r2_send)

        r2_rec = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.list_of_steps[step_i].max_receiving_data),
            lambda x, y: beta_send_rec_estimation_steps.get_network_data(x, y)[1],
            plot_dir, f"{instruction_test_name} [rcv, step={step_i + 1} (of {len(network_tests[0].list_of_steps)})]")
        all_r2s.append(r2_rec)

        r2_global = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.list_of_steps[step_i].global_data),
            lambda x, y: beta_send_rec_estimation_steps.get_network_data(x, y)[2],
            plot_dir, f"{instruction_test_name} [tot, step={step_i + 1} (of {len(network_tests[0].list_of_steps)})]")
        all_r2s.append(r2_global)

        r2_num_messages = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.list_of_steps[step_i].num_messages),
            lambda x, y: beta_send_rec_estimation_steps.get_network_data(x, y)[3],
            plot_dir, f"{instruction_test_name} [num, step={step_i + 1} (of {len(network_tests[0].list_of_steps)})]")
        all_r2s.append(r2_num_messages)

        r2_max_single = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.list_of_steps[step_i].max_single_message),
            lambda x, y: beta_send_rec_estimation_steps.get_network_data(x, y)[4],
            plot_dir, f"{instruction_test_name} [max, step={step_i + 1} (of {len(network_tests[0].list_of_steps)})]")
        all_r2s.append(r2_max_single)

        # No plot for number of messages
        

    # And do it once for the MP-SPDZ global data
    tmp_beta_mpspdz_global = multi_linear_regression(tmp_list_of_itams, lambda x: (regression_global(x.number_of_parties, x.vector_length),
                                                                                   REGRESSION_NET_BCH_MPSPDZ_GLOBAL_VALUE(x)),
                                                                                   logging_name=f"MP-SPDZ Global")

    # Add the single beta_register and the list of beta_send and beta_receiving
    estimation = EstimationNetwork(list_of_beta_send_and_rec, tmp_beta_mpspdz_global, regression_type)

    # Plot the mp-spdz global data
    r2_mpspdz = plot_regression(tmp_list_of_itams, network_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.mpspdz_global_data) ,
            lambda x, y: estimation.get_mpspdz_global_data(x, y),
            plot_dir, f"{instruction_test_name} [mpspdz-global]")
    all_r2s.append(r2_mpspdz)

    logger.debug(estimation)

    # Write it to file
    with open(join_path_abs(estimation_dir, f"{instruction_test_name}.json"), 'w') as f:
        logger.debug(f"WRITING {test_parameters.suffix.upper()} Estimation for: {instruction_test_name}")
        f.write(estimation.to_json())

    # If at least one is not perfect, return False, else True
    for r2 in all_r2s:
        if not r2["perfect"]:
            return instruction_test_name, False
    return instruction_test_name, True


def do_single_cpu_regression(instruction_cpu_file, empty_regression, instruction_tests_cpu_dir, cpu_timings_dir, asm_instructions_cpu_dir, estimation_cpu_dir, plot_cpu_dir ):
    """
    Do CPU Regression for a single instruction
    """
    logger.debug(f"Doing CPU: {instruction_cpu_file[:-5]}")
    # Load config and asm isntructions
    instruction_test_name, ins_test_type, configs_and_asm = load_instruction_config_and_asm_instruction(instruction_tests_cpu_dir, instruction_cpu_file, asm_instructions_cpu_dir)
    
    computation_tests = []
    for instruction_config, asm_instructions in configs_and_asm:
        # Get the network meta for each number of parties
        for n_parties in sorted(set(COMPUTATION_TEST_PARAMETERS.parties + COMPUTATION_TEST_PARAMETERS.additional_parties)):
            if ins_test_type == "network-batch":
                tmp_batch_size = instruction_config.vector_length
            else:
                tmp_batch_size = 0
            cpu_file_name= f"{instruction_test_name}_[n={n_parties},v={instruction_config.vector_length},b={tmp_batch_size}].txt"
            
            with open(join_path_abs(cpu_timings_dir, cpu_file_name), 'r') as f:
                cpu_timings = [float(line.strip()) for line in f.readlines()]

            # Ensure the correct number of Repititions selected
            # Ensures two things:
            # 1) that we have at least the number of specified repititions
            # and 2) that we do not use more than specified
            if len(cpu_timings) != NUMBER_OF_CPU_TEST_REPETITIONS:
                logger.critical(f"Number of expected Test Repetitions (CPU) missmatch. Got {len(cpu_timings)} and expected {NUMBER_OF_CPU_TEST_REPETITIONS} for {cpu_file_name}")
                exit(1)
            # Use minmal or a fast average
            # for minimal:
            cpu_timings = [min(cpu_timings)]
            # for faster average:
            # cpu_timings = [sum(cpu_timings)/len(cpu_timings)]
            

            for tmp_computation_time in cpu_timings:
                if instruction_test_name != "empty":
                    # Important substract the time for timers/start/stop (empty_regression)
                    tmp_computation_time -= empty_regression.get_computation_time(n_parties, 0)

                if instruction_test_name == "empty":
                    if not empty_regression:
                        computation_tests.append(EmptyInstructionTestASMComputation("empty", n_parties, tmp_computation_time))
                    else:
                        logger.critical("FOUND multiple empty regressions. Aborting")
                        exit(1)
                else:
                    computation_tests.append(InstructionTestASMComputation(asm_instructions, instruction_config.vector_length, n_parties, tmp_computation_time))

    
    # Check if all executed tests belong to the same command
    for itam in computation_tests[1:]:
        if itam.instruction_name != computation_tests[0].instruction_name:
            logger.critical(f"Instructions in Test ({instruction_test_name}) do not match:. First={computation_tests[0].instruction_name}, Other={itam.instruction_name}")
            exit(1)

    logger.debug(f"Number of executed computation Tests: {len(computation_tests)}")


    # Get the points for regression
    # For Regression use only the defined testpoints (not the ones for verification)
    if instruction_test_name == "empty" and ins_test_type == "empty":
        # empry benchmark has vector_length = 0
        tmp_list_of_itams = [itam for itam in computation_tests \
                                if itam.number_of_parties in COMPUTATION_TEST_PARAMETERS.parties
                                and itam.vector_length == 0]
    else:
        tmp_list_of_itams = [itam for itam in computation_tests \
                                if itam.number_of_parties in COMPUTATION_TEST_PARAMETERS.parties \
                                and itam.vector_length in COMPUTATION_TEST_PARAMETERS.vector_lengthes]

    
    # Perform the actual regression
    # Note, the empty one has no instruction and 0 vector legnth, so is covered here to
    if ins_test_type == "empty" and instruction_test_name == "empty":
        # For empty instruction (overhead of timers start/stop) we just the time itself
        # Note, we cannot devide by zero, therefore separate regression
        regression_type = "empty"
    elif ins_test_type in ["local-single", "local-vector"]:
        # No network traffic equals only local computations
        # So we assume it does not depend on the number of parties, only on the vector length
        if all([itam.number_of_asm_instructions == itam.vector_length for itam in tmp_list_of_itams]):
            # For single instructions number of instructions equals the vector length
            # To improve accuracy, we use only the largest vector size for the estimation (over different number of parties)
            tmp_list_of_itams = [itam for itam in tmp_list_of_itams if itam.vector_length == max(COMPUTATION_TEST_PARAMETERS.vector_lengthes)]
            regression_type = "local-single"
        elif all([itam.number_of_asm_instructions == 1 for itam in tmp_list_of_itams]):
            # So it must be a vectorized instruction with no network traffic
            # Can depend on vector lenght, or better, can have an offset
            regression_type = "local-vector"
        else:
            logger.critical("Regression case not covered (local)")
            exit(1)
    elif ins_test_type in ["network-only", "network-batch"]:
        if all([itam.number_of_asm_instructions == 1 for itam in tmp_list_of_itams]):
            # Only this part depends on the number of parties, so
            # They are either a parallel command or a "vector"-asm instruction
            # can be with batchsize
            regression_type = "parallel"
        elif all([itam.number_of_asm_instructions == itam.vector_length for itam in tmp_list_of_itams]):
            # Batch instruction non vectorized
            regression_type = "parallel"
        else:
            logger.critical("Regression case not covered (network)")
            exit(1)
    else:
        logger.critical("Regression case not covered (no fitting type)")
        exit(1)
    
    logger.debug(f"Number of points for regression: {len(tmp_list_of_itams)}")
    logger.debug(f"Regression Type: {regression_type}")
    
    beta_cpu = multi_linear_regression(tmp_list_of_itams, lambda x: (REGRESSION_COMPUTATION_BY_NAME[regression_type](x.number_of_parties, x.vector_length),
                                                                    REGRESSION_COMPUTATION_VALUE_BY_NAME[regression_type](x)),
                                                                    logging_name=f"CPU")
    cpu_estimation = EstimationComputation(beta_cpu, regression_type)

    logger.debug(cpu_estimation)

    if regression_type in ["local-single"]:
        # Devide the measured runtimes by the vector_length
        r2 = plot_regression(tmp_list_of_itams, computation_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.cpu_time/x.vector_length),
            lambda x, y: cpu_estimation.get_computation_time(x, y),
            plot_cpu_dir, f"{instruction_test_name} [cpu]")
    else:
        r2 = plot_regression(tmp_list_of_itams, computation_tests,
            lambda x: (x.number_of_parties, x.vector_length, x.cpu_time),
            lambda x, y: cpu_estimation.get_computation_time(x, y),
            plot_cpu_dir, f"{instruction_test_name} [cpu]")
       
    # Write it to file
    with open(join_path_abs(estimation_cpu_dir, f"{instruction_test_name}.json"), 'w') as f:
        logger.debug(f"WRITING CPU Estimation for: {instruction_test_name}")
        f.write(cpu_estimation.to_json())

    return cpu_estimation, (instruction_test_name, r2)



def do_init_regression(estimation_init_dir, plot_init_dir):
    """
    Do the INIT Regression (Connection and Closing Phase global data)
    """
    logger.debug("Doing INIT")
        
    init_tests = []
    # Get the network meta for each number of parties
    for n_parties in sorted(set(INIT_TEST_PARAMETERS.parties + INIT_TEST_PARAMETERS.additional_parties)):
        init_global_datas = []
        for i in range(NUMBER_OF_INIT_TEST_REPETITIONS):

            init_file_name= f"init_global_data_[n={n_parties}]_{i+1}.txt"
            
            with open(join_path_abs(PATH_TO_TEMP, "network_models", init_file_name), 'r') as f:
                init_global_datas.append(int(f.readline().strip()))

        # for faster average:
        # init_global_datas = [sum(init_global_datas)/len(init_global_datas)]
        
        for tmp_init_bytes in init_global_datas:
            init_tests.append(InitTestRepresentation(n_parties, tmp_init_bytes))
    
    # Get the points for regression
    # For Regression use only the defined testpoints (not the ones for verification)
    tmp_list_of_itams = [itam for itam in init_tests \
                            if itam.number_of_parties in INIT_TEST_PARAMETERS.parties]
    
    logger.debug(f"Number of points for regression: {len(tmp_list_of_itams)}")
    
    beta_init = multi_linear_regression(tmp_list_of_itams, lambda x: (REGRESSION_INIT_GLOBAL_DATA(x.number_of_parties),
                                                                      REGRESSION_INIT_GLOBAL_DATA_VALUE(x)),
                                                                      logging_name=f"INIT")
    # TODO OWN CLASS FOR ESTIMATION    
    init_estimation = EstimationInit(beta_init)

    logger.debug(init_estimation)


    r2 = plot_regression(tmp_list_of_itams, init_tests,
        lambda x: (x.number_of_parties, 0, x.global_data), # Set vector length to zero, to reuse the same plotting
        lambda x, y: init_estimation.get_init_global_data(x),
        plot_init_dir, "global_data_init_compensation")
       
    # Write it to file
    with open(join_path_abs(estimation_init_dir, "global_data_init_compensation.json"), 'w') as f:
        logger.debug("WRITING INIT Estimation")
        f.write(init_estimation.to_json())

    return r2


def do_get_ack_size(estimation_ack_dir, plot_ack_dir):
    """
    Do the INIT Regression (Connection and Closing Phase global data)
    """
    logger.debug("Doing INIT")
        
    list_of_ack_sizes = []
    # Get the network meta for each number of parties
    for n_parties in sorted(set(INIT_TEST_PARAMETERS.parties + INIT_TEST_PARAMETERS.additional_parties)):
        for i in range(NUMBER_OF_INIT_TEST_REPETITIONS):
            with open(join_path_abs(PATH_TO_TEMP, "network_models", f"ack_size_[n={n_parties}]_{i+1}.txt"), 'r') as f:
                list_of_ack_sizes.append(AckTestRepresentation(n_parties,int(f.readline().strip())))

    # Get the actual points
    tmp_list_of_itams = [itam for itam in list_of_ack_sizes \
                            if itam.number_of_parties in INIT_TEST_PARAMETERS.parties]
    
    logger.debug(f"Number of ACK tests: {len(tmp_list_of_itams)}")
    
    # Get ACK size
    ack_sizes = set([x.ack_size for x in tmp_list_of_itams])
    if len(ack_sizes) != 1:
        logger.critical(f"ACK size not all the same: {ack_sizes}. Aborting.")
        exit(1)
    else:
        final_ack_size = ack_sizes.pop()

    # If we have more poitns, check them, too
    if list_of_ack_sizes != tmp_list_of_itams:
        ack_sizes_verify = set([x.ack_size for x in list_of_ack_sizes])
        if len(ack_sizes_verify) != 1:
            logger.critical(f"ACK size not all the same (with verification): {ack_sizes_verify}. Aborting.")
            exit(1)

    logger.debug(f"Found ACK size: {final_ack_size}")
       
    # Write it to file
    with open(join_path_abs(estimation_init_dir, "ack_size.txt"), 'w') as f:
        logger.debug("WRITING ACK Size")
        f.write(str(final_ack_size))

    return final_ack_size



##############################
# Simple commandline options #
##############################

generate_net_models = False
generate_bch_models = False
generate_cpu_models = False
generate_init_and_ack_models = False
if len(sys.argv) == 1:
    generate_net_models = True
    generate_bch_models = True
    generate_cpu_models = True
    generate_init_and_ack_models = True
elif len(sys.argv) == 2 and sys.argv[1] == "cpu":
    generate_cpu_models = True
elif len(sys.argv) == 2 and sys.argv[1] == "net":
    generate_net_models = True
elif len(sys.argv) == 2 and sys.argv[1] == "bch":
    generate_bch_models = True
elif len(sys.argv) == 2 and sys.argv[1] == "init+ack":
    generate_init_and_ack_models = True
else:
    logger.critical("Invalid Argument passed. Use no argument for performing network and computation time test. And use 'net', 'bch', 'cpu' or 'init+ack' for network, batchsize, computation time, or init-phase with ack size, respectively.")


############################
# Network and/or Batchsize #
############################

list_of_net_bch_test_parameters = []

if generate_net_models:
    list_of_net_bch_test_parameters.append(NETWORK_TEST_PARAMETERS)

if generate_bch_models:
    list_of_net_bch_test_parameters.append(BATCHSIZE_TEST_PARAMETERS)

for test_parameters in list_of_net_bch_test_parameters:
    logger.info(f"{test_parameters.suffix.upper()} Regression")
    
    # Get Basic Folders and files (ignore cpu timings dir)
    instruction_tests_dir, instruction_tests_files, _, asm_instructions_dir, estimation_dir, plot_dir = create_basic_directories_and_get_files(test_parameters)

    # Define regression type
    if test_parameters.suffix == NETWORK_TEST_PARAMETERS.suffix:
        regression_type = "network-only"
    elif test_parameters.suffix == BATCHSIZE_TEST_PARAMETERS.suffix:
        regression_type = "network-batch"
    else:
        logger.critical(f"Unknown test parameter suffix: {test_parameters.suffix}")
        exit(1)

    # Some stuff to be able to execute it in parallel
    all_net_r2s = {}
    def tmp_do_single_net_regression(instruction_file):
        return do_single_net_regression(instruction_file, instruction_tests_dir, test_parameters,
                                        asm_instructions_dir, estimation_dir, plot_dir,
                                        regression_type)

    if logger.level <= logging.DEBUG:
        for instruction_file in tqdm(instruction_tests_files, desc=f"{test_parameters.suffix.upper()}"):
            instruction_test_name, r2 = tmp_do_single_net_regression(instruction_file)
            all_net_r2s[instruction_test_name] = r2
    else:
        with Pool() as pool:
            for instruction_test_name, r2 in tqdm(pool.imap_unordered(tmp_do_single_net_regression, instruction_tests_files), total=len(instruction_tests_files), desc=f"{test_parameters.suffix.upper()}"):
                all_net_r2s[instruction_test_name] = r2

    # Check how good the regression is
    all_r2_good = True
    for instruction_test_name in sorted(all_net_r2s.keys()):
        if not all_net_r2s[instruction_test_name]:
            logger.warning(f"NON perfect regression: {instruction_test_name}")
            all_r2_good = False
    if all_r2_good:
        logger.info(f"Perfect Estimation for all instructions ({test_parameters.suffix})")


###############
# Computation #
###############

if generate_cpu_models:
    logger.info("CPU Regression")

    # Get Basic Folders and files
    instruction_tests_cpu_dir, instruction_tests_cpu_files, cpu_timings_dir, asm_instructions_cpu_dir, estimation_cpu_dir, plot_cpu_dir = create_basic_directories_and_get_files(COMPUTATION_TEST_PARAMETERS)

    # Make sure "empty.json" is the first (slightly more complicated than necessary, but fails if something is missing)
    tmp_empty_file = instruction_tests_cpu_files.pop(instruction_tests_cpu_files.index("empty.json"))

    # Storing confidentialities
    all_r2s = {}

    logger.info("Doing empty regression first")
    empty_regression, (instruction_test_name, r2) = do_single_cpu_regression(tmp_empty_file, None, instruction_tests_cpu_dir, cpu_timings_dir, asm_instructions_cpu_dir, estimation_cpu_dir, plot_cpu_dir )
    all_r2s[instruction_test_name] = r2

    # Some stuff to be able to execute it in parallel
    def tmp_do_single_cpu_regression(instruction_cpu_file):
        # Needed for multiprocessing
        return do_single_cpu_regression(instruction_cpu_file, empty_regression,
                                        instruction_tests_cpu_dir,
                                        cpu_timings_dir, asm_instructions_cpu_dir,
                                        estimation_cpu_dir, plot_cpu_dir)

    logger.info("Doing other regressions")
    if logger.level <= logging.DEBUG:
        for instruction_cpu_file in tqdm(instruction_tests_cpu_files, desc="CPU"):
            instruction_estimation, (instruction_test_name, r2) = tmp_do_single_cpu_regression(instruction_cpu_file)
            all_r2s[instruction_test_name] = r2
    else:
        with Pool() as pool:
            for instruction_estimation, (instruction_test_name, r2) in tqdm(pool.imap_unordered(tmp_do_single_cpu_regression, instruction_tests_cpu_files), total=len(instruction_tests_cpu_files), desc='CPU'):
                all_r2s[instruction_test_name] = r2
                pass
    
    # Check how good the regression is
    r2_threshold = 0.95 # this is considered still good
    r2_lines_to_print = {"bad":[], "good":[], "average":[]}
    # First determine if we have verificaiton points or not
    # and based on that determine which R2-Values to consider
    if COMPUTATION_TEST_PARAMETERS.additional_parties or COMPUTATION_TEST_PARAMETERS.additional_vector_lengthes:
        r2_verification_data_present = ["actual", "verify", "both"]
    else:
        r2_verification_data_present = ["actual"]
    # Check each instructuibs R2 value
    for instruction_test_name in sorted(all_r2s.keys()):
        r2 = all_r2s[instruction_test_name]
        # Create text output
        text_result_r2 = ""
        for r2_verify_data_name in r2_verification_data_present:
            text_result_r2 += f', {r2_verify_data_name}={r2[r2_verify_data_name]:5.3f}'
        # Remove additional space and comma
        text_result_r2 = f'({text_result_r2[2:]})'
        # Classifiy as "good", "average", or "bad"
        if all([r2_threshold <= r2[key] <= 1.0 for key in r2_verification_data_present]):
            # above the threshold and below 1.0, this is good
            r2_lines_to_print["good"].append(f'GOOD regression {instruction_test_name} {text_result_r2}')
        elif all([r2[key] == 0 for key in r2_verification_data_present]):
            # if R2 == 0, then in the regression we probably used only the average, as then R2 is always 0.
            # Could also happen if our regression accidentially un-wanted computes the average, but should be something else
            # However, we consider this very unlikely
            r2_lines_to_print["average"].append(f'AVERAGE (probably OK) regression {instruction_test_name} {text_result_r2}')
        else:
            r2_lines_to_print["bad"].append(f'BAD regression {instruction_test_name} {text_result_r2}')
    # Only in debug print good or average values
    for line_to_print in r2_lines_to_print["good"] + r2_lines_to_print["average"]:
        logger.debug(line_to_print)
    for line_to_print in r2_lines_to_print["bad"]:
        logger.warning(line_to_print)
    if not r2_lines_to_print["bad"]:
        logger.info(f"Good Estimation for all instructions (threshold={r2_threshold} or for average=0)")
    else:
        logger.info(f'{len(r2_lines_to_print["good"])} GOOD and {len(r2_lines_to_print["average"])} AVERAGE estimations are not shown as {r2_threshold} <= R2 <= 1.0 ("good") or R2 == 0 (probably "average")')
    if not COMPUTATION_TEST_PARAMETERS.additional_parties and not COMPUTATION_TEST_PARAMETERS.additional_vector_lengthes:
        logger.info(f"Note, R2 values are without verification data. Only points for regression itself are used.")


###################################################
# Connecting and Closing Phase Global Data (init) #
###################################################

if generate_init_and_ack_models:
    estimation_init_dir, plot_init_dir = create_basic_directories_and_get_files(INIT_TEST_PARAMETERS)
    # Do the init part
    init_r2 = do_init_regression(estimation_init_dir, plot_init_dir)
    logger.info(f'Regression for INIT: (Actual={init_r2["actual"]:6.3f}, Verify={init_r2["verify"]:6.3f}, Both={init_r2["both"]:6.3f})')

    # Get ACK size
    ack_size = do_get_ack_size(estimation_init_dir, plot_init_dir)
    logger.info(f"ACK size: {ack_size}")
