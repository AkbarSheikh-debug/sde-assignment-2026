"""Hard rule 1: the controller learns only through the HTTP API."""
import re
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
FORBIDDEN = [
    (re.compile(r"^\s*(import|from)\s+sim(\s|\.|$)", re.M), "imports the simulator"),
    (re.compile(r"['\"/]runs/"), "reads a simulator run directory"),
    (re.compile(r"scenarios/|\.ya?ml['\"]"), "opens a scenario file"),
    (re.compile(r"_truth|aggregates\.json|sim_log"), "reads hidden truth or simulator logs"),
    (re.compile(r"base_pickup|did_base_health|base_engage|halluc['\"]\s*:|engage_mult"), "uses hidden fields"),
]


def test_controller_never_touches_the_simulator_internals():
    bad = []
    for f in sorted(CONTROLLER.rglob("*.py")):
        text = f.read_text()
        for rx, why in FORBIDDEN:
            for m in rx.finditer(text):
                line = text[: m.start()].count("\n") + 1
                bad.append(f"{f.name}:{line} {why}: {m.group(0)!r}")
    assert not bad, "\n".join(bad)
