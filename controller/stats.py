"""Deterministic statistics. No randomness, no LLM.

Lift of an arm over its control is estimated on the log scale *within strata* (day x config cohort) and
combined with inverse-variance weights. Pooling across strata would confound allocation changes with
day-to-day mix shifts (Simpson); stratifying keeps each comparison inside one randomised block.
Each stratum uses Beta(1,1)-smoothed rates, so tiny cells don't explode.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class Lift:
    est: float      # log(p_arm / p_ctrl)
    se: float
    n_arm: int
    n_ctrl: int
    days: int       # distinct days with both arms present

    def p_gt(self, delta: float) -> float:
        """P(relative lift > delta) under the normal approximation (flat prior on log-lift)."""
        if self.se <= 0:
            return 1.0 if self.est > math.log1p(delta) else 0.0
        return 1.0 - phi((math.log1p(delta) - self.est) / self.se)

    def p_lt(self, delta: float) -> float:
        return 1.0 - self.p_gt(delta)

    @property
    def rel(self) -> float:
        return math.expm1(self.est)

    def interval(self, z: float = 1.2816) -> tuple[float, float]:   # 80% two-sided
        return math.expm1(self.est - z * self.se), math.expm1(self.est + z * self.se)


def stratified_lift(strata) -> Lift | None:
    """strata: iterable of (day, n_arm, e_arm, n_ctrl, e_ctrl). Strata missing either side are skipped."""
    sw = swy = 0.0
    na = nc = 0
    days = set()
    for day, n_a, e_a, n_c, e_c in strata:
        if n_a <= 0 or n_c <= 0:
            continue
        pa = (e_a + 1.0) / (n_a + 2.0)
        pc = (e_c + 1.0) / (n_c + 2.0)
        var = (1 - pa) / (pa * (n_a + 2.0)) + (1 - pc) / (pc * (n_c + 2.0))
        w = 1.0 / var
        sw += w
        swy += w * math.log(pa / pc)
        na += n_a
        nc += n_c
        days.add(day)
    if sw == 0:
        return None
    return Lift(swy / sw, math.sqrt(1.0 / sw), na, nc, len(days))


def shrink(segments: dict[str, Lift], overall: Lift) -> dict[str, Lift]:
    """Empirical-Bayes shrinkage of segment lifts toward the overall lift (DerSimonian-Laird tau^2)."""
    ks = [k for k, v in segments.items() if v is not None and v.se > 0]
    if len(ks) < 2:
        return dict(segments)
    y = [segments[k].est for k in ks]
    v = [segments[k].se ** 2 for k in ks]
    w = [1 / x for x in v]
    sw = sum(w)
    ybar = sum(wi * yi for wi, yi in zip(w, y)) / sw
    q = sum(wi * (yi - ybar) ** 2 for wi, yi in zip(w, y))
    denom = sw - sum(wi * wi for wi in w) / sw
    tau2 = max(0.0, (q - (len(ks) - 1)) / denom) if denom > 0 else 0.0
    out = dict(segments)
    for k, yi, vi in zip(ks, y, v):
        b = vi / (tau2 + vi) if tau2 + vi > 0 else 1.0     # weight on the overall estimate
        est = (1 - b) * yi + b * overall.est
        var = (1 - b) * vi + b * b * overall.se ** 2   # normal-normal posterior var + uncertainty in the mean
        s = segments[k]
        out[k] = Lift(est, math.sqrt(max(var, 1e-12)), s.n_arm, s.n_ctrl, s.days)
    return out


def bh(pvals: list[float], q: float) -> list[bool]:
    """Benjamini-Hochberg: which hypotheses are rejected at FDR q."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (pvals[i], i))
    k_max = 0
    for rank, i in enumerate(order, 1):
        if pvals[i] <= q * rank / m:
            k_max = rank
    keep = set(order[:k_max])
    return [i in keep for i in range(m)]


def cusum_binomial(counts, p0: float, p1: float, h: float = math.inf):
    """counts: iterable of (n, k) in time order. Returns (final S, index of first alarm or None, max S)."""
    p0 = min(max(p0, 1e-6), 0.5)
    p1 = min(max(p1, p0 * 1.01), 0.9)
    a = math.log(p1 / p0)
    b = math.log((1 - p1) / (1 - p0))
    s = smax = 0.0
    first = None
    for i, (n, k) in enumerate(counts):
        s = max(0.0, s + k * a + (n - k) * b)
        smax = max(smax, s)
        if first is None and s > h:
            first = i
    return s, first, smax


def two_prop_z(n1: int, k1: int, n2: int, k2: int) -> float:
    """z for p2 > p1 (positive when sample 1 looks worse)."""
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p) * (1 / n1 + 1 / n2), 1e-12))
    return (k2 / n2 - k1 / n1) / se


def min_detectable_lift(n: int, base: float = 0.55, z_alpha: float = 1.645, z_power: float = 0.8416) -> float:
    """Relative lift detectable with n per arm (one-sided alpha, 80% power). Used in docs and dry runs."""
    d = (z_alpha + z_power) * math.sqrt(2 * base * (1 - base) / n)
    return d / base
