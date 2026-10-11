#!/usr/bin/env bash
# scripts/ledger.py: task/progress ledgers, state hash, stall detection, replan budget, step legs.
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"
mkdir -p "$CLAUDE_PROJECT_DIR/docs" "$CLAUDE_PROJECT_DIR/.claude/state"
L="$CLAUDE_PROJECT_DIR/docs/ledgers"; S="$CLAUDE_PROJECT_DIR/.claude/state"
led() { "$QS_PYTHON" "$ROOT/scripts/ledger.py" "$@"; }
q() { local o; o="$(led "$@" 2>&1)"; printf '%s' "${o//$'\r'/}"; }
# jget <file> <python-expr over d>  -> prints the value
jget() { local o; o="$("$QS_PYTHON" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print($2)" "$1")"; printf '%s' "${o//$'\r'/}"; }
lastline() { tail -n 1 "$L/progress.jsonl"; }
reset() { led init --goal "$1" --force >/dev/null 2>&1; }

# --- init ---
expect_exit "init creates ledgers" 0 led init --goal "Ship the ledger"
if [ -f "$L/task.json" ] && [ -f "$L/progress.jsonl" ]; then ok "both ledger files exist"; else bad "both ledger files exist"; fi
expect_contains "task.json holds the goal" "Ship the ledger" "$(jget "$L/task.json" "d['goal']")"
expect_contains "task.json counters start at 0" "0 0 False" "$(jget "$L/task.json" "d['replan_count'], d['stall_count'], d['is_complete']")"
expect_exit "init again without --force exits 2" 2 led init --goal "again"
expect_exit "init --force succeeds" 0 led init --goal "Ship the ledger" --force
expect_exit "init without --goal exits 2" 2 led init
if grep -q $'\r' "$L/task.json"; then bad "task.json is LF"; else ok "task.json is LF"; fi

# --- stall-check on a fresh ledger ---
expect_exit "stall-check fresh ledger exits 0" 0 led stall-check

# --- task-add / task-set ---
expect_exit "task-add" 0 led task-add add-readme --goal "Write README" --owns README.md,docs/x.md
expect_contains "task-add stores pending task" "add-readme pending ['README.md', 'docs/x.md']" \
  "$(jget "$L/task.json" "d['plan'][0]['slug'], d['plan'][0]['status'], d['plan'][0]['owns']")"
expect_exit "task-add duplicate slug exits 2" 2 led task-add add-readme --goal "dup" --owns a
expect_exit "task-add bad slug exits 2" 2 led task-add Not_Kebab --goal "x" --owns a
expect_exit "task-add slug over 24 chars exits 2" 2 led task-add abcdefghij-abcdefghij-abcd --goal "x" --owns a
h1="$(q hash)"
if printf '%s' "$h1" | grep -Eqx '[0-9a-f]{64}'; then ok "hash is 64 hex"; else bad "hash is 64 hex ($h1)"; fi
if [ "$h1" = "$(q hash)" ]; then ok "hash stable across calls"; else bad "hash stable across calls"; fi
expect_exit "task-set merged with commit" 0 led task-set add-readme merged --branch feat/x--add-readme --commit abc1234
expect_contains "task-set updates status/branch/commit" "merged feat/x--add-readme abc1234" \
  "$(jget "$L/task.json" "d['plan'][0]['status'], d['plan'][0]['branch'], d['plan'][0]['commit']")"
h2="$(q hash)"
if [ "$h1" != "$h2" ]; then ok "hash changes after task-set"; else bad "hash changes after task-set"; fi
expect_exit "task-set bad status exits 2" 2 led task-set add-readme done
expect_exit "task-set unknown slug exits 2" 2 led task-set nope merged

# --- append ---
expect_exit "append dispatched" 0 led append dispatched add-readme "worker sent" --tokens 12 --cost 0.5
line="$(lastline)"
expect_contains "append writes step s001" '"step": "s001"' "$line"
expect_contains "append writes event" '"event": "dispatched"' "$line"
expect_contains "append writes detail" '"detail": "worker sent"' "$line"
expect_contains "append writes tokens" '"tokens": 12' "$line"
if printf '%s' "$line" | grep -Eq '"ts": "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"'; then ok "append writes UTC ts"; else bad "append writes UTC ts: $line"; fi
if printf '%s' "$line" | grep -Eq '"state_hash": "[0-9a-f]{64}"'; then ok "append writes 64-hex state_hash"; else bad "append writes 64-hex state_hash: $line"; fi
expect_contains "append state_hash equals current hash" "$h2" "$line"
expect_exit "append bad event exits 2" 2 led append exploded add-readme "x"
led append note add-readme "second" >/dev/null 2>&1
expect_contains "append second line is s002" '"step": "s002"' "$(lastline)"

# --- hash excludes counters ---
hb="$(q hash)"
led replan >/dev/null 2>&1
if [ "$hb" = "$(q hash)" ]; then ok "hash unchanged by replan"; else bad "hash unchanged by replan"; fi

# --- stall-check rules ---
reset "stall goal"; led task-add t1 --goal "Do t1" --owns a >/dev/null 2>&1
led append dispatched t1 "first" >/dev/null 2>&1
expect_exit "stall-check one dispatch exits 0" 0 led stall-check
led task-set t1 dispatched >/dev/null 2>&1
led append dispatched t1 "second" >/dev/null 2>&1
expect_exit "stall-check two dispatches same slug+goal exits 1" 1 led stall-check
expect_contains "stall-check names the dispatch reason" "dispatched" "$OUT"
expect_contains "stall_count incremented to 1" "1" "$(jget "$L/task.json" "d['stall_count']")"

reset "failed goal"; led task-add t2 --goal "Do t2" --owns b >/dev/null 2>&1
for i in 1 2 3; do led task-set t2 pending --commit "c$i" >/dev/null 2>&1; led append failed t2 "tests red" >/dev/null 2>&1; done
expect_exit "stall-check three identical failed exits 1" 1 led stall-check
expect_contains "stall-check names the failed reason" "failed" "$OUT"

reset "failed differ"; led task-add t3 --goal "Do t3" --owns c >/dev/null 2>&1
for i in 1 2 3; do led task-set t3 pending --commit "c$i" >/dev/null 2>&1; led append failed t3 "error $i" >/dev/null 2>&1; done
expect_exit "stall-check three differing failed exits 0" 0 led stall-check

reset "hash goal"; led task-add t4 --goal "Do t4" --owns d >/dev/null 2>&1
led append note t4 "a" >/dev/null 2>&1; led append note t4 "b" >/dev/null 2>&1
expect_exit "stall-check unchanged state_hash exits 1" 1 led stall-check
expect_contains "stall-check names the hash reason" "state_hash" "$OUT"

reset "timeout goal"; led task-add t5 --goal "Do t5" --owns e >/dev/null 2>&1
led append timeout t5 "worker hung" >/dev/null 2>&1
expect_exit "stall-check timeout exits 1" 1 led stall-check
expect_contains "stall-check names the timeout reason" "timeout" "$OUT"

# --- replan ---
reset "replan goal"
printf '{"budgets": {"replan_limit": 1}}' > "$S/brief.json"
led append timeout t "x" >/dev/null 2>&1; led stall-check >/dev/null 2>&1
expect_contains "stall_count is 1 before replan" "1" "$(jget "$L/task.json" "d['stall_count']")"
expect_exit "replan within limit exits 0" 0 led replan
expect_contains "replan resets stall, bumps replan_count" "0 1" "$(jget "$L/task.json" "d['stall_count'], d['replan_count']")"
expect_contains "replan appends a replan line" '"event": "replan"' "$(lastline)"
expect_exit "replan over limit exits 3" 3 led replan
expect_contains "replan_count is 2" "2" "$(jget "$L/task.json" "d['replan_count']")"
rm -f "$S/brief.json"
expect_exit "replan without brief uses default limit 5" 0 led replan
# the overseer's force_replan flag is answered by a replan; the lead may not delete .claude/state files itself
touch "$S/force_replan"
expect_exit "replan with force_replan flag exits 0" 0 led replan
expect_contains "replan reports the cleared flag" "cleared force_replan" "$OUT"
[ ! -e "$S/force_replan" ] && ok "replan removes force_replan" || bad "replan removes force_replan"
expect_exit "replan without the flag exits 0" 0 led replan
case "$OUT" in *"cleared force_replan"*) bad "replan without the flag says nothing about it";; *) ok "replan without the flag says nothing about it";; esac

# --- handoff: a prose note for the next context window, appended, newest last ---
rm -f "$L/handoff.md"
expect_exit "handoff writes handoff.md" 0 led handoff "merged add-readme" --next "dispatch wire-ci" --note "CI needs Node 20"
expect_contains "handoff prints the path" "docs/ledgers/handoff.md" "$OUT"
H="$(cat "$L/handoff.md")"
expect_contains "handoff has Done line" "Done: merged add-readme" "$H"
expect_contains "handoff has Next line" "Next: dispatch wire-ci" "$H"
expect_contains "handoff has Note line" "Note: CI needs Node 20" "$H"
if grep -Eq '^## [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$L/handoff.md"; then ok "handoff section is dated UTC"; else bad "handoff section is dated UTC"; fi
expect_exit "handoff appends a second section" 0 led handoff "merged wire-ci" --next "run criteria"
[ "$(grep -c '^## ' "$L/handoff.md")" = "2" ] && ok "handoff keeps earlier sections" || bad "handoff keeps earlier sections ($(grep -c '^## ' "$L/handoff.md"))"
expect_contains "newest handoff is last" "Next: run criteria" "$(tail -n 1 "$L/handoff.md")"
expect_exit "handoff without --next exits 2" 2 led handoff "done only"
if grep -q $'\r' "$L/handoff.md"; then bad "handoff.md is LF"; else ok "handoff.md is LF"; fi

# --- step-start ---
reset "step goal"; led append note x "one" >/dev/null 2>&1
expect_exit "step-start writes current_step" 0 led step-start add-readme --legs untrusted_content,local_write
expect_contains "current_step has next id, slug, legs" "s002 add-readme ['untrusted_content', 'local_write']" \
  "$(jget "$S/current_step.json" "d['id'], d['slug'], d['legs']")"
expect_exit "step-start untrusted_content+outbound exits 2" 2 led step-start leak --legs untrusted_content,outbound
expect_contains "denied step-start leaves current_step alone" "add-readme" "$(jget "$S/current_step.json" "d['slug']")"

# --- facts-invalidate ---
"$QS_PYTHON" -c "
import json, sys
p = sys.argv[1]; d = json.load(open(p, encoding='utf-8'))
d['facts'] = [{'text': 'API uses v2 endpoints', 'source': 'docs', 'ts': '2026-09-30T00:00:00Z', 'valid': True},
              {'text': 'Node 20 required', 'source': 'pkg', 'ts': '2026-09-30T00:00:00Z', 'valid': True}]
open(p, 'w', encoding='utf-8', newline='\n').write(json.dumps(d))
" "$L/task.json"
expect_exit "facts-invalidate" 0 led facts-invalidate "v2 endpoints"
expect_contains "facts-invalidate flips only matches" "False True" "$(jget "$L/task.json" "d['facts'][0]['valid'], d['facts'][1]['valid']")"

# --- tier ---
rm -f "$S/tier"
expect_exit "tier with no file and no arg exits 0" 0 led tier
expect_contains "tier with no file prints act" "act" "$OUT"
expect_exit "tier plan writes plan" 0 led tier plan
if [ "$(cat "$S/tier")" = "plan" ]; then ok "tier file contains plan"; else bad "tier file contains plan ($(cat "$S/tier" 2>&1))"; fi
bytes="$(wc -c < "$S/tier" | tr -d ' ')"
if [ "$bytes" = "5" ]; then ok "tier file is 'plan' plus one newline, no more"; else bad "tier file is 'plan' plus one newline, no more ($bytes bytes)"; fi
expect_exit "tier act overwrites" 0 led tier act
if [ "$(cat "$S/tier")" = "act" ]; then ok "tier file contains act after overwrite"; else bad "tier file contains act after overwrite ($(cat "$S/tier" 2>&1))"; fi
led tier plan >/dev/null 2>&1
expect_exit "tier no arg prints current value" 0 led tier
expect_contains "tier no arg prints plan" "plan" "$OUT"
expect_exit "tier bogus exits 2" 2 led tier bogus
expect_contains "tier bogus stderr names the value" "bogus" "$OUT"
if [ "$(cat "$S/tier")" = "plan" ]; then ok "tier bogus leaves existing tier alone"; else bad "tier bogus leaves existing tier alone"; fi
rm -rf "$S"
expect_exit "tier plan creates missing .claude/state dir" 0 led tier plan
if [ -f "$S/tier" ]; then ok "tier file exists after creating missing state dir"; else bad "tier file exists after creating missing state dir"; fi

# --- missing ledger ---
rm -f "$L/task.json"
expect_exit "task-add without task.json exits 2" 2 led task-add x --goal y --owns z
expect_exit "hash without task.json exits 2" 2 led hash

# --- archive-stale: a merged mission PR carries RUN_STATE and the ledgers into the next mission's checkout;
# a terminal RUN_STATE that belongs to a different goal than brief.json is moved to docs/runs/ and the runtime state reset ---
D="$CLAUDE_PROJECT_DIR/docs"
printf '{"mission":{"goal":"Goal A"},"budgets":{"replan_limit":3}}' > "$S/brief.json"
rm -f "$D/RUN_STATE"; rm -rf "$D/runs"
expect_exit "archive-stale: no RUN_STATE exits 0" 0 led archive-stale
expect_contains "archive-stale: no RUN_STATE says current" "current" "$OUT"
rm -f "$L/task.json" "$L/progress.jsonl"
printf '{"state":"DONE_PARTIAL","reason":"ended before planning","at":"2026-09-30T11:00:00Z"}
' > "$D/RUN_STATE"
expect_exit "archive-stale: terminal RUN_STATE without ledgers exits 0" 0 led archive-stale
expect_contains "archive-stale: terminal RUN_STATE without ledgers is archived" "archived" "$OUT"
[ ! -e "$D/RUN_STATE" ] && ok "archive-stale: lone RUN_STATE moved" || bad "archive-stale: lone RUN_STATE moved"
expect_contains "archive-stale: lone run dir named by date" "2026-09-30" "$(ls -d "$D"/runs/*/ | head -1)"
rm -rf "$D/runs"
reset "Goal A"
printf '{"state":"DONE","reason":"x","at":"2026-09-30T12:00:00Z"}\n' > "$D/RUN_STATE"; echo report > "$D/REPORT.md"; echo plan > "$D/plan.md"
printf 'sess-1' > "$S/session_id"; echo 40 > "$S/steps"; echo 2 > "$S/restarts"
expect_exit "archive-stale: same goal exits 0" 0 led archive-stale
expect_contains "archive-stale: same goal says current" "current" "$OUT"
if [ -f "$D/RUN_STATE" ] && [ -f "$L/task.json" ] && [ -f "$S/session_id" ]; then ok "archive-stale: same goal leaves everything in place"; else bad "archive-stale: same goal leaves everything in place"; fi
printf '{"mission":{"goal":"Goal B"},"budgets":{"replan_limit":3}}' > "$S/brief.json"
expect_exit "archive-stale: different goal exits 0" 0 led archive-stale
expect_contains "archive-stale: different goal says archived" "archived" "$OUT"
if [ ! -e "$D/RUN_STATE" ] && [ ! -e "$D/REPORT.md" ] && [ ! -e "$D/plan.md" ] && [ ! -e "$L/task.json" ]; then ok "archive-stale: old run artifacts gone"; else bad "archive-stale: old run artifacts gone"; fi
runs="$(ls -d "$D"/runs/*/ 2>/dev/null | wc -l | tr -d ' ')"
expect_contains "archive-stale: one dir under docs/runs" "1" "$runs"
rdir="$(ls -d "$D"/runs/*/ | head -1)"
if [ -f "$rdir/RUN_STATE" ] && [ -f "$rdir/REPORT.md" ] && [ -f "$rdir/ledgers/task.json" ]; then ok "archive-stale: artifacts moved into the run dir"; else bad "archive-stale: artifacts moved into the run dir ($rdir)"; fi
expect_contains "archive-stale: run dir named by date and goal" "2026-09-30" "$rdir"
if [ ! -e "$S/session_id" ] && [ ! -e "$S/steps" ] && [ ! -e "$S/restarts" ]; then ok "archive-stale: runtime state reset"; else bad "archive-stale: runtime state reset"; fi
if [ -s "$S/started_at" ]; then ok "archive-stale: started_at rewritten"; else bad "archive-stale: started_at rewritten"; fi
expect_exit "archive-stale: second call is a no-op" 0 led archive-stale
expect_contains "archive-stale: second call says current" "current" "$OUT"

# --- archive-stale: a lead that fails before `ledger.py init` leaves a terminal RUN_STATE and no ledgers. While the
# controller's frozen brief carries the same goal it is still the current run: archiving it would reset the deadline,
# the launch counter and the budget on every rerun, so the 5-launch limit could never trip ---
rm -rf "$D/runs" "$L"
printf '{"mission":{"goal":"Goal G"}}' > "$S/brief.json"
printf '{"schema":3,"brief":{"mission":{"goal":"Goal G"}},"deadline":1}' > "$S/controller.json"
"$QS_PYTHON" -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)'); db.execute(\"INSERT OR REPLACE INTO kv VALUES ('launches','4')\"); db.commit(); db.close()" "$S/runtime.sqlite3"
launches() { local o; o="$("$QS_PYTHON" -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute(\"SELECT value FROM kv WHERE key='launches'\").fetchone()[0])" "$S/runtime.sqlite3" 2>&1)"; printf '%s' "${o//$'\r'/}"; }
printf 'sess-g' > "$S/session_id"; printf '2026-10-01T00:00:00Z\n' > "$S/started_at"
printf '{"state":"SAFE_STOP","reason":"Claude session failed","at":"2026-10-01T00:05:00Z"}\n' > "$D/RUN_STATE"
expect_exit "archive-stale: same-goal controller run without ledgers exits 0" 0 led archive-stale
expect_contains "archive-stale: same-goal controller run without ledgers says current" "current" "$OUT"
if [ -f "$D/RUN_STATE" ] && [ -f "$S/controller.json" ] && [ -f "$S/session_id" ] && [ ! -e "$D/runs" ]; then ok "archive-stale: same-goal controller run left in place"; else bad "archive-stale: same-goal controller run left in place"; fi
expect_contains "archive-stale: launch counter survives" "4" "$(launches)"
expect_contains "archive-stale: started_at survives" "2026-10-01T00:00:00Z" "$(cat "$S/started_at")"
printf '{"mission":{"goal":"Goal H"}}' > "$S/brief.json"
expect_exit "archive-stale: different-goal controller run exits 0" 0 led archive-stale
expect_contains "archive-stale: different-goal controller run is archived" "archived" "$OUT"
if [ ! -e "$S/controller.json" ] && [ ! -e "$S/runtime.sqlite3" ] && [ ! -e "$S/session_id" ] && [ ! -e "$D/RUN_STATE" ]; then ok "archive-stale: different-goal controller state reset"; else bad "archive-stale: different-goal controller state reset"; fi
expect_contains "archive-stale: different-goal run dir named by the old goal" "goal-g" "$(ls -d "$D"/runs/*/ | head -1)"

# --- archive-stale --force (behind `run.sh --archive`): the operator archives a terminal run of the same goal ---
rm -rf "$D/runs"
printf '{"mission":{"goal":"Goal G"}}' > "$S/brief.json"
printf '{"schema":3,"brief":{"mission":{"goal":"Goal G"}},"deadline":1}' > "$S/controller.json"
printf 'sess-g' > "$S/session_id"
printf '{"state":"DONE_PARTIAL","reason":"budget","at":"2026-10-02T00:00:00Z"}\n' > "$D/RUN_STATE"
expect_exit "archive-stale --force: same goal exits 0" 0 led archive-stale --force
expect_contains "archive-stale --force: same goal is archived" "archived docs/runs/" "$OUT"
if [ ! -e "$S/controller.json" ] && [ ! -e "$S/session_id" ] && [ ! -e "$D/RUN_STATE" ]; then ok "archive-stale --force: run state reset"; else bad "archive-stale --force: run state reset"; fi
expect_exit "archive-stale --force: nothing terminal exits 0" 0 led archive-stale --force
expect_contains "archive-stale --force: nothing terminal says current" "current" "$OUT"
finish
