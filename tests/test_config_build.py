"""Our config arithmetic must agree with the simulator's, or auto-apply would 403 (safety check 5)."""
import copy
import random

from controller.config_build import changed_dims, clean_dist, max_shift, normalise, reuse_exact, step_toward
from sim import world as W

SPEC = {"options": {"welcome_message": ["W1", "W2", "W3"], "voice": ["V1", "V2", "V3"],
                    "caller_id": ["D1", "D2", "D3"], "prompt_version": ["P1", "P3"]}, "dialling_window": [9, 21]}


def rand_dist(rng, opts):
    k = rng.randint(1, len(opts))
    return clean_dist({o: rng.random() + 0.01 for o in rng.sample(opts, k)})


def rand_cfg(rng, cohorts):
    cfg = {"cohorts": cohorts, "allocations": {"default": {d: rand_dist(rng, SPEC["options"][d])
                                                            for d in ("welcome_message", "voice", "caller_id")}},
           "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
    for c in cohorts:
        if rng.random() < 0.7:
            cfg["allocations"][c["id"]] = {"voice": rand_dist(rng, SPEC["options"]["voice"])}
    return cfg


def test_shift_and_dims_match_simulator():
    rng = random.Random(1)
    cohorts = [{"id": "a", "where": {"age_band": ["60+"]}}]
    for _ in range(300):
        a, b = rand_cfg(rng, cohorts), rand_cfg(rng, cohorts)
        sa, sb = W.normalise_config(SPEC, a), W.normalise_config(SPEC, b)
        assert abs(max_shift(sa, sb) - W.max_shift(sa, sb)) < 1e-9
        assert changed_dims(sa, sb) == W.changed_dims(sa, sb)


def test_step_toward_respects_limit_exactly_as_sim_measures():
    rng = random.Random(2)
    for _ in range(200):
        a = W.normalise_config(SPEC, rand_cfg(rng, []))
        b = W.normalise_config(SPEC, rand_cfg(rng, []))
        step = step_toward(a, normalise(b), 0.2)
        assert W.max_shift(a, W.normalise_config(SPEC, step)) <= 0.2 + 1e-9


def test_reuse_exact_prevents_phantom_changes():
    sim_cfg = W.normalise_config(SPEC, {"allocations": {"default": {"welcome_message": {"W1": 0.94, "W2": 0.06},
                                                                    "voice": {"V1": 1}, "caller_id": {"D1": 1, "D2": 2}}},
                                        "prompt_version": "P1"})
    ours = normalise(copy.deepcopy(sim_cfg))            # re-rounded copy, maybe differs in the last ulp
    ours["allocations"]["default"]["caller_id"] = clean_dist({"D1": 0.5, "D2": 0.5})
    out = reuse_exact(sim_cfg, ours)
    assert W.changed_dims(sim_cfg, W.normalise_config(SPEC, out)) == ["caller_id"]


def test_clean_dist_sums_to_one_and_is_stable():
    rng = random.Random(3)
    for _ in range(500):
        d = clean_dist({f"x{i}": rng.random() for i in range(rng.randint(1, 5))})
        assert abs(sum(d.values()) - 1) < 1e-6 and all(v > 0 for v in d.values())
        assert clean_dist(d) == d
