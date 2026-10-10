#!/usr/bin/env bash
# SessionStart hook: re-anchors the mission brief, plan, and flags into context after /compact or resume.
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"
mkdir -p "$CLAUDE_PROJECT_DIR/docs/ledgers" "$CLAUDE_PROJECT_DIR/.claude/state"

sess_json() { printf '{"hook_event_name":"SessionStart","source":"%s","cwd":"%s"}' "$1" "$CLAUDE_PROJECT_DIR"; }

# 1. source startup -> no-op, no output, even though nothing else set up
OUT="$(hook anchor.sh "$(sess_json startup)")"; rc=$?
if [ "$rc" = 0 ] && [ -z "$OUT" ]; then ok "startup: exit 0, empty stdout"; else bad "startup: exit 0, empty stdout (rc=$rc out=$OUT)"; fi

# 2. source compact without brief.json -> no-op
OUT="$(hook anchor.sh "$(sess_json compact)")"; rc=$?
if [ "$rc" = 0 ] && [ -z "$OUT" ]; then ok "compact w/o brief: exit 0, empty stdout"; else bad "compact w/o brief: exit 0, empty stdout (rc=$rc out=$OUT)"; fi

# 3. fixtures: brief.json + task.json (two tasks, one merged one pending; one assumption; one blocked entry)
cat > "$CLAUDE_PROJECT_DIR/.claude/state/brief.json" <<'EOF'
{
  "mission": {"goal": "Add a README that describes quickship", "deliverables": ["README.md"]},
  "success_criteria": [{"kind": "test", "cmd": "bash tests/run.sh", "expect": 0}],
  "budgets": {"tokens": 5000000, "cost_usd": 40, "wall_clock_min": 480, "steps": 400, "stall_limit": 3, "replan_limit": 5, "critic_rounds": 2},
  "permissions": {"irreversible": {"default": "skip-and-record", "allow": ["git push origin mission/*"]}},
  "ambiguity_policy": "choose-default-and-record"
}
EOF

cat > "$CLAUDE_PROJECT_DIR/docs/ledgers/task.json" <<'EOF'
{
  "goal": "Add a README that describes quickship",
  "plan": [
    {"slug": "add-readme", "goal": "Write the README", "owns": ["README.md"], "status": "merged", "branch": "mission/add-readme", "commit": "abc123"},
    {"slug": "wire-ci", "goal": "Wire up CI", "owns": [".github/workflows/ci.yml"], "status": "pending", "branch": "", "commit": ""}
  ],
  "facts": [],
  "assumptions": [{"text": "Node 20 is available in CI", "ts": "2026-09-30T09:00:00Z"}],
  "blocked": [{"step": "s003", "rule": "no direct push to main", "ts": "2026-09-30T09:05:00Z"}],
  "replan_count": 0, "stall_count": 0, "is_complete": false
}
EOF

touch "$CLAUDE_PROJECT_DIR/.claude/state/cancel"

OUT="$(hook anchor.sh "$(sess_json compact)")"; rc=$?
if [ "$rc" = 0 ]; then ok "compact with brief: exit 0"; else bad "compact with brief: exit 0 (rc=$rc out=$OUT)"; fi
parse_err="$(printf '%s' "$OUT" | "$QS_PYTHON" -c 'import json,sys; json.load(sys.stdin)' 2>&1 1>/dev/null)"
if [ -z "$parse_err" ]; then ok "compact with brief: stdout is valid JSON"; else bad "compact with brief: stdout is valid JSON ($parse_err)"; fi
ctx="$(printf '%s' "$OUT" | "$QS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])')"
ctx="${ctx//$'\r'/}"   # python on Windows emits CRLF; the two-line needles below must see LF
expect_contains "context has goal text" "Add a README that describes quickship" "$ctx"
expect_contains "context has mission-brief tag" "<mission-brief>" "$ctx"
expect_contains "context has merged slug+status" "add-readme|merged" "$ctx"
expect_contains "context has pending slug+status" "wire-ci|pending" "$ctx"
expect_contains "context has assumption text" "Node 20 is available in CI" "$ctx"
expect_contains "context has blocked step" "s003" "$ctx"
expect_contains "context has cancel=yes" "cancel=yes" "$ctx"
expect_contains "context has force_replan=no" "force_replan=no" "$ctx"
expect_contains "context has never-ask sentence" "You never ask a question" "$ctx"
expect_contains "context tells the lead to run the gate first" "run the gate before dispatching" "$ctx"
# no handoff note and no progress yet: the sections still appear, so the lead knows nothing was left for it
expect_contains "context has empty handoff section" "<handoff>
none yet" "$ctx"
expect_contains "context has empty progress section" "<progress>
none yet" "$ctx"

# 3b. a handoff note (ledger.py handoff) and progress lines: the newest handoff section and the last 5 events are injected
cat > "$CLAUDE_PROJECT_DIR/docs/ledgers/handoff.md" <<'EOF'
# Handoff notes

## 2026-09-30T09:10:00Z
Done: merged add-readme
Next: dispatch wire-ci

## 2026-09-30T09:40:00Z
Done: wire-ci failed twice on a missing secret
Next: mark wire-ci failed and run criteria
Note: CI needs Node 20
EOF
: > "$CLAUDE_PROJECT_DIR/docs/ledgers/progress.jsonl"
for i in 1 2 3 4 5 6; do
  printf '{"ts":"2026-09-30T09:0%s:00Z","step":"s00%s","slug":"t%s","event":"note","detail":"event %s","state_hash":"h","tokens":0,"cost_usd":0}\n' "$i" "$i" "$i" "$i" >> "$CLAUDE_PROJECT_DIR/docs/ledgers/progress.jsonl"
done
echo "not json" >> "$CLAUDE_PROJECT_DIR/docs/ledgers/progress.jsonl"
OUTH="$(hook anchor.sh "$(sess_json compact)")"; rc=$?
if [ "$rc" = 0 ]; then ok "compact with handoff: exit 0"; else bad "compact with handoff: exit 0 (rc=$rc out=$OUTH)"; fi
ctxh="$(printf '%s' "$OUTH" | "$QS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])')"
ctxh="${ctxh//$'\r'/}"
expect_contains "context has the newest handoff Next line" "Next: mark wire-ci failed and run criteria" "$ctxh"
expect_contains "context has the newest handoff note" "Note: CI needs Node 20" "$ctxh"
case "$ctxh" in *"Next: dispatch wire-ci"*) bad "context omits older handoff sections";; *) ok "context omits older handoff sections";; esac
expect_contains "context has the newest progress event" "s006 t6 note: event 6" "$ctxh"
expect_contains "context has the fifth-newest progress event" "s002 t2 note: event 2" "$ctxh"
case "$ctxh" in *"s001 t1"*) bad "context omits progress older than the last 5";; *) ok "context omits progress older than the last 5";; esac
case "$ctxh" in *"not json"*) bad "malformed progress line is skipped";; *) ok "malformed progress line is skipped";; esac
expect_contains "no git repo: recent-files section is empty" "<recent-files>
none yet" "$ctxh"
rm -f "$CLAUDE_PROJECT_DIR/docs/ledgers/handoff.md" "$CLAUDE_PROJECT_DIR/docs/ledgers/progress.jsonl"

# 3c. recent files: uncommitted changes first, then the last three commits, five paths at most, no duplicates
git -C "$CLAUDE_PROJECT_DIR" init -q -b main
git -C "$CLAUDE_PROJECT_DIR" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false commit -q --allow-empty -m "root"
for f in a b c d e f; do printf 'x\n' > "$CLAUDE_PROJECT_DIR/$f.txt"; git -C "$CLAUDE_PROJECT_DIR" add "$f.txt"; git -C "$CLAUDE_PROJECT_DIR" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false commit -q -m "$f"; done
printf 'y\n' > "$CLAUDE_PROJECT_DIR/e.txt"
printf 'z\n' > "$CLAUDE_PROJECT_DIR/new.txt"
OUTR="$(hook anchor.sh "$(sess_json resume)")"
ctxr="$(printf '%s' "$OUTR" | "$QS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])')"
ctxr="${ctxr//$'\r'/}"
section="$(printf '%s\n' "$ctxr" | sed -n '/<recent-files>/,/<\/recent-files>/p')"
expect_contains "recent-files lists an uncommitted modified file" "e.txt" "$section"
expect_contains "recent-files lists an uncommitted new file" "new.txt" "$section"
expect_contains "recent-files lists the newest commit's file" "f.txt" "$section"
case "$section" in *"a.txt"*|*"b.txt"*) bad "recent-files omits files older than the last three commits";; *) ok "recent-files omits files older than the last three commits";; esac
[ "$(printf '%s\n' "$section" | grep -c '\.txt')" -le 5 ] && ok "recent-files holds at most five paths" || bad "recent-files holds at most five paths: $section"
[ "$(printf '%s\n' "$section" | grep -c '^e\.txt$')" = "1" ] && ok "recent-files lists a path once" || bad "recent-files lists a path once: $section"
expect_contains "closing instruction names recent-files" "Read <handoff> and <recent-files>" "$ctxr"
rm -rf "$CLAUDE_PROJECT_DIR/.git" "$CLAUDE_PROJECT_DIR"/*.txt

# 4. source resume behaves like compact
OUT2="$(hook anchor.sh "$(sess_json resume)")"; rc=$?
if [ "$rc" = 0 ]; then ok "resume: exit 0"; else bad "resume: exit 0 (rc=$rc out=$OUT2)"; fi
ctx2="$(printf '%s' "$OUT2" | "$QS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])')"
expect_contains "resume context has goal text" "Add a README that describes quickship" "$ctx2"
expect_contains "resume context has never-ask sentence" "You never ask a question" "$ctx2"

# 5. a brief with a 20000-char goal yields additionalContext of at most 9000 characters
big="$("$QS_PYTHON" -c "print('x' * 20000)")"
cat > "$CLAUDE_PROJECT_DIR/.claude/state/brief.json" <<EOF
{"mission": {"goal": "$big", "deliverables": ["README.md"]}, "success_criteria": [], "budgets": {"tokens":1,"cost_usd":1,"wall_clock_min":1,"steps":1,"stall_limit":1,"replan_limit":1,"critic_rounds":1}, "permissions": {"irreversible": {"default": "skip-and-record", "allow": []}}, "ambiguity_policy": "choose-default-and-record"}
EOF
OUT3="$(hook anchor.sh "$(sess_json compact)")"
len="$(printf '%s' "$OUT3" | "$QS_PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(len(d["hookSpecificOutput"]["additionalContext"]))')"
if [ -n "$len" ] && [ "$len" -le 9000 ]; then ok "huge goal: additionalContext <= 9000 chars"; else bad "huge goal: additionalContext <= 9000 chars (got $len)"; fi

# 6. malformed stdin fails closed
expect_exit "malformed stdin fails closed" 2 hook anchor.sh "not json"
expect_exit "empty stdin fails closed" 2 hook anchor.sh ""

finish
