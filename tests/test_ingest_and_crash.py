"""Ingest idempotency, end-to-end behaviour on the tiny world, and crash recovery.

The crash test kills the controller (an uncatchable exception at the wire) right after the simulator acted on
a request, restarts it on the same DB, and checks the run ends exactly like an uninterrupted one.
"""
import sqlite3

from controller.client import SimClient
from controller.ingest import Ingestor
from controller.main import run
from controller.store import Store

from .conftest import TINY, Crash, SimTransport, sim_log


def dump(db):
    c = sqlite3.connect(db)
    rows = list(c.execute("SELECT wf, minute, kind, summary, evidence_json, proposal_id FROM decisions ORDER BY seq"))
    rows += list(c.execute("SELECT proposal_id, state, approval_id, applied_version FROM proposals "
                           "ORDER BY created_minute, proposal_id"))
    return rows


def counters(db):
    c = sqlite3.connect(db)
    return {t: list(c.execute(f"SELECT * FROM {t} ORDER BY 1,2,3,4")) for t in
            ("arm_counts", "did_counts", "day_kpi", "prompt_counts")}


def test_reapplying_a_chunk_does_not_double_count(sim, tmp_path):
    store = Store(tmp_path / "a.db")
    ing = Ingestor(store)
    c = SimClient("http://sim", transport=sim)
    ch = c.chunks("history", -1)[0]
    lines = c.chunk_lines(ch["chunk"])
    ing.apply_chunk(ch["chunk"], lines, "history")
    before = counters(tmp_path / "a.db")
    ing.apply_chunk(ch["chunk"], lines, "history")
    Ingestor(store).apply_chunk(ch["chunk"], lines, "history")     # and after a "restart"
    assert counters(tmp_path / "a.db") == before


def test_tiny_world_end_to_end(sim, tmp_path):
    db = tmp_path / "c.db"
    out = run("http://sim", str(db), transport=sim, quiet=True)
    assert out["finished"]
    assert sim_log(sim.world, "auto_apply_rejected") == []
    assert sim_log(sim.world, "apply_conflict") == []
    s = Store(db)
    # flagged D2 on t_cancel is drained, the bad supervisor prompt is reverted with validation evidence
    assert s.q1("SELECT status FROM did_state WHERE wf='t_cancel' AND did='D2'")[0] == "quarantined"
    assert s.q1("SELECT COUNT(*) FROM incidents WHERE wf='t_cancel' AND bad_prompt='P2'")[0] == 1
    timeline = [v["config"]["prompt_version"] for v in sim.world.wfs["t_cancel"].versions]
    assert timeline[-1] != "P2" and "P2" in timeline
    # every approval request carried the fields the approver checks
    for r in sim_log(sim.world, "approval_requested"):
        req = r["request"]
        assert req["change_summary"] and req["diff"] and req["dry_run"] and req["rationale"]
        assert isinstance(req["affected_leads"], int) and 0 <= req["impact_score"] <= 1


def _crash_run(tmp_path, tag, predicate):
    world = SimTransport(TINY, tmp_path / f"run_{tag}")
    db = str(tmp_path / f"{tag}.db")
    world.faults["crash_after"] = predicate
    try:
        run("http://sim", db, transport=world, quiet=True)
        crashed = False
    except Crash:
        crashed = True
    out = run("http://sim", db, transport=world, quiet=True)        # restart on the same state
    return world, db, crashed, out


WIRE_KINDS = ("approval_requested", "reconciled", "duplicate_id", "applied", "auto_applied", "apply")


def _decisions_without_restart_noise(db):
    """Planning decisions + the full proposals table. Rows that only record a wire event differ by design after
    a kill (the lost response is recorded as 'reconciled' on restart instead), so they are left out; the
    proposals table still proves the same requests ended in the same states with the same ids."""
    return [r for r in dump(db) if not (len(r) == 6 and r[2] in WIRE_KINDS)]


def test_kill_after_request_restart_matches_uninterrupted_run(tmp_path):
    ref = SimTransport(TINY, tmp_path / "run_ref")
    run("http://sim", str(tmp_path / "ref.db"), transport=ref, quiet=True)
    ref_dump = _decisions_without_restart_noise(tmp_path / "ref.db")
    ref_grade = [v["config"] for v in ref.world.wfs["t_kyc"].versions]

    advances = {"n": 0}

    def nth_advance(r, b):
        if r.url.path != "/sim/advance":
            return False
        advances["n"] += 1
        return advances["n"] == 300

    points = {
        "after_approval_post": lambda r, b: r.method == "POST" and r.url.path == "/approvals",
        "after_apply": lambda r, b: r.url.path.endswith("/apply"),
        "after_auto_apply": lambda r, b: r.method == "PUT",
        "after_advance": nth_advance,
    }
    for tag, pred in points.items():
        world, db, crashed, out = _crash_run(tmp_path, tag, pred)
        assert crashed and out["finished"], tag
        assert len(sim_log(world.world, "approval_applied")) == len(sim_log(ref.world, "approval_applied")), tag
        assert len(sim_log(world.world, "approval_requested")) == len(sim_log(ref.world, "approval_requested")), tag
        assert sim_log(world.world, "apply_conflict") == [], tag
        assert [v["config"] for v in world.world.wfs["t_kyc"].versions] == ref_grade, tag
        assert _decisions_without_restart_noise(db) == ref_dump, tag


def test_kill_after_validation_is_safe_but_not_byte_identical(tmp_path):
    """Known gap: the API has no way to look up a validation run, so a kill between POST /validation/runs and
    recording its result loses that run (its credits) and the retry draws a new one. We still finish, never
    double-apply, and fix the incident; only the validation id (and what follows from its noise) may differ."""
    world, db, crashed, out = _crash_run(tmp_path, "val", lambda r, b: r.url.path == "/validation/runs")
    assert crashed and out["finished"]
    assert sim_log(world.world, "apply_conflict") == [] and sim_log(world.world, "auto_apply_rejected") == []
    applied = [r["approval_id"] for r in sim_log(world.world, "approval_applied")]
    assert len(applied) == len(set(applied))
    assert world.world.wfs["t_cancel"].versions[-1]["config"]["prompt_version"] != "P2"
