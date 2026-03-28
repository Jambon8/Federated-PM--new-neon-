# Differential Privacy in Privacy-Preserving Process Mining

## Motivation

Secure multi-party computation (SMPC) ensures that no party learns the other's raw event log during computation. However, the **output itself** -- variant traces and their frequencies -- can leak sensitive information. For example, if a rare clinical pathway appears with count=1, an adversary knows exactly one patient followed that pathway. Frequency thresholding (suppressing low-count variants) mitigates this partially, but provides no formal privacy guarantee.

Differential privacy (DP) [1] adds mathematically calibrated noise to the output, ensuring that the inclusion or removal of any single case has a bounded effect on the published result. This provides a provable privacy guarantee parameterized by epsilon, where smaller epsilon means stronger privacy (more noise).

## Mechanism: Discrete Laplace

### Definition

We use the **discrete Laplace mechanism** [2] because variant counts are integers and the discrete Laplace is the minimum-variance unbiased mechanism for integer-valued counting queries with sensitivity 1 [3]. The noise distribution is:

```
DLap(epsilon): noise = G1 - G2
where G1, G2 ~ Geometric(1 - exp(-epsilon))  (independent)
```

The Geometric(1-p) distribution counts the number of consecutive Bernoulli(p) successes before the first failure, starting from 0.

### Sensitivity

**Sensitivity = 1**: Adding or removing a single case from the input changes exactly one variant's count by exactly 1. This is because each case belongs to exactly one variant group after hashing and grouping.

### Properties

| Property | Formula | eps=0.1 | eps=0.5 | eps=1.0 | eps=2.0 | eps=5.0 |
|----------|---------|---------|---------|---------|---------|---------|
| E[noise] | 0 | 0 | 0 | 0 | 0 | 0 |
| Var[noise] | 2e^(-eps) / (1-e^(-eps))^2 | 199.0 | 7.84 | 1.84 | 0.36 | 0.014 |
| Pr[\|noise\|>=5] | 2p^5 / (1+p) | 0.637 | 0.102 | 0.010 | 8e-5 | 3e-11 |

PMF: `Pr[noise = k] = (1 - p) / (1 + p) * p^|k|` where `p = exp(-epsilon)`

### Post-processing: Clamping

Noisy counts that become negative are clamped to 0. Variants with noisy count = 0 are suppressed. This is a valid post-processing step under DP (post-processing cannot degrade the privacy guarantee). It introduces a slight positive bias for variants with small true counts.

## Implementation in MPC

The DP noise is generated and applied **inside** the MPC protocol (in `Programs/process_mining.mpc`, Step 5.5), between grouping and output. Generating DP noise within MPC (rather than having each party add local noise) follows the approach of distributed noise generation [4], ensuring neither party can bias or observe the noise. The Bernoulli sampling technique (threshold comparison on secret random bits) is the standard approach for biased coin flips in MPC circuits [5]. This means:
- Random bits come from MP-SPDZ's [6] cryptographic random bit generation (`sbit.get_random_bit()`)
- The noise value is never revealed individually -- only the final noisy count is revealed
- Neither party can influence or observe the noise independently

### Compile-time Parameters

```python
epsilon = EPSILON_NUM / EPSILON_DEN          # rational approximation
p_float = exp(-epsilon)                      # Bernoulli parameter
P_SCALED = int(p_float * 2^32)              # fixed-point threshold (32-bit)
MAX_NOISE = max(16, ceil(-ln(10^-6) / eps)) # truncation bound
```

The truncation bound `MAX_NOISE` determines how many Bernoulli trials the geometric sampler runs. The probability of the true geometric exceeding `MAX_NOISE` is less than 10^-6.

| Epsilon | MAX_NOISE | Truncation probability |
|---------|-----------|----------------------|
| 0.1 | 139 | < 10^-6 |
| 0.5 | 28 | < 10^-6 |
| 1.0 | 16 | < 10^-6 |
| 2.0 | 16 | < 10^-6 |
| 5.0 | 16 | < 10^-6 |

### Geometric Sampling

Each geometric sample is produced by counting consecutive Bernoulli(p) successes:

```
sample_geometric():
    running = secret_bit(1)
    G = secret_int(0)
    for trial in range(MAX_NOISE):       # unrolled at compile time
        R = random_32bit_secret()
        coin = (R < P_SCALED)            # Bernoulli(p): 1 with prob p
        still_running = running AND coin
        G = G + still_running.if_else(1, 0)
        running = still_running
    return G
```

The loop is **fully unrolled** at compile time -- there is no secret-dependent branching. Each iteration generates 32 random bits and performs one comparison. The circuit size scales linearly with `MAX_NOISE * TOTAL_N * 2` (two geometric samples per row).

**Important implementation detail**: The random value `R` is composed from `M_BITS` random bits using `sbitint.get_type(M_BITS + 1)` (one extra bit) to ensure unsigned comparison semantics. Without the extra bit, `sbitint` signed comparison treats half the random values as negative, which always satisfy `R < P_SCALED`, inflating the effective Bernoulli probability.

### Noise Application

For each row in the grouped output:
1. Sample two independent geometrics: `G1, G2 = sample_geometric(), sample_geometric()`
2. Compute noise: `noise = G1 - G2`
3. Add to count: `noisy = count + noise`
4. Clamp: `clamped = max(0, noisy)` (oblivious via `if_else`)
5. Apply only to group representatives (`is_last[i] == 1`)

### Compile-Time Cost

Because the geometric sampling loop is unrolled, smaller epsilon (larger `MAX_NOISE`) produces significantly larger compiled circuits. For epsilon=0.1, each row requires 139 * 2 = 278 geometric iterations, each generating 33 random bits and performing comparisons. This can make compilation take considerably longer than runtime.

## Evaluation on BPI 2013

Evaluated on BPI Challenge 2013 (Open Problems) event logs split across two parties (819 cases each, 3 activities, PARTIAL_LEN=13).

### Correctness Validation

Single MPC run with DP enabled (epsilon=1.0, threshold=1).

| Check | Result |
|-------|--------|
| DP metadata present | PASS (epsilon=1.0) |
| Reported epsilon matches | PASS |
| All counts >= 0 | PASS |
| All traces exist in baseline | PASS |
| Variant count (no DP, threshold=0) | 104 |
| Variant count (DP, eps=1.0, t=1) | 67 |

DP noise suppressed 37 low-count variants below the threshold. All 67 revealed traces are valid variants from the baseline set.

### Statistical Validation

Ran MPC with DP (epsilon=1.0) 10 times on the full BPI 2013 dataset. Pooled noise observations from high-count variants (baseline count >= 5) to avoid clamping bias. 160 noise samples collected (16 qualifying variants x 10 runs).

| Metric | Observed | Theoretical |
|--------|----------|-------------|
| Mean noise | -0.100 | 0.0 |
| Variance | 1.902 | 1.841 |
| Total noise samples | 160 | - |

The observed variance (1.902) closely matches the theoretical DLap(1.0) variance (1.841), confirming correct noise calibration. Observed noise PMF vs theoretical:

| Noise k | Observed | Theoretical |
|---------|----------|-------------|
| -5 | 0.006 | 0.003 |
| -2 | 0.062 | 0.063 |
| -1 | 0.169 | 0.170 |
| 0 | 0.481 | 0.462 |
| 1 | 0.169 | 0.170 |
| 2 | 0.031 | 0.063 |
| 3 | 0.025 | 0.023 |

### Performance Overhead

BPI 2013 Open Problems, N_PER_PARTY=635, PARTIAL_LEN=13, 16 threads.

| Config | Compile (s) | Runtime (s) | Data Sent (MB) | Runtime overhead |
|--------|-------------|-------------|----------------|------------------|
| No DP | 29.8 | 10.8 | 442.0 | - |
| DP eps=0.1 | 65.0 | 63.6 | 2003.8 | +489% |
| DP eps=0.5 | 36.5 | 28.9 | 773.8 | +168% |
| DP eps=1.0 | 0.5* | 22.9 | 640.7 | +112% |
| DP eps=2.0 | 35.2 | 23.2 | 640.7 | +115% |
| DP eps=5.0 | 35.4 | 23.3 | 640.7 | +116% |

*\* Cache hit: eps=1.0 shares `MAX_NOISE=16` with the preceding eps=0.5 run, so compilation was skipped. Expected compile time is ~35s (comparable to eps=2.0 and eps=5.0).*

Key observations:
- **Epsilon 0.1** has the highest overhead because `MAX_NOISE=139` (vs 16-28 for other values), requiring 8.7x more geometric iterations per row. This dominates both compile time and runtime.
- **Epsilon >= 1.0** all use `MAX_NOISE=16`, so their overhead is nearly identical (~23s runtime, ~640 MB communication).
- **Communication overhead**: DP at eps=0.1 sends 4.5x more data than no-DP, primarily from the random bit generation in the geometric sampler.
- The DP noise step itself (`TIMER_DP_NOISE`) accounts for the additional runtime beyond the no-DP baseline.

### Privacy-Utility Tradeoff

Evaluated using simulated discrete Laplace noise on the centralized baseline (valid because the Python simulation uses the identical DLap distribution as the MPC implementation, confirmed by the statistical validation above). EMD (Earth Mover's Distance) measures divergence from the ground-truth variant frequency distribution. 10 repetitions per configuration.

| Threshold | Epsilon | EMD (mean +/- std) | Variants (mean) |
|-----------|---------|-------------------|-----------------|
| 0 | None | 0.000 | 104 |
| 1 | None | 0.000 | 104 |
| 2 | None | 4.674 | 37 |
| 3 | None | 6.317 | 26 |
| 5 | None | 7.795 | 16 |
| 0 | 0.1 | 10.397 +/- 4.084 | 59.0 |
| 0 | 0.5 | 1.707 +/- 0.762 | 76.3 |
| 0 | 1.0 | 0.850 +/- 0.290 | 85.7 |
| 0 | 2.0 | 0.387 +/- 0.150 | 96.7 |
| 0 | 5.0 | 0.056 +/- 0.034 | 103.8 |
| 2 | 0.5 | 1.747 +/- 0.465 | 55.7 |
| 2 | 1.0 | 2.203 +/- 0.828 | 51.4 |
| 5 | 1.0 | 7.440 +/- 0.887 | 17.5 |

Key observations:
- **Epsilon >= 1.0 with no threshold**: EMD < 1.0, preserving the frequency distribution well. The noise is small enough that most variant counts change by at most +/-1.
- **Epsilon = 0.1**: High noise (EMD ~10) causes many variants to disappear or merge. Only useful when very strong privacy is required.
- **Threshold dominates at high thresholds**: At threshold=5, even without DP, EMD is 7.8 (only 16 variants remain). Adding DP noise has diminishing marginal impact when the threshold already eliminates most variants.
- **Sweet spot**: Epsilon in [0.5, 2.0] with threshold in [0, 2] provides a good balance -- EMD < 2.5 while maintaining formal privacy guarantees.

## Bug Found and Fixed During Evaluation

The statistical validation initially revealed a signed integer comparison bug in the geometric sampler. The original implementation used `sbitint.get_type(32)` (signed 32-bit), causing `bit_compose` of 32 random bits to produce values in [-2^31, 2^31-1]. Negative values always satisfied `R < P_SCALED` (since P_SCALED > 0), inflating the Bernoulli success probability from the intended `p = 0.368` to an effective `p = 0.868`.

This was fixed by using `sbitint.get_type(M_BITS + 1)` (33 bits), ensuring the sign bit is always 0 so that `bit_compose` of 32 random bits produces values in [0, 2^32-1] with correct unsigned comparison semantics.

**Before fix**: Observed noise variance = 44.2 (expected 1.84), effective epsilon ~0.14 instead of 1.0.
**After fix**: Observed noise variance = 1.90 (expected 1.84), matching the theoretical distribution.

## Limitations

1. **Truncation**: Geometric sampling is truncated at `MAX_NOISE`. For epsilon >= 0.5, the truncation probability is < 10^-6 per sample, which is negligible. For very small epsilon (e.g., 0.01), truncation could become meaningful.

2. **Fixed-point precision**: The Bernoulli parameter uses 32-bit fixed-point arithmetic (`P_SCALED = int(p * 2^32)`). This introduces a rounding error of at most 2^-32 in the Bernoulli probability, which is negligible for practical epsilon values.

3. **Clamping bias**: Clamping negative counts to 0 introduces a positive bias. For variants with true count >> 1/epsilon, clamping rarely triggers. For variants near the threshold, clamping can cause asymmetric noise (more likely to suppress than to inflate).

4. **Single query**: The current implementation applies DP to one query (variant frequency). If the same dataset is queried multiple times with different parameters, the privacy budget must be composed accordingly (basic composition: total epsilon = sum of per-query epsilons).

5. **No group privacy**: Sensitivity = 1 assumes one case per individual. If an individual contributes multiple cases, group privacy applies (effective epsilon scales linearly with the number of cases per individual).

## References

[1] C. Dwork, F. McSherry, K. Nissim, and A. Smith, "Calibrating Noise to Sensitivity in Private Data Analysis," in *Theory of Cryptography (TCC)*, 2006, pp. 265--284. -- Foundational paper defining epsilon-differential privacy and the Laplace mechanism.

[2] S. Inusah and T.J. Kozubowski, "A discrete analogue of the Laplace distribution," *Journal of Statistical Planning and Inference*, vol. 136, no. 3, pp. 1090--1102, 2006. -- Formally defines the discrete Laplace distribution as the difference of two geometric random variables (G1 - G2).

[3] C.L. Canonne, G. Kamath, and T. Steinke, "The Discrete Gaussian for Differential Privacy," in *NeurIPS*, 2020. -- Establishes optimality properties of discrete noise mechanisms for integer-valued queries; the discrete Laplace achieves minimum variance for sensitivity-1 counting queries under pure epsilon-DP.

[4] C. Dwork, K. Kenthapadi, F. McGregor, I. Mironov, and M. Naor, "Our Data, Ourselves: Privacy Via Distributed Noise Generation," in *EUROCRYPT*, 2006, pp. 486--503. -- Seminal paper on generating DP noise in a distributed/MPC setting rather than relying on a trusted curator.

[5] T. Champion, a. shelat, and J. Ullman, "Securely Sampling Biased Coins with Applications to Differential Privacy," in *ACM CCS*, 2019. -- Addresses Bernoulli sampling inside MPC circuits using secret random bits and fixed-point threshold comparison, the technique used in our geometric sampler.

[6] M. Keller, "MP-SPDZ: A Versatile Framework for Multi-Party Computation," in *ACM CCS*, 2020. -- The MPC framework used for this implementation, providing binary secret sharing and cryptographic random bit generation primitives.

[7] J. Bohler and F. Kerschbaum, "Secure Multi-party Computation of Differentially Private Heavy Hitters," in *ACM CCS*, 2021. -- Related work combining DP with MPC for frequency estimation (heavy hitters), demonstrating the feasibility of DP noise generation inside MPC for counting queries.

[8] S. Goryczka and L. Xiong, "A Comprehensive Comparison of Multiparty Secure Additions with Differential Privacy," *IEEE TDSC*, vol. 14, no. 5, pp. 463--477, 2017. -- Comprehensive comparison of approaches for combining secure computation with differential privacy for aggregate queries.
