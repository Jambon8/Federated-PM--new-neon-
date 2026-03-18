 #!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

import json
import math
import re
import numpy

from ProgramFiles.helper import get_path_to_temp, join_path_abs, get_mpspdz_version_from_path, get_path_to_mpspdz

# Needs to be here to avoid circular imports
class TestParameters:
    """
    Defines the test parameters
    """

    def __init__(self, suffix, parties, additional_parties, vector_lengthes, additional_vector_lengthes):
        self.suffix = suffix

        self.parties = parties
        self.additional_parties = additional_parties

        self.vector_lengthes = vector_lengthes
        self.additional_vector_lengthes = additional_vector_lengthes

# Path to folder where temporary files are stored
# Place it in the temp folder of NEON
# IMPORTANT -> INCLUDE the MP-SPDZ Version
PATH_TO_TEMP = join_path_abs(get_path_to_temp(), f"xenon_benchmarks_{get_mpspdz_version_from_path(get_path_to_mpspdz())}")

# # Path to folder where the parameters for estimations are stored
# Place it in the estimatior folder
# IMPORTANT -> INCLUDE the MP-SPDZ Version
PATH_TO_ESTIMATIONS = join_path_abs(get_path_to_temp(), "..", "XENON", f"estimation_functions_{get_mpspdz_version_from_path(get_path_to_mpspdz())}")


#################################################################################
# WARNING: Large Vector lenth for network/batchsize test and Congestion Control #
#################################################################################
# If the vector length is too large, or an instruction sends large messages
# then "NEON_MAIN_TEST" in the "PRE TEST" section in `Programs/xenon_net_benchmark.mpc` needs to be executed/inserted multiple times!!!
# This can be achieved with a @for_range(...) loop (non-parallel), or copy and past it multiple times, but with break_point() in between.
# If not, then single steps might be splitt (into multiple smaller ones) due to congestion control influences.
# This means, we would not obtain correct/precise/good steps/messages for the network model.
# We already have one execution "NEON_MAIN_TEST" in the "PRE TEST" section in `xenon_net_benchmark.mpc` to prevent this effect for at least small vector lengths.
# Thus, for small vector lengthes it should not be required to change `xenon_net_benchmark.mpc`


########################
# BENCHMARK Parameters #
########################

## SEE WARNING ABOVE !!! ##

# Version with all Tests, but no verifications enabled
# Suffix is the suffix used for folder names
# parties and vector length are the values used for the regression later -> the actual benchmarks required
# additional_* are additional benchmarks, not used for regression, but for verification, so not required
# We provided some example values for additional_* paramerters that should be suitable.
NETWORK_TEST_PARAMETERS = TestParameters(suffix="net",
                                         parties=[3, 4, 5, 6, 7, 8],
                                         additional_parties=[], #[11, 12, 19, 20],
                                         vector_lengthes=[2, 3], # Exclude 1 -> problem for vectorized instructions. Large vector lengths might cause problems with congestion control (see above).
                                         additional_vector_lengthes=[])# [10, 20, 30, 50, 100]) # Exclude 1 -> problem for vectorized instructions

# vector_size: Exclude 1 -> problem for vectorized instructions, 
# also here vector_size = batch_size, and smallest batchsize for regression at least 10 (at least 1 & 2 make problems)
BATCHSIZE_TEST_PARAMETERS = TestParameters(suffix="bch",
                                           parties=[3, 4, 5, 6, 7, 8],
                                           additional_parties=[], #[11, 12, 19, 20],
                                           vector_lengthes=[10, 11, 12, 13], # Exclude 1 -> problem for vectorized instructions. Large vector lengths might cause problems with congestion control (see above).
                                           additional_vector_lengthes=[]) #[14, 20, 21, 22, 23, 24, 25, 30, 50, 53, 100, 101, 102, 103]) # Exclude 1 -> problem for vectorized instructions

COMPUTATION_TEST_PARAMETERS = TestParameters(suffix="cpu",
                                             parties=[3, 4, 5, 6, 7, 8, 9, 10],
                                             additional_parties=[], #[11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                                             vector_lengthes=[  10,  20,  30,  40,  50,  60,  70,  80,  90,
                                                               100, 200, 300, 400, 500, 600, 700, 800, 900,
                                                              1000], # Exclude 1 -> problem for vectorized instructions
                                             additional_vector_lengthes=[]) #[   10,   20,   30,   40,   50,   60,   70,   80,   90,
                                                                        #   100,  200,  300,  400,  500,  600,  700,  800,  900, 
                                                                        #  1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 
                                                                        # 10000]) # Exclude 1 -> problem for vectorized instructions

# Test parameters for the connection and closing phase (init)
INIT_TEST_PARAMETERS = TestParameters(suffix=None, # Needs to be None
                                      parties=[3, 4, 5, 6, 7, 8, 9, 10],
                                      additional_parties=[], #[11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                                      vector_lengthes=None, additional_vector_lengthes=None)# No Vector length used


# Parameters for the sanity checks and classification
# This influences only 1_generate_code_and_check
# Number of parties for classification
CHEK_TEST_NUM_PARTIES = 3
# Vector lenght to use value >1
CHEK_TEST_VECTOR_LENGTH = 2
# Number of iterations for the for-loop in xenon_cpi_benchmark.mpc (for the sanity check, not the actual benchmarks)
CHEK_TEST_REPEATS = 1
# Batch size values used
# Important, use values greater than 0, and make sure the difference is large enough.
CHEK_TEST_BATCH_SIZE_ONE = 10
CHEK_TEST_BATCH_SIZE_TWO = 20

# For Debugging only
# restrict test generation to instructions in list only
# If the list is empty, all instructions are done
INSTRUCTIONS_TO_DO = []

# Compile and perform checks during code generation
# Usefull for debugging and when adding new instructions
COMPILE_AND_CHECK = False


# Number of repetitions for CPU Tests/benchmarks
NUMBER_OF_CPU_TEST_REPETITIONS = 10

# Number of repetitions for the connection and closing phase (init)
NUMBER_OF_INIT_TEST_REPETITIONS = 5

# Network delay (in ms) used in network benchmarks/test
# Do not set too low, as this can lead to bad network models
# Note, if increasing the value does not help in getting consistent network models, see the warning about congestion control above.
NETWORK_DELAY = 20


# If some benchmarks are already present, do only missing ones
# This implies combinations of number_of_parties and vector lengthes, as well as repetitions
DO_ONLY_MISSING_BENCHMARKS = True


# Determines if you want to store the list of messages sent (with sender and receiver), usefull for debugging, and not used/required for the regression
# This is only helpful if one wants to analyse the network traffic in detail
STORE_LIST_OF_MESSAGES_IN_NET_MODEL = False
# If you want to addtionally store the individual packets, you have to enable this, too.
# Thus this is only helpful if one wants to analyse the network traffic in even more detail
STORE_INDIVIDUAL_PACKETS_IN_NET_MODEL = False



#####################################
# TCP Congestion Control Parameters #
#####################################
# Note, we model only "CUBIC" congestion control (Linux's current default) and assume sshthresh=0 (default)
# This information can be obtained from `/proc/sys/net/ipv4/tcp_congestion_control`, and `/sys/module/tcp_cubic/parameters/initial_ssthresh`
# Note, the later (which is "0") determines that no slow start is used (RFC 5681).

# Initial CWND Size in BYTES (!!!!) (not number of MSS!!!) taken from RFC9438 (https://www.rfc-editor.org/rfc/rfc6928.txt)
TCP_INIT_CWND_SIZE_IN_BYTES = 14600
# TCP Cubic maximal window growth rate (used to simplify the estimation of the effects caused by CUBIC)
# Note: Value "1.5" (max growth) is taken from RFC9438 (https://www.rfc-editor.org/rfc/rfc9438.txt)
TCP_CUBIC_MAX_GROWTH_RATE = 1.5


#########################
# REGRESSION Parameters #
#########################

# Devide whether ot not to use mainly sympy or mainly numpy (w.r.t to storing and final estimation)
# Note, regression itself still uses sympy to guarantee a "0" is actually zero and not somthing like 2.13e-23
# Numpy is significantly faster in the estimation, and the deviation is negligible
USE_SYMPY=False

# Some instructions will be present in the ASM files even for single instructions
# They need to be ignored when checking the asm files
# Vectorized instructions typically have load and store instructions, therefore we need to ignore them as we cannot prevent them
instructions_vector_loads_and_stores = ["vldms", "vldmc", "vldmint", "vstms", "vstmc", "vstmint", "vgldms", "vgldmc", "vgstms", "vgstmc"]
INSTRUCTIONS_TO_IGNORE = ["start", "stop"] + instructions_vector_loads_and_stores

# Following the actual parameters used for regression and the corresponging value
# e.g. if you want a simple linear regression with a possible up to quadradtic dependency in the number of parties
# then use regression = lambda number_of_parties, vector_length: [1, number_of_parties, number_of_parties**2 ]
# and regression_VALUE = lambda x: x.cpu_time
# Note, the parameters below are highly connected to 4_generate_asm_model


### COMPUTATION
REGRESSION_COMPUTATION_EMPTY = lambda number_of_parties, vector_length: [1]
REGRESSION_COMPUTATION_EMPTY_VALUE = lambda x: x.cpu_time

REGRESSION_COMPUTATION_LOCAL_SINGLE = lambda number_of_parties, vector_length: [1]
REGRESSION_COMPUTATION_LOCAL_SINGLE_VALUE = lambda x: x.cpu_time/x.vector_length

REGRESSION_COMPUTATION_LOCAL_VECTOR = lambda number_of_parties, vector_length: [1,
                                                                                vector_length]
REGRESSION_COMPUTATION_LOCAL_VECTOR_VALUE = lambda x: x.cpu_time

REGRESSION_COMPUTATION_PARALLEL = lambda number_of_parties, vector_length: [1,
                                                                            number_of_parties,
                                                                            number_of_parties**2,
                                                                            vector_length,
                                                                            vector_length * number_of_parties,
                                                                            vector_length * number_of_parties**2]
REGRESSION_COMPUTATION_PARALLEL_VALUE = lambda x: x.cpu_time


REGRESSION_COMPUTATION_BY_NAME = {"empty": REGRESSION_COMPUTATION_EMPTY,
                                  "local-single": REGRESSION_COMPUTATION_LOCAL_SINGLE,
                                  "local-vector": REGRESSION_COMPUTATION_LOCAL_VECTOR,
                                  "parallel": REGRESSION_COMPUTATION_PARALLEL}
REGRESSION_COMPUTATION_VALUE_BY_NAME = {"empty": REGRESSION_COMPUTATION_EMPTY_VALUE,
                                        "local-single": REGRESSION_COMPUTATION_LOCAL_SINGLE_VALUE,
                                        "local-vector": REGRESSION_COMPUTATION_LOCAL_VECTOR_VALUE,
                                        "parallel": REGRESSION_COMPUTATION_PARALLEL_VALUE}


### NETWORK + BATCHSIZE

# Get the correspongin values form the Network Meta
# Note, the regressions differ for Network and Batch and are defined below
# Max Total Sending Value
REGRESSION_NET_BCH_SEND_VALUE = lambda x, step: x.list_of_steps[step].max_sending_data
# Max Total Receiving Value
REGRESSION_NET_BCH_REC_VALUE = lambda x, step: x.list_of_steps[step].max_receiving_data
# Real Global Traffic Value
REGRESSION_NET_BCH_GLOBAL_VALUE = lambda x, step: x.list_of_steps[step].global_data
# MP-SPDZ Global Traffic Value
REGRESSION_NET_BCH_MPSPDZ_GLOBAL_VALUE = lambda x: x.mpspdz_global_data

# The number of Messages
REGRESSION_NET_BCH_NUM_MESSAGES = lambda number_of_parties, vector_length: [1,
                                                                            number_of_parties,
                                                                            number_of_parties**2,
                                                                            (number_of_parties % 2),
                                                                            (number_of_parties % 2) * number_of_parties,
                                                                            (number_of_parties % 2) * number_of_parties**2,
                                                                            ]
REGRESSION_NET_BCH_NUM_MESSAGES_VALUE = lambda x, step: x.list_of_steps[step].num_messages


# Used for maximal single message size
# Note, the regression itself (not value) is the "SEND_REC_MAX" from either Network or Batchsize, respectively
REGRESSION_NET_BCH_MAX_SINGLE_MESSAGE_VALUE = lambda x, step: x.list_of_steps[step].max_single_message

### NETWORK (only)
# Remark:
# (number_of_parties % 2) is used to distinguish between odd and even number of parties, as their network traffic pattern differs slightly
REGRESSION_NETWORK_SEND_REC_MAX = lambda number_of_parties, vector_length: [1,
                                                                            number_of_parties,
                                                                            vector_length,
                                                                            vector_length * number_of_parties,
                                                                            (number_of_parties % 2),
                                                                            (number_of_parties % 2) * number_of_parties, # Not required in 0.3.5 and 0.3.6
                                                                            (number_of_parties % 2) * vector_length,
                                                                            (number_of_parties % 2) * vector_length * number_of_parties # Not required in 0.3.5 and 0.3.6
                                                                            ]


REGRESSION_NETWORK_GLOBAL = lambda number_of_parties, vector_length: [1,
                                                                      number_of_parties,
                                                                      number_of_parties**2,
                                                                      vector_length,
                                                                      vector_length * number_of_parties,
                                                                      vector_length * number_of_parties**2,
                                                                      (number_of_parties % 2),
                                                                      (number_of_parties % 2) * number_of_parties,
                                                                      (number_of_parties % 2) * number_of_parties**2, # Not required in 0.3.5 and 0.3.6
                                                                      (number_of_parties % 2) * vector_length,
                                                                      (number_of_parties % 2) * vector_length * number_of_parties, # Not required in 0.3.5 and 0.3.6
                                                                      (number_of_parties % 2) * vector_length * number_of_parties**2 # Not required in 0.3.5 and 0.3.6
                                                                      ]


### BATCHSIZE
# Remarks:
# Note for the Batchsize tests batch_size = vector_length
# ((batch_size - 1) // (number_of_parties//2 + 1)) is required to compensate for how MP-SPDZ generates certain values
REGRESSION_BATCHSIZE_SEND_REC_MAX = lambda number_of_parties, batch_size: [1,
                                                                           number_of_parties,
                                                                           batch_size,
                                                                           batch_size * number_of_parties,
                                                                           ((batch_size - 1) // (number_of_parties//2 + 1)), # Not required in 0.3.6
                                                                           ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties,
                                                                           (number_of_parties % 2),
                                                                           (number_of_parties % 2) * number_of_parties, # Not required in 0.3.5 and 0.3.6
                                                                           (number_of_parties % 2) * batch_size,
                                                                           (number_of_parties % 2) * batch_size * number_of_parties, # Not required in 0.3.5 and 0.3.6
                                                                           (number_of_parties % 2) * ((batch_size - 1) // (number_of_parties//2 + 1)), # Not required for RECEIVING in 0.3.5
                                                                           (number_of_parties % 2) * ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties # Not required in 0.3.5 and 0.3.6
                                                                           ]


REGRESSION_BATCHSIZE_GLOBAL = lambda number_of_parties, batch_size: [1,
                                                                    number_of_parties,
                                                                    number_of_parties**2,
                                                                    batch_size,
                                                                    batch_size * number_of_parties,
                                                                    batch_size * number_of_parties**2,
                                                                    ((batch_size - 1) // (number_of_parties//2 + 1)), # Not required in 0.3.6
                                                                    ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties, # Not required in 0.3.6
                                                                    ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties**2,
                                                                    (number_of_parties % 2),
                                                                    (number_of_parties % 2) * number_of_parties,
                                                                    (number_of_parties % 2) * number_of_parties**2, # Not required in 0.3.5 and 0.3.6
                                                                    (number_of_parties % 2) * batch_size,
                                                                    (number_of_parties % 2) * batch_size * number_of_parties, # Not required in 0.3.5 and 0.3.6
                                                                    (number_of_parties % 2) * batch_size * number_of_parties**2, # Not required in 0.3.5 and 0.3.6
                                                                    (number_of_parties % 2) * ((batch_size - 1) // (number_of_parties//2 + 1)), # Not required in 0.3.6
                                                                    (number_of_parties % 2) * ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties,
                                                                    (number_of_parties % 2) * ((batch_size - 1) // (number_of_parties//2 + 1)) * number_of_parties**2 # Not required in 0.3.5 and 0.3.6
                                                                    ]


# Connection and Closing Phase (init)
# Note, no vector length/batch size
REGRESSION_INIT_GLOBAL_DATA = lambda number_of_parties: [1,
                                                         number_of_parties,
                                                         number_of_parties**2,
                                                         ]
REGRESSION_INIT_GLOBAL_DATA_VALUE = lambda x: x.global_data