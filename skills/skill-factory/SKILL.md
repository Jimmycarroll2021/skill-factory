---
name: skill-factory
description: "Turns a plain-language description into a validated, tested and versioned agent skill for Claude Code or Codex, using the skillz CLI. Use when the user asks to create a skill, make a skill for a task, test or evaluate a skill, package a skill, or install a skill for Claude Code or Codex. Also covers listing installed skills and rolling back to an earlier version. Never approves a version without the human's explicit say-so."
license: MIT
---

# skill-factory

Drive the `skillz` CLI to build a skill from a description, prove it, and install it. Run
commands from the project root, the directory that contains `skills/`. Use `--out <dir>` to
point at a different root.

## Create and check a skill

1. Run `skillz run "<paragraph describing the capability>"`. The source can also be a path to
   a `.txt`, `.yaml`, `.yml` or `.json` spec. Add `--name <kebab-case>` to choose the name and
   `--force` to regenerate an existing skill.
2. Read `skills/<name>/.factory/pipeline.md`. It gives one verdict per stage: capture,
   generate, validate, render, evaluate, regression, package and approval.
3. Report the verdicts to the human in your own words. Say that offline grading is a
   heuristic lexical-overlap signal, not proof that a model will trigger the skill.
4. If the run stopped (exit 1), show the failing stage from the pipeline report. Fix the
   canonical `skills/<name>/SKILL.md` or the spec, then run again with `--force`.

Add `--runner claude` or `--runner codex` only if the human wants a with/without-skill
baseline and that CLI is installed. Without it the baseline is "skipped: no runner".

## Approval is the human's decision

A packaged version is installable only after `skillz approve <name> <version>`. Never run
approve on your own initiative and never infer consent from silence or from a general
instruction such as "go ahead". Ask the human to review `pipeline.md` and wait for an
explicit instruction to approve that skill and that version.

## After the human approves

1. `skillz approve <name> <version>`
2. `skillz install <name> --platform claude|codex --scope project|user`
   (add `--version X.Y.Z` to pick one; the default is the highest approved)
3. `skillz list` shows the registry and the version history.
4. `skillz rollback <name>` reinstalls the previous approved version; `--platform` and
   `--scope` narrow it.

Claude Code installs to `.claude/skills/<name>/` (project) or `~/.claude/skills/<name>/`
(user). Codex installs to `.agents/skills/<name>/` with `agents/openai.yaml`.

## Other commands

- `skillz validate <name>` static checks; `skillz render <name>` builds the two variants.
- `skillz eval <name> [--strict]` regenerates trigger and behaviour cases and grades them.
- `skillz package <name> [--minor|--major]` snapshots a new immutable version.

## Never

- Edit files under `.factory/versions/`; versions are immutable and checksummed.
- Edit an installed copy; change `skills/<name>/` and package a new version instead.
- Run `git push` or publish a skill anywhere. The factory works locally only.
