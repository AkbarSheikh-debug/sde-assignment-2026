# Controller — code map

Start with `SUBMISSION.md` (what and why), `RUNBOOK.md` (operate it), `DECISIONS.md` (choices that could have gone
the other way). This page is for changing the code.

| File | Owns | Read it when |
|---|---|---|
| `controller/main.py` | The loop: ingest → step → mark minute decided → advance 15 | Changing cadence or startup |
| `controller/client.py` | HTTP + retry rules (GET retried; advance never retried blind; mutations single-shot) | A new endpoint |
| `controller/store.py` | SQLite schema, transactions, `decide()` (audit row), canonical JSON | Adding state |
| `controller/ingest.py` | Chunk cursor, lead → feature-combo join, counters; one transaction per chunk | New event fields |
| `controller/cohorts.py` | Segments (stable ids), layout ↔ config compile/parse | Cohort semantics |
| `controller/stats.py` | Stratified discounted lift, shrinkage, BH, CUSUM | Any statistic |
| `controller/detect.py` | Hour-matched caller-ID ratios, flag CUSUM + calibration, prompt records | Detection |
| `controller/plan.py` | Targets: caller-ID spread, the arm ladder (probe/ramp/step-back/prune/promote/rollback), schedule | Learning behaviour |
| `controller/incidents.py` | Credits ledger, validate-on-change, CUSUM confirmation, last-known-good, LLM fallback, verify | Incident flow |
| `controller/config_build.py` | Normalise, changed dims, TV shift (simulator's formula), step split, exact-byte reuse | Auto-apply 403s |
| `controller/safety.py` | The 12 checks; every candidate passes or is blocked with a reason | Adding a guard |
| `controller/proposal.py` | Summary, diff, affected leads, dry run, impact score | Proposal content |
| `controller/governance.py` | Outbox state machine, ids, caps, timing, reconcile, poll/apply | Lifecycle bugs |
| `controller/decide.py` | Per-workflow step: incidents → caller → arms → build → route (auto / approval / wait) | Routing |
| `controller/audit.py` / `explain.py` | `python -m controller.explain` | Audit questions |
| `controller/view.py` | Supervisor page + `/metrics` (read-only) | The screen |
| `controller/statecheck.py` | Does this DB belong to the running simulator? (resume vs fresh) | Deploy behaviour |
| `controller/params.py` | Every threshold with its reason; `CONTROLLER_PARAM_OVERRIDES` for ablations | Tuning |

**Invariants a change must keep** (each has a test):
1. Nothing under `controller/` imports `sim` or reads `runs/`, `scenarios/` or hidden fields (`test_forbidden.py`).
2. A request row is written before the request; ids are deterministic; unknown outcomes reconcile before any retry
   (`test_governance.py`).
3. Counters commit with the cursor (`test_reapplying_a_chunk_does_not_double_count`).
4. No wall clock or unseeded randomness in decisions (two runs → identical decision logs; `scripts/crash_test.sh`).
5. Our `max_shift`/`changed_dims` equal the simulator's (`test_shift_and_dims_match_simulator`).
6. A candidate that differs from the live config but carries no intent is never sent (it is logged as `blocked`).

**Adding a new kind of change:** produce a target in `plan.py` with an `Intent` (key, kind, summary, evidence), add
its priority to `PRIORITY`, let `decide.build()` compile it, make sure `safety.evaluate` covers it, and add a fuzz
world to `scripts/fuzz_scenarios.py` that exercises it. Judge it on `scripts/fuzz_eval.py`, not on one public run:
single runs move by ±1 harmful change per tweak because proposal ids re-seed the approver.
