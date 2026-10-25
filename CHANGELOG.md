# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Receipt v1 (`skill-receipt/1`, defined in `docs/RECEIPT.md`): every packaged version carries
  `receipt.json` and `receipt.md` under `.factory/versions/<semver>/`, covered by the manifest
  checksums. A receipt records the evidence level (`verified` when a different model judged the
  runner's transcripts, `self-judged` when the same model did, `unverified` when only the lexical
  grading ran), the runner and judge, the baseline version, trigger and behaviour counts, and the
  gains and regressions against the last approved version, each with id, kind, prompt, before and
  after. `skillz receipt <name> [version] [--json]` prints it.
- `evals/evals.json` as a case source: `skillz eval`, `judge`, `package` and `challenge` read the
  standard's `{id, prompt, should_trigger, assertions}` entries. Each is a trigger case, and a
  `should_trigger` entry with assertions is also a behaviour case whose rubric is the assertions,
  so a hand-written skill with no `.factory/` is evaluated, judged, packaged and receipted.
- `skillz candidate <name>`: copies the stable skill into `.factory/candidates/<id>/skill/` for a
  human to edit, ahead of `skillz challenge` and the human's `skillz promote`.
- `action.yml` at the repository root: a composite GitHub Action, "Skill standard gate", that
  runs `scripts/check.py` over another repository's `skills/` (`uses:
  Jimmycarroll2021/skill-factory@main`, after `actions/setup-python`).
- `docs/RECEIPT.md`: the receipt standard, with the schema, the evidence levels, the definitions
  of regression and gain, what refuses what, and the finding it rests on (Tank and Nama, 2026,
  arXiv:2607.22520).
- Moved in from `Jimmycarroll2021/skillz-factory`, with its history: the skill standard
  (`docs/SKILL-STANDARD.md`), the study behind it (`docs/research/claude-skills-study.md`), its
  stdlib gate `scripts/check.py` with `scripts/allowlist.txt` and self-tests in
  `tests/test_check.py`, and the reference skill `skills/demo-spec-lint/`. CI now runs
  `scripts/check.py`, and `CLAUDE.md` carries the standard's rules and lessons.
- `skills/skill-factory/evals/evals.json`: prompts with assertions for the meta-skill, which the
  gate requires.
- Validator quality rules, from a study of a 398-skill public collection in which these
  signals separated stronger skills from weaker ones. Errors (the skill cannot work as
  written): `reserved-name` (a name that replaces a bundled Claude Code command, such as
  `verify`), `missing-script` (a fenced command runs a script that does not exist) and
  `script-flag-missing` (a fenced command passes an option the Python script never declares;
  read with `ast`, never executed; `--no-x` from `BooleanOptionalAction` and unambiguous
  prefixes count as declared). Both look only at script paths in command position, not at a
  path inside a string literal. Warnings: `description-no-boundary`,
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
  a candidate is built under `.factory/candidates/<id>/` without touching stable
  files; `skillz challenge` validates both sides, grades the union of cases and gives a verdict
  (`candidate-unsafe`, `candidate-worse`, `candidate-better`, `no-difference`); `skillz promote`
  is the explicit human command that replaces the canonical files with an acceptable candidate
  and packages a new awaiting-approval version. The pipeline and `skillz run` never promote.
- The meta-skill documents the loop and was improved through it; the challenge report is
  committed under `docs/evidence/`.

### Changed

- `skillz approve` refuses a version whose receipt status is `regressed` unless
  `--accept-regressions "<reason>"` is given; the reason and the regressed case ids are recorded in
  `approval.json`. A reason given for a version that did not regress is recorded too, with an
  empty id list.
- `skillz install` and `rollback` verify the whole version against its manifest (reports and
  receipt included), not only the `dist/` files, so an edited `receipt.json` is refused with a
  non-zero exit and nothing installed. A receipt whose `schema` is not `skill-receipt/1` is
  refused by `approve`, `install` and `skillz receipt` instead of being read with guesses.
- `skillz eval`, `judge`, `package` and `challenge` grade the name and description that SKILL.md
  carries even when `.factory/spec.json` exists, so an edited or promoted skill is never judged
  against a stale spec.
- `evals/evals.json` rejects an id that collides with another entry's behaviour case id
  (`<id>-beh`), with the same `SpecError` as a duplicate id.
- A receipt is `self-judged` or `verified` only when the judge scored at least one behaviour
  case; a completed run with no cases or with every call failed stays `unverified`.
- `skillz promote` treats a challenge as stale when `evals/evals.json` or
  `.factory/cases/captured.json` changed since it ran, as both are inputs the challenge graded.
- `skillz list` and the version listing read a receipt of an unknown schema as no evidence
  instead of failing, so one such version cannot block installing or rolling back to another.
- Version verification never opens a FIFO or device file; a special file in a version directory
  is reported as not matching the manifest, and a `receipt.json` that is not a regular file is
  refused without being read.
- Cases are de-duplicated across sources by what they check (kind, prompt, assertions and
  expected markers), not by prompt alone, so two `evals.json` entries with one prompt and
  different assertions are both judged, in `eval`, `package` and `challenge`.
- An `evals/evals.json` entry without an `id` is named after a digest of its prompt
  (`evl-<10 hex>`) instead of its position, so reordering or inserting entries cannot hand one
  prompt's history to another in the receipt's comparison.
- The regression report splits the comparison into gains and regressions instead of one
  `newly_failing` list; the pipeline's regression line reads like
  `pass (2 gains, 0 regressions vs 0.1.0; 7 cases compared)`.
- `skills/registry.json` records each installed version's evidence level and `skillz list` shows
  it in an evidence column.
- README repositioned around the receipt: it leads with getting a receipt for an existing skill,
  the challenger loop uses `skillz candidate`, the quality tiers name the evidence level each
  earns, the gate has a "use it in your own repo" snippet, and `skillz new`/`run` move to a short
  section near the end. The illustrative pipeline summary is no longer presented as pasted from a
  run. The banner and the loop and architecture diagrams are regenerated to match.
- The meta-skill `skills/skill-factory/SKILL.md` starts from "Get a receipt for a skill", names
  `skillz receipt` and `skillz candidate`, and its description says what it is NOT for.
- `skillz validate` accepts an `evals/` directory in a skill, for committed eval prompts.
  Render never ships it, so installed skills are unchanged.
- `skills/demo-spec-lint/SKILL.md` writes its description on one line, which `skillz validate`
  can parse; the text is unchanged.
- `scripts/check.py` no longer passes a broken skill in three cases Codex found: an
  `evals.json` holding JSON `null`, a verdict script in a subdirectory of `scripts/`, and a
  documented command with a quoted script path or a line continuation, and a case naming a
  script the skill does not have. It also stops calling a documented extensionless file (such
  as `assets/Dockerfile`) an orphan.
- Generated descriptions now carry the paragraph's "use it when" and "do not use it" sentences.
  Claude reads only the frontmatter description when choosing a skill, so `capture` composes
  them into the description (first trigger and first boundary before any others, each kept
  whole and only if it fits 1,024 characters), and the body no longer repeats them. On the
  README example the evaluation goes from 6 passed, 2 failed to 7 passed, 1 failed.
- The offline grader ignores the description's boundary sentences ("Do not use it for X",
  "Never Y", "It does not handle X") when collecting trigger words, so stating a boundary cannot make a request about
  X look like a match.
- The meta-skill's description carries the captured request "Help me write a SKILL.md file so my
  agent learns this workflow" whole, instead of a keyword list such as "help, write, md, file",
  which made it load on unrelated prompts.
- `generate` no longer writes placeholder `README.md` files into resource directories; an
  empty directory is reported by the validator instead.
- `.claude/settings.json` keeps every guardrail the harness had, without the harness: the
  sandbox stays on with unsandboxed commands off, and its network allowlist drops only the npm
  and Playwright hosts this Python project never used. The deny list still blocks force pushes,
  pushes to `main`, history rewrites, `git branch -D`, `git checkout --`, merges, `rm -rf`,
  `pip install`, `PowerShell` and `.env` files. Its six harness hook scripts (guard, idempotency,
  budget, anchor, stop gate, subagent check) are replaced by one stdlib hook, `.claude/hooks/guard.py`, which
  catches what a prefix rule cannot: `push -uf`, `HEAD:main`, `rm -r -f`, `cat .env` (any
  case), `cd` before `git`, and the same commands behind wrappers (`env -i`, `sudo`, `find -exec`),
  inside `if`/`for`/`{ }` bodies, `$(...)`, backticks, `bash -c` (or `-lc`) or `eval`, across
  line continuations, after a comment, or spelled `git.exe`. It also denies PowerShell and `cmd`
  run from Bash, `gh` PR merges with `--repo` anywhere, write-side `gh api` (payload flags,
  `graphql`), a `merge_pull_request` tool from any MCP server, writes to hosting, infra and
  deploy config, any hook event with a missing or mistyped field, and any command it cannot
  read: one it cannot tokenise (an unquoted heredoc body with an apostrophe) or shells nested
  more than three deep. `defaultMode: acceptEdits` and
  the broad allow rules (`npm`, `python`, `cat`, `cd`) are gone, so more calls ask first.

### Removed

- `skillz improve`. It rewrote a skill's description from eval proposals and captured prompts, so
  the candidate it built was the lexical grader's idea of a fix, judged by the same lexical grader.
  A candidate is now a copy a human edits (`skillz candidate`), and the challenge and the receipt
  say whether the edit regressed anything.
- The quickship v0.3 agent harness that built the project: the controller and hook scripts
  under `scripts/`, its bash self-tests under `tests/`, `.quickship/`, `BRIEF*.yaml`,
  `IDEA*.md`, the `*.cmd` launchers, its subagent definitions, its `CLAUDE.md` contract and
  hook settings, its PR template, and the build records in `docs/missions/`, `docs/runs/`,
  `docs/PROGRAM.md`, `docs/PRD_TEMPLATE.md` and `docs/design/contracts.md`. They stay in git
  history. `CLAUDE.md` and the PR template are replaced with plain project versions; the
  `skillz` package is unchanged.

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
