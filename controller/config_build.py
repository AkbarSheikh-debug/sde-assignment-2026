"""Config arithmetic: normalise, compare, diff, measure shift, interpolate a partial step.

`changed_dims` and `max_shift` follow the contract in docs/SIMULATOR.md 4.2 (total-variation distance per
cohort and agent dimension), so our auto-apply routing agrees with the simulator's policy check.
"""
from __future__ import annotations

import copy

AGENT_DIMS = ("welcome_message", "voice", "caller_id")
CHANGE_DIMS = ("cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule")


def clean_dist(d: dict, ndigits: int = 6) -> dict:
    """Normalise weights, drop zeros, round so canonical JSON (and proposal ids) are stable."""
    pos = {k: float(v) for k, v in d.items() if v and v > 1e-9}
    s = sum(pos.values())
    if s <= 0:
        raise ValueError(f"empty distribution {d}")
    out = {k: round(v / s, ndigits) for k, v in sorted(pos.items())}
    # put rounding residue on the largest weight so the sum is exactly 1 to 1e-6
    top = max(out, key=lambda k: (out[k], k))
    out[top] = round(out[top] + 1.0 - sum(out.values()), ndigits)
    return {k: v for k, v in out.items() if v > 0}


def normalise(cfg: dict) -> dict:
    out = {"cohorts": [{"id": c["id"], "where": {f: list(v) for f, v in c["where"].items()}}
                       for c in cfg.get("cohorts", [])],
           "allocations": {}, "prompt_version": cfg["prompt_version"],
           "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
    for key, dims in cfg["allocations"].items():
        out["allocations"][key] = {d: clean_dist(w) for d, w in dims.items() if w}
    sched = cfg.get("schedule") or {}
    for key, table in (sched.get("hour_weights") or {}).items():
        out["schedule"]["hour_weights"][key] = {str(int(h)): round(float(w), 4) for h, w in table.items()}
    out["schedule"]["lead_preferred_hour"] = dict(sched.get("lead_preferred_hour") or {})
    return out


def dist(cfg: dict, cohort: str, dim: str) -> dict:
    return (cfg["allocations"].get(cohort) or {}).get(dim) or cfg["allocations"]["default"][dim]


def _close(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) < 1e-9 for k in keys)


def changed_dims(old: dict, new: dict) -> list[str]:
    ch = set()
    if old["cohorts"] != new["cohorts"]:
        ch.add("cohorts")
    keys = set(old["allocations"]) | set(new["allocations"])
    for d in AGENT_DIMS:
        if any(not _close(dist(old, k, d), dist(new, k, d)) for k in keys):
            ch.add(d)
    if old["prompt_version"] != new["prompt_version"]:
        ch.add("prompt_version")
    if _sched_key(old) != _sched_key(new):
        ch.add("schedule")
    return [d for d in CHANGE_DIMS if d in ch]


def _sched_key(cfg):
    s = cfg.get("schedule") or {}
    hw = {k: {str(int(h)): float(w) for h, w in t.items()} for k, t in (s.get("hour_weights") or {}).items()}
    return hw, s.get("lead_preferred_hour") or {}


def tv(a: dict, b: dict) -> float:
    return 0.5 * sum(abs(a.get(o, 0.0) - b.get(o, 0.0)) for o in set(a) | set(b))


def max_shift(old: dict, new: dict) -> float:
    keys = {"default"} | {c["id"] for c in old["cohorts"]} | {c["id"] for c in new["cohorts"]}
    return max(tv(dist(old, k, d), dist(new, k, d)) for k in keys for d in AGENT_DIMS)


def step_toward(old: dict, target: dict, limit: float) -> dict:
    """Move allocations from old toward target by a fraction so that max_shift <= limit.

    Only valid when cohorts are identical (TV scales linearly under interpolation). Schedule and prompt are
    taken from target as-is (they are not distributions).
    """
    sh = max_shift(old, target)
    if sh <= limit + 1e-12:
        return target
    alpha = max(0.0, limit / sh - 1e-6)
    out = copy.deepcopy(target)
    for k in set(old["allocations"]) | set(target["allocations"]):
        for d in AGENT_DIMS:
            a, b = dist(old, k, d), dist(target, k, d)
            if k != "default" and d not in (target["allocations"].get(k) or {}) \
                    and d not in (old["allocations"].get(k) or {}):
                continue
            mix = {o: a.get(o, 0.0) + alpha * (b.get(o, 0.0) - a.get(o, 0.0)) for o in set(a) | set(b)}
            out["allocations"].setdefault(k, {})[d] = clean_dist(mix, 6)
    # rounding can nudge TV a hair above the limit; shave alpha until it fits
    while max_shift(old, out) > limit + 1e-9 and alpha > 0:
        alpha *= 0.98
        for k in out["allocations"]:
            for d in list(out["allocations"][k]):
                if d not in AGENT_DIMS:
                    continue
                a, b = dist(old, k, d), dist(target, k, d)
                mix = {o: a.get(o, 0.0) + alpha * (b.get(o, 0.0) - a.get(o, 0.0)) for o in set(a) | set(b)}
                out["allocations"][k][d] = clean_dist(mix, 6)
    return out


def diff(old: dict, new: dict) -> dict:
    """Readable per-cohort/per-dim diff (free-form in the contract; supervisors read it)."""
    out: dict = {}
    oc = {c["id"]: c["where"] for c in old["cohorts"]}
    nc = {c["id"]: c["where"] for c in new["cohorts"]}
    added = [{"id": k, "where": nc[k]} for k in nc if k not in oc]
    removed = [k for k in oc if k not in nc]
    if added or removed or [c["id"] for c in old["cohorts"]] != [c["id"] for c in new["cohorts"]]:
        out["cohorts"] = {"added": added, "removed": removed, "order": list(nc)}
    for k in ["default"] + list(nc):
        for d in AGENT_DIMS:
            a, b = dist(old, k, d) if k in old["allocations"] or k == "default" else None, dist(new, k, d)
            if k != "default" and d not in (new["allocations"].get(k) or {}):
                continue
            if a is None or not _close(a, b):
                out[f"allocations.{k}.{d}"] = {"from": a, "to": b}
    if old["prompt_version"] != new["prompt_version"]:
        out["prompt_version"] = {"from": old["prompt_version"], "to": new["prompt_version"]}
    ohw = (old.get("schedule") or {}).get("hour_weights") or {}
    nhw = (new.get("schedule") or {}).get("hour_weights") or {}
    for k in sorted(set(ohw) | set(nhw)):
        if ohw.get(k) != nhw.get(k):
            out[f"schedule.hour_weights.{k}"] = {"from": ohw.get(k), "to": nhw.get(k)}
    return out


def same(a: dict, b: dict) -> bool:
    return not changed_dims(a, b)


def _where_key(cohorts):
    return [(c["id"], sorted((f, sorted(v)) for f, v in c["where"].items())) for c in cohorts]


def reuse_exact(current: dict, cand: dict) -> dict:
    """Where the candidate equals the live config up to rounding, copy the live bytes.

    The simulator compares distributions with `!=`; a re-rounded but unchanged weight would otherwise count
    as a changed dimension (and make an auto-apply fail with 403).
    """
    out = copy.deepcopy(cand)
    if _where_key(current["cohorts"]) == _where_key(out["cohorts"]):
        out["cohorts"] = copy.deepcopy(current["cohorts"])
    for k, dims in out["allocations"].items():
        for d, w in list(dims.items()):
            cur = (current["allocations"].get(k) or {}).get(d)
            if cur is not None and _close(cur, w):
                dims[d] = copy.deepcopy(cur)
    chw = (current.get("schedule") or {}).get("hour_weights") or {}
    nhw = out.setdefault("schedule", {}).setdefault("hour_weights", {})
    for k, t in list(nhw.items()):
        if k in chw and set(map(str, chw[k])) == set(map(str, t)) and \
                all(abs(float(chw[k][h]) - float(t[h])) < 1e-3 for h in t):
            nhw[k] = copy.deepcopy(chw[k])
    if not out["schedule"].get("lead_preferred_hour"):
        out["schedule"]["lead_preferred_hour"] = copy.deepcopy(
            (current.get("schedule") or {}).get("lead_preferred_hour") or {})
    return out
