#!/usr/bin/env bash
# scripts/init.sh: one-command bootstrap of the harness into a target project, --force and --upgrade,
# plus the Windows .cmd wrappers. No LLM calls.
# QS_INIT_NESTED=1 skips the "run the installed suite" case so an installed copy cannot recurse forever.
source "$(dirname "$0")/lib.sh"
INIT="$ROOT/scripts/init.sh"
manifest_files() { # expand scripts/manifest.txt globs against the tree in $1
  ( cd "$1" && shopt -s globstar nullglob && while IFS= read -r p || [ -n "$p" ]; do
      p="${p%%#*}"; p="${p//[$'\r\t ']/}"; [ -z "$p" ] && continue
      for f in $p; do [ -f "$f" ] && echo "$f"; done
    done < "$ROOT/scripts/manifest.txt" )
}
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# --- fresh install ---
t="$(tmpdir)/proj"
expect_exit "fresh target exits 0" 0 bash "$INIT" "$t"
expect_contains "fresh install reports adds" "add: scripts/gate.sh" "$OUT"
expect_contains "fresh install prints next steps" "next: edit BRIEF.yaml" "$OUT"
expect_contains "fresh install points at the idea path" "from a raw idea" "$OUT"
missing=""
for f in $(manifest_files "$ROOT"); do [ -f "$t/$f" ] || missing="$missing $f"; done
[ -z "$missing" ] && ok "every manifest file installed" || bad "missing in target:$missing"
[ -d "$t/.git" ] && ok "target is a git repo" || bad "no .git in target"
[ "$(cat "$t/.quickship/VERSION")" = "$(cat "$ROOT/VERSION")" ] && ok ".quickship/VERSION matches source" || bad ".quickship/VERSION mismatch"
expect_contains "manifest.sha256 records diffbase" "  scripts/diffbase.sh" "$(cat "$t/.quickship/manifest.sha256")"
for line in ".claude/worktrees/" ".claude/state/" "work/_untrusted/" "__pycache__/"; do
  grep -qxF -- "$line" "$t/.gitignore" && ok ".gitignore has $line" || bad ".gitignore lacks $line"
done
# the harness's own imports write bytecode under scripts/__pycache__; it must not surface as untracked project files
(cd "$t" && bash scripts/gate.sh >/dev/null 2>&1)   # quality.py imports runtime and policy
pyc="$(git -C "$t" ls-files --others --exclude-standard | grep -c '\.pyc$')"
[ "$pyc" = 0 ] && ok "bytecode written by a harness command is ignored" || bad "bytecode surfaces as $pyc untracked .pyc file(s)"
[ -f "$t/docs/decisions.md" ] && ok "docs/decisions.md created" || bad "docs/decisions.md missing"
expect_contains "decisions.md has the table header" "| date | task | change | why |" "$(cat "$t/docs/decisions.md")"
[ -f "$t/BRIEF.yaml" ] && ok "BRIEF.yaml created from example" || bad "BRIEF.yaml missing"
expect_contains "run.cmd installed" "bash.exe" "$(cat "$t/run.cmd")"

# --- the installed copy's own suite passes (about a minute; skipped when nested) ---
if [ -z "${QS_INIT_NESTED:-}" ]; then
  expect_exit "installed suite passes in the target" 0 env QS_INIT_NESTED=1 bash -c "cd '$t' && bash tests/run.sh"
fi

# --- idempotent second run ---
expect_exit "second run exits 0" 0 bash "$INIT" "$t"
expect_contains "second run ends with unchanged" "unchanged" "$(printf '%s' "$OUT" | tail -n 1)"

# --- a locally modified file is skipped, --force restores it ---
echo "# local edit" >> "$t/scripts/diffbase.sh"
expect_exit "third run exits 0" 0 bash "$INIT" "$t"
expect_contains "modified file is skipped" "skip (modified): scripts/diffbase.sh" "$OUT"
grep -q "# local edit" "$t/scripts/diffbase.sh" && ok "modified file left alone" || bad "modified file was overwritten"
expect_exit "--force exits 0" 0 bash "$INIT" "$t" --force
[ "$(sha "$t/scripts/diffbase.sh")" = "$(sha "$ROOT/scripts/diffbase.sh")" ] && ok "--force restores the file" || bad "--force did not restore"

# --- upgrade from a newer harness: untouched files refresh, modified files are kept ---
echo "# local edit" >> "$t/scripts/diffbase.sh"
new="$(tmpdir)/harness"
expect_exit "stage a newer harness copy" 0 bash "$INIT" "$new"
echo 9.9.9 > "$new/VERSION"
echo "# v9 change" >> "$new/scripts/diffbase.sh"
echo "# v9 change" >> "$new/tests/diffbase.sh"
expect_exit "--upgrade exits 0" 0 bash "$new/scripts/init.sh" "$t" --upgrade
expect_contains "untouched file is upgraded" "upgrade: tests/diffbase.sh" "$OUT"
expect_contains "modified file is kept on upgrade" "skip (modified): scripts/diffbase.sh" "$OUT"
grep -q "# v9 change" "$t/tests/diffbase.sh" && ok "upgraded file has new content" || bad "tests/diffbase.sh not refreshed"
grep -q "# local edit" "$t/scripts/diffbase.sh" && ok "modified file survives upgrade" || bad "scripts/diffbase.sh clobbered"
[ "$(cat "$t/.quickship/VERSION")" = "$(cat "$ROOT/VERSION")" ] && ok "incomplete upgrade retains installed version" || bad "incomplete upgrade falsely bumped version"
[ -f "$t/.quickship/conflicts" ] && ok "upgrade records unresolved conflicts" || bad "upgrade lost conflicts"

# --- CLAUDE.md is never overwritten ---
echo "# my own rules" > "$t/CLAUDE.md"
expect_exit "run with differing CLAUDE.md exits 0" 0 bash "$INIT" "$t" --force
expect_contains "CLAUDE.md notice printed" 'notice: CLAUDE.md exists; merge the "PR sizing and evidence", "Hard rules" and "Lead loop" sections' "$OUT"
[ "$(cat "$t/CLAUDE.md")" = "# my own rules" ] && ok "CLAUDE.md left alone even with --force" || bad "CLAUDE.md overwritten"

# --- refusals and wrappers ---
expect_exit "source root as target exits 2" 2 bash "$INIT" "$ROOT"
expect_exit "no target exits 2" 2 bash "$INIT"
[ -f "$ROOT/run.cmd" ] && expect_contains "run.cmd finds bash.exe" "bash.exe" "$(cat "$ROOT/run.cmd")" || bad "run.cmd missing"
if [ -z "${QS_INIT_NESTED:-}" ]; then # init.cmd ships with the harness only; the manifest does not install it
  [ -f "$ROOT/init.cmd" ] && expect_contains "init.cmd finds bash.exe" "bash.exe" "$(cat "$ROOT/init.cmd")" || bad "init.cmd missing"
  if command -v cmd.exe >/dev/null 2>&1; then # Windows only: the wrapper finds bash and passes the exit code through
    expect_exit "init.cmd runs init.sh and relays exit 2" 2 cmd.exe //c "$(cygpath -w "$ROOT/init.cmd")"
    expect_contains "init.cmd output comes from init.sh" "usage" "$OUT"
  fi
fi
finish
