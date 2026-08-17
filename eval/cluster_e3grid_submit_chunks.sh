#!/usr/bin/env bash
# Wait for E3g precompilation, then submit measurements below CLAIX limits.

set -euo pipefail

PRECOMPILE_JOB="${PRECOMPILE_JOB:?set PRECOMPILE_JOB to the E3g precompile array ID}"
REPO="${REPO:-$HOME/Federated-PM--new-neon-}"
CMDFILE="${CMDFILE:-$REPO/cmds_e3g.txt}"
JOB_SCRIPT="${JOB_SCRIPT:-eval/cluster_e3grid.sbatch}"
TOTAL="${TOTAL:-96}"
CHUNK_SIZE="${CHUNK_SIZE:-64}"
CONCURRENCY="${CONCURRENCY:-10}"
QUEUE_THRESHOLD="${QUEUE_THRESHOLD:-10}"
POLL_SECONDS="${POLL_SECONDS:-60}"

cd "$REPO"

count_live() {
    squeue --me -h -r 2>/dev/null | wc -l
}

echo "[$(date)] waiting for precompile array $PRECOMPILE_JOB"
while squeue -j "$PRECOMPILE_JOB" -h 2>/dev/null | grep -q .; do
    echo "[$(date)] precompile still active; live user tasks=$(count_live)"
    sleep "$POLL_SECONDS"
done

# sacct can lag briefly after the final task leaves squeue.
states=""
for _ in 1 2 3 4 5; do
    states=$(sacct -j "$PRECOMPILE_JOB" -X -n -P -o State 2>/dev/null || true)
    [[ -n "$states" ]] && break
    sleep 10
done
if [[ -z "$states" ]]; then
    echo "[$(date)] ERROR: no accounting states found for $PRECOMPILE_JOB"
    exit 1
fi
if grep -Eq 'FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL' <<<"$states"; then
    echo "[$(date)] ERROR: precompile array did not complete cleanly"
    printf '%s\n' "$states" | sort | uniq -c
    exit 1
fi
completed=$(grep -c '^COMPLETED' <<<"$states" || true)
if [[ $completed -lt 32 ]]; then
    echo "[$(date)] ERROR: expected at least 32 completed task records, found $completed"
    printf '%s\n' "$states" | sort | uniq -c
    exit 1
fi
echo "[$(date)] precompile complete ($completed completed records)"

start=0
chunk=0
while [[ $start -lt $TOTAL ]]; do
    while true; do
        live=$(count_live)
        if [[ $live -le $QUEUE_THRESHOLD ]]; then
            break
        fi
        echo "[$(date)] live queue=$live; waiting for <=$QUEUE_THRESHOLD"
        sleep "$POLL_SECONDS"
    done

    end=$((start + CHUNK_SIZE - 1))
    [[ $end -ge $TOTAL ]] && end=$((TOTAL - 1))
    chunk=$((chunk + 1))
    echo "[$(date)] submitting measurement chunk $chunk: $start-$end%$CONCURRENCY"
    while ! sbatch --export=ALL,CMDFILE="$CMDFILE" \
                   --array="$start-$end%$CONCURRENCY" "$JOB_SCRIPT"; do
        echo "[$(date)] submission refused; retrying after $POLL_SECONDS seconds"
        sleep "$POLL_SECONDS"
    done
    start=$((end + 1))
done

echo "[$(date)] all $TOTAL E3g measurements submitted in $chunk chunks"
