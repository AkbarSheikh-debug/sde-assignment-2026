"""Proposal state machine against the real simulator, including lost responses (fault injection).

Each test checks the simulator's own log: whatever goes wrong on the wire, a change is requested once and
applied once.
"""
import copy

from controller.client import SimClient
from controller.decide import Controller
from controller.governance import proposal_id
from controller.incidents import Credits
from controller.store import Store

from .conftest import sim_log


def setup(sim, tmp_path):
    store = Store(tmp_path / "c.db")
    ctl = Controller(store, SimClient("http://sim", transport=sim))
    ctl.bootstrap()
    ctl.ingest()
    return ctl


def advance_to(sim, minute):
    while sim.world.now < minute:
        sim.world.advance(15)


def body(ctl, wf, voice, summary="test change"):
    v = ctl.s.latest_version(wf)
    cfg = copy.deepcopy(ctl.s.config_at(wf, v))
    cfg["allocations"]["default"]["voice"] = voice
    return {"workflow_id": wf, "base_version": v, "proposed_config": cfg, "change_summary": summary,
            "diff": {"voice": voice}, "affected_leads": 10, "dry_run": {"x": 1}, "impact_score": 0.1,
            "rationale": "test"}, v


def propose(ctl, wf, voice, now, route="approval"):
    b, v = body(ctl, wf, voice)
    if route == "auto":
        b = {"base_version": v, "proposed_config": b["proposed_config"], "rationale": "test"}
    pid = ctl.gov.prepare(wf, route, v, b, [], False, now)
    b["proposal_id"] = pid
    ctl.s.x("UPDATE proposals SET payload_json=? WHERE proposal_id=?", (__import__("json").dumps(b), pid))
    return pid


def state(ctl, pid):
    return ctl.gov.row(pid)["state"]


def poll_until(ctl, sim, pid, states, limit=48):
    for _ in range(limit):
        sim.world.advance(15)
        ctl.ingest()
        ctl.handle_control(sim.world.now)
        live = {wf: ctl.s.latest_version(wf) for wf in ctl.s.workflow_ids()}
        ctl.gov.poll(sim.world.now, live, ctl.on_applied)
        if state(ctl, pid) in states:
            return state(ctl, pid)
    return state(ctl, pid)


def test_ids_are_deterministic_and_attempt_scoped():
    assert proposal_id("w", 3, 0, {"a": 1}) == proposal_id("w", 3, 0, {"a": 1})
    assert proposal_id("w", 3, 0, {"a": 1}) != proposal_id("w", 3, 1, {"a": 1})


def test_happy_path_requested_once_applied_once(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    assert ctl.gov.send(pid, sim.world.now, ctl.on_applied) == "PENDING"
    assert poll_until(ctl, sim, pid, ("APPLIED",)) == "APPLIED"
    assert len(sim_log(sim.world, "approval_requested")) == 1
    assert len(sim_log(sim.world, "approval_applied")) == 1


def test_lost_response_on_request_is_reconciled_not_resent(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    sim.faults["drop_after"] = lambda r, b: r.url.path == "/approvals" and r.method == "POST"
    assert ctl.gov.send(pid, sim.world.now) == "SUBMITTED"
    ctl.gov.reconcile(sim.world.now)
    assert state(ctl, pid) == "PENDING"
    assert len(sim_log(sim.world, "approval_requested")) == 1


def test_resend_after_crash_hits_duplicate_and_reconciles(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    ctl.gov.send(pid, sim.world.now)
    ctl.s.x("UPDATE proposals SET state='SUBMITTED', approval_id=NULL WHERE proposal_id=?", (pid,))  # "forgot"
    assert ctl.gov.send(pid, sim.world.now) == "SUBMITTED"     # 409 duplicate -> left for reconcile
    ctl.gov.reconcile(sim.world.now)
    assert state(ctl, pid) == "PENDING"
    assert len(sim_log(sim.world, "approval_requested")) == 1


def test_lost_response_on_apply_never_double_applies(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    ctl.gov.send(pid, sim.world.now)
    sim.faults["drop_after"] = lambda r, b: r.url.path.endswith("/apply")
    poll_until(ctl, sim, pid, ("APPLYING",))
    assert state(ctl, pid) == "APPLYING"
    ctl.gov.reconcile(sim.world.now, ctl.on_applied)
    assert state(ctl, pid) == "APPLIED"
    assert len(sim_log(sim.world, "approval_applied")) == 1
    assert sim_log(sim.world, "apply_conflict") == []


def test_lost_response_on_auto_apply_is_found_in_history(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now, route="auto")
    sim.faults["drop_after"] = lambda r, b: r.method == "PUT"
    assert ctl.gov.send(pid, sim.world.now) == "SUBMITTED"
    ctl.gov.reconcile(sim.world.now, ctl.on_applied)
    assert state(ctl, pid) == "APPLIED"
    assert len(sim_log(sim.world, "auto_applied")) == 1


def test_supervisor_edit_makes_approved_change_stale_without_a_conflict(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    ctl.gov.send(pid, sim.world.now)
    other = propose(ctl, "t_cancel", {"V1": 0.8, "V2": 0.2}, sim.world.now, route="auto")   # moves the base
    ctl.gov.send(other, sim.world.now, ctl.on_applied)
    ctl.refresh_config("t_cancel", sim.world.now)
    assert poll_until(ctl, sim, pid, ("STALE",)) == "STALE"
    assert sim_log(sim.world, "apply_conflict") == []          # we never sent the doomed apply


def test_not_applying_in_time_lapses(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    pid = propose(ctl, "t_cancel", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    ctl.gov.send(pid, sim.world.now)
    advance_to(sim, sim.world.now + 6 * 60)                     # decided, then 2h window passes unpolled
    ctl.ingest()
    ctl.gov.poll(sim.world.now, {"t_cancel": ctl.s.latest_version("t_cancel")}, ctl.on_applied)
    assert state(ctl, pid) == "LAPSED"


def test_auto_apply_outside_policy_is_refused_and_logged(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    pid = propose(ctl, "t_kyc", {"V1": 0.9, "V2": 0.1}, sim.world.now, route="auto")
    assert ctl.gov.send(pid, sim.world.now) == "REFUSED"
    assert ctl.s.q1("SELECT COUNT(*) FROM alerts WHERE name='AUTO_APPLY_REFUSED'")[0] == 1


def test_expired_request_is_terminal(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 18 * 60 + 30)                               # approver gone for the day
    b, v = body(ctl, "t_kyc", {"V1": 0.9, "V2": 0.1})
    b["expires_in_hours"] = 1
    pid = ctl.gov.prepare("t_kyc", "approval", v, b, [], False, sim.world.now)
    b["proposal_id"] = pid
    ctl.s.x("UPDATE proposals SET payload_json=? WHERE proposal_id=?", (__import__("json").dumps(b), pid))
    ctl.gov.send(pid, sim.world.now)
    assert poll_until(ctl, sim, pid, ("EXPIRED",), limit=12) == "EXPIRED"


def test_request_cap_counts_every_sent_request(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    advance_to(sim, 10 * 60 + 30)
    spec = ctl.s.spec("t_kyc")
    assert ctl.gov.can_request(spec, "t_kyc", sim.world.now, urgent=False)[0]
    pid = propose(ctl, "t_kyc", {"V1": 0.9, "V2": 0.1}, sim.world.now)
    ctl.gov.send(pid, sim.world.now)
    assert ctl.gov.requests_last_24h("t_kyc", sim.world.now) == 1
    ok, why = ctl.gov.can_request(spec, "t_kyc", sim.world.now, urgent=False)
    assert not ok and "routine slots used" in why                # cap 2: one kept for incidents
    assert ctl.gov.can_request(spec, "t_kyc", sim.world.now, urgent=True)[0]


def test_credits_stop_before_the_simulator_says_402(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    cr = Credits(ctl.s, ctl.c)
    for i in range(40):
        if cr.validate("t_cancel", "P1", i, "incident", reuse=False) is None:
            break
    assert sim.world.credits < 2                                  # spent down to the last call it could afford
    assert not any(code == 402 for _, _, code in sim.log)         # our own ledger stopped us first


def test_a_402_anyway_is_recorded_and_pages(sim, tmp_path):
    ctl = setup(sim, tmp_path)
    sim.world.credits = 1.0                                        # our ledger is wrong (e.g. shared budget)
    assert Credits(ctl.s, ctl.c).validate("t_cancel", "P1", 0, "incident", reuse=False) is None
    assert ctl.s.q1("SELECT COUNT(*) FROM credit_ops WHERE state='FAILED'")[0] == 1
    assert ctl.s.q1("SELECT COUNT(*) FROM alerts WHERE name='OUT_OF_CREDITS'")[0] == 1
