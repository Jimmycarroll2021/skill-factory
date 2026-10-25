# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- Generated descriptions now carry the paragraph's "use it when" and "do not use it" sentences.
  Claude reads only the frontmatter description when choosing a skill, so `capture` composes
  them into the description (first trigger and first boundary before any others, each kept
  whole and only if it fits 1,024 characters), and the body no longer repeats them. On the
  README example the evaluation goes from 6 passed, 2 failed to 7 passed, 1 failed.
- The offline grader ignores the description's boundary sentences ("Do not use it for X",
  "Never Y") when collecting trigger words, so stating a boundary cannot make a request about
  X look like a match.
- `skillz improve` extends a description with whole requests ("Also use for requests like
  "..."") instead of a list of loose keywords. A keyword list such as "help, write, md, file"
  makes a skill load on unrelated prompts. If no whole request fits, the first is shortened
  word by word, down to three words. The meta-skill's own keyword list is replaced.
- `generate` no longer writes placeholder `README.md` files into resource directories; an
  empty directory is reported by the validator instead.

### Added

- Validator quality rules, from a study of a 398-skill public collection in which these
  signals separated stronger skills from weaker ones. Errors (the skill cannot work as
  written): `reserved-name` (a name that replaces a bundled Claude Code command, such as
  `verify`), `missing-script` (a fenced command runs a script that does not exist) and
  `script-flag-missing` (a fenced command passes an option the Python script never declares;
  read with `ast`, never executed). Warnings: `description-no-boundary`,
  `description-keyword-list`, `persona-opener`, `brochure-heading`, `phantom-path` (SKILL.md
  names a file that does not exist, which is fine if the skill creates it), `orphan-file` and
  `empty-resource-dir`.

- The model-backed quality tier (mission 07): behaviour cases now carry a task prompt, a rubric
  and expected markers, one per spec behaviour and per trigger. `skillz judge` runs each case
  with and without the skill through a runner, has a judge model score both transcripts 0 to 5
  on instruction following and task quality, and writes `.factory/judge.json` and `judge.md`;
  the judge is a different model from the runner by default when both CLIs are on PATH. The
  judge's JSON reply is parsed strictly and a malformed reply is recorded as a per-case error.
  With `--runner` (and optionally `--judge`), `skillz challenge`, `skillz package` and
  `skillz run` fold the scores into verdicts, version reports and regressions. The lexical tier
  stays the default: nothing calls a model without `--runner`. README documents the six-tier
  ladder.
- The challenger loop (mission 06): `skillz capture-failure` records a failed prompt as a
  persistent eval case that every later eval, package, regression check and challenge includes;
  `skillz improve` builds a candidate under `.factory/candidates/<id>/` without touching stable
  files; `skillz challenge` validates both sides, grades the union of cases and gives a verdict
  (`candidate-unsafe`, `candidate-worse`, `candidate-better`, `no-difference`); `skillz promote`
  is the explicit human command that replaces the canonical files with an acceptable candidate
  and packages a new awaiting-approval version. The pipeline and `skillz run` never promote.
- The meta-skill documents the loop and was improved through it; the challenge report is
  committed under `docs/evidence/`.

## 0.1.0 - 2026-10-10

First release, built in five missions.

### Added

- Capture and generate (mission 01): the `skillz` CLI and `skillz new`, which turns a paragraph
  or a `.txt`/`.yaml`/`.yml`/`.json` spec into a structured spec and a canonical
  `skills/<name>/SKILL.md`. Includes a small in-package YAML-subset reader/writer, the MIT
  licence, `.gitignore` and a CI workflow running ruff and pytest on Linux and Windows.
- Validate and render (mission 02): `skillz validate` with stable rule ids for frontmatter,
  naming, allowed paths, size limits and secret, network-call and destructive-shell patterns,
  writing `validate.json` and `validate.md`; `skillz render` builds the Claude and Codex
  variants (with `agents/openai.yaml`) from the canonical source without modifying it.
- Evaluation (mission 03): `skillz eval` generates trigger and behaviour cases, grades them
  offline with a deterministic lexical-overlap heuristic, and proposes improvements. The
  opt-in `--runner claude|codex` runs a with/without-skill baseline and degrades to
  `skipped: no runner`.
- Release and install (mission 04): `skillz package` creates immutable, checksummed versions
  with a regression check against the last approved version; `skillz approve` is the human
  approval gate; `skillz install` writes approved versions only into the platform directory
  and records them in `skills/registry.json`; `skillz rollback` and `skillz list`.
- Pipeline, meta-skill and docs (mission 05): `skillz run` takes a paragraph or spec through
  capture, generate, validate, render, evaluate, regression and package, stops at awaiting
  approval, and writes `.factory/pipeline.json` and `pipeline.md`. The meta-skill
  `skills/skill-factory/SKILL.md`, README with quickstart and pipeline diagram,
  CONTRIBUTING and this changelog. Hardening carried over from earlier security reviews:
  ASCII-only semantic versions, `--` before runner prompts and escaped registry fields in
  `skillz list`.
