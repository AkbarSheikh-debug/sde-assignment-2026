"""Every tunable threshold in one place, each with the reason it has this value.

Frozen before looking at fuzz-world grades (see DECISIONS.md ADR 009). Change a value here, never inline.
"""

# --------------------------------------------------------------------------- loop
TICK_MINUTES = 15                 # the simulator's tick; smallest reaction time we can have
EXP_EVAL_EVERY_MINUTES = 360      # arm decisions at most every 6h: re-testing the same data each tick inflates FPs
PLAN_EVERY_MINUTES = 120          # routine (non-urgent) planning cadence; data barely moves in less
AUTO_STEP_GAP_MINUTES = 15        # min gap between two auto-apply steps on one workflow (one tick)

# --------------------------------------------------------------------------- caller ID (5.3)
DID_FAST_WINDOW_HOURS = 2         # catches a sudden flag within ~2 dialling hours
DID_SLOW_WINDOW_HOURS = 6         # steadier estimate for slower degradation
DID_FLAG_RATIO = 0.60             # severe: pickup < 60% of the rest of the pool -> quarantine
DID_FLAG_Z = 5.0                  # one-sided z. We test every in-use number every tick (~thousands of checks a
                                  # run): z>3 gave ~1 false quarantine per world; real flags show z>6 within 2h
DID_FLAG_MIN_DIALS = 300          # population, not anecdote
DID_HEALTHY_RATIO = 0.85          # a probed number is "proven" at >= 85% of the pool
DID_PROBE_MIN_DIALS = 250         # dials before a probe can be promoted (se of ratio ~0.1)
DID_PROBE_BAD_MIN_DIALS = 600     # dials before a mediocre probe (0.6-0.85) is dropped
DID_PROBE_SHARE = 0.04            # 4%: a flagged number at 0.55 health costs < 2% expected health
DID_SINGLE_POOL_PROBE_TOTAL = 0.40  # one worn number: moving 40% to fresh ones beats any flagged risk
DID_OVERLOAD_FACTOR = 1.5         # a number above 1.5x its fair share is worn by load, not flagged
DID_MIN_POOL = 2                 # never concentrate all volume on one number (health falls with load)
DID_QUARANTINE_HOURS = 168        # base health does not recover; re-probing sooner is a harmful change
DID_REPROBE_SHARE = 0.02          # re-probe small enough that a still-bad number stays "neutral"
DID_PROBE_LOOKBACK_HOURS = 72     # probe evidence older than this no longer describes the number

# --------------------------------------------------------------------------- incidents (5.4)
VALIDATION_N = 200                # 2 credits; enough to separate 0.945 from 0.99 at the 0.97 bar
FLAG_P1_MULT = 2.5                # CUSUM alternative: flag rate 2.5x the clean baseline
CUSUM_H_MIN = 5.0                 # minimum CUSUM threshold (null drift is negative; ~months ARL)
CUSUM_CALIBRATION_PATHS = 300     # seeded null paths of 30 days each; h = their 99th-percentile maximum
CUSUM_QUANTILE = 0.99             # ~1% chance of a false alarm per workflow-month; alarms are also confirmed
CONFIRM_VALIDATION_N = 500        # a CUSUM alarm is acted on only if validation agrees (5 credits)
CONFIRM_Z = 1.64                  # live prompt validates worse than the last-known-good one at 5% one-sided
CUSUM_H_HISTORY_MULT = 1.5       # h = max(min, 1.5 x the largest statistic seen on clean history)
LKG_MIN_CONNECTED = 500           # a prompt needs this many live calls to count as "known good"
LKG_FLAG_MARGIN = 0.006           # flag rate <= baseline + 0.6pp counts as clean
FIX_VERIFY_CONNECTED = 300        # calls after a fix before we judge it
FIX_FAIL_Z = 2.33                 # a fix "failed" only if flags are above baseline at 1% one-sided
CREDIT_INCIDENT_RESERVE_SHARE = 0.5  # half the budget is kept for incidents
CREDIT_INCIDENT_MAX = 15          # one incident may not burn more than this
STRICT_PROMPT_WORDS = ("verbatim", "strict", "word for word", "word-for-word")  # Anita: kills engagement

# --------------------------------------------------------------------------- experiments (5.5/5.6)
ENG_DIMS = ("welcome_message", "voice")
PROBE_SHARE = 0.05                # a new arm enters at 5% of its scope
NEW_UNPROVEN_PER_APPLY = 0.06     # one new arm per apply: even a 0.83x arm at 5% costs < 1% expected engagement
UNPROVEN_TOTAL_CAP = 0.25         # never more than 30% of a scope on arms not yet shown safe
RAMP_SHARE_STEPS = (0.12, 0.20)   # ramp ladder; each step <= 8 points so a wrongly ramped 0.86x arm stays < 1.2%
RAMP_MIN_STEP = 0.05              # smaller ramps aren't worth an approver's attention
RAMP_PER_APPLY = 0.12             # share moved onto ramping arms per apply (they passed "not worse")
MAX_TEST_SHARE = 0.60             # incumbent keeps >= 40% of every scope while testing
RAMP_MIN_CONNECTED = 150          # calls on an arm before it may ramp
RAMP_P_BETTER = 0.80              # P(lift > 0) needed to ramp (0.6-0.7 ramped bad arms on noise in fuzz worlds)
PRUNE_MIN_CONNECTED = 250
DERAMP_P = 0.80                   # P(lift < 0) above this -> step back to probe share (cheap if wrong)
PRUNE_DELTA = -0.03               # "worse than control by 3%"
PRUNE_P = 0.90                    # P(lift < -3%) above this -> prune (asymmetric: a wrong prune is harmful)
WIN_MIN_CONNECTED = 400           # per arm and per control (16% lift detectable at 80% power)
WIN_MIN_DAYS = 2                  # at least two campaign days, so one odd batch can't crown a winner
WIN_DELTA = 0.02                  # winners must beat control by >2%, not just by >0
WIN_P = 0.95
BH_Q = 0.10                       # Benjamini-Hochberg false discovery rate across the day's tests
HOLDOUT_SHARE = 0.15              # kept on the old control after a promotion, to see decay
LIFT_HALF_LIFE_DAYS = 1.0         # yesterday counts half as much as today: effects decay, averages hide it
EVAL_DAYS = 4                     # experiment evidence window (days); older data may predate decay
DECAY_WINDOW_DAYS = 2             # recent window for "has the winner stopped winning"
DECAY_P = 0.90                    # P(lift < 0 over the recent window) above this -> roll back
TREND_Z = 2.5                     # latest day's lift this many se below the window lift = fading, don't promote
TREND_MIN_CONNECTED_TODAY = 150   # below this, "latest" is yesterday's full day
RECENT_P_POSITIVE = 0.80          # a winner must also be ahead over the last 2 days (decay onset guard)
DECAY_MIN_HOLDOUT = 120          # connected calls on the holdout before a rollback can fire
SEGMENT_MIN_SHARE = 0.15          # a segment becomes a cohort only if >=15% of connected calls
SEGMENT_MIN_CONNECTED = 400
MAX_COHORTS = 8                   # sim allows 20; 8 keeps per-cohort samples readable
SUPERVISOR_RESPECT_HOURS = 24     # dims a supervisor touched are left alone this long

# --------------------------------------------------------------------------- schedule (5.8)
SCHEDULE_ENABLED = False          # measured net-negative on public_01/02 (see DECISIONS ADR 008); kept, off
SCHEDULE_MIN_HISTORY_DIALS = 3000  # per workflow, before we trust an hour profile
SCHEDULE_SHRINK_DIALS = 400       # pseudo-dials shrinking a group-hour rate to the overall hour rate
SCHEDULE_W_MIN, SCHEDULE_W_MAX = 0.3, 3.0
SCHEDULE_MAX_GROUPS = 3
SCHEDULE_MIN_GAIN = 0.01          # predicted relative pickup gain needed to bother an approver

# --------------------------------------------------------------------------- governance (6)
EXPIRY_MARGIN_MINUTES = 60        # urgent requests wait until the approver can decide them with an hour to spare
CALLER_MAX_PREDICTED_DROP = 0.01  # safety 8: block a caller-ID spread predicted to cut pickup by more than 1%
APPROVAL_CLOSE_BUFFER_MIN = 120   # routine requests stop 2h before approvers leave
ROUTINE_MIN_GAP_MINUTES = 240     # two routine requests on one workflow at least 4h apart
REJECT_PARK_HOURS = 72            # an intent rejected twice is parked this long
STALE_RETRY_LIMIT = 3
GUARDRAIL_Z = 3.0                 # breach must be beyond noise at +24h *and* +48h (one day of drift is not it)
GUARDRAIL_RELATIVE = True         # read "max_drop: 0.02" as relative (stricter); see SUBMISSION A7

# Stakeholder overlays (memos). Stricter than policy only, keyed by tenant. Policy always wins if stricter.
STAKEHOLDER_OVERLAYS = {
    "kestrel_finance": {"max_requests_per_day": 2, "earliest_hour": 11},   # Anita, memo 04
}

# Ablation runs only (never set by run.sh): CONTROLLER_PARAM_OVERRIDES='{"SCHEDULE_ENABLED": true}'
import json as _json
import os as _os

for _k, _v in _json.loads(_os.environ.get("CONTROLLER_PARAM_OVERRIDES", "{}")).items():
    if _k not in globals():
        raise KeyError(f"unknown param override {_k}")
    globals()[_k] = _v
