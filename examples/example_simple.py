#!/usr/bin/env python3

# simple fix so that you can execute the examples from the examples directory without moving
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import protocol

# Create Config
config = NeonConfig.from_config_files()

# Create new NEON handler
neon = NeonHandler(OperationMode.LOCAL, config)

# set the protocol for the execution
neon.set_protocol(protocol.Shamir)

# Set number of parties
neon.set_number_of_parties(3)

# Set all inputs to the same value for demonstration purpose
# Note, Input has to be set AFTER the number of parties is defined 
neon.set_all_inputs("1 2 3 4")

# Select the Programm to be executed
neon.set_program("example")

# Perform the computation
report = neon.smpc()
