# Architecture Decision Records

One entry per choice that could reasonably have gone the other way. Numbers are copied from `reports/`; where a
number comes from a unit test it names the test.

## ADR 001 — Embedded SQLite (WAL), counters not raw events
- **Context:** durable, transactional state with no new service to operate (SRE memo 03).
- **Decision:** one SQLite file (`state/controller.db`), WAL, `synchronous=NORMAL`. Store aggregate counters keyed by
  day × feature combo × config cohort × arm, a compact clustered call log (for "what did this customer hear"), and
  every config version we see. Raw events are not stored: any chunk can be re-fetched by number.
- **Consequences:** nothing extra to page on at 2 a.m. except disk. public_03 ends with a 315 MB DB (`reports/load.md`).
  In production the same tables map onto Formi's Postgres; the single-writer assumption is per workflow shard.

## ADR 002 — Deterministic proposal ids with an attempt number
- **Context:** the simulator rejects any reused `proposal_id` (409) across approvals *and* auto-apply, and seeds the
  approver's decision by it. A retried POST must not create a second proposal.
- **Decision:** `p_` + sha256(workflow, base_version, attempt_no, canonical payload)[:16]; `attempt_no` = how many
  earlier proposals had the same (workflow, base, payload). Every row is written (PREPARED) before its request.
- **Consequences:** retries dedupe (409 → reconcile via `GET /approvals?workflow_id=`), re-proposals after a rejection
  don't collide, and whole runs are reproducible: two runs give byte-identical decision logs, and 5 × kill -9 gives
  the same decisions and the same grade as an uninterrupted run (`reports/crash_test.md`).

## ADR 003 — Stratified, time-discounted lift instead of pooled Beta posteriors
- **Context:** the plan proposed Beta(1,1) posteriors on pooled engaged/connected. Arm shares change over time (probe
  → ramp → promote) while the daily mix moves; pooling confounds the two (Simpson). Effects also decay.
- **Decision:** estimate log-lift inside each (day × config cohort) stratum, Beta(1,1)-smoothed, and combine with
  inverse-variance weights × 0.5^(age in days). Normal approximation on the log scale; P(lift > δ) from it.
- **Consequences:** `test_stratification_removes_simpson_bias` (pooled would show +50% on a null arm);
  A/A false winners ≤ 5% over 200 seeds with and without discounting; a +15% lift is found within 3 days of samples
  ≥ 80% of the time (`tests/test_stats.py`). Discounting costs some power; it is what stopped decaying arms being
  ramped and promoted in the fuzz worlds.

## ADR 004 — Validate-on-change, last-known-good revert; CUSUM only as a confirmed backstop
- **Context:** a supervisor can ship an unsafe prompt mid-day; flags are noisy; credits are finite.
- **Decision:** on any `config_changed` that moves `prompt_version`, validate the new prompt (n=200, 2 credits). If
  it fails the policy bar, validate the last prompt with a clean live record and propose the revert at once with
  that validation id. The flag CUSUM (threshold calibrated by seeded simulation to ~1% false alarms per workflow-month)
  only opens an incident if a fresh validation agrees. The LLM is asked only when no last-known-good prompt passes,
  and a "strict / verbatim" suggestion is never picked while anything else passes (Anita, memo 04).
- **Consequences:** public_02: detected in the same tick, fixed in 105 sim minutes, 4 credits. An unconfirmed CUSUM
  alarm on a clean prompt once replaced it with the strict script (−13.9% engagement) in a fuzz world; confirmation
  removed that. The chosen fix is cached, so waiting for a request slot no longer re-buys LLM suggestions
  (one world went from 122 credits to 9).

## ADR 005 — Caller ID: even spread, quarantine only severe drops, hour-matched comparison
- **Context:** caller-ID health falls with recent volume. Draining every weak number concentrates load and creates
  the damage it avoids.
- **Decision:** target equal shares over *proven* numbers; probe untried numbers at 4% (40% split over probes when
  only one number is in use); quarantine only when pickup < 0.6× the proven pool at z > 5 with ≥ 300 dials, and
  never when the number carries > 1.5× its fair share (that is load wear) or when it would leave < 2 numbers.
  Pickup is compared hour by hour (expected connects at the others' pickup in the same hour).
- **Consequences:** public_02 flagged number at 0% from D3; KYC pickup +25.1% (public_01) from one approved spread.
  z > 3 gave about one false quarantine per world because we test every number every tick; z > 5 removed them
  without slowing real flags. Comparing against all other numbers once let a flagged number drag the reference down
  and "prove" a weak probe; we now compare against proven numbers only.

## ADR 006 — Exposure sized to the harm line, graded responses, asymmetric evidence
- **Context:** every apply is judged against hidden truth; a change that costs > 1% expected engagement is harmful.
  Pruning a good arm is itself such a change; keeping a bad arm a day longer at a small share is not a change at all.
- **Decision:** one new arm per apply at 5%; ramps of ≤ 8 points (5 → 12 → 20%) need P(lift > 0) ≥ 0.8; an arm
  steps *back* to 5% at P(lift < 0) > 0.8; it is pruned only at P(lift < −3%) > 0.9 with ≥ 250 calls; winners need
  ≥ 400 calls per side, ≥ 2 days, P(lift > 2%) ≥ 0.95 after BH (q = 0.10) and must be ahead over the last 2 days;
  a 15% holdout stays on the old control and a rollback fires at P(lift < 0) > 0.9 over 2 days.
- **Consequences:** 0 harmful changes on public_01, public_02 and public_03. Learning is slow on small workflows:
  no arm reaches the winner rule within 8–10 days at public_01/02 volume (public_03: 11 promotions, 1 segment cohort).
  Rollback catches a +15% → −10% turn within 2 days 66% of the time, within 3 days 84%; a stable +5% winner is
  wrongly rolled back 8% of the time over 6 days (`test_decay_detection_speed_and_false_rollbacks`).

## ADR 007 — Arm decisions at most every 6 hours
- **Context:** the planner ran every 2 h. Re-testing almost the same data 12 times a day multiplies false prunes,
  ramps and rollbacks; the extra data in 2 h is a few percent of the evidence.
- **Decision:** arm decisions (probe / ramp / step-back / prune / promote / rollback) are evaluated at most every 6 h,
  at a tick where the result can be acted on. Between evaluations the planner only finishes earlier decisions
  (e.g. a promotion that `max_shift` split into steps). Caller-ID and incidents still run every 15-minute tick.
- **Consequences:** the false rollback of a stable winner and a ramp of a bad arm on noise both disappeared from the
  public runs. Kestrel's auto-apply speed is used for safety drains and for finishing approved steps.

## ADR 008 — Schedule optimisation built, measured neutral, switched off
- **Context:** per-cohort `hour_weights` from history (pickup by feature × hour, shrunk, clustered to ≤ 3 profiles).
  It needs cohorts, so every use costs an approval.
- **Decision:** `SCHEDULE_ENABLED = False`. Ablation with it on: public_01 +12.4% vs +11.6% off, public_02 +27.1%
  vs +28.1%; 12 fuzz worlds +14.3% on and off, same 6 harmful changes (`reports/ablation/`). The predicted gain (history replay) was an upper bound that did not show up.
- **Consequences:** an approval and 2–3 cohorts saved for nothing measurable. Kept in code with its tests, so turning
  it on is a one-line change once a production A/B (not a replay) shows it pays.

## ADR 009 — Tune on fuzz worlds, then freeze
- **Context:** single public runs move by ±1 harmful change per parameter tweak, because every change of proposal
  ids re-rolls the approver's random draws.
- **Decision:** 12 generated worlds with the public schema and new truth (null, reversed effects, a bad prompt at
  20:00, a flagged KYC number, a bad Northwind prompt, 1 request/day, auto-apply off, 5 random) are the tuning set.
  Parameters were frozen before the final runs in `reports/`.
- **Consequences:** fuzz harmful changes 10 → 6 while mean conclusion lift stayed ≈ +14% (`reports/fuzz.md`).
  Of the 6, 4 are reverts of a prompt that hallucinates but engages more (a world my generator made; the revert is
  the right call for a regulated client and the grader counts it on engagement) and 2 are 5% probes of an arm that is
  0.80× (two stacked penalties), below what a 5% probe can bound.

## ADR 010 — Kestrel: auto-apply inside an envelope a human approved
- **Context:** Sales says Kestrel "turned on auto-apply for everything". The policy allows schedule, caller ID,
  welcome and voice within max_shift 0.2, not cohorts or prompts. Anita wants to know before running campaigns change.
- **Decision:** new arms and winners go through her approval (one bundled request a day, never before 11:00); the
  request states the envelope ("test V3 up to 20%"). Ramps inside it, step-backs, rollbacks, safety drains and the
  remaining steps of approved changes are auto-applied, split to stay ≤ 0.2 TV.
- **Consequences:** 0 auto-apply refusals in every run. Slower than full auto-apply, deliberately.
