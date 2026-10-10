#!/usr/bin/env bash
# Stop hook: no active run -> gate only. Active run -> require a terminal RUN_STATE before the gate
# runs, with 2 warnings then a forced SAFE_STOP on the 3rd attempt. HALT skips the gate entirely.
source "$(dirname "$0")/lib.sh"
mk() { # mk <dir>: fresh git repo with a copy of scripts/ and an initial commit
  git init -q -b main "$1" && cp -r "$ROOT/scripts" "$1/" && (cd "$1" && git add -A && git -c user.email=t@t -c user.name=t commit -qm init)
  seed_quality "$1"
}
seed_quality() { printf 'quality:\n  lint:\n    skip: "fixture"\n  test: "true"\n  build:\n    skip: "fixture"\n' > "$1/BRIEF.yaml"; }
run_stop() { # run_stop <repo> <json> -> runs the real stop.sh against <repo> as CLAUDE_PROJECT_DIR
  ( cd "$1" && CLAUDE_PROJECT_DIR="$1" bash "$ROOT/scripts/hooks/stop.sh" <<<"$2" )
}
attempts() { cat "$1/.claude/state/stop_attempts" 2>/dev/null; }
stop_json='{"hook_event_name":"Stop","session_id":"s1","transcript_path":"/tmp/t.jsonl","stop_hook_active":false}'

# --- no brief.json: no-op run, stop hook just gates ---
r1="$(tmpdir)"; mk "$r1"; seed_quality "$r1"
expect_exit "no brief.json: gate runs, clean repo passes" 0 run_stop "$r1" "$stop_json"

# --- brief.json present, no RUN_STATE: 2 blocks then forced SAFE_STOP on the 3rd ---
r2="$(tmpdir)"; mk "$r2"; seed_quality "$r2"; mkdir -p "$r2/.claude/state"; printf '{}' > "$r2/.claude/state/brief.json"
expect_exit "no RUN_STATE, attempt 1: blocked" 2 run_stop "$r2" "$stop_json"
expect_contains "attempt 1: block reason" "no terminal RUN_STATE: write docs/RUN_STATE and docs/REPORT.md before stopping" "$OUT"
[ "$(attempts "$r2")" = "1" ] && ok "attempt 1: stop_attempts=1" || bad "attempt 1: stop_attempts=$(attempts "$r2")"

expect_exit "no RUN_STATE, attempt 2: blocked" 2 run_stop "$r2" "$stop_json"
[ "$(attempts "$r2")" = "2" ] && ok "attempt 2: stop_attempts=2" || bad "attempt 2: stop_attempts=$(attempts "$r2")"

expect_exit "no RUN_STATE, attempt 3: forced SAFE_STOP, gate runs and passes" 0 run_stop "$r2" "$stop_json"
expect_contains "attempt 3: RUN_STATE has SAFE_STOP" "SAFE_STOP" "$(cat "$r2/docs/RUN_STATE" 2>/dev/null)"

# --- RUN_STATE already DONE: gate runs normally ---
r3="$(tmpdir)"; mk "$r3"; mkdir -p "$r3/.claude/state" "$r3/docs"
printf '{}' > "$r3/.claude/state/brief.json"
printf '{"state":"DONE","reason":"ok","at":"2026-01-01T00:00:00Z"}\n' > "$r3/docs/RUN_STATE"
expect_exit "RUN_STATE DONE: gate runs, clean repo passes" 0 run_stop "$r3" "$stop_json"

# --- RUN_STATE HALT: exit 0 without running the gate (proven by a secret the gate would catch) ---
r4="$(tmpdir)"; mk "$r4"; mkdir -p "$r4/.claude/state" "$r4/docs"
printf '{}' > "$r4/.claude/state/brief.json"
printf '{"state":"HALT","reason":"paused","at":"2026-01-01T00:00:00Z"}\n' > "$r4/docs/RUN_STATE"
printf '%s%s\n' AKIA ABCDEFGHIJKLMNOP > "$r4/untracked_secret.txt"   # split so this file never contains the key
expect_exit "RUN_STATE HALT: exit 0, gate not run" 0 run_stop "$r4" "$stop_json"

# --- same repo, RUN_STATE flipped to DONE: gate now runs and its failure (the secret) passes through ---
printf '{"state":"DONE","reason":"ok","at":"2026-01-01T00:00:00Z"}\n' > "$r4/docs/RUN_STATE"
expect_exit "RUN_STATE DONE with secret file: gate failure passes through" 2 run_stop "$r4" "$stop_json"

# --- malformed stdin: fails closed ---
r5="$(tmpdir)"; mk "$r5"
expect_exit "malformed stdin fails closed" 2 run_stop "$r5" "not json"
expect_exit "empty stdin fails closed" 2 run_stop "$r5" ""

# --- overseer role: the overseer's own session shares the repo and must never block or write RUN_STATE ---
r5="$(tmpdir)"; mk "$r5"; mkdir -p "$r5/.claude/state"; printf '{}' > "$r5/.claude/state/brief.json"
expect_exit "overseer role: stop never blocks" 0 bash -c "cd '$r5' && CLAUDE_PROJECT_DIR='$r5' QS_ROLE=overseer bash '$ROOT/scripts/hooks/stop.sh' <<<'$stop_json'"
[ -z "$(attempts "$r5")" ] && ok "overseer role: stop_attempts untouched" || bad "overseer role: stop_attempts=$(attempts "$r5")"
[ ! -f "$r5/docs/RUN_STATE" ] && ok "overseer role: no RUN_STATE written" || bad "overseer role: RUN_STATE was written"

# --- strategist role (scripts/idea.sh): no mission run, so no gate; proven by a secret the gate would catch ---
r6="$(tmpdir)"; mk "$r6"
printf '%s%s\n' AKIA ABCDEFGHIJKLMNOP > "$r6/untracked_secret.txt"   # split so this file never contains the key
expect_exit "negative control: lead role with secret file, gate fails" 2 run_stop "$r6" "$stop_json"
expect_exit "strategist role: exit 0, gate not run" 0 bash -c "cd '$r6' && CLAUDE_PROJECT_DIR='$r6' QS_ROLE=strategist bash '$ROOT/scripts/hooks/stop.sh' <<<'$stop_json'"
mkdir -p "$r6/.claude/state"; printf '{}' > "$r6/.claude/state/brief.json"
expect_exit "strategist role with a run's brief.json: never blocks" 0 bash -c "cd '$r6' && CLAUDE_PROJECT_DIR='$r6' QS_ROLE=strategist bash '$ROOT/scripts/hooks/stop.sh' <<<'$stop_json'"
[ -z "$(attempts "$r6")" ] && ok "strategist role: stop_attempts untouched" || bad "strategist role: stop_attempts=$(attempts "$r6")"
[ ! -f "$r6/docs/RUN_STATE" ] && ok "strategist role: no RUN_STATE written" || bad "strategist role: RUN_STATE was written"
finish
