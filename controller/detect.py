"""Detectors. All act on populations over windows, never on single calls.

Caller ID: a number's pickup is compared with the rest of the pool *hour by hour* (indirect
standardisation): expected connects = its dials in each hour x the others' pickup in that hour. Caller ID
is randomised per call, so within an hour the comparison is like-for-like; hour-matching removes the bias
from a number that only dialled in a good or bad part of the day.

Hallucination: binomial CUSUM on hourly flag counts for the live prompt, p0 = clean history flag rate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import params as P
from .context import WfCtx
from .stats import cusum_binomial


@dataclass
class DidStat:
    did: str
    dials: int
    conn: int
    expected: float     # connects expected at the others' hour-matched pickup

    @property
    def ratio(self) -> float:
        return self.conn / self.expected if self.expected > 0 else 1.0

    def z_below(self, ratio: float = 1.0) -> float:
        """How many se the observed connects sit below `ratio` x expected (positive = worse)."""
        e = self.expected * ratio
        if e <= 0 or self.dials <= 0:
            return 0.0
        p = min(max(e / self.dials, 1e-6), 1 - 1e-6)
        return (e - self.conn) / math.sqrt(self.dials * p * (1 - p))

    def as_dict(self) -> dict:
        return {"did": self.did, "dials": self.dials, "connected": self.conn,
                "expected": round(self.expected, 1), "ratio": round(self.ratio, 3), "z": round(self.z_below(), 2)}


def did_window(ctx: WfCtx, since_hour_idx: int, n_hours: int | None = None,
               pool: set[str] | None = None) -> dict[str, DidStat]:
    """Per-DID stats over hour buckets >= since_hour_idx (optionally only the last n dialled hours).

    `pool` = the reference numbers a DID is compared with (default: every other number). A flagged number left
    in the reference would make a mediocre probe look healthy, so planners pass the proven numbers only."""
    rows = ctx.store.q("SELECT hour_idx, did, dials, conn FROM did_counts WHERE wf=? AND hour_idx>=? "
                       "AND hour_idx < ? ORDER BY hour_idx DESC, did", (ctx.wf, since_hour_idx, ctx.now // 60 + 1))
    by_hour: dict[int, dict[str, tuple[int, int]]] = {}
    for h, did, n, c in rows:
        if n_hours is not None and h not in by_hour and len(by_hour) >= n_hours:
            continue
        by_hour.setdefault(h, {})[did] = (n, c)
    tot: dict[str, list] = {}
    for h, cells in by_hour.items():
        ref = {d: v for d, v in cells.items() if pool is None or d in pool}
        all_n = sum(n for n, _ in ref.values())
        all_c = sum(c for _, c in ref.values())
        for did, (n, c) in cells.items():
            on, oc = (all_n - n, all_c - c) if did in ref else (all_n, all_c)
            if on <= 0:          # nothing proven to compare with (e.g. a single-number pool): use everyone else
                on = sum(x for d2, (x, _) in cells.items() if d2 != did)
                oc = sum(y for d2, (_, y) in cells.items() if d2 != did)
            t = tot.setdefault(did, [0, 0, 0.0, 0])
            t[0] += n
            t[1] += c
            if on > 0:
                t[2] += n * oc / on
                t[3] += n
    out = {}
    for did, (n, c, e, matched) in tot.items():
        # only the matched dials carry an expectation; scale observed to the matched share
        if matched == 0:
            out[did] = DidStat(did, n, c, 0.0)
        else:
            out[did] = DidStat(did, matched, round(c * matched / n), e)
    return out


def did_flags(ctx: WfCtx, active: list[str], pool: set[str] | None = None) -> list[tuple[str, dict]]:
    """Severely degraded numbers among those currently in use: ratio < 0.6, z > 5, >= 300 dials."""
    hi = ctx.now // 60
    flagged = []
    for n_hours in (P.DID_FAST_WINDOW_HOURS, P.DID_SLOW_WINDOW_HOURS):
        stats = did_window(ctx, hi - 48, n_hours, pool)
        for did in active:
            s = stats.get(did)
            if s and s.dials >= P.DID_FLAG_MIN_DIALS and s.ratio < P.DID_FLAG_RATIO and s.z_below() > P.DID_FLAG_Z:
                if did not in [f for f, _ in flagged]:
                    flagged.append((did, {"window_hours": n_hours, **s.as_dict()}))
    return flagged


# --------------------------------------------------------------------------- hallucination
def flag_baseline(ctx: WfCtx) -> tuple[float, float]:
    """(p0, h): clean flag rate from history and a CUSUM threshold calibrated on that history."""
    cached = ctx.store.get_meta(f"flag_baseline:{ctx.wf}")
    if cached:
        return cached[0], cached[1]
    hrows = ctx.store.q("SELECT hour_idx, conn, flag FROM prompt_counts WHERE wf=? AND hour_idx < 0 ORDER BY hour_idx",
                        (ctx.wf,))
    rows = [(c, f) for _, c, f in hrows]
    n_days = len({h // 24 for h, _, _ in hrows}) or 1
    n = sum(r[0] for r in rows)
    k = sum(r[1] for r in rows)
    p0 = (k + 1) / (n + 100) if n else 0.015
    _, _, smax = cusum_binomial(rows, p0, p0 * P.FLAG_P1_MULT)
    per_hour = n / len(rows) if rows else 50.0
    h = max(P.CUSUM_H_MIN, P.CUSUM_H_HISTORY_MULT * smax, calibrate_h(ctx.wf, p0, per_hour, len(rows) / n_days))
    ctx.store.set_meta(f"flag_baseline:{ctx.wf}", [p0, h])
    return p0, h


def calibrate_h(wf: str, p0: float, conn_per_hour: float, hours_per_day: float) -> float:
    """Smallest h with P(any false alarm within 30 days of clean traffic) <= 1%, by seeded simulation.

    A fixed h is wrong at scale: the null walk has negative drift, but more calls per day means more
    chances to cross. Seeded by the workflow id, so the threshold is reproducible (hard rule 8).
    """
    import hashlib
    import numpy as np
    seed = int.from_bytes(hashlib.sha256(f"cusum|{wf}".encode()).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    steps = int(30 * max(hours_per_day, 1))
    n = max(int(round(conn_per_hour)), 1)
    maxima = []
    for _ in range(P.CUSUM_CALIBRATION_PATHS):
        ks = rng.binomial(n, p0, steps)
        _, _, smax = cusum_binomial(((n, int(x)) for x in ks), p0, p0 * P.FLAG_P1_MULT)
        maxima.append(smax)
    maxima.sort()
    return float(maxima[int(P.CUSUM_QUANTILE * (len(maxima) - 1))])


def prompt_live_since(ctx: WfCtx) -> int:
    """Minute the current prompt became live (start of the run of versions carrying it)."""
    rows = ctx.store.q("SELECT version, applied_minute, config_json FROM config_versions WHERE wf=? "
                       "ORDER BY version DESC", (ctx.wf,))
    since = None
    for v, m, cj in rows:
        if not cj:
            continue
        import json
        if json.loads(cj)["prompt_version"] != ctx.cfg["prompt_version"]:
            break
        since = m
    return since if since is not None else -10 ** 9


def hallucination_cusum(ctx: WfCtx) -> dict:
    p0, h = flag_baseline(ctx)
    since = max(prompt_live_since(ctx), ctx.store.get_meta(f"cusum_reset:{ctx.wf}", -10 ** 9))
    rows = ctx.store.q("SELECT conn, flag FROM prompt_counts WHERE wf=? AND prompt=? AND hour_idx >= ? "
                       "ORDER BY hour_idx", (ctx.wf, ctx.cfg["prompt_version"], max(since // 60, 0)))
    s, first, _ = cusum_binomial(rows, p0, p0 * P.FLAG_P1_MULT, h)
    n = sum(r[0] for r in rows)
    k = sum(r[1] for r in rows)
    return {"prompt": ctx.cfg["prompt_version"], "S": round(s, 2), "h": round(h, 2), "alarm": first is not None,
            "connected": n, "flagged": k, "flag_rate": round(k / n, 4) if n else None, "p0": round(p0, 4)}


def prompt_record(ctx: WfCtx, prompt: str) -> tuple[int, int]:
    """(connected, flagged) for a prompt across all its live periods, history included."""
    r = ctx.store.q1("SELECT COALESCE(SUM(conn),0), COALESCE(SUM(flag),0) FROM prompt_counts WHERE wf=? AND prompt=?",
                     (ctx.wf, prompt))
    return int(r[0]), int(r[1])
