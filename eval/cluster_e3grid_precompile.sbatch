#!/usr/bin/env bash
#SBATCH --job-name=e3g_pre
#SBATCH --partition=c23ms
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=36G
#SBATCH --time=01:30:00
#SBATCH --output=logs/e3grid_pre_%A_%a.out
#SBATCH --error=logs/e3grid_pre_%A_%a.err

# E3g has 32 conservatively deduplicated compile configurations
# (2 datasets x 4 case caps x 4 trace-width caps). Submit with:
#   sbatch --array=0-31%10 eval/cluster_e3grid_precompile.sbatch
set -euo pipefail

module load GCCcore/11.3.0
module load Python/3.10.4

REPO="$HOME/Federated-PM--new-neon-"
cd "$REPO"
source fed_env/bin/activate
export NEON_DATA_ROOT="$HOME"
# Compilation is single-threaded, but this substitution must match the later
# 64-thread measurement circuits so the program hashes and cache files match.
export NEON_THREADS=64

SCRATCH="$TMPDIR/e3grid_pre_$SLURM_ARRAY_JOB_ID/$SLURM_ARRAY_TASK_ID"
mkdir -p "$SCRATCH"
set +e
rsync -a --exclude='eval_results' --exclude='logs' --exclude='.git' \
      --exclude='__pycache__' "$REPO/" "$SCRATCH/"
rsync_rc=$?
set -e
if [[ $rsync_rc -ne 0 && $rsync_rc -ne 24 ]]; then
    echo "rsync to scratch failed with rc=$rsync_rc"
    exit "$rsync_rc"
fi

cd "$SCRATCH"
mkdir -p temp/MP/mp-spdz-0.4.2/Programs/{Source,Bytecode,Schedules,asm}
echo "[$(date)] task=$SLURM_ARRAY_TASK_ID node=$SLURMD_NODENAME starting"
set +e
python3 eval/precompile_all.py --experiment e3g --only "$SLURM_ARRAY_TASK_ID"
compile_rc=$?
set -e

if [[ $compile_rc -eq 0 ]]; then
    for directory in Source Bytecode Schedules; do
        rsync -a "temp/MP/mp-spdz-0.4.2/Programs/$directory/" \
                 "$REPO/temp/MP/mp-spdz-0.4.2/Programs/$directory/"
    done
    echo "[$(date)] task=$SLURM_ARRAY_TASK_ID cache shipped"
else
    echo "[$(date)] task=$SLURM_ARRAY_TASK_ID failed rc=$compile_rc"
fi

exit "$compile_rc"
