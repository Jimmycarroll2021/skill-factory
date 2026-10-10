# Product Requirements Document: skillz-factory

## Idea

skillz-factory is a meta-skill and CLI (`skillz`) that turns a one-paragraph description of a capability into a complete, statically validated, tested and versioned Agent Skill for Claude Code and Codex. One canonical skill source is rendered into thin platform-specific install variants, tested against trigger and behaviour cases (and, when asked, against a no-skill baseline via the `claude` or `codex` CLI), held as a versioned package until a human approves it, then installed and recorded in a registry of proven skills with rollback. The evaluation engine, regression suite, version history and registry are the product; the drafting prompt is not.

## User and pain

- **User:** a developer or small team lead who uses Claude Code and/or Codex daily and wants the agent to do new, repeatable jobs (release notes, migration checks, house-style reviews) by writing Agent Skills.
- **Painful job:** writing a skill by hand: a `SKILL.md` with a guessed trigger description, no evidence it triggers when it should (or stays quiet when it should not), no comparison against "no skill at all", no record of which version worked, and a separate hand-maintained copy per platform.
- **Today:** hand-written `SKILL.md` files copied between `.claude/skills/` and `.agents/skills/`, ad-hoc trial in a chat session, git history as the only version record.
- **Why switch:** one command produces a spec-compliant skill for both platforms, a machine-readable report proving it passes structural, security and trigger checks, a regression comparison against the previous version, and a safe approve/install/rollback path. The skill becomes an asset with evidence rather than a guess.

## Riskiest assumptions

1. **Offline, deterministic evaluation is useful enough.** Without model calls the trigger and behaviour checks are heuristic (lexical matching of prompts against the description). If developers find these signals meaningless, the core value (evidence) collapses to the optional model-backed run.
2. **Developers want a gated pipeline rather than a quick draft.** If the approve/package/install ceremony feels heavier than editing a markdown file, people will skip the factory after the first draft.
3. **One canonical source can serve both platforms.** If Claude and Codex skill formats diverge beyond frontmatter and an `agents/openai.yaml` sidecar, "thin variants" become forks and the single-source promise breaks.
4. **Static validation catches the dangerous cases.** Pattern-based detection of secrets, network calls and destructive shell will have false negatives; if users treat a green report as a security guarantee, a bad skill gets installed.

## What would prove it not worth building

- Users accept the generated draft and then edit the installed copy directly, never re-running `skillz` (the pipeline adds nothing after drafting).
- Offline trigger-test verdicts disagree with model-backed runs on more than about a third of cases for real skills, so nobody trusts the offline report.
- Users never run `skillz rollback` or look at version history after several releases (versioning is ceremony, not value).
- Maintaining the Claude and Codex renderers requires per-skill hand edits for most skills.
- Writing a skill by hand with a template still takes less time than writing a paragraph plus reviewing the factory's reports.

## Product vision

- **Capture:** turn a paragraph, YAML or JSON spec into a structured, editable skill specification with intent, triggers, non-triggers and expected behaviours.
- **Generate:** draft a portable, progressive-disclosure skill (short `SKILL.md`, detail in `references/`, `scripts/` and `assets/` only when justified), optionally model-assisted.
- **Render:** produce thin Claude and Codex install variants (and future platforms) from one canonical source, never forking it.
- **Validate:** static structure, spec-compliance and security analysis with a clear, machine-readable verdict.
- **Evaluate:** generated positive/negative trigger tests and behavioural cases, run offline heuristically and online against a no-skill baseline with graded outputs and concrete improvement proposals.
- **Regress and release:** immutable versioned packages, regression comparison against the last approved version, human approval gate, install, registry of proven skills and one-command rollback.
- **Self-improve:** run the factory on itself as a challenger: a candidate factory version passes the same meta-evaluations and regression comparison as the stable one before a human promotes it.
- **Share:** export and import proven skills between projects and teams with their evaluation evidence attached.

## MVP scope

Every stage works offline and deterministically. Model-backed baseline runs are opt-in and degrade to "skipped: no runner".

- **REQ-001 Capture and generate:** `skillz new` accepts a paragraph (argument or text file) or a small YAML/JSON spec, writes a structured spec, and generates a canonical skill directory `skills/<name>/` with a spec-compliant `SKILL.md` (kebab-case `name` matching the directory, at most 64 characters; non-empty `description` at most 1024 characters; optional `license`, `allowed-tools`, `metadata`), adding `references/`, `scripts/` or `assets/` only when the spec justifies them.
- **REQ-002 Static validation:** `skillz validate <name>` checks frontmatter, naming, allowed paths (only `SKILL.md`, `scripts/`, `references/`, `assets/` plus the factory's own `.factory/`), body and file size limits, and forbidden patterns in scripts and text (secrets, network calls, destructive shell), without executing anything, and writes a JSON report and a Markdown summary under `skills/<name>/.factory/`. Exit code is non-zero on any error-level finding.
- **REQ-003 Platform rendering:** `skillz render <name>` produces a Claude variant (layout for `.claude/skills/<name>/`) and a Codex variant (layout for `.agents/skills/<name>/` plus `agents/openai.yaml`) from the canonical source into `skills/<name>/.factory/dist/`, never modifying the canonical files, with a JSON report.
- **REQ-004 Evaluation:** `skillz eval <name>` generates positive and negative trigger tests and behavioural evaluation cases from the spec, runs the trigger tests with a deterministic offline matcher, grades results, lists weak areas with concrete improvement proposals, and, only when `--runner claude` or `--runner codex` is given and that executable is on PATH, runs each case with and without the skill and grades the difference; otherwise it records "skipped: no runner". JSON report plus Markdown summary.
- **REQ-005 Versioned release:** `skillz package <name>` snapshots the canonical skill, rendered variants and reports into an immutable, checksummed version under `skills/<name>/.factory/versions/<semver>/`, running regression: the previous approved version's trigger and behaviour cases are re-run against the candidate, and any newly failing case is reported. `skillz approve <name> <version>` is the only way a version becomes installable.
- **REQ-006 Install, registry and rollback:** `skillz install <name> --platform claude|codex --scope project|user` installs only an approved version into the platform directory, records it in a registry of proven skills (`skills/registry.json`), and refuses to write anywhere else. `skillz rollback <name>` reinstalls the previous approved version; `skillz list` shows the registry and version history.
- **REQ-007 One-command pipeline:** `skillz run` takes a paragraph or spec through capture, generation, validation, rendering, evaluation, regression and packaging in one go, stops at "awaiting approval" with a Markdown summary of every stage, and completes even when no model runner is available. The factory ships its own meta-skill, `skills/skillz-factory/`, which tells Claude Code or Codex how to drive `skillz` and passes `skillz validate`.
- **REQ-008 Public-ready repository:** MIT `LICENSE`, `README.md` with quickstart and pipeline diagram, `CONTRIBUTING.md`, `CHANGELOG.md`, a GitHub Actions workflow running ruff and pytest on Linux and Windows, `.gitignore`, and `uv build` producing a wheel.

No design language section: the product is a CLI with no UI.

## Non-goals

- Factory self-improvement (challenger factory versions and meta-evaluations); the versioning and evaluation engine built here is its foundation, but the loop itself is a later mission.
- Model-assisted drafting of SKILL.md content; the MVP generator is deterministic and template-based.
- Executing a generated skill's scripts, sandboxing, or dynamic security analysis.
- Sharing, publishing or syncing skills to any remote registry, marketplace or git remote; no `git push` ever.
- Platforms other than Claude Code and Codex; a GUI or web dashboard.
- Semantic (embedding-based) trigger matching.

## Missions

| Mission | Requirements | Reviewable outcome |
| --- | --- | --- |
| `docs/missions/01-skeleton-capture.yaml` | REQ-001, REQ-008 | uv package, `skillz` CLI, `skillz new` from paragraph or YAML/JSON spec generating a compliant skill; LICENSE, .gitignore and the Linux/Windows CI workflow |
| `docs/missions/02-validate-render.yaml` | REQ-002, REQ-003 | static validator with JSON/Markdown reports, and Claude/Codex variant rendering from the canonical source |
| `docs/missions/03-evaluation.yaml` | REQ-004 | trigger and behavioural case generation, offline grading, improvement proposals, opt-in baseline runner with "skipped: no runner" |
| `docs/missions/04-release-install.yaml` | REQ-005, REQ-006 | versioned packages with regression, approval gate, install, registry, rollback and list |
| `docs/missions/05-pipeline-docs.yaml` | REQ-007, REQ-008 | `skillz run` end-to-end pipeline stopping at approval, README with quickstart and diagram, CONTRIBUTING, CHANGELOG |

## Delivery slicing

- **REQ-008 is split across missions 01 and 05.** The CI workflow, `LICENSE`, `.gitignore` and the wheel build belong in the skeleton so every later PR runs on Linux and Windows from day one. The README quickstart, pipeline diagram, CONTRIBUTING and CHANGELOG are written last, in mission 05, because they describe the finished pipeline and would otherwise be rewritten by every mission.
- **Mission 01 may exceed 10 changed files** (manifest, package modules, tests, fixtures, LICENSE, .gitignore, CI workflow). The extra files are short boilerplate; splitting them out would leave a skeleton whose CI does not run, which is less safe to review.
- **Mission 04 is the largest** (versioning, regression, approval, install, registry, rollback). It may approach the 500-line target. Packaging and approval are kept with install because the install path's core safety property ("only approved versions are installed, only into the chosen directory") cannot be tested without them; splitting would ship an approval gate nothing enforces, or an installer with no gate.

## Assumptions made

- Stack fixed by the constraints: Python 3.12, uv, pytest and ruff as the only dev dependencies, no runtime dependencies. YAML input and frontmatter are handled by a small in-package reader/writer for the subset skills and specs need (scalars, lists, one level of mappings), because PyYAML is not allowed.
- Paragraph capture is deterministic: the skill name is derived from the first meaningful words (kebab-case, truncated to 64 characters), the description from the first sentences (truncated to 1024 characters), triggers and non-triggers from simple phrase extraction. A `--name` option overrides the derived name.
- Offline trigger matching is a deterministic lexical-overlap scorer between a test prompt and the skill's name and description, with a fixed threshold; it is a heuristic signal, and reports say so.
- Model-backed runs invoke `claude -p` or `codex exec` non-interactively via `subprocess` only with an explicit `--runner` flag and only when `shutil.which` finds the executable; tests use a fake runner and never need a network, key or logged-in CLI.
- Versions use semantic versioning starting at `0.1.0`; `skillz package` bumps the patch version by default with `--minor`/`--major` options. Version directories are never modified after creation; integrity is a SHA-256 manifest.
- Approval is an explicit `skillz approve <name> <version>` command recorded with a UTC timestamp and the local user name; there is no multi-user sign-off.
- The registry is a JSON file at `skills/registry.json` in the working project; user-scope installs are still recorded in the project registry.
- User-scope install directories are resolved from the home directory (`Path.home()`), so tests redirect `HOME`/`USERPROFILE` to a temporary directory and never touch the real one.
- Rollback reinstalls the previous approved version into the same platform and scope and records the rollback in the registry; it never deletes a version.
- Size limits: `SKILL.md` body at most 500 lines (warning above), any single file at most 1 MB (error), total skill at most 5 MB (error).
- Windows support: paths via `pathlib`, no symlinks, UTF-8 file I/O with explicit encoding, LF line endings in generated files.
- The "meta-skill" is delivered as the CLI plus a small Agent Skill at `skills/skillz-factory/SKILL.md` (part of REQ-007, mission 05) that tells Claude Code or Codex how to drive `skillz`; it must itself pass `skillz validate`.
- Single-user, local, offline-first tool; no telemetry.
