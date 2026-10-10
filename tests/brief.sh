#!/usr/bin/env bash
# scripts/brief.py: BRIEF.yaml -> .claude/state/brief.json (validate) and show.
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"; mkdir -p "$CLAUDE_PROJECT_DIR/.claude/state"
BRIEF="$ROOT/BRIEF.example.yaml"
SJSON="$CLAUDE_PROJECT_DIR/.claude/state/brief.json"
STARTED="$CLAUDE_PROJECT_DIR/.claude/state/started_at"

py() { "$QS_PYTHON" "$ROOT/scripts/brief.py" "$@"; }

# --- example validates ---
expect_exit "example validates" 0 py validate --brief "$BRIEF"
expect_exit "brief.json written" 0 test -f "$SJSON"
expect_exit "started_at written" 0 test -f "$STARTED"

brief_content="$(cat "$SJSON")"
expect_contains "brief.json has goal" "Add a README" "$brief_content"
expect_contains "brief.json has requirements array" '"requirements": [' "$brief_content"
expect_contains "brief.json keeps REQ-001" '"REQ-001"' "$brief_content"

# --- optional mission.requirements: stable REQ identifiers only ---
NOREQ="$CLAUDE_PROJECT_DIR/no-requirements.yaml"
sed '/^  requirements:/,+1d' "$BRIEF" > "$NOREQ"
expect_exit "requirements omitted: validates" 0 py check "$NOREQ"
BADREQ="$CLAUDE_PROJECT_DIR/bad-requirements.yaml"
sed 's/REQ-001/US-001/' "$BRIEF" > "$BADREQ"
expect_exit "bad requirement id -> exit 2" 2 py check "$BADREQ"
expect_contains "bad requirement error names mission.requirements" "mission.requirements" "$OUT"

ncrit="$(grep -o '"kind":' "$SJSON" | wc -l | tr -d ' ')"
expect_contains "brief.json has 4 criteria" "4" "$ncrit"

expect_contains "budgets.stall_limit=3" '"stall_limit": 3' "$brief_content"

# --- started_at not overwritten on second validate ---
first_started="$(cat "$STARTED")"
sleep 1
expect_exit "second validate ok" 0 py validate --brief "$BRIEF"
second_started="$(cat "$STARTED")"
expect_contains "started_at unchanged" "$first_started" "$second_started"

# --- show prints the goal ---
expect_exit "show runs" 0 py show
expect_contains "show prints goal" "Add a README" "$OUT"

# --- missing budgets.steps -> exit 2, stderr names it ---
BAD1="$CLAUDE_PROJECT_DIR/bad1.yaml"
sed '/^  steps:/d' "$BRIEF" > "$BAD1"
expect_exit "missing budgets.steps -> exit 2" 2 py validate --brief "$BAD1"
expect_contains "stderr names budgets.steps" "budgets.steps" "$OUT"

# --- budgets.tokens: 0 -> exit 2 ---
BAD2="$CLAUDE_PROJECT_DIR/bad2.yaml"
sed 's/^  tokens: .*/  tokens: 0/' "$BRIEF" > "$BAD2"
expect_exit "budgets.tokens=0 -> exit 2" 2 py validate --brief "$BAD2"
expect_contains "stderr names budgets.tokens" "budgets.tokens" "$OUT"

# --- unknown kind -> exit 2 ---
BAD3="$CLAUDE_PROJECT_DIR/bad3.yaml"
sed 's/{kind: test, cmd: "bash tests\/run.sh", expect: 0}/{kind: bogus, cmd: "x", expect: 0}/' "$BRIEF" > "$BAD3"
expect_exit "unknown kind -> exit 2" 2 py validate --brief "$BAD3"

# --- built-in reader forced via QS_NO_YAML=1 matches PyYAML path (when both available) ---
rm -f "$SJSON" "$STARTED"
expect_exit "validate with PyYAML/default reader" 0 py validate --brief "$BRIEF"
cp "$SJSON" "$CLAUDE_PROJECT_DIR/brief_default.json"

rm -f "$SJSON" "$STARTED"
expect_exit "validate with QS_NO_YAML=1 (built-in reader)" 0 env QS_NO_YAML=1 "$QS_PYTHON" "$ROOT/scripts/brief.py" validate --brief "$BRIEF"
cp "$SJSON" "$CLAUDE_PROJECT_DIR/brief_builtin.json"

d1="$(cat "$CLAUDE_PROJECT_DIR/brief_default.json")"
d2="$(cat "$CLAUDE_PROJECT_DIR/brief_builtin.json")"
d1="${d1//$'\r'/}"; d2="${d2//$'\r'/}"
if [ "$d1" = "$d2" ]; then ok "built-in reader output matches default reader"; else bad "built-in reader output differs from default reader"; fi

# --- built-in reader: trailing comments are dropped, a # inside quotes is kept ---
CMT="$CLAUDE_PROJECT_DIR/comments.yaml"
sed -e 's/^  steps: .*/  steps: 400   # every tool call counts/' \
    -e 's/^  cost_usd: .*/  cost_usd: 40 #no space before text/' \
    -e 's/^  goal: .*/  goal: "Ship issue #7 to prod"/' "$BRIEF" > "$CMT"
rm -f "$SJSON" "$STARTED"
expect_exit "built-in reader: trailing comment after a number" 0 env QS_NO_YAML=1 "$QS_PYTHON" "$ROOT/scripts/brief.py" validate --brief "$CMT"
c="$(cat "$SJSON" 2>/dev/null)"
expect_contains "built-in reader: steps parsed as 400" '"steps": 400' "$c"
expect_contains "built-in reader: cost_usd parsed as 40" '"cost_usd": 40' "$c"
expect_contains "built-in reader: # inside quotes kept" 'issue #7' "$c"

# --- a new goal is a new mission: validate resets the gitignored runtime state so a fresh run does not inherit
# the previous run's clock, step count, session id or flags (seen when the tree was switched back to main) ---
rm -f "$SJSON" "$STARTED"
expect_exit "runtime reset: first validate" 0 py validate --brief "$BRIEF"
printf '2020-01-01T00:00:00Z
' > "$STARTED"; echo 57 > "$CLAUDE_PROJECT_DIR/.claude/state/steps"; printf 'old-sess' > "$CLAUDE_PROJECT_DIR/.claude/state/session_id"
mkdir -p "$CLAUDE_PROJECT_DIR/.claude/state/legs"; echo outbound > "$CLAUDE_PROJECT_DIR/.claude/state/legs/s001"; touch "$CLAUDE_PROJECT_DIR/.claude/state/cancel"
expect_exit "runtime reset: same goal keeps state" 0 py validate --brief "$BRIEF"
expect_contains "runtime reset: same goal keeps started_at" "2020-01-01" "$(cat "$STARTED")"
[ -f "$CLAUDE_PROJECT_DIR/.claude/state/session_id" ] && ok "runtime reset: same goal keeps session_id" || bad "runtime reset: same goal keeps session_id"
NEWG="$CLAUDE_PROJECT_DIR/newgoal.yaml"; sed 's/^  goal: .*/  goal: "A different mission"/' "$BRIEF" > "$NEWG"
expect_exit "runtime reset: new goal validates" 0 py validate --brief "$NEWG"
expect_contains "runtime reset: new goal says so" "runtime state reset" "$OUT"
expect_contains "runtime reset: started_at is fresh" "$(date -u +%Y-%m-%d)" "$(cat "$STARTED")"
for f in steps session_id cancel legs; do [ ! -e "$CLAUDE_PROJECT_DIR/.claude/state/$f" ] && ok "runtime reset: $f removed" || bad "runtime reset: $f removed"; done
expect_contains "runtime reset: brief.json has the new goal" "A different mission" "$(cat "$SJSON")"

# --- check: validates any brief file without touching run state (used by idea.sh on generated mission briefs) ---
rm -f "$SJSON"
expect_exit "check: example passes" 0 py check "$BRIEF"
expect_contains "check: names the file" "ok" "$OUT"
expect_exit "check: writes no brief.json" 1 test -f "$SJSON"
expect_exit "check: bad brief -> exit 2" 2 py check "$BAD1"
expect_contains "check: names the bad key" "budgets.steps" "$OUT"
expect_exit "check: several files, one bad -> exit 2" 2 py check "$BRIEF" "$BAD1"

# --- optional mission.base: the branch a chained mission builds on and opens its PR against ---
BASED="$CLAUDE_PROJECT_DIR/based.yaml"
awk '{print} /^  goal:/{print "  base: \"mission/first-mission\""}' "$BRIEF" > "$BASED"
expect_exit "base: validates" 0 py validate --brief "$BASED"
expect_contains "base: kept in brief.json" '"base": "mission/first-mission"' "$(cat "$SJSON")"
expect_exit "no base: validates" 0 py validate --brief "$BRIEF"
[[ "$(cat "$SJSON")" == *'"base"'* ]] && bad "no base: brief.json has no base key" || ok "no base: brief.json has no base key"
BADBASE="$CLAUDE_PROJECT_DIR/badbase.yaml"
awk '{print} /^  goal:/{print "  base: \"bad branch name\""}' "$BRIEF" > "$BADBASE"
expect_exit "base with a space -> exit 2" 2 py check "$BADBASE"
expect_contains "base error names mission.base" "mission.base" "$OUT"
finish
