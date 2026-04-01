import os
import sys
import subprocess
import logging

# Add parent directory to path to find ProgramFiles
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from ProgramFiles.operationmode import OperationMode
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles import protocol
from ProgramFiles import network

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Driver")

import argparse
import importlib.util

_GRANULARITY_MS = {'ms': 1, 's': 1000, 'm': 60_000, 'h': 3_600_000}

def _parse_delta(s):
    import re
    s = s.strip()
    if s == '0':
        return 0  # exact equality sentinel — uses EQZ in MPC (cheaper than LTZ)
    m = re.fullmatch(r'(\d+)(ms|s|m|h)', s)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid delta '{s}'. Use: 0 (exact equality), 500ms, 10s, 1m, 2h")
    value, unit = int(m.group(1)), m.group(2)
    return value * _GRANULARITY_MS[unit]

# ... (Imports remain same)

def import_source_file(fname, modname):
    # ... (function body remains same)
    spec = importlib.util.spec_from_file_location(modname, fname)
    if spec is None:
        raise ImportError(f"Could not load source file: {fname}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def compute_dp_k(epsilon, dp_delta):
    """Compute partition selection threshold k from (epsilon, delta).
    Definition 5 of Rafiei et al. (ICPM 2022), based on Desfontaines et al. (2022).
    k = ceil((1/epsilon) * ln((e^epsilon + 2*delta - 1) / (delta * (e^epsilon + 1))))
    """
    import math
    e_eps = math.exp(epsilon)
    numerator = e_eps + 2 * dp_delta - 1
    denominator = dp_delta * (e_eps + 1)
    if denominator <= 0 or numerator <= 0:
        raise ValueError(f"Invalid DP parameters: epsilon={epsilon}, delta={dp_delta}")
    k = math.ceil((1.0 / epsilon) * math.log(numerator / denominator))
    return max(1, k)


def main():
    parser = argparse.ArgumentParser(description="Run Process Mining SMPC with Neon")
    parser.add_argument("--threshold", type=int, default=1, help="Filtering threshold (default: 1)")
    parser.add_argument("--threads", type=int, default=16, help="Number of threads (default: 16)")
    parser.add_argument("--k-anon", type=int, default=0, help="Enable K-Anonymity (0/1, default: 0)")
    parser.add_argument("--log-a", type=str, default="/home/jamil/Documents/Master_Input/OrgA/BPI_Challenge_2013_open_problems.xes.gz", help="Path to existing log A")
    parser.add_argument("--log-b", type=str, default="/home/jamil/Documents/Master_Input/OrgB/BPI_Challenge_2013_open_problems.xes.gz", help="Path to existing log B")
    parser.add_argument("--mode", type=str, choices=["local", "local-virtual"], default="local", help="Operation mode (default: local)")
    parser.add_argument("--delay", type=str, default=None, help="Manual Network delay (e.g. '20ms') - overrides preset if set")
    parser.add_argument("--network", type=str, default=None, choices=["unlimited", "lan", "wan-ent", "wan-fast", "wan-slow", "5g-avg", "5g-slow"], help="Network Preset Profile")
    # Runtime flags (default: enabled)
    parser.add_argument("--no-direct", dest="direct", action="store_false", help="Disable --direct flag at runtime: no direct communication between parties")
    parser.set_defaults(direct=True)
    # Features
    parser.add_argument("--use-handovers", action="store_true", help="Filter out internal events and only compute on handover synchronization points")
    parser.add_argument("--is-ocel", action="store_true", help="Force OCEL processing (Approach 1)")
    parser.add_argument("--flatten-type", type=str, default="Container", help="Object type to flatten OCEL log on")
    parser.add_argument("--partial-orders", type=int, default=0, help="Enable partial orders for concurrent events (0/1, default: 0)")
    parser.add_argument("--delta", type=_parse_delta, default='0',
                        help="Concurrency time window (default: 0 = exact timestamp equality, cheapest). Accepts: 0, 500ms, 10s, 1m, 2h")
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). Both parties must agree on the same value.")
    parser.add_argument("--enable-dp", type=int, default=0, help="Enable Differential Privacy (0/1, default: 0)")
    parser.add_argument("--epsilon", type=float, default=1.0, help="DP epsilon parameter (default: 1.0)")
    parser.add_argument("--dp-delta", type=float, default=0.01,
                        help="DP delta parameter for (eps,delta)-DP partition selection (default: 0.01)")
    parser.add_argument("--max-cases-per-individual", type=int, default=1,
                        help="Contribution bounding: max cases per individual (default: 1, sensitivity=1)")
    args = parser.parse_args()
    ts_granularity = _GRANULARITY_MS[args.timestamp_granularity]

    # --- 1. Generate Inputs ---
    is_ocel = args.is_ocel or args.log_a.lower().endswith(".json")
    script_name = "import_ocel.py" if is_ocel else "import_xes.py"
    module_name = "import_ocel" if is_ocel else "import_xes"
    print(f"--- Generating Inputs with {script_name} ---")
    
    # Ensure Player-Data exists
    os.makedirs("Player-Data", exist_ok=True)
    
    # Find import script
    import_path = f"./{script_name}" 
    if not os.path.exists(import_path):
        if os.path.exists(f"Programs/{script_name}"):
            import_path = f"Programs/{script_name}"
        elif os.path.exists(f"Programs/Source/{script_name}"):
            import_path = f"Programs/Source/{script_name}"
        else:
            raise FileNotFoundError(f"Could not find {script_name}")

    # Import as a module
    importer = import_source_file(import_path, module_name)

    # Execute generation logic directly
    print(f"Reading {args.log_a}...")
    if is_ocel:
        cases_a = importer.parse_ocel(args.log_a, flatten_type=args.flatten_type, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)
        print(f"Reading {args.log_b}...")
        cases_b = importer.parse_ocel(args.log_b, flatten_type=args.flatten_type, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)
    else:
        cases_a = importer.parse_xes(args.log_a, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)
        print(f"Reading {args.log_b}...")
        cases_b = importer.parse_xes(args.log_b, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)
    
    # encode_and_save returns (n_max, max_len)
    n_per_party, partial_len = importer.encode_and_save(cases_a, cases_b)
    
    print(f"Dynamic Config derived from inputs: N_PER_PARTY={n_per_party}, PARTIAL_LEN={partial_len}")
    
    # --- 2. Read Inputs ---
    with open("Player-Data/Input-P0-0", "r") as f:
        input_p0 = f.read().strip()
    
    with open("Player-Data/Input-P1-0", "r") as f:
        input_p1 = f.read().strip()
        
    print(f"Read inputs: P0 ({len(input_p0)} chars), P1 ({len(input_p1)} chars)")

    # --- 3. Setup Neon ---
    print("--- Setting up Neon ---")
    config = NeonConfig.from_config_files()
    
    # Select Mode
    op_mode = OperationMode.LOCAL
    if args.mode == "local-virtual":
        op_mode = OperationMode.LOCAL_VIRTUAL
        print("Mode: LOCAL_VIRTUAL (Network Simulation Enabled)")
    else:
        print("Mode: LOCAL (No Network Simulation)")
        
    neon = NeonHandler(op_mode, config)
    #TODO TLKS additional to k anonymity
    #TODO more motivation
    #TODO algo overview at the end
    # Apply Network Settings
    if args.network:
        preset_map = {
            "unlimited": network.Unlimited,
            "lan": network.LAN,
            "wan-ent": network.WAN_Enterprise,
            "wan-fast": network.WAN_Fast,
            "wan-slow": network.WAN_Slow,
            "5g-avg": network.Mobile_5G_Average,
            "5g-slow": network.Mobile_5G_Slow
        }
        selected_network = preset_map[args.network]
        print(f"Applying Network Preset: {args.network} ({selected_network})")
        neon.set_network(selected_network)
        
    # Manual delay overrides preset if provided
    if args.delay:
        config.delay = args.delay
        print(f"Manual Network Latency Override: {args.delay}")

    # Use SemiBin protocol
    neon.set_protocol(protocol.Semi)
    neon.set_number_of_parties(2)
    neon.set_program("process_mining")
    
    # Configuration Substitution
    neon.set_substitution('NEON_ARG_N_PER_PARTY', n_per_party)
    neon.set_substitution('NEON_ARG_PARTIAL_LEN', partial_len)
    neon.set_substitution('NEON_ARG_N_THREADS', args.threads) 
    neon.set_substitution('NEON_ARG_THRESHOLD', args.threshold)
    neon.set_substitution('NEON_ARG_ENABLE_K_ANON', args.k_anon)
    neon.set_substitution('NEON_ARG_ENABLE_PARTIAL_ORDERS', args.partial_orders)
    neon.set_substitution('NEON_ARG_DELTA', args.delta)  # already in ms
    neon.set_substitution('NEON_ARG_ENABLE_DP', args.enable_dp)
    epsilon_num = int(args.epsilon * 1000)
    neon.set_substitution('NEON_ARG_EPSILON_NUM', epsilon_num)
    neon.set_substitution('NEON_ARG_EPSILON_DEN', 1000)

    # (epsilon, delta)-DP partition selection (TraVaS, Rafiei et al. ICPM 2022)
    # k serves as both noise truncation bound and frequency threshold
    if args.enable_dp:
        # Contribution bounding: if k_max > 1, sensitivity = k_max
        # Scale epsilon down by k_max so each individual gets epsilon-DP
        k_max = args.max_cases_per_individual
        effective_epsilon = args.epsilon / k_max
        if k_max > 1:
            print(f"Contribution bounding: k_max={k_max}, effective epsilon={effective_epsilon:.4f} (from {args.epsilon})")
        dp_k = compute_dp_k(effective_epsilon, args.dp_delta)
        print(f"DP partition selection: epsilon={effective_epsilon}, delta={args.dp_delta}, k={dp_k}")
        neon.set_substitution('NEON_ARG_DP_K', dp_k)
        # Override threshold: k and threshold must be coupled for (eps,delta)-DP guarantee
        neon.set_substitution('NEON_ARG_THRESHOLD', dp_k)
        # Pass effective epsilon to MPC (scaled by contribution bound)
        epsilon_num = int(effective_epsilon * 1000)
        neon.set_substitution('NEON_ARG_EPSILON_NUM', epsilon_num)
    else:
        neon.set_substitution('NEON_ARG_DP_K', 0)

    # Set Inputs
    neon.set_input(0, input_p0)
    neon.set_input(1, input_p1)
    
    # --- 4. Execute ---
    print("--- Executing SMPC (compile + run) ---")
    import time
    smpc_start = time.time()
    report = neon.smpc(
        direct=args.direct
    )
    smpc_wall = time.time() - smpc_start

    if report.run_was_successfull():
        smpc_runtime = report.client_reports[0].total_runtime
        compile_time = smpc_wall - smpc_runtime
        print("SMPC finished (successful)")
        print(f"Compile time: {compile_time:.2f}s")
        print(f"Runtime: {smpc_runtime}s")
        print(f"Total (compile + run): {smpc_wall:.2f}s")
        
        # Parse output for Data Sent and Rounds
        try:
            raw_output = report.client_reports[0].stdout.decode('utf-8', errors='ignore')
            import re
            
            # Global data sent = 28.5284 MB (approx)
            data_sent_match = re.search(r"Global data sent\s*=\s*([0-9.]+)\s*MB", raw_output)
            if data_sent_match:
                print(f"Data Sent (MB): {data_sent_match.group(1)}")
            else:
                 # Fallback: Sum of individual data sent if global missing
                 pass

            # Rounds = 1234
            rounds_match = re.search(r"Rounds\s*=\s*([0-9]+)", raw_output)
            if rounds_match:
                print(f"Rounds: {rounds_match.group(1)}")
                
        except Exception as e:
            print(f"Error parsing metrics: {e}")
    else:
        print("SMPC failed")
        exit(1)

if __name__ == "__main__":
    main()
