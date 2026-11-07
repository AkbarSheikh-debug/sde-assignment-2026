"""Run the controller on many worlds in parallel and summarise harm, lift and incident handling.

    python scripts/fuzz_eval.py runs/fuzz/*.yaml [--jobs 6] [--tag cfgA] [--out reports/fuzz.md]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_one(args):
    i, scenario, tag = args
    name = Path(scenario).stem + (f"_{tag}" if tag else "")
    port = 8800 + i
    r = subprocess.run([str(ROOT / "scripts" / "run_scenario.sh"), scenario, str(port), name], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return name, {"error": r.stderr[-600:]}
    return name, json.loads((ROOT / "runs" / f"grade_{name}.json").read_text())


def summarise(name: str, g: dict) -> dict:
    if "error" in g:
        return {"world": name, "error": g["error"]}
    v = g["controller_changes"]["verdicts"]
    prim = []
    for wid, d in g["delta_vs_baseline"].items():
        if wid == "ALL":
            continue
        prim.append(d["conclusion_rate"] or 0)
    inc = [x for x in g["incidents"] if "mttr_minutes" in x]
    decay = g["decaying_variant_share_after_onset"]
    late_decay = [max([s for d, s in days.items() if int(d) >= max(map(int, days)) - 1] or [0])
                  for days in decay.values()] if decay else []
    return {"world": name, "harmful": v.get("harmful", 0), "beneficial": v.get("beneficial", 0),
            "neutral": v.get("neutral", 0), "conclusion_all": g["delta_vs_baseline"]["ALL"]["conclusion_rate"],
            "pickup_all": g["delta_vs_baseline"]["ALL"]["pickup_rate"],
            "engagement_all": g["delta_vs_baseline"]["ALL"]["engagement_rate"],
            "gap_captured": g["kpis"]["ALL"]["engagement_gap_captured"],
            "mttr": [x["mttr_minutes"] for x in inc], "unfixed": sum(1 for x in inc if x["mttr_minutes"] is None),
            "auto_rejected": g["governance"]["auto_apply_rejected"], "conflicts": g["governance"]["apply_conflicts"],
            "lapsed": g["governance"]["lapsed"], "decay_share_last_days": late_decay,
            "credits": g["credits_used"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--tag", default="")
    ap.add_argument("--out")
    a = ap.parse_args()
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(run_one, [(i, s, a.tag) for i, s in enumerate(a.scenarios)]))
    rows = [summarise(n, g) for n, g in res]
    pct = lambda x: "—" if x is None else f"{x * 100:+.1f}%"
    lines = ["| world | harmful | beneficial | neutral | conclusion Δ | pickup Δ | engagement Δ | gap captured | "
             "incident MTTR (min) | unfixed | 403s | conflicts | decayed-variant share (last days) | credits |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = {"harmful": 0, "beneficial": 0, "neutral": 0, "unfixed": 0, "auto_rejected": 0, "conflicts": 0}
    concl = []
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['world']} | ERROR | {r['error'][:80]!r} |")
            continue
        for k in tot:
            tot[k] += r[k]
        concl.append(r["conclusion_all"])
        gc = r["gap_captured"]
        lines.append(f"| {r['world']} | {r['harmful']} | {r['beneficial']} | {r['neutral']} | {pct(r['conclusion_all'])}"
                     f" | {pct(r['pickup_all'])} | {pct(r['engagement_all'])} | "
                     f"{'—' if gc is None else f'{gc * 100:.0f}%'} | {r['mttr']} | {r['unfixed']} | "
                     f"{r['auto_rejected']} | {r['conflicts']} | {r['decay_share_last_days']} | {r['credits']:.0f} |")
    mean = sum(concl) / len(concl) if concl else 0
    lines.append(f"| **total / mean** | **{tot['harmful']}** | {tot['beneficial']} | {tot['neutral']} | "
                 f"**{pct(mean)}** | | | | | {tot['unfixed']} | {tot['auto_rejected']} | {tot['conflicts']} | | |")
    text = "\n".join(lines)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
