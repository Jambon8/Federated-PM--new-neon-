# Partial Orders in Privacy-Preserving Process Mining

## Problem

Event logs record activities with timestamps. When two or more events within the same case occur at approximately the same time (e.g., lab tests ordered simultaneously), a total-order sort produces an **arbitrary** ordering. This causes two problems:

1. **Non-determinism**: The same logical trace can produce different activity sequences depending on the party assignment and merge layout, leading to different hashes and inflated variant counts.
2. **Loss of information**: Concurrent events are forced into a sequential order, hiding the true process structure.

The concurrency window is configurable via a **delta (Δ)** parameter. Events within Δ of their predecessor in the sorted sequence are grouped as concurrent. `Δ=0` (default) detects exact timestamp equality using an equality check (EQZ circuit, cheapest). Positive values use a less-than comparison (LTZ circuit): `Δ=10s` groups events within 10 seconds, `Δ=1m` within one minute. Accepted formats: `0`, `500ms`, `10s`, `1m`, `2h`.

### Example

A hospital case has three lab tests ordered at the same time:

```
10:00  CRP
10:00  Leucocytes
10:00  LacticAcid
```

Without partial orders, these get an arbitrary sequential order like `CRP -> Leucocytes -> LacticAcid` or `Leucocytes -> CRP -> LacticAcid`. Two cases with the same labs may hash differently.

With partial orders, they form a **concurrent multiset**: `[CRP, LacticAcid, Leucocytes]`, and all permutations are recognized as the same variant. Multiset notation `[...]` is used because duplicate activities can appear (e.g., `[Lab^3, X-ray]` for three lab tests and one X-ray at the same time).

### Real-World Impact (Sepsis Cases Event Log)

Evaluated on the full Sepsis Cases event log (N=1044, PARTIAL_LEN=103, 1037 matched cases). Without PO, lab test permutations inflate variant counts:

**Standard mode — top variants:**
```
31x  ... ER Sepsis Triage -> CRP -> Leucocytes
16x  ... ER Sepsis Triage -> CRP -> LacticAcid -> Leucocytes -> IV Liquid -> IV Antibiotics
14x  ... ER Sepsis Triage -> CRP -> Leucocytes -> LacticAcid -> IV Liquid -> IV Antibiotics
12x  ... ER Sepsis Triage -> Leucocytes -> CRP
 6x  ... ER Sepsis Triage -> CRP -> Leucocytes -> LacticAcid
 6x  ... ER Sepsis Triage -> CRP -> LacticAcid -> Leucocytes
 4x  ... ER Sepsis Triage -> LacticAcid -> Leucocytes -> CRP
 4x  ... ER Sepsis Triage -> Leucocytes -> CRP -> LacticAcid -> IV Liquid -> IV Antibiotics
     ... (831 variants total, most with count 1)
```

The 31x and 12x traces are the same process — triage, then two concurrent lab tests — differing only in the arbitrary order of CRP and Leucocytes. Similarly, the 16x and 14x traces are the same process with three concurrent lab tests in different order.

**Partial orders mode — how traces merge:**

```
31x  ... ER Sepsis Triage -> CRP -> Leucocytes               ─┐
12x  ... ER Sepsis Triage -> Leucocytes -> CRP                ─┤ merged
─────────────────────────────────────────────────────────────────┘
43x  ... ER Sepsis Triage -> [CRP, Leucocytes]

16x  ... ER Sepsis Triage -> CRP -> LacticAcid -> Leucocytes -> IV ...  ─┐
14x  ... ER Sepsis Triage -> CRP -> Leucocytes -> LacticAcid -> IV ...  ─┤
 4x  ... ER Sepsis Triage -> Leucocytes -> CRP -> LacticAcid -> IV ...  ─┤ merged
 + more permutations                                                     ─┤
──────────────────────────────────────────────────────────────────────────┘
41x  ... ER Sepsis Triage -> [CRP, LacticAcid, Leucocytes] -> IV Liquid -> IV Antibiotics

 6x  ... ER Sepsis Triage -> CRP -> Leucocytes -> LacticAcid   ─┐
 6x  ... ER Sepsis Triage -> CRP -> LacticAcid -> Leucocytes   ─┤
 4x  ... ER Sepsis Triage -> LacticAcid -> Leucocytes -> CRP   ─┤ merged
 + more permutations                                            ─┤
─────────────────────────────────────────────────────────────────┘
20x  ... ER Sepsis Triage -> [CRP, LacticAcid, Leucocytes]
```

Overall: **831 → 692 variants (-16.7%)**, with 685 out of 692 PO variants containing concurrent sets. All 1037 cases are preserved.

## Solution Overview

Partial orders are implemented across preprocessing and three MPC pipeline stages:

0. **Preprocessing** (`import_xes.py`): Events are sorted by `(timestamp, activity_name)` instead of just `timestamp`. This ensures same-timestamp events are in a canonical (alphabetical) order within each party's data, which the bitonic merge preserves.
1. **Reconstruction** (Step 3): Composite sort keys `(timestamp << 17 | activity_id)` merge the two parties' events into a single canonically ordered sequence. Concurrent markers detect adjacent events within the delta window and pack them into activity values.
2. **Hashing** (Step 4): Standard sequential Jenkins hash on packed values `(activity_id | conc_bit << 32)`. Since the preprocessing + composite sort already canonicalize order within concurrent groups, a simple sequential hash is order-independent in practice. The `conc_bit` distinguishes sequential from concurrent structure. **Zero AND gates — same cost as non-PO hashing.**
3. **Output** (Step 6): Packed values are unpacked after reveal; two-column format (`activity_id concurrent_bit`) encodes the concurrent structure.

The feature is **optional** — controlled by `--partial-orders 1 --delta <value>` on the CLI or a checkbox + slider in the web UI. Default delta is `0` (exact timestamp equality via EQZ). Accepted formats: `0`, `500ms`, `10s`, `1m`, `2h`.

## Implementation Details

### 1. Composite Sort Key (Reconstruction)

In standard mode, the reconstruction merges two partial traces by sorting on raw timestamps. When timestamps tie, the result is arbitrary.

In PO mode, the sort key becomes a composite that packs both timestamp and activity ID into a single 64-bit value:

```
sort_key = (timestamp << ACT_BITS) | activity_id
```

The left shift moves the timestamp into the high bits (primary sort key), and the activity ID occupies the low bits (tiebreaker). This is a standard technique for multi-field sorting with a single comparison.

`ACT_BITS = 17` because 2^17 = 131,072 — sufficient for over one hundred thousand unique activity types. Real event logs typically have tens to hundreds of distinct activities. The value 17 was chosen to keep the composite key safely below the padding sentinel: timestamps are stored in milliseconds (~2^41 for Unix ms through year 2100), so the composite is at most `2^41 << 17 = 2^58`, safely below `2^60` (padding).

Timestamps are stored in **milliseconds** (`int(dt.timestamp() * 1000)` in the import scripts). This captures sub-second events and enables delta values below 1 second. The `--delta` parameter accepts `0` (exact equality) or a human-readable duration (`500ms`, `10s`, `1m`, `2h`), parsed to milliseconds before passing to MPC. `0` is the default and the cheapest option.

This ensures:
- Events with different timestamps sort by timestamp (unchanged behavior).
- Events with the same timestamp sort deterministically by activity ID.

**Overflow guard**: The padding sentinel `PAD_TIME = 2^60` would overflow when shifted (`2^60 << 17 = 2^77` exceeds 64-bit `sbitint`). The code guards against this:

```python
is_real = (ts != sint64(MAX_TIME))
composite = (ts << ACT_BITS) | act
sort_keys[k] = is_real.if_else(composite, sint64(MAX_TIME))
```

Padding events keep `MAX_TIME` as their sort key, staying at the end of the sorted array.

### 1b. Timestamp Granularity Alignment

In the current centralized demo, both parties process their data through the same import script, so timestamps are always in the same unit. In a real federated deployment, each party runs the import script independently, which introduces a risk: Party A might have second-precision XES logs and Party B might have millisecond-precision logs. After the `* 1000` conversion both parties produce millisecond integers, but Party A's timestamps are all multiples of 1000 (e.g., `1234567890000`) while Party B's timestamps have sub-second precision (e.g., `1234567890423`). The MPC comparison is numerically correct, but the semantic interpretation of delta changes: a delta of 1 second (1000ms) might capture some cross-party event pairs that appear concurrent only because one party's clock has coarser resolution.

To address this, a `--timestamp-granularity` parameter rounds all timestamps to the nearest multiple of the specified unit before writing input files:

```python
timestamp = (timestamp // granularity_ms) * granularity_ms
```

Accepted values: `ms` (no rounding, default), `s` (round to seconds), `m` (round to minutes), `h` (round to hours). Both parties must agree on the same value before running the protocol — this is a public, pre-agreed parameter.

| Scenario | Recommendation |
|----------|---------------|
| Both parties have ms-precision logs | `--timestamp-granularity ms` (default, no rounding) |
| Party A has second precision, Party B has ms | `--timestamp-granularity s` (round both to seconds) |
| Both have second-precision logs | `--timestamp-granularity s` (explicit, avoids spurious sub-second delta matches) |

Granularity also interacts with delta: `--delta 1s` has no additional effect if events only ever differ by exact multiples of 1000ms anyway. In that case `--timestamp-granularity s` makes the effective unit explicit.

### 2. Concurrent Markers (Delta-Based, Packed Encoding)

After sorting, adjacent events within the configurable delta window are detected as concurrent. The concurrent bit is packed directly into the activity value:

```python
packed_value = activity_id | (conc_bit << 32)
```

Activity IDs use at most 17 bits (`ACT_BITS = 17`), so bit 32 is free. This keeps `merged_data` at the same width (`2 + FULL_LEN`) regardless of PO mode — no matrix width doubling.

The concurrent detection logic uses **chaining**: if the time difference between adjacent events is less than Δ, they belong to the same concurrent group. This naturally chains — events A, B, C all end up in one group if the chain A→B and B→C are both within Δ:

```python
for k in range(FULL_LEN):
    if k == 0:
        # First position: never concurrent, store activity only
        merged_data[i][2 + k] = final_act
    else:
        diff = ts[k] - ts[k-1]
        is_close = (ts == prev_ts) if DELTA == 0 else (diff < sint64(DELTA))  # 0: EQZ; >0: LTZ sliding window
        both_valid = (ts[k] != MAX_TIME) & (ts[k-1] != MAX_TIME)
        is_conc = is_close & both_valid
        packed = final_act | (is_conc << 32)
        merged_data[i][2 + k] = packed
```

A `conc_bit = 1` means "this activity is in the same concurrent set as the previous position." When `DELTA=0` (default, `--delta 0`), the MPC uses `ts == prev_ts` directly — an EQZ (XOR-tree) circuit that is cheaper than the LTZ (parallel prefix adder) used for positive deltas. For positive deltas, `diff < DELTA` captures a sliding window; `--delta 1ms` (DELTA=1) is semantically equivalent to `--delta 0` on integer ms timestamps but pays the full LTZ cost. However, the subtraction-based less-than is slightly more expensive in binary secret sharing than direct equality (a parallel prefix adder uses more AND gates than an XOR-reduction tree), so PO=1 with Δ=1 uses ~150% more data than PO=0, compared to the ~53% overhead of the original equality-based implementation.

Example encoding of `A -> [B, C, D] -> E`:

| Position | Stored Value | Activity | conc_bit | Meaning |
|----------|-------------|----------|----------|---------|
| 0 | A | A | 0 | Sequential |
| 1 | B | B | 0 | Start of new group |
| 2 | C \| (1 << 32) | C | 1 | Within Δ of B |
| 3 | D \| (1 << 32) | D | 1 | Within Δ of C (chains to B) |
| 4 | E | E | 0 | Sequential |

### 3. Hashing (Zero Overhead)

The hash must treat `{B, C, D}` and `{D, B, C}` as identical. A naive approach would use an order-independent hash function (e.g., MSet-XOR-Hash [1]), but any such function requires `if_else` operations to detect group boundaries — each `if_else` costs 64 AND gates and requires network communication between parties.

Instead, we exploit the fact that **the preprocessing sort + composite sort key already canonicalize the order within concurrent groups**. Events are sorted by `(timestamp, activity_name)` in `import_xes.py`, which maps to ascending activity IDs (since IDs are assigned alphabetically). The composite key `(ts << 17) | act_id` preserves this ordering through the bitonic merge. So `[CRP, LacticAcid, Leucocytes]` always appears as `CRP, LacticAcid, Leucocytes` (IDs 3, 9, 10) — never in any other order.

This means a **standard sequential Jenkins hash** [2] on the packed values `(activity_id | conc_bit << 32)` is sufficient:

```python
for k in range(1 + FULL_LEN):
    val = merged_data[i][1 + k]   # packed value (activity | conc_bit << 32)
    h = h ^ val
    h = h ^ (h << 10)
    h = h ^ (h >> 6)
```

The `conc_bit` embedded in bit 32 ensures `A -> B` (sequential) hashes differently from `{A, B}` (concurrent), while the canonical ordering ensures the same concurrent set always produces the same hash.

**Gate cost: 0 AND gates** — identical to non-PO hashing. All operations (XOR, shift) are local in binary secret sharing and require no communication between parties.

### 4. Output Format

Standard mode outputs one value per line:

```
RAW_RESULT Count:46 Trace:
3
10
0
END_TRACE
```

PO mode stores packed values in a single flat array (same width as standard). After a single batch reveal, packed public values are unpacked:

```python
act_val = packed & ((1 << 32) - 1)   # public integer op, free
conc_val = packed >> 32                # public integer op, free
print_ln("%s %s", act_val, conc_val)
```

Output format:

```
RAW_RESULT Count:46 Trace:
3 0
10 1
0 0
END_TRACE
```

The decoders (`decode_output.py`, `api_helper.py`) parse this and render concurrent multisets with `[...]` notation, collapsing duplicates with `^N`:

```
46       | CRP -> [LacticAcid, Leucocytes]
```

If duplicate activities occur in the same concurrent group (e.g., three lab tests of the same type), they display as `[Lab^3, X-ray]`.

## Performance

### Stage Breakdown (Full Sepsis Cases, N=1044, PARTIAL_LEN=103)

| Stage | PO=0 | PO=1 Δ=0 (EQZ) | PO=1 Δ=10s (LTZ) |
|-------|------|-----------------|------------------|
| INPUTS | 0.05s | 0.06s | 0.06s |
| PSI | 2.5s | 2.6s | 2.5s |
| **RECON** | **22.1s** | **39.9s (+81%)** | **68.3s (+209%)** |
| HASH | 0.06s | 0.06s (0%) | 0.07s (0%) |
| GROUP | 8.0s | 8.2s | 8.0s |
| OUTPUT | 2.0s | 1.8s | 1.9s |
| **Runtime** | **36.1s** | **53.7s (+49%)** | **81.9s (+127%)** |
| **Compile** | **276s** | **323s (+17%)** | **418s (+51%)** |
| **Data sent** | **5,268 MB** | **8,056 MB (+53%)** | **13,154 MB (+150%)** |

### Key Design Decision: Zero-Cost Hashing

The hash stage has **zero PO overhead** — ~0.06s in all modes, even at full scale. This is achieved by canonicalizing the order of concurrent events *before* hashing, rather than using an order-independent hash function inside MPC.

In binary secret sharing, XOR and shift operations are **free** (local computation, no communication between parties). But any conditional operation (`if_else`, equality comparison) requires AND gates, each costing 64 bits of network communication. An order-independent hash (e.g., MSet-XOR-Hash [1]) would need `if_else` at every position to detect group boundaries — ~320 AND gates per position. In earlier implementations, this caused the hash stage alone to take 111s on this dataset (55% of total runtime).

Instead, the preprocessing step in `import_xes.py` sorts events by `(timestamp, activity_name)`, which guarantees that same-timestamp events appear in ascending activity ID order within each party's data. The composite sort key `(ts << 20) | act_id` preserves this canonical ordering through the bitonic merge. As a result, a standard sequential Jenkins hash on packed values `(activity | conc_bit << 32)` is both order-independent and completely free.

### Where the Overhead Comes From

PO overhead is concentrated entirely in **reconstruction**. Three satellite arrays (keys, timestamps, activities) are carried through the bitonic merge instead of two (keys, activities), and each position requires additional secret operations for the composite key and concurrent marker. The delta mode determines the dominant cost:

**Δ=0 (EQZ)**: uses `ts == prev_ts` — an XOR-reduction tree. +81% RECON, +53% data sent overall.

**Δ>0 (LTZ)**: uses `diff = ts - prev_ts; diff < DELTA` — a full parallel prefix adder circuit for the subtraction, requiring O(n) AND gates with O(log n) rounds. This is the dominant cost and explains the large gap: Δ=10s uses +209% RECON vs PO=0, and +150% data sent — nearly 3× the overhead of Δ=0.

When `--partial-orders 0`, the compile-time `if ENABLE_PARTIAL_ORDERS:` guards ensure none of this code is compiled — zero overhead.

## Correctness Verification

The implementation is verified against a centralized Python baseline (`eval/baseline.py`) that replicates the exact same logic:

| Dataset | Mode | Baseline | MPC | Match |
|---------|------|----------|-----|-------|
| Sepsis Cases (full) | PO=0 | 831 | 831 | 831/831 |
| Sepsis Cases (full) | PO=1, Δ=0 | 692 | 692 | 692/692 |
| BPI Challenge 2013 | PO=0 | 104 | 104 | 104/104 |
| BPI Challenge 2013 | PO=1, Δ=0 | 104 | 104 | 104/104 |

### Sliding Window Evaluation (Sepsis 100)

Evaluated on Sepsis Cases 100-trace subset to test the effect of different delta values:

**Sepsis 100 (correctness):**

| Mode | Δ | Baseline | MPC | Match |
|------|---|----------|-----|-------|
| PO=0 | — | 94 | 94 | ✓ |
| PO=1 | 0 (default) | 85 | 85 | ✓ |
| PO=1 | 1m | 95 | 95 | ✓ |

**Full Sepsis (N=1044, variants + timing):**

| Δ | Baseline | MPC | Match | Runtime |
|---|----------|-----|-------|---------|
| 0 | 692 | 692 | ✓ | 76s |
| 10s | 726 | 726 | ✓ | 81s |
| 1m | 815 | 815 | ✓ | 82s |

Runtime is constant across all delta values — DELTA is a compile-time constant substitution; the circuit structure (comparisons, AND gates) is identical regardless of the delta value. Compile time (~354s) is likewise unaffected by delta; each delta value produces a different program hash requiring a fresh compilation, but compile time does not grow with the delta value.

**Effect of larger delta on variant count**: delta has two competing effects:

1. *Merging*: cases where the same activities appear in different orders within the window both become the same concurrent set — same hash, fewer variants.
2. *Splitting*: cases that were the same sequential variant under Δ=0 can diverge if their inter-event gaps differ. If one case has events A and B within 10s and another has A and B more than 10s apart, they are the same variant under Δ=1s (both `A → B`) but different under Δ=10s (first becomes `{A, B}`, second stays `A → B`).

For Sepsis, most same-timestamp ties are already resolved by Δ=0 (831→692). Expanding the window then mostly splits sequential variants whose inter-event gaps straddle the threshold. Verified on the full dataset: 34 out of 692 Δ=0 variants split at Δ=10s, with the split dominated by a single large variant:

```
Δ=1s  (41 cases):  ... -> ER Sepsis Triage -> [CRP, LacticAcid, Leucocytes] -> IV Liquid -> IV Antibiotics

Δ=10s (29 cases):  ... -> ER Sepsis Triage -> [CRP, LacticAcid, Leucocytes] -> [IV Antibiotics, IV Liquid]
      (12 cases):  ... -> ER Sepsis Triage -> [CRP, LacticAcid, Leucocytes] -> IV Liquid -> IV Antibiotics
```

In 29 cases, IV Liquid and IV Antibiotics are administered within 10 seconds of each other and become concurrent. In the remaining 12 cases they are more than 10 seconds apart and stay sequential. Both groups are identical under Δ=0 (different timestamps, same sequential order), but Δ=10s reveals a real process difference. Whether this is desirable depends on whether a 10-second gap is clinically meaningful. Δ=0 is the conservative default.

### Variant Reduction

| Dataset | Cases | Standard | Partial Orders (Δ=0) | Reduction |
|---------|-------|----------|----------------------|-----------|
| Sepsis Cases (full) | 1,037 | 831 | 692 | -16.7% |
| BPI Challenge 2013 | 434 | 104 | 104 | 0% |

Sepsis Cases shows a 16.7% reduction because lab tests (CRP, Leucocytes, LacticAcid) are frequently ordered simultaneously — all permutations of the same concurrent set collapse into one variant. BPI 2013 has no timestamp ties in matched cases, so PO has no effect on variant counts.

## Files Modified

| File | Change |
|------|--------|
| `import_xes.py` | Pre-sort events by `(timestamp, activity_name)` for canonical ordering |
| `Programs/process_mining.mpc` | Composite sort key, delta-based concurrent markers, sequential hash on packed values, unified output |
| `decode_output.py` | Two-column parsing, `[A^N, B]` multiset rendering, diagnostic line filtering |
| `api_helper.py` | Two-column parsing, structured trace output for web UI |
| `examples/run_process_mining.py` | `--partial-orders`, `--delta` (duration string), `--timestamp-granularity` flags, NEON substitutions |
| `app.py` | Checkbox + delta slider in web UI, pass-through to CLI |
| `templates/index.html` | JS parser for concurrent sets, multiset rendering with `^N`, delta slider UI |
| `eval/baseline.py` | Matching Python implementation with delta (ms) and timestamp-granularity parameters for correctness testing |

## References

[1] D. Clarke, S. Devadas, M. van Dijk, B. Gassend, and G. E. Suh, "Incremental Multiset Hash Functions and Their Application to Memory Integrity Checking," *ASIACRYPT 2003*, LNCS 2894, pp. 188–207. Introduces MSet-XOR-Hash: the hash of a multiset as the XOR of individually-hashed elements.

[2] B. Jenkins, "A Hash Function for Hash Table Lookup," *Dr. Dobb's Journal*, 1997. The one-at-a-time hash used for the sequential chain between concurrent groups.
