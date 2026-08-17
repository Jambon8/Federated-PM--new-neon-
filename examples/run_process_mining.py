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
    parser.add_argument("--log-a", type=str, default=None, help="Path to log A (2-party shorthand)")
    parser.add_argument("--log-b", type=str, default=None, help="Path to log B (2-party shorthand)")
    parser.add_argument("--logs", nargs='+', default=None, help="Paths to N log files (one per party, minimum 2)")
    parser.add_argument("--mode", type=str, choices=["local", "local-virtual"], default="local", help="Operation mode (default: local)")
    parser.add_argument("--delay", type=str, default=None, help="Manual Network delay (e.g. '20ms') - overrides preset if set")
    parser.add_argument("--network", type=str, default=None, choices=["unlimited", "lan", "wan-ent", "wan-fast", "wan-slow", "5g-avg", "5g-slow"], help="Network Preset Profile")
    # Runtime flags (default: enabled)
    parser.add_argument("--no-direct", dest="direct", action="store_false", help="Disable --direct flag at runtime: no direct communication between parties")
    parser.set_defaults(direct=True)
    # Features
    parser.add_argument("--use-handovers", action="store_true", help="Collapse each party's maximal runs of internal (non-H) events into keyed fingerprint events before secret sharing; handover events are kept verbatim")
    parser.add_argument("--handover-activities", type=str, default=None,
                        help="Path to the global handover list H (one activity per line), applied identically by every party. "
                             "With --use-handovers, defaults to the union of activities flagged in the logs.")
    parser.add_argument("--partial-orders", type=int, default=0, help="Enable partial orders for concurrent events (0/1, default: 0)")
    parser.add_argument("--delta", type=_parse_delta, default='0',
                        help="Concurrency time window (default: 0 = exact timestamp equality, cheapest). Accepts: 0, 500ms, 10s, 1m, 2h")
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). Both parties must agree on the same value.")
    parser.add_argument("--enable-dp", type=int, default=0, help="Enable Differential Privacy (0/1, default: 0)")
    parser.add_argument("--protocol", type=str, default="semi",
                        choices=["semi", "rep-bin", "mal-rep-bin", "ps-rep-bin", "ccd", "mal-ccd"],
                        help="MPC protocol (default: semi). rep-bin/mal-rep-bin/ps-rep-bin = 3-party only, ccd/mal-ccd = 3+ party honest majority")
    parser.add_argument("--epsilon", type=float, default=1.0, help="DP epsilon parameter (default: 1.0)")
    parser.add_argument("--dp-delta", type=float, default=0.01,
                        help="DP delta parameter for (eps,delta)-DP partition selection (default: 0.01)")
    parser.add_argument("--n-per-party-cap", type=int, default=None,
                        help="Subsample to at most N case-IDs (shared across parties) before encoding. Used by E3 scaling experiments.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for subsampling (default: 42)")
    parser.add_argument("--compile-only", action="store_true",
                        help="Compile the MPC circuit and exit (warms the cache). Used by the precompile pass on HPC.")
    args = parser.parse_args()
    ts_granularity = _GRANULARITY_MS[args.timestamp_granularity]

    # --- Resolve log paths ---
    if args.logs:
        log_paths = args.logs
    elif args.log_a and args.log_b:
        log_paths = [args.log_a, args.log_b]
    else:
        # Default 2-party paths
        log_paths = [
            "/home/jamil/Documents/Master_Input/OrgA/BPI_Challenge_2013_open_problems.xes.gz",
            "/home/jamil/Documents/Master_Input/OrgB/BPI_Challenge_2013_open_problems.xes.gz",
        ]
    n_parties = len(log_paths)
    if n_parties < 2:
        print("Error: At least 2 log files required.")
        sys.exit(1)

    # --- 1. Generate Inputs ---
    script_name = "import_xes.py"
    module_name = "import_xes"
    print(f"--- Generating Inputs with {script_name} ({n_parties} parties) ---")

    os.makedirs("Player-Data", exist_ok=True)

    import_path = f"./{script_name}"
    if not os.path.exists(import_path):
        if os.path.exists(f"Programs/{script_name}"):
            import_path = f"Programs/{script_name}"
        elif os.path.exists(f"Programs/Source/{script_name}"):
            import_path = f"Programs/Source/{script_name}"
        else:
            raise FileNotFoundError(f"Could not find {script_name}")

    importer = import_source_file(import_path, module_name)

    # Resolve the single global handover list H (shared by every party). Either
    # load a curated file or derive it as the union of activities flagged in the
    # logs; persist the resolved list for reproducibility.
    handover_set = None
    if args.use_handovers:
        if args.handover_activities:
            handover_set = importer.load_handover_list(args.handover_activities)
            print(f"Loaded global handover list H: {len(handover_set)} activities from {args.handover_activities}")
        else:
            handover_set = importer.derive_handover_union(log_paths)
            print(f"Derived global handover list H (union): {len(handover_set)} activities")
        h_path = "Player-Data/handover_activities.txt"
        with open(h_path, "w") as f:
            f.write("\n".join(sorted(handover_set)) + "\n")
        print(f"Wrote resolved handover list H to '{h_path}'")

    cases_list = []
    for p, path in enumerate(log_paths):
        print(f"Reading P{p}: {path}...")
        cases = importer.parse_xes(path, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity,
                                   party_index=p, handover_activities=handover_set)
        cases_list.append(cases)

    if args.n_per_party_cap is not None and args.n_per_party_cap > 0:
        import random
        rng = random.Random(args.seed)
        # Subsample on the intersection of case IDs so PSI still finds matches.
        id_sets = [set(c["id"] for c in cases) for cases in cases_list]
        shared = sorted(set.intersection(*id_sets))
        if len(shared) > args.n_per_party_cap:
            shared = rng.sample(shared, args.n_per_party_cap)
        keep = set(shared)
        cases_list = [[c for c in cases if c["id"] in keep] for cases in cases_list]
        print(f"Subsampled to {len(keep)} shared case IDs (cap={args.n_per_party_cap})")

    n_per_party, partial_len = importer.encode_and_save(cases_list)

    print(f"Dynamic Config: N_PER_PARTY={n_per_party}, PARTIAL_LEN={partial_len}, N_PARTIES={n_parties}")

    # --- 2. Read Inputs ---
    inputs = {}
    for p in range(n_parties):
        with open(f"Player-Data/Input-P{p}-0", "r") as f:
            inputs[p] = f.read().strip()
        print(f"Read input P{p}: {len(inputs[p])} chars")

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

    # Protocol selection
    protocol_map = {
        'semi': protocol.Semi,
        'rep-bin': protocol.ReplicatedBin,
        'mal-rep-bin': protocol.MaliciousRepBin,
        'ps-rep-bin': protocol.PSRepBin,
        'ccd': protocol.CCD,
        'mal-ccd': protocol.MaliciousCCD,
    }
    selected_protocol = protocol_map.get(args.protocol, protocol.Semi)
    print(f"Protocol: {args.protocol}")
    neon.set_protocol(selected_protocol)
    neon.set_number_of_parties(n_parties)
    neon.set_program("process_mining")
    
    # Configuration Substitution
    neon.set_substitution('NEON_ARG_N_PER_PARTY', n_per_party)
    neon.set_substitution('NEON_ARG_PARTIAL_LEN', partial_len)
    neon.set_substitution('NEON_ARG_N_THREADS', args.threads) 
    neon.set_substitution('NEON_ARG_THRESHOLD', args.threshold)
    neon.set_substitution('NEON_ARG_N_PARTIES', n_parties)
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
        dp_k = compute_dp_k(args.epsilon, args.dp_delta)
        print(f"DP partition selection: epsilon={args.epsilon}, delta={args.dp_delta}, k={dp_k}")
        neon.set_substitution('NEON_ARG_DP_K', dp_k)
        # Override threshold: the (eps,delta)-DP guarantee (Desfontaines et al.,
        # Thm. 6) requires the STRICT release rule "noisy count > k". The MPC
        # program tests "count >= THRESHOLD", so pass k+1 (integers: > k <=> >= k+1).
        neon.set_substitution('NEON_ARG_THRESHOLD', dp_k + 1)
    else:
        neon.set_substitution('NEON_ARG_DP_K', 0)

    # Set Inputs
    for p in range(n_parties):
        neon.set_input(p, inputs[p])
    
    # --- 4. Execute ---
    if args.compile_only:
        print("--- Compiling MPC circuit (compile-only) ---")
        import time
        compile_start = time.time()
        h = neon.compile_and_return_hash(compile_debug=False)
        compile_wall = time.time() - compile_start
        print(f"Compile finished in {compile_wall:.2f}s. Program hash: {h}")
        return

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
