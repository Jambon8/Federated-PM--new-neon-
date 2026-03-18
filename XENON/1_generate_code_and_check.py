#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

from ProgramFiles.neonconfig import NeonConfig, ReportVerbosityLevel
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import protocol
from ProgramFiles import network
from ProgramFiles.helper import get_path_to_mpspdz, join_path_abs, get_logger
from xenonMetadata import InstructionConfig, InstructionTestSet, ASMInstruction, get_relevant_asm_instructions
from xenonConfig import NETWORK_TEST_PARAMETERS, BATCHSIZE_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS, \
                            INSTRUCTIONS_TO_DO, INSTRUCTIONS_TO_IGNORE, COMPILE_AND_CHECK, PATH_TO_TEMP,\
                            NUMBER_OF_CPU_TEST_REPETITIONS, INIT_TEST_PARAMETERS, NUMBER_OF_INIT_TEST_REPETITIONS, \
                            CHEK_TEST_NUM_PARTIES, CHEK_TEST_VECTOR_LENGTH, CHEK_TEST_REPEATS, CHEK_TEST_BATCH_SIZE_ONE, CHEK_TEST_BATCH_SIZE_TWO

import os
from tqdm import tqdm

import logging
logger = get_logger("XENON Code Generation")


def compile_and_check_instruction_test_set(instruction_test_set, instruction_name, test_params, neon):
    """
    Compiles the code and checks whether it is valid
    """

    logger.debug(f"Verifying {instruction_name}")

    duplication_results = []
    # Do it once normally, then with some duplication of the main_test
    # this allows to test correct vector legnth for vectorized-parallel commands
    for number_of_duplications in tqdm([1, 2, 3], desc="Duplication Check", leave=None):

        logger.debug(f"DOING test with {number_of_duplications} duplications")

        essential_program_asms = {}

        for instruction_config in tqdm(instruction_test_set.test_set, desc="Individual Check", leave=None):

            # Select the Programm to be executed
            if test_params == COMPUTATION_TEST_PARAMETERS:
                neon.set_program("xenon_cpu_benchmark")
                # Repetitions occur only in the Computation test
                neon.set_substitution("NEON_NUM_REPEATS", NUMBER_OF_CPU_TEST_REPETITIONS)
                # The computation test requires different amount of spaces
                number_of_spaces = 4
            else:
                neon.set_program("xenon_net_benchmark")
                # Repetitions occur only in the Computation test, so make sure it is empty
                neon.set_substitution("NEON_NUM_REPEATS", "")
                # The main network test requires no extra spaces
                number_of_spaces = 0
             

            # Substitute Values (these are the same for both the network and computation benchmark)
            neon.set_substitution("NEON_INITIALIZATION", instruction_config.get_init_string())
            # The main test requires different amount of spaces
            # Here we add the duplicates
            # Note, the network benchmark contains "NEON_MAIN_TEST" twice
            # The construction for duplication seems weired, but works :-)
            tmp_instruction_string = instruction_config.get_instructions_string(add_extra_space=4)
            neon.set_substitution("NEON_MAIN_TEST", tmp_instruction_string + ("\n" + " " * number_of_spaces + tmp_instruction_string) * (number_of_duplications - 1))
            
            # Get Hashes
            program_hash = neon.compile_and_return_hash(compile_debug=True) 
    
            # Get main corresponding ASM instructions
            asm_instructions = get_relevant_asm_instructions(program_hash, as_asm_object=True)
    
            essential_program_asms[instruction_config.vector_length * number_of_duplications] = asm_instructions

        ###################
        # START OF CHECKS #
        ###################

        if not essential_program_asms:
            raise Exception("NO INSTRUCIONS. Error")

        logger.debug(f"INSTRUCTIONS ARE:")
        for vec, values in essential_program_asms.items():
            logger.debug(f"   v={vec:3d}: {[(v.instruction_name, v.first_parameter, v.second_parameter) for v in values]}")

        # Check if instructions are all the same
        all_instructions = set()
        for values in essential_program_asms.values():
            for v in values:
                all_instructions.add(v.instruction_name)
        if len(all_instructions) != 1:
            logger.critical(f"INSTRUCTIONS not all the same: {all_instructions}")
            exit(1)

        # Check if it differes from the expected name
        tmp_name = all_instructions.pop()
        if tmp_name != instruction_name:
            logger.critical(f"INSTRUCTION Name not correct: {tmp_name} != {instruction_name}")
            exit(1)

        # Check if vector length is as expected
        if all([sum([asi.get_vector_length() for asi in values]) == vec for vec, values in essential_program_asms.items()]):
            # Just for fun and good measure
            if all([len(values) == vec for vec, values in essential_program_asms.items()]):
                # Single command, number of instructions == vector length
                logger.debug("This is a SINGLE command.")
                duplication_results.append("single")
            elif all([len(values) == 1 and values[0].get_vector_length() == vec for vec, values in essential_program_asms.items()]):
                # Only one occurance of the instruction
                logger.debug("This is a PARALLEL or Vectorized command.")
                duplication_results.append("parallel-or-vector")
            elif all([len(values) == number_of_duplications and all([v.get_vector_length() == vec/number_of_duplications for v in values]) for vec, values in essential_program_asms.items()]):
                # Vectorized instructions without network are not combined
                logger.debug("This is a Vectorized command, non parallel")
                duplication_results.append("vector-non-parallel")
            else:
                logger.critical("UNKNOWN type of instruction")
                exit(1)
            continue
        else:
            logger.critical(f"Vector length wrong for {instruction_name}")
            exit(1)

        # Check if it is a parallel command
        # Only one instruction, and variable number of arguments >= vector_length


        # This should not occur
        raise Exception("UNKNOWN category type")
        
    if all([ds == "single" for ds in duplication_results]):
        return "single"
    elif all([ds == "parallel-or-vector" for ds in duplication_results]):
        return "parallel"
    elif duplication_results[0] == "parallel-or-vector" and all([ds == "vector-non-parallel" for ds in duplication_results[1:]]):
        return "vector"
    
    raise Exception("COULD not verify instruction")



def write_instruction_test_set(test_type, test_params, instruction_name, neon):
    """
        Write an instruction test set and return the number of tests executed
    """
    instructions_dir = join_path_abs(PATH_TO_TEMP, f"instruction_tests_{test_params.suffix}")
    # Create directories if missing
    if not os.path.exists(instructions_dir):
        os.makedirs(instructions_dir)

    instruction_tests = []

    if test_type in ["local-single"]:
        tmp_vector_lengthes = [max(test_params.vector_lengthes)]
    else:
        tmp_vector_lengthes = test_params.vector_lengthes
    # We do it that way so that we can have additional vector lenghtes (e.g. for the "local-single" case), but no duplicates otherwise
    for tavl in test_params.additional_vector_lengthes:
        if tavl not in tmp_vector_lengthes:
            tmp_vector_lengthes.append(tavl)

    for vector_length in tmp_vector_lengthes:
        instruction_tests.append(generate_instruction(vector_length))

    instruction_test_set = InstructionTestSet(instruction_tests, test_type)

    # CHECK if it is correct
    if COMPILE_AND_CHECK:
        check = compile_and_check_instruction_test_set(instruction_test_set, instruction_name, test_params, neon)
        # Verify that the check fits the test type
        if (test_type == "local-single"  and check in ["single"]) or \
           (test_type == "local-vector"  and check in ["vector"]) or \
           (test_type == "network-only"  and check in ["parallel", "vector"]) or\
           (test_type == "network-batch" and check in ["single"  , "vector"]):
                logger.info(f"Verification SUCCESFULL: {instruction_name}")
        else:
            logger.critical(f"Verificaton Failed for {instruction_name}: check={check}, type={test_type}")
            exit(1)

    with open( join_path_abs(instructions_dir, f"{instruction_name}.json"), 'w') as f:
        logger.info(f"WRITING CONFIG: {instruction_name}")
        f.write(instruction_test_set.to_json())

    return len(instruction_tests)


#### Below a lot of test definitions

def dual_variable_operation(type1, type2, operator_symbol, vector_length):
    """
    Generate config file for operators with two inputs and one output, e.g., additon, multiplication, ...
    """
    init = [f"a = {type1}(3)",
            f"b = {type2}(3)"]
    main = [f"for i in range({vector_length}):",
            f"    c = a {operator_symbol} b"]
    
    return InstructionConfig(init, main, vector_length)


def dual_variable_operation_compression(type1, type2, operator_symbol, vector_length):
    """
    Generate config file for operators with two inputs and one output, e.g., additon, multiplication, ...
    """
    init = []
    if type1:
        init.extend([f"a = Array({vector_length}, {type1})",
                     f"for i in range({vector_length}):",
                     f"    a[i] = {type1}(3)"])
        first_arg = f"a[:]"
    else:
        init.append(f"a = 3")
        first_arg = f"a"
    if type2:
        init.extend([f"b = Array({vector_length}, {type2})",
                     f"for i in range({vector_length}):",
                     f"    b[i] = {type2}(3)"])
        second_arg = f"b[:]"
    else:
        init.append(f"b = 3")
        second_arg = f"b"
    main = [f"c = {first_arg} {operator_symbol} {second_arg}"]
    
    return InstructionConfig(init, main, vector_length)


def generate_empty():
    # OUtput (Broadcast)
    test_set_output = []
    init = []
    main = []

    return InstructionConfig(init, main, vector_length=0)

def generate_asm_open(vector_length):
    init = [f"a = sint(3)"]
    main = [f"for i in range({vector_length}):",
            f"    a.reveal()"]

    return InstructionConfig(init, main, vector_length)

def generate_gasm_open(vector_length):
    init = [f"a = sgf2n(3)"]
    main = [f"for i in range({vector_length}):",
            f"    a.reveal()"]

    return InstructionConfig(init, main, vector_length)

def generate_vasm_open(vector_length):
    init = [f"a = Array({vector_length}, sint)"]
    main = [f"a[:].reveal()"]

    return InstructionConfig(init, main, vector_length)

def generate_vgasm_open(vector_length):
    init = [f"a = Array({vector_length}, sgf2n)"]
    main = [f"a[:].reveal()"]

    return InstructionConfig(init, main, vector_length)

def generate_ldX(type1, vector_length):
    init = []
    main = [f"for _ in range({vector_length}):",
            f"    a = {type1}(3)"]
        
    return InstructionConfig(init, main, vector_length)

def generate_ldms(vector_length):
    init = ["a = sint(3)", \
            "b = Array(1, sint)"]
    main = [f"for _ in range({vector_length}):",
            f"    a = b[0]"]
        
    return InstructionConfig(init, main, vector_length)

def generate_gldms(vector_length):
    init = ["a = sgf2n(3)", \
            "b = Array(1, sgf2n)"]
    main = [f"for _ in range({vector_length}):",
            f"    a = b[0]"]
        
    return InstructionConfig(init, main, vector_length)

def generate_stms(vector_length):
    init = ["a = sint(3)", \
            "b = Array(1, sint)"]
    main = [f"for _ in range({vector_length}):",
            f"    b[0] = a"]
        
    return InstructionConfig(init, main, vector_length)

def generate_gstms(vector_length):
    init = ["a = sgf2n(3)", \
            "b = Array(1, sgf2n)"]
    main = [f"for _ in range({vector_length}):",
            f"    b[0] = a"]
        
    return InstructionConfig(init, main, vector_length)


def generate_random_function(function_name, type1, vector_length):
    init = []
    main = [f"for _ in range({vector_length}):",
                   f"    {type1}.{function_name}()"]
        
    return InstructionConfig(init, main, vector_length)

def generate_random_function_vec(function_name, type1, vector_length):
    init = []
    main = [f"{type1}.{function_name}(size={vector_length})"]
        
    return InstructionConfig(init, main, vector_length)

def generate_conv_and_mov(type1, type2, vector_length):
    """
    Generate convint, convintmodp, and movc, and movs
    """
    init = [f"a = {type2}(3)"]
    main = [f"for i in range({vector_length}):",
            f"    {type1}(a)"]
    
    return InstructionConfig(init, main, vector_length)

def generate_vconv(type1, type2, vector_length):
    """
    Generate vconvint and vconvintmodp
    """
    init = [f"a = Array({vector_length}, {type1})",
            f"b = Array({vector_length}, {type2})"]
    main = [f"a[:] = b[:]"]
    
    return InstructionConfig(init, main, vector_length)

def generate_dotprods(vector_length):
    init = [f"a = Array({vector_length}, sint)",
            f"b = Array({vector_length}, sint)"]
    main = [f"sint.dot_product(a[:],a[:])"]

    return InstructionConfig(init, main, vector_length)

########################
# Benchmark defintions #
########################

# Generate Test Lists
list_of_tests = [NETWORK_TEST_PARAMETERS, COMPUTATION_TEST_PARAMETERS]

# Empty test is handled separately
# Some special cases are handled separately
list_of_benchmarks = [("ldms",          generate_ldms),
                      ("gldms",         generate_gldms),
                      ("stms",          generate_stms),
                      ("gstms",         generate_gstms),
                      ("dotprods",      generate_dotprods),
                      ("asm_open",      generate_asm_open),
                      ("gasm_open",     generate_gasm_open),
                      ("vasm_open",     generate_vasm_open),
                      ("vgasm_open",    generate_vgasm_open)
                      ]

# General SUFFIX/Types meanings (<> as operator)
# s = sint <> sint
# c = cint <> cint
# m = sint <> cint
#   Different for subtraction
#   mr = cint <> sint
#   ml = sint <> cint
# ci = cint <> 3 (fixed value)
# si = sint <> 3
#   Different for subtraction
#   si = sint <> 3
#   sfi = 3 <> sint
# int = regint <> regint
#   Note other combination with regint are converted to cint first


# AddX and mulX
base_name_and_symbols = [("add", "+"),
                         ("mul", "*")]
suffix_and_types = [( "",   "c",   "cint",   "cint"),
                    ( "",   "s",   "sint",   "sint"),
                    ( "",   "m",   "sint",   "cint"),
                    ( "",  "ci",   "cint",       ""),
                    ( "",  "si",   "sint",       ""),
                    ( "", "int", "regint", "regint"),
                    ("g",   "c",  "cgf2n",  "cgf2n"),
                    ("g",   "s",  "sgf2n",  "sgf2n"),
                    ("g",   "m",  "sgf2n",  "cgf2n"),
                    ("g",  "ci",  "cgf2n",       ""),
                    ("g",  "si",  "sgf2n",       "")]
for base_name, symbol in base_name_and_symbols:
    for prefix, suffix, type1, type2 in suffix_and_types:
        # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ...
        # Single or Parallel
        list_of_benchmarks.append((f"{prefix}{base_name}{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation(t1, t2, s, v)))
        # Vectorized
        list_of_benchmarks.append((f"v{prefix}{base_name}{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation_compression(t1, t2, s, v)))

# subX is slightly different
sub_suffix_and_types = [( "",   "c",   "cint",   "cint"),
                        ( "",   "s",   "sint",   "sint"),
                        ( "",  "ml",   "sint",   "cint"),
                        ( "",  "mr",   "cint",   "sint"),
                        ( "",  "ci",   "cint",       ""),
                        ( "",  "si",   "sint",       ""),
                        ( "", "sfi",       "",   "sint"),
                        ( "", "int", "regint", "regint"),
                        ("g",   "c",  "cgf2n",  "cgf2n"),
                        ("g",   "s",  "sgf2n",  "sgf2n"),
                        ("g",  "ml",  "sgf2n",  "cgf2n"),
                        ("g",  "mr",  "cgf2n",  "sgf2n"),
                        ("g",  "ci",  "cgf2n",       ""),
                        ("g",  "si",  "sgf2n",       ""),
                        ("g", "sfi",       "",  "sgf2n")]
for prefix, suffix, type1, type2 in sub_suffix_and_types:
    # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ... 
    # Single (most certenly not Parallel)
    list_of_benchmarks.append((f"{prefix}sub{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation(t1, t2, "-", v)))
    # Vectorized
    list_of_benchmarks.append((f"v{prefix}sub{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation_compression(t1, t2, "-", v)))

# generates tests for a=sint()/cint()/regint()
ld_suffix_and_type = [( "",   "i",   "cint"),
                      ( "",  "si",   "sint"),
                      ( "", "int", "regint"),
                      ("g",   "i",  "cgf2n"),
                      ("g",  "si",  "sgf2n")]
for prefix, suffix, type1 in ld_suffix_and_type:
    list_of_benchmarks.append((f"{prefix}ld{suffix}", lambda x, t1=type1: generate_ldX(t1, x)))


# Generate Randoms etc.
random_name_function = [("randomfulls", "",  "sint",        "get_random"),
                        ("bit",         "",  "sint",    "get_random_bit"),
                        ("inverse",     "",  "sint", "get_random_inverse"),
                        ("square",      "",  "sint",  "get_random_square"),
                        ("triple",      "",  "sint",  "get_random_triple"),
                        ("bit",        "g", "sgf2n",     "get_random_bit"),
                        ("inverse",    "g", "sgf2n", "get_random_inverse"),
                        ("square",     "g", "sgf2n",  "get_random_square"),
                        ("triple",     "g", "sgf2n",  "get_random_triple")]
for name, prefix, type1, function_name in random_name_function:
    # Normal randoms
    list_of_benchmarks.append((f"{prefix}{name}", lambda x, t1=type1, fn=function_name: generate_random_function(fn, t1, x)))
    # Vectorized ones
    list_of_benchmarks.append((f"v{prefix}{name}", lambda x, t1=type1, fn=function_name: generate_random_function_vec(fn, t1, x)))


# regint with regint <, >, ==
base_name_and_symbols = [("ltc", "<"),
                         ("gtc", ">"),
                         ("eqc", "==")]
for base_name, symbol in base_name_and_symbols:
    # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ... 
    # Single or Parallel
    list_of_benchmarks.append((f"{base_name}", lambda v, s=symbol: dual_variable_operation("regint", "regint", s, v)))
    # Vectorized
    list_of_benchmarks.append((f"v{base_name}", lambda v, s=symbol: dual_variable_operation_compression("regint", "regint", s, v)))


# Division
suffix_and_types = [( "",   "c",   "cint",   "cint"),
                    ( "",  "ci",   "cint",       ""),
                    ( "", "int", "regint", "regint"),
                    ("g",   "c",  "cgf2n",  "cgf2n"),
                    ("g",  "ci",  "cgf2n",       "")]
for prefix, suffix, type1, type2 in suffix_and_types:
    # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ... 
    # Single or Parallel
    list_of_benchmarks.append((f"{prefix}div{suffix}", lambda v, t1=type1, t2=type2: dual_variable_operation(t1, t2, "/", v)))
    # Vectorized
    list_of_benchmarks.append((f"v{prefix}div{suffix}", lambda v, t1=type1, t2=type2: dual_variable_operation_compression(t1, t2, "/", v)))


# AND / OR / XOR
base_name_and_symbols = [("and", "&"),
                         ( "or", "|"),
                         ("xor", "^")]
suffix_and_types = [( "",  "c",  "cint",  "cint"),
                    ( "", "ci",  "cint",      ""),
                    ("g",  "c", "cgf2n", "cgf2n"),
                    ("g", "ci", "cgf2n",      ""),
                    ]
for base_name, symbol in base_name_and_symbols:
    for prefix, suffix, type1, type2 in suffix_and_types:
        # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ...
        # Single or Parallel
        list_of_benchmarks.append((f"{prefix}{base_name}{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation(t1, t2, s, v)))
        # Vectorized
        list_of_benchmarks.append((f"v{prefix}{base_name}{suffix}", lambda v, t1=type1, t2=type2, s=symbol: dual_variable_operation_compression(t1, t2, s, v)))


# sint(s1) # movs
# cint(c1) # movc
# cint(r1) # convint
# regint(c1) # convmodp
name_and_types = [( "",     "movs",   "sint",   "sint"),
                  ( "",     "movc",   "cint",   "cint"),
                  ( "",  "convint",   "cint", "regint"),
                  ( "", "convmodp", "regint",   "cint"),
                  ("g",     "movs",  "sgf2n",  "sgf2n"),
                  ("g",     "movc",  "cgf2n",  "cgf2n"),]
for prefix, name, type1, type2 in name_and_types:
    # Note, the t1=type1, ... assigments are important, otherwise it considers only the last values for type1, ... 
    # Single (most certenly not Parallel)
    list_of_benchmarks.append((f"{prefix}{name}", lambda v, t1=type1, t2=type2: generate_conv_and_mov(t1, t2, v)))
    if name.startswith("conv"):
        # Vectorized
        list_of_benchmarks.append((f"v{prefix}{name}", lambda v, t1=type1, t2=type2: generate_vconv(t1, t2, v)))


########################
# Benchmark generation #
########################


# Create Basic NEON
config = NeonConfig.from_config_files()
neon = NeonHandler(OperationMode.LOCAL, config)

# Further Config
neon.set_report_verbosity(ReportVerbosityLevel.LOW)
neon.set_print_timers_upon_deletion(False)
neon.set_read_secrets(False)
neon.set_bits_from_squares(True)
neon.set_number_of_parties(CHEK_TEST_NUM_PARTIES)
neon.set_all_inputs("3 " * CHEK_TEST_VECTOR_LENGTH)
neon.set_protocol(protocol.Shamir)
neon.set_network(network.Unlimited)

# Keep track of number of tests
num_net_tests = 0
num_bch_tests = 0
num_cpu_tests = 0


# First Write the empty test, then do the others
# Here make sure path exists
# Make sure base directory exists
path_to_empty_instruction_folder = join_path_abs(PATH_TO_TEMP, f"instruction_tests_{COMPUTATION_TEST_PARAMETERS.suffix}")
if not os.path.exists(path_to_empty_instruction_folder):
    os.makedirs(path_to_empty_instruction_folder)

with open(join_path_abs(path_to_empty_instruction_folder , f"empty.json"), 'w') as f:
    logger.info(f"WRITING CONFIG: empty")
    f.write(InstructionTestSet([generate_empty()], "empty").to_json())
num_cpu_tests += 1

# DO the others
# sort list of benchmarks
list_of_benchmarks.sort()

used_instructions = []
# Note, cannot be made parallel, as NEON does not support parallel executions
for instruction_name, generate_instruction in tqdm(list_of_benchmarks, desc="Generate Code", leave=None):
    # Only if INSTRUCTIONS_TO_DO is not empty
    if INSTRUCTIONS_TO_DO and instruction_name not in INSTRUCTIONS_TO_DO:
        continue
    
    # Check for duplicates
    if instruction_name in used_instructions:
        raise Exception(f"INSTRUCTION NAME '{instruction_name}' already used")
    used_instructions.append(instruction_name)
    
    tmp_test_conf = generate_instruction(CHEK_TEST_VECTOR_LENGTH)

    neon.set_program("xenon_cpu_benchmark")
    neon.set_substitution("NEON_NUM_REPEATS", CHEK_TEST_REPEATS)
    neon.set_substitution("NEON_INITIALIZATION", tmp_test_conf.get_init_string())
    neon.set_substitution("NEON_MAIN_TEST", tmp_test_conf.get_instructions_string(add_extra_space=4))

    neon.set_batch_size(CHEK_TEST_BATCH_SIZE_ONE)

    # Perform first test
    # Determines if the instructions has network traffic
    logger.info(f"Testing {instruction_name} for Network")
    report = neon.smpc(compile_debug=True)
    # Only get traffic from the main test (timer 3333) to avoid traffic caused by initialization
    tmp_global_data_one = report.get_timer_global_data_sent(3333)


    if tmp_global_data_one > 0:
        # NETWORK TRAFFIC
        neon.set_batch_size(CHEK_TEST_BATCH_SIZE_TWO)

        # Second Network test
        # Test if the instruction is influenced by the batch size
        logger.info(f"Testing {instruction_name} for Batch Size")
        report = neon.smpc(compile_debug=True)
        # Only get traffic from the main test (timer 3333) to avoid traffic caused by initialization
        tmp_global_data_two = report.get_timer_global_data_sent(3333)

        if tmp_global_data_one == tmp_global_data_two:
            logger.debug("Is NETWORK-ONLY")
            num_net_tests += write_instruction_test_set("network-only", NETWORK_TEST_PARAMETERS, instruction_name, neon)
            num_cpu_tests += write_instruction_test_set("network-only", COMPUTATION_TEST_PARAMETERS, instruction_name, neon)
        else:
            logger.debug("Is NETWORK-BATCH")
            num_bch_tests += write_instruction_test_set("network-batch", BATCHSIZE_TEST_PARAMETERS, instruction_name, neon)
            num_cpu_tests += write_instruction_test_set("network-batch", COMPUTATION_TEST_PARAMETERS, instruction_name, neon)
    else:
        # NO NETWORK
        # Check disasembled code for single or more instuctions (single or vector)
        asm_instructions = get_relevant_asm_instructions(report.program_hash, as_asm_object=True)
        tmp_set = set([asm.instruction_name for asm in asm_instructions])

        if len(tmp_set) != 1:
            logger.critical(f"Multiple instructions found for: {instruction_name}")
            for k, v in report.substitutions.items():
                logger.critical(f"{k}\n{v}")
            exit(1)
        if instruction_name != tmp_set.pop():
            raise Exception("Instruction name missmatch")
        if len(asm_instructions) <= 0:
            raise Exception("NO Instructions found.")
        elif len(asm_instructions) == 1:
            # If only one instruction, then it must be vector
            logger.debug("Is LOCAL-VECTOR")
            num_cpu_tests += write_instruction_test_set("local-vector", COMPUTATION_TEST_PARAMETERS, instruction_name, neon)
        else:
            # Otherwise if multiple instructions, then it must be single
            logger.debug("Is LOCAL-SINGLE")
            num_cpu_tests += write_instruction_test_set("local-single", COMPUTATION_TEST_PARAMETERS, instruction_name, neon)
    


logger.info("CODE GENERATION COMPLETE")
if COMPILE_AND_CHECK:
    logger.info("VERIFICATION OF ALL CODE: SUCCESFULL")


#################################
# COMPUTE total number of tests #
#################################


# Number of parties
net_p = len(NETWORK_TEST_PARAMETERS.parties)
bch_p = len(BATCHSIZE_TEST_PARAMETERS.parties)
net_a_p = len(NETWORK_TEST_PARAMETERS.additional_parties)
bch_a_p = len(BATCHSIZE_TEST_PARAMETERS.additional_parties)
cpu_p = len(COMPUTATION_TEST_PARAMETERS.parties)
cpu_a_p = len(COMPUTATION_TEST_PARAMETERS.additional_parties)
init_p = len(INIT_TEST_PARAMETERS.parties)
init_a_p = len(INIT_TEST_PARAMETERS.additional_parties)

num_net_tests *= (net_p + net_a_p)
num_bch_tests *= (bch_p + bch_a_p)
num_cpu_tests *= (cpu_p + cpu_a_p)
num_init_tests = (init_p + init_a_p) * NUMBER_OF_INIT_TEST_REPETITIONS

# Hack test to initialize the network bridge
num_hacks = net_p + net_a_p + bch_p + bch_a_p + cpu_p + cpu_a_p + init_p + init_a_p

# Print statistics
logger.info(f"Number of uniq instructions: {len(used_instructions):4d}")
logger.info(f"Number of Hack Tests       : {num_hacks:4d}")
logger.info(f"Number of Network Tests    : {num_net_tests:4d}")
logger.info(f"Number of Batch Tests      : {num_bch_tests:4d}")
logger.info(f"Number of CPU Tests        : {num_cpu_tests:4d}")
logger.info(f"Number of INIT Tests       : {num_init_tests:4d}")
logger.info(f"Total Number of Tests      : {num_hacks+num_net_tests+num_bch_tests+num_cpu_tests+num_init_tests:4d}")
