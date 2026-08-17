#!/usr/bin/env bash
#SBATCH --job-name=e4b
#SBATCH --partition=c23ms
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=logs/e4b_%A_%a.out

# E4b: controlled party-count sweep (24 cells + 18 size-matched controls).
# Submit with:
#   sbatch --array=0-41%10 --export=ALL,CMDFILE=$HOME/Federated-PM--new-neon-/cmds_e4b.txt eval/cluster_e4b.sbatch
set -euo pipefail

module load GCCcore/11.3.0
module load Python/3.10.4

REPO="$HOME/Federated-PM--new-neon-"
cd "$REPO"
source fed_env/bin/activate
export NEON_DATA_ROOT="$HOME"
export NEON_THREADS="$SLURM_CPUS_PER_TASK"

# Every task needs an isolated checkout: input encoding writes Player-Data,
# and MP-SPDZ compilation writes shared Program files. Running array cells in
# the same checkout would make concurrent tasks overwrite one another.
SCRATCH="$TMPDIR/e4b_$SLURM_ARRAY_JOB_ID/$SLURM_ARRAY_TASK_ID"
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
mkdir -p eval_results/e4b_scaling_n logs
mkdir -p temp/MP/mp-spdz-0.4.2/Programs/{Source,Bytecode,Schedules,asm}

CMD=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$CMDFILE")
if [[ -z "$CMD" ]]; then
    echo "No command for task $SLURM_ARRAY_TASK_ID in $CMDFILE"
    exit 2
fi
echo "[$(date)] task=$SLURM_ARRAY_TASK_ID node=$SLURMD_NODENAME cmd: $CMD"

set +e
eval "$CMD"
run_rc=$?
set -e

# The runner persists success and failure records before returning. Ship the
# unique per-cell JSON back even when the command fails.
mkdir -p "$REPO/eval_results/e4b_scaling_n"
rsync -a eval_results/e4b_scaling_n/ "$REPO/eval_results/e4b_scaling_n/"

# Reuse compiled programs across later cells with the same public dimensions.
for directory in Source Bytecode Schedules; do
    if [[ -d "temp/MP/mp-spdz-0.4.2/Programs/$directory" ]]; then
        rsync -a "temp/MP/mp-spdz-0.4.2/Programs/$directory/" \
                 "$REPO/temp/MP/mp-spdz-0.4.2/Programs/$directory/"
    fi
done

echo "[$(date)] task=$SLURM_ARRAY_TASK_ID finished rc=$run_rc"
exit "$run_rc"
