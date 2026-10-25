---
name: skill-factory
description: "Gets a receipt for an agent skill with the skillz CLI: evaluates, judges, packages and compares a skill with its last approved version, so a human can see its gains and regressions before approving or installing it for Claude Code or Codex. Use when the user asks to test, evaluate, check, package, version, approve or install a skill, to read a skill's receipt, to fix a skill that fired wrongly or failed to fire, or to create a skill from a description. Also covers listing installed skills and rolling back to an earlier version. Never approves a version without the human's explicit say-so. Also use for requests like 'Help me write a SKILL.md file so my agent learns this workflow'. NOT for writing application code or for answering questions unrelated to agent skills."
license: MIT
---

# skill-factory

Drive the `skillz` CLI to get a receipt for a skill, read it to the human, and install the skill
only after the human approves. Run commands from the project root, the directory that contains
`skills/`. Use `--out <dir>` to point at a different root.

## Get a receipt for a skill

The skill lives at `skills/<name>/SKILL.md`, hand-written or generated, with its prompts in
`skills/<name>/evals/evals.json` (entries `{id, prompt, should_trigger, assertions}`).

1. `skillz eval <name>` grades the trigger and behaviour cases offline and writes
   `skills/<name>/.factory/eval.md`. Read it and fix the description if a case fails.
2. `skillz package <name>` writes an immutable version under `.factory/versions/<semver>/` with
   its receipt. Add `--runner claude` or `--runner codex` only if the human wants a model to run
   and judge the behaviour cases and that CLI is installed; without it the judge is
   "skipped: no runner" and the receipt's evidence is `unverified`.
3. `skillz receipt <name>` prints the receipt: evidence level (`unverified`, `self-judged` or
   `verified`), the trigger and behaviour counts, and the gains and regressions against the last
   approved version. Read it to the human in your own words. Say that `unverified` means the
   grading was a lexical-overlap heuristic, not proof that a model will trigger the skill.
4. If package stopped (exit 1) on a regression, show the regressed cases. Fix the canonical
   `skills/<name>/SKILL.md` and package again, or ask the human whether to pass
   `--allow-regression`.

## Approval is the human's decision

A packaged version is installable only after `skillz approve <name> <version>`. Never run
approve on your own initiative and never infer consent from silence or from a general
instruction such as "go ahead". Ask the human to read the receipt and wait for an explicit
instruction to approve that skill and that version. A `regressed` receipt is refused unless the
human gives a reason for `--accept-regressions "<reason>"`; never invent one.

## After the human approves

1. `skillz approve <name> <version>` (with `--accept-regressions "<reason>"` only in the human's words)
2. `skillz install <name> --platform claude|codex --scope project|user`
   (add `--version X.Y.Z` to pick one; the default is the highest approved)
3. `skillz list` shows the registry, the version history and each version's evidence level.
4. `skillz rollback <name>` reinstalls the previous approved version; `--platform` and
   `--scope` narrow it.

Claude Code installs to `.claude/skills/<name>/` (project) or `~/.claude/skills/<name>/`
(user). Codex installs to `.agents/skills/<name>/` with `agents/openai.yaml`.

## Fix a skill that got something wrong

When a skill missed a prompt it should have handled (or fired on one it should not have),
record it and let the challenge decide. Stable files are never changed until a human promotes.

1. `skillz capture-failure <name> --prompt "<what went wrong>" --should-trigger`
   (or `--should-not-trigger`, or `--expect MARKER ...` for a behaviour case). Every later
   eval, package and challenge includes the case.
2. `skillz candidate <name>` copies the stable skill to `skills/<name>/.factory/candidates/<id>/skill/`.
   Edit that copy, never the stable `SKILL.md`.
3. `skillz challenge <name> <id>` validates both sides, grades the union of cases and writes
   `challenge.md`. Verdicts: candidate-unsafe, candidate-worse, candidate-better,
   no-difference. Read it to the human; without `--runner` the grading is the offline heuristic.
4. Never run `skillz promote <name> <id>` on your own initiative. Run it only when the human
   explicitly names that skill and that candidate. It refuses worse or unsafe candidates,
   packages a new version that still waits for `skillz approve`, and exits 1 when refused.

## Create a skill from a description

`skillz run "<paragraph describing the capability>"` captures, generates, validates, renders,
evaluates, compares and packages in one go, then stops at the gate. The source can also be a
`.txt`, `.yaml`, `.yml` or `.json` spec; add `--name <kebab-case>` to choose the name and
`--force` to regenerate. Read `skills/<name>/.factory/pipeline.md`, then continue from
`skillz receipt` above. `skillz validate <name>` and `skillz render <name>` run single stages.

## Never

- Edit files under `.factory/versions/`; versions and receipts are immutable and checksummed.
- Edit an installed copy; change `skills/<name>/` and package a new version instead.
- Run `git push` or publish a skill anywhere. The factory works locally only.
- Report a receipt's evidence as stronger than its `evidence` field says.
