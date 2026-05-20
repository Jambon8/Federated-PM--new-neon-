#!/bin/bash
# SLURM array dispatch template for Chapter 6 experiments.
#
# Usage:
#   1. Generate the command list:
#        python3 eval/thesis_experiments.py --list > eval/commands.txt
#   2. Submit:
#        sbatch --array=1-$(wc -l < eval/commands.txt) eval/run_on_hpc.sh
#   3. Aggregate after all jobs finish:
#        python3 eval/thesis_experiments.py --aggregate
#
# Each job runs in its own per-task scratch directory because
# Player-Data/ and the compiled .mpc binaries are clobbered per run.
#
# E7 (network) caveat: the 27 E7 runs use --mode local-virtual which calls
# `ip netns add` — needs CAP_NET_ADMIN. On HPC either:
#   (a) grant capabilities once on each compute node:
#         sudo setcap cap_net_admin+eip $(which ip) $(which tc)
#   (b) split the array: submit E7 commands as a separate job with sudo.
# All other 228 runs need no elevated privileges.

#SBATCH --job-name=neon_eval
#SBATCH --output=logs/%A_%a.out
#SBATCH --error=logs/%A_%a.err
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G

set -euo pipefail

REPO_SRC="${REPO_SRC:-$HOME/neon_new}"
SCRATCH="${SCRATCH:-/scratch/$USER}"
WORK="$SCRATCH/$SLURM_ARRAY_JOB_ID/$SLURM_ARRAY_TASK_ID"

mkdir -p "$WORK"
rsync -a --exclude='eval_results' --exclude='.git' "$REPO_SRC/" "$WORK/"
cd "$WORK"

CMD=$(sed -n "${SLURM_ARRAY_TASK_ID}p" eval/commands.txt)
echo "Task $SLURM_ARRAY_TASK_ID: $CMD"

eval "$CMD"

# Copy the JSON result back so aggregation sees it.
mkdir -p "$REPO_SRC/eval_results"
rsync -a "eval_results/" "$REPO_SRC/eval_results/"
