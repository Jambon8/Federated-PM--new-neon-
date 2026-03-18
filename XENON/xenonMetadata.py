 #!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

import json
import math
import re
import sympy
import numpy

from ProgramFiles.helper import join_path_abs, get_path_to_mpspdz
from xenonConfig import USE_SYMPY, INSTRUCTIONS_TO_IGNORE, STORE_INDIVIDUAL_PACKETS_IN_NET_MODEL, STORE_LIST_OF_MESSAGES_IN_NET_MODEL, \
                            REGRESSION_COMPUTATION_BY_NAME, \
                            REGRESSION_NETWORK_SEND_REC_MAX, REGRESSION_NETWORK_GLOBAL, \
                            REGRESSION_BATCHSIZE_SEND_REC_MAX, REGRESSION_BATCHSIZE_GLOBAL, \
                            REGRESSION_NET_BCH_NUM_MESSAGES, \
                            REGRESSION_NET_BCH_MAX_SINGLE_MESSAGE_VALUE, \
                            REGRESSION_INIT_GLOBAL_DATA \



def get_relevant_asm_instructions(program_hash, as_asm_object):
    """
    Extract the relevant asm instructions between the timer

    as_asm_object: if True -> return ASMINstructions, else just string
    """
    decompiled_file = join_path_abs(get_path_to_mpspdz(), "Programs", f"asm-{program_hash}-0")
    asm_instructions = []
    asm_started = False
    with open(decompiled_file) as f:
        while l := f.readline():
            if l.startswith("#"):
                continue
            elif l.startswith("start 3333"):
                asm_started = True
            elif asm_started and l.startswith("stop 3333"):
                break
            elif asm_started:
                # Get only the instrucion name
                if as_asm_object:
                    asi = ASMInstruction(l)
                    if asi.instruction_name not in INSTRUCTIONS_TO_IGNORE:
                        asm_instructions.append(asi)
                else:
                    # Remove comments
                    asm_instructions.append(l.rsplit("#")[0].strip())
    return asm_instructions


class InstructionConfig:
    """
    Defines an Instruction
    Consists of name, the actual code to execute, possible initialization code
    And additional Metadata required (e.g. vector length)
    """
    def __init__(self, initialization, instructions_to_run, vector_length):
        self.initialization = initialization
        self.instructions_to_run = instructions_to_run
        self.vector_length = vector_length

    def get_init_string(self):
        return "\n".join(self.initialization)

    def get_instructions_string(self, add_extra_space=0):
        return f'\n{" " * add_extra_space}'.join(self.instructions_to_run)

class InstructionTestSet:
    """
    Defines a test set of the same instruction, with different parameters
    """

    def __init__(self, test_set, test_type):
        self.test_set = test_set
        self.test_type = test_type

    def to_json(self):
        new_dict = { "test_type": self.test_type, "test_set": []}
        for i in self.test_set:
            new_dict["test_set"].append(i.__dict__)
        return json.dumps(new_dict, indent=4)

    @classmethod
    def from_json_file(cls, file_name):
        with open(file_name, 'r') as f:
            json_data = json.load(f)

        test_set = []
        for i in json_data["test_set"]:
            test_set.append(InstructionConfig(**i))
        return cls(test_set, json_data["test_type"])

class NetworkMetaMessage:
    """
    A message from one party to another
    This might contain multiple different data packets
    """

    def __init__(self, src, dst, datas, total_data_len):
        self.src = src
        self.dst = dst
        # Store detailed information (not required for regression/estimation)
        if STORE_INDIVIDUAL_PACKETS_IN_NET_MODEL:
            self.datas = datas
        else:
            self.datas = None
        self.total_data_len = total_data_len

class NetworkMetaStep:
    """
    Single step, containes possibly multiple messages between parties (list)
    """

    def __init__(self, list_of_messages, max_sending_data, max_receiving_data, global_data, num_messages, max_single_message):
        self.list_of_messages = list_of_messages
        self.max_sending_data = max_sending_data
        self.max_receiving_data = max_receiving_data
        self.global_data = global_data
        self.num_messages = num_messages
        self.max_single_message = max_single_message


class NetworkMetaTraffic:
    """
    This class represents the network traffic relevant for estimation
    """

    def __init__(self, list_of_steps, n_parties, mpspdz_global_data):
        self.list_of_steps = list_of_steps
        self.n_parties = n_parties
        self.mpspdz_global_data = mpspdz_global_data

    def to_json(self):
        new_dict = { }
        new_dict["n_parties"] = self.n_parties
        new_dict["mpspdz_global_data"] = self.mpspdz_global_data
        new_dict["list_of_steps"] = [ ]
        for nms in self.list_of_steps:
            tmp_dict = {}
            tmp_dict["max_sending_data"] = nms.max_sending_data
            tmp_dict["max_receiving_data"] = nms.max_receiving_data
            tmp_dict["global_data"] = nms.global_data
            tmp_dict["num_messages"] = nms.num_messages
            tmp_dict["max_single_message"] = nms.max_single_message
            if STORE_LIST_OF_MESSAGES_IN_NET_MODEL:
                tmp_dict["list_of_messages"] = [ m.__dict__ for m in nms.list_of_messages]
            else:
                tmp_dict["list_of_messages"] = None
            new_dict["list_of_steps"].append(tmp_dict)
        return json.dumps(new_dict, indent=4)

    @classmethod
    def from_json_file(cls, file_name):
        with open(file_name, 'r') as f:
            json_data = json.load(f)

        list_of_steps = []
        for step in json_data["list_of_steps"]:
            if step["list_of_messages"] != None:
                list_of_messages = []
                for msg in step["list_of_messages"]:
                    list_of_messages.append(NetworkMetaMessage(**msg))
            else:
                list_of_messages = None
            list_of_steps.append(NetworkMetaStep(list_of_messages, step["max_sending_data"], step["max_receiving_data"], step["global_data"], step["num_messages"], step["max_single_message"]))
        return cls(list_of_steps, json_data["n_parties"], json_data["mpspdz_global_data"])

class ASMInstruction:
    """
    This class represents a MP-SPDZ asm instruction
    """

    def __init__(self, asm_instruction_line):
        # Split on space, but we are only interested in the first two elements
        # 1) is the instrucion name
        # 2) is either variable number of argumebts or a register
        self.splitted = asm_instruction_line.rsplit("#")[0].strip().split(' ')


        # Name of instruction, e.g. muls, adds, ...
        self.instruction_name = self.splitted[0]


        # Determine if first parameter is numeric
        # then this is mostly the vector length
        if len(self.splitted) > 1:
            tmp_frist_param = self.splitted[1].strip(',')
            if tmp_frist_param.isnumeric():
                self.first_parameter = int(tmp_frist_param)
            else:
                self.first_parameter = None
        else:
            self.first_parameter = None

        # Determine if second parameter is numeric
        # This can occur on two occasions
        # 1) for some vectorized instructions that are also parallel
        # 2) required for jumps (it is an ldint instruction, but second parameter has value)
        # then this is mostly the vector length
        if len(self.splitted) > 2:
            tmp_second_param = self.splitted[2].strip(',')
            # Note, isnumeric returns False for negative numbers
            if tmp_second_param.isnumeric():
                self.second_parameter = int(tmp_second_param)
            elif self.instruction_name in ["jmpnz"]:
                # Here we just know it is numeric
                # Note, the value should be negative
                self.second_parameter = int(tmp_second_param)
            else:
                self.second_parameter = None
        else:
            self.second_parameter = None
        
        # Only for estimation required
        self.computation_time = None
        self.network_time = None
        self.global_network_traffic = None
        self.global_mpspdz_traffic = None
        self.number_of_steps = None
        self.max_single_message = None

        # Used for for loops, list of multipliers
        self.multiplier = []

    def get_vector_length(self):
        # Return the vector length
        if not self.first_parameter:
            # This is true for all non parallel or single instructions
            return 1
        elif self.first_parameter and self.second_parameter == None:
            # These are parallel instructions or simple vectorized ones
            if self.instruction_name in ["muls", "gmuls"]:
                # simple parallel
                return self.first_parameter / 3
            elif self.instruction_name in ["asm_open", "gasm_open"]:
                # simple parallel (but different computation)
                return (self.first_parameter - 1)/2
            elif self.instruction_name.startswith("v"):
                # simple vectorized
                # Needs to be last
                return self.first_parameter
        elif self.first_parameter and self.second_parameter != None:
            # Vectorized Parallel instrucations
            if self.instruction_name in ["vmuls", "vgmuls"]:
                return self.first_parameter * self.second_parameter/3
            elif self.instruction_name in ["vasm_open", "vgasm_open"]:
                return self.first_parameter * (self.second_parameter - 1)/2
            elif self.instruction_name in ["dotprods"]:
                tmp_vec = (self.second_parameter - 2) / 2
                tmp_i = 2 + self.second_parameter
                while tmp_i < len(self.splitted):
                    tmp_value = int(self.splitted[tmp_i].strip(','))
                    tmp_vec += (tmp_value - 2) / 2
                    tmp_i += tmp_value
                return tmp_vec
        raise Exception(f"VECOTR length not known for {self}")

    def __str__(self):
        return f"ASM: {self.instruction_name} [{self.first_parameter}, {self.second_parameter}] (CPU={self.computation_time}s, NET={self.network_time}s/{self.global_network_traffic} bytes/{self.global_mpspdz_traffic} bytes, X={self.multiplier})"

class EstimationComputation:
    """
    This class defines the comuation estimation information
    """

    def __init__(self, beta_computation, regression_type):
        # Values computation time
        self.beta_computation = beta_computation

        # lookuptable to speed up estimations
        self._lookuptable = {}

        # Store the type of regression
        self.regression_type = regression_type
        
    
    def get_computation_time(self, number_of_parties, vector_length):
        if (number_of_parties, vector_length) not in self._lookuptable:
            regression = REGRESSION_COMPUTATION_BY_NAME[self.regression_type]
            compute_time = sum([b * r for b,r in zip(self.beta_computation, regression(number_of_parties, vector_length), strict=True)])
            self._lookuptable[number_of_parties, vector_length] = compute_time
        return self._lookuptable[number_of_parties, vector_length]

    def __str__(self):
        return f"Estimation CPU: {self.beta_computation}"

    def to_json(self):
        new_dict = { "regression_type": self.regression_type}
        if USE_SYMPY:
            new_dict["beta_computation"] = str(self.beta_computation)
        else:
            new_dict["beta_computation"] = self.beta_computation

        return json.dumps(new_dict, indent=4)

    @classmethod
    def from_json_file(cls, file_name):
        with open(file_name, 'r') as f:
            json_data = json.load(f)

        if USE_SYMPY:
            t_beta = sympy.sympify(json_data["beta_computation"])
        else:
            t_beta = numpy.asarray(json_data["beta_computation"]).astype(numpy.float64)
        return cls(t_beta, json_data["regression_type"])


class EstimationNetworkStep:
    """
    This represents a single step/network traffic based on parameters for batchsizes

    Note the values are based on number_of_parties - 1
    """

    def __init__(self, beta_sending, beta_receiving, beta_global, beta_num_messages, beta_max_single, regression_type):
        """
        Note the values are based on number_of_parties - 1
        """
        # Values for sending
        self.beta_sending = beta_sending
        # Values for receving
        self.beta_receiving = beta_receiving
        # Values for global data
        self.beta_global = beta_global
        # Vlaues for number of messages
        self.beta_num_messages = beta_num_messages
        # Value for Max Single Message Size
        self.beta_max_single = beta_max_single

        # lookuptable to speed up estimations
        self._lookuptable = {}
        # regression type
        self._regression_type = regression_type

    def get_network_data(self, number_of_parties, dimension):
        """
        Here we compute the sending and receiving traffic
        dimension is either vector_length or batch_size

        WARNING: use exclude_acks=True ONLY for the initial benchmark plots!!!
        """
        if (number_of_parties, dimension) not in self._lookuptable:
            if self._regression_type == "network-batch":
                regression_send_rec_max = REGRESSION_BATCHSIZE_SEND_REC_MAX
                regresseion_global = REGRESSION_BATCHSIZE_GLOBAL
            elif self._regression_type == "network-only":
                regression_send_rec_max = REGRESSION_NETWORK_SEND_REC_MAX
                regresseion_global = REGRESSION_NETWORK_GLOBAL
            else:
                raise Exception(f"Unknown network step type: {self._regression_type}")

            sending = sum([b * r for b,r in zip(self.beta_sending, regression_send_rec_max(number_of_parties, dimension), strict=True)])
            receiving = sum([b * r for b,r in zip(self.beta_receiving, regression_send_rec_max(number_of_parties, dimension), strict=True)])
            global_data = sum([b * r for b,r in zip(self.beta_global, regresseion_global(number_of_parties, dimension), strict=True)])
            num_messages = sum([b * r for b,r in zip(self.beta_num_messages, REGRESSION_NET_BCH_NUM_MESSAGES(number_of_parties, dimension), strict=True)])
            max_single = sum([b * r for b,r in zip(self.beta_max_single, regression_send_rec_max(number_of_parties, dimension), strict=True)])
            self._lookuptable[number_of_parties, dimension] = (sending, receiving, global_data, num_messages, max_single)
        return self._lookuptable[number_of_parties, dimension]

    def __str__(self):
        output = f"Beta Sending    : {self.beta_sending}\n"
        output += f"Beta Receiving : {self.beta_receiving}"
        output += f"Beta Global    : {self.beta_global}\n"
        output += f"Beta Num Msg   : {self.beta_num_messages}\n"
        output += f"Beta Max Single: {self.beta_max_single}"
        return output


class EstimationNetwork:
    """
    This represents the estimation formula for an ASM instruction
    """

    def __init__(self, asm_steps_sending_receiving, beta_mpspdz_global_data, regression_type):
        # Values for sending and receiving
        self.asm_steps_sending_receiving = asm_steps_sending_receiving

        # mp-spdz global data beta
        self.beta_mpspdz_global_data = beta_mpspdz_global_data
        # lookuptable to speed up estimations
        self._lookuptable = {}

        # Store the type of regression
        self.regression_type = regression_type

    def get_steps(self):
        # TODO REMOVE
        return self.asm_steps_sending_receiving

    def get_mpspdz_global_data(self, number_of_parties, dimension):
        """
        Here we compute the MP-SPDZ global data sent
        dimension is either vector_length ot batch size
        """
        if self.regression_type == "network-batch":
            regression = REGRESSION_BATCHSIZE_GLOBAL
        elif self.regression_type == "network-only":
            regression = REGRESSION_NETWORK_GLOBAL
        else:
            logger.critical(f"UNKNOWN estimation type network: {self.regression_type}")
            exit(1)
        if (number_of_parties, dimension) not in self._lookuptable:
            mpspdz_global = sum([b * r for b,r in zip(self.beta_mpspdz_global_data, regression(number_of_parties, dimension), strict=True)])
            self._lookuptable[number_of_parties, dimension] = mpspdz_global
        return self._lookuptable[number_of_parties, dimension]

    def __str__(self):
        output = f"Estimation {self.regression_type}: Total Steps = {len(self.asm_steps_sending_receiving)}"
        for i, s in enumerate(self.asm_steps_sending_receiving):
            output += f"\n    Step {i+1}: Beta Sending   : {s.beta_sending}"
            output += f"\n                Beta Receiving : {s.beta_receiving}"
            output += f"\n                Beta Global    : {s.beta_global}"
            output += f"\n                Beta Num MSg   : {s.beta_num_messages}"
            output += f"\n                Beta Max Single: {s.beta_max_single}"
        output += f"\n    Beta MP-SPDZ  : {self.beta_mpspdz_global_data}"
        return output

    def to_json(self):
        new_dict = { "regression_type": self.regression_type,
                     "beta_mpspdz_global_data": self.beta_mpspdz_global_data,
                     "asm_steps_sending_receiving": []}
        if USE_SYMPY:
            new_dict["beta_mpspdz_global_data"] = str(self.beta_mpspdz_global_data)
        else:
            new_dict["beta_mpspdz_global_data"] = self.beta_mpspdz_global_data

        for s in self.asm_steps_sending_receiving:
            # Map frequencies to dicts
            if USE_SYMPY:
                tmp = {"beta_sending":str(s.beta_sending), "beta_receiving":str(s.beta_receiving), "beta_global":str(s.beta_global), "beta_num_messages":str(s.beta_num_messages), "beta_max_single":str(s.beta_max_single)}
            else:
                tmp = {"beta_sending":s.beta_sending, "beta_receiving":s.beta_receiving, "beta_global":s.beta_global, "beta_num_messages":s.beta_num_messages, "beta_max_single":s.beta_max_single}
            new_dict["asm_steps_sending_receiving"].append(tmp)
        return json.dumps(new_dict, indent=4)


    @classmethod
    def from_json_file(cls, file_name):
        with open(file_name, 'r') as f:
            json_data = json.load(f)

        if json_data["regression_type"] not in ["network-batch", "network-only"]:
            raise Exception(f"UNKNOWN estimation type network: {self.regression_type}")

        if USE_SYMPY:
            tmp_beta_mpspdz_global = sympy.sympify(json_data["beta_mpspdz_global_data"])
        else:
            tmp_beta_mpspdz_global = numpy.asarray(json_data["beta_mpspdz_global_data"]).astype(numpy.float64)

        asm_steps_sending_receiving = []
        for s in json_data["asm_steps_sending_receiving"]:
            if USE_SYMPY:
                tmp = EstimationNetworkStep(sympy.sympify(s["beta_sending"]),
                                            sympy.sympify(s["beta_receiving"]),
                                            sympy.sympify(s["beta_global"]),
                                            sympy.sympify(s["beta_num_messages"]),
                                            sympy.sympify(s["beta_max_single"]),
                                            json_data["regression_type"])
            else:
                tmp = EstimationNetworkStep(numpy.asarray(s["beta_sending"]).astype(numpy.float64),
                                            numpy.asarray(s["beta_receiving"]).astype(numpy.float64),
                                            numpy.asarray(s["beta_global"]).astype(numpy.float64),
                                            numpy.asarray(s["beta_num_messages"]).astype(numpy.float64),
                                            numpy.asarray(s["beta_max_single"]).astype(numpy.float64),
                                            json_data["regression_type"])
            asm_steps_sending_receiving.append(tmp)
        return cls(asm_steps_sending_receiving, tmp_beta_mpspdz_global, json_data["regression_type"])


class EstimationInit:
    """
    This class defines the comuation estimation information
    """

    def __init__(self, beta_init):
        # Values computation time
        self.beta_init = beta_init

        # lookuptable to speed up estimations
        self._lookuptable = {}
        
    
    def get_init_global_data(self, number_of_parties):
        if number_of_parties not in self._lookuptable:
            init_global_data = sum([b * r for b,r in zip(self.beta_init, REGRESSION_INIT_GLOBAL_DATA(number_of_parties), strict=True)])
            self._lookuptable[number_of_parties] = init_global_data
        return self._lookuptable[number_of_parties]

    def __str__(self):
        return f"Estimation INIT: {self.beta_init}"

    def to_json(self):
        if USE_SYMPY:
            new_dict = {"beta_init": str(self.beta_init)}
        else:
            new_dict = {"beta_init": self.beta_init}

        return json.dumps(new_dict, indent=4)

    @classmethod
    def from_json_file(cls, file_name):
        with open(file_name, 'r') as f:
            json_data = json.load(f)

        if USE_SYMPY:
            t_beta = sympy.sympify(json_data["beta_init"])
        else:
            t_beta = numpy.asarray(json_data["beta_init"]).astype(numpy.float64)
        return cls(t_beta)

#######################
# Helper section      #
# Not actual metadata #
#######################

def compute_r_squared(verify, estimated):
    """
    Compute R2 (R squared value)
    """
    if not verify or not estimated:
        return -math.inf
    v_avg = sum(verify)/len(verify)
    sum_square_residuals = sum([(v - e) ** 2 for v, e in zip(verify, estimated, strict=True)])
    sum_square_totals = sum([(v - v_avg) ** 2 for v in verify])
    if sum_square_totals == 0:
        if sum_square_residuals == 0:
            return 1  
        else:
            return -math.inf
    r_squared = 1 - (sum_square_residuals / sum_square_totals)
    return r_squared

def compute_mse(verify, estimated):
    """
    Compute Mean Squared Error
    """
    if not verify or not estimated:
        return math.inf
    mse = sum([(v - e) ** 2 for v, e in zip(verify, estimated, strict=True)])/len(verify)
    return mse
