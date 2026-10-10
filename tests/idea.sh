#!/usr/bin/env bash
# scripts/idea.sh: IDEA.md -> docs/PRD.md + docs/missions/NN-*.yaml via the strategist, run headlessly against a stub
# `claude` first on PATH (no model is ever called). The script must refuse a missing or empty idea, pass the same
# safety flags as run.sh, validate the missions with brief.py check, retry once with the errors, and give up after that.
source "$(dirname "$0")/lib.sh"
CTL="$(tmpdir)"   # stub control files, outside every test repo
mkdir -p "$CTL/bin"; cat > "$CTL/bin/claude" <<'EOF'
#!/usr/bin/env bash
n="$(cat "$STUB_CTL/count" 2>/dev/null || echo 0)"; n=$((n + 1)); echo "$n" > "$STUB_CTL/count"
{ echo "CALL_START"; printf '%s\n' "$@"; echo "ENV QS_ROLE=${QS_ROLE:-}"; echo "CALL_END"; } >> "$STUB_CTL/argv.log"
mode="${STUB_MODE:-valid}"
[ "$mode" = flip ] && { [ "$n" -ge 2 ] && mode=valid || mode=invalid; }
case "$mode" in
  valid)   mkdir -p docs/missions; printf '# PRD\n\nA tool.\n' > docs/PRD.md; cp "${STUB_SRC:-BRIEF.example.yaml}" docs/missions/01-skeleton.yaml;;
  invalid) mkdir -p docs/missions; printf '# PRD\n' > docs/PRD.md; printf 'mission:\n  deliverables:\n    - x\n' > docs/missions/01-skeleton.yaml;;
  nothing) ;;
esac
printf '{"is_error":false,"session_id":"idea-1","result":"ok"}'
EOF
chmod +x "$CTL/bin/claude"
mk() { # fresh git repo with the harness scripts, agents and example brief; prints its path
  local d; d="$(tmpdir)"; git init -q -b main "$d"
  cp -r "$ROOT/scripts" "$ROOT/.claude" "$ROOT/BRIEF.example.yaml" "$d/" 2>/dev/null; rm -rf "$d/.claude/worktrees" "$d/.claude/state"
  printf '# Idea\n\nA CLI that turns a folder of receipts into a monthly expense CSV.\n' > "$d/IDEA.md"
  echo "$d"
}
idea() { # idea <repo> [mode] [args...]
  local d=$1 mode=${2:-valid}; shift 2 2>/dev/null || shift $#
  : > "$CTL/argv.log"; rm -f "$CTL/count"
  (cd "$d" && PATH="$CTL/bin:$PATH" STUB_CTL="$CTL" STUB_MODE="$mode" bash scripts/idea.sh "$@")
}
calls() { cat "$CTL/count" 2>/dev/null || echo 0; }

# --- missing or empty idea file: exit 2, the model is never called ---
d="$(mk)"; rm "$d/IDEA.md"
expect_exit "missing IDEA.md: exit 2" 2 idea "$d" valid
expect_contains "missing IDEA.md: message names the file" "IDEA.md" "$OUT"
[ "$(calls)" = 0 ] && ok "missing IDEA.md: claude not called" || bad "missing IDEA.md: claude called $(calls) times"
printf '  \n\n' > "$d/IDEA.md"
expect_exit "blank IDEA.md: exit 2" 2 idea "$d" valid
[ "$(calls)" = 0 ] && ok "blank IDEA.md: claude not called" || bad "blank IDEA.md: claude called $(calls) times"
expect_exit "named idea file missing: exit 2" 2 idea "$d" valid notes/other-idea.md
expect_contains "named idea file missing: message names it" "other-idea.md" "$OUT"

# --- happy path: PRD + one valid mission -> exit 0, the plan is printed ---
d="$(mk)"
expect_exit "valid missions: exit 0" 0 idea "$d" valid
expect_contains "lists the PRD" "docs/PRD.md" "$OUT"
expect_contains "lists the mission" "docs/missions/01-skeleton.yaml" "$OUT"
expect_contains "prints the next command" "bash scripts/program.sh" "$OUT"
[ "$(calls)" = 1 ] && ok "valid missions: claude called once" || bad "valid missions: claude called $(calls) times"
A="$(cat "$CTL/argv.log")"
expect_contains "runs claude -p" "-p" "$A"
expect_contains "passes --permission-prompts none" "--permission-prompts" "$A"
expect_contains "drops the user's MCP servers" "--strict-mcp-config" "$A"
expect_contains "empty MCP config" '{"mcpServers":{}}' "$A"
expect_contains "json output" "--output-format" "$A"
expect_contains "caps the turns" "--max-turns" "$A"
expect_contains "allows brief.py check" "Bash(python3 scripts/brief.py check *)" "$A"
expect_contains "allows brief.py check via python" "Bash(python scripts/brief.py check *)" "$A"
expect_contains "selects the strategist agent" "strategist" "$A"
expect_contains "puts the idea text in the prompt" "monthly expense CSV" "$A"
expect_contains "session carries QS_ROLE=strategist" "ENV QS_ROLE=strategist" "$A"
expect_contains "no blanket Bash rule" "" "$(grep -c -x -- 'Read,Glob,Grep,Write,Edit,Bash' "$CTL/argv.log" | sed 's/^0$//')"
expect_contains "never bypasses permissions" "" "$(grep -c -- 'bypassPermissions\|dangerously' "$CTL/argv.log" | sed 's/^0$//')"

# --- a named idea file is used instead of IDEA.md ---
d="$(mk)"; rm "$d/IDEA.md"; mkdir -p "$d/notes"; printf 'A habit tracker for climbers.\n' > "$d/notes/climb.md"
expect_exit "named idea file: exit 0" 0 idea "$d" valid notes/climb.md
expect_contains "named idea file: its text reaches the prompt" "habit tracker for climbers" "$(cat "$CTL/argv.log")"

# --- invalid every time: one retry with the errors, then exit 2 ---
d="$(mk)"
expect_exit "invalid twice: exit 2" 2 idea "$d" invalid
[ "$(calls)" = 2 ] && ok "invalid twice: claude called exactly twice" || bad "invalid twice: claude called $(calls) times"
expect_contains "invalid twice: retry prompt carries the check error" "mission.goal" "$(sed -n '/^CALL_START$/,/^CALL_END$/p' "$CTL/argv.log" | awk '/^CALL_START$/{n++} n==2')"
expect_contains "invalid twice: failure names the bad key" "mission.goal" "$OUT"

# --- invalid first, valid on the retry: exit 0 ---
d="$(mk)"
expect_exit "invalid then valid: exit 0" 0 idea "$d" flip
[ "$(calls)" = 2 ] && ok "invalid then valid: claude called twice" || bad "invalid then valid: claude called $(calls) times"
expect_contains "invalid then valid: lists the mission" "docs/missions/01-skeleton.yaml" "$OUT"

# --- no mission files at all: retried, then exit 2 ---
d="$(mk)"
expect_exit "no missions written: exit 2" 2 idea "$d" nothing
[ "$(calls)" = 2 ] && ok "no missions written: claude called twice" || bad "no missions written: claude called $(calls) times"

# --- a mission that sets mission.base is rejected (scripts/program.sh owns it) ---
d="$(mk)"; sed 's/^  goal: /  base: "main"\n  goal: /' "$d/BRIEF.example.yaml" > "$CTL/based.yaml"
expect_exit "mission.base set: brief.py check alone accepts it" 0 "$QS_PYTHON" "$ROOT/scripts/brief.py" check "$CTL/based.yaml"
export STUB_SRC="$CTL/based.yaml"
expect_exit "mission.base set: exit 2" 2 idea "$d" valid
expect_contains "mission.base set: says why" "mission.base" "$OUT"
unset STUB_SRC

# --- existing missions are never mixed with a new idea's: refuse before calling the model ---
d="$(mk)"; mkdir -p "$d/docs/missions"; cp "$d/BRIEF.example.yaml" "$d/docs/missions/01-old.yaml"
expect_exit "existing missions: exit 2" 2 idea "$d" valid
expect_contains "existing missions: names the folder" "docs/missions" "$OUT"
[ "$(calls)" = 0 ] && ok "existing missions: claude not called" || bad "existing missions: claude called $(calls) times"

# --- the strategist agent definition ---
P="$(cat "$ROOT/.claude/agents/strategist.md")"
expect_contains "strategist: named" "name: strategist" "$P"
expect_contains "strategist: opus" "model: opus" "$P"
expect_contains "strategist: no web tools" "disallowedTools: WebFetch, WebSearch" "$P"
expect_contains "strategist: writes the PRD" "docs/PRD.md" "$P"
expect_contains "strategist: writes missions" "docs/missions/" "$P"
expect_contains "strategist: stable requirement IDs" "REQ-001" "$P"
expect_contains "strategist: missions carry requirements" '`requirements` list' "$P"
expect_contains "strategist: PR size target" "500 reviewable human-written changed lines" "$P"
expect_contains "strategist: validates with brief.py check" "scripts/brief.py check" "$P"
expect_contains "strategist: never sets mission.base" "mission.base" "$P"
expect_contains "strategist: one command per Bash call" "One command per Bash call" "$P"
expect_contains "strategist: never asks" "never ask" "$P"
[ -s "$ROOT/IDEA.example.md" ] && ok "IDEA.example.md exists" || bad "IDEA.example.md missing"
expect_contains "IDEA.example.md: constraints section" "Constraints" "$(cat "$ROOT/IDEA.example.md" 2>/dev/null)"
finish
