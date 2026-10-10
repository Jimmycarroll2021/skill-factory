#!/usr/bin/env bash
# gate.sh must test the tree it is run from (worktree), scan untracked files, and pass on a clean repo.
source "$(dirname "$0")/lib.sh"
mk() { # mk <dir>: fresh git repo with a copy of scripts/gate.sh and an initial commit
  git init -q -b main "$1" && cp -r "$ROOT/scripts" "$1/" && (cd "$1" && git add -A && git -c user.email=t@t -c user.name=t commit -qm init)
}
# Fixture projects explicitly skip non-test checks; missing checks are separately tested by hardening.py.
seed_quality() {
  printf 'quality:\n  lint:\n    skip: "fixture has no linter"\n  build:\n    skip: "fixture has no build"\n  test: "%s"\n' "$2" > "$1/BRIEF.yaml"
}
# The failing test exists ONLY in the worktree. With the old root resolution the gate silently checks main and passes.
main="$(tmpdir)"; mk "$main"; seed_quality "$main" "npm test"
( cd "$main" && git worktree add -q .claude/worktrees/wt -b main--wt \
  && cd .claude/worktrees/wt && printf '{"name":"x","scripts":{"test":"exit 1"}}' > package.json && mkdir -p node_modules \
  && git add package.json && git -c user.email=t@t -c user.name=t commit -qm "failing test" )
OUT="$(cd "$main/.claude/worktrees/wt" && CLAUDE_PROJECT_DIR="$main" bash scripts/gate.sh 2>&1)"; got=$?
[ "$got" = 2 ] && ok "worktree: gate exit 2 with CLAUDE_PROJECT_DIR set to main" || bad "worktree: want exit 2, got $got: $(printf '%s' "$OUT" | tail -n 2)"
expect_contains "worktree: failure names the test step" "test failed" "$OUT"

clean="$(tmpdir)"; mk "$clean"; seed_quality "$clean" "true"
expect_exit "clean repo passes" 0 bash -c "cd '$clean' && bash scripts/gate.sh"
printf '%s%s\n' AKIA ABCDEFGHIJKLMNOP > "$clean/untracked.txt"   # split so this file never matches the scan
OUT="$(cd "$clean" && bash scripts/gate.sh 2>&1)"; got=$?
[ "$got" = 2 ] && ok "untracked secret: exit 2" || bad "untracked secret: want exit 2, got $got"
expect_contains "untracked secret: names the file" "untracked.txt" "$OUT"
rm "$clean/untracked.txt"; printf 'see %s%s\n' sk-ant- api03-abcdefghijklmnopqrstuvwxyz0123 > "$clean/notes.md"
expect_exit "sk-ant key in untracked file: exit 2" 2 bash -c "cd '$clean' && bash scripts/gate.sh"
rm "$clean/notes.md"; echo "the task-list uses sk-slugs-like-this-one-here-ok" > "$clean/prose.md"
expect_exit "prose with sk- prefix is not a secret" 0 bash -c "cd '$clean' && bash scripts/gate.sh"
rm "$clean/prose.md"
# fail closed: the Stop hook execs the gate, and Claude Code lets a stop through on any exit other than 2, so a crash on
# a malformed brief (non-dict quality, non-string command) must be a gate FAIL with exit 2, never exit 1
mkdir -p "$clean/.claude/state"
printf '{"quality":"lint"}' > "$clean/.claude/state/brief.json"
expect_exit "non-dict quality in brief: exit 2" 2 bash -c "cd '$clean' && bash scripts/gate.sh"
expect_contains "non-dict quality in brief: gate FAIL" "gate: FAIL" "$OUT"
printf '{"quality":{"lint":7,"test":"true","build":{"skip":"none"}}}' > "$clean/.claude/state/brief.json"
expect_exit "non-string command in brief: exit 2" 2 bash -c "cd '$clean' && bash scripts/gate.sh"
expect_contains "non-string command in brief: gate FAIL" "gate: FAIL" "$OUT"
rm -f "$clean/.claude/state/brief.json"
expect_exit "missing interpreter: exit 2" 2 bash -c "cd '$clean' && QS_PYTHON=/nonexistent/python bash scripts/gate.sh"
# --- harness self-tests run in the quickship repo itself, not in an installed copy (.quickship/VERSION present):
# they take minutes, test the harness rather than the project, and two of them in parallel (lead + worker) time out
# a 30-minute mission. QS_SELFTEST=1 forces them. The stub leaves a marker file because the gate swallows the
# output of a passing step. ---
inst="$(tmpdir)"; mk "$inst"; seed_quality "$inst" "true"
mkdir -p "$inst/tests"; printf '#!/usr/bin/env bash
touch selftests_ran; exit 0
' > "$inst/tests/run.sh"
(cd "$inst" && bash scripts/gate.sh >/dev/null 2>&1)
[ -f "$inst/selftests_ran" ] && ok "no .quickship: self-tests run" || bad "no .quickship: self-tests did not run"
rm -f "$inst/selftests_ran"; mkdir -p "$inst/.quickship"; echo 0.1.0 > "$inst/.quickship/VERSION"
OUT="$(cd "$inst" && bash scripts/gate.sh 2>&1)"; got=$?
[ "$got" = 0 ] && ok "installed copy: gate passes" || bad "installed copy: gate exit $got"
expect_contains "installed copy: self-tests skipped with a notice" "self-tests skipped" "$OUT"
[ -f "$inst/selftests_ran" ] && bad "installed copy: self-tests must not run" || ok "installed copy: self-tests did not run"
(cd "$inst" && QS_SELFTEST=1 bash scripts/gate.sh >/dev/null 2>&1)
[ -f "$inst/selftests_ran" ] && ok "installed copy: QS_SELFTEST=1 runs them" || bad "installed copy: QS_SELFTEST=1 did not run them"
finish
