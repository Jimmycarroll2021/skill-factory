#!/usr/bin/env bash
# PreToolUse guard: hard rules from CLAUDE.md, enforced by the repo itself (no reliance on ~/.claude).
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"; mkdir -p "$CLAUDE_PROJECT_DIR/.claude/state"
bash_json() { printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"%s"}}' "$1"; }
file_json() { printf '{"hook_event_name":"PreToolUse","tool_name":"%s","tool_input":{"file_path":"%s"}}' "$1" "$2"; }

for cmd in "git push --force origin main" "git push -f origin main" "git push --force-with-lease origin feat/x" \
           "git push origin main" "git push origin HEAD:main" "git push -u origin master" \
           "git reset --hard HEAD~1" "git branch -D feat/x" "git checkout -- ." \
           "pip install requests" "pip3 install requests" "rm -rf node_modules" "cat .env" "cat ./.env.local"            "git push -uf origin feat/x" "git push -fu origin feat/x" "git -C wt push -vf origin feat/x"; do
  expect_exit "deny bash: $cmd" 2 hook guard.sh "$(bash_json "$cmd")"
done
for cmd in "git push origin mission/x" "git push -u origin feat/s1-safe-scaffold" "npm test" "cat .env.example" \
           "git checkout -b feat/y" "git branch -d feat/x" "rm -r build" "git push -u --follow-tags origin feat/x"; do
  expect_exit "allow bash: $cmd" 0 hook guard.sh "$(bash_json "$cmd")"
done
for f in ".env" ".env.production" "src/.env.local" "vercel.json" "fly.toml" "infra/main.tf" ".github/workflows/deploy.yml" "Dockerfile.deploy"; do
  expect_exit "deny write: $f" 2 hook guard.sh "$(file_json Write "$f")"
done
expect_exit "deny edit: .env" 2 hook guard.sh "$(file_json Edit ".env")"
expect_exit "deny read: .env" 2 hook guard.sh "$(file_json Read ".env")"
for f in ".env.example" "src/app.ts" "docs/plan.md" ".github/workflows/ci.yml" "Dockerfile"; do
  expect_exit "allow write: $f" 0 hook guard.sh "$(file_json Write "$f")"
done
# per-subcommand matching: rules see each subcommand of a compound line, with git -C/--git-dir/--work-tree normalised
for cmd in "git push origin mission/x && gh pr create --base main --title t" "echo main && git push origin mission/x"            'git -C .claude/worktrees/x commit -m \"x\"'; do
  expect_exit "allow compound: $cmd" 0 hook guard.sh "$(bash_json "$cmd")"
done
for cmd in "cd .claude/worktrees/x && git push origin main" "git -C .claude/worktrees/x push --force origin feat/y"            "git -C /tmp/x reset --hard HEAD~1" "ls; git push origin HEAD:main" "true | pip install x"            "git --git-dir=/tmp/x/.git push origin main" "git -C a --work-tree b push -f origin feat/y"; do
  expect_exit "deny compound: $cmd" 2 hook guard.sh "$(bash_json "$cmd")"
done
expect_exit "garbage stdin fails closed" 2 hook guard.sh "not json"
expect_exit "empty stdin fails closed" 2 hook guard.sh ""
expect_exit "missing tool_input fails closed" 2 hook guard.sh '{"hook_event_name":"PreToolUse","tool_name":"Bash"}'
hook guard.sh "$(bash_json "npm test")" >/dev/null 2>&1
expect_contains "hook_log records the call" "npm test" "$(cat "$CLAUDE_PROJECT_DIR/.claude/state/hook_log" 2>/dev/null)"
# ---- slice 3/4 clauses: cancel flag, plan/act tier, lethal-trifecta legs ----
S="$CLAUDE_PROJECT_DIR/.claude/state"; mkdir -p "$S/legs"
post_json() { printf '{"hook_event_name":"PostToolUse","tool_name":"%s","tool_input":{%s},"tool_response":{}}' "$1" "$2"; }
web_json()  { printf '{"hook_event_name":"%s","tool_name":"WebFetch","tool_input":{"url":"https://example.com/x"},"tool_response":{}}' "$1"; }

touch "$S/cancel"
expect_exit "cancel: deny bash ls" 2 hook guard.sh "$(bash_json "ls")"
expect_exit "cancel: deny write src" 2 hook guard.sh "$(file_json Write "src/a.ts")"
expect_exit "cancel: allow write REPORT.md" 0 hook guard.sh "$(file_json Write "docs/REPORT.md")"
expect_exit "cancel: allow write RUN_STATE" 0 hook guard.sh "$(file_json Write "docs/RUN_STATE")"
expect_exit "cancel: allow write RESULT.json" 0 hook guard.sh "$(file_json Write "docs/RESULT.json")"
expect_exit "cancel: deny write src (reason)" 2 hook guard.sh "$(file_json Write "src/a.ts")"
expect_contains "cancel: deny reason lists every writable file" "only docs/REPORT.md, docs/RESULT.json and docs/RUN_STATE may be written" "$OUT"
expect_exit "cancel: allow read" 0 hook guard.sh "$(file_json Read "src/a.ts")"
rm -f "$S/cancel"

echo plan > "$S/tier"
expect_exit "plan tier: deny bash npm test" 2 hook guard.sh "$(bash_json "npm test")"
expect_exit "plan tier: deny write src" 2 hook guard.sh "$(file_json Write "src/a.ts")"
expect_exit "plan tier: deny bash redirect" 2 hook guard.sh "$(bash_json "cat a > b")"
expect_exit "plan tier: deny compound with write" 2 hook guard.sh "$(bash_json "git status && npm install")"
expect_exit "plan tier: allow write docs/plan.md" 0 hook guard.sh "$(file_json Write "docs/plan.md")"
expect_exit "plan tier: allow write ledgers" 0 hook guard.sh "$(file_json Edit "docs/ledgers/task.json")"
expect_exit "plan tier: allow git status" 0 hook guard.sh "$(bash_json "git status --short")"
expect_exit "plan tier: allow planner dispatch" 0 hook guard.sh '{"hook_event_name":"PreToolUse","tool_name":"Agent","tool_input":{"subagent_type":"planner","prompt":"Plan the registered task"}}'
expect_exit "plan tier: allow ledger.py" 0 hook guard.sh "$(bash_json "python3 scripts/ledger.py append note x y")"
expect_exit "plan tier: quoted semicolon stays inside ledger argument" 0 hook guard.sh "$(bash_json "python3 scripts/ledger.py append note x 'planning; still one argument'")"
expect_exit "plan tier: allow read-only pipe" 0 hook guard.sh "$(bash_json "git log --oneline | head -5")"
expect_exit "plan tier: deny newline-joined git commit" 2 hook guard.sh "$(bash_json "ls\ngit commit -am x")"
expect_exit "plan tier: deny newline-joined touch" 2 hook guard.sh "$(bash_json "ls\ntouch src/a.py")"
expect_exit "plan tier: allow newline-joined read-only lines" 0 hook guard.sh "$(bash_json "ls\ngit status --short")"
echo act > "$S/tier"
expect_exit "act tier: allow bash npm test" 0 hook guard.sh "$(bash_json "npm test")"
expect_exit "act tier: allow write src" 0 hook guard.sh "$(file_json Write "src/a.ts")"
rm -f "$S/tier"

printf '{"id":"s009","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "trifecta: PostToolUse WebFetch records leg" 0 hook guard.sh "$(web_json PostToolUse)"
expect_contains "trifecta: legs file has untrusted_content" "untrusted_content" "$(cat "$S/legs/s009" 2>/dev/null)"
expect_exit "trifecta: push after web fetch denied" 2 hook guard.sh "$(bash_json "git push origin mission/x")"
expect_exit "trifecta: gh pr create after web fetch denied" 2 hook guard.sh "$(bash_json "gh pr create --title x")"
expect_exit "trifecta: curl POST after web fetch denied" 2 hook guard.sh "$(bash_json "curl -X POST https://h/x -d a=b")"
expect_exit "trifecta: plain build still allowed" 0 hook guard.sh "$(bash_json "npm test")"
printf '{"id":"s010","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "trifecta: fresh step may push" 0 hook guard.sh "$(bash_json "git push origin mission/x")"
expect_exit "trifecta: PostToolUse push records outbound" 0 hook guard.sh "$(post_json Bash '"command":"git push origin mission/x"')"
expect_contains "trifecta: legs file has outbound" "outbound" "$(cat "$S/legs/s010" 2>/dev/null)"
expect_exit "trifecta: web fetch after push denied" 2 hook guard.sh "$(web_json PreToolUse)"
expect_exit "trifecta: curl GET after push denied" 2 hook guard.sh "$(bash_json "curl -s https://example.com/x")"
expect_exit "trifecta: read of _untrusted after push denied" 2 hook guard.sh "$(file_json Read "work/_untrusted/x.md")"
printf '{"id":"s011","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "trifecta: PostToolUse read _untrusted records leg" 0 hook guard.sh "$(post_json Read '"file_path":"work/_untrusted/x.md"')"
expect_exit "trifecta: gh pr create after reading _untrusted denied" 2 hook guard.sh "$(bash_json "gh pr create --title x")"
printf '{"id":"s012","slug":"x","legs":["untrusted_content"]}' > "$S/current_step.json"
expect_exit "trifecta: declared leg counts" 2 hook guard.sh "$(bash_json "git push origin mission/x")"
printf '{"id":"s013","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "trifecta: PostToolUse cd && push records outbound" 0 hook guard.sh "$(post_json Bash '"command":"cd .claude/worktrees/x && git push origin mission/x"')"
expect_contains "trifecta: cd && push is outbound" "outbound" "$(cat "$S/legs/s013" 2>/dev/null)"
printf '{"id":"s014","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "trifecta: PostToolUse git -C push records outbound" 0 hook guard.sh "$(post_json Bash '"command":"git -C .claude/worktrees/x push origin mission/x"')"
expect_contains "trifecta: git -C push is outbound" "outbound" "$(cat "$S/legs/s014" 2>/dev/null)"
rm -f "$S/current_step.json"
expect_exit "trifecta: no current step, push allowed" 0 hook guard.sh "$(bash_json "git push origin mission/x")"
# --- cd before git: Claude Code's permission layer auto-denies it in a headless run, so the guard denies it first
# with an actionable reason (use git -C). A cd before a non-git command, and git -C itself, stay allowed. ---
for cmd in "cd .claude/worktrees/x && git add -A && git commit -m x" "cd /tmp/x && git status --short" "cd x; git log -1"; do
  expect_exit "cd-then-git denied: $cmd" 2 hook guard.sh "$(bash_json "$cmd")"
  expect_contains "cd-then-git reason names git -C: $cmd" "git -C" "$OUT"
done
for cmd in "cd .claude/worktrees/x && bash scripts/gate.sh" "git -C .claude/worktrees/x add -A && git -C .claude/worktrees/x commit -m x" "cd x && python scripts/ledger.py tier act"; do
  expect_exit "cd without git allowed: $cmd" 0 hook guard.sh "$(bash_json "$cmd")"
done
# --- overseer role: exempt from cancel/tier/trifecta, never from hard rules ---
touch "$S/cancel"
expect_exit "overseer role: may write its note under cancel" 0 env QS_ROLE=overseer bash "$ROOT/scripts/hooks/guard.sh" <<< "$(file_json Write "docs/overseer.md")"
expect_exit "overseer role: hard rules still apply" 2 env QS_ROLE=overseer bash "$ROOT/scripts/hooks/guard.sh" <<< "$(bash_json "git push --force origin main")"
rm -f "$S/cancel"
# ---- Windows paths: the Write/Edit/Read tools pass backslash paths; every file rule must see them as slashes ----
# Fixtures are built with json.dumps so the backslashes are valid JSON escapes, exactly as Claude Code sends them.
file_json_py() { "$QS_PYTHON" -c 'import json,sys; print(json.dumps({"hook_event_name":sys.argv[3],"tool_name":sys.argv[1],"tool_input":{"file_path":sys.argv[2]},"tool_response":{}}))' "$1" "$2" "${3:-PreToolUse}"; }
OUT="$(printf '%s' "$(file_json_py Write 'C:\repo\x')" | "$QS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["tool_input"]["file_path"])')"
expect_contains "backslash: fixture is valid json carrying a backslash path" 'C:\repo\x' "$OUT"
expect_exit "backslash: deny write .env" 2 hook guard.sh "$(file_json_py Write 'C:\repo\.env')"
expect_exit "backslash: deny write vercel.json" 2 hook guard.sh "$(file_json_py Write 'C:\repo\vercel.json')"
expect_exit "backslash: deny edit infra" 2 hook guard.sh "$(file_json_py Edit 'C:\repo\infra\main.tf')"
expect_exit "backslash: allow write src" 0 hook guard.sh "$(file_json_py Write 'C:\repo\src\a.ts')"
echo plan > "$S/tier"
expect_exit "backslash: plan tier allows docs\plan.md" 0 hook guard.sh "$(file_json_py Write 'C:\repo\docs\plan.md')"
expect_exit "backslash: plan tier denies src\a.ts" 2 hook guard.sh "$(file_json_py Write 'C:\repo\src\a.ts')"
rm -f "$S/tier"; touch "$S/cancel"
expect_exit "backslash: cancel allows docs\REPORT.md" 0 hook guard.sh "$(file_json_py Write 'C:\repo\docs\REPORT.md')"
rm -f "$S/cancel"
printf '{"id":"s020","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "backslash: PostToolUse read of work\_untrusted records leg" 0 hook guard.sh "$(file_json_py Read 'C:\repo\work\_untrusted\x.md' PostToolUse)"
expect_contains "backslash: legs file has untrusted_content" "untrusted_content" "$(cat "$S/legs/s020" 2>/dev/null)"
rm -f "$S/current_step.json"
# ---- agent_type: the reviewer and security subagents are read-only and may run only inspection, test and eval commands ----
# Fixtures carry a top-level agent_type (absent = lead). Built with json.dumps so $(...) and quotes survive.
agent_bash_json() { "$QS_PYTHON" -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","tool_name":"Bash","agent_type":sys.argv[1],"tool_input":{"command":sys.argv[2]}}))' "$1" "$2"; }
agent_file_json() { "$QS_PYTHON" -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","tool_name":sys.argv[2],"agent_type":sys.argv[1],"tool_input":{"file_path":sys.argv[3]}}))' "$1" "$2" "$3"; }
echo act > "$S/tier"
# allowed: inspection, the criteria checker, the project's test/eval runners, the gate, and reviewer.md's own diffbase line
for cmd in "git diff --stat" "python3 scripts/check_criteria.py" "uv run localrag eval --min 12" "npm test" "pytest -q" "bash scripts/gate.sh" \
           'base=$(bash scripts/diffbase.sh)' "git status --porcelain && git diff" "npm run lint" "cargo test"; do
  expect_exit "reviewer allowed: $cmd" 0 hook guard.sh "$(agent_bash_json reviewer "$cmd")"
done
# denied: anything that changes git state, installs, writes through a redirect or tee
for cmd in "git commit -m x" "git add -A" "npm install x" "cat a > b" "gh pr create --title x" "git diff | tee out.txt" \
           "git -C .claude/worktrees/x merge feat/y" "git status && git checkout -b feat/z"; do
  expect_exit "reviewer denied: $cmd" 2 hook guard.sh "$(agent_bash_json reviewer "$cmd")"
done
expect_exit "reviewer denied: git commit reason" 2 hook guard.sh "$(agent_bash_json reviewer "git commit -m x")"
expect_contains "reviewer git deny reason" "reviewer never changes git state" "$OUT"
expect_exit "reviewer denied: gh reason" 2 hook guard.sh "$(agent_bash_json reviewer "gh pr create --title x")"
expect_contains "reviewer gh deny reason" "reviewer never changes git state" "$OUT"
expect_exit "reviewer denied: npm install reason" 2 hook guard.sh "$(agent_bash_json reviewer "npm install x")"
expect_contains "reviewer bash deny reason names the subcommand" "reviewer may only run read-only, test and eval commands (npm install x)" "$OUT"
expect_exit "reviewer denied: Write src/a.ts" 2 hook guard.sh "$(agent_file_json reviewer Write "src/a.ts")"
expect_contains "reviewer write deny reason" "reviewer is read-only" "$OUT"
expect_exit "reviewer denied: Edit docs/REPORT.md" 2 hook guard.sh "$(agent_file_json reviewer Edit "docs/REPORT.md")"
expect_exit "reviewer denied: MultiEdit docs/plan.md" 2 hook guard.sh "$(agent_file_json reviewer MultiEdit "docs/plan.md")"
expect_exit "reviewer allowed: Read src/a.ts" 0 hook guard.sh "$(agent_file_json reviewer Read "src/a.ts")"
# hard rules run first and still apply to the reviewer, with the hard-rule reason
expect_exit "reviewer: force push still denied by hard rules" 2 hook guard.sh "$(agent_bash_json reviewer "git push --force origin main")"
expect_contains "reviewer: force push reason is the hard rule" "force push" "$OUT"
# a brief success criterion of kind test is allowed verbatim; nothing else under tools/ is, and args may not be added
"$QS_PYTHON" -c 'import json,sys; json.dump({"success_criteria":[{"kind":"test","cmd":"bash tools/verify.sh --strict","expect":0},{"kind":"file","path":"README.md"},{"kind":"test","cmd":"","expect":0}]}, open(sys.argv[1],"w"))' "$S/brief.json"
expect_exit "reviewer allowed: brief test criterion verbatim" 0 hook guard.sh "$(agent_bash_json reviewer "bash tools/verify.sh --strict")"
expect_exit "reviewer allowed: brief criterion chained with inspection" 0 hook guard.sh "$(agent_bash_json reviewer "bash tools/verify.sh --strict && git status")"
expect_exit "reviewer denied: non-criterion script" 2 hook guard.sh "$(agent_bash_json reviewer "bash tools/other.sh")"
expect_exit "reviewer denied: criterion with extra args" 2 hook guard.sh "$(agent_bash_json reviewer "bash tools/verify.sh --strict --fix")"
rm -f "$S/brief.json"
expect_exit "reviewer denied: criterion command once the brief is gone" 2 hook guard.sh "$(agent_bash_json reviewer "bash tools/verify.sh --strict")"
# the security subagent gets the same read-only clause, with deny reasons naming it
expect_exit "security denied: Write src/a.ts" 2 hook guard.sh "$(agent_file_json security Write "src/a.ts")"
expect_contains "security write deny reason" "security is read-only" "$OUT"
expect_exit "security denied: git commit -m x" 2 hook guard.sh "$(agent_bash_json security "git commit -m x")"
expect_contains "security git deny reason" "security never changes git state" "$OUT"
expect_exit "security denied: npm install x" 2 hook guard.sh "$(agent_bash_json security "npm install x")"
expect_contains "security bash deny reason" "security may only run read-only, test and eval commands (npm install x)" "$OUT"
expect_exit "security allowed: git diff main...HEAD" 0 hook guard.sh "$(agent_bash_json security "git diff main...HEAD")"
expect_exit "security allowed: pytest -q" 0 hook guard.sh "$(agent_bash_json security "pytest -q")"
"$QS_PYTHON" -c 'import json,sys; json.dump({"success_criteria":[{"kind":"test","cmd":"bash tools/verify.sh --strict","expect":0}]}, open(sys.argv[1],"w"))' "$S/brief.json"
expect_exit "security allowed: brief test criterion verbatim" 0 hook guard.sh "$(agent_bash_json security "bash tools/verify.sh --strict")"
expect_exit "security denied: criterion with extra args" 2 hook guard.sh "$(agent_bash_json security "bash tools/verify.sh --strict --fix")"
rm -f "$S/brief.json"
# an unquoted newline separates commands exactly like ';' (outside a controller run, as on main): the second line is
# checked as its own subcommand, so it can neither ride on an allowed first line nor hide behind one
[ ! -f "$S/controller.json" ] && ok "newline: these cases run outside a controller run" || bad "newline: controller.json present"
for cmd in $'ls\nrm -f src/app.py' $'ls\npython -c "print(1)"' $'cat x\nnpm publish'; do
  expect_exit "reviewer denied: newline-joined ${cmd//$'\n'/\\n}" 2 hook guard.sh "$(agent_bash_json reviewer "$cmd")"
done
expect_exit "reviewer allowed: newline-joined read-only lines" 0 hook guard.sh "$(agent_bash_json reviewer $'git status\ngit diff')"
expect_exit "reviewer allowed: newline after && is a continuation" 0 hook guard.sh "$(agent_bash_json reviewer $'git status &&\ngit diff')"
expect_exit "reviewer allowed: quoted newline stays inside its argument" 0 hook guard.sh "$(agent_bash_json reviewer $'grep -n \'a\nb\' README.md')"
expect_exit "newline: cd before git denied" 2 hook guard.sh "$(agent_bash_json worker $'cd sub\ngit status')"
expect_contains "newline: cd before git reason names git -C" "git -C" "$OUT"
expect_exit "newline: force push on a second line denied" 2 hook guard.sh "$(agent_bash_json worker $'ls\ngit push --force origin feat/y')"
# the lead (no agent_type) and other subagents are unaffected
expect_exit "lead: npm install allowed in act tier" 0 hook guard.sh "$(bash_json "npm install x")"
expect_exit "lead: Write src allowed in act tier" 0 hook guard.sh "$(file_json Write "src/a.ts")"
expect_exit "worker: npm install allowed in act tier" 0 hook guard.sh "$(agent_bash_json worker "npm install x")"
rm -f "$S/tier"
# ---- overseer role: may write only its note and the two flag files; bash only its status scripts and read-only commands ----
ov_hook() { printf '%s' "$1" | env QS_ROLE=overseer bash "$ROOT/scripts/hooks/guard.sh"; }
for f in "docs/overseer.md" ".claude/state/cancel" ".claude/state/force_replan" "/repo/.claude/state/cancel"; do
  expect_exit "overseer write allowed: $f" 0 ov_hook "$(file_json Write "$f")"
done
expect_exit "overseer edit allowed: backslash docs\overseer.md" 0 ov_hook "$(file_json_py Edit 'C:\repo\docs\overseer.md')"
for f in "docs/REPORT.md" "src/a.ts" "docs/RUN_STATE" ".claude/state/tier" "xdocs/overseer.md"; do
  expect_exit "overseer write denied: $f" 2 ov_hook "$(file_json Write "$f")"
done
expect_contains "overseer write deny reason" "overseer may only write docs/overseer.md and the two flag files" "$OUT"
for cmd in "python3 scripts/overseer_status.py" "python scripts/budget.py --exhausted-only" "git status --short" "tail -n 20 docs/ledgers/progress.jsonl" "git log --oneline | head -3"; do
  expect_exit "overseer bash allowed: $cmd" 0 ov_hook "$(bash_json "$cmd")"
done
for cmd in "npm test" "git status && npm install" "rm -r build" "python3 scripts/other.py" "git checkout -b feat/z" "ls\ntouch src/a.py"; do
  expect_exit "overseer bash denied: $cmd" 2 ov_hook "$(bash_json "$cmd")"
done
expect_exit "overseer read allowed" 0 ov_hook "$(file_json Read "docs/ledgers/task.json")"
ov_hook "$(bash_json "git status --short")" >/dev/null 2>&1
expect_contains "overseer calls are logged" $'Bash\tgit status --short' "$(tail -n 1 "$S/hook_log")"
# ---- MCP tools: write-side GitHub tools are an outbound leg, read-side ones an untrusted_content leg, the rest none ----
mcp_json() { "$QS_PYTHON" -c 'import json,sys; print(json.dumps({"hook_event_name":sys.argv[1],"tool_name":sys.argv[2],"tool_input":json.loads(sys.argv[3]),"tool_response":{}}))' "$1" "$2" "$3"; }
pr_input='{"owner":"o","repo":"r","head":"mission/x","base":"main","title":"t"}'
get_input='{"owner":"o","repo":"r","path":"README.md"}'
printf '{"id":"s030","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "mcp: PostToolUse create_pull_request records leg" 0 hook guard.sh "$(mcp_json PostToolUse mcp__github__create_pull_request "$pr_input")"
expect_contains "mcp: legs file has outbound" "outbound" "$(cat "$S/legs/s030" 2>/dev/null)"
expect_exit "mcp: web fetch after mcp PR denied" 2 hook guard.sh "$(web_json PreToolUse)"
expect_exit "mcp: get_file_contents after mcp PR denied" 2 hook guard.sh "$(mcp_json PreToolUse mcp__github__get_file_contents "$get_input")"
expect_exit "mcp: unrelated mcp tool after mcp PR allowed" 0 hook guard.sh "$(mcp_json PreToolUse mcp__other__do_thing '{"x":1}')"
printf '{"id":"s031","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "mcp: PostToolUse get_file_contents records leg" 0 hook guard.sh "$(mcp_json PostToolUse mcp__github__get_file_contents "$get_input")"
expect_contains "mcp: legs file has untrusted_content" "untrusted_content" "$(cat "$S/legs/s031" 2>/dev/null)"
expect_exit "mcp: git push after mcp read denied" 2 hook guard.sh "$(bash_json "git push origin mission/x")"
expect_exit "mcp: create_pull_request after mcp read denied" 2 hook guard.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$pr_input")"
expect_exit "mcp: list_issues after mcp read allowed (same leg)" 0 hook guard.sh "$(mcp_json PreToolUse mcp__github__list_issues '{"owner":"o","repo":"r"}')"
printf '{"id":"s032","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "mcp: PostToolUse unrelated tool records nothing" 0 hook guard.sh "$(mcp_json PostToolUse mcp__other__do_thing '{"x":1}')"
if [ ! -s "$S/legs/s032" ]; then ok "mcp: no leg recorded for unrelated tool"; else bad "mcp: leg recorded for unrelated tool: $(cat "$S/legs/s032")"; fi
expect_exit "mcp: create_pull_request in a fresh step allowed" 0 hook guard.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$pr_input")"
expect_contains "mcp: hook_log arg is the head/base/title summary" $'mcp__github__create_pull_request\thead=mission/x base=main title=t' "$(tail -n 1 "$S/hook_log")"
rm -f "$S/current_step.json"
# ---- a controller.json that is not schema 3 is not a controller run (runtime.active(), as policy.py and the runner
# decide): it must not switch off the trifecta legs or the state-file write limits outside a run ----
printf '{}' > "$S/controller.json"
printf '{"id":"s040","slug":"x","legs":[]}' > "$S/current_step.json"
expect_exit "{} controller.json: PostToolUse WebFetch records leg" 0 hook guard.sh "$(web_json PostToolUse)"
expect_contains "{} controller.json: legs file has untrusted_content" "untrusted_content" "$(cat "$S/legs/s040" 2>/dev/null)"
printf '{"id":"s041","slug":"x","legs":["untrusted_content"]}' > "$S/current_step.json"
expect_exit "{} controller.json: push after untrusted content denied" 2 hook guard.sh "$(bash_json "git push origin mission/x")"
expect_contains "{} controller.json: trifecta reason" "trifecta" "$OUT"
expect_exit "{} controller.json: overseer may not write .claude/state/tier" 2 ov_hook "$(file_json Write ".claude/state/tier")"
expect_exit "{} controller.json: researcher may not write .claude/state" 2 hook guard.sh "$(agent_file_json researcher Write ".claude/state/current_step.json")"
expect_exit "{} controller.json: reviewer may not write .claude/state" 2 hook guard.sh "$(agent_file_json reviewer Write ".claude/state/current_step.json")"
rm -f "$S/controller.json" "$S/current_step.json"
# ---- DENY log: a denied call leaves exactly one five-field line  <utc ts>\tDENY\t<tool>\t<reason>\t<arg> ----
before="$(wc -l < "$S/hook_log")"
hook guard.sh "$(bash_json "git push origin main")" >/dev/null 2>&1
after="$(wc -l < "$S/hook_log")"; last="$(tail -n 1 "$S/hook_log")"
if [ "$((after - before))" = 1 ]; then ok "deny log: one line per denied call"; else bad "deny log: $((after - before)) lines for one denied call"; fi
expect_contains "deny log: DENY marker, tool, reason and arg" $'\tDENY\tBash\tpush to main\tgit push origin main' "$last"
if [[ "$last" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'\t' ]]; then ok "deny log: starts with a utc timestamp"; else bad "deny log: no timestamp: $last"; fi
if [ "$(printf '%s\n' "$last" | awk -F'\t' '{print NF}')" = 5 ]; then ok "deny log: five tab-separated fields"; else bad "deny log: field count: $last"; fi
hook guard.sh "$(bash_json "git status")" >/dev/null 2>&1
if [ "$(tail -n 1 "$S/hook_log" | awk -F'\t' '{print NF}')" = 3 ]; then ok "deny log: allowed call keeps three fields"; else bad "deny log: allowed line: $(tail -n 1 "$S/hook_log")"; fi
hook guard.sh "$(agent_file_json reviewer Write "src/a.ts")" >/dev/null 2>&1
expect_contains "deny log: reviewer deny recorded" $'\tDENY\tWrite\treviewer is read-only\tsrc/a.ts' "$(tail -n 1 "$S/hook_log")"
finish
