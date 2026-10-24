# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `docs/research/claude-skills-study.md`: the study the standard is built on.
- `docs/SKILL-STANDARD.md`: the skill standard, each rule tagged with what enforces it.
- `scripts/check.py`: the repo gate, with checks for frontmatter, reserved names, boundary clauses, command contracts, script entrypoints, reachability, script cases with expected exit codes, eval structure, prose cruft and duplicates. It refuses to pass an empty tree and fails on unreasoned or stale allowlist entries.
- `tests/test_check.py`: self-tests in which every check fails on a known-bad fixture.
- `skills/demo-spec-lint`: the reference skill. It blocks a demo spec whose golden-path steps name no observable result.
- CI running the gate and its self-tests on Linux and Windows.
- MIT licence; skills carry `license: MIT` in their frontmatter.
