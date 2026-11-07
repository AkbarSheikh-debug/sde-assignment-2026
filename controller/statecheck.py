"""Does an existing state DB belong to the simulator world that is running now?

Exit 0 (keep it, resume) when: same scenario name, our clock is not ahead of the simulator's, and every
approval id we recorded exists in the simulator with our proposal id. Otherwise exit 1 (run.sh wipes it).
This is what makes `./run.sh <url>` after a kill -9 a safe resume, and a new scenario a clean start.
"""
from __future__ import annotations

import argparse
import sys

from .client import SimClient
from .store import Store


def belongs(sim_url: str, db: str) -> tuple[bool, str]:
    s = Store(db)
    if not s.get_meta("bootstrapped"):
        return False, "never bootstrapped"
    c = SimClient(sim_url, timeout=30)
    st = c.state()
    if s.get_meta("scenario") != st["scenario"]:
        return False, f"scenario {s.get_meta('scenario')} != {st['scenario']}"
    if s.get_meta("last_decided", -10 ** 9) > st["sim_minute"]:
        return False, "our clock is ahead of the simulator: a new world"
    for wf in s.workflow_ids():
        sim = {a["approval_id"]: a["proposal_id"] for a in c.approvals(wf)}
        for pid, aid in s.q("SELECT proposal_id, approval_id FROM proposals WHERE wf=? AND approval_id IS NOT NULL",
                            (wf,)):
            if sim.get(aid) != pid:
                return False, f"approval {aid} unknown to this simulator"
    return True, "state matches the running simulator; resuming"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-url", required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    ok, why = belongs(a.sim_url, a.db)
    print(f"statecheck: {why}", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
