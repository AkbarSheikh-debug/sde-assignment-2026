"""kill -9 drill: SIGKILL the real controller process at 5 points, restart with ./run.sh each time, finish, then
compare with an uninterrupted run on a second fresh simulator.

    python scripts/crash_test.py [scenario] [--kills 5] [--seed 1]

Kill points: 4 at seeded random sim minutes + 1 while a proposal sits between "approved/pending" and "applied".
Writes reports/crash_test.md. Exit code 1 if anything differs.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get("PYTHON", str(ROOT / ".venv" / "bin" / "python"))
WIRE = ("approval_requested", "reconciled", "duplicate_id", "applied", "auto_applied", "apply")


def start_sim(scenario: str, run_dir: str, port: int):
    p = subprocess.Popen([PY, "-m", "sim", "serve", "--scenario", scenario, "--run-dir", run_dir, "--force",
                          "--port", str(port)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx
    for _ in range(600):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return p
        except Exception:
            pass
        time.sleep(0.2)
    raise SystemExit("simulator did not start")


def start_ctl(port: int, db: str, resume: bool):
    env = dict(os.environ, CONTROLLER_DB=db, CONTROLLER_RESUME="1" if resume else "0", PYTHON=PY)
    return subprocess.Popen([str(ROOT / "run.sh"), f"http://127.0.0.1:{port}"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=open(db + ".log", "a"))


def meta(db: str, key: str, default=None):
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default
    except sqlite3.Error:
        return default


def pending_between_propose_and_apply(db: str) -> bool:
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        return c.execute("SELECT COUNT(*) FROM proposals WHERE state IN ('PENDING','APPROVED')").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def dump(db: str) -> list:
    c = sqlite3.connect(db)
    rows = [r for r in c.execute("SELECT wf, minute, kind, summary, evidence_json, proposal_id FROM decisions "
                                 "ORDER BY seq") if r[2] not in WIRE]
    rows += list(c.execute("SELECT proposal_id, wf, route, base_version, attempt_no, state, approval_id, "
                           "applied_version FROM proposals ORDER BY created_minute, proposal_id"))
    return rows


def grade(run_dir: str, base_dir: str) -> dict:
    out = subprocess.run([PY, "-m", "sim", "grade", "--run", run_dir, "--baseline", base_dir, "--json"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    g = json.loads(out)
    return {"kpis": g["kpis"], "verdicts": g["controller_changes"]["verdicts"], "governance": g["governance"],
            "incidents": g["incidents"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="scenarios/public_02_incidents.yaml")
    ap.add_argument("--kills", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    name = Path(a.scenario).stem
    rng = random.Random(a.seed)
    st = ROOT / "state"
    st.mkdir(exist_ok=True)
    base = f"runs/crash_base_{name}"
    subprocess.run([PY, "-m", "sim", "baseline", "--scenario", a.scenario, "--run-dir", base], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)

    # ---- reference: uninterrupted
    ref_db = str(st / f"crash_ref_{name}.db")
    for f in (ref_db, ref_db + "-wal", ref_db + "-shm", ref_db + ".log"):
        Path(f).unlink(missing_ok=True)
    sim = start_sim(a.scenario, f"runs/crash_ref_{name}", 8790)
    start_ctl(8790, ref_db, False).wait()
    sim.terminate()
    days = meta(ref_db, "last_decided", 0) // 1440 + 1

    # ---- crashed: SIGKILL at seeded minutes and once mid-approval
    db = str(st / f"crash_kill_{name}.db")
    for f in (db, db + "-wal", db + "-shm", db + ".log"):
        Path(f).unlink(missing_ok=True)
    targets = sorted(rng.randrange(60, days * 1440 - 60) // 15 * 15 for _ in range(a.kills - 1))
    sim = start_sim(a.scenario, f"runs/crash_kill_{name}", 8791)
    log = []
    ctl = start_ctl(8791, db, False)
    plan = [("minute", t) for t in targets[:1]] + [("mid_approval", None)] + [("minute", t) for t in targets[1:]]
    for kind, t in plan:
        while ctl.poll() is None:
            m = meta(db, "last_decided", -1) or -1
            if (kind == "minute" and m >= t) or (kind == "mid_approval" and m > 600 and
                                                 pending_between_propose_and_apply(db)):
                ctl.send_signal(signal.SIGKILL)
                ctl.wait()
                log.append({"kill": kind, "at_minute": m, "pending_approval": pending_between_propose_and_apply(db)})
                break
            time.sleep(0.02)
        ctl = start_ctl(8791, db, True)          # restart: run.sh resumes because statecheck matches the world
    ctl.wait()
    sim.terminate()

    ref_dump, kill_dump = dump(ref_db), dump(db)
    g_ref = grade(f"runs/crash_ref_{name}", base)
    g_kill = grade(f"runs/crash_kill_{name}", base)
    same_log = ref_dump == kill_dump
    same_grade = g_ref == g_kill
    first_diff = next((i for i, (x, y) in enumerate(zip(ref_dump, kill_dump)) if x != y), None)
    lines = [f"# Crash-recovery drill — {name}", "",
             f"Controller process SIGKILLed {len(log)} times, restarted with `CONTROLLER_RESUME=1 ./run.sh` after each "
             "kill, run to the end; compared with an uninterrupted run on a second fresh simulator.", "",
             "| # | kill point | sim minute | proposal in flight at kill |", "|---|---|---|---|"]
    for i, k in enumerate(log, 1):
        lines.append(f"| {i} | {k['kill']} | {k['at_minute']} (D{k['at_minute'] // 1440} "
                     f"{k['at_minute'] % 1440 // 60:02d}:{k['at_minute'] % 60:02d}) | {k['pending_approval']} |")
    gk, gr = g_kill["governance"], g_ref["governance"]
    lines += ["", f"- Decision log + proposals table identical (wire-level rows excluded): **{same_log}** "
                  f"({len(kill_dump)} rows" + ("" if same_log else f", first difference at row {first_diff}") + ")",
              f"- Grade identical (KPIs, verdicts, governance, incidents): **{same_grade}**",
              f"- Approval requests: {gk['approval_requests']} vs {gr['approval_requests']} uninterrupted; applied via "
              f"approval {gk['applied_via_approval']} vs {gr['applied_via_approval']}; auto-applied "
              f"{gk['auto_applied']} vs {gr['auto_applied']}",
              f"- Apply conflicts {gk['apply_conflicts']}, lapsed {gk['lapsed']}, auto-apply refused "
              f"{gk['auto_apply_rejected']} (all must be 0)",
              f"- Conclusion rate ALL: {g_kill['kpis']['ALL']['conclusion_rate']:.4f} vs "
              f"{g_ref['kpis']['ALL']['conclusion_rate']:.4f}"]
    text = "\n".join(lines) + "\n"
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "crash_test.md").write_text(text)
    print(text)
    ok = same_log and same_grade and gk["apply_conflicts"] == 0 and gk["auto_apply_rejected"] == 0
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
