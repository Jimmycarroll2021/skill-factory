# skill-factory

[![CI](https://github.com/Jimmycarroll2021/skill-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/Jimmycarroll2021/skill-factory/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

skill-factory is a meta-skill and a command line tool (`skillz`) that turns a one-paragraph
description of a capability into a complete Agent Skill for Claude Code and Codex. One
canonical `skills/<name>/SKILL.md` is validated statically, rendered into thin Claude and
Codex variants, tested with trigger and behaviour cases, packaged as an immutable checksummed
version, held until a human approves it, then installed and recorded in a registry with
rollback. Everything works offline and deterministically; model-backed baseline runs are
opt-in. Python 3.12, no runtime dependencies.

## Pipeline

```mermaid
flowchart LR
    A[describe] --> B[capture]
    B --> C[generate]
    C --> D[validate]
    D --> E[render]
    E --> F[evaluate]
    F --> G[regression]
    G --> H[package]
    H --> I{{human approval gate}}
    I --> J[install]
    J --> K[registry and rollback]
    K --> L[capture-failure]
    L --> M[improve]
    M --> N[challenge]
    N --> O{{human promote}}
    O --> H
```

The same stages as a plain list:

1. describe: you write a paragraph (or a small spec file)
2. capture: `skillz` turns it into a structured spec
3. generate: the canonical `skills/<name>/` directory is written
4. validate: static checks, nothing is executed
5. render: Claude and Codex install variants are built from the canonical source
6. evaluate: trigger and behaviour cases are generated and graded
7. regression: the candidate is compared with the last approved version
8. package: an immutable, checksummed version is created
9. human approval gate: you run `skillz approve`; nothing is installable before this
10. install: the approved version is copied into the platform directory
11. registry and rollback: installs are recorded; `skillz rollback` reinstalls the previous version

`skillz run` performs stages 2 to 8 in one command and stops at the approval gate. It never
approves and never installs. The lower branch of the diagram is the optional challenger loop
described under [Self-improvement](#self-improvement-the-challenger-loop): it feeds a
promoted candidate back into `package`.

## Quickstart

```bash
git clone https://github.com/Jimmycarroll2021/skill-factory
cd skill-factory
uv sync
```

```bash
# 1. capture, generate, validate, render, evaluate, regression check and package
uv run skillz run "Draft release notes from a list of merged pull requests, grouped by area."

# 2. read the per-stage summary the run wrote (the skill name is printed)
cat skills/<name>/.factory/pipeline.md

# 3. approve a version after reviewing it (a human decision)
uv run skillz approve <name> 0.1.0

# 4. install it for Claude Code or Codex
uv run skillz install <name> --platform claude --scope project

# 5. see what is installed, and go back if needed
uv run skillz list
uv run skillz rollback <name>
```

Claude Code installs to `.claude/skills/<name>/` (project scope) or `~/.claude/skills/<name>/`
(user scope). Codex installs to `.agents/skills/<name>/` plus `agents/openai.yaml`. Installs
only write to those exact directories.

## Commands

Every command accepts `--out <dir>`, the root that contains `skills/` (default: the current
directory).

| Command | What it does |
| --- | --- |
| `skillz run <source> [--name NAME] [--runner {claude,codex}] [--force]` | The pipeline: capture, generate, validate, render, evaluate, regression, package; stops at awaiting approval and writes `.factory/pipeline.json` and `pipeline.md`. Exit 0 awaiting approval, 1 stopped by a failed stage, 2 input error. |
| `skillz new <source> [--name NAME] [--force]` | Capture a skill from a paragraph or a `.txt`/`.yaml`/`.yml`/`.json` spec and generate `skills/<name>/`. |
| `skillz validate <name>` | Check frontmatter, naming, paths, sizes, secrets, network calls and destructive shell. Writes `.factory/validate.json` and `validate.md`; exit 1 on errors. |
| `skillz render <name>` | Build the Claude and Codex variants under `.factory/dist/` without touching canonical files. |
| `skillz eval <name> [--runner {claude,codex}] [--strict]` | Generate cases, grade them offline, propose improvements. `--strict` exits 1 when a trigger case fails. |
| `skillz package <name> [--minor \| --major] [--set-version X.Y.Z] [--allow-regression]` | Validate, render and evaluate, compare with the last approved version, and write an immutable version under `.factory/versions/<semver>/`. |
| `skillz approve <name> <version>` | Verify checksums and record approval (UTC time and local user). The only way a version becomes installable. |
| `skillz install <name> --platform {claude,codex} --scope {project,user} [--version X.Y.Z]` | Install an approved version (default: highest approved) and record it in `skills/registry.json`. |
| `skillz rollback <name> [--platform {claude,codex}] [--scope {project,user}]` | Reinstall the highest approved version below the installed one. Versions are never deleted. |
| `skillz list [--json]` | Show the registry and each skill's versions with their status. |
| `skillz capture-failure <name> --prompt TEXT (--should-trigger \| --should-not-trigger \| --expect MARKER ...) [--note TEXT]` | Record a real failure as a persistent eval case in `.factory/cases/captured.json`. Exit 0 recorded, 2 input error. |
| `skillz improve <name>` | Build a challenger candidate under `.factory/candidates/<id>/` from eval proposals and captured failures; stable files are untouched. Exit 0 created, 1 nothing to improve, 2 input error. |
| `skillz challenge <name> <candidate-id>` | Validate the stable skill and the candidate, grade the union of cases on both sides and write `challenge.json` and `challenge.md`. Exit 0 for every verdict, 2 on missing or invalid input. |
| `skillz promote <name> <candidate-id>` | Explicit human command. Replace the canonical files with a challenged candidate and package a new version that waits for approval. Exit 0 promoted, 1 refused (stale, missing or non-acceptable challenge, or packaging failed and was rolled back), 2 input error. |

## Self-improvement (the challenger loop)

When a skill gets a prompt wrong, record it and let a challenger candidate prove a fix before
anything stable changes. Every step is offline and deterministic.

```bash
# 1. record the failure; every later eval, package and challenge includes it
uv run skillz capture-failure <name> --prompt "Help me write a SKILL.md file" --should-trigger

# 2. build a candidate under skills/<name>/.factory/candidates/<id>/ (stable files untouched)
uv run skillz improve <name>

# 3. compare candidate with stable and read the verdict in challenge.md
uv run skillz challenge <name> c001

# 4. a human decides: promote, then approve the new version as usual
uv run skillz promote <name> c001
uv run skillz approve <name> <new-version>
```

- `capture-failure` appends to `.factory/cases/captured.json` with `--should-trigger`,
  `--should-not-trigger` or `--expect MARKER ...` (a behaviour case). Records are never rewritten.
- `improve` applies the eval proposals and the captured failures deterministically: it extends
  the frontmatter `description` with the missing trigger words (never past 1024 characters),
  adds captured prompts to the spec's trigger lists and copies the body unchanged.
- `challenge` validates the stable skill and the candidate, grades the union of both sides'
  cases plus the captured ones, and gives one verdict, by precedence:
  `candidate-unsafe` (the candidate has a validation error), `candidate-worse` (a case that
  passed on stable fails on the candidate), `candidate-better` (a case improved, none regressed)
  or `no-difference`.
- `promote` refuses unless the challenge is current and the verdict is `candidate-better` or
  `no-difference`. It then replaces the canonical files, packages a new `awaiting-approval`
  version and leaves earlier versions byte-identical. A refused or failed promote changes no
  canonical file, and the same candidate cannot be promoted twice.

Promotion is an explicit human command. `skillz run` and the pipeline never promote, approve
or install. Grading is the same lexical-overlap heuristic as `skillz eval`: a better verdict
is a signal about the description, not proof that a model will behave better. This repository
dogfooded the loop on `skills/skill-factory`; the committed challenge report is
[docs/evidence/skill-factory-challenge.md](docs/evidence/skill-factory-challenge.md).

## What the factory checks

- **Validation rules** (static, nothing is executed): frontmatter and kebab-case name matching
  the directory, description length, allowed paths (`SKILL.md`, `scripts/`, `references/`,
  `assets/`, `.factory/`), file and total size limits, and patterns for secrets, network calls
  and destructive shell in scripts. A green report is not a security guarantee: pattern
  matching has false negatives.
- **Trigger tests**: positive and negative prompts are generated from the spec and graded
  offline by a lexical-overlap scorer against the skill's name and description. This is a
  heuristic signal, not evidence that a model will trigger the skill.
- **Behaviour cases**: generated from the spec. With the opt-in `--runner claude|codex` and that
  executable on PATH, each case runs with and without the skill and the difference is graded.
  Otherwise the report records `skipped: no runner` and the pipeline still completes.
- **Regression**: the previous approved version's trigger cases are re-run against the
  candidate. A newly failing case stops the pipeline unless you package with `--allow-regression`.

## Design

- One canonical skill source in `skills/<name>/`; the Claude and Codex variants are thin
  renderings of it and are never edited by hand.
- Offline and deterministic by default. Model runners are opt-in and never needed to finish.
- Versions are immutable and checksummed; install and rollback verify the checksums.
- A human approval gate sits between packaging and installing. The tool never approves for you.
- No `git push` and no publishing, ever. Skills are never executed by the factory.

## Use the meta-skill

The repository ships its own skill, `skills/skill-factory/SKILL.md`, which tells an agent how to
drive `skillz`: run the pipeline, read `pipeline.md`, report to you, and approve only when you say so.
Add it to an agent by copying or symlinking `skills/skill-factory` into `.claude/skills/` (Claude
Code) or `.agents/skills/` (Codex).

The dogfooding path uses the factory on itself, from the repository root:

```bash
uv run skillz validate skill-factory
uv run skillz package skill-factory
uv run skillz approve skill-factory 0.1.0
uv run skillz install skill-factory --platform claude --scope project
```

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
bash scripts/gate.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## How this repository was built

This repository was built unattended with [quickship](https://github.com/Jimmycarroll2021/quickship),
an agent harness, in five reviewed missions. The harness and its evidence are committed: the
`scripts/` directory, `tests/*.sh`, `CLAUDE.md`, `BRIEF*.yaml`, `docs/missions/`, `docs/PRD.md`
and the per-mission reports and ledgers under `docs/runs/`. They are not part of the `skillz`
package and you can ignore them when using the tool. The package itself is `src/skill_factory/`.

## Licence

MIT. See [LICENSE](LICENSE).
