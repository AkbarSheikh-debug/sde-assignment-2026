"""Generate fuzz worlds: same schema as the public scenarios, new truth. Used to tune and then freeze params.

    python scripts/fuzz_scenarios.py --out runs/fuzz --n 12

Named worlds (BUILD_PLAN G6) + random ones. Never read by the controller: these are inputs to the *simulator*.
"""
from __future__ import annotations

import argparse
import copy
import random
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[1] / "scenarios" / "public_02_incidents.yaml"
FEATURE_VALUES = {"lang": ["hi", "en", "ta", "kn"], "age_band": ["18-30", "31-45", "46-60", "60+"],
                  "city_tier": ["1", "2", "3"], "value_band": ["low", "mid", "high"]}
MULTS = [0.86, 0.9, 0.93, 1.06, 1.08, 1.12, 1.15, 1.2]


def random_effects(rng: random.Random, sc: dict, null=False, reverse=False) -> list[dict]:
    if null:
        return []
    out = []
    for t in sc["tenants"]:
        for wf in t["workflows"]:
            for _ in range(rng.randint(2, 4)):
                dim = rng.choice(["welcome_message", "voice"])
                value = rng.choice(wf["options"][dim][1:])
                m = rng.choice(MULTS)
                if reverse:
                    m = round(1 / m, 3)
                e = {"workflow": wf["id"], "dim": dim, "value": value, "mult": m}
                if rng.random() < 0.55:
                    f = rng.choice(list(FEATURE_VALUES))
                    langs = list(wf["language_mix"])
                    vals = langs if f == "lang" else FEATURE_VALUES[f]
                    e["where"] = {f: [rng.choice(vals)]}
                if m > 1 and rng.random() < 0.25:
                    e["decay"] = {"after_exposure_days": rng.choice([2, 3]), "to_mult": rng.choice([0.9, 0.93]),
                                  "over_days": 2}
                out.append(e)
    return out


def make(name: str, seed: int, **kw) -> dict:
    rng = random.Random(seed)
    sc = yaml.safe_load(BASE.read_text())
    sc["name"] = f"fuzz_{name}"
    sc["seed"] = seed
    sc["days"] = 8
    sc["effects"] = random_effects(rng, sc, kw.get("null", False), kw.get("reverse", False))
    wfs = {wf["id"]: wf for t in sc["tenants"] for wf in t["workflows"]}
    inc = []
    if kw.get("incidents", True):
        if rng.random() < 0.7 or kw.get("bad_prompt_at"):
            wid = kw.get("bad_prompt_wf") or rng.choice(["ks_cancel", "nw_renewal"])
            bad = "P2" if "P2" in wfs[wid]["options"]["prompt_version"] else "P4"
            if bad == "P4":
                sc["prompts"]["P4"] = dict(sc["prompts"]["P4"], halluc=0.05)
            at = kw.get("bad_prompt_at") or f"D{rng.randint(1, 5)} {rng.choice([10, 11, 14, 16])}:30"
            inc.append({"type": "supervisor_edit", "workflow": wid, "at": at, "patch": {"prompt_version": bad}})
        if rng.random() < 0.6 or kw.get("flag_did"):
            wid = kw.get("flag_did") or rng.choice(list(wfs))
            dids = wfs[wid]["default_config"]["allocations"]["default"]["caller_id"]
            did = rng.choice(list(dids))
            inc.append({"type": "did_flagged", "workflow": wid, "did": did, "at": f"D{rng.randint(1, 4)} 10:00",
                        "base_health": rng.choice([0.3, 0.4, 0.5])})
        if rng.random() < 0.5:
            wid = rng.choice(list(wfs))
            v = rng.choice(wfs[wid]["options"]["voice"])
            inc.append({"type": "supervisor_edit", "workflow": wid, "at": f"D{rng.randint(2, 6)} 15:00",
                        "patch": {"allocations": {"default": {"voice": {v: 1}}}}})
    sc["incidents"] = inc
    for wid, wf in wfs.items():
        g = wf["governance"]
        if kw.get("max_requests") is not None:
            g["max_requests_per_24h"] = kw["max_requests"]
        if kw.get("auto_off"):
            g["auto_apply"] = {"enabled": False, "allowed_dims": [], "max_shift": 0}
        if rng.random() < 0.3:
            wf["did_base_health"] = {rng.choice(wf["options"]["caller_id"][2:] or ["D1"]): 0.55}
    return sc


NAMED = {
    "null": dict(null=True),
    "reversed": dict(reverse=True),
    "prompt_2000": dict(bad_prompt_at="D3 20:00", bad_prompt_wf="ks_cancel"),
    "kyc_flagged": dict(flag_did="nw_kyc"),
    "nw_bad_prompt": dict(bad_prompt_at="D2 11:30", bad_prompt_wf="nw_renewal"),
    "max_req_1": dict(max_requests=1),
    "auto_off": dict(auto_off=True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/fuzz")
    ap.add_argument("--n", type=int, default=5, help="random worlds in addition to the named ones")
    ap.add_argument("--seed", type=int, default=9000)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, (name, kw) in enumerate(NAMED.items()):
        (out / f"fuzz_{name}.yaml").write_text(yaml.safe_dump(make(name, a.seed + i, **kw), sort_keys=False))
    for i in range(a.n):
        (out / f"fuzz_rand{i}.yaml").write_text(yaml.safe_dump(make(f"rand{i}", a.seed + 100 + i), sort_keys=False))
    print(f"wrote {len(NAMED) + a.n} worlds to {out}")


if __name__ == "__main__":
    main()
