# Mission 02 report: validate and render

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| validate-skill | `validate.py`: 14 stable rule ids (frontmatter, name, description, allowed paths, size limits, secret / network-call / destructive-shell patterns), static only, redacted secret messages, `.factory/validate.json` + `.md`; `capture.NAME_RE` now `fullmatch`; 10 fixture skills; 41 tests | cf71179 |
| render-variants | `render.py`: Claude variant (`.factory/dist/claude/`) and Codex variant (`.factory/dist/codex/` + `agents/openai.yaml`) from the canonical source, sha256 manifest in `render.json` + `render.md`; ci.yml `permissions: contents: read`; 14 tests | c9497cd |
| cli-validate-render | `skillz validate` and `skillz render` wired into cli.py; end-to-end CLI tests; interim README | b0ef1ac |

Gate after the final merge: `gate: PASS` (ruff, pytest 111 passed, uv build). Diff against mission 01: 34 files, +1535/-26. Over the 10-file target because 10 small fixture skill directories are the test data for one cohesive validate+render outcome; splitting them out would ship rules without tests (docs/plan.md, Assumptions 7).

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0 | test `uv run pytest -q` | PASS | check_criteria.py: passed |
| 1 | test validate+render tests | PASS | check_criteria.py: passed |
| 2 | test `skillz validate --help` | PASS | check_criteria.py: passed |
| 3 | test `skillz render --help` | PASS | check_criteria.py: passed |
| 4 | judge | PASS | reviewer ran the tests itself ("54 passed"), read each rubric element's test, and ran a real new/validate/render flow; destructive-shell fixture "FAILED (1 errors, 0 warnings)" exit 1; see docs/ledgers/criteria.json |

Requirements advanced: REQ-002, REQ-003. Mission 01 security low notes (NAME_RE fullmatch, ci.yml permissions) closed.

## Assumptions
- `network-call` and `destructive-shell` are scanned only under `scripts/`; `secret` in every text file except `.factory/`, so prose mentioning `curl` is not flagged.
- Symlinks and special files: `path-not-allowed` in validate; skipped and recorded in render.
- `.factory/` is allowed at the top level and excluded from scans and size totals.
- Size tests generate files at test time; secret-like values are built by concatenation and never committed.
- The CLI test uses the destructive-shell fixture for its exit-1 case (reviewer noted the plan suggested a curl script; equivalent coverage).

## Blocked steps
- None.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 3 workers, reviewer) | ~0.28M | wall clock | ~12 min | tool calls (subagents) | ~97 |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.

## Security
PASS on commit 78f6d92. Low notes: skills/<name> directory itself may be a symlink (operator-supplied local path); CI actions pinned by tag not SHA (carried to mission 05); BRIEF.yaml change is the mission brief written by program flow from docs/missions/02-validate-render.yaml.
