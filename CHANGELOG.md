# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

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
