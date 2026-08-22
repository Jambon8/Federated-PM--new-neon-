# Differential Privacy Improvements: Analysis and Recommendations

This document analyzes potential improvements to the differential privacy (DP) implementation in our privacy-preserving process mining system. Each improvement is evaluated for MPC feasibility, utility impact, and implementation effort. For the current implementation details, see `docs/differential_privacy.md`. For the underlying research survey, see `docs/dp_research.md`.

## 1. Executive Summary

| # | Improvement | Category | MPC Feasibility | Utility Impact | Performance | Effort | Status |
|---|---|---|---|---|---|---|---|
| 1 | Multithreaded Noise Loop | Implementation | High | None | **3x speedup** | Low | **Done** |
| 3 | Bit-Decomposed Geometric Sampling | Noise generation | High | None | **-15.8% comm (eps=0.1)** | Medium | **Done** |
| 4 | Subsampling Amplification | Budget amplification | High | Medium | Positive | Low | User option |

### What Is Already Implemented (and Why It Is Optimal)

The current system (`mpc/process_mining.mpc`, Step 5.5) implements:
- **k-TSGD partition selection** (Rafiei et al. ICPM 2022 / Desfontaines et al. PoPETs 2022) -- **provably optimal** for maximizing released variants under (epsilon, delta)-DP (see Section 4.1)
- Discrete Laplace noise via truncated geometric sampling -- **universally optimal** for sensitivity-1 counting queries under pure epsilon-DP (Ghosh et al. STOC 2009 [13])
- **Multithreaded** noise loop -- 3x speedup on the DP noise step (see Section 4.4)
- 32-bit fixed-point Bernoulli sampling -- **sufficient precision** (privacy loss < 10^-7, see Section 4.7)
- Coupled k as both noise truncation bound and frequency threshold -- **this coupling IS the key contribution** of Desfontaines et al. and is what makes the mechanism optimal
- In-MPC noise generation via biased coin flips -- **novel contribution vs. TraVaS's trusted curator**

**Critical insight: pure epsilon-DP is impossible for our use case.** The variant set is data-dependent (adding one case can create a new variant). Any mechanism releasing variant identities from an unbounded domain **must** use (epsilon, delta)-DP with delta > 0 (Desfontaines et al. [9], Theorem 2). See Section 4.2.

### Discrete Laplace Is Optimal for Sensitivity-1 Counting

Ghosh, Roughgarden, and Sundararajan (STOC 2009 [13]) proved that the discrete Laplace (geometric mechanism) is **universally optimal** for sensitivity-1 counting queries under pure epsilon-DP. "Universally" means it simultaneously minimizes expected loss for every Bayesian prior and every monotone loss function. No other pure epsilon-DP mechanism can achieve lower variance.

The discrete Gaussian (Canonne et al. NeurIPS 2020) is optimal under concentrated DP (zCDP), not pure epsilon-DP. For our single-query setting, the discrete Laplace is the correct choice.

### How Desfontaines and Rafiei Relate

**Desfontaines et al. [9]** ("Differentially Private Partition Selection," PoPETs 2022) provides the **theoretical foundation**:
- Proves that releasing data-dependent partitions (like process variants) from an unbounded domain **requires (epsilon, delta)-DP** — pure epsilon-DP is impossible (Theorem 2)
- Defines the optimal primitive π_opt(n): a smooth retention probability function that maximizes the expected number of released partitions while satisfying (epsilon, delta)-DP
- Proves π_opt is optimal: no other mechanism can release more partitions at the same privacy level

**Rafiei et al. [10]** (TraVaS, ICPM 2022) provides the **concrete realization for process mining**:
- Introduces k-TSGD as a practical implementation of Desfontaines' optimal π_opt
- Shows that adding k-TSGD noise and thresholding at k **exactly implements** the optimal retention probability
- Derives k from (epsilon, delta) via a closed-form formula (Definition 5)
- Applies this to process mining variant selection with empirical evaluation

The dependency is **Rafiei builds on Desfontaines**:
- k-TSGD's **optimality proof** comes from Desfontaines (TraVaS cites it as [4])
- The **impossibility of pure epsilon-DP** comes from Desfontaines (Theorem 2)
- The **coupling insight** (noise bound = frequency threshold) comes from Desfontaines

**Can you ignore Desfontaines and cite only Rafiei?** No:
1. TraVaS does not contain the optimality proof — it references Desfontaines for it
2. TraVaS does not prove the pure epsilon impossibility — it states it as a known result from Desfontaines
3. For a thesis, you need to cite the theoretical foundation, not just the application paper
4. Desfontaines is the more general, widely-cited result; TraVaS is the process mining specialization

In practice: **cite both**. Desfontaines for the theory (why k-TSGD is optimal, why pure epsilon fails), Rafiei for the application (how k-TSGD applies to process mining, the k formula, the empirical validation on event logs).

### Remaining Gaps

1. **Bit-decomposed geometric sampling** could reduce noise generation cost from O(k) to O(log k) comparisons per sample (see Section 4.5)

## 2. Current System Baseline

The system computes variant frequencies (sensitivity-1 counting queries) inside binary-circuit MPC using the Semi protocol. The DP mechanism (`mpc/process_mining.mpc`, Step 5.5) is:

- **Mechanism**: (epsilon, delta)-DP partition selection via k-TSGD [10] (Rafiei et al. ICPM 2022, based on Desfontaines et al. [9])
- **Noise**: Discrete Laplace via difference of two truncated geometric samples: `noise = G1 - G2`, where `G1, G2 ~ min(Geometric(1-p), k)` and `p = exp(-epsilon)`
- **Sampling**: Bernoulli(p) coin flips from 32 random bits composed into 33-bit `sbitint`, compared against fixed-point threshold `P_SCALED = int(p * 2^32)`
- **Truncation**: `MAX_NOISE = k`, derived from (epsilon, delta), coupled with frequency threshold for formal guarantee
- **Cost per row**: 2 geometric samples x k iterations x 32 random bits = 64k random bits + 2k comparison circuits
- **Performance** (BPI 2013, eps=1.0, delta=0.01, k=4): +40% runtime overhead, +15% communication overhead

## 3. How Random Bits Work in MPC (Semi Protocol)

Understanding the cost of randomness is critical for evaluating DP improvements. Here is the full call chain from the `.mpc` DSL to the cryptographic PRNG.

### 3.1 Compiler Level

In `mpc/process_mining.mpc:553`:
```python
random_bits = [sbit.get_random_bit() for _ in range(M_BITS)]
```

`sbit.get_random_bit()` is defined in `Compiler/GC/types.py:488-491`:
```python
@staticmethod
def get_random_bit():
    res = sbit()
    inst.bitb(res)
    return res
```

This emits a `BITB` instruction (opcode `0x20d`, defined in `Compiler/GC/instructions.py:597-606`). The compiler tracks one `('bit', 'bit')` preprocessing requirement per call via `add_usage`.

### 3.2 Runtime Level

At runtime, `BITB` triggers `ShareSecret::random_bit()` (`GC/ShareSecret.hpp:360-365`):
```cpp
template<class U>
void ShareSecret<U>::random_bit()
{
    U res;
    ShareThread<U>::s().DataF.get_one(DATA_BIT, res);
    *this = res;
}
```

This fetches a preprocessed random bit share from the `DataF` (data factory) buffer.

### 3.3 Semi Protocol: Local PRNG

For the Semi protocol (semi-honest 2PC), `SemiPrep::buffer_bits()` (`GC/SemiPrep.cpp:67-74`) fills the bit buffer:
```cpp
void SemiPrep::buffer_bits()
{
    word r = secure_prng.get_word();
    for (size_t i = 0; i < sizeof(word) * 8; i++)
    {
        this->bits.push_back((r >> i) & 1);
    }
}
```

`secure_prng` is a `SeededPRNG` (`Tools/random.h:185-191`), which inherits from `PRNG` (`Tools/random.h:45-75`):

- **Algorithm**: AES-128 in counter mode (CTR)
- **Seed**: Sourced from `/dev/urandom` at initialization
- **Buffering**: 10-block cache (`N_CACHE = 10`), producing `10 * 16 = 160` bytes per AES invocation
- **Bit extraction**: Each `get_word()` call returns a 64-bit word, yielding 64 random bits

### 3.4 Cost Model

**Random bit generation is communication-free in Semi.** Each party independently generates its own random bit share using a local AES-CTR PRNG. No interaction between parties is needed because Semi is semi-honest: each party's share is simply a local random bit, and the XOR of both shares is a uniformly random bit that neither party knows.

**The actual cost comes from the comparison circuit.** The Bernoulli test `R < P_SCALED` (`process_mining.mpc:555`) compiles to a less-than comparison on 33-bit `sbitint` values. This comparison decomposes into AND gates (binary multiplications), each consuming one **Beaver triple**. Beaver triples are generated via **OT extension** (oblivious transfer), which requires communication between the parties.

For each Bernoulli trial:
- **Local cost**: 32 AES-CTR bits (negligible)
- **Communication cost**: O(log(M_BITS)) = O(5) rounds for the comparison circuit, consuming ~33 Beaver triples

For the full DP noise step (per row, k=4):
- **Random bits**: 2 x 4 x 32 = 256 local AES bits (free)
- **Beaver triples**: 2 x 4 x ~33 = ~264 triples (OT communication)
- **AND gates for running/increment logic**: 2 x 4 x ~3 = ~24 additional triples

### 3.5 Why `sbitvec.get_random_int()` Doesn't Help

One might expect `sbitvec.get_type(33).get_random_int(32)` to be more efficient. However, examining `Compiler/GC/types.py`, this method internally calls `sbit.get_random_bit()` in a loop -- it is syntactic sugar over the same `BITB` opcodes. There is **no vectorized random bit instruction** in MP-SPDZ's binary circuit backend.

## 4. Improvement Analysis

### 4.1 Current Mechanism Is Already Optimal (k-TSGD = Optimal Partition Selection)

**Status: ALREADY IMPLEMENTED. No change needed.**

**Reference**: Desfontaines, Voss, Gipson, and Mandayam, "Differentially Private Partition Selection," PoPETs 2022 [9]. Rafiei, Wangelik, and van der Aalst, "TraVaS," ICPM 2022 [10].

**Key finding**: The k-TSGD mechanism we implement IS the optimal partition selection from Desfontaines et al. [9]. TraVaS explicitly confirms this (Section 4.2, p.119): "optimality is guaranteed w.r.t. the number of variants being published due to the k-TSGD structure [4]."

The k-TSGD noise + threshold at k produces a **smooth retention probability** equivalent to the optimal primitive pi_opt(n). For a variant with true count n, the retention probability is:
```
Pr[keep] = Pr[n + k-TSGD_noise > k] = Pr[noise > k - n]
```

This is NOT a "hard threshold" -- it is a smooth function:
- n >> k: Pr[keep] -> 1 (almost certainly kept)
- n = k: Pr[keep] ≈ 0.5 (50% retention at the crossover point)
- n < k: Pr[keep] decreases smoothly but remains nonzero
- n = 0: Pr[keep] = Pr[noise > k] (small but nonzero)

The **coupling of noise truncation bound with frequency threshold** is the core insight of Desfontaines et al. that makes this mechanism optimal. Any approach that decouples these (e.g., separate partition selection + count noise) would be provably worse for maximizing released variants.

### 4.2 Why Pure Epsilon-DP Is Impossible Here

**Status: Not applicable. Fundamental impossibility result.**

**Reference**: Desfontaines et al. [9], Theorem 2. Also `docs/differential_privacy.md`, Section "Why Pure epsilon-DP Is Insufficient."

Pure epsilon-DP requires the output distribution to change by at most e^epsilon when any single record is added or removed. Since adding a case can create a **new variant** (probability 0 -> nonzero), no finite epsilon suffices. Any mechanism releasing variant identities from an unbounded domain must use (epsilon, delta)-DP with delta > 0.

TraVaS (Section 6, p.125) explicitly notes this limitation: "the differentially private partition selection mechanism only works for delta > 0."

### 4.4 Multithreaded DP Noise Loop

**Status: IMPLEMENTED.** Changed `@for_range_opt(TOTAL_N)` to `@for_range_opt_multithread(N_THREADS, TOTAL_N)` at `process_mining.mpc:565`.

Each row's noise is independent — no `MemValue`, no cross-iteration dependencies, disjoint array accesses per thread. Safe to parallelize.

**Measured results** (BPI 2013, 16 threads, delta=0.01):

| Config | Sequential DP step (s) | Multithreaded DP step (s) | Speedup | Runtime overhead |
|--------|------------------------|---------------------------|---------|------------------|
| DP eps=0.1 (k=18) | 5.2 | 1.7 | **3.1x** | +99% → +33% |
| DP eps=0.5 (k=7) | 2.8 | 0.9 | **3.1x** | +54% → +18% |
| DP eps=1.0 (k=4) | 2.1 | 0.7 | **3.0x** | +40% → +14% |
| DP eps=2.0 (k=3) | 1.8 | 0.6 | **3.0x** | +35% → +13% |
| DP eps=5.0 (k=1) | 1.2 | 0.4 | **3.0x** | +23% → +8% |

Consistent **3x speedup** on the DP noise step. Communication unchanged (same Beaver triples, batched per thread). Compile time slightly higher (~5s) due to per-thread circuit copies.

### 4.5 Bit-Decomposed Geometric Sampling

**Status: IMPLEMENTED.** Adaptive selection at compile time (`process_mining.mpc:546-577`).

**Reference**: Ghosh, Roughgarden, and Sundararajan, STOC 2009 [13]. Champion, Shelat, and Ullman, CCS 2019 [5]. Fu and Wang, CCS 2024 [14].

**Key insight**: Bit j of a Geometric(1-p) random variable is an **independent** Bernoulli with probability p_j = p^(2^j) / (1 + p^(2^j)). Instead of k sequential coin flips, directly sample n_bits = ceil(log2(k+1)) independent bits and compose.

**Precision requirement**: Sampling only n_bits bits introduces an approximation error proportional to p^(2^n_bits). To match the existing 32-bit Bernoulli precision (epsilon degradation < 10^-7):
```
n_bits >= ceil(log2(32 * ln(2) / epsilon))
```
Plus 1 for the clamp comparison (handling values above MAX_NOISE). This makes the approach **beneficial only when n_bits + 1 < k** — i.e., for small epsilon where k is large.

**Measured results** (BPI 2013, 16 threads, delta=0.01):

| eps | k | Method selected | n_bits | Data Sent (MB) | vs sequential-only |
|-----|---|----------------|--------|----------------|-------------------|
| 0.1 | 18 | bit-decomposed | 8 | 558.7 | **-15.8%** |
| 0.5 | 7 | sequential | - | 542.3 | 0% |
| 1.0 | 4 | sequential | - | 507.5 | 0% |
| 2.0 | 3 | sequential | - | 499.2 | 0% |
| 5.0 | 1 | sequential | - | 473.1 | 0% |

At eps=0.1 (k=18): 8 biased coins + 1 clamp = 9 operations vs 18 sequential trials, saving 104.9 MB (-15.8%) in communication. For eps >= 0.5, the precision requirement (n_bits + 1 >= k) makes bit-decomposed equal or worse, so sequential is automatically selected.

**Why the benefit is limited to small epsilon**: For large epsilon, p = e^(-eps) is small, the geometric is concentrated near 0, and k is already small (1-7 trials). The precision bits needed to cover the geometric tail exceed k, making the bit-decomposed approach more expensive. Conversely, for small epsilon, p is close to 1, the tail is heavier, but k is much larger, so the O(log k) savings dominate.

### 4.6 Subsampling Amplification

**Status: NOT IMPLEMENTED.**

**Reference**: Wang, Balle, Kasiviswanathan, "Privacy Amplification by Subsampling," ICML 2016 [11].

**Idea**: If each case is included independently with probability q (Poisson subsampling), privacy amplifies:
```
epsilon' = ln(1 + q * (e^epsilon - 1))
```

| q (subsample rate) | epsilon (original) | epsilon' (amplified) | Amplification |
|---|---|---|---|
| 1.0 | 1.0 | 1.0 | 1x |
| 0.5 | 1.0 | 0.62 | 1.6x |
| 0.1 | 1.0 | 0.105 | 9.5x |
| 0.01 | 1.0 | 0.017 | 60x |

**Application**: Each party subsamples cases before MPC input. Valid because:
1. Subsampling happens before MPC -- no circuit changes
2. Adversary doesn't know which records are subsampled (inputs are secret-shared)
3. Semi-honest model guarantees honest subsampling

**Utility trade-off**: Subsampling reduces counts proportionally. For BPI 2013 (1638 cases, 104 variants):
- q=0.5: ~819 cases, count-1 variants disappear ~50% of the time
- q=0.1: ~164 cases, only the 10-20 most common variants survive

**Implementation**: Add `--subsample-rate q` flag to `pipeline/run.py`. In `pipeline/import_xes.py`, randomly include each case with probability q before encoding. No MPC code changes.

**Recommendation: Implement as a user-facing option.** Low effort, dramatic privacy amplification for users willing to trade utility.

### 4.7 Finite-Precision Noise Analysis

**Status: ALREADY ADEQUATE. Documentation only.**

**Reference**: Keller et al., EPRINT 2023/1594 [12].

The 32-bit fixed-point Bernoulli parameter introduces a rounding error of at most 2^(-32) per trial. Over k trials, the total privacy degradation is bounded by:
```
epsilon_actual <= epsilon + k * 2^(-32) / min(p, 1-p)
```

For all practical parameters, this is less than 10^(-7) -- entirely negligible.

**Recommendation: No code changes.** Include the formal precision analysis in the thesis.

## 5. Interaction with Other System Features

### 5.1 Partial Orders

Partial orders (see `docs/partial_orders.md`) merge permutation-equivalent traces, **reducing the number of distinct variants** and **increasing per-variant counts**. This is synergistic with DP: fewer variants with higher counts means the threshold k suppresses fewer variants and noise has less relative impact.

### 5.2 Handover Optimization

Handovers reduce TOTAL_N (fewer padding rows), directly reducing DP noise cost which scales as O(TOTAL_N * k). On BPI 2013 with handovers: -42.9% communication implies similar reduction in DP overhead.

### 5.3 k-Anonymity Interaction

The k-anonymity suppression (`ENABLE_K_ANON`) is separate from DP. If k-anon threshold > DP threshold k, k-anonymity dominates suppression. DP still adds noise to counts. The two guarantees are independent and do not compose formally.

## 6. Implementation Roadmap

### Done

1. **Multithreaded noise loop** (Section 4.4): 3x speedup on DP noise step.
3. **Bit-decomposed geometric sampling** (Section 4.5): -15.8% communication at eps=0.1. Auto-selects at compile time.

### User option

4. **Subsampling amplification** (Section 4.6): `--subsample-rate` flag.

## 7. References

References [1]-[10] are defined in `docs/differential_privacy.md`.

[11] Y.-X. Wang, S. Balle, and S. Kasiviswanathan, "Privacy Amplification by Subsampling," in *ICML*, 2016. -- Tight amplification bounds for Poisson and uniform subsampling.

[12] M. Keller, "Secure Noise Sampling for DP in MPC with Finite Precision," IACR ePrint 2023/1594. -- Formal bounds on precision-induced privacy degradation.

[13] A. Ghosh, T. Roughgarden, and M. Sundararajan, "Universally Utility-Maximizing Privacy Mechanisms," in *STOC*, 2009. -- Proves discrete Laplace (geometric mechanism) is universally optimal for sensitivity-1 counting queries under pure epsilon-DP.

[14] M. Fu and Y. Wang, "Benchmarking Secure Sampling Protocols for Differential Privacy," in *ACM CCS*, 2024. -- Benchmarks 8 MPC sampling protocols in MP-SPDZ; identifies bit-decomposed (ODO) Laplace as most efficient for small truncation bounds.
