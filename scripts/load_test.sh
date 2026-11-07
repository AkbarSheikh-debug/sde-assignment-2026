#!/usr/bin/env bash
# Load test on public_03 (12 workflows, ~650k dials/day). Writes reports/load.md from measured numbers only.
#   scripts/load_test.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8706}"; PY="${PYTHON:-.venv/bin/python}"; TAG=public_03
mkdir -p runs reports state
"$PY" -m sim serve --scenario scenarios/public_03_load.yaml --run-dir runs/mine_$TAG --force --port "$PORT" > runs/sim_$TAG.log 2>&1 &
SIM=$!; trap 'kill $SIM 2>/dev/null || true' EXIT
for i in $(seq 1 300); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
rm -f state/$TAG.db state/$TAG.db-wal state/$TAG.db-shm
if /usr/bin/time -v true >/dev/null 2>&1; then TFLAG=-v; else TFLAG=-l; fi      # GNU (Linux) vs BSD (macOS)
/usr/bin/time $TFLAG "$PY" -m controller.main --sim-url "http://127.0.0.1:$PORT" --db state/$TAG.db 2> runs/controller_$TAG.log
SIM_RSS_KB=$(ps -o rss= -p $SIM | tr -d ' ' || echo 0)
"$PY" scripts/load_report.py state/$TAG.db runs/controller_$TAG.log "$SIM_RSS_KB" > reports/load.md
[ -d runs/base_$TAG ] || "$PY" -m sim baseline --scenario scenarios/public_03_load.yaml --run-dir runs/base_$TAG > /dev/null
"$PY" -m sim grade --run runs/mine_$TAG --baseline runs/base_$TAG --out reports/$TAG.md > /dev/null
cat reports/load.md
