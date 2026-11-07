# Formi Controller — Build Plan

Put this file in the repo root next to `docs/SIMULATOR.md`. Work one gate at a time (section 7). Each gate has a
prompt for the coding agent and a pass/fail check. Don't start a gate until the previous one passes.

---

## 1. Hard rules (break one and the submission fails)

1. The controller learns **only** through the HTTP API. Never `import sim`, read `runs/`, or open `scenarios/`.
   A test greps `controller/` for these and fails the build.
2. Every config change goes through `POST /approvals` → `POST /workflows/{id}/apply`, or through `PUT /workflows/{id}/config`
   where the policy allows it. Never overwrite a supervisor edit without an approval.
3. Pickup and engagement are never blended. Optimise `objective.primary` and respect `objective.guardrail`.
4. Winners are picked by deterministic statistics. The LLM only suggests prompt fixes.
5. No action is triggered by a single call. Every detector uses thresholds over a population.
6. Every decision is written to our own SQLite store **before** we act on it.
7. Kill -9 at any point → restart → same final result, no double-apply, no lost approval.
8. No wall clock and no unseeded randomness inside decisions. Seeds = hash(workflow_id, sim_minute).

## 2. Simulator facts we verified (read the code and ran it; build on these)

| # | Fact | Consequence |
|---|---|---|
| F1 | A no-op controller advancing 15 min at a time produces aggregates **identical** to `sim baseline`. | Gate G1 check is exact: grade must show 0.0% deltas. |
| F2 | Real endpoints: `/events/chunks?kind=&after=`, `/events/chunks/{n}`, `/validation/runs`, `/approvals/{id}`, `/workflows/{id}/config/history`. There is **no** `/workflows/{id}/events` and **no** `/llm/validate-prompt`. Default port **8700**. | Use these names only. |
| F3 | `/config/history` returns version, actor, dims, ids — **not the config itself**. | Store a snapshot of every config version ourselves (needed for last-known-good prompt and audit). |
| F4 | `proposal_id` is one namespace for approvals **and** auto-apply, and any reuse → `409 duplicate_proposal_id`. A 403 auto-apply does not consume the id. | ID = hash(workflow, base_version, payload, **attempt_no**). Without `attempt_no`, re-proposing the same change after a rejection/expiry collides. |
| F5 | The approver's judgement-call rejection and latency are seeded by `proposal_id`. | Deterministic ids make whole runs reproducible (needed for the kill-9 test). |
| F6 | **Every** request in the last 24h counts toward `max_requests_per_24h`, including rejected ones. | Count our own requests in the store; never send one that would exceed the cap. |
| F7 | `cohorts` is a change dimension and is **not** in any workflow's `allowed_dims`. | Creating/editing cohorts always needs approval, even on Kestrel. Allocation changes inside an existing cohort can be auto-applied on Kestrel. |
| F8 | `mid_flight: next_campaign_only` locks `cohorts` **and all allocations, including caller_id**, per campaign. | A KYC caller-ID change applied today reaches customers from tomorrow's 08:00 batch. `prompt_version` and `schedule` apply immediately everywhere. |
| F9 | Caller-ID health falls with recent volume on that number and recovers over ~days. | Spread volume evenly. Taking numbers out concentrates load and **causes** the damage you were avoiding (see F10). |
| F10 | Prototype: a naive "drain any number with pickup < 0.8× the others" rule put renewal on one number by D6; pickup fell from 0.26 to 0.19. The grader rated every step "beneficial" at apply time. | Use the caller-ID rules in 5.3: severe-only quarantine, even spread, minimum pool of 2. |
| F11 | Prototype: validate a supervisor's new prompt the moment `config_changed` arrives (2 credits), validate the previous prompt (2 credits), propose the revert with evidence → public_02 bad prompt fixed in **90–150 min, 4 credits**. Detection-only (CUSUM) takes ~1.5 h longer. | Validate-on-change is the main incident path; CUSUM is the backstop. |
| F12 | Prototype: one D0 approval spreading KYC from D1-only to 60/20/20 gave **+15–18% pickup** (KYC's primary KPI). | Highest-value single change in the public scenarios. Retry next day if judgement-rejected. |
| F13 | Load scenario: simulator ≈ 7 s per simulated day; stdlib `json` parse + aggregate ≈ 270k events/s (≈ 3 s/day for 840k events); 7-day history = 5.5M events ≈ 20 s. | SRE's 10-min/day target is easy. No orjson, no workers needed. Don't claim a bottleneck you didn't measure. |
| F14 | When pickup rises, engagement-per-connected can dip ~1–3% (the extra pickups are people who didn't engage earlier). | Expected selection effect. Guardrail check must compare like with like (same attempt number / campaign day) before calling a breach. |

Only `fastapi`/`httpx`/`uvicorn`/`numpy`/`pyyaml`/`pytest` are needed (already in `requirements.txt`). Add nothing else.

## 3. Architecture

One process, one SQLite file (`state/controller.db`, WAL). Loop every 15 sim minutes:

```
advance(15) → ingest new chunks → reconcile approvals & versions → detect → plan intents
   → build ONE candidate config per workflow → SafetyEvaluator → route (auto-apply | approval | wait)
   → write decision + outbox row → call API → record result
```

```
controller/
  main.py          loop, startup reconcile, --plan-only flag
  client.py        httpx client; retry rules (sec 6)
  store.py         schema + transactions
  ingest.py        chunk cursor, lead joins, counters
  cohorts.py       one cohort registry (ids stable, order preserved)
  stats.py         Beta posteriors, P(lift>δ), shrinkage, BH, CUSUM
  detect.py        caller_id, hallucination, decay, guardrail
  plan.py          intents: did_spread, prompt_revert, arms, promote, rollback, schedule
  config_build.py  merge intents onto current config; diff; impact/affected/dry_run
  safety.py        12 checks (5.9)
  governance.py    slots, timing, outbox, state machine, reconcile
  audit.py         `python -m controller.explain`
  view.py          supervisor page + /metrics (stdlib http.server, read-only)
  params.py        every threshold in one place, each with a one-line reason
tests/  scripts/ (crash_test.sh, load_test.sh, fuzz_scenarios.py, grade_all.sh)
```

## 4. Store (lean — keep counters, not raw events)

| Table | Key | Holds |
|---|---|---|
| `cursor` | stream | last committed chunk no. Updated **in the same transaction** as the counters it produced. |
| `leads` | lead_id (int) | person_id (int), workflow, feature code (lang·tier·age·value packed in one int), campaign |
| `calls` | (workflow, sim_min, lead) | compact: config_version, cohort, wm, voice, did, prompt, connected, engaged, flag, attempt_no. Answers "what did this customer hear". |
| `counters` | (workflow, day, hour, seg_code, wm, voice, did, prompt, cfg_ver) | dials, connected, engaged, flagged. Every statistic reads from here. |
| `config_versions` | (workflow, version) | full config JSON, actor, dims, proposal/approval id, seen_at |
| `proposals` | proposal_id | workflow, base_version, attempt_no, intents, payload, local_state (`PREPARED→SUBMITTED→PENDING→APPROVED→APPLIED` or `REJECTED/EXPIRED/LAPSED/STALE/BLOCKED`), approval_id, timestamps |
| `decisions` | decision_id | workflow, sim_min, kind, rationale, evidence JSON, diff JSON, outcome |
| `post_apply` | (proposal_id, window) | primary + guardrail KPI at +4h/+24h/+48h vs pre-window; predicted vs realised impact |
| `credits` | op_id (unique) | spend per workflow and reason (incident reserve vs routine) |

Don't store raw event JSON: chunks can always be re-fetched by number, and storing them is ~4M rows/day on the load scenario.

## 5. Decision logic (parameters live in `params.py`)

**5.1 Ingest.** Read history chunks first (bootstrap), then new chunks after each advance. Dedupe by `event_id ≤ cursor`.
Join `call` to `lead` features via lead_id. Track `config_changed` (fetch and snapshot the new config immediately) and
`approval_decided`.

**5.2 Cohorts.** One registry. Analysis uses single-feature segments (each lang/age/tier/value value). A segment
becomes a config cohort only with a confirmed effect and ≥15% of daily connected calls. Cap 8 cohorts. New cohorts are
appended **after** existing ones (first-match order never changes for existing leads). Readable stable ids (`age_60p`).

**5.3 Caller ID.**
- Health signal: rolling 6h pickup of each number vs the rest of the pool (caller ID is randomised per call, so
  comparable).
- **Flagged** (quarantine): ratio < 0.6, z > 3, ≥300 dials. Drain in `max_shift` steps; quarantine 72h; then re-probe.
- **Worn** (0.6–0.85): don't quarantine. Even spread fixes it.
- Target allocation = **equal shares across proven healthy numbers**; never fewer than 2 numbers in the pool (if only
  2 remain, don't quarantine, rebalance).
- Untried numbers: probe at 4–5% each; promote to the equal share only after ≥250 dials with ratio ≥ 0.85; else quarantine.
- Routing: auto-apply where `caller_id` is allowed (renewal, Kestrel); otherwise one bundled proposal (KYC: 60/20/20
  on D0, equalise after probes). Remember F8 for KYC.

**5.4 Hallucination incidents.**
- On any `config_changed` that changes `prompt_version` (supervisor or us): run `/validation/runs` n=200 on the new
  prompt (2 credits). If pass_rate < `min_validation_pass_rate`: validate the last-known-good prompt (n=200), then
  propose the revert **immediately, using the reserved slot**, with the validation id as evidence and the numbers in
  plain words.
- Last-known-good = most recent prompt in our snapshots with a clean live record (flag rate ≤ baseline + margin over
  ≥500 connected calls) that passes validation.
- Backstop: binomial CUSUM on `hallucination_flag` per workflow, p0 = history flag rate, p1 = 2.5·p0, threshold `h`
  calibrated on history for < 1 false alarm per 30 days.
- No LKG? `/llm/suggest-fixes` (3 credits), validate candidates n=200, reject any "near-verbatim/strict" candidate
  unless nothing else passes (safe but kills engagement — Anita's memo). After apply: flags back to baseline within 300
  connected calls **and** engagement not below the pre-incident baseline, else next candidate.
- Credits: 50% reserved for incidents; one incident ≤ 15 credits.

**5.5 Experiments (welcome × voice).**
- Dimensions are randomised independently per call → one config tests both; analyse each marginally, check the W×V
  interaction weekly.
- **Exposure budget** (the grader judges every apply; harmful = expected engagement −1%): new unproven arms ≤ 6% each,
  ≤ 12% new unproven share per apply, ≤ 20% unproven share per workflow at any time.
- Prune an arm after ≥2 days if P(worse than control by 3%) > 0.7. Ramp to 15–20% when P(better) > 0.7.
- Learn only from the `variant` on call events (KYC arms appear a campaign later).
- On Northwind, arms ride the daily bundled proposal. On Kestrel, allocation steps can be auto-applied **inside an
  envelope Anita approved** (e.g. "test V3/V4 up to 20% each"); new cohorts always need approval (F7).

**5.6 Winner rule.** Beta(1,1) posterior on engaged/connected (pickup for KYC). Segment effects shrunk toward the arm's
overall effect (empirical Bayes). Promote when **all** hold: ≥400 connected on arm and control in that cohort; ≥2 full
days exposed; P(relative lift > 2%) ≥ 0.95; BH at q=0.10 across that day's tests (use 1 − posterior prob as the
p-value stand-in, say so in SUBMISSION); predicted guardrail drop within policy. Minimum sample → detectable lift:
400≈16%, 800≈11%, 1300≈9% (base 0.55, α=5% one-sided, 80% power).

**5.7 Decay & rollback.** Keep a 10% holdout on the old control after every promotion. Every 6h: P(lift < 0) over
48h > 0.8 or CUSUM on daily log-lift fires → rollback intent (auto on Kestrel, top routine item on Northwind).

**5.8 Schedule (partial).** From history, pickup by (age_band × city_tier, hour), shrunk and clustered into ≤4 time
profiles. `hour_weights` = relative pickup clipped to [0.3, 3]; **every hour in the window gets a weight** (missing hour =
0). Per-profile tables need cohorts → approval. A single `default` table can be auto-applied. Per-person preferred hour:
behind a flag, returning people with ≥3 dials only.

**5.9 SafetyEvaluator — every candidate passes all 12 or is blocked with a logged reason.**
1. Built on the current base version (snapshot matches live) — never over a supervisor edit.
2. Only options listed in `options`; weights ≥0, sum to 1 ±1e-6.
3. Cohort predicates use known features/values; ≤8 cohorts; existing cohort order unchanged.
4. Schedule covers every hour in `[start,end)`, nothing outside it; ≤200k preferred-hour entries.
5. Auto-apply route only if every changed dim ∈ `allowed_dims` and max TV shift ≤ `max_shift` (same formula as the sim).
6. Prompt change carries a validation run for this workflow+prompt with pass_rate ≥ bar.
7. Exposure budget (5.5) respected.
8. Predicted guardrail drop ≤ policy; predicted primary change has a finite interval.
9. Caller-ID pool ≥ 2 numbers; no number takes > 1/(n−1) share unless others are quarantined.
10. Request cap: our requests in the last 24h + 1 ≤ min(policy cap, stakeholder cap); routine requests leave the incident slot free.
11. Timing: routine requests only from approver open (Kestrel: 11:00) to close − 2h. Incident requests any time.
12. Data fresh: ingest cursor at the latest chunk. (Checks 7, 10, 11, 12 are relaxed for incident fixes, never 1–6.)

**5.10 Every proposal carries:** one-line summary; per-cohort/per-dim diff; affected_leads = active leads in touched
cohorts + expected share of tomorrow's batch (int); dry_run = predicted KPI change with 80% interval from the last 3 days
of matching calls; impact_score = (expected extra concluded leads/day ÷ workflow's daily concluded) × P(benefit), clipped
to [0,1]; rationale; evidence ids.

## 6. Governance details

- Slots/day = min(policy, stakeholder) − 1 reserve: renewal 2+1, KYC 1+1, Kestrel 1+1 (Anita: max 2, not before 11:00).
- One bundled proposal per workflow per day, priority: safety > caller ID > rollback > promotion > new arms > schedule.
- Poll pending approvals every tick; apply as soon as `approved` (2h window). Before apply, if live version ≠ base →
  mark STALE, re-queue intents, rebuild on the new base next window.
- Rejected (judgement) → retry once next day with fresh data and a new attempt_no; rejected twice → park 72h, show on view.
- Expired = rejected (Product). Lapsed → re-queue. 402 → stop routine spend. 403 → bug: log and alert (target 0).
- Supervisor edit → becomes new control; close arms on that dim; never revert it silently.
- **Client retry rules:** GET retry with backoff (0.2→2 s, 3×). `/sim/advance` never retried blind: on timeout read
  `/sim/state`. POST approvals/auto-apply: retry same id; on 409 duplicate → reconcile via `GET /approvals?workflow_id=`
  or `/config/history` (match proposal_id). Before `/apply`, check `/config/history` for our approval_id.
- Startup reconcile: replay outbox rows in PREPARED/SUBMITTED/APPROVED against the API, then continue.

## 7. Gates — paste one prompt at a time

Common preamble for every prompt: *"Read BUILD_PLAN.md and docs/SIMULATOR.md first. Follow the hard rules in section 1.
Write tests first, keep functions small, put thresholds in params.py with a one-line reason, and commit after each
passing step with a message `area: what and why`."*

**G0 — Scaffold & rules (≈1h).** "Create the package layout from section 3, `tests/test_forbidden.py` (fails if any file
under controller/ imports sim or references runs/ or scenarios/), `params.py`, and `client.py` with the retry rules in
section 6. Update run.sh to `python -m controller.main --sim-url "$1"`. Add `make check` running pytest."
✔ `pytest tests/test_forbidden.py` passes; `./run.sh http://127.0.0.1:8700` starts and exits cleanly when the sim finishes.

**G1 — Store, ingest, no-op loop (≈2h).** "Implement store.py (section 4), ingest.py (5.1) with cursor + counters in one
transaction, config snapshots, and a main loop that advances 15 min, ingests and changes nothing."
✔ `sim grade` on public_01 shows exactly 0.0% delta vs baseline (F1). Kill and restart mid-run → no double counting
(counters equal to an uninterrupted run).

**G2 — Governance, safety, config builder (≈2.5h).** "Implement config_build.py, safety.py (5.9), governance.py
(section 6) with outbox, attempt_no ids (F4), cap counting (F6), timing, reconcile. Table-driven tests for every state
transition including 409 duplicate, 409 stale, 410 lapsed, 402, 403, expiry."
✔ All state-machine tests pass. A fault-injection test (client drops the response after the server acted) creates no
duplicate proposal or apply.

**G3 — Caller ID + incidents (≈3h).** "Implement 5.3 and 5.4 in detect.py/plan.py, plus explain CLI v1 in audit.py."
✔ public_02: flagged ks_cancel D2 share 0 by D3; P2 fixed by the controller in < 4 sim hours; ≤ 15 credits; 0
`auto_apply_rejected`; 0 harmful. public_01: no caller-ID change rated harmful; renewal never ends with < 2 numbers.

**G4 — Experiments & winners (≈3h).** "Implement stats.py and 5.2, 5.5, 5.6, 5.10. Statistical tests: A/A split of
history calls into fake arms → false promotions ≤ 5% over 200 seeds; power test detects a simulated 15% lift within 3
sim days of samples."
✔ Tests pass. public_01: 0 harmful; at least one promotion with evidence; every proposal has all fields non-empty.

**G5 — Decay, schedule, guardrail (≈2h).** "Implement 5.7, 5.8, the post_apply watch, and guardrail rollback (F14:
compare same attempt_no/campaign day)."
✔ Unit tests: decay detected within 2 sim days in a synthetic +15%→−7% series; every generated schedule covers the full
window. public_01 decaying-variant share falls after onset.

**G6 — Crash, load, fuzz (≈2h).** "Write scripts/crash_test.sh: start a fresh sim, run controller, SIGKILL at 5 random
ticks (one between propose and apply), restart each time, finish; run an uninterrupted controller on a second fresh
sim; diff decision logs and grades. Write scripts/load_test.sh for public_03 (wall time per sim day, events/s, peak
RSS via /usr/bin/time -v, decision lag). Write scripts/fuzz_scenarios.py: same schema, new truth (null world, reversed
effects, incident at 20:00, flagged KYC number, bad Northwind prompt, max_requests=1, auto-apply off)."
✔ Decision logs byte-identical; null world → 0 promotions; no fuzz run has a harmful change or a missed incident.
Freeze params **before** looking at fuzz grades.

**G7 — View, docs, rehearsal (≈2h).** "Implement view.py (one page per workflow: being tested, winning, waiting for
approval, changed today) and /metrics (ingest lag, decisions/h, credits vs reserve, approvals in flight). Fill
SUBMISSION.md from the template with numbers copied from reports/. Write RUNBOOK.md."
✔ `python -m controller.explain --workflow ks_cancel --at "D3 14:00"` answers what/why/who approved/what happened next.

## 8. Don'ts

- Don't write any number in SUBMISSION.md that isn't in `reports/`. Never claim hidden-scenario results.
- Don't explore prompts for lift; prompts change only to fix safety.
- Don't auto-apply anything customer-facing on Northwind (policy forbids it, and Meera asked).
- Don't tune thresholds to the public scenarios' truth. Tune on fuzz worlds, then freeze.

> Status: this was the starting plan. Where the built controller differs (stratified + discounted lift, exposure
> sized to the harm line, z>5 caller-ID flags, schedule off, 6h arm evaluation), DECISIONS.md says why.
