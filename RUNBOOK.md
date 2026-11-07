# Controller runbook

For the engineer on call. Everything here works from the repo root with Python 3.10+ and `requirements.txt`
installed (locally: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`; scripts default to
`.venv/bin/python`, override with `PYTHON=...`).

## Run it

```bash
python -m sim serve --scenario scenarios/public_02_incidents.yaml --run-dir runs/mine02 --force   # :8700
./run.sh http://127.0.0.1:8700                     # full run, no manual steps, exits when the scenario ends
CONTROLLER_ARGS=--plan-only ./run.sh <url>         # decide and log everything, change nothing
CONTROLLER_ARGS="--view-port 8710" ./run.sh <url>  # also serve the supervisor view + /metrics
make check                                         # 44 tests: stats, governance faults, crash, config math
scripts/run_scenario.sh public_01_dev 8701 public_01   # sim + controller + grade -> reports/public_01.md
scripts/crash_test.sh scenarios/public_02_incidents.yaml   # kill -9 x5 -> reports/crash_test.md
scripts/load_test.sh 8706                          # public_03 numbers -> reports/load.md
python scripts/fuzz_scenarios.py --out runs/fuzz && python scripts/fuzz_eval.py runs/fuzz/*.yaml --out reports/fuzz.md
```

State lives in `state/controller.db` (SQLite, WAL). `run.sh` keeps it when it belongs to the simulator world that is
running (same scenario, our clock not ahead, every approval id we hold exists there) and wipes it otherwise
(`python -m controller.statecheck`). So **after a crash or a deploy, just run `./run.sh <url>` again**; it resumes.
`CONTROLLER_RESUME=1` skips the check and always keeps the file.

Never delete the DB while the simulator keeps running: the controller would forget pending approvals and the
quarantine list. Restart instead.

## Investigate

```bash
python -m controller.explain --workflow ks_cancel --at "D3 13:15"      # what changed, why, who approved, what next
python -m controller.explain --lead "ks_cancel:l00030100" --day D6     # what this customer heard and why it was live
python -m controller.explain --decisions nw_kyc --day D2               # every decision for a workflow on a day
python -m controller.explain --pending                                 # proposals in flight and apply_by deadlines
python -m controller.explain --credits                                 # spend by workflow and reason vs reserve
python -m controller.explain --alerts                                  # alert history
python -m controller.view --db state/controller.db --port 8710         # supervisor view, read-only, any time
```
Add `--db state/<tag>.db` for runs started by the scripts.

`decisions` is the audit trail: every row is written *before* the action it describes. `proposals` holds every
request with its full payload and state. `config_versions` holds every config the workflow ever had (the API's
history has no configs). `calls` answers "what did this customer hear".

## Alerts

| Alert | Pages? | Means | Do this |
|---|---|---|---|
| `INCIDENT_UNFIXED` | yes | An unsafe prompt is live and no validated fix could be found or sent | `explain --decisions W` around the incident; check credits (`--credits`) and whether the fix is waiting for a request slot (`blocked` rows). A human may need to pick a prompt. |
| `APPLY_DEADLINE` | yes | An approved change lapsed before we applied it | `explain --pending`; look for API errors in the log. Re-planning happens automatically next window. |
| `OUT_OF_CREDITS` | yes | The simulator returned 402, or our ledger disagrees with it | Routine spend is already zero; confirm the shared budget, nothing loops (`--credits`). |
| `GUARDRAIL_BREACH` | no (ticket) | The guardrail KPI fell beyond policy at +24h and +48h after one of our changes, on a dim that can move it | `explain --workflow W --at <apply time>`; decide in the morning whether to roll back. |
| `AUTO_APPLY_REFUSED` | no (ticket) | 403: our policy check (safety check 5) disagreed with the simulator's | A bug in `config_build.max_shift` / `changed_dims`; the test `test_shift_and_dims_match_simulator` should be extended with the failing config. Target: 0. |
| `PROPOSAL_PARKED` | no | The same change was rejected twice; parked 72 h | Shows on the supervisor view. Nothing to do overnight. |
| `DID_QUARANTINED` | no | A caller ID was drained after a severe drop | Review in the morning; it is re-probed at 2% after 7 days. |

Loop health without reading logs: `/metrics` (with `--view-port`) shows the sim minute decided, chunks behind,
decisions in the last hour, credits vs incident reserve, proposals by state and alerts by name. `INGEST` falling
behind shows as `controller_ingest_chunks_behind > 0` for more than a tick; a restart is safe.

## Change a constraint

- Policy values (caps, approver hours, TTL, apply window, auto-apply dims, max_shift, validation bar) are read from
  `GET /workflows/{id}` every run; nothing to change in code.
- Stakeholder overlays that are stricter than policy (Anita: 2 requests/day, not before 11:00) live in
  `controller/params.py: STAKEHOLDER_OVERLAYS`, keyed by tenant. Incidents use the policy cap, not the overlay.
- Every threshold is in `controller/params.py` with its reason. For an ablation run without editing:
  `CONTROLLER_PARAM_OVERRIDES='{"SCHEDULE_ENABLED": true}' ./run.sh <url>` (run.sh never sets it).

## Known failure modes

- **Kill exactly between `POST /validation/runs` and recording its result** loses that run's credits and the retry
  draws a new run (the API cannot look a run up). Safe (`test_kill_after_validation_is_safe_but_not_byte_identical`),
  not byte-identical.
- **Approver works only in `approver_hours`.** An urgent fix found at 20:15 on a 12 h-TTL workflow is held until it can
  be decided before it expires (otherwise it would expire unseen and burn a request).
- **A supervisor edit** makes any approved-but-unapplied proposal stale; we mark it STALE without calling apply
  (no `apply_conflict`) and re-plan on the new base. Dims the supervisor touched are left alone for 24 h.
