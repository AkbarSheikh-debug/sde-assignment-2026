"""Governance client: proposal lifecycle, outbox, deterministic ids, caps, timing, reconcile.

State machine (proposals.state):

    PREPARED --POST--> SUBMITTED --200--> PENDING --decided--> APPROVED --apply--> APPLYING --> APPLIED
        |                  |                  |                     |                   (409) -> STALE
        |                  |  (409 dup) -> reconcile by proposal_id |  (410) -> LAPSED
        |                  |  (409 stale) -> STALE                  `-- base moved -> STALE (never POSTed)
        |                  `-- 4xx -> BLOCKED                  `-> REJECTED | EXPIRED
     auto route: PREPARED -> SUBMITTED -> APPLIED | REFUSED (403) | STALE (409)

Every row is written *before* the request that it describes (hard rule 6). A row left in SUBMITTED or
APPLYING after a crash is reconciled against the API on the next start; nothing is ever sent twice under a
new id, and a retried id that the simulator already holds comes back 409 duplicate -> reconcile.
"""
from __future__ import annotations

import hashlib
import json

import httpx

from . import params as P
from .client import SimClient
from .store import Store, dumps

INFLIGHT = ("PREPARED", "SUBMITTED", "PENDING", "APPROVED", "APPLYING")
TERMINAL = ("APPLIED", "REJECTED", "EXPIRED", "LAPSED", "STALE", "BLOCKED", "REFUSED")
MIN = 1440


def content_key(wf: str, base_version: int, payload: dict) -> str:
    return hashlib.sha256(f"{wf}|{base_version}|{dumps(payload)}".encode()).hexdigest()[:24]


def proposal_id(wf: str, base_version: int, attempt_no: int, payload: dict) -> str:
    h = hashlib.sha256(f"{wf}|{base_version}|{attempt_no}|{dumps(payload)}".encode()).hexdigest()[:16]
    return f"p_{h}"


def parse_ts(ts: str | None) -> int | None:
    if not ts:
        return None
    from .ingest import TimeParser
    return TimeParser().minute(ts)


class Governance:
    def __init__(self, store: Store, client: SimClient | None, plan_only: bool = False):
        self.s = store
        self.c = client
        self.plan_only = plan_only

    # ------------------------------------------------------------------ policy helpers
    def caps(self, spec: dict) -> tuple[int, int]:
        """(requests allowed in 24h, of which routine). One slot is kept for incidents when cap >= 2."""
        cap = int(spec["governance"]["max_requests_per_24h"])
        ov = P.STAKEHOLDER_OVERLAYS.get(spec.get("tenant_id"), {})
        if "max_requests_per_day" in ov:
            cap = min(cap, int(ov["max_requests_per_day"]))
        routine = cap - 1 if cap >= 2 else 0
        return cap, routine

    def requests_last_24h(self, wf: str, now: int) -> int:
        # F6: every request the simulator accepted counts, whatever its outcome. Unknown outcomes count too.
        return self.s.q1("SELECT COUNT(*) FROM proposals WHERE wf=? AND route='approval' AND submitted_minute IS NOT "
                         "NULL AND submitted_minute > ? AND state NOT IN ('BLOCKED','PREPARED') "
                         "AND (approval_id IS NOT NULL OR state='SUBMITTED')", (wf, now - MIN))[0]

    def routine_window(self, spec: dict, now: int) -> bool:
        lo, hi = spec["governance"]["approver_hours"]
        ov = P.STAKEHOLDER_OVERLAYS.get(spec.get("tenant_id"), {})
        lo = max(lo, ov.get("earliest_hour", lo))
        mod = now % MIN
        return lo * 60 <= mod < hi * 60 - P.APPROVAL_CLOSE_BUFFER_MIN

    def can_request(self, spec: dict, wf: str, now: int, urgent: bool) -> tuple[bool, str]:
        cap, routine = self.caps(spec)
        used = self.requests_last_24h(wf, now)
        if urgent:
            return (used + 1 <= cap, f"{used}/{cap} requests used in 24h")
        if not self.routine_window(spec, now):
            return False, "outside routine approval window"
        if used + 1 > routine:
            return False, f"routine slots used ({used}/{routine}, 1 kept for incidents)"
        last = self.s.q1("SELECT MAX(submitted_minute) FROM proposals WHERE wf=? AND route='approval' AND urgent=0 "
                         "AND approval_id IS NOT NULL", (wf,))[0]
        if last is not None and now - last < P.ROUTINE_MIN_GAP_MINUTES:
            return False, "routine request sent less than 4h ago"
        return True, f"{used}/{cap} requests used in 24h"

    def inflight(self, wf: str) -> list[tuple]:
        return self.s.q("SELECT proposal_id, route, state, urgent, base_version FROM proposals WHERE wf=? AND state IN "
                        "(%s) ORDER BY created_minute, proposal_id" % ",".join("?" * len(INFLIGHT)), (wf, *INFLIGHT))

    def parked(self, wf: str, ckey: str, now: int) -> bool:
        r = self.s.q1("SELECT until_minute FROM parked WHERE wf=? AND intent_key=?", (wf, ckey))
        return bool(r and r[0] is not None and r[0] > now)

    # ------------------------------------------------------------------ create
    def prepare(self, wf: str, route: str, base_version: int, payload: dict, intents: list[dict], urgent: bool,
                now: int, extra: dict | None = None) -> str:
        """Write the outbox row. Returns proposal_id. Same content after a failure gets a new attempt_no."""
        ck = content_key(wf, base_version, payload)
        attempt = self.s.q1("SELECT COUNT(*) FROM proposals WHERE content_key=?", (ck,))[0]
        pid = proposal_id(wf, base_version, attempt, payload)
        with self.s.tx():
            self.s.x("INSERT INTO proposals(proposal_id, wf, route, base_version, attempt_no, content_key, intents_json, "
                     "payload_json, state, urgent, created_minute, updated_minute, detail) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, wf, route, base_version, attempt, ck, dumps(intents), dumps(payload), "PREPARED",
                      int(urgent), now, now, dumps(extra or {})))
            self.s.decide(wf, now, f"propose_{route}", payload.get("change_summary") or payload.get("rationale", ""),
                          {"intents": [i["key"] for i in intents], "dims": (extra or {}).get("dims"),
                           "attempt_no": attempt, "urgent": urgent}, pid)
        return pid

    def _set(self, pid: str, now: int, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        self.s.x(f"UPDATE proposals SET {sets}, updated_minute=? WHERE proposal_id=?", (*cols.values(), now, pid))

    def row(self, pid: str) -> dict:
        cur = self.s.db.execute("SELECT * FROM proposals WHERE proposal_id=?", (pid,))
        names = [d[0] for d in cur.description]
        r = cur.fetchone()
        return dict(zip(names, r)) if r else {}

    # ------------------------------------------------------------------ send
    def send(self, pid: str, now: int, on_applied=None) -> str:
        """Push a PREPARED/SUBMITTED row to the simulator. Returns the new state."""
        r = self.row(pid)
        if self.plan_only:
            with self.s.tx():
                self._set(pid, now, state="BLOCKED", detail=dumps({"plan_only": True}))
            return "BLOCKED"
        payload = json.loads(r["payload_json"])
        with self.s.tx():
            self._set(pid, now, state="SUBMITTED", submitted_minute=now)
        try:
            if r["route"] == "approval":
                resp = self.c.send("POST", "/approvals", payload)
            else:
                resp = self.c.send("PUT", f"/workflows/{r['wf']}/config", payload)
        except httpx.TransportError:
            return "SUBMITTED"         # outcome unknown; reconcile() resolves it next tick
        return self._record_send(r, resp, now, on_applied)

    def _record_send(self, r: dict, resp, now: int, on_applied) -> str:
        pid, wf = r["proposal_id"], r["wf"]
        with self.s.tx():
            if resp.ok and r["route"] == "approval":
                b = resp.body
                self._set(pid, now, state="PENDING", approval_id=b["approval_id"])
                self.s.decide(wf, now, "approval_requested", f"{b['approval_id']} pending", {"response": b}, pid)
                return "PENDING"
            if resp.ok:
                self._set(pid, now, state="APPLIED", applied_version=resp.body["version"], decided_minute=now)
                self.s.decide(wf, now, "auto_applied", f"v{resp.body['version']} {resp.body['changed_dims']}",
                              {"response": resp.body}, pid)
                if on_applied:
                    on_applied(pid, resp.body["version"])
                return "APPLIED"
            if resp.status == 409 and resp.code == "duplicate_proposal_id":
                self.s.decide(wf, now, "duplicate_id", "simulator already has this id; reconciling", {}, pid)
                return "SUBMITTED"     # leave for reconcile
            if resp.status == 409 and resp.code == "stale_base_version":
                self._set(pid, now, state="STALE", detail=dumps(resp.body))
                self.s.decide(wf, now, "stale_base", "config moved before our request landed", resp.body, pid)
                return "STALE"
            if resp.status == 403:
                self._set(pid, now, state="REFUSED", detail=dumps(resp.body))
                self.s.decide(wf, now, "auto_apply_refused", "policy check disagreed with ours", resp.body, pid)
                self.s.alert(wf, now, "AUTO_APPLY_REFUSED", False, f"{pid}: {resp.body}")
                return "REFUSED"
            if resp.status == 402:
                self._set(pid, now, state="BLOCKED", detail=dumps(resp.body))
                self.s.alert(wf, now, "OUT_OF_CREDITS", True, str(resp.body))
                return "BLOCKED"
            self._set(pid, now, state="BLOCKED", detail=dumps({"status": resp.status, "body": resp.body}))
            self.s.decide(wf, now, "request_failed", f"{resp.status} {resp.code}", {"body": resp.body}, pid)
            return "BLOCKED"

    # ------------------------------------------------------------------ reconcile & poll
    def reconcile(self, now: int, on_applied=None):
        """Resolve rows whose outcome is unknown (crash or lost response). Called at start and every tick."""
        if self.plan_only:
            return
        for pid, wf, route, state in self.s.q("SELECT proposal_id, wf, route, state FROM proposals WHERE state IN "
                                              "('PREPARED','SUBMITTED','APPLYING') ORDER BY created_minute, proposal_id"):
            r = self.row(pid)
            if route == "approval" and state in ("PREPARED", "SUBMITTED"):
                found = next((a for a in self.c.approvals(wf) if a["proposal_id"] == pid), None)
                if found:
                    with self.s.tx():
                        self._set(pid, now, state="PENDING", approval_id=found["approval_id"],
                                  submitted_minute=r["submitted_minute"] or parse_ts(found["created_at"]))
                        self.s.decide(wf, now, "reconciled", f"found {found['approval_id']} for our id", found, pid)
                elif state == "SUBMITTED" or r["created_minute"] == now:
                    self.send(pid, now, on_applied)     # never reached the simulator: resend the same id
                else:
                    with self.s.tx():
                        self._set(pid, now, state="STALE", detail=dumps({"reason": "prepared but not sent in time"}))
            elif route == "auto" and state in ("PREPARED", "SUBMITTED"):
                hist = self.c.config_history(wf)
                v = next((h for h in hist if h.get("proposal_id") == pid), None)
                if v:
                    with self.s.tx():
                        self._set(pid, now, state="APPLIED", applied_version=v["version"], decided_minute=now)
                        self.s.decide(wf, now, "reconciled", f"auto-apply landed as v{v['version']}", v, pid)
                        if on_applied:
                            on_applied(pid, v["version"])
                elif r["created_minute"] == now or state == "SUBMITTED":
                    if hist[-1]["version"] == r["base_version"]:
                        self.send(pid, now, on_applied)
                    else:
                        with self.s.tx():
                            self._set(pid, now, state="STALE")
                else:
                    with self.s.tx():
                        self._set(pid, now, state="STALE")
            elif state == "APPLYING":
                self._apply(pid, now, on_applied, current_version=None)

    def poll(self, now: int, live_version: dict[str, int], on_applied=None):
        """Advance every PENDING/APPROVED proposal: read its approval, apply when approved."""
        if self.plan_only:
            return
        for pid, wf, state, aid in self.s.q("SELECT proposal_id, wf, state, approval_id FROM proposals WHERE state IN "
                                            "('PENDING','APPROVED') ORDER BY created_minute, proposal_id"):
            a = self.c.approval(aid)
            if not isinstance(a, dict):
                continue
            st = a["status"]
            with self.s.tx():
                if st == "pending":
                    continue
                if st in ("rejected", "expired"):
                    self._set(pid, now, state=st.upper(), decided_minute=parse_ts(a.get("decided_at")) or now,
                              detail=dumps({"reasons": a.get("reasons", [])}))
                    self.s.decide(wf, now, f"approval_{st}", "; ".join(a.get("reasons") or []) or st, a, pid)
                    self._note_rejection(wf, pid, now)
                    continue
                if st == "lapsed":
                    self._set(pid, now, state="LAPSED")
                    self.s.decide(wf, now, "approval_lapsed", "apply window passed", a, pid)
                    continue
                if st == "applied":
                    self._set(pid, now, state="APPLIED")
                    continue
                if st == "approved" and state == "PENDING":
                    self._set(pid, now, state="APPROVED", decided_minute=parse_ts(a.get("decided_at")),
                              apply_by_minute=parse_ts(a.get("apply_by")))
                    self.s.decide(wf, now, "approval_approved", f"{aid} approved", a, pid)
            if st == "approved":
                self._apply(pid, now, on_applied, current_version=live_version.get(wf))

    def _note_rejection(self, wf: str, pid: str, now: int):
        r = self.row(pid)
        ck = content_key(wf, r["base_version"], json.loads(r["payload_json"])["proposed_config"])
        r = self.s.q1("SELECT rejections FROM parked WHERE wf=? AND intent_key=?", (wf, ck))
        n = (r[0] if r else 0) + 1
        until = now + P.REJECT_PARK_HOURS * 60 if n >= 2 else None
        self.s.x("INSERT INTO parked VALUES (?,?,?,?) ON CONFLICT(wf, intent_key) DO UPDATE SET "
                 "rejections=excluded.rejections, until_minute=excluded.until_minute", (wf, ck, until, n))
        if until:
            self.s.alert(wf, now, "PROPOSAL_PARKED", False, f"same change rejected twice; parked 72h ({pid})")

    def _apply(self, pid: str, now: int, on_applied, current_version: int | None):
        r = self.row(pid)
        wf, aid = r["wf"], r["approval_id"]
        hist = self.c.config_history(wf)
        done = next((h for h in hist if h.get("approval_id") == aid), None)
        if done:                                    # applied before a crash: record, never re-apply
            with self.s.tx():
                self._set(pid, now, state="APPLIED", applied_version=done["version"])
                self.s.decide(wf, now, "reconciled", f"{aid} already applied as v{done['version']}", done, pid)
                if on_applied:
                    on_applied(pid, done["version"])
            return
        live = hist[-1]["version"]
        if live != r["base_version"]:
            with self.s.tx():
                self._set(pid, now, state="STALE", detail=dumps({"base": r["base_version"], "live": live}))
                self.s.decide(wf, now, "stale_base", f"approved on v{r['base_version']} but live is v{live}; "
                                                    "re-plan on the new base", {"live": live}, pid)
            return
        with self.s.tx():
            self._set(pid, now, state="APPLYING")
            self.s.decide(wf, now, "apply", f"applying {aid}", {"approval_id": aid}, pid)
        try:
            resp = self.c.send("POST", f"/workflows/{wf}/apply", {"approval_id": aid})
        except httpx.TransportError:
            return                                   # APPLYING: next reconcile checks history first
        with self.s.tx():
            if resp.ok:
                self._set(pid, now, state="APPLIED", applied_version=resp.body["version"])
                self.s.decide(wf, now, "applied", f"v{resp.body['version']} {resp.body['changed_dims']}",
                              resp.body, pid)
                if on_applied:
                    on_applied(pid, resp.body["version"])
            elif resp.status == 410:
                self._set(pid, now, state="LAPSED")
                self.s.decide(wf, now, "approval_lapsed", "410 on apply", resp.body, pid)
                self.s.alert(wf, now, "APPLY_DEADLINE", True, f"{aid} lapsed before apply")
            elif resp.status == 409 and resp.code == "stale_base_version":
                self._set(pid, now, state="STALE", detail=dumps(resp.body))
                self.s.decide(wf, now, "stale_base", "409 on apply", resp.body, pid)
            else:
                self._set(pid, now, state="BLOCKED", detail=dumps({"status": resp.status, "body": resp.body}))
                self.s.decide(wf, now, "apply_failed", f"{resp.status}", resp.body, pid)
