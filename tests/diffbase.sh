#!/usr/bin/env bash
# diffbase.sh prints the ref to diff against: origin/main, else main, else the root commit.
source "$(dirname "$0")/lib.sh"
commit() { git -c user.email=t@t -c user.name=t commit -qm "$1" --allow-empty; }
r1="$(tmpdir)"; ( cd "$r1" && git init -q -b main . && commit a && git checkout -q -b feat && commit b )
expect_contains "no remote -> main" "main" "$(cd "$r1" && bash "$ROOT/scripts/diffbase.sh")"
r2="$(tmpdir)"; ( cd "$r2" && git init -q -b feat . && commit a )
root="$(cd "$r2" && git rev-list --max-parents=0 HEAD)"
expect_contains "no main -> root commit" "$root" "$(cd "$r2" && bash "$ROOT/scripts/diffbase.sh")"
bare="$(tmpdir)"; git init -q --bare "$bare"
( cd "$r1" && git remote add origin "$bare" && git push -q origin main )
expect_contains "remote main -> origin/main" "origin/main" "$(cd "$r1" && bash "$ROOT/scripts/diffbase.sh")"
# a chained mission diffs against the branch it was built on (brief.json mission.base), not main, so the reviewer and
# the security reviewer see only this mission's changes
( cd "$r1" && git checkout -q -b mission/first && commit c && git checkout -q -b mission/second && commit d )
mkdir -p "$r1/.claude/state"
printf '{"mission":{"goal":"g","deliverables":["x"],"base":"mission/first"}}' > "$r1/.claude/state/brief.json"
expect_contains "mission.base -> that branch" "mission/first" "$(cd "$r1" && CLAUDE_PROJECT_DIR="$r1" bash "$ROOT/scripts/diffbase.sh")"
( cd "$r1" && git push -q origin mission/first )
expect_contains "mission.base pushed -> origin/<base>" "origin/mission/first" "$(cd "$r1" && CLAUDE_PROJECT_DIR="$r1" bash "$ROOT/scripts/diffbase.sh")"
printf '{"mission":{"goal":"g","deliverables":["x"],"base":"mission/gone"}}' > "$r1/.claude/state/brief.json"
expect_contains "unresolvable base -> falls back to origin/main" "origin/main" "$(cd "$r1" && CLAUDE_PROJECT_DIR="$r1" bash "$ROOT/scripts/diffbase.sh")"
finish
