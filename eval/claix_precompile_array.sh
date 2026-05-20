#!/usr/bin/zsh
# CLAIX parallel precompile pass — one array task per unique compile config.
# 89 tasks at 30 concurrent → ~50 min wall instead of ~12 h sequential.
#
# Each task:
#   1. rsyncs the repo to $TMPDIR (isolated from other tasks' Player-Data writes)
#   2. runs `python3 eval/precompile_all.py --only $SLURM_ARRAY_TASK_ID`
#   3. ships the compiled bytecode back to $HOME — each task produces files
#      with a UNIQUE program hash so concurrent writes to the same dir don't
#      collide.
#
# Submit:
#   sbatch --array=0-88%30 eval/claix_precompile_array.sh
# (89 unique configs; bump the upper bound if precompile_all.py reports more.)

### --- Job Parameters ---
#SBATCH --job-name=neon_pre
#SBATCH --output=logs/slurm/precompile_%A_%a.out
#SBATCH --error=logs/slurm/precompile_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=01:30:00
#SBATCH --partition=c23ms

set -euo pipefail

### --- Setup ---
module load GCCcore/11.3.0
module load Python/3.10.4

REPO="$HOME/Federated-PM--new-neon-"
cd "$REPO"
source fed_env/bin/activate

export NEON_DATA_ROOT="$HOME"
# NEON_THREADS feeds NEON_ARG_N_THREADS into the .mpc substitution, which
# changes the program hash. Must match what the eval array sets so caches hit.
# Note: this is INDEPENDENT of --cpus-per-task — compile itself is single-threaded.
export NEON_THREADS=64

mkdir -p logs/slurm

### --- Per-task scratch directory ---
SCRATCH="$TMPDIR/neon_pre_$SLURM_ARRAY_JOB_ID/$SLURM_ARRAY_TASK_ID"
mkdir -p "$SCRATCH"
rsync -a --exclude='eval_results' --exclude='logs/slurm' \
      --exclude='.git' --exclude='__pycache__' \
      "$REPO/" "$SCRATCH/"
cd "$SCRATCH"

### --- Run the assigned compile ---
echo "[$(date)] task=$SLURM_ARRAY_TASK_ID node=$SLURMD_NODENAME starting"
python3 eval/precompile_all.py --only "$SLURM_ARRAY_TASK_ID"
rc=$?

### --- Ship the compiled bytecode back ---
# Each task wrote files with its own unique program-hash prefix, so concurrent
# rsyncs to the shared Programs/ directory do not collide.
if [[ $rc -eq 0 ]]; then
    rsync -a "temp/MP/mp-spdz-0.4.2/Programs/Source/" \
             "$REPO/temp/MP/mp-spdz-0.4.2/Programs/Source/"
    rsync -a "temp/MP/mp-spdz-0.4.2/Programs/Schedules/" \
             "$REPO/temp/MP/mp-spdz-0.4.2/Programs/Schedules/"
    rsync -a "temp/MP/mp-spdz-0.4.2/Programs/Bytecode/" \
             "$REPO/temp/MP/mp-spdz-0.4.2/Programs/Bytecode/"
    echo "[$(date)] task=$SLURM_ARRAY_TASK_ID cache shipped back to \$HOME"
else
    echo "[$(date)] task=$SLURM_ARRAY_TASK_ID failed (rc=$rc) — not shipping"
fi

exit $rc
