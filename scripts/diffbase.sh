#!/usr/bin/env bash
# Prints the ref to diff a branch against. Used by the reviewer and the security reviewer so they never PASS on an
# empty diff when there is no remote, and never review an earlier mission's work again:
#   the brief's mission.base (a chained mission, see scripts/program.sh): origin/<base>, else <base>
#   otherwise origin/main, origin/master, main, master, else the root commit
brief="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}/.claude/state/brief.json"
base="$(grep -o '"base": *"[^"]*"' "$brief" 2>/dev/null | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/')"
for ref in ${base:+"origin/$base" "$base"} origin/main origin/master main master; do
  if git rev-parse --verify -q "$ref" >/dev/null 2>&1; then echo "$ref"; exit 0; fi
done
git rev-list --max-parents=0 HEAD 2>/dev/null | tail -n 1
