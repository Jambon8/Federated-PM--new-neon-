#!/usr/bin/zsh
# CLAIX pre-compilation pass — warms the MP-SPDZ compile cache for all 89 unique
# configurations BEFORE the parallel array sweep.
#
# Runs on 1 core in the persistent repo directory (not $TMPDIR) so the compile
# artifacts in temp/MP/.../Programs/ persist for the subsequent array jobs to
# rsync into their $TMPDIR scratch and hit cache instead of re-compiling.
#
# Estimated wall time: 30–90 min on the c23ms partition (single-threaded).
#
# Submit with:
#   sbatch eval/claix_precompile.sh

### --- Job Parameters ---
#SBATCH --job-name=neon_precompile
#SBATCH --output=logs/slurm/precompile_%j.out
#SBATCH --error=logs/slurm/precompile_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --partition=c23ms

set -euo pipefail

### --- Setup ---
module load GCCcore/11.3.0
module load Python/3.10.4

REPO="$HOME/Federated-PM--new-neon-"
cd "$REPO"
source fed_env/bin/activate

export NEON_DATA_ROOT="$HOME"
export NEON_THREADS=1

mkdir -p logs/slurm

### --- Pre-compile every unique config ---
echo "[$(date)] Starting pre-compile pass on node $SLURMD_NODENAME"
python3 eval/precompile_all.py
echo "[$(date)] Done. Cache populated under $REPO/temp/MP/.../Programs/"
echo "Subsequent array tasks will see cache hits and skip the compile step."
