"""SafetyEvaluator: every candidate passes all checks or is blocked with a logged reason (BUILD_PLAN 5.9).

Checks 7, 10, 11, 12 are relaxed for urgent (incident / flagged caller ID) changes; 1-6 never are.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import params as P
from .config_build import AGENT_DIMS, changed_dims, dist, max_shift
from .context import WfCtx

KNOWN_FEATURES = ("lang", "city_tier", "age_band", "value_band")


@dataclass
class Verdict:
    checks: dict = field(default_factory=dict)

    def add(self, name: str, ok: bool, why: str = ""):
        self.checks[name] = {"ok": bool(ok), "why": why}

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks.values())

    @property
    def failures(self) -> list[str]:
        return [f"{k}: {v['why']}" for k, v in self.checks.items() if not v["ok"]]


def evaluate(ctx: WfCtx, gov, cand: dict, route: str, urgent: bool = False, evidence: dict | None = None) -> Verdict:
    v = Verdict()
    cur = ctx.cfg
    dims = changed_dims(cur, cand)
    opts = ctx.options

    # 1. built on the live base
    live = ctx.store.latest_version(ctx.wf)
    v.add("1_base_is_live", live == ctx.version, f"base v{ctx.version}, live v{live}")

    # 2. only allowed options; weights >= 0 and sum to 1
    bad = []
    for key, dd in cand["allocations"].items():
        for d, w in dd.items():
            if any(o not in opts.get(d, []) for o in w):
                bad.append(f"{key}.{d} option not allowed")
            if any(x < 0 for x in w.values()) or abs(sum(w.values()) - 1) > 1e-6:
                bad.append(f"{key}.{d} weights")
    if cand["prompt_version"] not in opts["prompt_version"]:
        bad.append("prompt not allowed")
    if any(d not in cand["allocations"].get("default", {}) for d in AGENT_DIMS):
        bad.append("default allocation incomplete")
    v.add("2_options_and_weights", not bad, "; ".join(bad))

    # 3. cohorts: known features/values, cap, existing order preserved
    problems = []
    ids = [c["id"] for c in cand["cohorts"]]
    if len(ids) != len(set(ids)) or "default" in ids:
        problems.append("duplicate or reserved cohort id")
    known_vals: dict[str, set] = {f: set() for f in KNOWN_FEATURES}
    for feats in ctx.combos.values():
        for f, val in feats.items():
            known_vals.setdefault(f, set()).add(val)
    for c in cand["cohorts"]:
        for f, vals in c["where"].items():
            if f not in KNOWN_FEATURES or not vals or any(x not in known_vals.get(f, set()) for x in vals):
                problems.append(f"{c['id']}: unknown feature/value {f}={vals}")
    foreign = [c["id"] for c in ctx.layout.foreign]
    if [i for i in ids if i in foreign] != foreign:
        problems.append("cohorts we did not create were reordered or removed")
    if len(ids) > max(P.MAX_COHORTS, len(cur["cohorts"])):
        problems.append(f"more than {P.MAX_COHORTS} cohorts")
    v.add("3_cohorts", not problems, "; ".join(problems))

    # 4. schedule covers exactly the dialling window
    lo, hi = ctx.spec["dialling_window"]
    want = {str(h) for h in range(lo, hi)}
    sp = []
    for key, table in (cand.get("schedule", {}).get("hour_weights") or {}).items():
        if set(table) != want:
            sp.append(f"{key}: hours {sorted(set(table) ^ want)} missing/outside window")
        if any(float(w) <= 0 for w in table.values()):
            sp.append(f"{key}: zero weight would starve an hour")
    if len(cand.get("schedule", {}).get("lead_preferred_hour") or {}) > 200_000:
        sp.append("lead_preferred_hour > 200k")
    v.add("4_schedule_window", not sp, "; ".join(sp))

    # 5. auto route only inside policy (same TV formula as the simulator)
    if route == "auto":
        allowed = ctx.auto.get("allowed_dims") or []
        sh = max_shift(cur, cand)
        ok = bool(ctx.auto.get("enabled")) and all(d in allowed for d in dims) and \
            sh <= float(ctx.auto.get("max_shift", 0)) + 1e-9 and bool(dims)
        v.add("5_auto_policy", ok, f"dims={dims} allowed={allowed} shift={sh:.3f}")
    else:
        v.add("5_auto_policy", True, "approval route")

    # 6. prompt changes carry passing validation for this workflow and prompt
    if "prompt_version" in dims:
        run_id = (evidence or {}).get("validation_run_id")
        from .incidents import Credits
        runs = [r for r in [Credits(ctx.store, None).validation_for(ctx.wf, cand["prompt_version"])] if r]
        ok = bool(run_id) and any(r["validation_run_id"] == run_id and
                                  r["pass_rate"] >= ctx.gov.get("min_validation_pass_rate", 0.97) for r in runs) \
            if route == "approval" else bool(runs and runs[0]["pass_rate"] >= ctx.gov.get("min_validation_pass_rate", 0.97))
        v.add("6_prompt_validated", ok, f"validation {run_id}")
    else:
        v.add("6_prompt_validated", True, "no prompt change")

    # 7. exposure budget on unproven arms
    # A "new arm" is one with zero share before this change in a cohort that already exists. New segment
    # cohorts carry proven winners (they passed the winner rule) and are excluded.
    over = []
    existing = {"default"} | {c["id"] for c in cur["cohorts"]}
    for key in sorted(existing & ({"default"} | {c["id"] for c in cand["cohorts"]})):
        added = sum(w for d in P.ENG_DIMS for a, w in dist(cand, key, d).items() if dist(cur, key, d).get(a, 0) == 0)
        if added > P.NEW_UNPROVEN_PER_APPLY + 1e-6:
            over.append(f"{key} adds {added:.2f} share on never-tried arms")
    v.add("7_exposure_budget", urgent or not over, "; ".join(over))

    # 8. guardrail prediction is finite (dry run computed in proposal.package); nothing to block here beyond
    #    refusing NaNs in the candidate itself
    v.add("8_finite", all(w == w for dd in cand["allocations"].values() for x in dd.values() for w in x.values()), "")

    # 9. caller-ID pool
    cid = dist(cand, "default", "caller_id")
    live_ids = [d for d, w in cid.items() if w > 0]
    v.add("9_caller_pool", len(live_ids) >= min(P.DID_MIN_POOL, len(opts["caller_id"])) or urgent,
          f"{len(live_ids)} numbers in use")

    # 10. request cap  11. timing  (only for the approval route)
    if route == "approval":
        ok, why = gov.can_request(ctx.spec, ctx.wf, ctx.now, urgent)
        v.add("10_11_cap_and_timing", ok, why)
    else:
        v.add("10_11_cap_and_timing", True, "auto route")

    # 12. data fresh: no unprocessed control events
    v.add("12_data_fresh", urgent or not ctx.store.get_meta("pending_control", []), "pending control events")
    return v
