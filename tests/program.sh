#!/usr/bin/env bash
# scripts/program.sh: runs docs/missions/NN-*.yaml in order. Each mission starts from the previous mission's branch
# with mission.base set to it, stops at the first mission that is not DONE, and resumes by skipping DONE missions.
source "$(dirname "$0")/lib.sh"
mk() { # repo with scripts/, two mission briefs and a stub lead (QS_RUN) that branches, commits and ends terminal
  local d; d="$(tmpdir)"; git init -q -b main "$d"
  git -C "$d" config user.email t@t; git -C "$d" config user.name t; git -C "$d" config core.autocrlf false
  cp -r "$ROOT/scripts" "$d/"; mkdir -p "$d/docs/missions"
  awk '/^  goal:/{print "  goal: \"alpha\""; next} {print}' "$ROOT/BRIEF.example.yaml" > "$d/docs/missions/01-alpha.yaml"
  awk '/^  goal:/{print "  goal: \"beta\""; next} {print}' "$ROOT/BRIEF.example.yaml" > "$d/docs/missions/02-beta.yaml"
  (cd "$d" && git add -A && git commit -qm init)
  cat > "$d/stub_run.sh" <<'STUB'
#!/usr/bin/env bash
g="$(sed -n 's/^  goal: "\(.*\)"/\1/p' BRIEF.yaml)"
printf '%s|%s|%s\n' "$g" "$(git rev-parse HEAD)" "$(sed -n 's/^  base: "\(.*\)"/\1/p' BRIEF.yaml)" >> "$STUB_LOG"
[ "$g" = "${STUB_FAIL:-}" ] && exit 1                 # crashes before archiving: the last mission's state stays
rm -f docs/RUN_STATE                                   # run.sh archives the previous mission's state
if [ "$g" = "${STUB_RETRY:-}" ] && [ ! -e .claude/state/stub-retried ]; then   # the lead crashes once
  touch .claude/state/stub-retried
  printf '{"state":"SAFE_STOP","reason":"crash","at":"y"}\n' > docs/RUN_STATE
  printf '{"state":"SAFE_STOP","retryable":true}\n' > docs/COMPLETION.json; exit 3
fi
git checkout -q -b "mission/$g" 2>/dev/null || git checkout -q "mission/$g"
echo "$g" > "$g.txt"; git add "$g.txt"; git commit -qm "$g"
state=DONE; [ "$g" = "${STUB_PARTIAL:-}" ] && state=DONE_PARTIAL; [ "$g" = "${STUB_SAFE_STOP:-}" ] && state=SAFE_STOP
printf '{"state":"%s","reason":"r","at":"x"}\n' "$state" > docs/RUN_STATE
printf '{"state":"%s","retryable":false}\n' "$state" > docs/COMPLETION.json
printf 'PR: https://github.com/example-owner/x/pull/%s\n' "${#g}" > docs/REPORT.md
STUB
  chmod +x "$d/stub_run.sh"; echo "$d"
}
prog() { (cd "$1" && QS_RUN="$1/stub_run.sh" STUB_LOG="$1/stub.log" bash scripts/program.sh "${@:2}"); }

d="$(mk)"
expect_exit "two missions: exit 0" 0 prog "$d"
expect_contains "both missions ran" "2" "$(wc -l < "$d/stub.log" | tr -d ' ')"
first="$(sed -n 1p "$d/stub.log")"; second="$(sed -n 2p "$d/stub.log")"
expect_contains "mission 1 has no base" "alpha||" "${first%%|*}||${first##*|}"
expect_contains "mission 2 base is mission 1's branch" "mission/alpha" "${second##*|}"
tip="$(git -C "$d" rev-parse mission/alpha)"; started="$(printf '%s' "$second" | cut -d'|' -f2)"
[ "$(git -C "$d" rev-parse "$started^")" = "$tip" ] && ok "mission 2 starts from mission 1's tip (plus the brief commit)" || bad "mission 2 starts from mission 1's tip"
expect_contains "main untouched" "$(git -C "$d" rev-parse main)" "$(git -C "$d" log -1 --format=%H main)"
[ "$(git -C "$d" rev-list --count main)" = 1 ] && ok "no commits on main" || bad "no commits on main"
expect_contains "PROGRAM.md lists mission 2" "02-beta.yaml" "$(cat "$d/docs/PROGRAM.md")"
expect_contains "PROGRAM.md has the PR link" "pull/" "$(cat "$d/docs/PROGRAM.md")"
expect_contains "final message says merge the stack" "merge" "$OUT"

: > "$d/stub.log"
expect_exit "re-run: exit 0" 0 prog "$d"
expect_contains "re-run: nothing ran" "0" "$(wc -l < "$d/stub.log" | tr -d ' ')"

d2="$(mk)"
expect_exit "mission 1 partial: exit 3" 3 bash -c "cd '$d2' && QS_RUN='$d2/stub_run.sh' STUB_LOG='$d2/stub.log' STUB_PARTIAL=alpha bash scripts/program.sh"
expect_contains "mission 1 partial: mission 2 not run" "1" "$(wc -l < "$d2/stub.log" | tr -d ' ')"
expect_contains "mission 1 partial: names the state" "DONE_PARTIAL" "$OUT"

d6="$(mk)"
expect_exit "mission 2 partial: exit 3" 3 bash -c "cd '$d6' && QS_RUN='$d6/stub_run.sh' STUB_LOG='$d6/stub.log' STUB_PARTIAL=beta bash scripts/program.sh"
printf '{"schema":3}\n' > "$d6/.claude/state/controller.json"
echo recovery > "$d6/beta.txt"
resume_tip="$(git -C "$d6" rev-parse HEAD)"
: > "$d6/stub.log"
expect_exit "resume preserves dirty mission checkout" 0 prog "$d6"
expect_contains "resume does not detach back to predecessor" "$resume_tip" "$(head -1 "$d6/stub.log")"
expect_contains "resume names the preserved checkout" "existing checkout" "$OUT"

d3="$(mk)"; rm "$d3"/docs/missions/*.yaml
expect_exit "no missions: exit 2" 2 prog "$d3"
d4="$(mk)"; sed -i '/^  steps:/d' "$d4/docs/missions/02-beta.yaml"
expect_exit "invalid mission brief: exit 2 before anything runs" 2 prog "$d4"
expect_contains "invalid brief: nothing ran" "" "$(cat "$d4/stub.log" 2>/dev/null)"

# run.sh rejecting the brief (exit 2, before it archives the last mission's DONE) must not read as DONE
d5="$(mk)"; printf '#!/usr/bin/env bash
exit 2
' > "$d5/stub_run.sh"
printf '{"state":"DONE","reason":"old","at":"x"}
' > "$d5/docs/RUN_STATE"
expect_exit "run.sh exit 2 with a stale DONE: exit 3" 3 prog "$d5"

# run.sh failing (exit 1) before it archives the last mission's DONE: that stale DONE is not mission 2's outcome
d7="$(mk)"
expect_exit "mission 2 fails before archiving: exit 3" 3 bash -c "cd '$d7' && QS_RUN='$d7/stub_run.sh' STUB_LOG='$d7/stub.log' STUB_FAIL=beta bash scripts/program.sh"
phantom="$(awk -F'\t' '$1 ~ /02-beta/ && $2 == "DONE"' "$d7/.claude/state/program.tsv" | wc -l | tr -d ' ')"
[ "$phantom" = 0 ] && ok "mission 2 failure is not logged as DONE" || bad "mission 2 failure is not logged as DONE ($(cat "$d7/.claude/state/program.tsv"))"
expect_contains "mission 2 failure logs the real outcome" "FAILED rc=1" "$(cat "$d7/.claude/state/program.tsv")"
: > "$d7/stub.log"
expect_exit "re-run after mission 2 failed: exit 0" 0 prog "$d7"
expect_contains "re-run runs mission 2" "beta|" "$(cat "$d7/stub.log")"

# the runner marks a crashed lead retryable in COMPLETION.json: program.sh re-invokes run.sh within its passes
d8="$(mk)"
expect_exit "retryable SAFE_STOP then DONE: exit 0" 0 bash -c "cd '$d8' && QS_RUN='$d8/stub_run.sh' STUB_LOG='$d8/stub.log' STUB_RETRY=alpha bash scripts/program.sh"
expect_contains "retryable SAFE_STOP: mission 1 ran twice, then mission 2" "alpha alpha beta" "$(cut -d'|' -f1 "$d8/stub.log" | tr '\n' ' ')"
expect_contains "retryable SAFE_STOP: mission 1 logged DONE" "01-alpha.yaml	DONE" "$(cat "$d8/.claude/state/program.tsv")"
d9="$(mk)"
expect_exit "non-retryable SAFE_STOP: exit 3" 3 bash -c "cd '$d9' && QS_RUN='$d9/stub_run.sh' STUB_LOG='$d9/stub.log' STUB_SAFE_STOP=alpha bash scripts/program.sh"
expect_contains "non-retryable SAFE_STOP: one pass only" "1" "$(wc -l < "$d9/stub.log" | tr -d ' ')"
finish
