#!/usr/bin/env bash
# Run one scenario end to end: fresh sim, controller, grade.   scripts/run_scenario.sh public_01_dev [port] [tag]
# RUNS_DIR (default runs/) holds simulator output; event/history chunks are deleted after grading unless
# KEEP_EVENTS=1 (public_03 writes ~4 GB). Keep RUNS_DIR off synced folders (iCloud/Dropbox) for speed.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="$1"; PORT="${2:-8700}"; TAG="${3:-$NAME}"
PY="${PYTHON:-.venv/bin/python}"; RUNS="${RUNS_DIR:-runs}"
SC="scenarios/$NAME.yaml"; [ -f "$SC" ] || SC="$NAME"
mkdir -p "$RUNS" reports state
[ -f "$RUNS/base_$TAG/_truth/aggregates.json" ] || "$PY" -m sim baseline --scenario "$SC" --run-dir "$RUNS/base_$TAG" >/dev/null
"$PY" -m sim serve --scenario "$SC" --run-dir "$RUNS/mine_$TAG" --force --port "$PORT" > "$RUNS/sim_$TAG.log" 2>&1 &
SIM=$!
trap 'kill $SIM 2>/dev/null || true' EXIT
for i in $(seq 1 600); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
rm -f "state/$TAG.db" "state/$TAG.db-wal" "state/$TAG.db-shm"
CONTROLLER_DB="state/$TAG.db" ./run.sh "http://127.0.0.1:$PORT" 2> "$RUNS/controller_$TAG.log"
"$PY" -m sim grade --run "$RUNS/mine_$TAG" --baseline "$RUNS/base_$TAG" --out "reports/$TAG.md" > /dev/null
"$PY" -m sim grade --run "$RUNS/mine_$TAG" --baseline "$RUNS/base_$TAG" --json > "$RUNS/grade_$TAG.json"
[ "${KEEP_EVENTS:-0}" = "1" ] || rm -rf "$RUNS/mine_$TAG/events" "$RUNS/mine_$TAG/history" "$RUNS/base_$TAG/events" "$RUNS/base_$TAG/history"
echo "graded -> reports/$TAG.md"
