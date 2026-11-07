"""Hallucination incidents: validate-on-change, last-known-good revert, LLM fallback, verify after apply.

States: open -> fix_proposed -> verifying -> resolved | (fix failed) open ...; no_fix raises a page.
Credits: an incident may spend at most CREDIT_INCIDENT_MAX; validation results are reused across the run
(the approver accepts any run for the same workflow and prompt).
"""
from __future__ import annotations

import json

import httpx

from . import params as P
from .context import WfCtx
from .detect import flag_baseline, hallucination_cusum, prompt_record
from .store import Store, dumps


class Credits:
    def __init__(self, store: Store, client, plan_only: bool = False):
        self.s, self.c, self.plan_only = store, client, plan_only

    def remaining(self) -> float:
        return float(self.s.get_meta("credits_remaining", 0.0))

    def total(self) -> float:
        return float(self.s.get_meta("credits_total", 0.0))

    def spent(self, wf: str | None = None, reason: str | None = None) -> float:
        q = "SELECT COALESCE(SUM(cost),0) FROM credit_ops WHERE state='DONE'"
        args: list = []
        if wf:
            q += " AND wf=?"; args.append(wf)
        if reason:
            q += " AND reason=?"; args.append(reason)
        return float(self.s.q1(q, args)[0])

    def validation_for(self, wf: str, prompt: str) -> dict | None:
        rows = self.s.q("SELECT result_json FROM credit_ops WHERE wf=? AND kind='validation' AND state='DONE' "
                        "ORDER BY minute, op_id", (wf,))
        runs = [json.loads(r[0]) for r in rows]
        runs = [r for r in runs if r.get("prompt_version") == prompt]
        return runs[-1] if runs else None

    def _op(self, op_id: str, wf: str, now: int, kind: str, reason: str, cost: float, call):
        r = self.s.q1("SELECT state, result_json FROM credit_ops WHERE op_id=?", (op_id,))
        if r and r[0] == "DONE":
            return json.loads(r[1])
        if r and r[0] == "PREPARED":           # crashed mid-call last time: outcome unknown, try once more
            op_id = op_id + ":retry"
        if self.plan_only or self.remaining() < cost:
            return None
        with self.s.tx():
            self.s.x("INSERT OR REPLACE INTO credit_ops VALUES (?,?,?,?,?,?,?,?)",
                     (op_id, wf, now, kind, reason, cost, "PREPARED", None))
        try:
            resp = call()
        except httpx.TransportError:
            return None
        with self.s.tx():
            if not resp.ok:
                self.s.x("UPDATE credit_ops SET state='FAILED', result_json=? WHERE op_id=?", (dumps(resp.body), op_id))
                if resp.status == 402:
                    self.s.alert(wf, now, "OUT_OF_CREDITS", True, str(resp.body))
                return None
            self.s.x("UPDATE credit_ops SET state='DONE', result_json=? WHERE op_id=?", (dumps(resp.body), op_id))
            if "credits_remaining" in resp.body:
                self.s.set_meta("credits_remaining", resp.body["credits_remaining"])
        return resp.body

    def validate(self, wf: str, prompt: str, now: int, reason: str, reuse: bool = True) -> dict | None:
        if reuse:
            prev = self.validation_for(wf, prompt)
            if prev:
                return prev
        n = P.VALIDATION_N
        return self._op(f"val:{wf}:{prompt}:{now}", wf, now, "validation", reason, -(-n // 100),
                        lambda: self.c.send("POST", "/validation/runs", {"workflow_id": wf, "prompt_version": prompt,
                                                                         "n": n}))

    def suggest(self, wf: str, problem: str, now: int) -> dict | None:
        return self._op(f"llm:{wf}:{now}", wf, now, "llm", "incident", 3,
                        lambda: self.c.send("POST", "/llm/suggest-fixes", {"workflow_id": wf, "problem": problem}))


def open_incident(ctx: WfCtx) -> dict | None:
    r = ctx.store.q1("SELECT incident_id, state, bad_prompt, fix_prompt, detail_json, opened_minute FROM incidents "
                     "WHERE wf=? AND state NOT IN ('resolved') ORDER BY opened_minute DESC LIMIT 1", (ctx.wf,))
    if not r:
        return None
    return {"id": r[0], "state": r[1], "bad": r[2], "fix": r[3], "detail": json.loads(r[4] or "{}"), "opened": r[5]}


def set_incident(store: Store, iid: str, **cols):
    if "detail" in cols:
        cols["detail_json"] = dumps(cols.pop("detail"))
    sets = ", ".join(f"{k}=?" for k in cols)
    store.x(f"UPDATE incidents SET {sets} WHERE incident_id=?", (*cols.values(), iid))


def start(ctx: WfCtx, bad: str, trigger: str, evidence: dict):
    iid = f"inc:{ctx.wf}:{ctx.now}"
    with ctx.store.tx():
        ctx.store.x("INSERT OR IGNORE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (iid, ctx.wf, ctx.now, bad, trigger, "open", None, 0.0, None, dumps({"trigger": evidence})))
        ctx.store.decide(ctx.wf, ctx.now, "incident_opened", f"prompt {bad} looks unsafe ({trigger})", evidence)
    return iid


def bar(ctx: WfCtx) -> float:
    return float(ctx.gov.get("min_validation_pass_rate", 0.97))


def check_new_prompt(ctx: WfCtx, credits: Credits, prompt: str) -> None:
    """Validate-on-change: called when a config_changed event moves prompt_version."""
    if open_incident(ctx) and open_incident(ctx)["bad"] == prompt:
        return
    v = credits.validate(ctx.wf, prompt, ctx.now, "incident")
    with ctx.store.tx():
        ctx.store.decide(ctx.wf, ctx.now, "validate_on_change", f"prompt {prompt}: pass_rate "
                         f"{v['pass_rate'] if v else 'n/a'} (bar {bar(ctx)})", v or {})
    if v and v["pass_rate"] < bar(ctx):
        start(ctx, prompt, "validation", {"validation": v, "bar": bar(ctx)})


def last_known_good(ctx: WfCtx, exclude: set[str]) -> tuple[str | None, dict]:
    p0, _ = flag_baseline(ctx)
    seen, tried = [], {}
    rows = ctx.store.q("SELECT config_json FROM config_versions WHERE wf=? AND config_json IS NOT NULL "
                       "ORDER BY version DESC", (ctx.wf,))
    for (cj,) in rows:
        p = json.loads(cj)["prompt_version"]
        if p not in seen:
            seen.append(p)
    for p in seen:
        if p in exclude:
            continue
        n, k = prompt_record(ctx, p)
        tried[p] = {"connected": n, "flagged": k, "flag_rate": round(k / n, 4) if n else None}
        if n >= P.LKG_MIN_CONNECTED and k / n <= p0 + P.LKG_FLAG_MARGIN:
            return p, {"record": tried, "baseline_flag_rate": round(p0, 4)}
    return None, {"record": tried, "baseline_flag_rate": round(p0, 4)}


def choose_fix(ctx: WfCtx, credits: Credits, inc: dict) -> tuple[str | None, dict]:
    exclude = {inc["bad"]} | set(inc["detail"].get("failed_fixes", []))
    spent_before = credits.spent(ctx.wf)
    budget_left = lambda: P.CREDIT_INCIDENT_MAX - (credits.spent(ctx.wf) - spent_before) - inc["detail"].get("spent", 0)
    lkg, ev = last_known_good(ctx, exclude)
    if lkg:
        v = credits.validate(ctx.wf, lkg, ctx.now, "incident")
        ev["lkg_validation"] = v
        if v and v["pass_rate"] >= bar(ctx):
            return lkg, {**ev, "validation_run_id": v["validation_run_id"], "source": "last_known_good"}
        exclude.add(lkg)
    if budget_left() < 3 + 2:
        return None, {**ev, "why": "incident credit cap reached"}
    sug = credits.suggest(ctx.wf, f"hallucination flags high on prompt {inc['bad']}; need a safer prompt that keeps "
                                  "customers engaged (not a word-for-word script)", ctx.now)
    if not sug:
        return None, {**ev, "why": "suggest-fixes unavailable"}
    cands = [s for s in sug["suggestions"] if s["prompt_version"] not in exclude]
    strict = lambda s: any(w in (s.get("summary") or "").lower() for w in P.STRICT_PROMPT_WORDS)
    ordered = [s for s in cands if not strict(s)] + [s for s in cands if strict(s)]
    passing = []
    for s in ordered:
        if strict(s) and passing:
            break
        if budget_left() < 2:
            break
        v = credits.validate(ctx.wf, s["prompt_version"], ctx.now, "incident")
        if v and v["pass_rate"] >= bar(ctx):
            passing.append((strict(s), -v["pass_rate"], s["prompt_version"], v))
    ev["suggestions"] = sug["suggestions"]
    if not passing:
        return None, {**ev, "why": "no suggested prompt passed validation"}
    passing.sort()
    _, _, p, v = passing[0]
    return p, {**ev, "validation_run_id": v["validation_run_id"], "source": "llm_suggestion", "validation": v}


def tick(ctx: WfCtx, credits: Credits) -> dict | None:
    """Detect and advance the incident for this workflow. Returns a fix request {prompt, evidence} or None."""
    inc = open_incident(ctx)
    live = ctx.cfg["prompt_version"]
    if inc is None:
        cs = hallucination_cusum(ctx)
        if cs["alarm"] and cs["connected"] >= 100:
            start(ctx, live, "cusum", cs)
            inc = open_incident(ctx)
        else:
            return None
    if live != inc["bad"] and inc["state"] in ("open", "fix_proposed", "no_fix"):
        # someone (supervisor or an approved fix) already moved off the bad prompt
        with ctx.store.tx():
            set_incident(ctx.store, inc["id"], state="verifying", fix_prompt=live,
                         detail={**inc["detail"], "verify_from": ctx.now})
        inc = open_incident(ctx)
    if inc["state"] == "verifying":
        since = inc["detail"].get("verify_from", ctx.now)
        r = ctx.store.q1("SELECT COALESCE(SUM(conn),0), COALESCE(SUM(flag),0), COALESCE(SUM(eng),0) FROM "
                         "prompt_counts WHERE wf=? AND prompt=? AND hour_idx >= ?", (ctx.wf, live, since // 60))
        n, k, e = r
        p0, _ = flag_baseline(ctx)
        if n >= P.FIX_VERIFY_CONNECTED:
            ok = k / n <= p0 + P.LKG_FLAG_MARGIN
            with ctx.store.tx():
                if ok:
                    set_incident(ctx.store, inc["id"], state="resolved", closed_minute=ctx.now)
                    ctx.store.decide(ctx.wf, ctx.now, "incident_resolved",
                                     f"flag rate {k / n:.2%} on {live} over {n} calls (baseline {p0:.2%}); "
                                     f"engagement {e / n:.1%}", {"connected": n, "flagged": k, "engaged": e})
                else:
                    fails = inc["detail"].get("failed_fixes", []) + [live]
                    set_incident(ctx.store, inc["id"], state="open", bad_prompt=live,
                                 detail={**inc["detail"], "failed_fixes": fails})
                    ctx.store.decide(ctx.wf, ctx.now, "incident_fix_failed", f"{live} still flags {k / n:.2%}",
                                     {"connected": n, "flagged": k})
        return None
    if inc["state"] == "fix_proposed":
        return None          # governance tracks the proposal; on failure it sets state back to open
    if inc["state"] in ("open", "no_fix"):
        if inc["state"] == "no_fix" and ctx.now - inc["detail"].get("no_fix_at", ctx.now) < 120:
            return None
        fix, ev = choose_fix(ctx, credits, inc)
        with ctx.store.tx():
            if fix is None:
                set_incident(ctx.store, inc["id"], state="no_fix", detail={**inc["detail"], "no_fix_at": ctx.now,
                                                                           "last": ev})
                ctx.store.decide(ctx.wf, ctx.now, "incident_no_fix", ev.get("why", "no fix"), ev)
                ctx.store.alert(ctx.wf, ctx.now, "INCIDENT_UNFIXED", True,
                                f"unsafe prompt {inc['bad']} live; no validated fix ({ev.get('why')})")
                return None
            ctx.store.decide(ctx.wf, ctx.now, "incident_fix_chosen", f"revert {inc['bad']} -> {fix} ({ev['source']})",
                             ev)
        return {"incident": inc["id"], "prompt": fix, "evidence": ev, "bad": inc["bad"]}
    return None
