#!/usr/bin/env bash
# kill -9 drill; see scripts/crash_test.py.   scripts/crash_test.sh [scenario]
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PYTHON:-.venv/bin/python}" scripts/crash_test.py "$@"
