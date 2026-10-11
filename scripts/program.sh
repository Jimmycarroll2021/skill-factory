#!/usr/bin/env bash
# Run a chain of mission briefs in order, unattended: docs/missions/NN-*.yaml, as written by scripts/idea.sh.
# Each mission starts from the previous mission's branch and opens its PR against it (mission.base), so the PRs form
# a stack you review and merge at the end, first one first. quickship itself never merges.
# Stops at the first mission that does not end DONE. Re-run to resume: missions already DONE are skipped.
# Usage: bash scripts/program.sh [missions-dir]
# Exit: 0 all DONE, 2 no missions or a brief is invalid, 3 stopped at a mission that did not end DONE.
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 2
export CLAUDE_PROJECT_DIR="$PWD"
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
RUN="${QS_RUN:-scripts/run.sh}"
dir="${1:-docs/missions}"
S=.claude/state; mkdir -p "$S" docs
LOG="$S/program.tsv"   # mission file <TAB> state <TAB> branch, one line per finished mission
SUMMARY=docs/PROGRAM.md

shopt -s nullglob; missions=("$dir"/[0-9][0-9]-*.yaml); shopt -u nullglob
[ ${#missions[@]} -gt 0 ] || { echo "program: no mission briefs in $dir (write them, or run scripts/idea.sh)" >&2; exit 2; }
"$PY" scripts/brief.py check "${missions[@]}" >/dev/null || { echo "program: fix the mission briefs above first" >&2; exit 2; }
touch "$LOG"
[ -f "$SUMMARY" ] || printf '# Program\n\n| Mission | State | Branch | PR |\n|---|---|---|---|\n' > "$SUMMARY"

state_of() { sed -n 's/.*"state": *"\([A-Z_]*\)".*/\1/p' docs/RUN_STATE 2>/dev/null | head -n 1; }
# run.sh can fail before it archives the previous mission's terminal state, so a RUN_STATE only counts for this
# mission when its runs wrote it: it is newer than the marker touched before this invocation, or differs from the
# copy taken before this invocation or before the mission began.
fresh_state() { # fresh_state <RUN_STATE text before this invocation>
  local now; now="$(cat docs/RUN_STATE 2>/dev/null)"
  [ -n "$now" ] || return 0
  if [ docs/RUN_STATE -nt "$S/program-mark" ] || [ "$now" != "$1" ] || [ "$now" != "$(cat "$S/program-baseline" 2>/dev/null)" ]; then
    state_of
  fi
}
base=""
for m in "${missions[@]}"; do
  prev="$(awk -F'\t' -v f="$m" '$1 == f && $2 == "DONE"' "$LOG" | tail -n 1)"
  if [ -n "$prev" ]; then base="$(printf '%s' "$prev" | cut -f3)"; echo "program: $m already DONE on $base"; continue; fi
  echo "program: starting $m${base:+ on top of $base}"

  current="$(cat "$S/program-current" 2>/dev/null)"
  if [ "$current" = "$m" ] && [ -f "$S/controller.json" ]; then
    echo "program: resuming $m in the existing checkout"
  else
    # A fresh mission begins at its predecessor. A resume preserves the existing branch,
    # dirty work, brief and session so checkout cannot discard or conflict with recovery data.
    git checkout -q --detach "${base:-HEAD}" || { echo "program: cannot check out ${base:-HEAD}" >&2; exit 3; }
    if [ -n "$base" ]; then
      awk -v b="$base" '{print} /^  goal:/ && !d {print "  base: \"" b "\""; d=1}' "$m" > BRIEF.yaml
    else
      cp "$m" BRIEF.yaml
    fi
    git add BRIEF.yaml && { git commit -q -m "brief: $(basename "$m" .yaml)" -- BRIEF.yaml >/dev/null 2>&1 || true; }
    printf '%s\n' "$m" > "$S/program-current"
    cat docs/RUN_STATE > "$S/program-baseline" 2>/dev/null || : > "$S/program-baseline"
  fi

  st=""; rc=0
  # Re-invoke run.sh when it left no fresh terminal state or the runner marked the outcome retryable (a crashed lead,
  # an interrupted controller): run.sh then resumes the same session. The runner gives up itself after 5 launches.
  for _ in 1 2 3 4 5 6; do
    before="$(cat docs/RUN_STATE 2>/dev/null)"; touch "$S/program-mark"
    bash "$RUN"; rc=$?
    [ "$rc" = 2 ] && { st=""; break; }   # brief rejected before the last mission's state was archived
    st="$(fresh_state "$before")"; [ -n "$st" ] || continue
    [ "$st" != DONE ] && grep -q '"retryable": *true' docs/COMPLETION.json 2>/dev/null || break
  done
  [ "$rc" = 0 ] || [ "$st" != DONE ] || st=""   # DONE only counts with a zero exit
  outcome="${st:-FAILED rc=$rc}"
  branch="$(git branch --show-current)"
  pr="$(grep -Eo 'https://github\.com/[^ )"`]+/pull/[0-9]+' docs/REPORT.md 2>/dev/null | head -n 1)"
  printf '%s\t%s\t%s\n' "$m" "$outcome" "$branch" >> "$LOG"
  printf '| %s | %s | %s | %s |\n' "$(basename "$m")" "$outcome" "${branch:-?}" "${pr:-none}" >> "$SUMMARY"
  if [ "$st" != DONE ]; then
    echo "program: $m ended $outcome; read docs/REPORT.md. Later missions were not started." >&2
    exit 3
  fi
  [ -n "$branch" ] || { echo "program: $m ended DONE but not on a branch; cannot chain the next mission" >&2; exit 3; }
  base="$branch"
done
echo "program: all ${#missions[@]} missions DONE. Review and merge the PR stack in order, first mission first. Summary: $SUMMARY"
