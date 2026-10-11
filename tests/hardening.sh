#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
expect_exit "release hardening regressions" 0 "$QS_PYTHON" "$ROOT/tests/hardening.py"
if [ "$FAIL" != 0 ]; then printf '%s\n' "$OUT"; fi
finish
