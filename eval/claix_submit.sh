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
# Memory is intentionally NOT set: c23ms gives 2540 MiB / core by default,
# so 64 cores → ~158 GiB without being billed for extra cores (per CLAIX docs).
#SBATCH --job-name=neon_eval
#SBATCH --output=logs/slurm/%A_%a.out
#SBATCH --error=logs/slurm/%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --time=04:00:00
#SBATCH --partition=c23ms

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
# rc 24 = files vanished mid-rsync; happens when another array task is writing
# back to $REPO concurrently. Treat as warning, not failure.
set +e
rsync -a --exclude='eval_results' --exclude='logs/slurm' \
      --exclude='.git' --exclude='__pycache__' \
      "$REPO/" "$SCRATCH/"
rc=$?
set -e
if [[ $rc -ne 0 && $rc -ne 24 ]]; then
    echo "rsync to scratch failed with rc=$rc"
    exit $rc
fi
cd "$SCRATCH"
mkdir -p eval_results logs
# Defensive: MP-SPDZ writes into these dirs but does not mkdir them.
mkdir -p temp/MP/mp-spdz-0.4.2/Programs/{Source,Bytecode,Schedules,asm}

### --- Pick the command for this array index ---
CMD=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$REPO/eval/commands.txt")
echo "[$(date)] task=$SLURM_ARRAY_TASK_ID node=$SLURMD_NODENAME cmd: $CMD"

eval "$CMD" || echo "Task $SLURM_ARRAY_TASK_ID FAILED"

### --- Ship results and compile artifacts back ---
# eval_results: the per-run JSON
mkdir -p "$REPO/eval_results"
rsync -a eval_results/ "$REPO/eval_results/"
# Compile cache: each task has unique program-hash filenames, so concurrent
# rsyncs to the same shared dir don't collide. Future tasks see this cache.
for d in Source Bytecode Schedules; do
    if [[ -d "temp/MP/mp-spdz-0.4.2/Programs/$d" ]]; then
        rsync -a "temp/MP/mp-spdz-0.4.2/Programs/$d/" \
                 "$REPO/temp/MP/mp-spdz-0.4.2/Programs/$d/"
    fi
done

echo "[$(date)] task=$SLURM_ARRAY_TASK_ID done"
