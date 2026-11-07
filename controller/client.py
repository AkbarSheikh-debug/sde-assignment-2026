"""HTTP client for the simulator. The only way the controller learns anything about the world.

Retry rules (BUILD_PLAN section 6):
- GETs are safe: retried with backoff on transport errors and 5xx.
- POST /sim/advance is never retried blind: on a transport error we read /sim/state and return that.
- Mutating POST/PUT are never retried here. Callers own idempotency (deterministic proposal ids + reconcile).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

GET_BACKOFF = (0.2, 0.5, 1.0, 2.0)   # seconds; 4 tries in total


class SimUnavailable(RuntimeError):
    pass


@dataclass
class Resp:
    status: int
    body: object

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> str | None:
        return self.body.get("error") if isinstance(self.body, dict) else None


class SimClient:
    def __init__(self, base_url: str, timeout: float = 120.0, transport=None, sleep=time.sleep):
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        self._sleep = sleep
        self.requests_sent = 0

    # ------------------------------------------------------------------ primitives
    def _get(self, path: str, params=None) -> httpx.Response:
        last = None
        for i, pause in enumerate((0.0,) + GET_BACKOFF):
            if pause:
                self._sleep(pause)
            try:
                self.requests_sent += 1
                r = self.http.get(path, params=params)
                if r.status_code < 500:
                    return r
                last = SimUnavailable(f"GET {path}: {r.status_code}")
            except httpx.TransportError as e:
                last = e
        raise SimUnavailable(f"GET {path} failed after retries: {last}")

    def get_json(self, path: str, params=None):
        r = self._get(path, params)
        if r.status_code >= 400:
            return Resp(r.status_code, _json(r))
        return r.json()

    def get_text(self, path: str) -> str:
        r = self._get(path)
        r.raise_for_status()
        return r.text

    def send(self, method: str, path: str, body: dict) -> Resp:
        """One attempt, no retry. Raises httpx.TransportError if the outcome is unknown."""
        self.requests_sent += 1
        r = self.http.request(method, path, json=body)
        return Resp(r.status_code, _json(r))

    # ------------------------------------------------------------------ endpoints
    def state(self) -> dict:
        return self.get_json("/sim/state")

    def advance(self, minutes: int) -> dict:
        try:
            r = self.send("POST", "/sim/advance", {"minutes": minutes})
        except httpx.TransportError:
            st = self.state()          # outcome unknown: ask where time actually is, never re-advance
            return {"sim_minute": st["sim_minute"], "finished": st["finished"], "uncertain": True}
        if r.status == 409 and r.code == "finished":
            st = self.state()
            return {"sim_minute": st["sim_minute"], "finished": True}
        if not r.ok:
            raise SimUnavailable(f"advance failed: {r.status} {r.body}")
        return r.body

    def workflows(self) -> list[dict]:
        return self.get_json("/workflows")

    def config(self, wid: str) -> dict:
        return self.get_json(f"/workflows/{wid}/config")

    def config_history(self, wid: str) -> list[dict]:
        return self.get_json(f"/workflows/{wid}/config/history")

    def chunks(self, kind: str, after: int) -> list[dict]:
        return self.get_json("/events/chunks", {"kind": kind, "after": after})

    def chunk_lines(self, n: int) -> list[str]:
        return self.get_text(f"/events/chunks/{n}").splitlines()

    def approval(self, aid: str):
        return self.get_json(f"/approvals/{aid}")

    def approvals(self, wid: str) -> list[dict]:
        return self.get_json("/approvals", {"workflow_id": wid})


def _json(r: httpx.Response):
    try:
        return r.json()
    except (json.JSONDecodeError, ValueError):
        return {"error": "non_json", "detail": r.text[:200]}
