#!/usr/bin/zsh
# CLAIX submission wrapper that chunks the 327-task array into batches that
# stay under the default project's 100-submitted-job quota.
#
# Usage:   bash eval/claix_submit_chunks.sh
#
# Waits between chunks until the live queue drains below a threshold,
# then submits the next chunk. Tail the SLURM logs in eval/logs/slurm/
# to follow progress; squeue --me shows the live count.

set -euo pipefail

CHUNK_SIZE=90                # stay safely below the 100-submit cap
CONCURRENCY=30               # tasks running at once within a chunk
TOTAL=$(wc -l < eval/commands.txt)
QUEUE_THRESHOLD=20           # submit next chunk when ≤ this many of mine are queued

if [[ $TOTAL -eq 0 ]]; then
    echo "ERROR: eval/commands.txt is empty. Run eval/claix_setup.sh first."
    exit 1
fi

echo "Total tasks: $TOTAL   chunk=$CHUNK_SIZE   concurrency=$CONCURRENCY"

start=1
chunk_idx=0
while [[ $start -le $TOTAL ]]; do
    end=$(( start + CHUNK_SIZE - 1 ))
    [[ $end -gt $TOTAL ]] && end=$TOTAL
    chunk_idx=$(( chunk_idx + 1 ))

    echo
    echo "[$(date +%H:%M:%S)] Submitting chunk $chunk_idx: array=${start}-${end}%${CONCURRENCY}"
    sbatch --array=${start}-${end}%${CONCURRENCY} eval/claix_submit.sh

    start=$(( end + 1 ))
    [[ $start -gt $TOTAL ]] && break

    # Wait until the queue drains enough for the next chunk.
    echo "[$(date +%H:%M:%S)] Waiting for queue to drain below $QUEUE_THRESHOLD…"
    while true; do
        live=$(squeue --me -h 2>/dev/null | wc -l)
        if [[ $live -le $QUEUE_THRESHOLD ]]; then
            echo "[$(date +%H:%M:%S)] Queue has $live jobs — submitting next chunk."
            break
        fi
        sleep 60
    done
done

echo
echo "[$(date +%H:%M:%S)] All $TOTAL tasks submitted across $chunk_idx chunks."
echo "Monitor with: squeue --me"
echo "Aggregate when finished: NEON_DATA_ROOT=\$HOME python3 eval/thesis_experiments.py --aggregate"
