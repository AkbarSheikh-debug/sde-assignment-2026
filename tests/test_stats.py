"""Statistical properties of the winner rule: false winners under A/A, power under a real lift, BH, CUSUM."""
import math

import numpy as np

from controller import params as P
from controller.detect import calibrate_h
from controller.stats import bh, cusum_binomial, min_detectable_lift, shrink, stratified_lift


def _simulate(rng, days, n_arm_day, n_ctrl_day, p_ctrl, lift):
    strata = []
    for d in range(days):
        na, nc = rng.poisson(n_arm_day), rng.poisson(n_ctrl_day)
        day_shift = rng.normal(0, 0.03)             # day-to-day mix moves both arms together
        pc = min(max(p_ctrl * (1 + day_shift), 0.01), 0.99)
        pa = min(pc * (1 + lift), 0.99)
        strata.append((d, na, rng.binomial(na, pa), nc, rng.binomial(nc, pc)))
    return stratified_lift(strata)


def winner(l):
    return (l.n_arm >= P.WIN_MIN_CONNECTED and l.n_ctrl >= P.WIN_MIN_CONNECTED and l.days >= P.WIN_MIN_DAYS
            and l.p_gt(P.WIN_DELTA) >= P.WIN_P)


def test_aa_false_promotions_at_most_5pct_over_200_seeds():
    rng = np.random.default_rng(2027)
    false = sum(winner(_simulate(rng, 4, 150, 600, 0.55, 0.0)) for _ in range(200))
    assert false / 200 <= 0.05, false


def test_power_detects_15pct_lift_within_3_days_of_samples():
    rng = np.random.default_rng(11)
    hits = sum(winner(_simulate(rng, 3, 250, 600, 0.55, 0.15)) for _ in range(200))
    assert hits / 200 >= 0.8, hits


def test_stratification_removes_simpson_bias():
    # arm gets most of its traffic on a high-engagement day; true lift is 0
    strata = [(0, 900, 630, 100, 70), (1, 100, 40, 900, 360)]
    l = stratified_lift(strata)
    assert abs(l.rel) < 0.05
    pooled = (630 + 40) / 1000 / ((70 + 360) / 1000) - 1
    assert pooled > 0.5          # naive pooling would have crowned it


def test_bh_controls_family():
    assert bh([0.001, 0.2, 0.03, 0.04], 0.10) == [True, False, True, True]
    assert bh([0.5, 0.6], 0.10) == [False, False]
    assert bh([], 0.1) == []


def test_shrinkage_pulls_noisy_segments_toward_overall():
    rng = np.random.default_rng(3)
    overall = _simulate(rng, 4, 400, 1600, 0.5, 0.0)
    segs = {f"s{i}": _simulate(rng, 4, 40, 160, 0.5, 0.0) for i in range(8)}
    sh = shrink(segs, overall)
    assert sum(abs(sh[k].est) for k in segs) < sum(abs(segs[k].est) for k in segs)


def test_shrinkage_keeps_real_heterogeneity():
    rng = np.random.default_rng(5)
    overall = _simulate(rng, 4, 400, 1600, 0.5, 0.0)
    segs = {"big_up": _simulate(rng, 4, 400, 1600, 0.5, 0.25), "big_down": _simulate(rng, 4, 400, 1600, 0.5, -0.2)}
    segs.update({f"n{i}": _simulate(rng, 4, 200, 800, 0.5, 0.0) for i in range(4)})
    sh = shrink(segs, overall)
    assert sh["big_up"].rel > 0.15 and sh["big_down"].rel < -0.1


def test_cusum_quiet_on_clean_and_fires_on_bad_prompt():
    rng = np.random.default_rng(9)
    p0 = 0.015
    h = calibrate_h("wf_test", p0, 100, 12)
    clean = [(100, int(k)) for k in rng.binomial(100, p0, 360)]
    _, first, _ = cusum_binomial(clean, p0, p0 * P.FLAG_P1_MULT, h)
    assert first is None
    bad = [(100, int(k)) for k in rng.binomial(100, 0.054, 24)]
    _, first, _ = cusum_binomial(bad, p0, p0 * P.FLAG_P1_MULT, h)
    assert first is not None and first <= 6          # within 6 dialling hours at 100 connected/hour


def test_calibrated_threshold_holds_false_alarm_rate_out_of_sample():
    # calibration targets P(false alarm in 30 days) <= 10%; check on fresh paths at low and high volume
    for n_hour in (50, 1500):
        h = calibrate_h("w", 0.015, n_hour, 12)
        rng = np.random.default_rng(12345 + n_hour)
        alarms = 0
        for _ in range(300):
            ks = rng.binomial(n_hour, 0.015, 360)
            _, first, _ = cusum_binomial(((n_hour, int(k)) for k in ks), 0.015, 0.015 * P.FLAG_P1_MULT, h)
            alarms += first is not None
        assert alarms / 300 <= 0.15, (n_hour, alarms)


def test_min_detectable_lift_matches_plan_table():
    assert math.isclose(min_detectable_lift(400), 0.16, abs_tol=0.01)
    assert math.isclose(min_detectable_lift(800), 0.113, abs_tol=0.01)


def _rollback_day(rng, after_lift, days=10, turn=4, base_lift=0.15):
    hist, fired = [], None
    for d in range(days):
        lift = base_lift if d < turn else after_lift
        nc, na = rng.poisson(150), rng.poisson(850)            # 15% holdout of ~1000 connected/day
        hist.append((d, na, rng.binomial(na, min(0.55 * (1 + lift), 0.99)), nc, rng.binomial(nc, 0.55)))
        l = stratified_lift([x for x in hist if x[0] >= d - P.DECAY_WINDOW_DAYS + 1], d, P.LIFT_HALF_LIFE_DAYS)
        if fired is None and l.n_ctrl >= P.DECAY_MIN_HOLDOUT and l.p_lt(0.0) > P.DECAY_P:
            fired = d
    return fired


def test_decay_detection_speed_and_false_rollbacks():
    """Rollback rule, checked once a day. Winner +15% turns to -10% on day 4: caught within 2 days >= 60%,
    within 3 days >= 80%, before the turn <= 2%. A stable +5% winner is rolled back by mistake <= 12% of the time
    over 6 days. (Measured: 66% / 84% / 0.3% / 8%. For a milder turn to -7%: 50% / 68%.)"""
    rng = np.random.default_rng(77)
    res = [_rollback_day(rng, -0.10) for _ in range(300)]
    assert sum(f is not None and f < 4 for f in res) / 300 <= 0.02
    assert sum(f is not None and 4 <= f <= 5 for f in res) / 300 >= 0.60
    assert sum(f is not None and 4 <= f <= 6 for f in res) / 300 >= 0.80
    rng = np.random.default_rng(5)
    false = [_rollback_day(rng, 0.05, days=6, turn=99, base_lift=0.05) for _ in range(300)]
    assert sum(f is not None for f in false) / 300 <= 0.12


def test_discounting_keeps_aa_false_winners_low():
    rng = np.random.default_rng(99)
    false = 0
    for _ in range(200):
        strata = [(d, rng.poisson(150), 0, rng.poisson(600), 0) for d in range(4)]
        strata = [(d, na, rng.binomial(na, 0.55), nc, rng.binomial(nc, 0.55)) for d, na, _, nc, _ in strata]
        l = stratified_lift(strata, 3, P.LIFT_HALF_LIFE_DAYS)
        false += winner(l)
    assert false / 200 <= 0.05
