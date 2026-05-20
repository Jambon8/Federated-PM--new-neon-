#!/usr/bin/zsh
# CLAIX-2023 (RWTH Aachen) SLURM array dispatch for Chapter 6 experiments.
#
# One-time prep (run on a login node):
#   1. Generate N-way splits + verify data layout:
#        bash eval/claix_setup.sh
#   2. Generate the command list (327 lines after E6 expansion + E9):
#        NEON_DATA_ROOT=$HOME NEON_THREADS=64 \
#          python3 eval/thesis_experiments.py --list > eval/commands.txt
#   3. Submit (run no more than 30 tasks at once to avoid hammering shared FS):
#        sbatch --array=1-$(wc -l < eval/commands.txt)%30 eval/claix_submit.sh
#   4. Watch:           squeue --me
#   5. Aggregate after all tasks finish:
#        NEON_DATA_ROOT=$HOME python3 eval/thesis_experiments.py --aggregate
#
### --- Job Parameters ---
#SBATCH --job-name=neon_eval
#SBATCH --output=logs/slurm/%A_%a.out
#SBATCH --error=logs/slurm/%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --time=04:00:00
#SBATCH --partition=c23ms
#SBATCH --mem=64G

set -euo pipefail

### --- Setup ---
module load GCCcore/11.3.0
module load Python/3.10.4

REPO="$HOME/Federated-PM--new-neon-"
cd "$REPO"
source fed_env/bin/activate

export NEON_DATA_ROOT="$HOME"
export NEON_THREADS="$SLURM_CPUS_PER_TASK"

### --- Per-task scratch directory ---
# Each array task gets its own working copy so the per-run Player-Data/,
# logs/latest/, and temp/MP/.../Programs/ writes do not collide.
SCRATCH="$TMPDIR/neon_$SLURM_ARRAY_JOB_ID/$SLURM_ARRAY_TASK_ID"
mkdir -p "$SCRATCH"
rsync -a --exclude='eval_results' --exclude='logs/slurm' \
      --exclude='.git' --exclude='__pycache__' \
      "$REPO/" "$SCRATCH/"
cd "$SCRATCH"
mkdir -p eval_results logs

### --- Pick the command for this array index ---
CMD=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$REPO/eval/commands.txt")
echo "[$(date)] task=$SLURM_ARRAY_TASK_ID node=$SLURMD_NODENAME cmd: $CMD"

eval "$CMD" || echo "Task $SLURM_ARRAY_TASK_ID FAILED"

### --- Ship the JSON result back to the persistent location ---
mkdir -p "$REPO/eval_results"
rsync -a eval_results/ "$REPO/eval_results/"

echo "[$(date)] task=$SLURM_ARRAY_TASK_ID done"
