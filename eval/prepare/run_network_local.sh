#!/usr/bin/env bash
# Run all 27 E7 network-simulation experiments locally with sudo.
#
# CLAIX denies CAP_NET_ADMIN to user processes, so `ip netns add` and
# `tc qdisc/netem` fail there. This wrapper runs the same 27 E7 runs on
# this machine where sudo is available.
#
# Usage:
#   sudo -E env "PATH=$PATH" "HOME=$HOME" \
#        NEON_THREADS=8 \
#        bash eval/prepare/run_network_local.sh
#
# Reads NEON_DATA_ROOT automatically as <repo-root>/data (the location of the
# per-party-count log tree on a local checkout). NEON_THREADS defaults to 8.
#
# What it does:
#   1. Cleans any stale network namespaces (idempotent).
#   2. Loops the 27 E7 commands in eval/commands_e7.txt (regenerated below).
#   3. Cleans namespaces between runs (defensive — a crashed run leaves them).
#   4. Returns ownership of new files to the invoking user via SUDO_USER.

set -uo pipefail

# Where the calling user's environment lives (we'll chown results back here).
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")

# Validate we're actually root.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: this script must be invoked with sudo (needs CAP_NET_ADMIN)." >&2
    exit 1
fi

# Locate repo root (the script lives in <repo>/eval/).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# On a local checkout the logs live at <repo>/data/<n>parties/.
# On the CLAIX cluster they live at $HOME/<n>parties/. Default to local.
: "${NEON_DATA_ROOT:=$REPO/data}"
: "${NEON_THREADS:=8}"
export NEON_DATA_ROOT NEON_THREADS

# Verify the expected data layout — fail fast if not.
if [[ ! -d "$NEON_DATA_ROOT/2parties" ]]; then
    echo "ERROR: expected $NEON_DATA_ROOT/2parties/. Set NEON_DATA_ROOT correctly." >&2
    exit 1
fi

echo "Running E7 locally as root. real_user=$REAL_USER repo=$REPO threads=$NEON_THREADS"
echo

# (1) Initial cleanup — kill any stale netns from previous attempts.
echo "[$(date +%H:%M:%S)] cleaning stale namespaces…"
python3 vendor/setup.py clean-virtual 2>/dev/null || true
ip netns 2>/dev/null | grep '^neon_ns' | xargs -r -I{} ip netns del {} || true

# (2) Regenerate the E7 commands list — skipping runs already on disk with rc=0.
#     Re-running a successful run is just wasted wall-clock on a slow laptop.
REPO_FOR_PY="$REPO" python3 -u <<'PY' > eval/commands_e7.txt
import json, os, sys
sys.path.insert(0, os.environ["REPO_FOR_PY"])
from eval.registry import all_runs

results_dir = os.path.join(os.environ["REPO_FOR_PY"], "eval_results", "scaling_network_latency")
done = set()
if os.path.isdir(results_dir):
    for fn in os.listdir(results_dir):
        if not fn.endswith(".json"):
            continue
        try:
            j = json.load(open(os.path.join(results_dir, fn)))
            if j["metrics"].get("return_code") == 0:
                done.add(j["run_id"])
        except Exception:
            pass

for rid, args, n, logs, meta in all_runs(only="e7"):
    if rid in done:
        continue
    print(f"python3 eval/registry.py --run {rid}")
PY
total=$(wc -l < eval/commands_e7.txt)
echo "[$(date +%H:%M:%S)] $total E7 runs to execute (skipping ones already done with rc=0)"
echo

# (3) Run them. Capture rc; clean ns between runs.
fail=0
mkdir -p logs/e7_local
i=0
while IFS= read -r cmd; do
    i=$(( i + 1 ))
    rid=$(echo "$cmd" | awk '{print $NF}')
    echo "[$(date +%H:%M:%S)] [$i/$total] $rid"
    eval "$cmd" > "logs/e7_local/${rid}.log" 2>&1
    rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "    -> FAILED (rc=$rc); see logs/e7_local/${rid}.log"
        fail=$(( fail + 1 ))
    fi
    # Defensive: kill any namespace the run leaked.
    ip netns 2>/dev/null | grep '^neon_ns' | xargs -r -I{} ip netns del {} || true
done < eval/commands_e7.txt

# (4) Chown new files back to the invoking user.
echo
echo "[$(date +%H:%M:%S)] restoring file ownership to $REAL_USER…"
chown -R "$REAL_USER:$REAL_USER" eval_results logs temp 2>/dev/null || true

echo
echo "[$(date +%H:%M:%S)] DONE. ran=$total failed=$fail"
echo "Aggregate with:  NEON_DATA_ROOT=\$HOME python3 eval/registry.py --aggregate"
