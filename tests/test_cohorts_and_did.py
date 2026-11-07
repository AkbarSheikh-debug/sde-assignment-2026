"""Cohort compile semantics (first match = intended per-dim allocation) and caller-ID target rules."""
import itertools

from controller.cohorts import Layout, Registry, matches
from controller.config_build import dist
from controller.plan import did_target
from controller.store import Store

FEATS = {"lang": ["hi", "en", "ta"], "city_tier": ["1", "2", "3"], "age_band": ["18-30", "31-45", "46-60", "60+"],
         "value_band": ["low", "mid", "high"]}


def _reg():
    return Registry(Store(":memory:"))


def first_cohort(cfg, feats):
    return next((c["id"] for c in cfg["cohorts"] if matches(c["where"], feats)), "default")


def test_compiled_cohorts_give_each_lead_its_intended_allocation():
    reg = _reg()
    s_age = reg.register("age_band", ["60+"])
    s_lang = reg.register("lang", ["hi"])
    s_young = reg.register("age_band", ["18-30"])
    s_sched = reg.register("age_band", ["46-60", "60+"])
    lay = Layout({"welcome_message": [s_lang], "voice": [s_age, s_young], "sched": [s_sched]})
    dists = {("default", "welcome_message"): {"W1": 1.0}, (s_lang, "welcome_message"): {"W2": 1.0},
             ("default", "voice"): {"V1": 1.0}, (s_age, "voice"): {"V3": 1.0}, (s_young, "voice"): {"V2": 1.0},
             ("default", "caller_id"): {"D1": 0.5, "D2": 0.5}}
    tables = {"default": {"9": 1.0}, s_sched: {"9": 2.0}}
    base = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1},
                                                       "caller_id": {"D1": 1}}}, "prompt_version": "P1",
            "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
    cfg = lay.compile(reg, dists, tables, base)
    assert len(cfg["cohorts"]) == lay.n_cohorts(reg)
    for combo in itertools.product(*FEATS.values()):
        f = dict(zip(FEATS, combo))
        c = first_cohort(cfg, f)
        want_w = "W2" if f["lang"] == "hi" else "W1"
        want_v = "V3" if f["age_band"] == "60+" else "V2" if f["age_band"] == "18-30" else "V1"
        assert dist(cfg, c, "welcome_message") == {want_w: 1.0}, (f, c)
        assert dist(cfg, c, "voice") == {want_v: 1.0}, (f, c)
        hw = cfg["schedule"]["hour_weights"]
        table = hw.get(c, hw.get("default"))
        assert table == ({"9": 2.0} if f["age_band"] in ("46-60", "60+") else {"9": 1.0}), (f, c)


def test_layout_round_trips_through_config_and_keeps_foreign_cohorts_first():
    reg = _reg()
    s = reg.register("lang", ["ta"])
    foreign = {"id": "supervisor_vip", "where": {"value_band": ["high"]}}
    base = {"cohorts": [foreign], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1},
                                                              "caller_id": {"D1": 1}},
                                                  "supervisor_vip": {"voice": {"V4": 1}}},
            "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
    lay = Layout({"voice": [s]}, [foreign])
    cfg = lay.compile(reg, {(s, "voice"): {"V2": 1.0}}, {}, base)
    assert cfg["cohorts"][0] == foreign and cfg["allocations"]["supervisor_vip"] == {"voice": {"V4": 1}}
    back = Layout.from_config(cfg, reg)
    assert back.overrides["voice"] == [s] and back.foreign == [foreign]


def test_did_target_even_spread_and_pool_floor():
    opts = ["D1", "D2", "D3", "D4"]
    t = did_target(opts, {"D1": "proven", "D2": "proven", "D3": "proven", "D4": "quarantined"})
    assert t == {"D1": 0.333333, "D2": 0.333333, "D3": 0.333334} or abs(sum(t.values()) - 1) < 1e-6
    assert "D4" not in t and max(t.values()) - min(t.values()) < 1e-5


def test_did_target_single_worn_number_moves_40pct_to_probes():
    t = did_target(["D1", "D2", "D3"], {"D1": "proven", "D2": "unknown", "D3": "unknown"})
    assert abs(t["D1"] - 0.6) < 1e-6 and abs(t["D2"] - 0.2) < 1e-6


def test_did_target_probes_small_when_pool_is_healthy_and_running_probes_keep_share():
    t = did_target(["D1", "D2", "D3"], {"D1": "proven", "D2": "proven", "D3": "unknown"})
    assert abs(t["D3"] - 0.04) < 1e-6
    t = did_target(["D1", "D2", "D3"], {"D1": "proven", "D2": "proven", "D3": "unknown"}, {"D3": 0.2})
    assert abs(t["D3"] - 0.2) < 1e-6
