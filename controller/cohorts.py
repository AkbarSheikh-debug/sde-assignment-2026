"""The one shared cohort object.

A *segment* is a `where` clause over lead features with a stable, readable id (`age_60p`, `lang_hi`,
`age_46to60+60p`). Detection, experiments, schedule and the supervisor view all speak in segment ids.

The live config's cohorts are *compiled* from a layout: per dimension, an ordered list of override segments
(welcome_message, voice) and a list of schedule groups. Each generated cohort id names its parts,
`wm=lang_hi|voice=age_60p|sched=age_18to30`, so a config can always be parsed back into its layout.
Cohorts we did not create (business default or a supervisor) are kept untouched in front of ours.
"""
from __future__ import annotations

import itertools
import json

from .store import Store, dumps

FEAT_SHORT = {"lang": "lang", "city_tier": "tier", "age_band": "age", "value_band": "value"}
SHORT_FEAT = {v: k for k, v in FEAT_SHORT.items()}
LAYOUT_DIMS = ("welcome_message", "voice", "sched")
PART_TAG = {"welcome_message": "wm", "voice": "voice", "sched": "sched"}
TAG_PART = {v: k for k, v in PART_TAG.items()}


def slug(v: str) -> str:
    return v.replace("+", "p").replace("-", "to").replace(" ", "")


def seg_id(feature: str, values) -> str:
    return f"{FEAT_SHORT.get(feature, feature)}_{'+'.join(slug(v) for v in values)}"


def merge_where(wheres: list[dict]) -> dict | None:
    """AND of clauses; same feature -> intersect values. None if the intersection is empty."""
    out: dict[str, list] = {}
    for w in wheres:
        for f, vals in w.items():
            if f in out:
                keep = [v for v in out[f] if v in vals]
                if not keep:
                    return None
                out[f] = keep
            else:
                out[f] = list(vals)
    return {f: out[f] for f in sorted(out)}


def matches(where: dict, feats: dict) -> bool:
    return all(feats.get(f) in vals for f, vals in where.items())


class Registry:
    def __init__(self, store: Store):
        self.s = store

    def register(self, feature: str, values) -> str:
        values = list(values)
        sid = seg_id(feature, values)
        self.s.x("INSERT OR IGNORE INTO segments(seg_id, where_json, label) VALUES (?,?,?)",
                 (sid, dumps({feature: values}), f"{feature} in {values}"))
        return sid

    def where(self, sid: str) -> dict:
        r = self.s.q1("SELECT where_json FROM segments WHERE seg_id=?", (sid,))
        if not r:
            raise KeyError(sid)
        return json.loads(r[0])

    def known(self, sid: str) -> bool:
        return self.s.q1("SELECT 1 FROM segments WHERE seg_id=?", (sid,)) is not None

    def single_feature_segments(self, combos: dict[int, dict]) -> list[tuple[str, str, str]]:
        """Every (seg_id, feature, value) observed in lead features, in a stable order."""
        out = set()
        for feats in combos.values():
            for f, v in feats.items():
                if v not in (None, "None"):
                    out.add((f, v))
        res = []
        for f, v in sorted(out):
            res.append((self.register(f, [v]), f, v))
        return res


def feats_in(where: dict, combos: dict[int, dict]) -> set[int]:
    return {c for c, feats in combos.items() if matches(where, feats)}


# --------------------------------------------------------------------------- layout <-> config
class Layout:
    """Which segments override which dimension. Order inside a list = first-match priority."""

    def __init__(self, overrides: dict[str, list[str]] | None = None, foreign: list[dict] | None = None):
        self.overrides = {d: list((overrides or {}).get(d, [])) for d in LAYOUT_DIMS}
        self.foreign = foreign or []

    def n_cohorts(self, reg: Registry) -> int:
        return len(self.foreign) + len(self._combos(reg))

    def _combos(self, reg: Registry):
        lists = [[None] + self.overrides[d] for d in LAYOUT_DIMS]
        combos = []
        for parts in itertools.product(*lists):
            if all(p is None for p in parts):
                continue
            where = merge_where([reg.where(p) for p in parts if p is not None])
            if where is None:
                continue
            combos.append((parts, where))
        # more specific first (more parts set), then by override order -> correct first-match semantics
        def rank(item):
            parts = item[0]
            idx = [(self.overrides[d].index(p) if p is not None else len(self.overrides[d]))
                   for d, p in zip(LAYOUT_DIMS, parts)]
            return (-sum(p is not None for p in parts), idx)
        combos.sort(key=rank)
        return combos

    @staticmethod
    def cohort_id(parts) -> str:
        return "|".join(f"{PART_TAG[d]}={p}" for d, p in zip(LAYOUT_DIMS, parts) if p is not None)

    @staticmethod
    def parse_id(cid: str) -> dict | None:
        out = {}
        for piece in cid.split("|"):
            if "=" not in piece:
                return None
            tag, sid = piece.split("=", 1)
            if tag not in TAG_PART:
                return None
            out[TAG_PART[tag]] = sid
        return out

    @classmethod
    def from_config(cls, cfg: dict, reg: Registry) -> "Layout":
        ov: dict[str, list[str]] = {d: [] for d in LAYOUT_DIMS}
        foreign = []
        for c in cfg.get("cohorts", []):
            parts = cls.parse_id(c["id"])
            if parts is None or not all(reg.known(s) for s in parts.values()):
                foreign.append(c)
                continue
            for d, sid in parts.items():
                if sid not in ov[d]:
                    ov[d].append(sid)
        return cls(ov, foreign)

    def compile(self, reg: Registry, dists: dict, sched_tables: dict, base_cfg: dict) -> dict:
        """dists[(scope, dim)] -> {arm: share}; scope 'default' or seg id. sched_tables[scope] -> {hour: w}."""
        cfg = {"cohorts": [dict(c) for c in self.foreign],
               "allocations": {}, "prompt_version": base_cfg["prompt_version"],
               "schedule": {"hour_weights": {}, "lead_preferred_hour":
                            dict(base_cfg.get("schedule", {}).get("lead_preferred_hour", {}))}}
        for c in self.foreign:
            if c["id"] in base_cfg["allocations"]:
                cfg["allocations"][c["id"]] = base_cfg["allocations"][c["id"]]
            hw = base_cfg.get("schedule", {}).get("hour_weights", {})
            if c["id"] in hw:
                cfg["schedule"]["hour_weights"][c["id"]] = hw[c["id"]]
        default = dict(base_cfg["allocations"]["default"])
        for dim in ("welcome_message", "voice"):
            if ("default", dim) in dists:
                default[dim] = dists[("default", dim)]
        if ("default", "caller_id") in dists:
            default["caller_id"] = dists[("default", "caller_id")]
        cfg["allocations"]["default"] = default
        if "default" in sched_tables:
            cfg["schedule"]["hour_weights"]["default"] = sched_tables["default"]
        for parts, where in self._combos(reg):
            cid = self.cohort_id(parts)
            cfg["cohorts"].append({"id": cid, "where": where})
            alloc = {}
            for d, p in zip(LAYOUT_DIMS, parts):
                if p is None:
                    continue
                if d == "sched":
                    if p in sched_tables:
                        cfg["schedule"]["hour_weights"][cid] = sched_tables[p]
                elif (p, d) in dists:
                    alloc[d] = dists[(p, d)]
            if alloc:
                cfg["allocations"][cid] = alloc
        return cfg


def scope_dist(cfg: dict, scope: str, dim: str) -> dict:
    """Current distribution of `dim` for leads in override scope `scope` (or 'default')."""
    if scope != "default":
        for c in cfg.get("cohorts", []):
            parts = Layout.parse_id(c["id"])
            if parts and parts.get(dim) == scope:
                d = (cfg["allocations"].get(c["id"]) or {}).get(dim)
                if d:
                    return dict(d)
    return dict(cfg["allocations"]["default"][dim])


def scope_sched(cfg: dict, scope: str) -> dict | None:
    hw = cfg.get("schedule", {}).get("hour_weights", {})
    if scope == "default":
        return hw.get("default")
    for c in cfg.get("cohorts", []):
        parts = Layout.parse_id(c["id"])
        if parts and parts.get("sched") == scope and c["id"] in hw:
            return hw[c["id"]]
    return None


def scope_of(feats: dict, overrides: list[str], reg: Registry) -> str:
    for sid in overrides:
        if matches(reg.where(sid), feats):
            return sid
    return "default"
