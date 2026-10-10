# Mission 04 report: release and install

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| release-package | `release.py`: `skillz package` (re-runs validate, render, offline eval; immutable `.factory/versions/<semver>/` with manifest.json SHA-256, status awaiting-approval; regression against the previous approved version's cases, exit 1 on newly failing cases unless `--allow-regression`), `skillz approve` (approval.json, idempotent, verifies checksums), index.json; 15 tests | 58452c4 |
| install-registry | `install.py`: `skillz install` (approved versions only, exact target dir for claude/codex x project/user, manifest integrity check, atomic swap), `skillz rollback` (previous approved version, all-or-nothing, never deletes), `skillz list` (+ `--json`), `skills/registry.json` append-only installs + current pointers; 27 tests | 7c4dc76 |
| release-cli | five subcommands wired; end-to-end CLI flow test; "git push" source scan | ef4c5e4 |

Gate after the final merge: `gate: PASS` (ruff, pytest 206 passed, uv build). Diff against mission 03: 12 files, about +2200/-15. Over both targets; the PRD's delivery slicing explains why packaging, approval and install ship together: the install path's core safety property (only approved versions, only into the chosen directory) cannot be tested without the approval gate, and an approval gate nothing enforces would be unreviewable on its own.

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0 | test `uv run pytest -q` | PASS | check_criteria.py: passed |
| 1 | test release+install tests | PASS | check_criteria.py: passed |
| 2-6 | test `--help` for package/approve/install/rollback/list | PASS | check_criteria.py: passed |
| 7 | judge | PASS | reviewer ran the tests ("50 passed"), tied every rubric clause to a named test, and ran a real package/approve/install/rollback/list flow with HOME redirected; see docs/ledgers/criteria.json |

Requirements advanced: REQ-005, REQ-006.

## Assumptions
- First package has regression status "no-baseline"; regression compares trigger cases only.
- `approve` is idempotent and verifies manifest checksums first; a tampered version cannot be approved.
- Install and rollback verify `dist/<platform>/` against the manifest; extra, missing or mismatched files refuse the install (exit 1).
- Rollback picks the highest approved version below the installed one and plans every selected install before swapping any.
- `--set-version` exists so a version clash can be tested (exit 2, nothing touched).
- No module in release/install uses subprocess or git.

## Blocked steps
- None.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 3 workers, reviewer) | ~0.36M | wall clock | ~18 min | tool calls (subagents) | ~101 |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.
