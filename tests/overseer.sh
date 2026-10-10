#!/usr/bin/env bash
# scripts/overseer_status.py on fixture ledgers, then scripts/overseer.sh (one tick) against a stub `claude`.
source "$(dirname "$0")/lib.sh"

PY="$QS_PYTHON"
STATUS="$ROOT/scripts/overseer_status.py"

# jget <json> <python-expr over d>  -> prints the value (None -> "null", bools -> true/false), CR stripped
jget() {
  local out
  out="$(printf '%s' "$1" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
v = eval(sys.argv[1])
if v is None: print("null")
elif v is True: print("true")
elif v is False: print("false")
else: print(v)
' "$2" 2>&1)"
  printf '%s' "${out//$'\r'/}"
}
# iso_ago <minutes> -> ISO 8601 UTC timestamp that many minutes in the past
iso_ago() {
  local out
  out="$("$PY" -c '
import datetime as dt, sys
t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=float(sys.argv[1]))
print(t.strftime("%Y-%m-%dT%H:%M:%SZ"))
' "$1")"
  printf '%s' "${out//$'\r'/}"
}
# status_project -> a fresh fake root with .claude/state and docs/ledgers; prints the path
status_project() {
  local p; p="$(tmpdir)"
  mkdir -p "$p/.claude/state" "$p/docs/ledgers"
  printf '%s' "$p"
}
write_brief() { # write_brief <project>
  printf '%s' '{"mission":{"goal":"Ship the widget","deliverables":["README.md"]},"success_criteria":[],"budgets":{"tokens":5000000,"cost_usd":40,"wall_clock_min":480,"steps":400,"stall_limit":3,"replan_limit":5,"critic_rounds":2}}' > "$1/.claude/state/brief.json"
  printf '%s\n' "$(iso_ago 10)" > "$1/.claude/state/started_at"
}
write_task() { # write_task <project>
  cat > "$1/docs/ledgers/task.json" <<'JSON'
{"goal": "Ship the widget",
 "plan": [
  {"slug": "a-pending", "goal": "a", "owns": ["a"], "status": "pending", "branch": "", "commit": ""},
  {"slug": "b-pending", "goal": "b", "owns": ["b"], "status": "pending", "branch": "", "commit": ""},
  {"slug": "c-dispatched", "goal": "c", "owns": ["c"], "status": "dispatched", "branch": "x--c", "commit": ""},
  {"slug": "d-merged", "goal": "d", "owns": ["d"], "status": "merged", "branch": "", "commit": "abc"},
  {"slug": "e-merged", "goal": "e", "owns": ["e"], "status": "merged", "branch": "", "commit": "def"},
  {"slug": "f-merged", "goal": "f", "owns": ["f"], "status": "merged", "branch": "", "commit": "012"},
  {"slug": "g-failed", "goal": "g", "owns": ["g"], "status": "failed", "branch": "", "commit": ""},
  {"slug": "h-skipped", "goal": "h", "owns": ["h"], "status": "skipped", "branch": "", "commit": ""}
 ],
 "facts": [], "assumptions": [], "blocked": [],
 "replan_count": 2, "stall_count": 1, "is_complete": false}
JSON
}
progress_line() { # progress_line <ts> <step> <slug> <event> <hash>
  printf '{"ts": "%s", "step": "%s", "slug": "%s", "event": "%s", "detail": "d", "state_hash": "%s", "tokens": 0, "cost_usd": 0.0}\n' "$1" "$2" "$3" "$4" "$5"
}

# =============================================================================
# overseer_status.py
# =============================================================================

# --- case: assumption/note/blocked lines after a merge share the merge's hash; they are not a stall.
# last3_same_slug_and_hash looks only at work events (dispatched, failed, timeout, retry, replan). ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
{
  progress_line "$(iso_ago 9)" s001 hello dispatched aaaa
  progress_line "$(iso_ago 8)" s002 hello merged bbbb
  progress_line "$(iso_ago 7)" s003 hello assumption bbbb
  progress_line "$(iso_ago 6)" s004 hello assumption bbbb
  progress_line "$(iso_ago 5)" s005 hello blocked bbbb
} > "$P/docs/ledgers/progress.jsonl"
expect_exit "status: assumption burst exits 0" 0 "$PY" "$STATUS"
J="${OUT//$''/}"
expect_contains "status: assumption burst is not a stall" "false" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"
{
  progress_line "$(iso_ago 9)" s001 x dispatched aaaa
  progress_line "$(iso_ago 8)" s002 x note aaaa
  progress_line "$(iso_ago 7)" s003 x failed aaaa
  progress_line "$(iso_ago 6)" s004 x assumption aaaa
  progress_line "$(iso_ago 5)" s005 x dispatched aaaa
} > "$P/docs/ledgers/progress.jsonl"
expect_exit "status: three work events same slug+hash exits 0" 0 "$PY" "$STATUS"
J="${OUT//$''/}"
expect_contains "status: three work events with same slug+hash is a stall even with notes between" "true" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"

# --- flags are set through the status script (the Write tool is refused on .claude/state paths in unattended sessions) ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
expect_exit "set-flag force_replan exits 0" 0 "$PY" "$STATUS" --set-flag force_replan
expect_contains "set-flag prints confirmation" "flag force_replan set" "$OUT"
[ -f "$P/.claude/state/force_replan" ] && ok "set-flag wrote the force_replan file" || bad "set-flag wrote the force_replan file"
expect_exit "set-flag cancel with reason exits 0" 0 "$PY" "$STATUS" --set-flag cancel --reason "no progress for 50 min"
expect_contains "cancel file holds the reason" "no progress for 50 min" "$(cat "$P/.claude/state/cancel")"
expect_exit "set-flag twice is a no-op" 0 "$PY" "$STATUS" --set-flag cancel --reason "again"
expect_contains "set-flag twice says already set" "already set" "$OUT"
expect_contains "cancel reason unchanged" "no progress for 50 min" "$(cat "$P/.claude/state/cancel")"
expect_exit "set-flag unknown name exits 2" 2 "$PY" "$STATUS" --set-flag other
expect_exit "status after flags exits 0" 0 "$PY" "$STATUS"
J="${OUT//$''/}"
expect_contains "status: flags.cancel true" "true" "$(jget "$J" 'd["flags"]["cancel"]')"

# --- case: full fixtures, last three lines share slug and hash, newest line 50 min old, 5 trailing DENY ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
write_brief "$P"
write_task "$P"
{
  progress_line "$(iso_ago 120)" s001 d-merged dispatched aaaa
  progress_line "$(iso_ago 100)" s002 d-merged merged bbbb
  progress_line "$(iso_ago 90)"  s003 c-dispatched dispatched cccc
  progress_line "$(iso_ago 70)"  s004 c-dispatched failed cccc
  progress_line "$(iso_ago 60)"  s005 c-dispatched dispatched cccc
  progress_line "$(iso_ago 50)"  s006 c-dispatched failed cccc
} > "$P/docs/ledgers/progress.jsonl"
{
  printf '2026-01-01T00:00:00Z\tBash\tls\n'
  printf '2026-01-01T00:00:01Z\tDENY\tBash\tother reason\tgit push --force\n'
  printf '2026-01-01T00:00:02Z\tRead\tREADME.md\n'
  for i in 1 2 3 4 5; do printf '2026-01-01T00:01:0%s\tDENY\tBash\tx\tgit push origin main\n' "$i"; done
} > "$P/.claude/state/hook_log"
touch "$P/.claude/state/cancel"
printf '%s' '{"state": "DONE", "reason": "r", "at": "2026-01-01T00:00:00Z"}' > "$P/docs/RUN_STATE"

expect_exit "status: exit 0 on full fixtures" 0 "$PY" "$STATUS"
J="${OUT//$'\r'/}"
LINES="$(printf '%s\n' "$J" | grep -c .)"
[ "$LINES" = "1" ] && ok "status: prints exactly one line" || bad "status: prints exactly one line (got $LINES)"
expect_contains "status: goal"                "Ship the widget" "$(jget "$J" 'd["goal"]')"
expect_contains "status: tasks.pending=2"     "2" "$(jget "$J" 'd["tasks"]["pending"]')"
expect_contains "status: tasks.dispatched=1"  "1" "$(jget "$J" 'd["tasks"]["dispatched"]')"
expect_contains "status: tasks.merged=3"      "3" "$(jget "$J" 'd["tasks"]["merged"]')"
expect_contains "status: tasks.failed=1"      "1" "$(jget "$J" 'd["tasks"]["failed"]')"
expect_contains "status: tasks.skipped=1"     "1" "$(jget "$J" 'd["tasks"]["skipped"]')"
expect_contains "status: replan_count=2"      "2" "$(jget "$J" 'd["replan_count"]')"
expect_contains "status: replan_limit=5"      "5" "$(jget "$J" 'd["replan_limit"]')"
expect_contains "status: stall_count=1"       "1" "$(jget "$J" 'd["stall_count"]')"
expect_contains "status: stall_limit=3"       "3" "$(jget "$J" 'd["stall_limit"]')"
expect_contains "status: run_state.state"     "DONE" "$(jget "$J" 'd["run_state"]["state"]')"
expect_contains "status: newest_progress_ts is the 50-min line" "s006" "$(jget "$J" '[l["step"] for l in d["progress_tail"] if l["ts"] == d["newest_progress_ts"]][0]')"
AGE_OK="$(jget "$J" '48 <= d["newest_progress_age_min"] <= 52')"
[ "$AGE_OK" = "true" ] && ok "status: newest_progress_age_min ~ 50 (+/-2)" || bad "status: newest_progress_age_min ~ 50 (+/-2): $(jget "$J" 'd["newest_progress_age_min"]')"
expect_contains "status: last3_same_slug_and_hash true" "true" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"
expect_contains "status: repeated_denials.reason x" "x" "$(jget "$J" 'd["repeated_denials"]["reason"]')"
expect_contains "status: repeated_denials.count 5" "5" "$(jget "$J" 'd["repeated_denials"]["count"]')"
expect_contains "status: flags.cancel true"        "true"  "$(jget "$J" 'd["flags"]["cancel"]')"
expect_contains "status: flags.force_replan false" "false" "$(jget "$J" 'd["flags"]["force_replan"]')"
expect_contains "status: progress_tail has 5 entries" "5" "$(jget "$J" 'len(d["progress_tail"])')"
expect_contains "status: progress_tail last step s006" "s006" "$(jget "$J" 'd["progress_tail"][-1]["step"]')"
expect_contains "status: budget is budget.py JSON (has limits)" "400" "$(jget "$J" 'd["budget"]["limits"]["steps"]')"
expect_contains "status: budget.exhausted is a list" "0" "$(jget "$J" 'len(d["budget"]["exhausted"])')"
expect_contains "status: stale progress is warned about" "45 minutes" "$(jget "$J" '" | ".join(d["warnings"])')"

# --- case: last three lines differ, last hook_log line is not a DENY, no flags ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
write_brief "$P"
write_task "$P"
{
  progress_line "$(iso_ago 30)" s001 a-pending dispatched aaaa
  progress_line "$(iso_ago 20)" s002 a-pending merged bbbb
  progress_line "$(iso_ago 1)"  s003 b-pending dispatched cccc
} > "$P/docs/ledgers/progress.jsonl"
{
  for i in 1 2 3 4 5; do printf '2026-01-01T00:01:0%s\tDENY\tBash\tx\tgit push origin main\n' "$i"; done
  printf '2026-01-01T00:02:00Z\tBash\tls\n'
} > "$P/.claude/state/hook_log"
touch "$P/.claude/state/force_replan"
expect_exit "status: exit 0 (differing tail)" 0 "$PY" "$STATUS"
J="${OUT//$'\r'/}"
expect_contains "status: last3_same_slug_and_hash false" "false" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"
expect_contains "status: repeated_denials.count 0 when last line is not DENY" "0" "$(jget "$J" 'd["repeated_denials"]["count"]')"
expect_contains "status: repeated_denials.reason null when last line is not DENY" "null" "$(jget "$J" 'd["repeated_denials"]["reason"]')"
expect_contains "status: flags.cancel false"      "false" "$(jget "$J" 'd["flags"]["cancel"]')"
expect_contains "status: flags.force_replan true" "true"  "$(jget "$J" 'd["flags"]["force_replan"]')"
expect_contains "status: run_state null when RUN_STATE absent" "null" "$(jget "$J" 'd["run_state"]')"
AGE_OK="$(jget "$J" '0 <= d["newest_progress_age_min"] <= 3')"
[ "$AGE_OK" = "true" ] && ok "status: newest_progress_age_min ~ 1" || bad "status: newest_progress_age_min ~ 1: $(jget "$J" 'd["newest_progress_age_min"]')"
expect_contains "status: warnings empty when everything is present and fresh" "0" "$(jget "$J" 'len(d["warnings"])')"

# --- case: same slug, different hash in last three -> false ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
write_brief "$P"
write_task "$P"
{
  progress_line "$(iso_ago 3)" s001 a-pending dispatched aaaa
  progress_line "$(iso_ago 2)" s002 a-pending failed aaaa
  progress_line "$(iso_ago 1)" s003 a-pending dispatched bbbb
} > "$P/docs/ledgers/progress.jsonl"
expect_exit "status: exit 0 (same slug, different hash)" 0 "$PY" "$STATUS"
J="${OUT//$'\r'/}"
expect_contains "status: last3 same slug but different hash -> false" "false" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"

# --- case: missing ledgers, brief, hook_log -> exit 0, nulls/zeros, warnings non-empty ---
P="$(status_project)"
export CLAUDE_PROJECT_DIR="$P"
expect_exit "status: exit 0 with nothing on disk" 0 "$PY" "$STATUS"
J="${OUT//$'\r'/}"
expect_contains "status: goal null"               "null" "$(jget "$J" 'd["goal"]')"
expect_contains "status: tasks all zero"          "0"    "$(jget "$J" 'sum(d["tasks"].values())')"
expect_contains "status: replan_count 0"          "0"    "$(jget "$J" 'd["replan_count"]')"
expect_contains "status: newest_progress_ts null" "null" "$(jget "$J" 'd["newest_progress_ts"]')"
expect_contains "status: newest_progress_age_min null" "null" "$(jget "$J" 'd["newest_progress_age_min"]')"
expect_contains "status: last3 false"             "false" "$(jget "$J" 'd["last3_same_slug_and_hash"]')"
expect_contains "status: repeated_denials.count 0" "0"   "$(jget "$J" 'd["repeated_denials"]["count"]')"
expect_contains "status: budget null without brief" "null" "$(jget "$J" 'd["budget"]')"
expect_contains "status: progress_tail empty"     "0"    "$(jget "$J" 'len(d["progress_tail"])')"
WARN_OK="$(jget "$J" 'len(d["warnings"]) > 0')"
[ "$WARN_OK" = "true" ] && ok "status: warnings non-empty when files are missing" || bad "status: warnings non-empty when files are missing"
expect_contains "status: warning names task.json"       "task.json"       "$(jget "$J" '" | ".join(d["warnings"])')"
expect_contains "status: warning names progress.jsonl"  "progress.jsonl"  "$(jget "$J" '" | ".join(d["warnings"])')"
expect_contains "status: warning names budget"          "budget"          "$(jget "$J" '" | ".join(d["warnings"])')"

# --- case: --help exits 0 ---
expect_exit "status: --help exits 0" 0 "$PY" "$STATUS" --help

# =============================================================================
# overseer.sh against a stub claude
# =============================================================================

CTL="$(tmpdir)"                 # control dir: stub argv/result, never read by overseer.sh
ARGV_FILE="$CTL/argv.log"
RESULT_FILE="$CTL/result.json"
ONCE_MARKER="$CTL/once_marker"
RUN_STATE_PATH_FOR_STUB="$CTL/run_state_path"   # written by tests that need the stub to flip RUN_STATE

STUB_DIR="$(tmpdir)"
cat > "$STUB_DIR/claude" <<'STUB'
#!/usr/bin/env bash
{
  echo "CALL_START"
  printf '%s\n' "$@"
  echo "ENV QS_ROLE=${QS_ROLE:-}"
  echo "CALL_END"
} >> "$CLAUDE_STUB_ARGV_FILE"

if [ -n "${CLAUDE_STUB_WRITE_RUN_STATE:-}" ] && [ ! -f "$CLAUDE_STUB_ONCE_MARKER" ]; then
  touch "$CLAUDE_STUB_ONCE_MARKER"
  target="$(cat "$CLAUDE_STUB_RUN_STATE_PATH_FILE")"
  mkdir -p "$(dirname "$target")"
  printf '%s' "$CLAUDE_STUB_WRITE_RUN_STATE" > "$target"
fi

cat "$CLAUDE_STUB_RESULT_FILE"
STUB
chmod +x "$STUB_DIR/claude"
export PATH="$STUB_DIR:$PATH"
export CLAUDE_STUB_ARGV_FILE="$ARGV_FILE"
export CLAUDE_STUB_RESULT_FILE="$RESULT_FILE"
export CLAUDE_STUB_ONCE_MARKER="$ONCE_MARKER"
export CLAUDE_STUB_RUN_STATE_PATH_FILE="$RUN_STATE_PATH_FOR_STUB"
unset CLAUDE_STUB_WRITE_RUN_STATE

# Fresh fake project root: a copy of this repo's overseer agent + scripts,
# never the repo's own .claude/state.
new_project() {
  local p; p="$(tmpdir)"
  mkdir -p "$p/.claude/agents" "$p/.claude/state" "$p/docs" "$p/docs/ledgers"
  cp "$ROOT/.claude/agents/overseer.md" "$p/.claude/agents/overseer.md"
  cp -r "$ROOT/scripts" "$p/scripts"
  printf '%s' "$p"
}

call_count() { grep -c '^CALL_START$' "$ARGV_FILE" 2>/dev/null || true; }
reset_argv() { : > "$ARGV_FILE"; }

printf '%s' '{"is_error":false,"result":"ok","session_id":"x"}' > "$RESULT_FILE"

# --- case: no brief.json -> exit 0, stub not called ---
PROJECT="$(new_project)"
export CLAUDE_PROJECT_DIR="$PROJECT"
reset_argv
expect_exit "no brief.json -> exit 0" 0 bash "$PROJECT/scripts/overseer.sh"
[ "$(call_count)" = "0" ] && ok "no brief.json -> stub not called" || bad "no brief.json -> stub not called (called $(call_count) times)"

# --- case: brief.json present, RUN_STATE DONE -> exit 0, stub not called ---
PROJECT="$(new_project)"
export CLAUDE_PROJECT_DIR="$PROJECT"
printf '{}' > "$PROJECT/.claude/state/brief.json"
printf '%s' '{"state": "DONE", "reason": "mission complete", "at": "2026-01-01T00:00:00Z"}' > "$PROJECT/docs/RUN_STATE"
reset_argv
expect_exit "RUN_STATE DONE -> exit 0" 0 bash "$PROJECT/scripts/overseer.sh"
[ "$(call_count)" = "0" ] && ok "RUN_STATE DONE -> stub not called" || bad "RUN_STATE DONE -> stub not called (called $(call_count) times)"

# --- case: brief.json present, no RUN_STATE -> exit 0, stub called once with the contracted flags ---
PROJECT="$(new_project)"
export CLAUDE_PROJECT_DIR="$PROJECT"
printf '{}' > "$PROJECT/.claude/state/brief.json"
reset_argv
expect_exit "brief present, no RUN_STATE -> exit 0" 0 bash "$PROJECT/scripts/overseer.sh"
[ "$(call_count)" = "1" ] && ok "brief present, no RUN_STATE -> stub called once" || bad "brief present, no RUN_STATE -> stub called once (called $(call_count) times)"
ARGV="$(cat "$ARGV_FILE")"
expect_contains "argv has -p" "$(printf '\n-p\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --max-turns" "--max-turns" "$ARGV"
expect_contains "argv has 8" "$(printf '\n8\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --permission-mode acceptEdits" "$(printf '\n--permission-mode\nacceptEdits\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --permission-prompts" "--permission-prompts" "$ARGV"
expect_contains "argv has none" "$(printf '\nnone\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --output-format json" "$(printf '\n--output-format\njson\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --strict-mcp-config" "$(printf '\n--strict-mcp-config\n')" "$(printf '\n%s\n' "$ARGV")"
expect_contains "argv has --mcp-config with an empty server map" "$(printf '\n--mcp-config\n{"mcpServers":{}}\n')" "$(printf '\n%s\n' "$ARGV")"
# The --allowedTools value is the argv entry right after the flag.
ALLOWED_TOOLS_LINE="$(awk '/^--allowedTools$/{getline; print; exit}' "$ARGV_FILE")"
expect_contains "allowedTools contains Write" "Write" "$ALLOWED_TOOLS_LINE"
expect_contains "allowedTools contains Edit" "Edit" "$ALLOWED_TOOLS_LINE"
expect_contains "allowedTools contains Read" "Read" "$ALLOWED_TOOLS_LINE"
expect_contains "allowedTools contains Bash(python3 scripts/overseer_status.py *)" "Bash(python3 scripts/overseer_status.py *)" "$ALLOWED_TOOLS_LINE"
expect_contains "allowedTools contains Bash(python scripts/overseer_status.py *)" "Bash(python scripts/overseer_status.py *)" "$ALLOWED_TOOLS_LINE"
expect_contains "allowedTools contains Bash(python3 scripts/budget.py *)" "Bash(python3 scripts/budget.py *)" "$ALLOWED_TOOLS_LINE"
case "$ALLOWED_TOOLS_LINE" in
  *git*) bad "allowedTools must not contain 'git'" ;;
  *) ok "allowedTools does not contain 'git'" ;;
esac
case "$ALLOWED_TOOLS_LINE" in
  *"Bash(tail"*) bad "allowedTools must not contain 'Bash(tail' (the status script replaces it)" ;;
  *) ok "allowedTools does not contain 'Bash(tail'" ;;
esac
PROMPT_ARG="$(awk '/^-p$/{f=1; next} /^--max-turns$/{f=0} f' "$ARGV_FILE")"
expect_contains "prompt argument contains overseer_status.py" "overseer_status.py" "$PROMPT_ARG"
expect_contains "prompt argument contains force_replan" "force_replan" "$PROMPT_ARG"
expect_contains "prompt argument contains cancel" "cancel" "$PROMPT_ARG"
expect_contains "prompt argument contains docs/overseer.md" "docs/overseer.md" "$PROMPT_ARG"
case "$PROMPT_ARG" in
  *"|| python"*) bad "prompt must not suggest a chained fallback ('|| python')" ;;
  *) ok "prompt does not suggest a chained fallback ('|| python')" ;;
esac
case "$PROMPT_ARG" in
  *"python3 scripts/overseer_status.py"*) ok "prompt names the exact status command" ;;
  *) bad "prompt names the exact status command" ;;
esac
PROMPT_FIRST_LINE="$(awk '/^-p$/{getline; print; exit}' "$ARGV_FILE")"
case "$PROMPT_FIRST_LINE" in
  -*) bad "prompt argument must not start with '-' (got: $PROMPT_FIRST_LINE)" ;;
  *) ok "prompt argument must not start with '-'" ;;
esac
case "$ARGV" in
  *"name: overseer"*) bad "prompt argument must not contain frontmatter line 'name: overseer'" ;;
  *) ok "prompt argument must not contain frontmatter line 'name: overseer'" ;;
esac
expect_contains "overseer session carries QS_ROLE=overseer (hooks exempt it)" "ENV QS_ROLE=overseer" "$ARGV"
[ -f "$PROJECT/.claude/state/overseer_last.json" ] && ok "overseer_last.json written" || bad "overseer_last.json written"
LOG_LINES="$(wc -l < "$PROJECT/.claude/state/overseer.log" 2>/dev/null || echo 0)"
[ "$LOG_LINES" = "1" ] && ok "overseer.log has one line" || bad "overseer.log has one line (got $LOG_LINES)"

# --- agent file: frontmatter tools include Write, body has no chained commands ---
AGENT="$(cat "$ROOT/.claude/agents/overseer.md")"
TOOLS_LINE="$(grep -m1 '^tools:' "$ROOT/.claude/agents/overseer.md")"
expect_contains "overseer.md frontmatter tools has Write" "Write" "$TOOLS_LINE"
expect_contains "overseer.md frontmatter tools has Bash" "Bash" "$TOOLS_LINE"
expect_contains "overseer.md frontmatter model sonnet" "model: sonnet" "$AGENT"
expect_contains "overseer.md names the status script" "python3 scripts/overseer_status.py" "$AGENT"
expect_contains "overseer.md mentions last3_same_slug_and_hash" "last3_same_slug_and_hash" "$AGENT"
expect_contains "overseer.md sets flags with --set-flag" "--set-flag cancel" "$AGENT"
expect_contains "overseer.md sets force_replan with --set-flag" "--set-flag force_replan" "$AGENT"
expect_contains "overseer.md mentions repeated_denials" "repeated_denials" "$AGENT"

# --- case: stub reports is_error:true -> exit 1 ---
PROJECT="$(new_project)"
export CLAUDE_PROJECT_DIR="$PROJECT"
printf '{}' > "$PROJECT/.claude/state/brief.json"
printf '%s' '{"is_error":true}' > "$RESULT_FILE"
reset_argv
expect_exit "is_error:true -> exit 1" 1 bash "$PROJECT/scripts/overseer.sh"
printf '%s' '{"is_error":false,"result":"ok","session_id":"x"}' > "$RESULT_FILE"

# --- case: --loop 1 with a stub that flips RUN_STATE terminal on its first call -> returns after one tick ---
PROJECT="$(new_project)"
export CLAUDE_PROJECT_DIR="$PROJECT"
printf '{}' > "$PROJECT/.claude/state/brief.json"
printf '%s' "$PROJECT/docs/RUN_STATE" > "$RUN_STATE_PATH_FOR_STUB"
rm -f "$ONCE_MARKER"
export CLAUDE_STUB_WRITE_RUN_STATE='{"state": "DONE", "reason": "stub finished", "at": "2026-01-01T00:00:00Z"}'
reset_argv
expect_exit "--loop 1 returns after one tick" 0 timeout 30 bash "$PROJECT/scripts/overseer.sh" --loop 1
[ "$(call_count)" = "1" ] && ok "--loop 1 -> stub called exactly once" || bad "--loop 1 -> stub called exactly once (called $(call_count) times)"
unset CLAUDE_STUB_WRITE_RUN_STATE

finish
