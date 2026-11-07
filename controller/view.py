"""Supervisor view and loop metrics. Read-only over the same SQLite file; stdlib only.

    python -m controller.view --db state/controller.db --port 8710      (or main --view-port 8710)

  /                 one line per workflow
  /wf/<id>          one screen: being tested, winning, waiting for you, changed today, caller IDs, incidents
  /metrics          Prometheus text: ingest lag, decisions/h, credits vs reserve, approvals in flight, alerts
"""
from __future__ import annotations

import argparse
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import params as P
from .audit import fmt_min
from .store import Store

CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px 24px;color:#1d1d1f;background:#fafafa}
h1{font-size:20px}h2{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;width:100%;background:#fff}td,th{border:1px solid #e3e3e3;padding:4px 8px;
text-align:left;vertical-align:top}th{background:#f2f2f2}.ok{color:#11743d}.bad{color:#b3261e}.muted{color:#777}
.pill{display:inline-block;padding:0 6px;border-radius:8px;background:#eee}a{color:#0b57d0}"""


def _table(head, rows):
    if not rows:
        return "<p class=muted>none</p>"
    h = "".join(f"<th>{html.escape(str(x))}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def _cfg(s: Store, wf: str):
    v = s.latest_version(wf)
    return v, s.config_at(wf, v) or {}


def page_index(s: Store) -> str:
    now = s.get_meta("last_decided", 0)
    rows = []
    for wf in s.workflow_ids():
        spec = s.spec(wf)
        v, cfg = _cfg(s, wf)
        inflight = s.q1("SELECT COUNT(*) FROM proposals WHERE wf=? AND state IN ('PENDING','APPROVED')", (wf,))[0]
        inc = s.q1("SELECT COUNT(*) FROM incidents WHERE wf=? AND state!='resolved'", (wf,))[0]
        k = s.q1("SELECT dials, conn, eng FROM day_kpi WHERE wf=? AND day=?", (wf, now // 1440)) or (0, 0, 0)
        rows.append([f"<a href='/wf/{wf}'>{wf}</a>", spec.get("tenant_id"), spec["objective"]["primary"], f"v{v}",
                     f"{k[1] / k[0]:.1%}" if k[0] else "-", f"{k[2] / k[1]:.1%}" if k[1] else "-",
                     str(inflight), f"<span class=bad>{inc} open</span>" if inc else "<span class=ok>none</span>"])
    return (f"<h1>Outcome loop — {fmt_min(now)}</h1>" +
            _table(["workflow", "tenant", "primary KPI", "config", "pickup today", "engagement today",
                    "waiting for approval", "incidents"], rows) + "<p><a href='/metrics'>/metrics</a></p>")


def page_wf(s: Store, wf: str) -> str:
    now = s.get_meta("last_decided", 0)
    spec = s.spec(wf)
    v, cfg = _cfg(s, wf)
    out = [f"<h1>{wf} <span class=muted>({spec.get('tenant_id')}, {spec.get('use_case')})</span></h1>",
           f"<p>Primary KPI <b>{spec['objective']['primary']}</b>, guardrail {spec['objective'].get('guardrail')}. "
           f"Live config v{v}, prompt <b>{cfg.get('prompt_version')}</b>. As of {fmt_min(now)}.</p>"]
    k = {d: (dl, c, e) for d, dl, c, e in s.q("SELECT day, dials, conn, eng FROM day_kpi WHERE wf=? AND day>=? "
                                                 "ORDER BY day", (wf, now // 1440 - 6))}
    out.append("<h2>KPIs by day (pickup = connected/dials, engagement = engaged/connected; never blended)</h2>")
    out.append(_table(["day", "dials", "pickup", "engagement"],
                      [[f"D{d}", f"{x[0]:,}", f"{x[1] / x[0]:.1%}" if x[0] else "-",
                        f"{x[2] / x[1]:.1%}" if x[1] else "-"] for d, x in sorted(k.items())]))
    arms = s.q("SELECT scope, dim, arm, status, since_minute, reason FROM arms WHERE wf=? ORDER BY scope, dim, arm", (wf,))

    def share(scope, dim, arm):
        from .cohorts import scope_dist
        try:
            return scope_dist(cfg, scope, dim).get(arm, 0.0)
        except Exception:
            return 0.0
    testing = [[sc, d, a, f"{share(sc, d, a):.0%}", st, fmt_min(m), html.escape(r or "")]
               for sc, d, a, st, m, r in arms if st in ("probe", "ramp")]
    winning = [[sc, d, a, f"{share(sc, d, a):.0%}", st, fmt_min(m), html.escape(r or "")]
               for sc, d, a, st, m, r in arms if st in ("winner", "control", "holdout")]
    out.append("<h2>Being tested</h2>" + _table(["cohort", "dimension", "arm", "share now", "status", "since", "why"],
                                                 testing))
    out.append("<h2>Winning / in control</h2>" + _table(["cohort", "dimension", "arm", "share now", "status", "since",
                                                         "why"], winning))
    wait = s.q("SELECT proposal_id, approval_id, state, submitted_minute, apply_by_minute, payload_json, urgent FROM "
               "proposals WHERE wf=? AND route='approval' AND state IN ('PENDING','APPROVED','APPLYING') "
               "ORDER BY created_minute", (wf,))
    rows = []
    for pid, aid, st, sm, ab, pj, urg in wait:
        p = json.loads(pj)
        rows.append([aid or pid, ("<b class=bad>SAFETY</b> " if urg else "") + html.escape(p.get("change_summary", "")),
                     f"{p.get('impact_score')}", f"{p.get('affected_leads'):,}", st, fmt_min(sm), fmt_min(ab)])
    out.append("<h2>What we want from you</h2>" + _table(["request", "summary", "impact", "affected leads", "state",
                                                          "sent", "apply by"], rows))
    parked = s.q("SELECT intent_key, until_minute FROM parked WHERE wf=? AND until_minute > ?", (wf, now))
    if parked:
        out.append("<p class=muted>Parked after two rejections: " + ", ".join(f"until {fmt_min(u)}" for _, u in parked)
                   + "</p>")
    day0 = (now // 1440) * 1440
    ch = s.q("SELECT version, actor, dims, applied_minute, proposal_id FROM config_versions WHERE wf=? AND "
             "applied_minute >= ? ORDER BY version", (wf, day0))
    rows = []
    for ver, actor, dims, m, pid in ch:
        summ = ""
        if pid:
            r = s.q1("SELECT payload_json FROM proposals WHERE proposal_id=?", (pid,))
            if r:
                pj = json.loads(r[0])
                summ = pj.get("change_summary") or pj.get("rationale", "")[:200]
        elif actor == "supervisor":
            summ = "your edit (kept as the new control)"
        rows.append([f"v{ver}", fmt_min(m), actor, ", ".join(json.loads(dims or "[]")), html.escape(summ)])
    out.append("<h2>What changed today</h2>" + _table(["version", "at", "by", "dims", "what and why"], rows))
    dids = s.q("SELECT did, status, since_minute, reason FROM did_state WHERE wf=? ORDER BY did", (wf,))
    cid = (cfg.get("allocations", {}).get("default", {}) or {}).get("caller_id", {})
    out.append("<h2>Caller IDs</h2>" + _table(["number", "share", "status", "since", "why"],
                                              [[d, f"{cid.get(d, 0):.0%}", st, fmt_min(m), html.escape(r or "")]
                                               for d, st, m, r in dids]))
    inc = s.q("SELECT incident_id, opened_minute, bad_prompt, state, fix_prompt, closed_minute FROM incidents WHERE wf=? "
              "ORDER BY opened_minute", (wf,))
    out.append("<h2>Incidents</h2>" + _table(["id", "opened", "unsafe prompt", "state", "fix", "closed"],
                                             [[i, fmt_min(o), b, st, f or "", fmt_min(c)] for i, o, b, st, f, c in inc]))
    al = s.q("SELECT minute, name, pages, message FROM alerts WHERE wf=? ORDER BY seq DESC LIMIT 20", (wf,))
    out.append("<h2>Alerts</h2>" + _table(["at", "alert", "pages?", "message"],
                                          [[fmt_min(m), n, "yes" if p else "no", html.escape(msg)]
                                           for m, n, p, msg in al]))
    return "".join(out) + "<p><a href='/'>all workflows</a></p>"


def metrics(s: Store) -> str:
    now = s.get_meta("last_decided", 0)
    total = float(s.get_meta("credits_total", 0) or 0)
    left = float(s.get_meta("credits_remaining", 0) or 0)
    lines = [
        "# HELP controller_sim_minute Last simulated minute the decision step completed",
        f"controller_sim_minute {now}",
        "# HELP controller_ingest_chunks_behind Chunks listed by the simulator but not yet committed",
        f"controller_ingest_chunks_behind {s.get_meta('chunks_behind', 0)}",
        "# HELP controller_decisions_last_hour Decision rows written in the last 60 sim minutes",
        f"controller_decisions_last_hour {s.q1('SELECT COUNT(*) FROM decisions WHERE minute > ?', (now - 60,))[0]}",
        f"controller_credits_remaining {left}",
        f"controller_credits_incident_reserve {P.CREDIT_INCIDENT_RESERVE_SHARE * total}",
    ]
    for wf, st, n in s.q("SELECT wf, state, COUNT(*) FROM proposals GROUP BY wf, state ORDER BY wf, state"):
        lines.append(f'controller_proposals{{workflow="{wf}",state="{st}"}} {n}')
    for name, pages, n in s.q("SELECT name, pages, COUNT(*) FROM alerts GROUP BY name, pages ORDER BY name"):
        lines.append(f'controller_alerts_total{{alert="{name}",pages="{bool(pages)}"}} {n}')
    ls = s.get_meta("loop_stats", {}) or {}
    for k in ("ingest_s", "decide_s"):
        if k in ls:
            lines.append(f"controller_last_tick_{k} {ls[k]}")
    return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    db = "state/controller.db"

    def log_message(self, *a):
        pass

    def do_GET(self):
        s = Store(self.db)
        try:
            if self.path == "/metrics":
                body, ctype = metrics(s), "text/plain; version=0.0.4"
            elif self.path.startswith("/wf/"):
                body, ctype = f"<style>{CSS}</style>" + page_wf(s, self.path[4:]), "text/html"
            else:
                body, ctype = f"<style>{CSS}</style>" + page_index(s), "text/html"
            code = 200
        except Exception as e:      # the view must never take the loop down; show the error instead
            body, ctype, code = f"error: {html.escape(str(e))}", "text/plain", 500
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(db: str, port: int):
    _Handler.db = db
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()


def serve_background(db: str, port: int):
    t = threading.Thread(target=serve, args=(db, port), daemon=True)
    t.start()
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controller.view")
    ap.add_argument("--db", default="state/controller.db")
    ap.add_argument("--port", type=int, default=8710)
    a = ap.parse_args(argv)
    print(f"supervisor view on http://127.0.0.1:{a.port}/")
    serve(a.db, a.port)


if __name__ == "__main__":
    main()
