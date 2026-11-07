# Crash-recovery drill — public_02_incidents

Controller process SIGKILLed 5 times, restarted with `CONTROLLER_RESUME=1 ./run.sh` after each kill, run to the end; compared with an uninterrupted run on a second fresh simulator.

| # | kill point | sim minute | proposal in flight at kill |
|---|---|---|---|
| 1 | minute | 2250 (D1 13:30) | True |
| 2 | mid_approval | 2250 (D1 13:30) | True |
| 3 | minute | 9390 (D6 12:30) | True |
| 4 | minute | 13260 (D9 05:00) | False |
| 5 | minute | 13950 (D9 16:30) | False |

- Decision log + proposals table identical (wire-level rows excluded): **True** (128 rows)
- Grade identical (KPIs, verdicts, governance, incidents): **True**
- Approval requests: 24 vs 24 uninterrupted; applied via approval 20 vs 20; auto-applied 16 vs 16
- Apply conflicts 0, lapsed 0, auto-apply refused 0 (all must be 0)
- Conclusion rate ALL: 0.4235 vs 0.4235
