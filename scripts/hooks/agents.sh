#!/usr/bin/env bash
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
in="$(cat)"
printf '%s' "$in" | "$PY" "$(dirname "$0")/../agent_hook.py"; rc=$?
[ "$rc" = 0 ] && exit 0
# Fail closed (exit 2 forces the subagent to continue), but never trap a subagent a second time.
[[ "$in" =~ \"stop_hook_active\"[[:space:]]*:[[:space:]]*true ]] && exit 0
exit 2
