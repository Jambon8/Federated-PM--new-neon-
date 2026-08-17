"""Finite-grid calibration for the MPC k-TSGD partition-selection sampler.

The ideal threshold formula is due to Desfontaines et al. and is used by
TraVaS.  The MPC sampler rounds the ideal CDF breakpoints to a public B-bit
grid.  Calibration therefore reserves enough of the requested delta budget
for the resulting total-variation error.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN, localcontext
from fractions import Fraction


DEFAULT_GRID_BITS = 32
_PRECISION = 90


def _decimal(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(str(value))


def ideal_dp_k(epsilon, delta):
    """Return the ideal k-TSGD partition-selection threshold."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        eps = _decimal(epsilon)
        delt = _decimal(delta)
        if eps <= 0 or not (Decimal(0) < delt < Decimal(1)):
            raise ValueError(f"Invalid DP parameters: epsilon={epsilon}, delta={delta}")
        exp_eps = eps.exp()
        numerator = exp_eps + 2 * delt - 1
        denominator = delt * (exp_eps + 1)
        if numerator <= 0 or denominator <= 0:
            raise ValueError(f"Invalid DP parameters: epsilon={epsilon}, delta={delta}")
        raw = (numerator / denominator).ln() / eps
        return max(1, int(raw.to_integral_value(rounding=ROUND_CEILING)))


def grid_tv_bound(k, grid_bits=DEFAULT_GRID_BITS):
    """Conservative TV bound for 2k rounded/clamped CDF breakpoints."""
    if k < 1 or grid_bits < 1:
        raise ValueError("k and grid_bits must be positive")
    return Decimal(2 * k) / Decimal(2 ** grid_bits)


@dataclass(frozen=True)
class DPCalibration:
    epsilon: Decimal
    requested_delta: Decimal
    ideal_delta: Decimal
    k: int
    grid_bits: int
    grid_tv_bound: Decimal
    grid_delta_reserve: Decimal


def calibrate_dp(epsilon, delta, grid_bits=DEFAULT_GRID_BITS):
    """Calibrate k while reserving finite-grid error from requested delta.

    If the ideal mechanism uses delta_0 and every input distribution is within
    TV distance eta of it, the grid mechanism is
    (epsilon, delta_0 + (1 + exp(epsilon)) eta)-DP.  Since eta depends on k,
    this routine iterates the integer calibration to a fixed point.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        eps = _decimal(epsilon)
        requested = _decimal(delta)
        if eps <= 0 or not (Decimal(0) < requested < Decimal(1)):
            raise ValueError(f"Invalid DP parameters: epsilon={epsilon}, delta={delta}")

        k = ideal_dp_k(eps, requested)
        exp_eps = eps.exp()
        for _ in range(100):
            eta = grid_tv_bound(k, grid_bits)
            reserve = (1 + exp_eps) * eta
            ideal_delta = requested - reserve
            if ideal_delta <= 0:
                raise ValueError(
                    "The requested delta is too small for the finite-grid "
                    f"sampler at B={grid_bits}"
                )
            next_k = ideal_dp_k(eps, ideal_delta)
            if next_k == k:
                return DPCalibration(
                    epsilon=+eps,
                    requested_delta=+requested,
                    ideal_delta=+ideal_delta,
                    k=k,
                    grid_bits=grid_bits,
                    grid_tv_bound=+eta,
                    grid_delta_reserve=+reserve,
                )
            k = next_k
    raise RuntimeError("DP finite-grid calibration did not converge")


def compute_dp_k(epsilon, delta, grid_bits=DEFAULT_GRID_BITS):
    """Compatibility helper returning the repaired finite-grid threshold."""
    return calibrate_dp(epsilon, delta, grid_bits).k


def cdf_thresholds(epsilon, k, grid_bits=DEFAULT_GRID_BITS):
    """Return the exact public integer threshold table used by the MPC code."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        eps = _decimal(epsilon)
        if eps <= 0 or k < 1:
            raise ValueError("epsilon and k must be positive")
        q = (-eps).exp()
        z = (1 + q - 2 * q ** (k + 1)) / (1 - q)
        scale = Decimal(2 ** grid_bits)
        maximum = 2 ** grid_bits - 1
        acc = Decimal(0)
        thresholds = []
        for x in range(-k, k):
            acc += q ** abs(x) / z
            threshold = int((acc * scale).to_integral_value(rounding=ROUND_HALF_EVEN))
            thresholds.append(min(maximum, max(1, threshold)))
        return thresholds


def implemented_mass_counts(thresholds, grid_bits=DEFAULT_GRID_BITS):
    """Return exact grid-point counts for the sampler's output buckets."""
    scale = 2 ** grid_bits
    if not thresholds:
        raise ValueError("At least one threshold is required")
    if thresholds != sorted(thresholds):
        raise ValueError("Thresholds must be non-decreasing")
    if thresholds[0] < 1 or thresholds[-1] >= scale:
        raise ValueError("Thresholds must lie strictly inside the grid")
    return [thresholds[0]] + [b - a for a, b in zip(thresholds, thresholds[1:])] + [scale - thresholds[-1]]


def implemented_tv_distance(epsilon, k, grid_bits=DEFAULT_GRID_BITS):
    """Compute the implemented PMF's TV distance from ideal k-TSGD."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        eps = _decimal(epsilon)
        q = (-eps).exp()
        z = (1 + q - 2 * q ** (k + 1)) / (1 - q)
        masses = implemented_mass_counts(cdf_thresholds(eps, k, grid_bits), grid_bits)
        scale = Decimal(2 ** grid_bits)
        total = Decimal(0)
        for x, mass in zip(range(-k, k + 1), masses):
            total += abs(Decimal(mass) / scale - q ** abs(x) / z)
        return +(total / 2)
