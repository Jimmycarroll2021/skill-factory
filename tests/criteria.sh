#!/usr/bin/env bash
# check_criteria.py: runs success_criteria from brief.json, writes docs/ledgers/criteria.json.
source "$(dirname "$0")/lib.sh"
export CLAUDE_PROJECT_DIR="$(tmpdir)"
mkdir -p "$CLAUDE_PROJECT_DIR/.claude/state" "$CLAUDE_PROJECT_DIR/docs/ledgers"
CC="$ROOT/scripts/check_criteria.py"
LEDGER="$CLAUDE_PROJECT_DIR/docs/ledgers/criteria.json"
BRIEF="$CLAUDE_PROJECT_DIR/.claude/state/brief.json"

write_brief() { # write_brief <success_criteria json array>
  printf '{"mission":{"goal":"t","deliverables":[]},"success_criteria":%s,"budgets":{"tokens":1,"cost_usd":1,"wall_clock_min":1,"steps":1,"stall_limit":3,"replan_limit":5,"critic_rounds":2},"permissions":{"irreversible":{"default":"skip-and-record","allow":[]}},"ambiguity_policy":"choose-default-and-record"}' "$1" > "$BRIEF"
}

# no brief.json -> exit 2
rm -f "$BRIEF"
expect_exit "no brief.json -> exit 2" 2 "$QS_PYTHON" "$CC"

# test kind: fail case (wrong expect)
write_brief '[{"kind":"test","cmd":"exit 1","expect":0}]'
expect_exit "test fail -> script exit 2" 2 "$QS_PYTHON" "$CC"
expect_contains "criteria.json failed=1" '"failed": 1' "$(cat "$LEDGER")"
expect_contains "criteria.json detail has exit code" "1" "$(cat "$LEDGER")"

# test kind: pass case with custom expect
write_brief '[{"kind":"test","cmd":"exit 3","expect":3}]'
expect_exit "test pass with expect=3 -> exit 0" 0 "$QS_PYTHON" "$CC"
expect_contains "criteria.json passed=1" '"passed": 1' "$(cat "$LEDGER")"

# file kind
echo "hello world" > "$CLAUDE_PROJECT_DIR/f.txt"
write_brief '[{"kind":"file","path":"f.txt","must_contain":"hello"}]'
expect_exit "file must_contain match -> pass" 0 "$QS_PYTHON" "$CC"

write_brief '[{"kind":"file","path":"f.txt","must_contain":"nomatch"}]'
expect_exit "file must_contain no match -> fail" 2 "$QS_PYTHON" "$CC"

write_brief '[{"kind":"file","path":"missing.txt"}]'
expect_exit "file missing -> fail" 2 "$QS_PYTHON" "$CC"

# grep kind
write_brief '[{"kind":"grep","pattern":"hello","path":"f.txt"}]'
expect_exit "grep match -> pass" 0 "$QS_PYTHON" "$CC"

write_brief '[{"kind":"grep","pattern":"nomatch","path":"f.txt"}]'
expect_exit "grep no match -> fail" 2 "$QS_PYTHON" "$CC"

write_brief '[{"kind":"grep","pattern":"x","path":"missing.txt"}]'
expect_exit "grep missing file -> fail" 2 "$QS_PYTHON" "$CC"

# judge kind: deferred, never causes exit 2 by itself
write_brief '[{"kind":"judge","rubric":"looks good"}]'
expect_exit "judge -> deferred, exit 0" 0 "$QS_PYTHON" "$CC"
expect_contains "criteria.json deferred=1" '"deferred": 1' "$(cat "$LEDGER")"
expect_contains "rubric recorded as detail" "looks good" "$(cat "$LEDGER")"

# the reviewer's grade of a judge rubric is recorded with --judge <index> <PASS|FAIL> --evidence "<quoted output>"
write_brief '[{"kind":"test","cmd":"exit 0","expect":0},{"kind":"judge","rubric":"summary says 0 failed"}]'
expect_exit "run before grading" 0 "$QS_PYTHON" "$CC"
expect_exit "judge grade PASS recorded" 0 "$QS_PYTHON" "$CC" --judge 1 PASS --evidence "diffbase.sh: 3 passed, 0 failed"
expect_contains "criteria.json judge pass" '"status": "pass"' "$(cat "$LEDGER")"
expect_contains "criteria.json keeps the evidence" "3 passed, 0 failed" "$(cat "$LEDGER")"
expect_contains "criteria.json passed=2 after grade" '"passed": 2' "$(cat "$LEDGER")"
expect_contains "criteria.json deferred=0 after grade" '"deferred": 0' "$(cat "$LEDGER")"
expect_contains "grade prints the summary" "passed=2 failed=0 deferred=0" "$OUT"
expect_exit "judge grade FAIL recorded, exit 2" 2 "$QS_PYTHON" "$CC" --judge 1 FAIL --evidence "summary line missing"
expect_contains "criteria.json judge fail" '"status": "fail"' "$(cat "$LEDGER")"
expect_contains "criteria.json failed=1 after FAIL grade" '"failed": 1' "$(cat "$LEDGER")"
expect_exit "judge grade on a non-judge index exits 2" 2 "$QS_PYTHON" "$CC" --judge 0 PASS --evidence "x"
expect_exit "judge grade without evidence exits 2" 2 "$QS_PYTHON" "$CC" --judge 1 PASS
expect_exit "judge grade out of range exits 2" 2 "$QS_PYTHON" "$CC" --judge 7 PASS --evidence "x"

# unknown kind -> fail
write_brief '[{"kind":"bogus"}]'
expect_exit "unknown kind -> fail, exit 2" 2 "$QS_PYTHON" "$CC"
expect_contains "criteria.json failed=1 for unknown kind" '"failed": 1' "$(cat "$LEDGER")"

# mix: one pass, one fail, one deferred -> exit 2, summary shows counts
write_brief '[{"kind":"test","cmd":"exit 0","expect":0},{"kind":"test","cmd":"exit 1","expect":0},{"kind":"judge","rubric":"r"}]'
expect_exit "mix with one fail -> exit 2" 2 "$QS_PYTHON" "$CC"
expect_contains "summary line shows counts" "criteria: passed=1 failed=1 deferred=1" "$OUT"

# --cwd resolves relative file/grep paths inside that dir, not root
mkdir -p "$CLAUDE_PROJECT_DIR/sub"
echo "cwd works" > "$CLAUDE_PROJECT_DIR/sub/g.txt"
write_brief '[{"kind":"file","path":"g.txt","must_contain":"cwd works"}]'
expect_exit "--cwd resolves relative file path" 0 "$QS_PYTHON" "$CC" --cwd "$CLAUDE_PROJECT_DIR/sub"

finish
