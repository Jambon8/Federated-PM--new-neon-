#!/usr/bin/zsh
# One-time CLAIX setup: verify every dataset path the experiment plan expects
# is reachable, then generate the 327-line command list. Run ONCE on a login
# node before sbatch.
#
# N-way splits are NOT generated here — they are generated locally
# (`python3 generate_test_data.py ...`) and uploaded with rsync. See the
# upload commands at the bottom of the verification check below.
#
# Assumes:
#   - Raw logs at  $HOME/Master_Input/OrgA/  and  $HOME/Master_Input/OrgB/
#   - N-way splits at  $HOME/split/  $HOME/split_4/  $HOME/split_5/
#                      $HOME/bpi13_closed/split_{3,4,5}/
#                      $HOME/bpi13_incidents/split_{3,4,5}/
#   - Repo at      $HOME/Federated-PM--new-neon-/
#   - Virtual env activated:  source fed_env/bin/activate

set -euo pipefail

REPO="$HOME/Federated-PM--new-neon-"
DATA="$HOME"

# Load the same modules used at runtime (libpython3.10.so lives in this module).
module load GCCcore/11.3.0
module load Python/3.10.4

cd "$REPO"
source fed_env/bin/activate

echo "=== Verifying every dataset path used by the experiment plan ==="
NEON_DATA_ROOT="$DATA" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ['HOME'], 'Federated-PM--new-neon-'))
from eval.thesis_experiments import all_runs
missing = []
for rid, _args, _n, logs, _meta in all_runs():
    for p in logs:
        if not os.path.exists(p):
            missing.append((rid, p))
if missing:
    print(f"MISSING {len(missing)} files. First 20:")
    for rid, p in missing[:20]:
        print(f"  {rid}  ->  {p}")
    print()
    print("Upload the missing N-way splits from local with:")
    print("  rsync -aP data/split data/split_4 data/split_5 \\")
    print("       data/bpi13_closed data/bpi13_incidents \\")
    print("       user-id@copy23-1.hpc.itc.rwth-aachen.de:~/")
    sys.exit(1)
print("OK — all dataset paths exist.")
PY

echo
echo "=== Generating the 327-command list ==="
NEON_DATA_ROOT="$DATA" NEON_THREADS=64 \
    python3 eval/thesis_experiments.py --list > eval/commands.txt
wc -l eval/commands.txt

echo
mkdir -p logs/slurm
echo "Created logs/slurm/ for SLURM stdout/stderr."
echo
echo "Submit with:  sbatch --array=1-$(wc -l < eval/commands.txt)%30 eval/claix_submit.sh"
