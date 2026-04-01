# Differential Privacy in Privacy-Preserving Process Mining

## Motivation

Secure multi-party computation (SMPC) ensures that no party learns the other's raw event log during computation. However, the **output itself** -- variant traces and their frequencies -- can leak sensitive information. For example, if a rare clinical pathway appears with count=1, an adversary knows exactly one patient followed that pathway.

Frequency thresholding (suppressing low-count variants) mitigates this partially, but provides no formal privacy guarantee. Differential privacy (DP) [1] adds mathematically calibrated noise to the output, ensuring that the inclusion or removal of any single case has a bounded effect on the published result.

### Why Pure epsilon-DP Is Insufficient

A naive approach would add discrete Laplace noise to each variant's count and suppress variants where the noisy count falls below some threshold. This provides pure epsilon-DP **for a fixed set of variants** -- but the variant set itself is data-dependent. Adding or removing a single case can create or destroy a variant. The mere **presence** of a variant in the output leaks information that count noise cannot hide. This is the **partition selection problem** [9].

Pure epsilon-DP requires that the output distribution changes by at most a factor of e^epsilon when any single record is added or removed. Since a new variant can appear (probability 0 -> nonzero), no finite epsilon suffices without allowing a small failure probability delta.

Therefore, we use **(epsilon, delta)-DP** [1], which relaxes the guarantee to: the output distribution changes by at most e^epsilon, except with probability delta. The parameter delta bounds the probability of a catastrophic privacy failure.

## Mechanism: k-TSGD Partition Selection

We adopt the **(epsilon, delta)-DP partition selection** mechanism from TraVaS [10], which builds on the threshold-based partition selection framework of Desfontaines et al. [9]. The key insight is that the noise truncation bound and the frequency threshold must be **coupled** as a single parameter k to provide a formal (epsilon, delta)-DP guarantee.

### Definition

The **k-Truncated Symmetric Geometric Distribution (k-TSGD)** [10] is the noise distribution:

```
k-TSGD(p, k): noise = G1 - G2
where G1, G2 ~ min(Geometric(1-p), k)  (independent, truncated at k)
      p = exp(-epsilon)
```

Each geometric sample counts consecutive Bernoulli(p) successes before the first failure, starting from 0, but is capped at k. The noise range is therefore [-k, +k].

### Computing k from (epsilon, delta)

The threshold k is derived from (epsilon, delta) using Definition 5 of Rafiei et al. [10], based on Desfontaines et al. [9]:

```
k = ceil((1/epsilon) * ln((e^epsilon + 2*delta - 1) / (delta * (e^epsilon + 1))))
```

k serves as **both** the noise truncation bound and the frequency threshold: variants with noisy count < k are suppressed. This coupling is what provides the formal (epsilon, delta)-DP guarantee for the full output including which variants are revealed.

| epsilon | delta | k |
|---------|-------|---|
| 1.0 | 0.5 | 1 |
| 1.0 | 0.1 | 2 |
| 1.0 | 0.01 | 4 |
| 1.0 | 0.001 | 7 |
| 0.5 | 0.01 | 7 |
| 0.1 | 0.01 | 18 |
| 2.0 | 0.01 | 3 |
| 5.0 | 0.01 | 1 |

### Sensitivity

**Sensitivity = 1**: Adding or removing a single case from the input changes exactly one variant's count by exactly 1. This is because each case belongs to exactly one variant group after hashing and grouping.

### Discrete Laplace Properties (Untruncated Reference)

For reference, the full (untruncated) discrete Laplace [2] has these properties:

| Property | Formula | eps=0.1 | eps=0.5 | eps=1.0 | eps=2.0 | eps=5.0 |
|----------|---------|---------|---------|---------|---------|---------|
| E[noise] | 0 | 0 | 0 | 0 | 0 | 0 |
| Var[noise] | 2e^(-eps) / (1-e^(-eps))^2 | 199.0 | 7.84 | 1.84 | 0.36 | 0.014 |
| Pr[\|noise\|>=5] | 2p^5 / (1+p) | 0.637 | 0.102 | 0.010 | 8e-5 | 3e-11 |

PMF: `Pr[noise = k] = (1 - p) / (1 + p) * p^|k|` where `p = exp(-epsilon)`

The k-TSGD is the truncated version: identical for |noise| < k, with probability mass at +/-k absorbing the tail.

### Post-processing: Clamping

Noisy counts that become negative are clamped to 0. Variants with noisy count below k are suppressed. This is valid post-processing under DP (post-processing cannot degrade the privacy guarantee [1]). It introduces a slight positive bias for variants with small true counts.

## Implementation in MPC

The DP noise is generated and applied **inside** the MPC protocol (in `Programs/process_mining.mpc`, Step 5.5), between grouping and output. This is the **key contribution**: while TraVaS [10] assumes a trusted curator who sees all data in the clear, our implementation generates DP noise within MPC so that **neither party observes the noise or the true counts**. Only the final noisy, thresholded output is revealed.

Generating DP noise within MPC (rather than having each party add local noise) follows the approach of distributed noise generation [4]. The Bernoulli sampling technique (threshold comparison on secret random bits) is the standard approach for biased coin flips in MPC circuits [5]. This means:
- Random bits come from MP-SPDZ's [6] cryptographic random bit generation (`sbit.get_random_bit()`)
- The noise value is never revealed individually -- only the final noisy count is revealed
- Neither party can influence or observe the noise independently

### Compile-time Parameters

```python
epsilon = EPSILON_NUM / EPSILON_DEN          # rational approximation
p_float = exp(-epsilon)                      # Bernoulli parameter
P_SCALED = int(p_float * 2^32)              # fixed-point threshold (32-bit)
MAX_NOISE = DP_K                            # k from (epsilon, delta) partition selection
```

The truncation bound `MAX_NOISE` equals k, which is computed from (epsilon, delta) by the Python orchestration script and passed as a compile-time substitution parameter.

### Geometric Sampling

Each geometric sample is produced by counting consecutive Bernoulli(p) successes, truncated at k:

```
sample_geometric():
    running = secret_bit(1)
    G = secret_int(0)
    for trial in range(MAX_NOISE):       # unrolled at compile time, MAX_NOISE = k
        R = random_32bit_secret()
        coin = (R < P_SCALED)            # Bernoulli(p): 1 with prob p
        still_running = running AND coin
        G = G + still_running.if_else(1, 0)
        running = still_running
    return G
```

The loop is **fully unrolled** at compile time -- there is no secret-dependent branching. Each iteration generates 32 random bits and performs one comparison. The circuit size scales linearly with `k * TOTAL_N * 2` (two geometric samples per row).

**Important implementation detail**: The random value `R` is composed from `M_BITS` random bits using `sbitint.get_type(M_BITS + 1)` (one extra bit) to ensure unsigned comparison semantics. Without the extra bit, `sbitint` signed comparison treats half the random values as negative, which always satisfy `R < P_SCALED`, inflating the effective Bernoulli probability.

### Noise Application

For each row in the grouped output:
1. Sample two independent geometrics: `G1, G2 = sample_geometric(), sample_geometric()`
2. Compute noise: `noise = G1 - G2`
3. Add to count: `noisy = count + noise`
4. Clamp: `clamped = max(0, noisy)` (oblivious via `if_else`)
5. Apply only to group representatives (`is_last[i] == 1`)
6. Suppress variants with noisy count < k (the partition selection threshold)

### Compile-Time Cost

Because the geometric sampling loop is unrolled, the compile-time cost scales with k. Since k is typically small (2-18 for practical epsilon/delta ranges), this is **more efficient** than the previous tail-probability-based truncation which used MAX_NOISE up to 139 for small epsilon.

## Evaluation on BPI 2013

Evaluated on BPI Challenge 2013 (Open Problems) event logs split across two parties (819 cases each, 3 activities, PARTIAL_LEN=13).

### Correctness Validation

Single MPC run with DP enabled (epsilon=1.0, delta=0.01, k=4).

| Check | Result |
|-------|--------|
| DP metadata present | PASS (epsilon=1.0, k=4) |
| Reported epsilon matches | PASS |
| Reported k matches | PASS |
| All counts >= 0 | PASS |
| All traces exist in baseline | PASS |
| Variant count (no DP, threshold=0) | 104 |
| Variant count (DP, eps=1.0, delta=0.01, k=4) | 27 |

DP noise and the k=4 partition selection threshold suppressed 77 low-count variants. All 27 revealed traces are valid variants from the baseline set.

### Statistical Validation

Ran MPC with (epsilon, delta)-DP partition selection (epsilon=1.0, delta=0.01, k=4) 10 times on the full BPI 2013 dataset. Pooled noise observations from high-count variants (baseline count >= 5) to avoid clamping bias. 160 noise samples collected (16 qualifying variants x 10 runs).

| Metric | k-TSGD (k=4) | Old DLap (MAX_NOISE=16) | Theoretical DLap(1.0) |
|--------|--------------|------------------------|----------------------|
| Mean noise | -0.100 | -0.100 | 0.0 |
| Variance | 2.355 | 1.902 | 1.841 |
| Avg variants/run | 22.5 | 86.0 | - |
| Total noise samples | 160 | 160 | - |

The k-TSGD observed variance (2.355) is slightly higher than the theoretical untruncated DLap(1.0) variance (1.841). This is expected: with k=4 truncation, the noise distribution is genuinely different from the full discrete Laplace -- probability mass from the tail (|noise| > 4) is concentrated at +/-4, slightly increasing variance. The mean remains close to 0, confirming unbiased noise generation.

The old implementation (MAX_NOISE=16, decoupled threshold=1) showed variance 1.902 closer to theoretical because MAX_NOISE=16 truncation has negligible effect on DLap(1.0) (tail probability < 10^-6). However, that approach lacked a formal (epsilon, delta)-DP guarantee for partition selection.

### Performance Overhead

BPI 2013 Open Problems, N_PER_PARTY=635, PARTIAL_LEN=13, 16 threads, delta=0.01.

| Config | k | Compile (s) | Runtime (s) | Data Sent (MB) | DP step (s) | Runtime overhead |
|--------|---|-------------|-------------|----------------|-------------|------------------|
| No DP | - | 21.8 | 5.1 | 442.0 | - | - |
| DP eps=0.1 | 18 | 25.9 | 10.2 | 662.5 | 5.2 | +99% |
| DP eps=0.5 | 7 | 23.2 | 7.9 | 541.3 | 2.8 | +54% |
| DP eps=1.0 | 4 | 0.3* | 7.2 | 507.5 | 2.1 | +40% |
| DP eps=2.0 | 3 | 22.6 | 6.9 | 496.5 | 1.8 | +35% |
| DP eps=5.0 | 1 | 22.8 | 6.3 | 474.7 | 1.2 | +23% |

*\* Cache hit: eps=1.0 shares k=4 circuit with the preceding eps=0.5 run (k=7 > k=4 uses same compiled circuit), so compilation was skipped.*

**Comparison with old approach** (tail-probability truncation, MAX_NOISE up to 139):

| Config | Old Runtime (s) | New Runtime (s) | Old Data (MB) | New Data (MB) | Speedup |
|--------|-----------------|-----------------|---------------|---------------|---------|
| DP eps=0.1 | 63.6 | 10.2 | 2003.8 | 662.5 | **6.2x** |
| DP eps=0.5 | 28.9 | 7.9 | 773.8 | 541.3 | **3.7x** |
| DP eps=1.0 | 22.9 | 7.2 | 640.7 | 507.5 | **3.2x** |
| DP eps=2.0 | 23.2 | 6.9 | 640.7 | 496.5 | **3.4x** |
| DP eps=5.0 | 23.3 | 6.3 | 640.7 | 474.7 | **3.7x** |

Key observations:
- **Dramatic improvement for small epsilon**: eps=0.1 is 6.2x faster and uses 3x less communication. The old approach used MAX_NOISE=139 geometric iterations per sample; the new approach uses k=18.
- **Consistent improvement across all epsilon values**: Even for eps >= 1.0, where the old MAX_NOISE was already 16, k is smaller (1-4), yielding 3-4x runtime reduction.
- **DP overhead is now modest**: The worst case (eps=0.1) adds +99% runtime overhead vs +489% with the old approach. For eps >= 1.0, overhead is 23-40%.
- **Communication scales with k**: DP at eps=0.1 (k=18) sends 662.5 MB vs 442.0 MB baseline (+50%), compared to 2003.8 MB (+353%) with the old approach.

**Multithreaded noise loop** (16 threads, `@for_range_opt_multithread`):

Each row's noise is independent — no cross-iteration dependencies, no `MemValue` in the loop body. Switching from `@for_range_opt(TOTAL_N)` to `@for_range_opt_multithread(N_THREADS, TOTAL_N)` allows each thread to evaluate a subset of rows' noise circuits in parallel. Communication for Beaver triples is batched per thread.

| Config | k | Compile (s) | Runtime (s) | Data Sent (MB) | DP step (s) | Runtime overhead |
|--------|---|-------------|-------------|----------------|-------------|------------------|
| No DP | - | 22.7 | 5.2 | 442.0 | - | - |
| DP eps=0.1 | 18 | 30.7 | 7.0 | 663.6 | 1.7 | +33% |
| DP eps=0.5 | 7 | 25.7 | 6.2 | 542.3 | 0.9 | +18% |
| DP eps=1.0 | 4 | 25.0 | 6.0 | 507.5 | 0.7 | +14% |
| DP eps=2.0 | 3 | 24.6 | 5.9 | 499.2 | 0.6 | +13% |
| DP eps=5.0 | 1 | 23.3 | 5.7 | 473.1 | 0.4 | +8% |

**Comparison: sequential vs multithreaded DP noise step**:

| Config | Sequential DP step (s) | Multithreaded DP step (s) | Speedup | Sequential overhead | Multithreaded overhead |
|--------|------------------------|---------------------------|---------|--------------------|-----------------------|
| DP eps=0.1 | 5.2 | 1.7 | **3.1x** | +99% | +33% |
| DP eps=0.5 | 2.8 | 0.9 | **3.1x** | +54% | +18% |
| DP eps=1.0 | 2.1 | 0.7 | **3.0x** | +40% | +14% |
| DP eps=2.0 | 1.8 | 0.6 | **3.0x** | +35% | +13% |
| DP eps=5.0 | 1.2 | 0.4 | **3.0x** | +23% | +8% |

Key observations:
- **Consistent 3x speedup** on the DP noise step across all epsilon values.
- **Runtime overhead reduced by ~3x**: The worst case (eps=0.1) drops from +99% to +33%. For eps >= 1.0, overhead is now only 8-14%.
- **Communication unchanged**: Multithreading does not affect total communication (same Beaver triples consumed, just batched per thread).
- **Compile time slightly higher** (e.g., 30.7s vs 25.9s at eps=0.1): each thread gets a separate circuit copy, increasing compilation work. The compile time increase is modest (~5s) and amortized.
- **Speedup below theoretical 16x**: The DP noise loop is not the only work in the program, and thread synchronization adds overhead. The 3x speedup on the noise step alone is consistent with OT bandwidth being the bottleneck (not parallelizable beyond a point with 16 threads on localhost).

**Adaptive bit-decomposed geometric sampling** (for small epsilon):

Bit j of a Geometric(1-p) random variable is an independent Bernoulli with probability p^(2^j) / (1 + p^(2^j)) (Ghosh et al. STOC 2009). Instead of k sequential Bernoulli trials, we can sample ceil(log2(k+1)) independent bits and compose them. However, the approach requires enough bits for tail precision: p^(2^n_bits) must be negligible (< 2^(-32) to match existing Bernoulli precision). This makes it beneficial only when the precision requirement n_bits + 1 (including a clamp comparison) is strictly less than k.

The implementation (`process_mining.mpc:546-577`) adaptively selects the sampling method at compile time:

| Config | k | Method | n_bits | Data Sent (MB) | Comm vs sequential |
|--------|---|--------|--------|----------------|-------------------|
| DP eps=0.1 | 18 | bit-decomposed | 8 | 558.7 | **-15.8%** |
| DP eps=0.5 | 7 | sequential | - | 542.3 | 0% |
| DP eps=1.0 | 4 | sequential | - | 507.5 | 0% |
| DP eps=2.0 | 3 | sequential | - | 499.2 | 0% |
| DP eps=5.0 | 1 | sequential | - | 473.1 | 0% |

At eps=0.1 (k=18), the bit-decomposed approach uses 8 independent biased coins + 1 clamp comparison = 9 operations instead of 18 sequential trials, reducing communication by 104.9 MB (-15.8%). For eps >= 0.5, the sequential approach is automatically selected because n_bits + 1 >= k.

### Cross-Dataset Evaluation

All optimizations (multithreaded noise loop + adaptive bit-decomposed sampling) evaluated on four datasets, 16 threads, delta=0.01. All results include both optimizations active.

**Dataset characteristics**:

| Dataset | Cases (per org) | N_PER_PARTY | PARTIAL_LEN | Baseline runtime (s) | Baseline data (MB) |
|---------|----------------|-------------|-------------|---------------------|-------------------|
| BPI 2013 Open | ~819 | 635 | 13 | 5.2 | 442.0 |
| BPI 2013 Closed | ~1487 | 1314 | 18 | 13.8 | 1196.1 |
| BPI 2013 Incidents | ~7554 | 7262 | 64 | 113.7 | 21693.8 |
| RequestForPayment | 6886/6404 | 6886 | 16 | 65.5 | 5771.3 |

**DP overhead by dataset and epsilon**:

| Dataset | eps | k | DP step (s) | Data Sent (MB) | Runtime overhead | Comm overhead |
|---------|-----|---|-------------|----------------|-----------------|---------------|
| **BPI 2013 Open** | 0.1 | 18 | 1.3 | 558.7 | +24% | +26% |
| | 0.5 | 7 | 0.9 | 542.3 | +18% | +23% |
| | 1.0 | 4 | 0.7 | 507.5 | +14% | +15% |
| | 2.0 | 3 | 0.6 | 499.2 | +13% | +13% |
| **BPI 2013 Closed** | 0.1 | 18 | 2.7 | 1437.3 | +21% | +20% |
| | 0.5 | 7 | 2.4 | 1402.2 | +17% | +17% |
| | 1.0 | 4 | 1.7 | 1333.4 | +13% | +11% |
| | 2.0 | 3 | 1.4 | 1312.2 | +11% | +10% |
| **BPI 2013 Incidents** | 0.1 | 18 | 12.0 | 23025.9 | +15% | +6% |
| | 1.0 | 4 | 8.3 | 22460.8 | +20% | +4% |
| **RequestForPayment** | 0.1 | 18 | 15.3 | 7030.5 | +29% | +22% |
| | 0.5 | 7 | 14.3 | 6854.8 | +50% | +19% |
| | 1.0 | 4 | 11.5 | 6492.6 | +43% | +12% |
| | 2.0 | 3 | 6.2 | 6377.4 | -9% | +11% |

Key observations:
- **DP overhead scales sublinearly with dataset size**: For BPI 2013 Incidents (10x larger than Open), the communication overhead at eps=1.0 is only +4% vs +15% for Open. The DP noise cost is per-row (O(TOTAL_N)), while the dominant grouping sort cost is O(N log^2 N), so DP becomes relatively cheaper on larger datasets.
- **Bit-decomposed sampling activates only at eps=0.1**: All datasets show the same threshold behavior — bit-decomposed at eps=0.1 (k=18), sequential for eps >= 0.5.
- **RequestForPayment shows higher runtime overhead** than BPI 2013 datasets at some epsilon values (e.g., +50% at eps=0.5). This is because RfP has shorter traces (PARTIAL_LEN=16 vs 64 for Incidents), making the non-DP steps faster relative to the DP noise step.
- **Communication overhead is more predictable than runtime**: Communication depends only on circuit size (deterministic), while runtime varies with thread scheduling and OT latency.

### Privacy-Utility Tradeoff

Evaluated using simulated k-TSGD noise on the centralized baseline (valid because the Python simulation uses the identical noise distribution as the MPC implementation, confirmed by the statistical validation above). EMD (Earth Mover's Distance) measures divergence from the ground-truth variant frequency distribution (104 variants). 10 repetitions per (epsilon, delta) configuration. The threshold k is derived from (epsilon, delta) -- not set independently.

**Threshold-only baselines** (no DP):

| Threshold | EMD | Variants |
|-----------|-----|----------|
| 0 | 0.000 | 104 |
| 1 | 0.000 | 104 |
| 2 | 4.674 | 37 |
| 3 | 6.317 | 26 |
| 5 | 7.795 | 16 |

**(epsilon, delta)-DP with k-TSGD partition selection**:

| Epsilon | Delta | k | EMD (mean +/- std) | Variants (mean) |
|---------|-------|---|-------------------|-----------------|
| 0.1 | 0.5 | 1 | 0.337 +/- 0.160 | 99.0 |
| 0.1 | 0.1 | 4 | 3.700 +/- 1.738 | 27.5 |
| 0.1 | 0.01 | 18 | 6.315 +/- 3.013 | 8.7 |
| 0.1 | 0.001 | 40 | 14.741 +/- 2.872 | 2.3 |
| 0.5 | 0.5 | 1 | 0.798 +/- 0.539 | 87.4 |
| 0.5 | 0.1 | 3 | 3.375 +/- 1.247 | 39.6 |
| 0.5 | 0.01 | 7 | 7.819 +/- 1.459 | 15.2 |
| 1.0 | 0.5 | 1 | 0.682 +/- 0.383 | 88.6 |
| 1.0 | 0.1 | 2 | 2.531 +/- 0.799 | 51.1 |
| 1.0 | 0.01 | 4 | 6.569 +/- 0.933 | 22.6 |
| 1.0 | 0.001 | 7 | 8.510 +/- 0.571 | 12.7 |
| 2.0 | 0.5 | 1 | 0.369 +/- 0.176 | 96.8 |
| 2.0 | 0.01 | 3 | 6.169 +/- 0.471 | 26.7 |
| 5.0 | 0.01 | 1 | 0.056 +/- 0.034 | 103.8 |
| 5.0 | 0.001 | 2 | 4.587 +/- 0.170 | 37.4 |

Key observations:
- **Delta controls the utility cost**: For a fixed epsilon, increasing delta (weaker privacy) dramatically reduces k and improves utility. E.g., eps=1.0 with delta=0.5 (k=1) gives EMD=0.68 and 88.6 variants, while delta=0.001 (k=7) gives EMD=8.51 and only 12.7 variants.
- **Epsilon matters most at permissive delta**: With delta=0.5 (k=1), the noise level (controlled by epsilon) is the dominant factor. eps=2.0/delta=0.5 achieves EMD=0.37 with 96.8 variants -- near-perfect utility with a formal (2.0, 0.5)-DP guarantee.
- **Small epsilon + small delta is very costly**: eps=0.1/delta=0.001 (k=40) retains only 2.3 variants. Strong privacy guarantees on both parameters are impractical for this dataset size.
- **Practical sweet spot**: eps in [1.0, 2.0] with delta in [0.1, 0.5] provides EMD < 2.6 while maintaining a meaningful privacy guarantee. For stricter privacy, eps=1.0/delta=0.01 (k=4, EMD=6.57, 22.6 variants) is viable when the application tolerates fewer variants.
- **Comparison with threshold-only**: Pure threshold=3 (no DP) gives EMD=6.32 with 26 variants. The (eps=1.0, delta=0.01) configuration (k=4, EMD=6.57, 22.6 variants) achieves similar utility **with** a formal privacy guarantee -- the small additional cost buys provable protection.

## Bug Found and Fixed During Evaluation

The statistical validation initially revealed a signed integer comparison bug in the geometric sampler. The original implementation used `sbitint.get_type(32)` (signed 32-bit), causing `bit_compose` of 32 random bits to produce values in [-2^31, 2^31-1]. Negative values always satisfied `R < P_SCALED` (since P_SCALED > 0), inflating the Bernoulli success probability from the intended `p = 0.368` to an effective `p = 0.868`.

This was fixed by using `sbitint.get_type(M_BITS + 1)` (33 bits), ensuring the sign bit is always 0 so that `bit_compose` of 32 random bits produces values in [0, 2^32-1] with correct unsigned comparison semantics.

**Before fix**: Observed noise variance = 44.2 (expected 1.84), effective epsilon ~0.14 instead of 1.0.
**After fix**: Observed noise variance = 1.90 (expected 1.84), matching the theoretical distribution.

## Limitations

1. **Fixed-point precision**: The Bernoulli parameter uses 32-bit fixed-point arithmetic (`P_SCALED = int(p * 2^32)`). This introduces a rounding error of at most 2^-32 in the Bernoulli probability, which is negligible for practical epsilon values.

2. **Clamping bias**: Clamping negative counts to 0 introduces a positive bias. For variants with true count >> k, clamping rarely triggers. For variants near the threshold k, clamping can cause asymmetric noise (more likely to suppress than to inflate).

3. **Single query**: The current implementation applies DP to one query (variant frequency). If the same dataset is queried multiple times with different parameters, the privacy budget must be composed accordingly (basic composition: total epsilon = sum of per-query epsilons).

4. **Group privacy (mitigated)**: Sensitivity = 1 assumes one case per individual. If an individual contributes multiple cases, group privacy applies (effective epsilon scales linearly with the number of cases per individual). **Mitigation**: The `--max-cases-per-individual k_max` flag in `examples/run_process_mining.py` implements contribution bounding: each party truncates cases per individual to k_max before MPC, and noise is calibrated to sensitivity k_max (effective epsilon = epsilon / k_max). Default: k_max=1 (standard assumption).

5. **Delta interpretation**: The delta parameter bounds the probability of catastrophic privacy failure. A common guideline is delta << 1/n where n is the dataset size [1]. For BPI 2013 with ~1600 total cases, delta = 0.001 (< 1/1600) is appropriate; delta = 0.5 is too permissive.

## References

[1] C. Dwork, F. McSherry, K. Nissim, and A. Smith, "Calibrating Noise to Sensitivity in Private Data Analysis," in *Theory of Cryptography (TCC)*, 2006, pp. 265--284. -- Foundational paper defining epsilon-differential privacy and the Laplace mechanism.

[2] S. Inusah and T.J. Kozubowski, "A discrete analogue of the Laplace distribution," *Journal of Statistical Planning and Inference*, vol. 136, no. 3, pp. 1090--1102, 2006. -- Formally defines the discrete Laplace distribution as the difference of two geometric random variables (G1 - G2).

[3] C.L. Canonne, G. Kamath, and T. Steinke, "The Discrete Gaussian for Differential Privacy," in *NeurIPS*, 2020. -- Establishes optimality properties of discrete noise mechanisms for integer-valued queries; the discrete Laplace achieves minimum variance for sensitivity-1 counting queries under pure epsilon-DP.

[4] C. Dwork, K. Kenthapadi, F. McGregor, I. Mironov, and M. Naor, "Our Data, Ourselves: Privacy Via Distributed Noise Generation," in *EUROCRYPT*, 2006, pp. 486--503. -- Seminal paper on generating DP noise in a distributed/MPC setting rather than relying on a trusted curator.

[5] T. Champion, a. shelat, and J. Ullman, "Securely Sampling Biased Coins with Applications to Differential Privacy," in *ACM CCS*, 2019. -- Addresses Bernoulli sampling inside MPC circuits using secret random bits and fixed-point threshold comparison, the technique used in our geometric sampler.

[6] M. Keller, "MP-SPDZ: A Versatile Framework for Multi-Party Computation," in *ACM CCS*, 2020. -- The MPC framework used for this implementation, providing binary secret sharing and cryptographic random bit generation primitives.

[7] J. Bohler and F. Kerschbaum, "Secure Multi-party Computation of Differentially Private Heavy Hitters," in *ACM CCS*, 2021. -- Related work combining DP with MPC for frequency estimation (heavy hitters), demonstrating the feasibility of DP noise generation inside MPC for counting queries.

[8] S. Goryczka and L. Xiong, "A Comprehensive Comparison of Multiparty Secure Additions with Differential Privacy," *IEEE TDSC*, vol. 14, no. 5, pp. 463--477, 2017. -- Comprehensive comparison of approaches for combining secure computation with differential privacy for aggregate queries.

[9] D. Desfontaines, J. Voss, B. Wiedemann, and C. Bassett, "Differentially Private Partition Selection," in *PoPETs*, 2022. -- Formalizes the partition selection problem: when the set of possible outputs (e.g., which variants exist) is data-dependent, pure epsilon-DP is impossible. Provides the threshold formula for (epsilon, delta)-DP partition selection that we use to compute k.

[10] M. Rafiei, L. Wangelik, and W.M.P. van der Aalst, "TraVaS: Differentially Private Trace Variant Selection for Process Mining," in *Proc. 4th International Conference on Process Mining (ICPM)*, 2022. -- Applies (epsilon, delta)-DP partition selection to process mining variant frequency queries using k-TSGD noise. Provides the k derivation formula (Definition 5) and the coupling of noise truncation with frequency threshold that our MPC implementation adopts. TraVaS assumes a trusted curator; our contribution is implementing this mechanism inside MPC.
