"""Planners turn evidence into *targets*: the allocation we want for each (scope, dim), the caller-ID spread
and the schedule tables. They never call the API. Routing, batching and safety happen in governance.py.

Planners are deterministic functions of (store, live config, params, sim minute). Arm status changes are
returned as intents and committed only when the change they belong to is actually applied, so a rejected
proposal leaves no trace in what we believe happened (Product memo: only what ran counts as evidence).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from . import params as P
from .cohorts import Layout, feats_in, matches, scope_dist, scope_sched
from .config_build import clean_dist
from .context import WfCtx
from .detect import did_flags, did_window
from .stats import Lift, bh, shrink, stratified_lift

PRIORITY = {"prompt_revert": 0, "did_quarantine": 1, "did_spread": 2, "rollback": 3, "promote": 4,
            "segment_promote": 4, "prune": 5, "ramp": 6, "probe": 7, "schedule": 8}


@dataclass
class Intent:
    key: str                  # stable id, e.g. "promote:default:voice:V3"
    kind: str
    summary: str
    evidence: dict = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.kind, 9)

    def as_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind, "summary": self.summary, "evidence": self.evidence}


# =========================================================================== caller ID
@dataclass
class CallerPlan:
    target: dict
    status_changes: list[tuple[str, str, str, dict]]     # (did, status, reason, evidence) -> commit now
    intents: list[Intent]
    urgent: bool


def did_states(ctx: WfCtx) -> dict[str, tuple[str, int]]:
    return {d: (s, m) for d, s, m in ctx.store.q("SELECT did, status, since_minute FROM did_state WHERE wf=?",
                                                  (ctx.wf,))}


def caller_plan(ctx: WfCtx) -> CallerPlan:
    opts = list(ctx.options["caller_id"])
    cur = ctx.cfg["allocations"]["default"]["caller_id"]
    st = did_states(ctx)
    changes: list[tuple[str, str, str, dict]] = []
    intents: list[Intent] = []

    def set_status(d, s, reason, ev=None):
        st[d] = (s, ctx.now)
        changes.append((d, s, reason, ev or {}))

    for d in opts:
        if d not in st:
            used = ctx.store.q1("SELECT COALESCE(SUM(dials),0) FROM did_counts WHERE wf=? AND did=?", (ctx.wf, d))[0]
            set_status(d, "proven" if cur.get(d, 0) > 0 and used > 0 else "unknown",
                       "initial: in use with history" if cur.get(d, 0) > 0 and used > 0 else "initial: never used")
    urgent = False
    active = [d for d in opts if cur.get(d, 0) > 0]
    pool = lambda: [d for d in opts if st[d][0] in ("proven", "unknown", "reprobe")]

    # 1. severe flags on numbers in use -> quarantine (but never shrink the pool below 2)
    fair = 1.0 / max(len(active), 1)
    healthy_ref = {d for d in opts if st[d][0] == "proven"} or None
    for d, ev in did_flags(ctx, active, healthy_ref):
        if st[d][0] in ("quarantined", "degraded"):
            continue
        if cur.get(d, 0) > P.DID_OVERLOAD_FACTOR * fair:
            # carrying far more than its share: low pickup is load wear, and spreading is the cure (F9/F10)
            continue
        if len([x for x in pool() if x != d]) >= P.DID_MIN_POOL:
            set_status(d, "quarantined", "severe pickup drop vs pool", ev)
            intents.append(Intent(f"did_quarantine:{d}", "did_quarantine",
                                  f"Stop dialling from {d}: pickup {ev['ratio']:.0%} of the other numbers "
                                  f"over {ev['dials']} dials (z={ev['z']})", ev))
            urgent = True
        else:
            set_status(d, "degraded", "severe drop but pool would fall below 2; reduced share", ev)
            urgent = True

    # 2. evaluate probes (numbers that are not yet proven) on recent evidence
    since_h = (ctx.now - P.DID_PROBE_LOOKBACK_HOURS * 60) // 60
    proven = {d for d in opts if st[d][0] == "proven"}
    stats = did_window(ctx, since_h, pool=proven or None)
    for d in opts:
        s, since = st[d]
        if s not in ("unknown", "reprobe"):
            continue
        ds = stats.get(d)
        if not ds or ds.dials < P.DID_PROBE_MIN_DIALS:
            continue
        if ds.ratio >= P.DID_HEALTHY_RATIO:
            set_status(d, "proven", "probe healthy", ds.as_dict())
        elif ds.ratio < P.DID_FLAG_RATIO and ds.z_below() > P.DID_FLAG_Z:
            set_status(d, "quarantined", "probe severely below pool", ds.as_dict())
        elif ds.dials >= P.DID_PROBE_BAD_MIN_DIALS and ds.z_below(P.DID_HEALTHY_RATIO) > 2.0:
            set_status(d, "quarantined", "probe persistently below 85% of pool", ds.as_dict())
        else:
            continue
        if st[d][0] == "quarantined" and len(pool()) < P.DID_MIN_POOL:     # the floor applies to probes too
            set_status(d, "degraded", "below pool but needed to keep 2 numbers; reduced share", ds.as_dict())

    # 3. quarantine expiry -> small re-probe
    for d in opts:
        s, since = st[d]
        if s == "quarantined" and ctx.now - since >= P.DID_QUARANTINE_HOURS * 60:
            set_status(d, "reprobe", "quarantine elapsed; re-probing at a small share")

    target = did_target(opts, {d: st[d][0] for d in opts}, cur)
    if target != clean_dist(cur) and not any(i.kind == "did_quarantine" for i in intents):
        intents.append(Intent("did_spread", "did_spread",
                              "Spread dialling evenly across healthy caller IDs "
                              + ", ".join(f"{d} {cur.get(d, 0):.0%}->{target.get(d, 0):.0%}" for d in opts),
                              {"states": {d: st[d][0] for d in opts}}))
    return CallerPlan(target, changes, intents, urgent)


def did_target(opts: list[str], status: dict[str, str], cur: dict | None = None) -> dict:
    """Equal shares across proven numbers; probes at a small share; never fewer than 2 numbers.

    A probe already running keeps its share (up to 20%) until it is judged: shrinking it would cost a
    change, slow the verdict and, on approval-only workflows, an approver's attention.
    """
    cur = cur or {}
    H = [d for d in opts if status[d] == "proven"]
    U = [d for d in opts if status[d] == "unknown"]
    R = [d for d in opts if status[d] == "reprobe"]
    D = [d for d in opts if status[d] == "degraded"]
    share: dict[str, float] = {}
    if not H:
        live = U + R + D or opts
        return clean_dist({d: 1.0 for d in live})
    if len(H) == 1 and U:
        for d in U:
            share[d] = min(0.2, P.DID_SINGLE_POOL_PROBE_TOTAL / len(U))
    else:
        for d in U:
            share[d] = max(P.DID_PROBE_SHARE, min(cur.get(d, 0.0), 0.2))
    for d in R:
        share[d] = P.DID_REPROBE_SHARE
    for d in D:
        share[d] = 0.10
    rest = max(0.0, 1.0 - sum(share.values()))
    for d in H:
        share[d] = rest / len(H)
    return clean_dist(share)


# =========================================================================== experiments
@dataclass
class EngPlan:
    dists: dict = field(default_factory=dict)            # (scope, dim) -> dist
    new_overrides: dict = field(default_factory=dict)    # dim -> [seg ids] to append
    arm_changes: list = field(default_factory=list)      # (scope, dim, arm, status, reason)
    intents: list = field(default_factory=list)
    envelope: list = field(default_factory=list)         # (scope, dim, arm, max_share)


def arm_rows(ctx: WfCtx, dim: str, since_day: int) -> list[tuple]:
    return ctx.store.q("SELECT day, feat, cohort, arm, conn, eng FROM arm_counts WHERE wf=? AND dim=? AND day>=? "
                       "AND day>=0 ORDER BY day, feat, cohort, arm", (ctx.wf, dim, since_day))


def strata(rows, feats: set[int], arm: str, ctrl: str):
    cells: dict[tuple, list] = {}
    for day, feat, cohort, a, n, e in rows:
        if feat not in feats or a not in (arm, ctrl):
            continue
        c = cells.setdefault((day, cohort), [0, 0, 0, 0])
        if a == arm:
            c[0] += n; c[1] += e
        else:
            c[2] += n; c[3] += e
    return [(k[0], *v) for k, v in sorted(cells.items())]


def lift_of(rows, feats, arm, ctrl, ref_day: int | None = None) -> Lift | None:
    """Time-discounted (half-life P.LIFT_HALF_LIFE_DAYS) when ref_day is given; see stats.stratified_lift."""
    return stratified_lift(strata(rows, feats, arm, ctrl), ref_day, P.LIFT_HALF_LIFE_DAYS if ref_day is not None
                           else None)


def arm_states(ctx: WfCtx) -> dict:
    out: dict = {}
    for scope, dim, arm, status in ctx.store.q("SELECT scope, dim, arm, status FROM arms WHERE wf=?", (ctx.wf,)):
        out.setdefault((scope, dim), {})[arm] = status
    return out


def _lift_ev(l: Lift | None) -> dict:
    if l is None:
        return {}
    lo, hi = l.interval()
    return {"lift": round(l.rel, 4), "ci80": [round(lo, 4), round(hi, 4)], "n_arm": l.n_arm, "n_ctrl": l.n_ctrl,
            "days": l.days, "p_gt_2pct": round(l.p_gt(P.WIN_DELTA), 4), "p_lt_0": round(l.p_lt(0.0), 4)}


def engagement_plan(ctx: WfCtx, evaluate: bool = True) -> EngPlan:
    """evaluate=False only finishes what earlier evaluations decided (e.g. a promotion that max_shift split into
    steps). Arm decisions are evaluated at most every 6h: re-testing nearly the same data every 2 hours multiplies the
    false-positive rate (prunes, ramps, rollbacks) without making decisions meaningfully faster."""
    plan = EngPlan()
    states = arm_states(ctx)
    budget_new = P.NEW_UNPROVEN_PER_APPLY
    budget_ramp = P.RAMP_PER_APPLY
    tests: list[tuple[float, dict]] = []      # (p-value stand-in, candidate)
    probe_queue: list[tuple[int, str, str, str]] = []
    scopes_by_dim = {}
    for dim in P.ENG_DIMS:
        opts = list(ctx.options.get(dim) or [])
        if len(opts) < 2 or ctx.supervisor_touched(dim, P.SUPERVISOR_RESPECT_HOURS):
            continue
        rows = arm_rows(ctx, dim, ctx.day - P.EVAL_DAYS + 1)
        recent = arm_rows(ctx, dim, ctx.day - P.DECAY_WINDOW_DAYS + 1)
        today = [r for r in recent if r[0] == ctx.day]
        yesterday = [r for r in recent if r[0] == ctx.day - 1]
        latest = today if sum(r[4] for r in today) >= P.TREND_MIN_CONNECTED_TODAY else yesterday
        overrides = ctx.layout.overrides[dim]
        scopes = ["default"] + overrides
        scopes_by_dim[dim] = scopes
        for scope in scopes:
            feats = _scope_feats(ctx, dim, scope, overrides)
            cur = scope_dist(ctx.cfg, scope, dim)
            st = dict(states.get((scope, dim), {}))
            inc = next((a for a in opts if st.get(a) in ("control", "winner")), None)
            if inc is None or cur.get(inc, 0) <= 0:
                inc = max(opts, key=lambda a: (cur.get(a, 0.0), -opts.index(a)))
                if st.get(inc) not in ("control", "winner"):
                    plan.arm_changes.append((scope, dim, inc, "control", "largest share in live config"))
                    st[inc] = "control"
            for a in opts:
                if a != inc and a not in st and cur.get(a, 0) > 0:
                    st[a] = "probe"
                    plan.arm_changes.append((scope, dim, a, "probe", "already live in config"))
            shares = {a: cur.get(a, 0.0) for a in opts if a != inc}
            # ---- decay / rollback: does the incumbent still beat the old control it replaced?
            hold = next((a for a in opts if st.get(a) == "holdout"), None)
            if evaluate and hold and st.get(inc) == "winner":
                dl = lift_of(recent, feats, inc, hold, ctx.day)
                if dl and dl.n_ctrl >= P.DECAY_MIN_HOLDOUT and dl.p_lt(0.0) > P.DECAY_P:
                    plan.arm_changes += [(scope, dim, inc, "retired", "decayed below holdout"),
                                         (scope, dim, hold, "control", "restored after rollback")]
                    plan.intents.append(Intent(f"rollback:{scope}:{dim}:{inc}", "rollback",
                                               f"Roll back {dim} {inc} in {scope}: over the last "
                                               f"{P.DECAY_WINDOW_DAYS} days it is {dl.rel:+.1%} vs {hold}",
                                               _lift_ev(dl)))
                    shares = {a: (0.0 if a in (inc,) else s) for a, s in shares.items() if a != hold}
                    shares[inc] = 0.0
                    st[inc], st[hold] = "retired", "control"
                    plan.dists[(scope, dim)] = _assemble(opts, hold, shares)
                    continue
            # ---- per-arm decisions
            for a in opts:
                if a == inc:
                    continue
                s = st.get(a)
                if s in ("pruned", "retired"):
                    shares[a] = 0.0
                    if cur.get(a, 0) > 0:      # an earlier partial step left traffic on it: finish the job
                        plan.intents.append(Intent(f"prune:{scope}:{dim}:{a}", "prune",
                                                   f"Finish removing {dim} {a} from {scope} ({s} earlier; "
                                                   f"{cur[a]:.0%} still live)", {}))
                    continue
                if s == "holdout":
                    shares[a] = P.HOLDOUT_SHARE
                    if abs(cur.get(a, 0) - P.HOLDOUT_SHARE) > 0.005:
                        plan.intents.append(Intent(f"promote:{scope}:{dim}:{inc}", "promote",
                                                   f"Finish promoting {dim} {inc} in {scope}; {a} held out at "
                                                   f"{P.HOLDOUT_SHARE:.0%}", {}))
                    continue
                if not evaluate:
                    continue
                if s is None:
                    probe_queue.append((opts.index(a), dim, scope, a))
                    continue
                l = lift_of(rows, feats, a, inc, ctx.day)        # discounted: tracks the current effect
                if l is None:
                    continue
                rl = lift_of(recent, feats, a, inc)     # "is it winning *now*": guards against decay onset
                recent_ok = rl is not None and rl.p_gt(0.0) >= P.RECENT_P_POSITIVE
                ll = lift_of(latest, feats, a, inc)
                # trend guard: the latest day must not sit clearly below the window average (decay in progress)
                fading = ll is not None and ll.se > 0 and (l.est - ll.est) / ll.se > P.TREND_Z
                recent_ok = recent_ok and not fading
                seg_good = False
                if scope == "default" and l.n_arm >= P.RAMP_MIN_CONNECTED:
                    for sid, sl, share in _segment_lifts(ctx, rows, feats, a, inc, l):
                        if sl.p_gt(0.0) > 0.8:
                            seg_good = True
                        # a segment cohort only when the segment stands out from the arm's overall effect;
                        # if shrinkage pulled it back to the overall lift, it is an overall effect, not a cohort
                        stands_out = sl.est - l.est > max(l.se, 0.02)
                        if sl.n_arm >= P.SEGMENT_MIN_CONNECTED and sl.n_ctrl >= P.SEGMENT_MIN_CONNECTED \
                                and sl.days >= P.WIN_MIN_DAYS and sid not in overrides and stands_out:
                            tests.append((1 - sl.p_gt(P.WIN_DELTA),
                                          {"kind": "segment_promote", "dim": dim, "scope": scope, "seg": sid,
                                           "arm": a, "ctrl": inc, "lift": sl, "share": share}))
                # pruning a good arm is itself a harmful change; keeping a bad one a little longer at a small
                # share is not a change at all -> prune only on strong evidence (time-discounted estimate)
                if l.n_arm >= P.PRUNE_MIN_CONNECTED and l.p_lt(P.PRUNE_DELTA) > P.PRUNE_P and not seg_good:
                    shares[a] = 0.0
                    plan.arm_changes.append((scope, dim, a, "pruned", "worse than control"))
                    plan.intents.append(Intent(f"prune:{scope}:{dim}:{a}", "prune",
                                               f"Stop testing {dim} {a} in {scope}: {l.rel:+.1%} vs {inc} "
                                               f"(recent days weighted most)", _lift_ev(l)))
                    continue
                if l.n_arm >= P.WIN_MIN_CONNECTED and l.n_ctrl >= P.WIN_MIN_CONNECTED and l.days >= P.WIN_MIN_DAYS \
                        and recent_ok:
                    tests.append((1 - l.p_gt(P.WIN_DELTA), {"kind": "promote", "dim": dim, "scope": scope,
                                                             "arm": a, "ctrl": inc, "lift": l, "recent": rl}))
                losing = l.n_arm >= P.RAMP_MIN_CONNECTED and l.p_lt(0.0) > P.DERAMP_P
                if (fading or losing) and shares.get(a, 0) > P.PROBE_SHARE + 1e-9 and not seg_good:
                    # graded response: step back to probe share on moderate evidence (a wrong step-down of a good
                    # arm stays under the harm line); full prune still needs strong evidence
                    shares[a] = P.PROBE_SHARE
                    why = (f"latest day {ll.rel:+.1%} vs {l.rel:+.1%} recently (fading)" if fading else
                           f"{l.rel:+.1%} vs {inc}, P(worse)={l.p_lt(0.0):.2f}")
                    plan.intents.append(Intent(f"fade:{scope}:{dim}:{a}", "prune",
                                               f"Reduce {dim} {a} in {scope} to {P.PROBE_SHARE:.0%}: {why}",
                                               {**_lift_ev(l), "latest": _lift_ev(ll)}))
                    continue
                if l.n_arm >= P.RAMP_MIN_CONNECTED and l.p_gt(0.0) >= P.RAMP_P_BETTER and not fading:
                    nxt = next((x for x in P.RAMP_SHARE_STEPS if x > shares.get(a, 0) + 1e-9), None)
                    room = P.MAX_TEST_SHARE - sum(v for k, v in shares.items())
                    if nxt is not None and budget_ramp > 0 and room > 0:
                        add = min(nxt - shares.get(a, 0), budget_ramp, room)
                        if add >= P.RAMP_MIN_STEP - 1e-9:
                            shares[a] = shares.get(a, 0) + add
                            budget_ramp -= add
                            if s != "ramp":
                                plan.arm_changes.append((scope, dim, a, "ramp", "not worse than control"))
                                st[a] = "ramp"
                            plan.intents.append(Intent(f"ramp:{scope}:{dim}:{a}", "ramp",
                                                       f"Give {dim} {a} more traffic in {scope} "
                                                       f"({cur.get(a, 0):.0%}->{shares[a]:.0%}): {l.rel:+.1%} so far",
                                                       _lift_ev(l)))
                            plan.envelope.append((scope, dim, a, P.RAMP_SHARE_STEPS[-1]))
            plan.dists[(scope, dim)] = _assemble(opts, inc, shares)
            states[(scope, dim)] = st

    # ---- winners: deterministic, BH-corrected across every test evaluated in this run
    decisions = bh([p for p, _ in tests], P.BH_Q)
    winners_done: set = set()
    for (p, t), ok in sorted(zip(tests, decisions), key=lambda x: (x[0][0], x[0][1]["dim"], x[0][1]["arm"])):
        l: Lift = t["lift"]
        if not ok or l.p_gt(P.WIN_DELTA) < P.WIN_P:
            continue
        key = (t["scope"], t["dim"]) if t["kind"] == "promote" else ("seg", t["dim"])   # <= 1 new cohort per dim
        if key in winners_done or (t["scope"], t["dim"]) in winners_done:
            continue
        opts = list(ctx.options[t["dim"]])
        ev = {**_lift_ev(l), "bh_q": P.BH_Q, "p_value_standin": round(p, 5), "tests_in_family": len(tests)}
        if t["kind"] == "promote":
            winners_done.add(key)
            scope, dim, a, inc = t["scope"], t["dim"], t["arm"], t["ctrl"]
            cur = plan.dists[(scope, dim)]
            prev_hold = [x for x in opts if states.get((scope, dim), {}).get(x) == "holdout"]
            others = {x: cur.get(x, 0.0) for x in opts if x not in (a, inc) and x not in prev_hold}
            shares = {**others, inc: P.HOLDOUT_SHARE}
            for h in prev_hold:
                shares[h] = 0.0
                plan.arm_changes.append((scope, dim, h, "retired", "superseded holdout"))
            plan.arm_changes += [(scope, dim, a, "winner", "met winner rule"),
                                 (scope, dim, inc, "holdout", "kept as holdout after promotion")]
            plan.dists[(scope, dim)] = _assemble(opts, a, shares)
            plan.envelope.append((scope, dim, a, 1.0))
            plan.intents.append(Intent(f"promote:{scope}:{dim}:{a}", "promote",
                                       f"Promote {dim} {a} in {scope}: {l.rel:+.1%} engagement vs {inc} "
                                       f"(P(lift>2%)={l.p_gt(P.WIN_DELTA):.2f}); {inc} kept at "
                                       f"{P.HOLDOUT_SHARE:.0%} as holdout", ev))
        else:
            seg, dim, a, inc = t["seg"], t["dim"], t["arm"], t["ctrl"]
            trial = Layout({**ctx.layout.overrides, dim: ctx.layout.overrides[dim] +
                            plan.new_overrides.get(dim, []) + [seg]}, ctx.layout.foreign)
            if trial.n_cohorts(ctx.reg) > P.MAX_COHORTS or t["share"] < P.SEGMENT_MIN_SHARE:
                continue
            winners_done.add(key)
            plan.new_overrides.setdefault(dim, []).append(seg)
            plan.dists[(seg, dim)] = _assemble(opts, a, {inc: P.HOLDOUT_SHARE})
            plan.arm_changes += [(seg, dim, a, "winner", "segment winner"),
                                 (seg, dim, inc, "holdout", "holdout in new segment cohort")]
            plan.envelope.append((seg, dim, a, 1.0))
            plan.intents.append(Intent(f"segment_promote:{seg}:{dim}:{a}", "segment_promote",
                                       f"New cohort {seg}: {dim} {a} {l.rel:+.1%} engagement vs {inc} "
                                       f"(shrunk estimate, {t['share']:.0%} of calls)", {**ev, "segment": seg}))

    # ---- new probes: round-robin across dims, within the per-apply exposure budget
    for _, dim, scope, a in sorted(probe_queue):
        if budget_new < P.PROBE_SHARE - 1e-9:
            break
        cur = plan.dists.get((scope, dim)) or scope_dist(ctx.cfg, scope, dim)
        st = states.get((scope, dim), {})
        opts = list(ctx.options[dim])
        inc = max(opts, key=lambda x: (cur.get(x, 0.0), -opts.index(x)))
        unproven = sum(cur.get(x, 0) for x in opts if st.get(x) == "probe")
        tested = sum(v for x, v in cur.items() if x != inc)
        if unproven + P.PROBE_SHARE > P.UNPROVEN_TOTAL_CAP + 1e-9 or tested + P.PROBE_SHARE > P.MAX_TEST_SHARE:
            continue
        shares = {x: cur.get(x, 0.0) for x in opts if x != inc}
        shares[a] = P.PROBE_SHARE
        plan.dists[(scope, dim)] = _assemble(opts, inc, shares)
        budget_new -= P.PROBE_SHARE
        plan.arm_changes.append((scope, dim, a, "probe", "new arm"))
        plan.envelope.append((scope, dim, a, P.RAMP_SHARE_STEPS[-1]))
        plan.intents.append(Intent(f"probe:{scope}:{dim}:{a}", "probe",
                                   f"Start testing {dim} {a} on {P.PROBE_SHARE:.0%} of {scope} calls", {}))
    return plan


def _assemble(opts, inc, shares: dict) -> dict:
    out = {a: max(0.0, v) for a, v in shares.items() if a != inc and v > 1e-9}
    out[inc] = max(0.0, 1.0 - sum(out.values()))
    return clean_dist(out)


def _scope_feats(ctx: WfCtx, dim: str, scope: str, overrides: list[str]) -> set[int]:
    """Feature codes whose first-matching override for `dim` is `scope`."""
    out = set()
    wheres = [(s, ctx.reg.where(s)) for s in overrides]
    for code, feats in ctx.combos.items():
        first = next((s for s, w in wheres if matches(w, feats)), "default")
        if first == scope:
            out.add(code)
    return out


def _segment_lifts(ctx: WfCtx, rows, scope_feats: set[int], arm: str, ctrl: str, overall: Lift):
    """Shrunk single-feature segment lifts inside a scope, with each segment's share of connected calls."""
    segs = ctx.reg.single_feature_segments(ctx.combos)
    total = sum(n for _, feat, _, a, n, _ in rows if feat in scope_feats and a in (arm, ctrl)) or 1
    raw, shares = {}, {}
    for sid, f, v in segs:
        sf = {c for c in scope_feats if ctx.combos[c].get(f) == v}
        if not sf:
            continue
        raw[sid] = lift_of(rows, sf, arm, ctrl, ctx.day)
        shares[sid] = sum(n for _, feat, _, a, n, _ in rows if feat in sf and a in (arm, ctrl)) / total
    shrunk = shrink({k: v for k, v in raw.items() if v is not None}, overall)
    return [(sid, shrunk[sid], shares[sid]) for sid in sorted(shrunk)]


# =========================================================================== schedule
@dataclass
class SchedPlan:
    feature: str
    groups: list            # [(scope id or "default", values, table)]
    gain: float
    evidence: dict


def schedule_plan(ctx: WfCtx) -> SchedPlan | None:
    cached = ctx.store.get_meta(f"sched_plan:{ctx.wf}")
    if cached is not None:
        return SchedPlan(**cached) if cached else None
    sp = _compute_schedule(ctx)
    ctx.store.set_meta(f"sched_plan:{ctx.wf}", sp.__dict__ if sp else {})
    return sp


def _compute_schedule(ctx: WfCtx) -> SchedPlan | None:
    lo, hi = ctx.spec["dialling_window"]
    hours = list(range(lo, hi))
    rows = ctx.store.q("SELECT feat, hour, SUM(dials), SUM(conn) FROM hour_counts WHERE wf=? AND day<0 "
                       "GROUP BY feat, hour ORDER BY feat, hour", (ctx.wf,))
    if sum(r[2] for r in rows) < P.SCHEDULE_MIN_HISTORY_DIALS:
        return None
    tot = {h: [0, 0] for h in hours}
    for feat, h, n, c in rows:
        if h in tot:
            tot[h][0] += n; tot[h][1] += c
    r_h = {h: (tot[h][1] + 1) / (tot[h][0] + 2) for h in hours}

    def profile(cells: dict[int, list]) -> dict[int, float]:
        K = P.SCHEDULE_SHRINK_DIALS
        q = {h: (cells.get(h, [0, 0])[1] + K * r_h[h]) / (cells.get(h, [0, 0])[0] + K) for h in hours}
        m = sum(q.values()) / len(q)
        return {h: q[h] / m for h in hours}

    best = None
    for f in ("lang", "city_tier", "age_band", "value_band"):
        by_v: dict[str, dict[int, list]] = {}
        for feat, h, n, c in rows:
            v = ctx.combos.get(feat, {}).get(f)
            if v is None or h not in tot:
                continue
            cell = by_v.setdefault(v, {}).setdefault(h, [0, 0])
            cell[0] += n; cell[1] += c
        overall = profile({h: tot[h] for h in hours})
        het = 0.0
        profs = {}
        for v, cells in by_v.items():
            pr = profile(cells)
            profs[v] = (pr, sum(x[0] for x in cells.values()), cells)
            het += profs[v][1] * sum((pr[h] - overall[h]) ** 2 for h in hours)
        if best is None or het > best[0]:
            best = (het, f, profs)
    _, f, profs = best
    groups = [([v], cells, n) for v, (pr, n, cells) in sorted(profs.items())]
    while len(groups) > P.SCHEDULE_MAX_GROUPS:     # agglomerative merge of the two closest profiles
        best_pair = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                pi, pj = profile(groups[i][1]), profile(groups[j][1])
                d = sum((pi[h] - pj[h]) ** 2 for h in hours) * min(groups[i][2], groups[j][2])
                if best_pair is None or d < best_pair[0]:
                    best_pair = (d, i, j)
        _, i, j = best_pair
        merged = {h: [groups[i][1].get(h, [0, 0])[0] + groups[j][1].get(h, [0, 0])[0],
                      groups[i][1].get(h, [0, 0])[1] + groups[j][1].get(h, [0, 0])[1]] for h in hours}
        g = (sorted(groups[i][0] + groups[j][0]), merged, groups[i][2] + groups[j][2])
        groups = [x for k, x in enumerate(groups) if k not in (i, j)] + [g]
        groups.sort(key=lambda x: x[0])
    tables = []
    for vals, cells, n in groups:
        pr = profile(cells)
        table = {str(h): round(min(max(pr[h], P.SCHEDULE_W_MIN), P.SCHEDULE_W_MAX), 2) for h in hours}
        tables.append((vals, cells, n, table))
    # predicted gain: replay history hours with dials re-split in proportion to n_gh * w_gh
    base_c = base_n = new_c = 0.0
    for h in hours:
        cells = [(t[1].get(h, [0, 0]), float(t[3][str(h)])) for t in tables]
        n_h = sum(c[0] for c, _ in cells)
        if n_h == 0:
            continue
        wsum = sum(c[0] * w for c, w in cells)
        for (n, c), w in cells:
            rate = (c + 1) / (n + 2)
            base_c += c
            new_c += n_h * (n * w / wsum) * rate if wsum else 0
        base_n += n_h
    gain = (new_c / base_c - 1) if base_c else 0.0
    largest = max(range(len(tables)), key=lambda i: (tables[i][2], -i))
    out = []
    for i, (vals, _, n, table) in enumerate(tables):
        scope = "default" if i == largest else ctx.reg.register(f, vals)
        out.append((scope, vals, table))
    return SchedPlan(f, out, round(gain, 4), {"feature": f, "predicted_pickup_gain": round(gain, 4),
                                              "groups": {s: v for s, v, _ in out},
                                              "method": "history replay, dials re-split by weight (upper bound)"})


def schedule_targets(ctx: WfCtx, sp: SchedPlan) -> tuple[list[str], dict]:
    """(sched override segments, tables per scope) — or the live ones if already applied."""
    return [s for s, _, _ in sp.groups if s != "default"], {s: t for s, _, t in sp.groups}


def schedule_is_live(ctx: WfCtx, sp: SchedPlan) -> bool:
    segs, tables = schedule_targets(ctx, sp)
    if set(ctx.layout.overrides["sched"]) != set(segs):
        return False
    for s, t in tables.items():
        cur = scope_sched(ctx.cfg, s)
        if not cur or any(abs(float(cur.get(h, -1)) - float(w)) > 1e-3 for h, w in t.items()):
            return False
    return True
