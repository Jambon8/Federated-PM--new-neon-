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
from ProgramFiles.helper import get_path_to_mpspdz, join_path_abs
import xenon

# Create Basic NEON
config = NeonConfig.from_config_files()
neon = NeonHandler(OperationMode.LOCAL, config)
neon.set_protocol(protocol.Shamir)

#####################
# Manual estimation #
#####################

# Set here the program name
neon.set_program("example_estimate_private-set-intersection")
# set the substitutions
# NEON_D = dimension
neon.set_substitution("NEON_D", 200)
program_hash = neon.compile_and_return_hash(compile_debug=True)
decompiled_file = join_path_abs(get_path_to_mpspdz(), "Programs", f"asm-{program_hash}-0")
# Set the number of parties
n_parties = 10

# Next, do the estimation with different network settings and batchsizes
print("LAN SETTING")
estimation = xenon.estimate_program(decompiled_file, n_parties, network.LAN, batch_size=10000)
xenon.print_estimation(estimation, print_details=False)

print("WAN SETTING")
estimation = xenon.estimate_program(decompiled_file, n_parties, network.WAN_Fast, batch_size=10000)
xenon.print_estimation(estimation, print_details=False)
# NOTE, set print_details=True for estiamtions of individual instructions
