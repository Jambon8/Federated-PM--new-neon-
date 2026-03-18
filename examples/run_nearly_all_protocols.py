#!/usr/bin/env python3

# simple fix so that you can execute the examples from the examples directory without moving
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports

# This file just tries to execute an example with all protocols
# Note some are excluded below as they are very slow, or might be able to freeze a normal PC
# Enable them if needed
from ProgramFiles.operationmode import OperationMode
from ProgramFiles.neonconfig import NeonConfig, ReportVerbosityLevel
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles import protocol
import math

config = NeonConfig.from_config_files()
neon = NeonHandler(OperationMode.LOCAL, config)


neon.set_report_verbosity(ReportVerbosityLevel.HIGH)

# Makes runs for parties "smooth" (MP-SPDZ switches an algorithm at about 20 parties)
neon.set_bits_from_squares(True)

# Should not be required
neon.set_insecure_share_generation(False)




neon.set_substitution("NEON_n_parties", 2)

neon.set_batch_size(1000)
neon.set_bucket_size(4)

checks = []

import inspect
for name, obj in inspect.getmembers(protocol):
    if not isinstance(obj, protocol.Protocol):
        continue

    if obj in [protocol.HighGear, protocol.LowGear, protocol.ChaiGear, protocol.CowGear]:
        # Some protocols are excluded as they freeze my PC or a very slow
        continue


    if obj.max_number_of_parties == math.inf:
        neon.set_number_of_parties(5)
    else:
        neon.set_number_of_parties(obj.max_number_of_parties)

    # Note, Input has to be set AFTER the number of parties is defined 
    neon.set_all_inputs("1 2 3")
    
    if obj.supports_secret:
        neon.set_secrets([4, 5, 6])
        neon.set_read_secrets(True)
        neon.set_program("test_prime")
    else:
        neon.set_secrets(None)
        neon.set_read_secrets(False)
        neon.set_program("test_binary")
    
    
    neon.set_protocol(obj)
    report = neon.smpc()

    
    priv_out = report.client_reports[0].private_outputs == [20, 54] and report.client_reports[1].private_outputs == [20]
    secrets = report.secrets_after_computation == [14, 30, 64]
    if obj.supports_secret:
        checks.append((obj, priv_out, secrets))
    else:
        checks.append((obj, "*   ", "*"))


maxlen = 0
for n, _, _ in checks:
    if len(str(n)) > maxlen:
        maxlen = len(str(n))

print("+++++++++++++++++++++++++++++++++")
print("PROT, PrivOut, Secrtes")
for i, (n, p, s) in enumerate(checks):
    print(" "*(1 - (i+1) // 10) + str((i+1)), str(n) + " "*(maxlen - len(str(n))), p, s)
print("+++++++++++++++++++++++++++++++++")
