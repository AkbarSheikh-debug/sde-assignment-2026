"""Answer audit questions from our own store (the simulator's logs don't count).

    python -m controller.explain --workflow ks_cancel --at "D3 14:00"     why did it change, who approved, what next
    python -m controller.explain --lead "ks_cancel:l00012345" [--day D4]  what this customer heard and why it was live
    python -m controller.explain --pending | --credits | --alerts | --decisions W [--day D3]
"""
from __future__ import annotations

import argparse
import json
import sys

from .store import Store


def fmt_min(m: int | None) -> str:
    if m is None:
        return "-"
    d, r = divmod(m, 1440)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def parse_when(s: str) -> int:
    day, hm = s.strip().split()
    h, m = hm.split(":")
    return int(day.lstrip("Dd")) * 1440 + int(h) * 60 + int(m)


def _proposal(s: Store, pid: str | None) -> dict | None:
    if not pid:
        return None
    cur = s.db.execute("SELECT * FROM proposals WHERE proposal_id=?", (pid,))
    names = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(names, r)) if r else None


def explain_change(s: Store, wf: str, at: int, window: int = 60) -> str:
    rows = s.q("SELECT version, actor, dims, proposal_id, approval_id, applied_minute FROM config_versions WHERE wf=? "
               "AND applied_minute BETWEEN ? AND ? ORDER BY version", (wf, at - window, at + window))
    if not rows:
        r = s.q1("SELECT version, actor, dims, proposal_id, approval_id, applied_minute FROM config_versions WHERE wf=? "
                 "AND applied_minute <= ? ORDER BY version DESC LIMIT 1", (wf, at))
        rows = [r] if r else []
        head = f"No change to {wf} within {window} min of {fmt_min(at)}. The config live at that time:"
    else:
        head = f"Changes to {wf} within {window} min of {fmt_min(at)}:"
    out = [head]
    for v, actor, dims, pid, aid, m in rows:
        out.append(f"\n== v{v} applied {fmt_min(m)} by {actor}; changed {', '.join(json.loads(dims or '[]'))}")
        if actor == "supervisor":
            d = s.q1("SELECT summary FROM decisions WHERE wf=? AND kind='supervisor_edit' AND minute>=? ORDER BY seq "
                     "LIMIT 1", (wf, m))
            out.append("   WHY: a client supervisor edited the config directly (outside governance). "
                       + (d[0] if d else ""))
            for k, summ in s.q("SELECT kind, summary FROM decisions WHERE wf=? AND minute BETWEEN ? AND ? AND kind IN "
                               "('validate_on_change','incident_opened','incident_fix_chosen') ORDER BY seq",
                               (wf, m, m + 30)):
                out.append(f"   OUR RESPONSE: [{k}] {summ}")
            continue
        p = _proposal(s, pid)
        if not p:
            out.append("   (no proposal record)")
            continue
        payload = json.loads(p["payload_json"])
        out.append(f"   WHAT: {payload.get('change_summary') or payload.get('rationale', '')[:200]}")
        intents = json.loads(p["intents_json"] or "[]")
        out.append("   WHY:")
        for i in intents:
            ev = i.get("evidence") or {}
            evs = ", ".join(f"{k}={ev[k]}" for k in ("lift", "ci80", "n_arm", "n_ctrl", "p_gt_2pct", "ratio", "dials",
                                                     "validation_run_id", "source") if k in ev)
            out.append(f"     - [{i['kind']}] {i['summary']}" + (f"  ({evs})" if evs else ""))
        if p["route"] == "approval":
            dr = payload.get("dry_run", {})
            out.append(f"   PREDICTED: engagement {dr.get('engagement_rate_change')}, pickup {dr.get('pickup_rate_change')};"
                       f" impact_score {payload.get('impact_score')}; affected leads {payload.get('affected_leads')}")
            dec = s.q1("SELECT minute, summary FROM decisions WHERE proposal_id=? AND kind='approval_approved'", (pid,))
            out.append(f"   WHO: requested {fmt_min(p['submitted_minute'])} as {p['approval_id']}; approved by the "
                       f"client approver at {fmt_min(p['decided_minute'])}; applied by the controller at {fmt_min(m)}")
        else:
            out.append(f"   WHO: auto-applied by the controller under the workflow's auto-apply policy "
                       f"(no human approval needed for these dims); proposal {pid}")
        safety = json.loads(p["detail"] or "{}").get("safety") or {}
        if safety:
            out.append("   SAFETY CHECKS: " + ", ".join(f"{k}:{'ok' if c['ok'] else 'FAIL'}" for k, c in safety.items()))
        out.append("   WHAT HAPPENED NEXT:")
        pre = s.q1("SELECT kpi_json FROM post_apply WHERE proposal_id=? AND window='pre'", (pid,))
        for win, kj in s.q("SELECT window, kpi_json FROM post_apply WHERE proposal_id=? AND window!='pre' ORDER BY "
                           "minute", (pid,)):
            k, b = json.loads(kj), json.loads(pre[0]) if pre else {}
            out.append(f"     - {win}: pickup {b.get('pickup_rate')} -> {k.get('pickup_rate')}, engagement "
                       f"{b.get('engagement_rate')} -> {k.get('engagement_rate')} ({k.get('connected')} connected)")
        nxt = s.q("SELECT version, actor, dims, applied_minute, proposal_id FROM config_versions WHERE wf=? AND version>? "
                  "ORDER BY version LIMIT 3", (wf, v))
        for v2, a2, d2, m2, p2 in nxt:
            p2r = _proposal(s, p2)
            summ = json.loads(p2r["payload_json"]).get("change_summary") or json.loads(p2r["payload_json"]).get(
                "rationale", "")[:120] if p2r else ""
            out.append(f"     - then v{v2} at {fmt_min(m2)} by {a2} ({', '.join(json.loads(d2 or '[]'))}): {summ}")
        for k, summ, mm in s.q("SELECT kind, summary, minute FROM decisions WHERE wf=? AND minute BETWEEN ? AND ? AND kind "
                               "IN ('guardrail_breach','incident_resolved','incident_fix_failed','did_quarantined') "
                               "ORDER BY seq", (wf, m, m + 2880)):
            out.append(f"     - {fmt_min(mm)} [{k}] {summ}")
    return "\n".join(out)


def explain_lead(s: Store, lead: str, day: int | None) -> str:
    wf, idx = lead.split(":l")
    idx = int(idx)
    q = "SELECT minute, attempt, cfg_ver, cohort, wm, voice, did, prompt, connected, engaged, flag FROM calls " \
        "WHERE wf=? AND lead_idx=?"
    args: list = [wf, idx]
    if day is not None:
        q += " AND minute >= ? AND minute < ?"
        args += [day * 1440, (day + 1) * 1440]
    calls = s.q(q + " ORDER BY minute", args)
    feats = s.q1("SELECT c.lang, c.city_tier, c.age_band, c.value_band, l.campaign FROM leads l JOIN feat_combos c ON "
                 "c.code=l.feat WHERE l.wf=? AND l.lead_idx=?", (wf, idx))
    out = [f"Lead {lead}" + (f" (lang={feats[0]}, tier={feats[1]}, age={feats[2]}, value={feats[3]}; campaign "
                             f"{feats[4]})" if feats else "")]
    if not calls:
        out.append("  no calls recorded" + (f" on D{day}" if day is not None else ""))
    for m, att, ver, coh, wm, vo, did, pr, conn, eng, flag in calls:
        outcome = "engaged" if eng else "picked up, dropped" if conn else "no answer"
        out.append(f"\n  {fmt_min(m)} attempt {att}: heard welcome {wm}, voice {vo}, prompt {pr}; caller ID {did}; "
                   f"{outcome}{'; QA flagged' if flag else ''}")
        r = s.q1("SELECT actor, proposal_id, applied_minute FROM config_versions WHERE wf=? AND version=?", (wf, ver))
        if not r:
            continue
        actor, pid, am = r
        out.append(f"    config v{ver} (cohort {coh}) was live: set {fmt_min(am)} by {actor}")
        p = _proposal(s, pid)
        if p:
            for i in json.loads(p["intents_json"] or "[]"):
                if any(x in i["key"] for x in (wm, vo, did, pr)) or i["kind"].startswith("did"):
                    out.append(f"      because [{i['kind']}] {i['summary']}")
            if p["approval_id"]:
                out.append(f"      approved as {p['approval_id']} at {fmt_min(p['decided_minute'])}")
        elif actor == "business_default":
            out.append("      the business's default config (before any controller change)")
        scope = {p.split("=")[0]: p.split("=")[1] for p in coh.split("|") if "=" in p}
        for dim, a in (("welcome_message", wm), ("voice", vo)):
            sc = scope.get("wm" if dim == "welcome_message" else "voice", "default")
            r2 = s.q1("SELECT status, reason FROM arms WHERE wf=? AND scope=? AND dim=? AND arm=?", (wf, sc, dim, a))
            if r2:
                out.append(f"      {dim} {a} in {sc}: {r2[0]} ({r2[1]})")
    return "\n".join(out)


def pending(s: Store) -> str:
    rows = s.q("SELECT wf, proposal_id, route, state, approval_id, submitted_minute, apply_by_minute, payload_json FROM "
               "proposals WHERE state IN ('PREPARED','SUBMITTED','PENDING','APPROVED','APPLYING') ORDER BY wf")
    if not rows:
        return "No proposals in flight."
    return "\n".join(f"{wf} {pid} {route} {st} {aid or ''} sent {fmt_min(sm)} apply_by {fmt_min(ab)}: "
                     f"{json.loads(pj).get('change_summary', '')[:120]}" for wf, pid, route, st, aid, sm, ab, pj in rows)


def credits(s: Store) -> str:
    total = float(s.get_meta("credits_total", 0) or 0)
    left = float(s.get_meta("credits_remaining", 0) or 0)
    from . import params as P
    out = [f"credits: {left:.0f} of {total:.0f} left; incident reserve {P.CREDIT_INCIDENT_RESERVE_SHARE * total:.0f}"]
    for wf, kind, reason, n, c in s.q("SELECT wf, kind, reason, COUNT(*), SUM(cost) FROM credit_ops WHERE state='DONE' "
                                      "GROUP BY wf, kind, reason ORDER BY wf"):
        out.append(f"  {wf}: {n} x {kind} ({reason}) = {c:.0f}")
    return "\n".join(out)


def alerts(s: Store) -> str:
    rows = s.q("SELECT minute, wf, name, pages, message FROM alerts ORDER BY seq")
    return "\n".join(f"{fmt_min(m)} {'PAGE' if p else 'ticket'} {n} {wf or ''}: {msg}" for m, wf, n, p, msg in rows) \
        or "No alerts."


def decisions(s: Store, wf: str, day: int | None) -> str:
    q, args = "SELECT minute, kind, summary FROM decisions WHERE wf=?", [wf]
    if day is not None:
        q += " AND minute >= ? AND minute < ?"
        args += [day * 1440, (day + 1) * 1440]
    return "\n".join(f"{fmt_min(m)} [{k}] {summ}" for m, k, summ in s.q(q + " ORDER BY seq", args))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controller.explain")
    ap.add_argument("--db", default="state/controller.db")
    ap.add_argument("--workflow")
    ap.add_argument("--at")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--lead")
    ap.add_argument("--day")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--credits", action="store_true")
    ap.add_argument("--alerts", action="store_true")
    ap.add_argument("--decisions")
    a = ap.parse_args(argv)
    s = Store(a.db)
    day = int(a.day.lstrip("Dd")) if a.day else None
    if a.workflow and a.at:
        print(explain_change(s, a.workflow, parse_when(a.at), a.window))
    elif a.lead:
        print(explain_lead(s, a.lead, day))
    elif a.pending:
        print(pending(s))
    elif a.credits:
        print(credits(s))
    elif a.alerts:
        print(alerts(s))
    elif a.decisions:
        print(decisions(s, a.decisions, day))
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
