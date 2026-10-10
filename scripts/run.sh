#!/usr/bin/env bash
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 2
export CLAUDE_PROJECT_DIR="$PWD"
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
exec "$PY" scripts/runner.py "$@"
