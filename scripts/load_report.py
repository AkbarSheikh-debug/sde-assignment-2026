"""Turn the controller's own per-day stats + /usr/bin/time output into reports/load.md."""
import json, os, re, sqlite3, sys

db, log, sim_rss_kb = sys.argv[1], sys.argv[2], int(sys.argv[3] or 0)
c = sqlite3.connect(db)
stats = json.loads(c.execute("SELECT value FROM meta WHERE key='day_stats'").fetchone()[0])
text = open(log).read()
m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
rss_mb = int(m.group(1)) / 1024 if m else None
if rss_mb is None:
    m = re.search(r"(\d+)\s+maximum resident set size", text)
    rss_mb = int(m.group(1)) / 1024 / 1024 if m else None
wall = re.search(r"'wall_s': ([\d.]+)", text)
events = re.search(r"'events': (\d+)", text)
dec_rows = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
db_mb = os.path.getsize(db) / 1e6
print("# Load test — public_03_load (12 workflows, 5 days, 7 history days)\n")
print(f"Machine: {os.uname().sysname} {os.uname().machine}, {os.cpu_count()} CPUs. One controller process, "
      "one simulator process, SQLite WAL on local disk.\n")
print("| sim day | ticks | events ingested | our ingest s | our decide s | max decide s / tick | simulator advance s | "
      "our events/s |")
print("|---|---|---|---|---|---|---|---|")
tot_i = tot_d = tot_e = 0
for d in sorted(stats, key=int):
    s = stats[d]
    eps = s["events"] / s["ingest_s"] if s["ingest_s"] else 0
    tot_i += s["ingest_s"]; tot_d += s["decide_s"]; tot_e += s["events"]
    print(f"| D{d} | {s['ticks']} | {s['events']:,} | {s['ingest_s']:.1f} | {s['decide_s']:.1f} | "
          f"{s['max_tick_decide_s']:.2f} | {s.get('sim_advance_s', 0):.1f} | {eps:,.0f} |")
print()
print(f"- End-to-end wall time (history bootstrap + 5 days, incl. simulator): **{wall.group(1) if wall else '?'} s**")
print(f"- Events ingested: {int(events.group(1)) if events else tot_e:,}; steady-state ingest throughput "
      f"{tot_e / tot_i if tot_i else 0:,.0f} events/s (D0-D4)")
print(f"- Our time per simulated day (ingest + decide, D0-D4 mean): **{(tot_i + tot_d) / max(len(stats), 1):.1f} s** "
      "(SRE target: < 600 s)")
print(f"- Decision lag: decisions run every 15 sim-minute tick on data up to that tick (lag <= 1 tick); "
      f"worst single decision step {max(s['max_tick_decide_s'] for s in stats.values()):.2f} s wall")
print(f"- Peak RSS controller: **{rss_mb:.0f} MB**" if rss_mb else "- Peak RSS: n/a")
print(f"- Simulator RSS at end: {sim_rss_kb / 1024:.0f} MB" if sim_rss_kb else "")
print(f"- State DB size at end: {db_mb:.0f} MB; decision rows: {dec_rows:,}")
