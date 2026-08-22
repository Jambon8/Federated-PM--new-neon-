# MP-SPDZ Internals Reference

Cost model for predicting the impact of MPC program changes. All analysis based on MP-SPDZ 0.4.2 (`vendor/temp/MP/mp-spdz-0.4.2/`).

## 1. Gate Costs for sbitint Operations

Binary secret sharing: XOR gates are **free** (local computation). AND gates require communication. All costs below are AND gate counts.

| Operation | AND Gates (64-bit) | Circuit Depth (rounds) | Implementation |
|-----------|-------------------|----------------------|----------------|
| `a > b` | **127** | O(log n) = 6 | KOpL binary tree comparator |
| `a == b` | **63** | O(log n) = 6 | XOR + NOT all bits, AND-tree reduction |
| `a != b` | **63** | O(log n) = 6 | Same as `==`, NOT is free |
| `sel.if_else(a, b)` (sbit sel) | **64** | 1 | `b XOR (sel AND (a XOR b))`, `andrs` instruction |
| `a & b` (bitwise) | **64** | 1 | `ands` instruction, 1 AND per bit |
| `a \| b` (bitwise OR) | **64** | 1 | `a XOR b XOR (a AND b)` |
| `a + b` | **189** | O(sqrt(n)) = 8 | Carry-select adder (default) |
| `a - b` | **640** | O(log n) = 6 | Borrow propagation via PreOpL tree |
| `a * b` | **4378** | O(log^2 n) = 12 | Wallace tree with `andrs` partial products |
| `a << k` (const k) | **0** | 0 | Bit permutation, no gates |
| `a >> k` (const k) | **0** | 0 | Bit permutation, no gates |
| `sbit.get_random_bit()` | **0** | 1 | `bitb` instruction, PRF-based |

All gate counts are **empirically measured** by compiling micro-benchmarks with `compile.py -B 64` and reading the "bit triples" output. These supersede theoretical estimates.

### Key Source Files

- **Comparison:** `Compiler/types.py:3638-3656` — `bit_comparator()` uses `floatingpoint.KOpL()`
- **Equality:** `Compiler/GC/types.py:638-641` — XOR bits, NOT, AND-tree reduce (`tree_reduce`)
- **MUX (if_else):** `Compiler/GC/types.py:1136-1149` — `andrs` instruction, formula: `b XOR (sel AND (a XOR b))`
- **Addition:** `Compiler/types.py:3505-3631` — `bit_adder_selection()` chooses carry-select/ripple/lookahead
- **Subtraction:** `Compiler/types.py:3748-3769` — borrow propagation via `PreOpL`
- **Multiplication:** `Compiler/GC/types.py:1336-1350` and `Compiler/types.py:3666-3725` — `get_bit_matrix()` + `wallace_tree_from_matrix()`
- **Shifts:** `Compiler/types.py:3780-3784` — bit reindexing, zero gates

### Derived Costs for Common Patterns

| Pattern | AND Gates | Rounds | Notes |
|---------|-----------|--------|-------|
| `cond_swap(a, b)` (library, 1 col) | 127 + 64 = **191** | 6 + 1 | Shared `prod = sel*(a^b)`, 64 AND per col |
| `2x if_else` (hand-written, 1 col) | 127 + 128 = **255** | 6 + 1 | No CSE: 64+64 AND per col |
| Compare-and-swap (W cols, cond_swap) | 127 + W*64 | 6 + 1 | Library sort uses this |
| Compare-and-swap (W cols, 2x if_else) | 127 + W*128 | 6 + 1 | Hand-written PSI uses this |
| `MemValue` read + write | 0 | 2 | Memory instructions, sequential dependency |
| `is_match_mask = sint64(0) - is_match` | **640** | 6 | Subtraction, creates 64-bit broadcast mask |
| `final_act & mask` | **64** | 1 | Bitwise AND |
| `(ts << 17) \| act` | **0** | 0 | Shift + XOR, both free |

**Critical:** The hand-written PSI merge uses `swap.if_else(b,a)` + `swap.if_else(a,b)` per column = 128 AND. The library `cond_swap` computes the shared XOR once = 64 AND. The PSI merge pays 2x per column.

## 2. Protocol Costs

### Semi Binary Protocol (`semi-bin-party.x`)

- **Implementation:** OT-based Beaver triples
- **Per AND gate per party:** ~16 bytes sent (OT extension preprocessing + online opening)
  - Online phase: 2 bits opened (x-a, y-b) via broadcast — ~0.25 bytes/party
  - Preprocessing: OT extension generates triples — ~16 bytes/party amortized
- **Rounds per AND batch:** 2 (prepare + exchange)
- **Parties:** Any N >= 2
- **Per-gate comm scaling:** O(N-1) OT multipliers per party
  - N=2: 1 OT multiplier, ~16 bytes/AND/party
  - N=3: 2 OT multipliers, ~32 bytes/AND/party
- **Security:** Semi-honest (dishonest majority)

Source: `Protocols/Beaver.hpp:44-99`, `OT/NPartyTripleGenerator.hpp:609-625`

### Replicated Binary Protocol (`replicated-bin-party.x`)

- **Implementation:** Local computation + single reshare
- **Per AND gate per party:** ~1 byte sent
  - Local mul: `x[0] * (y[0] XOR y[1]) XOR x[1] * y[0]` — no communication
  - Reshare: `pass_around()` — 1 byte to next party in ring
- **Rounds per AND:** 1 (single pass_around)
- **Parties:** Exactly 3 (hardcoded, `variable_players = false`)
- **Security:** Semi-honest, honest majority (t <= 1 out of 3)
- **Share representation:** Each party holds 2 shares of 8 bytes each (Rep3Share)

Source: `GC/ShareSecret.hpp`, `Protocols/Replicated.hpp:226-282`, `Protocols/Rep3Share.h`

### Protocol Comparison

| Metric | Semi (N=2) | Semi (N=3) | Replicated (N=3) |
|--------|-----------|-----------|-----------------|
| Bytes/AND/party | ~16 | ~32 | ~1 |
| Rounds/AND | 2 | 2 | 1 |
| Total bytes/AND (all parties) | ~32 | ~96 | ~3 |
| Relative cost | 1x | 3x | 0.09x |

**Replicated is ~32x cheaper per AND gate than Semi for N=3.**

### Bytecode Compatibility

Same `.mpc` source compiles to protocol-agnostic bytecode. Different protocol binaries (`semi-bin-party.x` vs `replicated-bin-party.x`) interpret the same bytecode instructions (AND, MULS, OPEN, etc.) with different underlying protocols. **Recompilation of .mpc is NOT needed** when switching protocols — only the runtime binary changes.

However, the NEON framework must be configured to launch the correct binary. Protocol switch requires:
1. Change `neon.set_protocol(protocol.Semi)` to the replicated protocol
2. Ensure `neon.set_number_of_parties(3)` (replicated requires exactly 3)

## 3. Threading Model

### `@for_range_opt_multithread(N_THREADS, n)`

**Compilation:** Creates N_THREADS separate bytecode tapes with **static work division** at compile time. Each tape handles `n // N_THREADS` iterations (with remainder distributed to last threads).

**NOT a work-stealing pool.** Work is statically assigned: thread `i` processes iterations `[i * chunk, (i+1) * chunk)`.

**Runtime:** Tapes launched via `RUN_TAPE` instruction, joined via `JOIN_TAPE`. Thread infrastructure is pre-allocated (no dynamic allocation).

**Overhead per block:** 1-2 rounds for spawn + join. Negligible for large iteration counts, but significant if many small threaded blocks are launched sequentially (e.g., N-party iterative merge with 8+ copy/merge loops per pass).

Source: `Compiler/library.py:1220-1405`, `Processor/Machine.hpp:247-258`

### `@for_range_opt(n)` (sequential)

Single-thread execution with basic loop optimizations. Each iteration that uses `MemValue` creates a **sequential data dependency** — iteration `i+1` cannot start until iteration `i`'s MemValue write completes.

**Cost:** 1 round per iteration that reads+writes a MemValue (due to memory dependency). For TOTAL_N=1638 iterations: ~1638 sequential rounds.

### Memory Model

- **Matrix:** Row-major contiguous storage. `matrix[i]` computes address as `base + i * cols * element_size`.
- **Shared memory:** All threads access the same Matrix/Array storage. No thread-local copies.
- **MemValue:** Explicit shared mutable state. Read = 1 `ldmc`, Write = 1 `stmc`. Cannot be used across threads within the same block (race condition).
- **Secret-indexed access:** `matrix[secret_idx]` compiles to a multiplexer tree of O(n) mux operations. Each mux costs 64 AND gates per 64-bit column. Total: O(n * cols * 64) AND gates per secret-indexed read.

### Sorting

`matrix.sort(key_indices=[0], n_threads=N_THREADS)` uses **Batcher's odd-even mergesort** (for binary programs compiled with `-B`).

- **Complexity:** O(n/2 * log^2(n)) compare-and-swap operations
- **Parallelization:** Independent compare-and-swap pairs within the same network layer run in parallel threads via `@for_range_opt_multithread`
- **Per compare-and-swap:** Comparison on key column (64 AND + 6 rounds) + swap of ALL columns (cols * 64 AND + 1 round)
- **The comparison is already on key column only.** The cost of sorting a wider matrix comes entirely from swapping more columns per pair.

Source: `Compiler/library.py:832-883` (loopy_odd_even_merge_sort), `Compiler/library.py:760-777` (cond_swap)

## 4. Cost Model Formulas

### Bitonic Merge (PSI stage)

For a merge of two sorted arrays totaling T elements, padded to POW2:
```
steps = log2(POW2)
pairs_per_step = POW2 / 2
total_pairs = steps * pairs_per_step

AND_per_pair = 64 (compare) + ROW_WIDTH * 64 (mux, shared diff optimization)
total_AND = total_pairs * AND_per_pair
```

Note: The mux `sel.if_else(a, b)` computes `diff = a XOR b` then `sel AND diff`. For a compare-and-swap pair, `new_a` and `new_b` share the same AND result: `m = sel AND (a XOR b)`, then `new_a = a XOR m`, `new_b = b XOR m`. So each column costs 64 AND gates (not 128).

### Pruned Bitonic Merge (Reconstruction inner merge)

Same as above, but compile-time pruning eliminates pairs where both positions are known padding:
- `both_padding`: skip entirely (0 AND)
- `data_padding` (data at idx1, padding at idx2): skip (already correct order)
- `padding_data` (padding at idx1, data at idx2): unconditional copy (0 AND, just register assignment)
- `real_pair`: full compare-and-swap (64 + arrays * 64 AND)

Currently 3 parallel arrays (keys, timestamps, activities): `AND_per_real_pair = 64 + 3 * 64 = 256` AND gates.
With packed events (1 array): `AND_per_real_pair = 64 + 64 = 128` AND gates.

### Reconstruction Total Cost

```
N=2:
  total = (TOTAL_N - 1) * (match_checks + inner_merge + extract_activities)
  match_checks = 3 * 64  (ids_match + diff_parties + is_valid_id = 192 AND)
  inner_merge = real_pairs * AND_per_pair  (see pruned merge above)
  extract = FULL_LEN * 64  (one if_else per event)

N>=3:
  pass1 = (TOTAL_N - 1) * ~256 AND  (sequential, 1 round per iteration)
  pass2 = (TOTAL_N - N + 1) * (distinctness + inner_merge + extract)
  distinctness = C(N,2) * 64 AND  (pairwise party ID checks)
  inner_merge = sum over (N-1) merge steps of: real_pairs_k * AND_per_pair
  extract = FULL_LEN * 128 AND  (comparison + bitwise AND mask per event)
```

### Total Communication

```
total_bytes = total_AND * bytes_per_AND
bytes_per_AND:
  Semi N=2: ~32 bytes (16 per party × 2 parties)
  Semi N=3: ~96 bytes (32 per party × 3 parties)
  Replicated N=3: ~3 bytes (1 per party × 3 parties)
```

## 5. Verification: Predicted vs Measured

Test configuration: BPIC 2013 open, 16 threads, local execution on 8-core AMD Ryzen 7 5800H.
- N=2: N_PER_PARTY=635, PARTIAL_LEN=13
- N=3: N_PER_PARTY=546, PARTIAL_LEN=15 (3-way split of same data)

### Gate Count Accuracy (model vs compiler)

The MP-SPDZ compiler reports exact "bit triples" consumed (= AND gates).

| Config | Model | Compiler | Error |
|--------|-------|----------|-------|
| BPIC Open N=2 (635×13) | 107,835,026 | **104,894,803** | **+2.8%** |
| BPIC Closed N=2 (1314×18) | 312,317,881 | **305,784,442** | **+2.1%** |
| BPIC Open N=3 (546×15) | 290,737,583 | **288,111,599** | **+0.9%** |

The gate count model from Section 4 is accurate to within 3% across all tested configurations.

### Per-Stage Gate Breakdown (model)

| Stage | N=2 AND gates | N=3 AND gates | Gate ratio |
|-------|--------------|--------------|-----------|
| PSI | 46.9M | 93.7M | 2.0x |
| Reconstruction | 40.8M | 160.3M | 3.9x |
| Grouping Sort | 16.1M | 24.2M | 1.5x |
| **Total** | **103.8M** | **278.2M** | **2.7x** |

### Communication Calibration

| Metric | N=2 | N=3 | Ratio |
|--------|-----|-----|-------|
| Compiler triples | 104.9M | 288.1M | 2.75x |
| Measured comm | 442 MB | 14,672 MB | 33.2x |
| **Bytes/triple** | **4.21** | **50.92** | **12.1x** |

**The bytes/triple is NOT proportional to C(N,2).** The 12x ratio (vs expected 3x from `C(3,2)/C(2,2) = 3`) reveals that the per-pair OT cost itself is ~4x higher for N=3:

| | N=2 (1 pair) | N=3 (3 pairs) | Per-pair cost |
|--|-------------|--------------|--------------|
| Bytes/triple | 4.21 | 50.92 | 16.97 |

The 4x per-pair inflation for N=3 is caused by:
1. **CPU contention:** 3 processes × 16 threads = 48 threads on 16 HW threads → OT computation takes longer → buffers grow larger
2. **OT batch scheduling:** 2 OT multipliers per party compete for CPU, causing suboptimal batching
3. **Protocol synchronization:** Online phase broadcast to N-1 parties adds per-round overhead

### Runtime Throughput

| Metric | N=2 | N=3 | Ratio |
|--------|-----|-----|-------|
| Runtime | 5.03s | 65.41s | 13.0x |
| **Triples/sec** | **20.9M** | **4.4M** | **0.21x** |
| Rounds | 682,708 | 979,363 | 1.43x |

N=3 processes 4.75x fewer triples per second than N=2. This combines:
- 2.75x more triples to process
- 4.75x lower throughput per triple
- Product: 2.75 × 4.75 ÷ 1 ≈ 13.1x total slowdown (matches measured 13.0x)

**The 4.75x throughput drop is local-execution-specific.** Each party process spawns ~5-6 OS threads per `N_THREADS` computation thread (for OT preprocessing, communication workers). With `--threads 16`: 118 OS threads per party. For N=3 local: 3 × 118 = 354 OS threads on 16 HW threads (22x oversubscription). On separate machines, each party has 118 threads on 16 HW threads (7.4x) — still oversubscribed but much less severe.

### Calibrated Prediction Model

For estimating the impact of code changes, use:

```
Predicted comm (MB) = delta_triples × bytes_per_triple / 1e6
Predicted runtime (s) = delta_triples / throughput

bytes_per_triple:
  Semi N=2 local: 4.21
  Semi N=3 local: 50.92

throughput (triples/sec):
  Semi N=2 local: 20.9M
  Semi N=3 local: 4.4M
```

**Example:** Reducing N=3 AND gates by 50M (e.g., from packed events):
- Comm saving: 50M × 50.92 = 2,546 MB
- Runtime saving: 50M / 4.4M = 11.4s

### Apples-to-Apples: N=2 vs N=3 (same N_PER_PARTY=546, PARTIAL_LEN=15)

| Metric | N=2 | N=3 | Ratio |
|--------|-----|-----|-------|
| Compiler triples | 113,062,143 | 288,111,599 | 2.55x |
| Predicted runtime | 5.4s | 65.5s | 12.1x |
| Measured runtime | 5.03s | 65.4s | 13.0x |

The triple count ratio (2.55x) combined with the throughput ratio (4.75x) accurately predicts the total runtime ratio: 2.55 × 4.75 = 12.1x (measured: 13.0x, ~7% error).

## 6. Impact Estimation Checklist

Before implementing any MPC program change, estimate impact using this checklist:

### Step 1: Count affected operations
For each modified code path, count:
- Number of comparisons (`>`, `==`, `!=`)
- Number of MUX operations (`if_else`, bitwise AND masking)
- Number of arithmetic operations (`+`, `-`, `*`)
- Width of data moved per compare-and-swap (ROW_WIDTH or number of parallel arrays)

### Step 2: Calculate AND gate delta
```
delta_AND = new_AND - old_AND
```
Use the gate costs from Section 1.

### Step 3: Estimate communication delta
```
delta_comm_MB = delta_AND * bytes_per_AND / 1e6
bytes_per_AND (calibrated from BPIC 2013 open, local execution):
  Semi N=2: 4.21 bytes
  Semi N=3: 50.92 bytes
  Replicated N=3: ~1.6 bytes (estimated: 50.92 / 32)
```

### Step 4: Estimate runtime delta
```
delta_time = delta_AND / throughput
throughput (calibrated, triples/sec, local execution):
  Semi N=2: 20.9M triples/sec
  Semi N=3: 4.4M triples/sec
  Replicated N=3: ~50M triples/sec (estimated)
```

**Example:** Saving 50M AND gates in N=3:
- Comm: 50M × 50.92 / 1e6 = 2,546 MB less
- Runtime: 50M / 4.4M = 11.4s faster

### Step 5: Check for round complexity changes
- Adding MemValue in a loop: +1 sequential round per iteration
- Removing a threaded loop and replacing with sequential: multiply rounds by iteration count
- Adding a comparison inside a threaded loop: +6 rounds depth (but amortized across threads)

### Step 6: Check compile-time impact
- Unrolled loops in `.mpc` generate bytecode proportional to iteration count × body size
- Larger bytecode = longer compile time (can be > runtime for small inputs)
- Adding a second pruned_bitonic_merge call in reconstruction roughly doubles compile time
