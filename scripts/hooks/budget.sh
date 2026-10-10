#!/usr/bin/env bash
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
# Fail closed: any exit other than 0 (including a missing interpreter) becomes 2, the only blocking code.
"$PY" "$(dirname "$0")/../budget_hook.py" || exit 2
