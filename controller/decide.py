"""The decision step for one simulated minute: detect -> plan -> build candidates -> safety -> route.

Routing rules (BUILD_PLAN 5.9/6):
- One bundled approval per workflow at a time, priority safety > caller ID > rollback > promotion > arms > schedule.
- Auto-apply only where every changed dim is allowed, cohorts unchanged, the step is within max_shift (we split
  larger moves into steps), and, for welcome/voice, every increase stays inside an envelope a human approved.
- While an approval is in flight, routine auto-applies wait (they would make it stale). Urgent caller-ID drains
  do not wait.
"""
from __future__ import annotations

import copy
import json

from . import incidents
from . import params as P
from .cohorts import Layout, Registry, scope_dist, scope_sched
from .config_build import changed_dims, max_shift, normalise, reuse_exact, same, step_toward
from .context import WfCtx
from .governance import Governance
from .ingest import Ingestor
from .incidents import Credits
from .plan import (EngPlan, Intent, arm_states, caller_plan, did_window, engagement_plan, schedule_is_live,
                   schedule_plan, schedule_targets)
from .proposal import package
from .safety import evaluate
from .store import Store, dumps


class Controller:
    def __init__(self, store: Store, client, plan_only: bool = False):
        self.s = store
        self.c = client
        self.plan_only = plan_only
        self.gov = Governance(store, client, plan_only)
        self.credits = Credits(store, client, plan_only)
        self.reg = Registry(store)
        self.ing = Ingestor(store)

    # ================================================================== bootstrap & ingest
    def bootstrap(self):
        if self.s.get_meta("bootstrapped"):
            return
        st = self.c.state()
        with self.s.tx():
            for w in self.c.workflows():
                self.s.x("INSERT OR REPLACE INTO workflows VALUES (?,?)", (w["workflow_id"], dumps(w)))
            self.s.set_meta("credits_total", st["credits_remaining"])
            self.s.set_meta("credits_remaining", st["credits_remaining"])
            self.s.set_meta("scenario", st["scenario"])
        for wf in self.s.workflow_ids():
            self.refresh_config(wf, st["sim_minute"])
        with self.s.tx():
            self.s.set_meta("bootstrapped", True)
            self.s.decide(None, st["sim_minute"], "bootstrap", f"{len(self.s.workflow_ids())} workflows",
                          {"scenario": st["scenario"]})

    def ingest(self) -> int:
        cur = self.ing.cursor
        chunks = self.c.chunks("history", cur) + self.c.chunks("events", cur)
        n = 0
        for ch in sorted(chunks, key=lambda c: c["chunk"]):
            if ch["chunk"] <= self.ing.cursor:
                continue
            self.ing.apply_chunk(ch["chunk"], self.c.chunk_lines(ch["chunk"]), ch["kind"])
            n += ch["events"]
        return n

    def refresh_config(self, wf: str, now: int):
        """Snapshot every config version we have not seen (F3: history has no configs, so we keep them)."""
        live = self.c.config(wf)
        hist = self.c.config_history(wf)
        with self.s.tx():
            for h in hist:
                have = self.s.q1("SELECT config_json FROM config_versions WHERE wf=? AND version=?", (wf, h["version"]))
                if have:
                    if have[0] is None and h["version"] == live["version"]:
                        # the simulator's normalised bytes, not our payload: exact-equality checks depend on it
                        self.s.x("UPDATE config_versions SET config_json=? WHERE wf=? AND version=?",
                                 (dumps(live["config"]), wf, h["version"]))
                    continue
                cfg = live["config"] if h["version"] == live["version"] else None
                if cfg is None and h.get("proposal_id"):
                    r = self.s.q1("SELECT payload_json FROM proposals WHERE proposal_id=?", (h["proposal_id"],))
                    cfg = json.loads(r[0])["proposed_config"] if r else None
                applied = _minute(h.get("applied_at"))
                self.s.x("INSERT INTO config_versions VALUES (?,?,?,?,?,?,?,?,?)",
                         (wf, h["version"], h["actor"], dumps(h["changed_dims"]), h.get("proposal_id"),
                          h.get("approval_id"), applied, dumps(cfg) if cfg else None, now))
                if h["actor"] == "supervisor":
                    self._supervisor_edit(wf, h, now)
        return live

    def _supervisor_edit(self, wf: str, h: dict, now: int):
        dims = h["changed_dims"]
        self.s.decide(wf, now, "supervisor_edit", f"supervisor changed {dims} (v{h['version']}); it becomes the new "
                      "control, we will not silently revert it", h)
        for d in dims:
            if d in P.ENG_DIMS:
                self.s.x("DELETE FROM arms WHERE wf=? AND dim=?", (wf, d))
                self.s.x("DELETE FROM envelope WHERE wf=? AND dim=?", (wf, d))
        if "prompt_version" in dims:
            self.s.set_meta(f"validate_pending:{wf}", True)

    def handle_control(self, now: int):
        events = self.s.get_meta("pending_control", [])
        changed = sorted({e["workflow_id"] for e in events if e["type"] == "config_changed"})
        for wf in changed:
            self.refresh_config(wf, now)
        if events:
            with self.s.tx():
                self.s.set_meta("pending_control", [])

    # ================================================================== context
    def ctx(self, wf: str, now: int) -> WfCtx:
        v = self.s.latest_version(wf)
        cfg = self.s.config_at(wf, v)
        if cfg is None:
            self.refresh_config(wf, now)
            v = self.s.latest_version(wf)
            cfg = self.s.config_at(wf, v) or self.c.config(wf)["config"]
        return WfCtx(self.s, self.reg, wf, self.s.spec(wf), cfg, v, now, self.ing.combo_values())

    # ================================================================== the decision step
    def step(self, now: int):
        self.gov.reconcile(now, self.on_applied)
        live = {wf: self.s.latest_version(wf) for wf in self.s.workflow_ids()}
        self.gov.poll(now, live, self.on_applied)
        self.handle_control(now)
        for wf in self.s.workflow_ids():
            self.decide_wf(wf, now)
        self.post_apply(now)

    def decide_wf(self, wf: str, now: int):
        ctx = self.ctx(wf, now)
        if self.s.get_meta(f"validate_pending:{wf}"):
            incidents.check_new_prompt(ctx, self.credits, ctx.cfg["prompt_version"])
            with self.s.tx():
                self.s.set_meta(f"validate_pending:{wf}", False)
        self._track_incident_proposal(ctx)
        fix = incidents.tick(ctx, self.credits)
        if fix:
            self.propose_prompt_fix(ctx, fix)
            return
        inc = incidents.open_incident(ctx)
        cp = caller_plan(ctx)
        if cp.status_changes:
            with self.s.tx():
                for did, status, reason, ev in cp.status_changes:
                    self.s.x("INSERT OR REPLACE INTO did_state VALUES (?,?,?,?,?)", (wf, did, status, now, reason))
                    self.s.decide(wf, now, f"did_{status}", f"{did}: {reason}", ev)
                    if status == "quarantined":
                        self.s.alert(wf, now, "DID_QUARANTINED", False, f"{did} drained: {reason}")
        routine = now % P.PLAN_EVERY_MINUTES == 0 and now >= 0
        if not routine and not cp.urgent:
            return
        eng = EngPlan()
        sp = None
        if routine and inc is None:
            eng = engagement_plan(ctx)
            boring = [c for c in eng.arm_changes if c[4] in ("largest share in live config", "already live in config")]
            if boring:
                with self.s.tx():
                    for scope, dim, arm, status, reason in boring:
                        self.s.x("INSERT OR IGNORE INTO arms VALUES (?,?,?,?,?,?,?)",
                                 (wf, scope, dim, arm, status, now, reason))
                eng.arm_changes = [c for c in eng.arm_changes if c not in boring]
            sp = schedule_plan(ctx)
            if sp and (sp.gain < P.SCHEDULE_MIN_GAIN or schedule_is_live(ctx, sp)
                       or ctx.supervisor_touched("schedule", P.SUPERVISOR_RESPECT_HOURS)):
                sp = None
        self.route(ctx, cp, eng, sp)

    # ------------------------------------------------------------------ building candidates
    def build(self, ctx: WfCtx, caller: dict | None, eng: dict, new_overrides: dict, sp, sched_scopes_ok=True):
        ov = {d: list(v) for d, v in ctx.layout.overrides.items()}
        for d, segs in new_overrides.items():
            ov[d] += [s for s in segs if s not in ov[d]]
        tables = {}
        if sp is not None:
            segs, tables = schedule_targets(ctx, sp)
            ov["sched"] = segs
        else:
            for scope in ["default"] + ov["sched"]:
                t = scope_sched(ctx.cfg, scope)
                if t is not None:
                    tables[scope] = t
        dists = {}
        for dim in P.ENG_DIMS:
            for scope in ["default"] + ov[dim]:
                dists[(scope, dim)] = eng.get((scope, dim)) or scope_dist(ctx.cfg, scope, dim)
        dists[("default", "caller_id")] = caller or ctx.cfg["allocations"]["default"]["caller_id"]
        cfg = Layout(ov, ctx.layout.foreign).compile(self.reg, dists, tables, ctx.cfg)
        return reuse_exact(ctx.cfg, normalise(cfg))

    def within_envelope(self, ctx: WfCtx, scope: str, dim: str, new: dict) -> bool:
        cur = scope_dist(ctx.cfg, scope, dim)
        st = arm_states(ctx).get((scope, dim), {})
        for arm, share in new.items():
            if share <= cur.get(arm, 0.0) + 1e-9 or st.get(arm) in ("control", "winner", "holdout"):
                continue
            r = self.s.q1("SELECT max_share FROM envelope WHERE wf=? AND scope=? AND dim=? AND arm=?",
                          (ctx.wf, scope, dim, arm))
            if not r or share > r[0] + 1e-9:
                return False
        return True

    # ------------------------------------------------------------------ routing
    def route(self, ctx: WfCtx, cp, eng: EngPlan, sp):
        wf, now = ctx.wf, ctx.now
        inflight = self.gov.inflight(wf)
        intents = list(cp.intents) + list(eng.intents)
        if sp is not None:
            intents.append(Intent("schedule", "schedule", f"Call each group in its best hours ({sp.feature}): "
                                  f"predicted pickup {sp.gain:+.1%} on history", sp.evidence))
        full = self.build(ctx, cp.target, eng.dists, eng.new_overrides, sp)
        if same(ctx.cfg, full):
            return
        dims = changed_dims(ctx.cfg, full)
        env_ok = all(self.within_envelope(ctx, s, d, v) for (s, d), v in eng.dists.items()
                     if d in P.ENG_DIMS and v != scope_dist(ctx.cfg, s, d))
        auto_full = "cohorts" not in dims and all(ctx.auto_allows(d) for d in dims) and env_ok
        urgent = cp.urgent
        if inflight and not urgent:
            return
        if auto_full:
            self.send_auto(ctx, full, intents, eng, urgent)
            return
        if not inflight:
            ok, why = self.gov.can_request(ctx.spec, wf, now, urgent=urgent and not ctx.auto_allows("caller_id"))
            if ok and not self.gov.parked(wf, _ckey(wf, ctx.version, full), now):
                self.send_approval(ctx, full, intents, eng, sp, urgent)
                return
        # fall back to the auto-appliable subset
        sub_eng = {k: v for k, v in eng.dists.items() if k[0] in (["default"] + ctx.layout.overrides[k[1]])
                   and ctx.auto_allows(k[1]) and self.within_envelope(ctx, k[0], k[1], v)} if not inflight else {}
        caller = cp.target if ctx.auto_allows("caller_id") else None
        sub = self.build(ctx, caller, sub_eng, {}, None)
        if same(ctx.cfg, sub):
            return
        sub_dims = changed_dims(ctx.cfg, sub)
        if all(ctx.auto_allows(d) for d in sub_dims) and "cohorts" not in sub_dims:
            kept = [i for i in intents if i.kind.startswith("did") or any(
                i.key.split(":")[1:3] == [s, d] for (s, d) in sub_eng)]
            sub_plan = EngPlan(dists=sub_eng, arm_changes=[c for c in eng.arm_changes if (c[0], c[1]) in sub_eng],
                               envelope=[])
            self.send_auto(ctx, sub, kept or intents[:1], sub_plan, urgent)

    def send_auto(self, ctx: WfCtx, target: dict, intents, eng: EngPlan, urgent: bool):
        if not intents:     # a change nobody can explain is a bug, never a request
            with self.s.tx():
                self.s.decide(ctx.wf, ctx.now, "blocked", "candidate differs from live config but carries no intent",
                              {"dims": changed_dims(ctx.cfg, target)})
            return
        last =self.s.q1("SELECT MAX(created_minute) FROM proposals WHERE wf=? AND route='auto' AND state='APPLIED'",
                         (ctx.wf,))[0]
        if last is not None and ctx.now - last < P.AUTO_STEP_GAP_MINUTES and not urgent:
            return
        limit = float(ctx.auto.get("max_shift", 0.0))
        step = reuse_exact(ctx.cfg, step_toward(ctx.cfg, target, limit))
        if same(ctx.cfg, step):
            return
        partial = not same(step, target)
        verdict = evaluate(ctx, self.gov, step, route="auto", urgent=urgent)
        rationale = " | ".join(f"[{i.kind}] {i.summary}" for i in sorted(intents, key=lambda i: i.priority))
        if partial:
            rationale += f" | step toward target limited by max_shift={limit}"
        if not verdict.ok:
            with self.s.tx():
                self.s.decide(ctx.wf, ctx.now, "blocked", "safety: " + "; ".join(verdict.failures),
                              {"checks": verdict.checks})
            return
        body = {"base_version": ctx.version, "proposed_config": step, "rationale": rationale[:2000],
                "evidence": {"intents": [i.as_dict() for i in intents]}}
        pid = self.gov.prepare(ctx.wf, "auto", ctx.version, body, [i.as_dict() for i in intents], urgent, ctx.now,
                               {"dims": changed_dims(ctx.cfg, step), "arm_changes": eng.arm_changes,
                                "envelope": [], "partial": partial, "max_shift": round(max_shift(ctx.cfg, step), 4),
                                "safety": verdict.checks})
        body["proposal_id"] = pid
        with self.s.tx():
            self.s.x("UPDATE proposals SET payload_json=? WHERE proposal_id=?", (dumps(body), pid))
        self.gov.send(pid, ctx.now, self.on_applied)

    def send_approval(self, ctx: WfCtx, full: dict, intents, eng: EngPlan, sp, urgent: bool,
                      safety: bool = False, evidence: dict | None = None, incident: str | None = None):
        if not intents:
            with self.s.tx():
                self.s.decide(ctx.wf, ctx.now, "blocked", "candidate differs from live config but carries no intent",
                              {"dims": changed_dims(ctx.cfg, full)})
            return None
        pickup = self._pickup_prediction(ctx, full, sp)
        pkg = package(ctx, ctx.cfg, full, intents, pickup=pickup, safety=safety, evidence=evidence)
        verdict = evaluate(ctx, self.gov, full, route="approval", urgent=urgent, evidence=evidence)
        if not verdict.ok:
            with self.s.tx():
                self.s.decide(ctx.wf, ctx.now, "blocked", "safety: " + "; ".join(verdict.failures),
                              {"checks": verdict.checks})
            return None
        if eng.envelope:
            pkg["rationale"] += " | envelope requested: " + ", ".join(
                f"{d} {a} up to {m:.0%} in {s}" for s, d, a, m in eng.envelope)
        body = {"workflow_id": ctx.wf, "base_version": ctx.version, "proposed_config": full, **pkg}
        pid = self.gov.prepare(ctx.wf, "approval", ctx.version, body, [i.as_dict() for i in intents], urgent, ctx.now,
                               {"dims": changed_dims(ctx.cfg, full), "arm_changes": eng.arm_changes,
                                "envelope": eng.envelope, "incident": incident, "safety": verdict.checks,
                                "impact_score": pkg["impact_score"], "affected_leads": pkg["affected_leads"]})
        body["proposal_id"] = pid
        with self.s.tx():
            self.s.x("UPDATE proposals SET payload_json=? WHERE proposal_id=?", (dumps(body), pid))
        self.gov.send(pid, ctx.now, self.on_applied)
        return pid

    def propose_prompt_fix(self, ctx: WfCtx, fix: dict):
        new = copy.deepcopy(ctx.cfg)
        new["prompt_version"] = fix["prompt"]
        ev = {"validation_run_id": fix["evidence"]["validation_run_id"]}
        v = fix["evidence"].get("lkg_validation") or fix["evidence"].get("validation") or {}
        intent = Intent(f"prompt_revert:{fix['bad']}->{fix['prompt']}", "prompt_revert",
                        f"Safety: replace prompt {fix['bad']} with {fix['prompt']} "
                        f"({fix['evidence']['source'].replace('_', ' ')}): validation pass rate "
                        f"{v.get('pass_rate')} vs bar {incidents.bar(ctx)}", fix["evidence"])
        if ctx.auto_allows("prompt_version"):
            pid = None
            self.send_auto(ctx, new, [intent], EngPlan(), urgent=True)
        else:
            ok, why = self.gov.can_request(ctx.spec, ctx.wf, ctx.now, urgent=True)
            if not ok:
                with self.s.tx():
                    self.s.decide(ctx.wf, ctx.now, "blocked", f"incident fix waiting: {why}", fix["evidence"])
                return
            pid = self.send_approval(ctx, new, [intent], EngPlan(), None, urgent=True, safety=True, evidence=ev,
                                     incident=fix["incident"])
        if pid:
            with self.s.tx():
                incidents.set_incident(self.s, fix["incident"], state="fix_proposed", fix_prompt=fix["prompt"])
                r = self.s.q1("SELECT detail_json FROM incidents WHERE incident_id=?", (fix["incident"],))
                d = json.loads(r[0] or "{}")
                d["proposal_id"] = pid
                incidents.set_incident(self.s, fix["incident"], detail=d)

    def _track_incident_proposal(self, ctx: WfCtx):
        inc = incidents.open_incident(ctx)
        if not inc or inc["state"] != "fix_proposed":
            return
        pid = inc["detail"].get("proposal_id")
        st = self.s.q1("SELECT state FROM proposals WHERE proposal_id=?", (pid,))
        if st and st[0] in ("REJECTED", "EXPIRED", "STALE", "LAPSED", "BLOCKED", "REFUSED"):
            with self.s.tx():
                incidents.set_incident(self.s, inc["id"], state="open")
                self.s.decide(ctx.wf, ctx.now, "incident_reopen", f"fix proposal {pid} ended {st[0]}; re-proposing",
                              {"proposal_id": pid})

    def _pickup_prediction(self, ctx: WfCtx, full: dict, sp) -> tuple[float, float] | None:
        """Relative pickup change from caller-ID spread (hour-matched ratios) and schedule (history replay)."""
        mean = var = 0.0
        old = ctx.cfg["allocations"]["default"]["caller_id"]
        new = full["allocations"]["default"]["caller_id"]
        if old != new:
            stats = did_window(ctx, (ctx.now - 72 * 60) // 60)
            r = lambda d: stats[d].ratio if d in stats and stats[d].dials >= 100 else 1.0
            pool = sum(old.get(d, 0) * r(d) for d in old) or 1.0
            new_r = sum(new.get(d, 0) * r(d) for d in new)
            mean += new_r / pool - 1
            var += 0.03 ** 2 * sum(1 for d in new if d not in stats)
        if sp is not None:
            mean += sp.gain * 0.5           # replay is an upper bound; we claim half of it
            var += (sp.gain * 0.5) ** 2
        return (mean, var ** 0.5) if (mean or var) else None

    # ================================================================== after apply
    def on_applied(self, pid: str, version: int):
        r = self.gov.row(pid)
        wf, now = r["wf"], r["updated_minute"]
        d = json.loads(r["detail"] or "{}")
        for scope, dim, arm, status, reason in d.get("arm_changes", []):
            self.s.x("INSERT OR REPLACE INTO arms VALUES (?,?,?,?,?,?,?)", (wf, scope, dim, arm, status, now, reason))
        if r["route"] == "approval":
            for scope, dim, arm, m in d.get("envelope", []):
                self.s.x("INSERT INTO envelope VALUES (?,?,?,?,?,?) ON CONFLICT(wf,scope,dim,arm) DO UPDATE SET "
                         "max_share=MAX(max_share, excluded.max_share), proposal_id=excluded.proposal_id",
                         (wf, scope, dim, arm, m, pid))
        # config_json stays NULL until refresh_config stores the simulator's own normalised copy
        self.s.x("INSERT OR IGNORE INTO config_versions VALUES (?,?,?,?,?,?,?,?,?)",
                 (wf, version, "controller_approved" if r["route"] == "approval" else "controller_auto",
                  dumps(d.get("dims")), pid, r["approval_id"], now, None, now))
        self.s.x("INSERT OR IGNORE INTO post_apply VALUES (?,?,?,?,?)", (pid, "pre", wf, now, dumps(self._kpi(wf, now - 1440, now))))
        if d.get("incident"):
            r2 = self.s.q1("SELECT detail_json FROM incidents WHERE incident_id=?", (d["incident"],))
            det = json.loads(r2[0] or "{}") if r2 else {}
            det["verify_from"] = now
            incidents.set_incident(self.s, d["incident"], state="verifying", detail=det)

    def _kpi(self, wf: str, a: int, b: int) -> dict:
        p = self.s.q1("SELECT COALESCE(SUM(dials),0), COALESCE(SUM(conn),0) FROM did_counts WHERE wf=? AND hour_idx>=? "
                      "AND hour_idx<?", (wf, a // 60, b // 60))
        e = self.s.q1("SELECT COALESCE(SUM(conn),0), COALESCE(SUM(eng),0), COALESCE(SUM(flag),0) FROM prompt_counts "
                      "WHERE wf=? AND hour_idx>=? AND hour_idx<?", (wf, a // 60, b // 60))
        return {"dials": p[0], "connected": p[1], "engaged": e[1], "flagged": e[2],
                "pickup_rate": round(p[1] / p[0], 4) if p[0] else None,
                "engagement_rate": round(e[1] / e[0], 4) if e[0] else None}

    def post_apply(self, now: int):
        """Measure every applied change at +4h/+24h/+48h; flag a guardrail breach (same 24h shape, F14)."""
        rows = self.s.q("SELECT proposal_id, wf, minute, kpi_json FROM post_apply WHERE window='pre' AND minute >= ? "
                        "ORDER BY minute, proposal_id", (now - 49 * 60,))
        for pid, wf, m, pre_j in rows:
            for label, hrs in (("+4h", 4), ("+24h", 24), ("+48h", 48)):
                if now < m + hrs * 60 or self.s.q1("SELECT 1 FROM post_apply WHERE proposal_id=? AND window=?",
                                                   (pid, label)):
                    continue
                post = self._kpi(wf, m, m + hrs * 60) if hrs < 24 else self._kpi(wf, m + (hrs - 24) * 60, m + hrs * 60)
                pre = json.loads(pre_j)
                with self.s.tx():
                    self.s.x("INSERT OR IGNORE INTO post_apply VALUES (?,?,?,?,?)", (pid, label, wf, now, dumps(post)))
                    if hrs >= 24:
                        self._guardrail(wf, pid, pre, post, now)

    def _guardrail(self, wf: str, pid: str, pre: dict, post: dict, now: int):
        spec = self.s.spec(wf)
        g = spec["objective"].get("guardrail") or {}
        for key, kpi, num, den in (("pickup_rate_max_drop", "pickup_rate", "connected", "dials"),
                                   ("engagement_rate_max_drop", "engagement_rate", "engaged", "connected")):
            if key not in g or not pre.get(kpi) or not post.get(kpi):
                continue
            drop = (pre[kpi] - post[kpi]) / pre[kpi] if P.GUARDRAIL_RELATIVE else pre[kpi] - post[kpi]
            n1, n2 = pre[den], post[den]
            pp = (pre[num] + post[num]) / max(n1 + n2, 1)
            z = (pre[kpi] - post[kpi]) / max((pp * (1 - pp) * (1 / max(n1, 1) + 1 / max(n2, 1))) ** 0.5, 1e-9)
            if drop > g[key] and z > 2:
                self.s.decide(wf, now, "guardrail_breach", f"{kpi} {pre[kpi]:.3f}->{post[kpi]:.3f} after {pid}",
                              {"pre": pre, "post": post, "z": round(z, 2)}, pid)
                self.s.alert(wf, now, "GUARDRAIL_BREACH", False, f"{kpi} fell {drop:.1%} after {pid}")


def _ckey(wf, v, cfg):
    from .governance import content_key
    return content_key(wf, v, cfg)


def _minute(ts):
    from .governance import parse_ts
    return parse_ts(ts)
