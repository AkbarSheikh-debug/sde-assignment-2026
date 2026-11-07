#!/usr/bin/env bash
# Start the controller and run until the scenario finishes. No Docker, no extra services.
# Needs Python 3.10+ with requirements.txt installed (numpy/pyyaml/fastapi/uvicorn/httpx/pytest; the controller
# itself only uses httpx + the standard library). State: state/controller.db (SQLite, WAL).
#   ./run.sh http://127.0.0.1:8700            # fresh run (wipes state from a previous scenario)
#   CONTROLLER_RESUME=1 ./run.sh <url>        # resume after a crash: keep state/controller.db
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
DB="${CONTROLLER_DB:-state/controller.db}"
if [ "${CONTROLLER_RESUME:-0}" != "1" ] && [ -f "$DB" ]; then
  # a DB from a different simulator world would hold approvals that world never saw
  "$PY" -m controller.statecheck --sim-url "$SIM_URL" --db "$DB" || rm -f "$DB" "$DB-wal" "$DB-shm"
fi
exec "$PY" -m controller.main --sim-url "$SIM_URL" --db "$DB" ${CONTROLLER_ARGS:-}
