#!/usr/bin/env bash
# Run one scenario end to end: fresh sim, controller, grade.  scripts/run_scenario.sh public_01_dev [port] [tag]
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="$1"; PORT="${2:-8700}"; TAG="${3:-$NAME}"
PY="${PYTHON:-.venv/bin/python}"
SC="scenarios/$NAME.yaml"; [ -f "$SC" ] || SC="$NAME"
mkdir -p runs reports state
[ -d "runs/base_$TAG" ] || "$PY" -m sim baseline --scenario "$SC" --run-dir "runs/base_$TAG" >/dev/null
"$PY" -m sim serve --scenario "$SC" --run-dir "runs/mine_$TAG" --force --port "$PORT" > "runs/sim_$TAG.log" 2>&1 &
SIM=$!
trap 'kill $SIM 2>/dev/null || true' EXIT
for i in $(seq 1 120); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.5; done
rm -f "state/$TAG.db" "state/$TAG.db-wal" "state/$TAG.db-shm"
CONTROLLER_DB="state/$TAG.db" ./run.sh "http://127.0.0.1:$PORT" 2> "runs/controller_$TAG.log"
"$PY" -m sim grade --run "runs/mine_$TAG" --baseline "runs/base_$TAG" --out "reports/$TAG.md" > /dev/null
"$PY" -m sim grade --run "runs/mine_$TAG" --baseline "runs/base_$TAG" --json > "runs/grade_$TAG.json"
echo "graded -> reports/$TAG.md"
