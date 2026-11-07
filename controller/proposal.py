"""What a supervisor sees: summary, diff, affected leads, dry run, impact score, rationale.

Honesty rules:
- dry_run replays the last 3 days of *our own* matching calls; arms with no data are shown as unknown
  (prior 0 +/- 10%), never as a guess dressed up as evidence.
- impact_score = clip(10 x expected relative change in daily concluded leads x P(benefit), 0, 1), so 0.5
  means "about a 5% lift we are fairly sure of". Safety fixes are 1.0 by definition, and say so.
- affected_leads = active leads in touched cohorts (+ the expected share of tomorrow's batch). With
  mid_flight=next_campaign_only, agent-facing changes reach only new campaigns and we count only those.
"""
from __future__ import annotations

import math

from . import params as P
from .cohorts import matches
from .config_build import AGENT_DIMS, changed_dims, diff, dist
from .context import WfCtx
from .stats import phi, stratified_lift

DRY_DAYS = 3
UNKNOWN_SE = 0.10


def _scope_feats_for_cohort(ctx: WfCtx, cfg: dict, cohort: str) -> set[int]:
    """Feature codes whose first-matching cohort in cfg is `cohort`."""
    out = set()
    for code, feats in ctx.combos.items():
        first = next((c["id"] for c in cfg["cohorts"] if matches(c["where"], feats)), "default")
        if first == cohort:
            out.add(code)
    return out


def touched_feats(ctx: WfCtx, old: dict, new: dict) -> set[int]:
    dims = changed_dims(old, new)
    if "prompt_version" in dims or "cohorts" in dims:
        return set(ctx.combos)
    out: set[int] = set()
    keys = {"default"} | {c["id"] for c in new["cohorts"]}
    for k in keys:
        if any(dist(old, k, d) != dist(new, k, d) for d in AGENT_DIMS) or \
                (old.get("schedule", {}).get("hour_weights", {}).get(k) !=
                 new.get("schedule", {}).get("hour_weights", {}).get(k)):
            out |= _scope_feats_for_cohort(ctx, new, k)
    return out


def affected_leads(ctx: WfCtx, old: dict, new: dict) -> tuple[int, dict]:
    feats = touched_feats(ctx, old, new)
    dims = changed_dims(old, new)
    cad = len(ctx.spec["cadence"])
    rows = ctx.store.q("SELECT feat, SUM(leads - concluded) FROM camp_counts WHERE wf=? AND cday >= ? GROUP BY feat",
                       (ctx.wf, ctx.day - cad + 1))
    active = sum(n for f, n in rows if f in feats)
    mix = ctx.store.q("SELECT feat, SUM(leads) FROM camp_counts WHERE wf=? AND cday >= ? GROUP BY feat",
                      (ctx.wf, ctx.day - 3))
    tot = sum(n for _, n in mix) or 1
    share = sum(n for f, n in mix if f in feats) / tot
    tomorrow = int(round(ctx.spec["daily_batch"]["size"] * share))
    agent_only = set(dims) <= {"cohorts", "welcome_message", "voice", "caller_id"}
    if ctx.spec.get("mid_flight") == "next_campaign_only" and agent_only:
        return tomorrow, {"active_leads_in_scope": active, "next_batch_in_scope": tomorrow,
                          "note": "next_campaign_only: running campaigns keep their config; only new batches change"}
    return active + tomorrow, {"active_leads_in_scope": active, "next_batch_in_scope": tomorrow}


def _arm_lift(ctx: WfCtx, dim: str, feats: set[int], arm: str, ctrl: str):
    rows = ctx.store.q("SELECT day, feat, cohort, arm, conn, eng FROM arm_counts WHERE wf=? AND dim=? AND day>=? "
                       "AND day>=0 AND arm IN (?,?)", (ctx.wf, dim, ctx.day - DRY_DAYS + 1, arm, ctrl))
    cells: dict = {}
    for day, feat, cohort, a, n, e in rows:
        if feat not in feats:
            continue
        c = cells.setdefault((day, cohort), [0, 0, 0, 0])
        if a == arm:
            c[0] += n; c[1] += e
        else:
            c[2] += n; c[3] += e
    return stratified_lift([(k[0], *v) for k, v in sorted(cells.items())])


def dry_run(ctx: WfCtx, old: dict, new: dict, extra: dict | None = None) -> dict:
    """Predicted change of each KPI with an 80% interval, from the last 3 days of matching calls."""
    out: dict = {"method": f"replay of our last {DRY_DAYS} days of matching calls (stratified by day x cohort)",
                 "components": []}
    e_mean = e_var = 0.0
    p_mean = p_var = 0.0
    k = ctx.store.q1("SELECT COALESCE(SUM(dials),0), COALESCE(SUM(conn),0), COALESCE(SUM(eng),0) FROM day_kpi "
                     "WHERE wf=? AND day>=?", (ctx.wf, ctx.day - DRY_DAYS))
    total_conn = max(k[1], 1)
    keys = {"default"} | {c["id"] for c in new["cohorts"]}
    for key in sorted(keys):
        feats = _scope_feats_for_cohort(ctx, new, key)
        if not feats:
            continue
        w_conn = ctx.store.q1("SELECT COALESCE(SUM(conn),0) FROM arm_counts WHERE wf=? AND dim='voice' AND day>=? "
                              "AND feat IN (%s)" % ",".join(map(str, feats)), (ctx.wf, ctx.day - DRY_DAYS))[0]
        weight = w_conn / total_conn
        for d in ("welcome_message", "voice"):
            a, b = dist(old, key, d), dist(new, key, d)
            if a == b:
                continue
            ctrl = max(a, key=lambda x: (a[x], x))
            for arm in sorted(set(a) | set(b)):
                ds = b.get(arm, 0) - a.get(arm, 0)
                if arm == ctrl or abs(ds) < 1e-9:
                    continue
                l = _arm_lift(ctx, d, feats, arm, ctrl)
                est, se = (l.rel, l.se) if l else (0.0, UNKNOWN_SE)
                e_mean += weight * ds * est
                e_var += (weight * ds * se) ** 2
                out["components"].append({"cohort": key, "dim": d, "arm": arm, "vs": ctrl, "share_change": round(ds, 3),
                                          "observed_lift": None if l is None else round(l.rel, 4),
                                          "n_arm": 0 if l is None else l.n_arm})
    if extra and "pickup" in extra:
        p_mean += extra["pickup"][0]
        p_var += extra["pickup"][1] ** 2
        out["components"].append({"dim": extra.get("pickup_dim", "caller_id/schedule"),
                                  "predicted_pickup_change": round(extra["pickup"][0], 4)})
    z = 1.2816
    out["engagement_rate_change"] = {"mean": round(e_mean, 4), "ci80": [round(e_mean - z * math.sqrt(e_var), 4),
                                                                          round(e_mean + z * math.sqrt(e_var), 4)]}
    out["pickup_rate_change"] = {"mean": round(p_mean, 4), "ci80": [round(p_mean - z * math.sqrt(p_var), 4),
                                                                      round(p_mean + z * math.sqrt(p_var), 4)]}
    out["calls_replayed"] = int(k[0])
    out["_moments"] = (e_mean, e_var, p_mean, p_var)
    return out


def impact_score(dr: dict, safety: bool = False) -> tuple[float, str]:
    if safety:
        return 1.0, "safety fix: ranked first by definition"
    e_mean, e_var, p_mean, p_var = dr.pop("_moments")
    # concluded leads/day move roughly with connected x engagement: relative changes add
    mean = e_mean + p_mean
    sd = math.sqrt(e_var + p_var) or 1e-6
    p_benefit = phi(mean / sd)
    score = max(0.0, min(1.0, 10.0 * max(mean, 0.0) * p_benefit))
    return round(score, 4), (f"expected {mean:+.1%} concluded leads/day, P(benefit)={p_benefit:.2f}; "
                             "score = 10 x expected relative lift x P(benefit), clipped to [0,1]")


def package(ctx: WfCtx, old: dict, new: dict, intents: list, rationale_extra: str = "", pickup=None,
            safety: bool = False, evidence: dict | None = None) -> dict:
    """The approval body (minus ids). Every field the approver checks is non-empty by construction."""
    intents = sorted(intents, key=lambda i: (i.priority, i.key))
    summary = intents[0].summary if len(intents) == 1 else \
        f"{len(intents)} changes: " + "; ".join(i.summary.split(":")[0] for i in intents[:4])
    d = diff(old, new) or {"note": "no visible diff"}
    al, al_detail = affected_leads(ctx, old, new)
    dr = dry_run(ctx, old, new, {"pickup": pickup} if pickup else None)
    dr["affected_detail"] = al_detail
    score, why = impact_score(dr, safety)
    dr["impact_score_explained"] = why
    rationale = " | ".join(f"[{i.kind}] {i.summary}" for i in intents)
    if rationale_extra:
        rationale += " | " + rationale_extra
    return {"change_summary": summary[:300], "diff": d, "affected_leads": int(al), "dry_run": dr,
            "impact_score": score, "rationale": rationale,
            "evidence": {**(evidence or {}), "intents": [i.as_dict() for i in intents]}}
