#!/usr/bin/env bash
# Idea -> PRD + missions. The front stage before scripts/program.sh: one paragraph in IDEA.md becomes docs/PRD.md and
# 2 to 5 chained mission briefs in docs/missions/NN-<kebab>.yaml, written by the strategist agent in a headless session.
#   usage: bash scripts/idea.sh [idea-file]        (default IDEA.md; see IDEA.example.md)
#   runs `claude -p --agent strategist` with QS_ROLE=strategist (the Stop hook skips the gate; the guard still applies),
#     the same safety flags as run.sh, and an allow list of file tools plus `brief.py check` only
#   validates every mission with `brief.py check`; on failure re-runs the strategist once with the errors in the prompt
# Exit: 0 PRD and valid missions written, 2 idea missing/empty, missions already present, or still invalid after the retry.
set -u
here="$PWD"
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 2
export CLAUDE_PROJECT_DIR="$PWD"
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
S=.claude/state; M=docs/missions; PRD=docs/PRD.md

idea="${1:-IDEA.md}"
case "$idea" in /*|[A-Za-z]:[/\\]*) ;; *) [ -f "$here/$idea" ] && idea="$here/$idea";; esac
if [ ! -f "$idea" ] || ! grep -q '[^[:space:]]' "$idea"; then
  echo "idea: ${1:-IDEA.md} is missing or empty; copy IDEA.example.md to IDEA.md and describe the idea in a paragraph" >&2
  exit 2
fi
shopt -s nullglob
old=("$M"/*.yaml)
if [ ${#old[@]} -gt 0 ]; then
  echo "idea: $M already holds ${#old[@]} mission file(s); move or delete them so a new idea's missions are not mixed with old ones" >&2
  exit 2
fi
mkdir -p "$S" "$M"
idea_text="$(cat "$idea")"; idea_text="${idea_text//$'\r'/}"

# --agent runs the session as .claude/agents/strategist.md (its prompt, tools and model). The allow list is passed
# explicitly because a never-trusted folder ignores project allow rules in -p mode; MCP is off, as in run.sh.
strategist() { # strategist <prompt>
  QS_ROLE=strategist claude -p "$1" --agent strategist \
    --max-turns 60 \
    --permission-mode acceptEdits \
    --permission-prompts none \
    --allowedTools "Read,Glob,Grep,Write,Edit,Bash(python3 scripts/brief.py check *),Bash(python scripts/brief.py check *)" \
    --mcp-config '{"mcpServers":{}}' \
    --strict-mcp-config \
    --output-format json > "$S/idea_last.json"
}
# check: every mission parses and validates, none sets mission.base, and the PRD exists. Errors go to $errs.
errs=""
check() {
  local files=("$M"/*.yaml) out f
  [ ${#files[@]} -gt 0 ] || { errs="no mission files in $M/"; return 1; }
  out="$("$PY" scripts/brief.py check "${files[@]}" 2>&1)" || { errs="${out//$'\r'/}"; return 1; }
  for f in "${files[@]}"; do
    "$PY" -c 'import re, sys; sys.exit(1 if re.search(r"^  base:", open(sys.argv[1], encoding="utf-8").read(), re.M) else 0)' "$f" \
      || { errs="brief: $f: mission.base must not be set (scripts/program.sh sets it when it chains the missions)"; return 1; }
  done
  [ -s "$PRD" ] || { errs="$PRD is missing or empty"; return 1; }
  return 0
}

prompt="Turn the idea below into $PRD and 2 to 5 mission briefs in $M/, following your instructions exactly, then validate them with brief.py check. Nobody is reading; never ask a question.

Idea (from ${1:-IDEA.md}):
$idea_text"
strategist "$prompt" || echo "idea: strategist session exited non-zero; checking what it wrote" >&2
if ! check; then
  echo "idea: first attempt invalid, retrying once:" >&2; printf '%s\n' "$errs" >&2
  strategist "$prompt

The previous attempt left these errors. Fix them (Edit the files named; write any missing file) and run brief.py check again until it passes:
$errs" || echo "idea: strategist session exited non-zero; checking what it wrote" >&2
  if ! check; then
    echo "idea: missions still invalid after one retry:" >&2; printf '%s\n' "$errs" >&2
    exit 2
  fi
fi

echo "idea: PRD: $PRD"
echo "idea: missions, in order:"
for f in "$M"/*.yaml; do echo "  $f"; done
echo "next: bash scripts/program.sh"
