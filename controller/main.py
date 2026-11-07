"""Controller entry point.

    python -m controller.main --sim-url http://127.0.0.1:8700 [--db state/controller.db] [--plan-only]

Loop, per simulated tick:
    ingest new chunks -> reconcile outbox -> poll approvals/apply -> detect/plan/route per workflow
    -> mark the minute decided -> advance 15 min

"Decided" is recorded per sim minute, so a restart at the same minute re-runs the decision step (every side
effect in it is idempotent) and a restart after the advance simply continues. Wall-clock time never enters a
decision; it is only used for the throughput numbers we log.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import params as P
from .client import SimClient
from .decide import Controller
from .store import Store

log = logging.getLogger("controller")


def run(sim_url: str, db: str, plan_only: bool = False, view_port: int | None = None, max_ticks: int | None = None,
        quiet: bool = False, transport=None) -> dict:
    store = Store(db)
    client = SimClient(sim_url, transport=transport)
    ctl = Controller(store, client, plan_only=plan_only)
    ctl.bootstrap()
    if view_port:
        from .view import serve_background
        serve_background(db, view_port)
    ticks = 0
    t0 = time.time()
    events = 0
    while True:
        st = client.state()
        now = st["sim_minute"]
        store.set_meta("credits_remaining", st["credits_remaining"])
        t_ing = time.time()
        n = ctl.ingest()
        events += n
        ingest_s = time.time() - t_ing
        if st["finished"]:
            break
        if store.get_meta("last_decided", -10 ** 9) < now:
            t_dec = time.time()
            ctl.step(now)
            with store.tx():
                store.set_meta("last_decided", now)
                store.set_meta("loop_stats", {"sim_minute": now, "ingest_s": round(ingest_s, 3),
                                              "decide_s": round(time.time() - t_dec, 3), "events": n,
                                              "requests": client.requests_sent})
        if now % 1440 == 0 and not quiet:
            log.info("%s day %d: %d events ingested so far, %.1fs wall", st["ts"], now // 1440, events, time.time() - t0)
        client.advance(P.TICK_MINUTES)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
    summary = {"ticks": ticks, "events": events, "wall_s": round(time.time() - t0, 1), "finished": st["finished"]}
    log.info("done: %s", summary)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controller")
    ap.add_argument("--sim-url", required=True)
    ap.add_argument("--db", default="state/controller.db")
    ap.add_argument("--plan-only", action="store_true", help="decide and log, never change config")
    ap.add_argument("--view-port", type=int, default=None, help="serve the supervisor view (e.g. 8710)")
    ap.add_argument("--max-ticks", type=int, default=None, help="stop after N ticks (tests, crash drills)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(a.sim_url, a.db, a.plan_only, a.view_port, a.max_ticks)


if __name__ == "__main__":
    main()
