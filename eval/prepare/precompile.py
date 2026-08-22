"""Pre-warm the MP-SPDZ compile cache for every unique configuration in the
Chapter 6 experiment plan. Run this ONCE before the parallel array sweep so
that each array task is pure MPC execute (multi-threaded) instead of paying
the single-threaded compile cost ~336 times.

The dedup key intentionally ignores axes that do NOT affect compilation:
  --threads, --mode, --network, --seed.

Usage:
    python3 eval/prepare/precompile.py                # serial, show progress
    python3 eval/prepare/precompile.py --dry-run      # just print what would run
"""

import argparse
import os
import subprocess
import sys
import time
from typing import List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from eval.registry import EXPERIMENTS, all_runs, build_command  # noqa: E402

NON_COMPILE_FLAGS = {"--threads", "--mode", "--network", "--seed"}


def compile_key(mpc_args: List[str], n_parties: int,
                log_paths: List[str]) -> Tuple:
    """Key that dedupes runs whose compile output is identical.

    We keep every flag that influences the substituted .mpc source (threshold,
    k-anon, partial-orders, delta, enable-dp, epsilon, dp-delta, protocol,
    n-per-party-cap) and drop ones that only affect runtime."""
    filtered = []
    skip_next = False
    for tok in mpc_args:
        if skip_next:
            skip_next = False
            continue
        if tok in NON_COMPILE_FLAGS:
            skip_next = True
            continue
        filtered.append(tok)
    return (n_parties, tuple(log_paths), tuple(filtered))


def enumerate_unique(only=None):
    """Return list of (rid, mpc_args, n_parties, log_paths) for each unique compile config."""
    seen = {}
    for rid, mpc_args, n_parties, log_paths, meta in all_runs(only=only):
        k = compile_key(mpc_args, n_parties, log_paths)
        if k not in seen:
            seen[k] = (rid, mpc_args, n_parties, log_paths)
    return list(seen.values())


def run_one(idx, unique):
    """Execute the idx-th unique compile and return rc."""
    if idx < 0 or idx >= len(unique):
        print(f"ERROR: idx {idx} out of range [0, {len(unique)})", file=sys.stderr)
        return 2
    rid, mpc_args, n_parties, log_paths = unique[idx]
    cmd = build_command(mpc_args, n_parties, log_paths) + ["--compile-only"]
    print(f"[{idx+1}/{len(unique)}] {rid}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"   FAIL rc={proc.returncode}  wall={dt:.1f}s")
        print("   stderr tail:", proc.stdout[-500:])
        return 2
    ct = "?"
    for line in proc.stdout.splitlines():
        if line.startswith("Compile finished in"):
            ct = line.split()[3]
            break
    print(f"   ok  compile={ct}  wall={dt:.1f}s")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print commands without running them.")
    ap.add_argument("--only", type=int, default=None,
                    help="Compile only the 0-indexed config N and exit. Used by SLURM array tasks.")
    ap.add_argument("--start", type=int, default=0,
                    help="0-indexed starting position (for sequential resume).")
    ap.add_argument("--experiment", choices=sorted(EXPERIMENTS),
                    help="Restrict enumeration to one experiment family.")
    args = ap.parse_args()

    unique = enumerate_unique(only=args.experiment)
    print(f"Found {len(unique)} unique compile configurations "
          f"across {sum(1 for _ in all_runs(only=args.experiment))} total runs.\n")

    if args.dry_run:
        for i, (rid, mpc_args, n_parties, log_paths) in enumerate(unique):
            cmd = build_command(mpc_args, n_parties, log_paths) + ["--compile-only"]
            print(f"[{i}] {rid}: {' '.join(cmd)}")
        return

    if args.only is not None:
        sys.exit(run_one(args.only, unique))

    # Sequential mode: run every unique config in order, from --start.
    fails = []
    total = 0.0
    for i in range(args.start, len(unique)):
        t0 = time.time()
        rc = run_one(i, unique)
        total += time.time() - t0
        if rc != 0:
            fails.append(unique[i][0])
        print(f"   cum {total/60:.1f} min")
    print(f"\nDone. {len(unique) - len(fails)}/{len(unique)} succeeded.")
    if fails:
        print(f"Failed: {fails}")
        sys.exit(1)


if __name__ == "__main__":
    main()
