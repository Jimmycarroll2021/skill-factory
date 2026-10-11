#!/usr/bin/env bash
# Agent prompts carry the shell discipline that keeps headless runs out of Claude Code's permission prompts:
# proof mission 4 lost ten minutes to compound commands, exit-code echoes, redirects into /tmp and absolute
# script paths, none of which match the relative allow rules passed via --allowedTools.
source "$(dirname "$0")/lib.sh"
for a in worker reviewer security; do
  P="$(cat "$ROOT/.claude/agents/$a.md")"
  expect_contains "$a: one command per Bash call" "One command per Bash call" "$P"
  expect_contains "$a: relative paths" "relative" "$P"
  expect_contains "$a: no redirects outside the repo" "/tmp" "$P"
  expect_contains "$a: no exit-code echoes" "PIPESTATUS" "$P"
done
# QA: the security reviewer is read-only by frontmatter; workers ship tests with behaviour; the reviewer fails code without one.
S_MD="$ROOT/.claude/agents/security.md"
if [ -f "$S_MD" ]; then ok "security.md exists"; else bad "security.md exists"; fi
expect_contains "security: disallows Write and Edit" "disallowedTools: Write, Edit" "$(grep '^disallowedTools:' "$S_MD" 2>/dev/null)"
expect_contains "worker: test that fails without it" "test that fails without it" "$(cat "$ROOT/.claude/agents/worker.md")"
expect_contains "reviewer: behaviour change without a test fails" "without a test" "$(cat "$ROOT/.claude/agents/reviewer.md")"
# tests are immutable from the agent's side: the worker may not weaken one, the reviewer fails a diff that does
expect_contains "worker: never weaken an existing test" "Never delete, skip or weaken an existing test" "$(cat "$ROOT/.claude/agents/worker.md")"
expect_contains "reviewer: weakened test is a FAIL item" "existing test weakened" "$(cat "$ROOT/.claude/agents/reviewer.md")"
expect_contains "reviewer: a found defect is a FAIL item" "A defect you found is a FAIL item" "$(cat "$ROOT/.claude/agents/reviewer.md")"
# taste is decided once in the PRD for UI products
expect_contains "strategist: design language in the PRD" "Design language" "$(cat "$ROOT/.claude/agents/strategist.md")"
expect_contains "PRD template: design language section" "## Design language" "$(cat "$ROOT/docs/PRD_TEMPLATE.md")"
# the planner is ambitious at product altitude and leaves the path to the workers; each task's done-when is a check, not prose
expect_contains "strategist: product vision section" "Product vision" "$(cat "$ROOT/.claude/agents/strategist.md")"
expect_contains "strategist: stays at product altitude" "high-level technical design" "$(cat "$ROOT/.claude/agents/strategist.md")"
expect_contains "PRD template: product vision section" "## Product vision" "$(cat "$ROOT/docs/PRD_TEMPLATE.md")"
expect_contains "planner: done-when is a command or a criterion" 'criterion <n>' "$(cat "$ROOT/.claude/agents/planner.md")"
expect_contains "planner: outcome not implementation steps" "not as implementation steps" "$(cat "$ROOT/.claude/agents/planner.md")"
expect_contains "reviewer: grades each merged task's done-when" "done-when not met" "$(cat "$ROOT/.claude/agents/reviewer.md")"
# the researcher reads untrusted web content (lethal-trifecta rule): it has no shell by frontmatter, in every mode
R_MD="$ROOT/.claude/agents/researcher.md"
case "$(grep '^tools:' "$R_MD")" in *Bash*) bad "researcher: tools has no Bash";; *) ok "researcher: tools has no Bash";; esac
expect_contains "researcher: disallows Bash" "Bash" "$(grep '^disallowedTools:' "$R_MD")"
expect_contains "researcher: binds without a shell" "step-bind <id>" "$(cat "$R_MD")"
expect_contains "lead loop carries the same rule" "One command per Bash call" "$(cat "$ROOT/CLAUDE.md")"
finish
