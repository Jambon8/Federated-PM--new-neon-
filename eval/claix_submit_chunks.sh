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

CHUNK_SIZE=80                # stay safely below the 100-submit cap
CONCURRENCY=10               # tasks running at once within a chunk (lower → easier to schedule on busy cluster)
CMDFILE="${CMDFILE:-eval/commands.txt}"   # override via:  CMDFILE=eval/commands_foo.txt bash eval/claix_submit_chunks.sh
TOTAL=$(wc -l < "$CMDFILE")
QUEUE_THRESHOLD=15           # submit next chunk when ≤ this many array tasks of mine are queued

# Optional first-task index. Use this to resume after a partial submission, e.g.
#   bash eval/claix_submit_chunks.sh --start 91
START_FROM=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --start) START_FROM=$2; shift 2 ;;
        *) echo "Unknown arg $1" >&2; exit 2 ;;
    esac
done

if [[ $TOTAL -eq 0 ]]; then
    echo "ERROR: $CMDFILE is empty. Run eval/claix_setup.sh first (or set CMDFILE)."
    exit 1
fi
if [[ $START_FROM -gt $TOTAL ]]; then
    echo "Nothing to do — start index $START_FROM > $TOTAL."
    exit 0
fi

# `squeue --me -h -r | wc -l` counts EXPANDED array tasks (one row per task);
# without -r SLURM collapses an array into a single summary row.
count_live() {
    squeue --me -h -r 2>/dev/null | wc -l
}

submit_chunk() {
    local s=$1 e=$2 idx=$3
    while true; do
        echo "[$(date +%H:%M:%S)] Submitting chunk $idx: array=${s}-${e}%${CONCURRENCY}"
        if sbatch --export=ALL,CMDFILE="$CMDFILE" --array=${s}-${e}%${CONCURRENCY} eval/claix_submit.sh; then
            return 0
        fi
        echo "[$(date +%H:%M:%S)] sbatch refused (likely quota). Sleeping 60s and retrying…"
        sleep 60
    done
}

echo "Cmdfile: $CMDFILE   total tasks: $TOTAL   chunk=$CHUNK_SIZE   concurrency=$CONCURRENCY   start=$START_FROM"
echo "Starting queue length: $(count_live)"

wait_for_drain() {
    while true; do
        live=$(count_live)
        if [[ $live -le $QUEUE_THRESHOLD ]]; then
            echo "[$(date +%H:%M:%S)] Queue has $live tasks (≤ $QUEUE_THRESHOLD) — submitting."
            return 0
        fi
        echo "[$(date +%H:%M:%S)] Queue has $live tasks, waiting…"
        sleep 60
    done
}

start=$START_FROM
chunk_idx=0
while [[ $start -le $TOTAL ]]; do
    end=$(( start + CHUNK_SIZE - 1 ))
    [[ $end -gt $TOTAL ]] && end=$TOTAL
    chunk_idx=$(( chunk_idx + 1 ))

    echo
    # Make sure the queue has room before we try to add this chunk.
    wait_for_drain
    submit_chunk $start $end $chunk_idx

    start=$(( end + 1 ))
done

echo
echo "[$(date +%H:%M:%S)] All $TOTAL tasks submitted across $chunk_idx chunks."
echo "Monitor with:  squeue --me -r        (shows individual array tasks)"
echo "Aggregate when finished: NEON_DATA_ROOT=\$HOME python3 eval/thesis_experiments.py --aggregate"
