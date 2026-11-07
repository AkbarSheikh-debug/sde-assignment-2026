"""Test helpers. Tests (not the controller) may import `sim`: they drive a real in-process simulator through an
httpx transport so governance and crash behaviour are tested against the actual contract."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny.yaml"


class Crash(BaseException):
    """Stands in for kill -9: not an Exception, so nothing in the controller can catch it."""


class SimTransport(httpx.BaseTransport):
    """Forwards requests to the FastAPI app in-process. `faults` lets a test break the wire:
    {"drop_after": predicate}  -> server acts, client sees a timeout
    {"crash_after": predicate} -> server acts, then the controller process "dies"
    """

    def __init__(self, scenario: Path, run_dir: Path):
        from sim import server
        server.init_world(str(scenario), str(run_dir))
        self.world = server._world
        self.tc = TestClient(server.app)
        self.faults: dict = {}
        self.log: list[tuple[str, str, int]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        r = self.tc.request(request.method, request.url.path, params=dict(request.url.params),
                            json=body if request.method in ("POST", "PUT") else None)
        self.log.append((request.method, request.url.path, r.status_code))
        for kind in ("drop_after", "crash_after"):
            pred = self.faults.get(kind)
            if pred and pred(request, body):
                self.faults.pop(kind)
                if kind == "drop_after":
                    raise httpx.ReadTimeout("injected: response lost", request=request)
                raise Crash(f"injected crash after {request.method} {request.url.path}")
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)


@pytest.fixture
def sim(tmp_path):
    return SimTransport(TINY, tmp_path / "run")


def sim_log(world, kind: str) -> list[dict]:
    path = Path(world.run_dir) / "sim_log.jsonl"
    return [r for r in map(json.loads, path.read_text().splitlines()) if r["kind"] == kind]
