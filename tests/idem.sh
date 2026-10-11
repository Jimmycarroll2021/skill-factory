#!/usr/bin/env bash
# PreToolUse/PostToolUse idempotency guard for `git push`, `gh pr|issue|release create` and the
# MCP `mcp__<server>__create_pull_request|create_issue|create_release` tools.
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"; mkdir -p "$CLAUDE_PROJECT_DIR/.claude/state"
S="$CLAUDE_PROJECT_DIR/.claude/state"

# JSON-encode the command so commands containing quotes/newlines still produce valid JSON.
pre_json() {
  local cmd_json
  cmd_json="$(printf '%s' "$1" | "$QS_PYTHON" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":%s}}' "$cmd_json"
}
post_json() {
  local cmd_json
  cmd_json="$(printf '%s' "$1" | "$QS_PYTHON" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  printf '{"hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":%s},"tool_response":{"exit_code":%s}}' "$cmd_json" "$2"
}
# assert_last_line <label> <expected cmd> <expected step> -- checks the most recently
# appended idem.jsonl line has exactly these normalised fields (catches field-shift bugs).
assert_last_line() {
  local label=$1 want_cmd=$2 want_step=$3
  OUT="$("$QS_PYTHON" -c '
import json, sys

path, want_cmd, want_step = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    lines = [l for l in f if l.strip()]
last = json.loads(lines[-1])
if last.get("cmd") == want_cmd and last.get("step") == want_step:
    print("OK")
else:
    print("MISMATCH cmd=%r step=%r" % (last.get("cmd"), last.get("step")))
' "$S/idem.jsonl" "$want_cmd" "$want_step" 2>&1)"
  case "$OUT" in
    OK) ok "$label" ;;
    *) bad "$label ($OUT)" ;;
  esac
}

# non-matching command is a pure no-op, no ledger created
expect_exit "non-matching command allowed" 0 hook idem.sh "$(pre_json "npm test")"
[ -f "$S/idem.jsonl" ] && bad "idem.jsonl created for non-matching command" || ok "idem.jsonl not created for non-matching command"

# matching command with a current step: PreToolUse allows and records pending
printf '{"id":"s007"}' > "$S/current_step.json"
expect_exit "first gh pr create allowed" 0 hook idem.sh "$(pre_json "gh pr create --title x")"
expect_contains "pending line has step s007" '"step": "s007"' "$(cat "$S/idem.jsonl")"
expect_contains "pending line has status pending" '"status": "pending"' "$(cat "$S/idem.jsonl")"

# PostToolUse records the done outcome
expect_exit "PostToolUse allowed" 0 hook idem.sh "$(post_json "gh pr create --title x" 0)"
expect_contains "done line recorded" '"status": "done"' "$(cat "$S/idem.jsonl")"

# repeat PreToolUse for the same key is denied
expect_exit "repeat gh pr create denied" 2 hook idem.sh "$(pre_json "gh pr create --title x")"
expect_contains "denial mentions already executed" "already executed" "$OUT"

# extra whitespace normalises to the same key, still denied
expect_exit "extra-space repeat denied" 2 hook idem.sh "$(pre_json "gh  pr   create --title x")"
expect_contains "denial mentions already executed (normalised)" "already executed" "$OUT"

# a new step id changes the key, so it's allowed again
printf '{"id":"s008"}' > "$S/current_step.json"
expect_exit "new step id allowed" 0 hook idem.sh "$(pre_json "gh pr create --title x")"

# git push behaves the same way; also covers no current_step.json -> "nostep"
rm -f "$S/current_step.json"
expect_exit "git push first allowed" 0 hook idem.sh "$(pre_json "git push origin mission/x")"
expect_exit "git push post allowed" 0 hook idem.sh "$(post_json "git push origin mission/x" 0)"
expect_exit "git push repeat denied" 2 hook idem.sh "$(pre_json "git push origin mission/x")"
expect_contains "git push denial mentions already executed" "already executed" "$OUT"
expect_contains "nostep used when current_step.json is absent" '"step": "nostep"' "$(cat "$S/idem.jsonl")"

# fail closed on malformed input
expect_exit "garbage stdin fails closed" 2 hook idem.sh "not json"
expect_exit "empty stdin fails closed" 2 hook idem.sh ""

# --- regression cases: only a subcommand that itself STARTS WITH the target, and text
# inside quotes never triggers a match (bug a); ledger fields never shift (bug b). ---

# the exact bad case from the live run: "git push" and "gh pr create" only appear inside
# a quoted argument to a ledger-append call, so this must not match at all.
ledger_before="$(wc -l < "$S/idem.jsonl" 2>/dev/null || echo 0)"
expect_exit "quoted git push/gh pr create text inside another command does not match" 0 \
  hook idem.sh "$(pre_json 'python scripts/ledger.py append blocked lead "combined git push and gh pr create denied ..."')"
ledger_after="$(wc -l < "$S/idem.jsonl" 2>/dev/null || echo 0)"
[ "$ledger_before" = "$ledger_after" ] && ok "ledger not appended for quoted-text command" || bad "ledger appended for quoted-text command (before=$ledger_before after=$ledger_after)"

# a leading "cd <dir> &&" is its own subcommand; the push subcommand after it still matches,
# and the ledger records the normalised push subcommand, not the whole line.
printf '{"id":"s009"}' > "$S/current_step.json"
expect_exit "cd-prefixed git push matches" 0 hook idem.sh "$(pre_json 'cd .claude/worktrees/x && git push origin mission/x')"
assert_last_line "cd-prefixed push pending line normalises to the push subcommand" "git push origin mission/x" "s009"
expect_exit "cd-prefixed git push post recorded" 0 hook idem.sh "$(post_json 'cd .claude/worktrees/x && git push origin mission/x' 0)"
assert_last_line "cd-prefixed push done line normalises to the push subcommand" "git push origin mission/x" "s009"
expect_exit "cd-prefixed git push repeat denied" 2 hook idem.sh "$(pre_json 'cd .claude/worktrees/x && git push origin mission/x')"
expect_contains "cd-prefixed push denial mentions already executed" "already executed" "$OUT"

# git -C <dir> push is recognised as a push subcommand too.
printf '{"id":"s010"}' > "$S/current_step.json"
expect_exit "git -C <dir> push matches" 0 hook idem.sh "$(pre_json 'git -C .claude/worktrees/x push origin mission/x')"
assert_last_line "git -C push pending line is the full push subcommand" "git -C .claude/worktrees/x push origin mission/x" "s010"

# a command containing a real newline, with "gh pr create" only inside a quoted string,
# must not match -- quoting, not the presence of a newline, decides.
multiline_cmd=$'echo "gh pr create fake\nstill quoted" && npm test'
ledger_before="$(wc -l < "$S/idem.jsonl" 2>/dev/null || echo 0)"
expect_exit "quoted gh pr create spanning a newline does not match" 0 hook idem.sh "$(pre_json "$multiline_cmd")"
ledger_after="$(wc -l < "$S/idem.jsonl" 2>/dev/null || echo 0)"
[ "$ledger_before" = "$ledger_after" ] && ok "ledger not appended for newline/quoted command" || bad "ledger appended for newline/quoted command (before=$ledger_before after=$ledger_after)"

# --- MCP tools: mcp__<server>__create_pull_request|create_issue|create_release are deduped
# on (step id, tool name, head/base/title); anything else from an MCP server, and every
# non-Bash tool, is a pure no-op. The settings.json matcher is widened to `Bash|mcp__.*`,
# so idem.sh must accept any tool_name and exit 0 fast for the ones it does not handle. ---

# mcp_json <event> <tool_name> <tool_input json> [<tool_response json>]
mcp_json() {
  local event=$1 tool=$2 input=$3 resp=${4:-}
  if [ -n "$resp" ]; then
    printf '{"hook_event_name":"%s","tool_name":"%s","tool_input":%s,"tool_response":%s}' "$event" "$tool" "$input" "$resp"
  else
    printf '{"hook_event_name":"%s","tool_name":"%s","tool_input":%s}' "$event" "$tool" "$input"
  fi
}
# ledger_lines -> current line count of idem.jsonl (0 if absent)
ledger_lines() { wc -l < "$S/idem.jsonl" 2>/dev/null || echo 0; }

PR_INPUT='{"owner":"example-owner","repo":"quickship","head":"mission/x","base":"main","title":"t","body":"b"}'
printf '{"id":"s001"}' > "$S/current_step.json"

# first create_pull_request for step s001: allowed, pending line with the tool name + head
expect_exit "mcp create_pull_request first allowed" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$PR_INPUT")"
assert_last_line "mcp pending line cmd/step" "mcp__github__create_pull_request head=mission/x base=main" "s001"
expect_contains "mcp pending line has status pending" '"status": "pending"' "$(tail -n 1 "$S/idem.jsonl")"

# PostToolUse with a normal response records done exit 0
expect_exit "mcp create_pull_request post allowed" 0 hook idem.sh "$(mcp_json PostToolUse mcp__github__create_pull_request "$PR_INPUT" '{"content":[{"type":"text","text":"https://github.com/example-owner/quickship/pull/1"}]}')"
expect_contains "mcp done line recorded" '"status": "done"' "$(tail -n 1 "$S/idem.jsonl")"
expect_contains "mcp done line exit 0" '"exit": 0' "$(tail -n 1 "$S/idem.jsonl")"

# a repeat with the same head/base/title in the same step is denied
expect_exit "mcp create_pull_request repeat denied" 2 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$PR_INPUT")"
expect_contains "mcp denial mentions already executed" "already executed" "$OUT"

# the body is not part of the key: a changed body with the same head/base/title is still denied
expect_exit "mcp repeat with different body still denied" 2 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request '{"head":"mission/x","base":"main","title":"t","body":"other"}')"

# a different head is a different key: allowed
expect_exit "mcp different head allowed" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request '{"head":"mission/y","base":"main","title":"t"}')"
assert_last_line "mcp different-head pending line" "mcp__github__create_pull_request head=mission/y base=main" "s001"

# a different step id with the same input is a different key: allowed
printf '{"id":"s002"}' > "$S/current_step.json"
expect_exit "mcp different step allowed" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$PR_INPUT")"
assert_last_line "mcp different-step pending line" "mcp__github__create_pull_request head=mission/x base=main" "s002"

# PostToolUse with isError true records done with exit 1. The contract is that a `done`
# key is denied regardless of its exit code (the recorded result stands, the lead must
# not retry), so the next PreToolUse for the same key is still denied.
expect_exit "mcp post with isError recorded" 0 hook idem.sh "$(mcp_json PostToolUse mcp__github__create_pull_request "$PR_INPUT" '{"isError":true,"content":[{"type":"text","text":"422 A pull request already exists"}]}')"
expect_contains "mcp isError done line has exit 1" '"exit": 1' "$(tail -n 1 "$S/idem.jsonl")"
expect_contains "mcp isError done line has status done" '"status": "done"' "$(tail -n 1 "$S/idem.jsonl")"
expect_exit "mcp repeat after isError still denied" 2 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request "$PR_INPUT")"
expect_contains "mcp isError denial mentions already executed" "already executed" "$OUT"
expect_contains "mcp isError denial reports exit 1" "(exit 1)" "$OUT"

# snake_case is_error is honoured too; create_issue has no head/base, so those render empty
printf '{"id":"s003"}' > "$S/current_step.json"
expect_exit "mcp create_issue first allowed" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_issue '{"title":"bug"}')"
assert_last_line "mcp create_issue pending line (missing head/base -> empty)" "mcp__github__create_issue head= base=" "s003"
expect_exit "mcp post with is_error recorded" 0 hook idem.sh "$(mcp_json PostToolUse mcp__github__create_issue '{"title":"bug"}' '{"is_error":true}')"
expect_contains "mcp is_error done line has exit 1" '"exit": 1' "$(tail -n 1 "$S/idem.jsonl")"
expect_exit "mcp create_issue repeat denied" 2 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_issue '{"title":"bug"}')"

# the server segment is not fixed to github: mcp__other__create_pull_request is handled
expect_exit "mcp__other__create_pull_request handled" 0 hook idem.sh "$(mcp_json PreToolUse mcp__other__create_pull_request "$PR_INPUT")"
assert_last_line "mcp__other pending line" "mcp__other__create_pull_request head=mission/x base=main" "s003"
expect_exit "mcp__other post recorded" 0 hook idem.sh "$(mcp_json PostToolUse mcp__other__create_pull_request "$PR_INPUT" '{"isError":false}')"
expect_contains "mcp__other isError false records exit 0" '"exit": 0' "$(tail -n 1 "$S/idem.jsonl")"
expect_exit "mcp__other repeat denied" 2 hook idem.sh "$(mcp_json PreToolUse mcp__other__create_pull_request "$PR_INPUT")"
expect_exit "mcp__gitlab__create_release handled" 0 hook idem.sh "$(mcp_json PreToolUse mcp__gitlab__create_release '{"tag":"v1"}')"
assert_last_line "mcp create_release pending line" "mcp__gitlab__create_release head= base=" "s003"

# a read-only MCP tool is a no-op: exit 0, no new ledger line
before="$(ledger_lines)"
expect_exit "mcp get_file_contents is a no-op" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__get_file_contents '{"path":"README.md"}')"
expect_exit "mcp get_file_contents post is a no-op" 0 hook idem.sh "$(mcp_json PostToolUse mcp__github__get_file_contents '{"path":"README.md"}' '{"content":[]}')"
[ "$before" = "$(ledger_lines)" ] && ok "ledger not appended for read-only mcp tool" || bad "ledger appended for read-only mcp tool (before=$before after=$(ledger_lines))"

# the suffix must be the whole final segment: create_pull_request_review / update_pull_request are not matched
before="$(ledger_lines)"
expect_exit "mcp create_pull_request_review is a no-op" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request_review '{"pull_number":1}')"
expect_exit "mcp update_pull_request is a no-op" 0 hook idem.sh "$(mcp_json PreToolUse mcp__github__update_pull_request "$PR_INPUT")"
[ "$before" = "$(ledger_lines)" ] && ok "ledger not appended for near-miss mcp tool names" || bad "ledger appended for near-miss mcp tool names (before=$before after=$(ledger_lines))"

# a non-Bash, non-MCP tool is a no-op: exit 0, no new ledger line
before="$(ledger_lines)"
expect_exit "Write tool is a no-op" 0 hook idem.sh "$(mcp_json PreToolUse Write '{"file_path":"x.txt","content":"gh pr create"}')"
expect_exit "Read tool is a no-op" 0 hook idem.sh "$(mcp_json PreToolUse Read '{"file_path":"x.txt"}')"
[ "$before" = "$(ledger_lines)" ] && ok "ledger not appended for Write/Read tools" || bad "ledger appended for Write/Read tools (before=$before after=$(ledger_lines))"

# a Bash call that lacks tool_input.command is malformed: fail closed
expect_exit "Bash without command fails closed" 2 hook idem.sh '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{}}'
# an MCP create call whose tool_input is not an object is malformed: fail closed
expect_exit "mcp create with non-object tool_input fails closed" 2 hook idem.sh "$(mcp_json PreToolUse mcp__github__create_pull_request '"nope"')"
# garbage is still refused after the tool_name widening
expect_exit "garbage stdin still fails closed" 2 hook idem.sh "{not json"

# after every match above, no ledger line ever has its cmd/step fields shifted: cmd must
# look like the matched subcommand it came from, and must never equal the step id.
OUT="$("$QS_PYTHON" -c '
import json, re, sys

path = sys.argv[1]
pattern = re.compile(r"^(git push\b|git -C \S+ push\b|gh (pr|issue|release) create\b|mcp__[^_]\S*__(create_pull_request|create_issue|create_release) head=\S* base=\S*$)")
bad_lines = []
with open(path, encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cmd = obj.get("cmd", "")
        step = obj.get("step", "")
        if cmd == step:
            bad_lines.append("line %d: cmd equals step (%r)" % (i, cmd))
        elif not pattern.match(cmd):
            bad_lines.append("line %d: cmd does not look like a matched subcommand: %r" % (i, cmd))
if bad_lines:
    print("BAD " + "; ".join(bad_lines))
else:
    print("OK")
' "$S/idem.jsonl" 2>&1)"
case "$OUT" in
  OK) ok "every ledger line cmd is the matched subcommand, never the step id" ;;
  *) bad "ledger field-shift check ($OUT)" ;;
esac

finish
