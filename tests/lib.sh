#!/usr/bin/env bash
# Shared helpers for tests/*.sh. Source this file; call finish at the end.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export QS_PYTHON="${QS_PYTHON:-$(command -v python3 || command -v python)}"
# Claude Code exports CLAUDE_PROJECT_DIR to its hooks, and the Stop hook's gate runs this suite in the harness
# repo. Scripts under test read the frozen brief and runtime state from that directory, so an inherited value
# points every fixture at the real repo instead of its own .claude/state. Each test sets it when it needs it.
unset CLAUDE_PROJECT_DIR
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1" >&2; }
# expect_exit <label> <want-exit> <cmd...>   (stdout/stderr of cmd go to $OUT)
expect_exit() {
  local label=$1 want=$2; shift 2
  OUT="$("$@" 2>&1)"; local got=$?
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want exit $want, got $got): $(printf '%s' "$OUT" | tail -n 3)"; fi
}
expect_contains() { # expect_contains <label> <needle> <haystack>
  case "$3" in *"$2"*) ok "$1";; *) bad "$1 (missing '$2' in: $(printf '%s' "$3" | tail -n 3))";; esac
}
# hook <name> <json>  -> feeds json on stdin to scripts/hooks/<name>
hook() { printf '%s' "$2" | bash "$ROOT/scripts/hooks/$1"; }
tmpdir() { mktemp -d "${TMPDIR:-/tmp}/qs-test.XXXXXX"; }
finish() { echo "$(basename "$0"): $PASS passed, $FAIL failed"; [ "$FAIL" = 0 ]; }
