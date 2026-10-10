# Mission 05 report: pipeline, meta-skill and public docs

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| pipeline-run | `pipeline.py` + `skillz run <source>`: capture → generate → validate → render → evaluate → regression → package → awaiting approval; `.factory/pipeline.json` and `pipeline.md`; exit 0/1/2 per contract; never approves or installs; 14 tests | 5d93431 |
| meta-skill-docs | `skills/skill-factory/SKILL.md` (validates clean), README (pitch, Mermaid pipeline diagram with the human approval gate, quickstart, command table, design, meta-skill use, development, how it was built), CONTRIBUTING, CHANGELOG 0.1.0, `readme` field in pyproject, `skills/*/.factory/` ignored; 11 tests | eb62abc |
| security-polish | ASCII-only semver with 9-digit cap (release + install), `--` before runner prompts (verified against the real `claude -p ... -- "prompt"` by the lead: exit 0), `_baseline_cases` catches AttributeError, control characters escaped in `skillz list` cells, CI pin-by-SHA TODO comment; 8 tests that fail without the changes | 41e77a6 |

Gate after the final merge: `gate: PASS` (ruff, pytest 220 passed, uv build). Diff against mission 04: 23 files, about +1300/-42. Over both targets: most of the lines are prose (README, CONTRIBUTING, CHANGELOG, SKILL.md) describing the command added in the same mission, and the security notes were carried here by earlier missions; splitting the docs from the pipeline they describe would ship a README for a command that does not exist.

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0 | test `uv run pytest -q` | PASS | check_criteria.py: passed |
| 1 | test tests/test_pipeline.py | PASS | check_criteria.py: passed |
| 2 | test `skillz run --help` | PASS | check_criteria.py: passed |
| 3 | test `skillz validate skill-factory` | PASS | check_criteria.py: passed |
| 4 | test `uv build` | PASS | check_criteria.py: passed |
| 5 | file README.md "skillz run" | PASS | check_criteria.py: passed |
| 6 | file CHANGELOG.md "0.1.0" | PASS | check_criteria.py: passed |
| 7 | judge | PASS | reviewer ran the tests ("25 passed"), checked README flags against every `--help`, validated the meta-skill, and ran a real `skillz run` with no runner on PATH ending at "approval: awaiting approval" with nothing installed; see docs/ledgers/criteria.json |

Requirements advanced: REQ-007, REQ-008 (completing the split started in mission 01). All eight MVP requirements are now covered.

## Assumptions
- `skillz run` reports the regression and package outcomes from `release.package_skill`; the packaged eval.json is the offline re-run, the pipeline's own evaluate verdict (including the baseline status) lives in pipeline.json/md.
- CI actions stay pinned by tag with a TODO to pin by commit SHA: no SHA is available offline and none was guessed.
- The README's dogfooding path installs the meta-skill into the repo's own `.claude/skills/`, which is not gitignored (a user choice).

## Blocked steps
- None.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 3 workers, reviewer) | ~0.34M | wall clock | ~16 min | tool calls (subagents) | ~121 |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.
- Open low notes: CI actions not SHA-pinned; TOCTOU between symlink check and rename on install targets (local write access required).
