"""
Centralized baseline: replicates the MPC pipeline logic in plain Python.
Used as ground truth for correctness testing.

Replicates:
  1. Encoding (P0 asc, P1 desc, padding)
  2. PSI via bitonic merge on case_id
  3. Reconstruction: merge partial traces via bitonic sort on timestamps
  4. Hashing: Jenkins XOR-shift with 64-bit wrapping
  5. Grouping: sort by hash, count consecutive equal hashes
  6. Filtering: threshold + optional k-anonymity
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_GRANULARITY_MS = {'ms': 1, 's': 1000, 'm': 60_000, 'h': 3_600_000}

def _parse_delta(s):
    import re
    import argparse
    s = s.strip()
    if s == '0':
        return 0  # exact equality sentinel
    m = re.fullmatch(r'(\d+)(ms|s|m|h)', s)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid delta '{s}'. Use: 0 (exact equality), 500ms, 10s, 1m, 2h")
    value, unit = int(m.group(1)), m.group(2)
    return value * _GRANULARITY_MS[unit]
import import_xes

PAD_ID = 2**60
PAD_TIME = 2**60
PAD_ACT = 0
MASK64 = (1 << 64) - 1
ACT_BITS = 17  # must match process_mining.mpc: 43-bit ts + 17-bit act = 60 bits, below the 2**60 padding sentinel


def _to_signed64(v):
    """Convert to signed 64-bit two's complement representation."""
    v = v & MASK64
    if v >= (1 << 63):
        v -= (1 << 64)
    return v


def _to_unsigned64(v):
    return v & MASK64


def _encode_rows(cases, case_id_map, act_map, n_max, partial_len, pad_at_start=False):
    rows = []
    for c in cases:
        row = [case_id_map[c["id"]]]
        for j in range(partial_len):
            if j < len(c["events"]):
                t, act = c["events"][j]
                row.append(t)
                row.append(act_map[act])
            else:
                row.append(PAD_TIME)
                row.append(PAD_ACT)
        rows.append(row)

    pad_row = [PAD_ID] + [PAD_TIME, PAD_ACT] * partial_len
    pad_needed = n_max - len(cases)
    if pad_at_start:
        rows = [list(pad_row) for _ in range(pad_needed)] + rows
    else:
        rows = rows + [list(pad_row) for _ in range(pad_needed)]
    return rows


def _next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p


def _bitonic_merge_network(data, _row_width):
    """Bitonic merge on column 0 (case_id). Data is already bitonic (asc + pad + desc)."""
    n = len(data)
    step = n // 2
    while step > 0:
        for i in range(n // 2):
            k = i % step
            j = (i // step) * (2 * step)
            idx1 = j + k
            idx2 = idx1 + step
            if data[idx1][0] > data[idx2][0]:
                data[idx1], data[idx2] = data[idx2], data[idx1]
        step //= 2


def _reconstruction_sort(sort_keys, sort_vals_ts, sort_vals_act, is_padding_arr, next_pow2, enable_po):
    """Pruned bitonic merge on (sort_keys, sort_vals_ts, sort_vals_act)."""
    step = next_pow2 // 2
    while step > 0:
        pairs = []
        for j in range(0, next_pow2, step * 2):
            for k in range(step):
                pairs.append((j + k, j + k + step))

        for idx1, idx2 in pairs:
            p1 = is_padding_arr[idx1]
            p2 = is_padding_arr[idx2]

            if p1 and p2:
                pass
            elif not p1 and p2:
                pass
            elif p1 and not p2:
                sort_keys[idx1] = sort_keys[idx2]
                sort_vals_ts[idx1] = sort_vals_ts[idx2]
                sort_vals_act[idx1] = sort_vals_act[idx2]
                sort_keys[idx2] = PAD_TIME if not enable_po else PAD_TIME
                sort_vals_ts[idx2] = PAD_TIME
                sort_vals_act[idx2] = 0
                is_padding_arr[idx1] = False
                is_padding_arr[idx2] = True
            else:
                if sort_keys[idx1] > sort_keys[idx2]:
                    sort_keys[idx1], sort_keys[idx2] = sort_keys[idx2], sort_keys[idx1]
                    sort_vals_ts[idx1], sort_vals_ts[idx2] = sort_vals_ts[idx2], sort_vals_ts[idx1]
                    sort_vals_act[idx1], sort_vals_act[idx2] = sort_vals_act[idx2], sort_vals_act[idx1]
        step //= 2


def _jenkins_hash(values):
    """Jenkins XOR-shift hash with 64-bit wrapping. Operates on unsigned representation."""
    h = 0
    for val in values:
        v = _to_unsigned64(val)
        h = h ^ v
        h = (h ^ ((h << 10) & MASK64)) & MASK64
        h = (h ^ (h >> 6)) & MASK64

    h = (h ^ ((h << 3) & MASK64)) & MASK64
    h = (h ^ (h >> 11)) & MASK64
    h = (h ^ ((h << 15) & MASK64)) & MASK64
    return _to_signed64(h)


def compute_baseline(cases_a, cases_b, threshold=1, enable_k_anon=0, enable_partial_orders=0, delta=0):
    """
    Compute process mining result centrally (no MPC).
    Returns dict with 'variants', 'others_count', 'activity_map'.
    """
    # Sort: P0 ascending, P1 descending
    cases_a = sorted(cases_a, key=lambda c: c["id"])
    cases_b = sorted(cases_b, key=lambda c: c["id"], reverse=True)

    # Build global mappings
    all_case_ids = set(c["id"] for c in cases_a + cases_b)
    all_activities = set(e[1] for c in cases_a + cases_b for e in c["events"])
    case_id_map = {cid: i + 1 for i, cid in enumerate(sorted(all_case_ids))}
    act_map = {act: i + 1 for i, act in enumerate(sorted(all_activities))}
    id_to_act = {v: k for k, v in act_map.items()}

    n_a, n_b = len(cases_a), len(cases_b)
    n_max = max(n_a, n_b)
    max_len_a = max((len(c["events"]) for c in cases_a), default=0)
    max_len_b = max((len(c["events"]) for c in cases_b), default=0)
    partial_len = max(max_len_a, max_len_b)
    full_len = partial_len * 2
    total_n = n_max * 2
    row_width = 2 + partial_len * 2  # case_id + party_id + events

    # Encode rows
    rows_a = _encode_rows(cases_a, case_id_map, act_map, n_max, partial_len, pad_at_start=False)
    rows_b = _encode_rows(cases_b, case_id_map, act_map, n_max, partial_len, pad_at_start=True)

    # Build data matrix: [case_id, party_id, ts0, act0, ts1, act1, ...]
    data = []
    for row in rows_a:
        data.append([row[0], 0] + row[1:])
    for row in rows_b:
        data.append([row[0], 1] + row[1:])

    # --- PSI: Bitonic Merge ---
    next_pow2_psi = _next_pow2(total_n)
    padded = list(data[:n_max])  # P0 ascending
    for _ in range(next_pow2_psi - total_n):
        padded.append([PAD_ID] + [0] * (row_width - 1))
    padded.extend(data[n_max:])  # P1 descending

    _bitonic_merge_network(padded, row_width)
    data = padded[:total_n]

    # --- Reconstruction ---
    next_pow2_recon = _next_pow2(full_len)
    merged_data = []  # list of dicts: {match, activities, concurrent}

    for i in range(total_n - 1):
        ids_match = data[i][0] == data[i + 1][0]
        diff_parties = data[i][1] != data[i + 1][1]
        is_valid = data[i][0] != PAD_ID
        is_match = ids_match and diff_parties and is_valid

        sort_keys = [PAD_TIME] * next_pow2_recon
        sort_vals_ts = [PAD_TIME] * next_pow2_recon
        sort_vals_act = [0] * next_pow2_recon

        for k in range(partial_len):
            ts = data[i][2 + k * 2]
            act = data[i][2 + k * 2 + 1]
            # Composite key canonicalizes same-timestamp events by activity in both
            # regimes (matches process_mining.mpc load_events).
            sort_keys[k] = (ts << ACT_BITS) | act
            sort_vals_ts[k] = ts
            sort_vals_act[k] = act

        for k in range(partial_len):
            idx = next_pow2_recon - 1 - k
            ts = data[i + 1][2 + k * 2]
            act = data[i + 1][2 + k * 2 + 1]
            sort_keys[idx] = (ts << ACT_BITS) | act
            sort_vals_ts[idx] = ts
            sort_vals_act[idx] = act

        is_padding_arr = [False] * next_pow2_recon
        for k in range(partial_len, next_pow2_recon - partial_len):
            is_padding_arr[k] = True

        _reconstruction_sort(sort_keys, sort_vals_ts, sort_vals_act, is_padding_arr, next_pow2_recon, enable_partial_orders)

        activities = []
        concurrent = []
        for k in range(full_len):
            ts = sort_vals_ts[k]
            act = sort_vals_act[k]
            is_pad = (ts == PAD_TIME)
            final_act = 0 if is_pad else act
            activities.append(final_act if is_match else 0)

            if enable_partial_orders:
                if k == 0:
                    concurrent.append(0)
                else:
                    prev_ts = sort_vals_ts[k - 1]
                    diff = ts - prev_ts
                    is_close = (diff == 0) if delta == 0 else (diff < delta)  # delta in ms; 0 = exact equality
                    both_valid = (ts != PAD_TIME) and (prev_ts != PAD_TIME)
                    is_conc = 1 if (is_close and both_valid) else 0
                    concurrent.append(is_conc if is_match else 0)
            else:
                concurrent.append(0)

        merged_data.append({
            "match_bit": 0 if is_match else 1,
            "activities": activities,
            "concurrent": concurrent,
        })

    # Last row (no pair)
    merged_data.append({
        "match_bit": 1,
        "activities": [0] * full_len,
        "concurrent": [0] * full_len,
    })

    # --- Hashing ---
    hashes = []
    for row in merged_data:
        if enable_partial_orders:
            # Sequential Jenkins hash on packed values: activity | (conc_bit << 32).
            # The composite sort key already canonicalizes order within concurrent
            # groups by activity ID, so sequential hashing is order-independent.
            packed_vals = []
            for k in range(full_len):
                act = row["activities"][k]
                conc = row["concurrent"][k]
                packed = _to_signed64((_to_unsigned64(act) | ((_to_unsigned64(conc) << 32) & MASK64)) & MASK64)
                packed_vals.append(packed)
            vals = [row["match_bit"]] + packed_vals
            hashes.append(_jenkins_hash(vals))
            continue
        else:
            vals = [row["match_bit"]] + row["activities"]
        hashes.append(_jenkins_hash(vals))

    # --- Grouping ---
    indexed = list(enumerate(hashes))
    indexed.sort(key=lambda x: _to_unsigned64(x[1]))

    c_counts = [0] * total_n
    is_last = [0] * total_n
    current_count = 1

    sorted_hashes = [h for _, h in indexed]
    sorted_orig_idx = [i for i, _ in indexed]

    for i in range(total_n - 1):
        if sorted_hashes[i] == sorted_hashes[i + 1]:
            current_count += 1
            c_counts[i] = 0
            is_last[i] = 0
        else:
            c_counts[i] = current_count
            is_last[i] = 1
            current_count = 1
    c_counts[total_n - 1] = current_count
    is_last[total_n - 1] = 1

    # --- Filtering ---
    variants = []
    others_count = 0

    for i in range(total_n):
        if not is_last[i]:
            continue
        orig_idx = sorted_orig_idx[i]
        row = merged_data[orig_idx]
        if row["match_bit"] != 0:
            continue

        count = c_counts[i]
        if count >= threshold:
            # Decode trace
            if enable_partial_orders:
                steps = []
                current_set = []
                for k in range(full_len):
                    act_id = row["activities"][k]
                    conc_bit = row["concurrent"][k]
                    if act_id == 0:
                        continue
                    name = id_to_act.get(act_id, f"Unknown({act_id})")
                    if conc_bit == 1 and current_set:
                        current_set.append(name)
                    else:
                        if current_set:
                            steps.append(current_set)
                        current_set = [name]
                if current_set:
                    steps.append(current_set)
                variants.append({"count": count, "trace": steps})
            else:
                trace = []
                for k in range(full_len):
                    act_id = row["activities"][k]
                    if act_id == 0:
                        continue
                    name = id_to_act.get(act_id, f"Unknown({act_id})")
                    trace.append(name)
                variants.append({"count": count, "trace": [[a] for a in trace]})
        elif enable_k_anon:
            others_count += count

    return {
        "variants": variants,
        "others_count": others_count,
        "activity_map": id_to_act,
        "n_per_party": n_max,
        "partial_len": partial_len,
    }


def _format_step(step):
    if len(step) == 1:
        return step[0]
    from collections import Counter
    counts = Counter(step)
    parts = []
    for name in sorted(counts):
        if counts[name] > 1:
            parts.append(f"{name}^{counts[name]}")
        else:
            parts.append(name)
    return "[" + ", ".join(parts) + "]"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Centralized baseline for process mining")
    parser.add_argument("--log-a", required=True)
    parser.add_argument("--log-b", required=True)
    parser.add_argument("--threshold", type=int, default=1)
    parser.add_argument("--k-anon", type=int, default=0)
    parser.add_argument("--partial-orders", type=int, default=0)
    parser.add_argument("--delta", type=_parse_delta, default='0',
                        help="Concurrency time window (default: 0 = exact timestamp equality). Accepts: 0, 500ms, 10s, 1m, 2h")
    parser.add_argument("--use-handovers", action="store_true")
    parser.add_argument("--timestamp-granularity", choices=['ms', 's', 'm', 'h'], default='ms',
                        help="Timestamp rounding granularity: ms (no rounding), s (seconds), m (minutes), h (hours). Both parties must use the same value.")
    args = parser.parse_args()
    ts_granularity = _GRANULARITY_MS[args.timestamp_granularity]

    cases_a = import_xes.parse_xes(args.log_a, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)
    cases_b = import_xes.parse_xes(args.log_b, use_handovers=args.use_handovers, timestamp_granularity=ts_granularity)

    result = compute_baseline(
        cases_a, cases_b,
        threshold=args.threshold,
        enable_k_anon=args.k_anon,
        enable_partial_orders=args.partial_orders,
        delta=args.delta,
    )

    print(f"\n{'COUNT':<8} | TRACE")
    print("-" * 60)
    for v in sorted(result["variants"], key=lambda x: -x["count"]):
        trace_str = " -> ".join(_format_step(s) for s in v["trace"])
        print(f"{v['count']:<8} | {trace_str}")
    if result["others_count"]:
        print(f"{result['others_count']:<8} | <Others (Below Threshold)>")
    print("-" * 60)
    print(f"Total variants: {len(result['variants'])}")
