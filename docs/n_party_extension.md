# N-Party Extension

## Overview

The pipeline has been extended from 2-party to N-party (N >= 2) computation. The NEON framework and MP-SPDZ runtime already supported N parties natively -- all changes are in the application layer. The N=2 code path is preserved unchanged as a fast path, ensuring zero performance regression.

## Implementation

### Modified Files

| File | Change |
|------|--------|
| `mpc/process_mining.mpc` | `N_PARTIES` param via `NEON_ARG_N_PARTIES`; `if N_PARTIES==2` keeps original bitonic merge; `else` iterative merge + two-pass reconstruction |
| `pipeline/import_xes.py` | `encode_and_save(cases_list)` accepts N party lists; P0 ascending, P1..Pk-1 descending sort |
| `pipeline/run.py` | `--logs` flag (nargs='+'); backward-compatible `--log-a`/`--log-b`; passes `NEON_ARG_N_PARTIES` |
| `app.py` | Accepts `logs` list or legacy `log_a`/`log_b` in API |
| `generate_test_data.py` (new) | Splits single XES log into N parties with event-level distribution |

### MPC Program Changes (`process_mining.mpc`)

**Configuration:**
```python
N_PARTIES = 2  # default, overridden by NEON_ARG_N_PARTIES
TOTAL_N = N_PER_PARTY * N_PARTIES
FULL_LEN = PARTIAL_LEN * N_PARTIES
```

**Stage 1 (Input):** Compile-time unrolled loop over `range(N_PARTIES)`:
```python
for p in range(N_PARTIES):
    for i in range(N_PER_PARTY):
        row = p * N_PER_PARTY + i
        data[row][0] = sint64.get_input_from(p)
        data[row][1] = sint64(p)
        ...
```

**Stage 2 (PSI):** Two code paths:
- `N_PARTIES == 2`: Original bitonic merge (P0 ascending + P1 descending = bitonic sequence). Zero regression.
- `N_PARTIES >= 3`: Iterative bitonic merge. Merge P0+P1, then result+P2, then result+P3, etc. Each step forms a bitonic sequence [sorted ascending | padding | next party descending] and applies the standard merge network.

**Stage 3 (Reconstruction):** Two code paths:
- `N_PARTIES == 2`: Original pairwise matching (`diff_parties` check) + pruned bitonic merge of 2 * PARTIAL_LEN events.
- `N_PARTIES >= 3`:
  - **Pass 1 (sequential):** Group boundary detection via `MemValue` run-length tracking. At each boundary where `case_id` changes, checks `run_len == N_PARTIES`.
  - **Pass 2 (parallel):** For each candidate group, validates C(N,2) pairwise party-ID distinctness, then iteratively merges N * PARTIAL_LEN events using pruned bitonic merge.

**Stages 4-6 (Hashing, Grouping, Output):** Unchanged -- already parameterized by `FULL_LEN` and `TOTAL_N`.

### Preprocessing Changes

`encode_and_save(cases_list)` in `pipeline/import_xes.py`:
- Accepts a list of N case lists (was 2 positional args)
- Sort direction: P0 ascending, P1..Pk-1 descending (for iterative bitonic merge)
- Writes `Input-P{k}-0` for k in range(N)
- Pads all parties to max dimensions across all parties

### Test Data Generation (`generate_test_data.py`)

Splits a single XES log into N party files simulating N organizations:
- Overlap fraction of cases appear in all N parties
- Remaining cases distributed round-robin (exclusive to one party)
- For shared cases, events are distributed round-robin across parties (each org sees different events for the same case ID)

### CLI Interface

```bash
# N=2 (backward compatible, both forms work)
python3 pipeline/run.py --log-a a.xes.gz --log-b b.xes.gz
python3 pipeline/run.py --logs a.xes.gz b.xes.gz

# N=3
python3 pipeline/run.py --logs a.xes b.xes c.xes --threads 16

# Generate test data (events split across orgs)
python3 generate_test_data.py input.xes.gz --parties 3 --overlap 0.5 --output-dir data/split/
```

## Algorithm: Iterative Bitonic Merge

A bitonic sequence is inherently a 2-sequence concept (ascending then descending). It does not generalize directly to k>2 inputs. The extension uses **iterative merging**: merge parties one at a time, reusing the same bitonic merge network.

**Steps:**
1. P0 (ascending) + P1 (descending) -> bitonic merge -> sorted ascending
2. [Result ascending | padding | P2 descending] -> bitonic merge -> sorted ascending
3. Repeat for P3, P4, ..., Pk-1

**Complexity per merge step j:** O(T_j * log T_j) where T_j = (j+1) * N_PER_PARTY, padded to next power of 2.

**Alternative considered:** Full Batcher's odd-even mergesort O(n * log^2 n) -- simpler but ~3.5x more comparisons for k=3. The iterative approach was chosen for efficiency.

**Literature:** Seiferas (JPDC 2005) shows k-tonic sorting networks achieve O(n * log k * log n). The iterative approach is equivalent to a left-skewed merge tree with the same asymptotic cost but simpler implementation.

## Reconstruction: N-Way Matching

After PSI sort, rows with the same case_id form contiguous runs. A valid N-party match requires:
1. Run of exactly N_PARTIES consecutive rows
2. All N_PARTIES party IDs are distinct (C(N,2) pairwise inequality checks)
3. Case ID is not the padding sentinel (2^60)

**Pass 1 (sequential):** Detects group boundaries via `MemValue` run-length tracking. O(TOTAL_N) sequential rounds -- same pattern as the existing grouping count stage.

**Pass 2 (parallel):** For each candidate group ending at position `i`:
- Validates distinctness of party IDs in rows `i-(N-1)` through `i`
- Iteratively merges N * PARTIAL_LEN events using pruned bitonic merge (same as PSI but on small per-row arrays)
- Stores result in `merged_data[i]`

## Evaluation Results

### Test Configuration

- **Event log:** BPIC 2013 (Open Problems, Closed Problems)
- **Protocol:** Semi (binary secret sharing), `--direct` communication
- **Hardware:** Local execution (no network simulation), 16 threads

### Test 1: N=2 BPIC 2013 Open Problems (OrgA + OrgB)

Two genuinely different organizations sharing case IDs. Each org observes different events for shared cases.

| Metric | Value |
|--------|-------|
| N_PER_PARTY | 635 |
| PARTIAL_LEN | 13 |
| Unique Cases | 819 |
| Unique Activities | 3 |
| **Compile time** | **21.68s** |
| **Runtime** | **4.96s** |
| **Total** | **26.65s** |
| Communication | 442 MB |
| Matched cases | 434 |
| Result variants | 104 |

**Stage breakdown:**

| Stage | Time (s) |
|-------|----------|
| Input | 0.004 |
| PSI (bitonic merge) | 0.592 |
| Reconstruction | 0.880 |
| Hashing | 0.007 |
| Grouping Sort | 3.062 |
| Grouping Count | 0.274 |
| Output | 0.133 |

### Test 2: N=2 BPIC 2013 Closed Problems (OrgA + OrgB)

Larger dataset from same BPIC 2013 collection.

| Metric | Value |
|--------|-------|
| N_PER_PARTY | 1314 |
| PARTIAL_LEN | 18 |
| Unique Cases | 1487 |
| Unique Activities | 4 |
| **Compile time** | **51.83s** |
| **Runtime** | **11.74s** |
| **Total** | **63.57s** |
| Communication | 1196 MB |
| Matched cases | 1130 |
| Result variants | 182 |

**Stage breakdown:**

| Stage | Time (s) |
|-------|----------|
| Input | 0.012 |
| PSI (bitonic merge) | 1.276 |
| Reconstruction | 2.981 |
| Hashing | 0.013 |
| Grouping Sort | 6.476 |
| Grouping Count | 0.645 |
| Output | 0.320 |

### Test 3: N=3 BPIC 2013 Open Problems (3-org event split)

Single log (819 traces) split into 3 simulated organizations. 50% of cases shared across all 3 parties. For shared cases, events are distributed round-robin across parties (each org sees different events for the same case). After parsing, 167 cases in the 3-way intersection.

| Metric | Value |
|--------|-------|
| N_PER_PARTY | 546 |
| PARTIAL_LEN | 15 |
| N_PARTIES | 3 |
| FULL_LEN | 45 |
| Unique Cases | 819 |
| Unique Activities | 3 |
| **Runtime** | **65.41s** |
| Communication | 14,672 MB |
| Matched cases | 167 |
| Result variants | 72 |

**Stage breakdown:**

| Stage | Time (s) |
|-------|----------|
| Input | 0.005 |
| PSI (iterative merge) | 15.514 |
| Reconstruction | 29.666 |
| Hashing | 0.016 |
| Grouping Sort | 17.703 |
| Grouping Count | 0.706 |
| Output | 1.781 |

### Correctness Verification

**N=2 tests:** The BPIC 2013 OrgA/OrgB logs are the standard test inputs for this project, previously validated against centralized baselines.

**N=3 test:** MPC output matches the centralized Python baseline exactly:
- **167 matched cases** = centralized 3-way intersection (167 cases)
- **72 trace variants** = centralized variant count (72 variants)

Verified by independently computing the intersection from the split party files, merging each case's events sorted by timestamp, and comparing activity sequences.

### Bug Found and Fixed: `sbit.if_else()` Type Truncation

During testing, N=3 initially produced only 19 variants with all activities mapped to ID 1. Root cause: in the N>=3 reconstruction path, `is_match` was an `sbit` (1-bit secret type from comparison chain `is_end & all_distinct`). Calling `sbit.if_else(sint64_value, sint64(0))` in MP-SPDZ performs a **1-bit mux** -- it only preserves the LSB of the 64-bit activity value, silently truncating all activities to 0 or 1.

**Fix:** The `is_match` value (sint64, 0 or 1) is negated to create a 64-bit bitmask: `mask = sint64(0) - is_match`, producing 0x0 or 0xFFFF...FFFF. Activities are masked with bitwise AND (`final_act & mask`), costing 0 AND gates (XOR-only in binary circuits). An earlier attempt using `sint64 * sint64` multiplication was 64x more expensive (O(64^2) AND gates per value). Converting to `sbit` for `sbit.if_else` was not possible due to a deadlock in MP-SPDZ's 3-party Semi protocol when performing `sint64 != sint64` comparisons inside `@for_range_opt_multithread`. This does not affect the N=2 path which uses a separate code branch.

### Test 4: N=4 BPIC 2013 Open Problems (4-org event split)

Single log (819 traces) split into 4 simulated organizations. 50% of cases shared across all 4 parties.

| Metric | Value |
|--------|-------|
| N_PER_PARTY | 512 |
| PARTIAL_LEN | 15 |
| N_PARTIES | 4 |
| FULL_LEN | 60 |
| **Compile time** | **93.38s** |
| **Runtime** | **187.86s** |
| **Total** | **281.23s** |
| Communication | 34,619 MB |
| Matched cases | 109 |
| Result variants | 63 |

**Stage breakdown:**

| Stage | Time (s) |
|-------|----------|
| Input | 0.011 |
| PSI (iterative merge) | 31.599 |
| Reconstruction | 105.273 |
| Hashing | 0.033 |
| Grouping Sort | 44.242 |
| Grouping Count | 1.676 |
| Output | 5.010 |

### Test 5: N=5 BPIC 2013 Open Problems (5-org event split)

Single log (819 traces) split into 5 simulated organizations. 50% of cases shared across all 5 parties.

| Metric | Value |
|--------|-------|
| N_PER_PARTY | 491 |
| PARTIAL_LEN | 15 |
| N_PARTIES | 5 |
| FULL_LEN | 75 |
| **Compile time** | **149.52s** |
| **Runtime** | **1,095.93s** |
| **Total** | **1,245.45s** |
| Communication | 110,424 MB |
| Matched cases | 65 |
| Result variants | 51 |

**Stage breakdown:**

| Stage | Time (s) |
|-------|----------|
| Input | 0.007 |
| PSI (iterative merge) | 204.103 |
| Reconstruction | 446.920 |
| Hashing | 0.062 |
| Grouping Sort | 417.415 |
| Grouping Count | 2.549 |
| Output | 24.847 |

### Performance Scaling: N=2 through N=5

All tests use BPIC 2013 Open Problems split data, Semi protocol, `--direct`, 8 threads, local execution on AMD Ryzen 7 5800H (8 cores / 16 threads).

| | N=2 | N=3 | N=4 | N=5 |
|--|-----|-----|-----|-----|
| N_PER_PARTY | 546 | 546 | 512 | 491 |
| PARTIAL_LEN | 15 | 15 | 15 | 15 |
| TOTAL_N | 1,092 | 1,638 | 2,048 | 2,455 |
| FULL_LEN | 30 | 45 | 60 | 75 |
| **Runtime (s)** | **5.15** | **36.06** | **187.86** | **1,095.93** |
| PSI (s) | 0.594 | 7.63 | 31.60 | 204.10 |
| Reconstruction (s) | 1.145 | 18.85 | 105.27 | 446.92 |
| Grouping Sort (s) | 3.015 | 8.62 | 44.24 | 417.41 |
| Communication (MB) | — | 9,252 | 34,619 | 110,424 |
| Matched cases | — | 167 | 109 | 65 |
| Variants | — | 72 | 63 | 51 |

**Scaling ratios (relative to N=2):**

| Stage | N=3/N=2 | N=4/N=2 | N=5/N=2 |
|-------|---------|---------|---------|
| PSI | 12.8x | 53.2x | 343.6x |
| Reconstruction | 16.5x | 91.9x | 390.3x |
| Grouping Sort | 2.9x | 14.7x | 138.5x |
| **Total runtime** | **7.0x** | **36.5x** | **212.8x** |

**Analysis:**

1. **Reconstruction dominates for all N≥3** (52-56% of total). The per-row inner merge grows superlinearly: each additional party adds another iterative merge pass on increasingly larger arrays (FULL_LEN = N × PARTIAL_LEN). For N=5: 4 merge passes per row on arrays up to 128 elements (next power of 2 of 75).

2. **Grouping Sort becomes the bottleneck for N=5** (38% of total). TOTAL_N=2,455 padded to 4,096; Batcher sort is O(n log²n) and operates on wider FULL_LEN arrays in the output stage.

3. **PSI scales moderately** because the iterative merge adds one pass per additional party, each on ~2048-element arrays. N=5 requires 4 merge passes vs 1 for N=2.

4. **Per-gate protocol cost** scales with N-1 OT multipliers per party (Semi protocol). For N=5: 4x more communication per AND gate than N=2.

5. **Communication growth** is superlinear: N=5 uses 110 GB, driven by larger TOTAL_N, wider rows (FULL_LEN=75), and N-1=4 OT pairs per party.

### Protocol Comparison (BPIC 2013 Open split, 8 threads, local)

#### N=3

| Protocol | Security model | Trust assumption | Runtime (s) | vs Semi |
|----------|---------------|-----------------|-------------|---------|
| Semi | Semi-honest | Dishonest majority (any N-1 corrupt) | 36.06 | 1.0x |
| CCD | Semi-honest | Honest majority (t < N/2) | 24.84 | **1.45x faster** |
| ReplicatedBin | Semi-honest | Honest majority (t < N/2) | 16.92 | **2.1x faster** |

**Stage breakdown (N=3):**

| Stage | Semi (s) | CCD (s) | ReplicatedBin (s) |
|-------|----------|---------|-------------------|
| PSI | 7.63 | 3.31 | — |
| Reconstruction | 18.85 | 5.95 | — |
| Grouping Sort | 8.62 | 13.80 | — |
| Grouping Count | 0.71 | 0.86 | — |
| Output | 1.78 | 0.86 | — |

Note: ReplicatedBin stage breakdown not captured (output parsing error in NEON handler for replicated-bin protocol).

#### N=4

| Protocol | Security model | Trust assumption | Runtime (s) | vs Semi |
|----------|---------------|-----------------|-------------|---------|
| Semi | Semi-honest | Dishonest majority | 187.86 | 1.0x |
| CCD | Semi-honest | Honest majority (t < N/2) | 80.56 | **2.33x faster** |

**Stage breakdown (N=4):**

| Stage | Semi (s) | CCD (s) | Speedup |
|-------|----------|---------|---------|
| PSI | 31.60 | 11.55 | 2.7x |
| Reconstruction | 105.27 | 43.65 | 2.4x |
| Grouping Sort | 44.24 | 20.34 | 2.2x |
| Grouping Count | 1.68 | 2.38 | 0.7x |
| Output | 5.01 | 2.44 | 2.1x |

**Protocol mechanics:**

- **Semi** (`semi-bin-party.x`): OT-based preprocessing. Each AND gate requires OT between every pair of parties → O(N²) communication per gate. Works for any N≥2, no trust assumption.
- **CCD** (`ccd-party.x`): Cramer-Damgård-Escudero scheme. Secret sharing over GF(2) with degree reduction for AND gates → O(N) communication per gate. Requires honest majority (t < N/2). Works for any N≥3.
- **ReplicatedBin** (`replicated-bin-party.x`): Each party holds 2 of 3 shares; AND gates computed locally + single reshare round (~1 byte/gate). Requires honest majority. **Exactly 3 parties only.**

CCD provides uniform ~2.3x speedup across all stages at N=4 (the only honest-majority binary protocol available for N≥4 in MP-SPDZ). At N=3, ReplicatedBin (2.1x) outperforms CCD (1.45x) due to its more efficient reshare mechanism.

**Malicious protocols (MaliciousRepBin, PSRepBin):** Tested but failed with MAC hash mismatch (`mac_fail: check hash mismatch`), both with and without `--direct`. Likely a compatibility issue between malicious binary protocols and the `sbitint`-heavy program structure in MP-SPDZ 0.4.2.

### Optimizations Applied

Two optimizations were applied to the MPC program (see `docs/mpc_optimizations.md` for details):

1. **cond_swap pattern** in PSI merge and reconstruction inner merge: replaced `2×if_else` (128 AND/column) with shared-XOR `cond_swap` (64 AND/column). Saves 32-34% of AND gates.

2. **Subtraction fix** in N≥3 reconstruction: replaced `sint64(1) - x` with `x ^ sint64(1)` (0 AND) and `sint64(0) - x` with `~x + sint64(1)` (189 AND vs 256 AND).

### Thread Scaling (N=3, BPIC 2013 Open split)

| Threads | Runtime (s) | Speedup vs 1 |
|---------|-------------|-------------|
| 1 | 70.63 | 1.00x |
| 2 | 46.69 | 1.51x |
| 4 | 43.72 | 1.62x |
| **8** | **42.81** | **1.65x** |
| 16 | 47.03 | 1.50x |
| 32 | 110.47 | 0.64x |

Optimal at 8 threads (= physical core count). Beyond 8, CPU contention from N×~118 OS threads per party (computation + OT preprocessing) degrades performance. 32 threads is catastrophic for N=3 (3×~200 = 600 OS threads on 16 HW threads).

## Known Limitations

1. **Reconstruction dominates N≥3 runtime:** The per-row iterative merge on FULL_LEN=N*PARTIAL_LEN elements is the bottleneck. The inner merge runs for ALL rows unconditionally (obliviousness requirement), even though only a fraction are actual matches.

2. **FULL_LEN growth:** `FULL_LEN = N_PARTIES * PARTIAL_LEN`. For N=5, P=15: FULL_LEN=75, requiring inner merge networks on 128-element padded arrays with 4 iterative merge passes per row.

3. **N=5 is impractical for larger datasets:** 1,096s runtime and 110 GB communication for just 491 cases per party. The combination of superlinear gate growth, per-gate protocol overhead (4 OT pairs), and local CPU contention makes N≥5 a stress test rather than a practical configuration.

4. **Only Semi protocol for N≥4:** ReplicatedBin (3-party RSS) provides 2x speedup for N=3 but doesn't extend to N≥4. No efficient binary protocol exists for N≥4 with honest majority in MP-SPDZ.

5. **`sbitint.if_else` per-bit mux:** In MP-SPDZ, `sbitint.if_else(a, b)` performs a per-bit mux using each bit of the condition independently — NOT a uniform mux controlled by a single bit. A condition with value 1 (only LSB set) will select bit 0 from `a` but bits 1-63 from `b`. To mask a full sint64 value with a 0/1 condition, use negation (`~condition + sint64(1)`) to broadcast the LSB to all 64 bits, then bitwise AND.

## References

- Seiferas, "Networks for sorting multitonic sequences" (JPDC 2005) -- k-tonic sorting networks
- Miltersen, Paterson, Tarui, "The Asymptotic Complexity of Merging Networks" (JACM 1996) -- Batcher merge optimality proof
- Bogdanov, Laur, Willemson, "Practical Analysis of Oblivious Sorting for Secure MPC" (NordSec 2014)
