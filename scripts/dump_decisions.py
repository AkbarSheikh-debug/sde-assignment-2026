"""Print the decision log in a canonical form (for crash/determinism diffs). Usage: dump_decisions.py DB"""
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
for row in db.execute("SELECT wf, minute, kind, summary, evidence_json, proposal_id FROM decisions ORDER BY seq"):
    print("\t".join("" if x is None else str(x) for x in row))
for row in db.execute("SELECT proposal_id, wf, route, base_version, attempt_no, state, approval_id, applied_version "
                      "FROM proposals ORDER BY created_minute, proposal_id"):
    print("P\t" + "\t".join("" if x is None else str(x) for x in row))
