# Mission 03 report: evaluation

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| eval-cases | `evalcases.py`: deterministic case generation (pos/neg/beh ids), lexical-overlap offline matcher (MATCH_THRESHOLD 0.3), `grade_offline`, `propose_improvements` (trigger-vocabulary, negative-overlap, vague-description, no-behaviours); two eval fixtures; 13 tests | 9c8a076 |
| baseline-runner | `runner.py`: Runner protocol, ClaudeRunner (`claude -p ... --output-format text`), CodexRunner (`codex exec ...`), `detect_runner` via shutil.which at call time, `run_baseline` with/without skill text, `grade_markers`, `SKIPPED_NO_RUNNER`; 17 tests, subprocess patched to raise | 0df9897 |
| eval-cli | `evaluate.py` + `skillz eval <name> [--runner] [--strict]`: cases.json, eval.json, eval.md under .factory/; advisory exit 0; 14 tests | cc037a1 |
| eval-cli-fix | reviewer round 1 finding: test for the `.factory` symlink refusal | 30ecef5 |

Gate after the final merge: `gate: PASS` (ruff, pytest 156 passed, uv build). Diff against mission 02: 15 files, about +1560/-15. Over the 500-line target: three modules each with a test file and two fixture specs form one outcome (`skillz eval`); the PRD's delivery slicing anticipated the evaluation engine being the largest single component.

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0 | test `uv run pytest -q` | PASS | check_criteria.py: passed |
| 1 | test eval+runner tests | PASS | check_criteria.py: passed |
| 2 | test `skillz eval --help` | PASS | check_criteria.py: passed |
| 3 | judge | PASS | two reviewer rounds; round 2 "45 passed" with each rubric element tied to a named test; real run with `--runner codex` (not installed) printed "baseline: skipped: no runner" and exited 0; see docs/ledgers/criteria.json |

Requirements advanced: REQ-004.

## Review rounds
Round 1: FAIL on one item (symlink guard without a test); judge PASS. Fixed by eval-cli-fix. Round 2: PASS.

## Assumptions
- Offline grading is heuristic lexical overlap; eval.json carries a `method` string saying so.
- Skill text is embedded in the prompt inside `<skill>` tags for both runners, so Claude and Codex are driven the same way.
- Baseline verdicts: skill-helps / skill-hurts / no-difference / error per behavioural case.
- `--strict` exits 1 on any failing trigger case; default is advisory (exit 0).
- Spec fallback from SKILL.md frontmatter yields empty trigger lists and a no-behaviours weak area.

## Blocked steps
- None.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 4 workers, 2 reviewers) | ~0.36M | wall clock | ~15 min | tool calls (subagents) | ~118 |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.
