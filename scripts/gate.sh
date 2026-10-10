#!/usr/bin/env bash
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
# The gate only ever exits 0 (pass) or 2 (fail): any other failure, a missing interpreter included, is a FAIL.
"$PY" "$(dirname "$0")/quality.py"; rc=$?
[ "$rc" = 0 ] && exit 0
[ "$rc" = 2 ] || echo "gate: FAIL (quality.py exit $rc)" >&2
exit 2
