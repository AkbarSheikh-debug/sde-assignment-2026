"""SQLite store: the controller's memory and its audit trail.

One file, WAL. Counters, not raw events (chunks can be re-fetched by number). Every counter update commits in
the same transaction as the ingest cursor that produced it, so a crash can never double-count.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workflows (wf TEXT PRIMARY KEY, spec_json TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS feat_combos (code INTEGER PRIMARY KEY, lang TEXT, city_tier TEXT, age_band TEXT,
    value_band TEXT, UNIQUE(lang, city_tier, age_band, value_band));

CREATE TABLE IF NOT EXISTS leads (wf TEXT, lead_idx INTEGER, feat INTEGER, person_idx INTEGER, campaign TEXT,
    PRIMARY KEY (wf, lead_idx)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS campaigns (campaign_id TEXT PRIMARY KEY, wf TEXT, minute INTEGER, leads INTEGER,
    config_version INTEGER);

-- one row per dial from D0 on: answers "what did this customer hear, and under which config"
-- clustered on (wf, lead, attempt): one B-tree, no secondary index; "explain --lead" is a range read
CREATE TABLE IF NOT EXISTS calls (wf TEXT, minute INTEGER, lead_idx INTEGER, attempt INTEGER, cfg_ver INTEGER,
    cohort TEXT, wm TEXT, voice TEXT, did TEXT, prompt TEXT, connected INTEGER, engaged INTEGER, flag INTEGER,
    PRIMARY KEY (wf, lead_idx, attempt)) WITHOUT ROWID;

-- engagement by arm: connected calls only (welcome/voice act after pickup)
CREATE TABLE IF NOT EXISTS arm_counts (wf TEXT, day INTEGER, feat INTEGER, cohort TEXT, dim TEXT, arm TEXT,
    conn INTEGER, eng INTEGER, PRIMARY KEY (wf, day, feat, cohort, dim, arm)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS did_counts (wf TEXT, hour_idx INTEGER, did TEXT, dials INTEGER, conn INTEGER,
    PRIMARY KEY (wf, hour_idx, did)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hour_counts (wf TEXT, day INTEGER, feat INTEGER, hour INTEGER, dials INTEGER,
    conn INTEGER, PRIMARY KEY (wf, day, feat, hour)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS prompt_counts (wf TEXT, hour_idx INTEGER, prompt TEXT, conn INTEGER, eng INTEGER,
    flag INTEGER, PRIMARY KEY (wf, hour_idx, prompt)) WITHOUT ROWID;
-- leads uploaded / concluded per campaign day and feature combo: active-lead counts without scanning calls
CREATE TABLE IF NOT EXISTS camp_counts (wf TEXT, cday INTEGER, feat INTEGER, leads INTEGER, concluded INTEGER,
    PRIMARY KEY (wf, cday, feat)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS day_kpi (wf TEXT, day INTEGER, dials INTEGER, conn INTEGER, eng INTEGER,
    flag INTEGER, PRIMARY KEY (wf, day)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS config_versions (wf TEXT, version INTEGER, actor TEXT, dims TEXT, proposal_id TEXT,
    approval_id TEXT, applied_minute INTEGER, config_json TEXT, seen_minute INTEGER, PRIMARY KEY (wf, version));

CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, wf TEXT, route TEXT, base_version INTEGER,
    attempt_no INTEGER, content_key TEXT, intents_json TEXT, payload_json TEXT, state TEXT, urgent INTEGER,
    approval_id TEXT, created_minute INTEGER, submitted_minute INTEGER, decided_minute INTEGER,
    apply_by_minute INTEGER, applied_version INTEGER, updated_minute INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS proposals_wf ON proposals (wf, state);

CREATE TABLE IF NOT EXISTS decisions (seq INTEGER PRIMARY KEY AUTOINCREMENT, wf TEXT, minute INTEGER,
    kind TEXT, summary TEXT, evidence_json TEXT, proposal_id TEXT);
CREATE INDEX IF NOT EXISTS decisions_wf ON decisions (wf, minute);

CREATE TABLE IF NOT EXISTS credit_ops (op_id TEXT PRIMARY KEY, wf TEXT, minute INTEGER, kind TEXT, reason TEXT,
    cost REAL, state TEXT, result_json TEXT);

CREATE TABLE IF NOT EXISTS did_state (wf TEXT, did TEXT, status TEXT, since_minute INTEGER, reason TEXT,
    PRIMARY KEY (wf, did));

CREATE TABLE IF NOT EXISTS arms (wf TEXT, scope TEXT, dim TEXT, arm TEXT, status TEXT, since_minute INTEGER,
    reason TEXT, PRIMARY KEY (wf, scope, dim, arm));

CREATE TABLE IF NOT EXISTS envelope (wf TEXT, scope TEXT, dim TEXT, arm TEXT, max_share REAL, proposal_id TEXT,
    PRIMARY KEY (wf, scope, dim, arm));

CREATE TABLE IF NOT EXISTS incidents (incident_id TEXT PRIMARY KEY, wf TEXT, opened_minute INTEGER,
    bad_prompt TEXT, trigger TEXT, state TEXT, fix_prompt TEXT, credits REAL, closed_minute INTEGER,
    detail_json TEXT);

CREATE TABLE IF NOT EXISTS parked (wf TEXT, intent_key TEXT, until_minute INTEGER, rejections INTEGER,
    PRIMARY KEY (wf, intent_key));

CREATE TABLE IF NOT EXISTS post_apply (proposal_id TEXT, window TEXT, wf TEXT, minute INTEGER, kpi_json TEXT,
    PRIMARY KEY (proposal_id, window));

-- the one shared cohort object (Product memo): analysis segments, config cohorts and the view all use these ids
CREATE TABLE IF NOT EXISTS segments (seg_id TEXT PRIMARY KEY, where_json TEXT NOT NULL, label TEXT);

CREATE TABLE IF NOT EXISTS alerts (seq INTEGER PRIMARY KEY AUTOINCREMENT, wf TEXT, minute INTEGER, name TEXT,
    pages INTEGER, message TEXT);
"""


def dumps(o) -> str:
    """Canonical JSON: the same object always serialises to the same bytes (ids and diffs depend on it)."""
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=_default)


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self._in_tx = False

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def tx(self):
        if self._in_tx:            # nested: join the outer transaction
            yield self.db
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False

    def q(self, sql: str, args=()) -> list[tuple]:
        return self.db.execute(sql, args).fetchall()

    def q1(self, sql: str, args=()):
        r = self.db.execute(sql, args).fetchone()
        return r

    def x(self, sql: str, args=()):
        return self.db.execute(sql, args)

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default=None):
        r = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r[0]) if r else default

    def set_meta(self, key: str, value):
        self.x("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, dumps(value)))

    # ------------------------------------------------------------------ audit
    def decide(self, wf: str | None, minute: int, kind: str, summary: str, evidence=None, proposal_id=None):
        """Write a decision row. Called before acting on it (hard rule 6)."""
        self.x("INSERT INTO decisions(wf, minute, kind, summary, evidence_json, proposal_id) VALUES(?,?,?,?,?,?)",
               (wf, minute, kind, summary, dumps(evidence or {}), proposal_id))

    def alert(self, wf: str | None, minute: int, name: str, pages: bool, message: str):
        self.x("INSERT INTO alerts(wf, minute, name, pages, message) VALUES(?,?,?,?,?)",
               (wf, minute, name, int(pages), message))

    # ------------------------------------------------------------------ config snapshots
    def config_at(self, wf: str, version: int) -> dict | None:
        r = self.q1("SELECT config_json FROM config_versions WHERE wf=? AND version=?", (wf, version))
        return json.loads(r[0]) if r and r[0] else None

    def latest_version(self, wf: str) -> int | None:
        r = self.q1("SELECT MAX(version) FROM config_versions WHERE wf=?", (wf,))
        return r[0] if r else None

    def spec(self, wf: str) -> dict:
        return json.loads(self.q1("SELECT spec_json FROM workflows WHERE wf=?", (wf,))[0])

    def workflow_ids(self) -> list[str]:
        return [r[0] for r in self.q("SELECT wf FROM workflows ORDER BY wf")]
