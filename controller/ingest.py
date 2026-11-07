"""Event ingestion: chunk cursor, lead->feature joins, counters. Idempotent and restartable.

A chunk is applied in exactly one transaction together with the cursor that records it as done. Kill the
process at any point and the next start resumes at the first chunk not committed.
"""
from __future__ import annotations

import datetime as dt
import json
from array import array
from collections import defaultdict

from .store import Store

BASE_DATE = dt.date(2027, 1, 4)        # D0 per docs/SIMULATOR.md section 1
FEATURE_KEYS = ("lang", "city_tier", "age_band", "value_band")
NO_FEAT = 0xFFFF


class TimeParser:
    """'2027-01-04T08:07:00+05:30' -> simulated minute (D0 00:00 = 0). Cached per date."""

    def __init__(self):
        self._days: dict[str, int] = {}

    def minute(self, ts: str) -> int:
        d = ts[:10]
        off = self._days.get(d)
        if off is None:
            off = (dt.date.fromisoformat(d) - BASE_DATE).days
            self._days[d] = off
        return off * 1440 + int(ts[11:13]) * 60 + int(ts[14:16])


def idx_of(entity_id: str, tag: str) -> int:
    """'nw_renewal:l00012345' -> 12345."""
    return int(entity_id.rsplit(":" + tag, 1)[1])


class Ingestor:
    def __init__(self, store: Store):
        self.s = store
        self.tp = TimeParser()
        self.combos: dict[tuple, int] = {}
        self.lead_feat: dict[str, array] = defaultdict(lambda: array("H"))
        self.lead_cday: dict[str, array] = defaultdict(lambda: array("h"))   # campaign day per lead
        self.events_ingested = 0
        self._load()

    # ------------------------------------------------------------------ state
    def _load(self):
        for code, *vals in self.s.q("SELECT code, lang, city_tier, age_band, value_band FROM feat_combos"):
            self.combos[tuple(vals)] = code
        camp_day = {c: m // 1440 for c, m in self.s.q("SELECT campaign_id, minute FROM campaigns")}
        for wf, idx, feat, camp in self.s.q("SELECT wf, lead_idx, feat, campaign FROM leads ORDER BY wf, lead_idx"):
            self._set_feat(wf, idx, feat, camp_day.get(camp, 0))

    def _set_feat(self, wf: str, idx: int, feat: int, cday: int):
        a, d = self.lead_feat[wf], self.lead_cday[wf]
        if idx >= len(a):
            a.extend([NO_FEAT] * (idx + 1 - len(a)))
            d.extend([0] * (idx + 1 - len(d)))
        a[idx] = feat
        d[idx] = cday

    @property
    def cursor(self) -> int:
        return self.s.get_meta("cursor", -1)

    def combo_values(self) -> dict[int, dict]:
        return {c: dict(zip(FEATURE_KEYS, k)) for k, c in self.combos.items()}

    # ------------------------------------------------------------------ chunk processing
    def apply_chunk(self, n: int, lines: list[str], kind: str) -> list[dict]:
        """Parse one chunk and commit it with the cursor. Returns control events (config/approval)."""
        if n <= self.cursor:
            return []
        acc = _Acc()
        control: list[dict] = []
        new_combos: list[tuple] = []
        loads = json.loads
        minute_of = self.tp.minute
        for line in lines:
            if not line:
                continue
            ev = loads(line)
            t = ev["type"]
            if t == "call":
                self._call(ev, acc, minute_of)
            elif t == "lead":
                f = ev["features"]
                key = tuple(str(f.get(k)) for k in FEATURE_KEYS)
                code = self.combos.get(key)
                if code is None:
                    code = len(self.combos)
                    self.combos[key] = code
                    new_combos.append((code, *key))
                li = idx_of(ev["lead_id"], "l")
                cday = minute_of(ev["ts"]) // 1440
                self._set_feat(ev["workflow_id"], li, code, cday)
                acc.camp[(ev["workflow_id"], cday, code)][0] += 1
                acc.leads.append((ev["workflow_id"], li, code, idx_of(ev["person_id"], "p"), ev["campaign_id"]))
            elif t == "campaign_created":
                acc.campaigns.append((ev["campaign_id"], ev["workflow_id"], minute_of(ev["ts"]), ev["leads"],
                                      ev.get("config_version_at_creation")))
            elif t in ("config_changed", "approval_decided"):
                ev["minute"] = minute_of(ev["ts"])
                control.append(ev)
        with self.s.tx() as db:
            if new_combos:
                db.executemany("INSERT OR IGNORE INTO feat_combos VALUES (?,?,?,?,?)", new_combos)
            acc.flush(db)
            self.s.set_meta("cursor", n)
            if control:
                prev = self.s.get_meta("pending_control", [])
                self.s.set_meta("pending_control", prev + control)
        self.events_ingested += len(lines)
        return control

    def _call(self, ev: dict, acc: "_Acc", minute_of):
        wf = ev["workflow_id"]
        minute = minute_of(ev["ts"])
        day, mod = divmod(minute, 1440)
        hour = mod // 60
        hidx = minute // 60
        li = idx_of(ev["lead_id"], "l")
        a = self.lead_feat[wf]
        feat = a[li] if li < len(a) else NO_FEAT
        v = ev["variant"]
        conn = 1 if ev["connected"] else 0
        eng = 1 if ev["engaged"] else 0
        qa = ev.get("qa") or {}
        flag = 1 if qa.get("hallucination_flag") else 0
        cohort = ev.get("cohort_id") or "default"
        k = acc.day_kpi[(wf, day)]
        k[0] += 1; k[1] += conn; k[2] += eng; k[3] += flag
        d = acc.did[(wf, hidx, v["caller_id"])]
        d[0] += 1; d[1] += conn
        h = acc.hour[(wf, day, feat, hour)]
        h[0] += 1; h[1] += conn
        if conn:
            for dim in ("welcome_message", "voice"):
                c = acc.arm[(wf, day, feat, cohort, dim, v[dim])]
                c[0] += 1; c[1] += eng
            p = acc.prompt[(wf, hidx, v["prompt_version"])]
            p[0] += 1; p[1] += eng; p[2] += flag
        if eng and li < len(a):
            acc.camp[(wf, self.lead_cday[wf][li], feat)][1] += 1
        if minute >= 0:
            acc.calls.append((wf, minute, li, ev["attempt_no"], ev["config_version"], cohort,
                              v["welcome_message"], v["voice"], v["caller_id"], v["prompt_version"],
                              conn, eng, flag))


class _Acc:
    def __init__(self):
        z2 = lambda: [0, 0]
        self.arm = defaultdict(z2)
        self.did = defaultdict(z2)
        self.hour = defaultdict(z2)
        self.prompt = defaultdict(lambda: [0, 0, 0])
        self.day_kpi = defaultdict(lambda: [0, 0, 0, 0])
        self.camp = defaultdict(lambda: [0, 0])
        self.leads: list[tuple] = []
        self.calls: list[tuple] = []
        self.campaigns: list[tuple] = []

    def flush(self, db):
        db.executemany("INSERT OR IGNORE INTO leads VALUES (?,?,?,?,?)", self.leads)
        db.executemany("INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?)", self.campaigns)
        db.executemany("INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", self.calls)
        db.executemany(
            "INSERT INTO arm_counts VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(wf,day,feat,cohort,dim,arm) "
            "DO UPDATE SET conn=conn+excluded.conn, eng=eng+excluded.eng",
            [(*k, *v) for k, v in self.arm.items()])
        db.executemany(
            "INSERT INTO did_counts VALUES (?,?,?,?,?) ON CONFLICT(wf,hour_idx,did) "
            "DO UPDATE SET dials=dials+excluded.dials, conn=conn+excluded.conn",
            [(*k, *v) for k, v in self.did.items()])
        db.executemany(
            "INSERT INTO hour_counts VALUES (?,?,?,?,?,?) ON CONFLICT(wf,day,feat,hour) "
            "DO UPDATE SET dials=dials+excluded.dials, conn=conn+excluded.conn",
            [(*k, *v) for k, v in self.hour.items()])
        db.executemany(
            "INSERT INTO prompt_counts VALUES (?,?,?,?,?,?) ON CONFLICT(wf,hour_idx,prompt) "
            "DO UPDATE SET conn=conn+excluded.conn, eng=eng+excluded.eng, flag=flag+excluded.flag",
            [(*k, *v) for k, v in self.prompt.items()])
        db.executemany(
            "INSERT INTO camp_counts VALUES (?,?,?,?,?) ON CONFLICT(wf,cday,feat) "
            "DO UPDATE SET leads=leads+excluded.leads, concluded=concluded+excluded.concluded",
            [(*k, *v) for k, v in self.camp.items()])
        db.executemany(
            "INSERT INTO day_kpi VALUES (?,?,?,?,?,?) ON CONFLICT(wf,day) DO UPDATE SET "
            "dials=dials+excluded.dials, conn=conn+excluded.conn, eng=eng+excluded.eng, flag=flag+excluded.flag",
            [(*k, *v) for k, v in self.day_kpi.items()])
