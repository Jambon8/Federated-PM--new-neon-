#!/usr/bin/env python3

# simple fix so that you can execute the examples from the examples directory without moving
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.neonconfig import NeonConfig, ReportVerbosityLevel
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import protocol
from ProgramFiles import network

# Create Config
config = NeonConfig.from_config_files()

# Create new NEON handler
neon = NeonHandler(OperationMode.LOCAL_VIRTUAL, config)

# set the protocol for the execution
neon.set_protocol(protocol.Shamir)



# Set the verbosity of the protocol, typically you want LOW, and HIGH only for debugging
neon.set_report_verbosity(ReportVerbosityLevel.HIGH)

# Makes runs for parties "smooth" (MP-SPDZ switches an algorithm at about 20 parties), so plotted graphs have a dip at around 20 parties
# Default is set to True
neon.set_bits_from_squares(True)

# Enable insecure share generateion, speeds ups the generation of a lot of shares
# Default is set to False
neon.set_insecure_share_generation(False)

# Whether the secrets shall be read after the exection. Can be used verify that the state is correct afterwards, otherwise set to false.
neon.set_read_secrets(True)



# Set number of parties
neon.set_number_of_parties(3)

# Set all inputs
# Note, Input has to be set AFTER the number of parties is defined 
neon.set_input(0, '1 2 3')
neon.set_input(1, '4 5 6')
neon.set_input(2, '7 8 9')

# Set preshared secrets (can be read in MP-SPDZ with read_from_file command)
neon.set_secrets([4, 5, 6])

# Select the Programm to be executed
neon.set_program("test_prime")

# Substitute the variable "NEON_n_parties" with the value "3"
neon.set_substitution("NEON_n_parties", "3")

# Set the batch and bucket size
neon.set_batch_size(1000)
neon.set_bucket_size(4)


# Perform the computation
report = neon.smpc()

# Check if the run was successfull
# This check can be used to e.g. repeat a run, or abort the complete evaluation
if report.run_was_successfull():
    print("Execution was as intended")
else:
    # Note, you can enforce an failure by providing not enough inputs to the protocol
    print("Something went wrong, please check the logs")

# Print the private output and the shared secrets
print("Private Output", report.client_reports[0].private_outputs)
print("Secrets", report.secrets_after_computation)


# Network delay and bandwidth restrictions can be set via
neon.set_network(network.LAN)
neon.smpc()

# Use a slower network setting
neon.set_network(network.Mobile_5G_Slow)
neon.smpc()

# Remove all network restrictions
neon.set_network(network.Unlimited)
neon.smpc()

# Introduce an artificial delay of 10 milliseconds only, no bandwidth restrictions.
neon.set_network(network.Network(delay="1ms", incoming_bandwidth=None, outgoing_bandwidth=None))
# or short
neon.set_network(network.Network(delay="1ms"))
neon.smpc()
