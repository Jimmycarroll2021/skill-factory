# Mission 01 report: skeleton and capture

## State
READY (lead verdict; see "Controller" below)

## Work
Three file-disjoint tasks, each in its own worktree, merged in order:

| task | outcome | commit |
|---|---|---|
| pkg-skeleton | uv package `skill_factory` (hatchling, src layout, no runtime deps, dev group pytest+ruff, `skillz` console script, uv.lock), MIT LICENSE, Python .gitignore entries, `.github/workflows/ci.yml` (ruff + pytest on ubuntu-latest and windows-latest), smoke test | 027f9cc |
| capture-spec | `yamlish.py` YAML-subset reader/writer, `capture.py` (paragraph / YAML / JSON -> `SkillSpec`, deterministic name and description, `validate_spec`), 28 tests, three fixtures | 60539c3 |
| generate-cli | `generate.py` (spec -> `skills/<name>/SKILL.md`, `.factory/spec.json`, resource dirs only when requested, `--force`), argparse `cli.py` with `skillz new`, `__main__.py`, 25 tests | 931a9c9 |

Gate after the final merge: `gate: PASS` (ruff, pytest 50 passed, uv build).

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0 | test `uv run pytest -q` | PASS | check_criteria.py: passed |
| 1 | test capture+generate | PASS | check_criteria.py: passed |
| 2 | test `uv run skillz --help` | PASS | check_criteria.py: passed |
| 3 | test `uv run skillz new --help` | PASS | check_criteria.py: passed |
| 4 | test `uv build` | PASS | check_criteria.py: passed |
| 5 | file LICENSE "MIT License" | PASS | check_criteria.py: passed |
| 6 | grep windows-latest ci.yml | PASS | check_criteria.py: passed |
| 7 | judge | PASS | reviewer ran the tests itself (49 passed) and quoted the frontmatter assertions, pyproject dependency block and CI matrix; see docs/ledgers/criteria.json |

Requirements advanced: REQ-001 (capture and generate), REQ-008 (LICENSE, .gitignore, CI, wheel build).

## Assumptions
- Spec files are strict: unknown keys and non-string metadata values raise `SpecError`.
- `allowed-tools` is written to frontmatter as a space-delimited string per the Agent Skills spec.
- `--force` rewrites only `SKILL.md` and `.factory/spec.json`; leftover resource directories are not removed (reviewer note, non-blocking, deferred to REQ-002 validation).
- The lead added `tests/test_package.py` to the pkg-skeleton task so the gate had a real test before the other tasks merged (docs/decisions.md).

## Blocked steps
- None inside the mission. The quickship controller (`scripts/run.sh`) could not run in this environment: preflight fails because `gh` has no valid token, so the lead loop was driven by hand and the controller's publication step did not happen.

## Controller
Not run (see above). The lead reran the gate and `check_criteria.py` on the final merge and recorded the reviewer's evidenced judge grade. `docs/RESULT.json` is the lead's submission only; no `RUN_STATE` or `COMPLETION.json` was written because only the controller may write them.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 3 workers, reviewer) | ~0.2M | wall clock | ~14 min | tool calls (subagents) | ~72 |

## Gaps
- No controller verification or PR; publication is handled outside quickship for this program (single branch, one PR at the end).
- Security review runs on the final commit after this report (see docs/ledgers/handoff.md).

## Security
PASS on commit 51c5c8c. Low notes (not blocking, carried forward): capture.py NAME_RE should use re.fullmatch; generate.py overwrite guard checks only SKILL.md; ci.yml lacks a top-level `permissions: contents: read` block and pins actions by tag.
