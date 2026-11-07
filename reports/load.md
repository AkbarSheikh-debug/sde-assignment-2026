# Load test — public_03_load (12 workflows, 5 days, 7 history days)

Machine: Darwin arm64, 8 CPUs. One controller process, one simulator process, SQLite WAL on local disk.

| sim day | ticks | events ingested | our ingest s | our decide s | max decide s / tick | simulator advance s | our events/s |
|---|---|---|---|---|---|---|---|
| D0 | 96 | 6,377,581 | 45.6 | 1.4 | 0.50 | 6.6 | 139,764 |
| D1 | 96 | 840,040 | 15.2 | 1.1 | 0.06 | 7.1 | 55,219 |
| D2 | 96 | 840,044 | 30.4 | 1.4 | 0.08 | 7.5 | 27,665 |
| D3 | 96 | 840,038 | 27.6 | 1.7 | 0.12 | 6.7 | 30,452 |
| D4 | 96 | 840,040 | 42.9 | 2.1 | 0.15 | 7.0 | 19,565 |

- End-to-end wall time (history bootstrap + 5 days, incl. simulator): **204.9 s**
- Events ingested: 9,737,743; steady-state ingest throughput 60,210 events/s (D0-D4)
- Our time per simulated day (ingest + decide, D0-D4 mean): **33.9 s** (SRE target: < 600 s)
- Decision lag: decisions run every 15 sim-minute tick on data up to that tick (lag <= 1 tick); worst single decision step 0.50 s wall
- Peak RSS controller: **945 MB**
- Simulator RSS at end: 164 MB
- State DB size at end: 315 MB; decision rows: 441
