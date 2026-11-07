"""Like-for-like guardrail check (F14): engagement per connected call by attempt number, ours vs baseline.

    python scripts/attempt_mix.py runs/mine_public_02 runs/base_public_02 nw_kyc
A drop in the blended rate with flat per-attempt rates is a composition effect of higher pickup, not harm.
(Analysis script over simulator outputs for the write-up; the controller never runs it.)"""
import collections, glob, json, sys

def stats(run, wf):
    c = collections.Counter()
    for f in sorted(glob.glob(f"{run}/events/*.jsonl")):
        for line in open(f):
            e = json.loads(line)
            if e["type"] == "call" and e["workflow_id"] == wf and e["connected"]:
                a = min(e["attempt_no"], 5)
                c[(a, "n")] += 1; c[(a, "e")] += e["engaged"]
    return c

mine, base, wf = sys.argv[1], sys.argv[2], sys.argv[3]
m, b = stats(mine, wf), stats(base, wf)
print(f"| attempt | connected (ours) | engagement (ours) | connected (baseline) | engagement (baseline) |")
print("|---|---|---|---|---|")
for a in range(1, 6):
    lab = f"{a}+" if a == 5 else str(a)
    print(f"| {lab} | {m[(a,'n')]:,} | {m[(a,'e')]/max(m[(a,'n')],1):.3f} | {b[(a,'n')]:,} | {b[(a,'e')]/max(b[(a,'n')],1):.3f} |")
tm, tb = sum(m[(a,'n')] for a in range(1,6)), sum(b[(a,'n')] for a in range(1,6))
print(f"| all | {tm:,} | {sum(m[(a,'e')] for a in range(1,6))/tm:.3f} | {tb:,} | {sum(b[(a,'e')] for a in range(1,6))/tb:.3f} |")
