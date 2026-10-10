#!/usr/bin/env bash
# v0.3 launcher scenarios use simulated providers; no live model/GitHub calls.
source "$(dirname "$0")/lib.sh"
expect_exit "controller/preflight/resume/publishing scenarios" 0 "$QS_PYTHON" "$ROOT/tests/controller.py"
if [ "$FAIL" != 0 ]; then printf '%s\n' "$OUT"; fi
finish
