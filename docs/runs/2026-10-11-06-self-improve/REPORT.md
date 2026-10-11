# Mission 06 report: the challenger loop

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| loop-foundation | `captured.py` (`skillz capture-failure`, persistent cap-NNN cases included by every later eval, package regression and challenge), `candidates.py` (candidate layout, id check, digests, verdict order); 26 tests | f3a513e |
| improve-challenge | `skillz improve` (candidate from eval proposals and captured failures, stable untouched) and `skillz challenge` (validation + union of cases on both sides, verdict candidate-unsafe > candidate-worse > candidate-better > no-difference, challenge.json/md); 27 tests | be54daf |
| promote-cmd | `skillz promote` (refuses worse/unsafe/stale/already-promoted, atomic replace with backups, packages a new awaiting-approval version, restores on failure; never called by the pipeline); 29 tests | 09c80b7 |
| loop-cli-docs | CLI wiring, end-to-end loop tests, README loop section + diagram + table, CHANGELOG Unreleased, SKILL.md loop instructions; dogfood on `skills/skill-factory`: captured "Help me write a SKILL.md file so my agent learns this workflow" (0.14 on stable), challenge candidate-better (1 improvement, 0 regressions), promoted as 0.1.0 awaiting approval; evidence in `docs/evidence/` | e9cb1b2 |

Gate after the final merge: `gate: PASS` (ruff, pytest 334 passed, uv build). Diff against mission 05: 24 files, about +3250/-23. Over both targets: the four commands are one loop and promote cannot be reviewed without challenge, improve and captured cases; the plan records the reason.

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0-6 | test (pytest, the two test files, four `--help`, `validate skill-factory`) | PASS | check_criteria.py: passed |
| 7 | file README "skillz challenge" | PASS | check_criteria.py: passed |
| 8 | judge | PASS | reviewer ran the tests ("88 passed"), tied each clause to a named test, read the committed evidence ("Verdict: **candidate-better**", cap-001 fail -> pass) and ran a real capture/improve/challenge/promote loop; see docs/ledgers/criteria.json |

Requirements advanced: REQ-009.

## Assumptions
- `improve` exits 1 when there is nothing to improve; SKILL.md body is copied byte for byte and only the description changes.
- `candidate-unsafe` whenever the candidate has any error-level validation finding.
- `promote` refuses a second promotion of the same candidate; only differing files are replaced; backups live under the candidate directory.
- The meta-skill's promoted 0.1.0 version is awaiting approval and was not approved by the lead.

## Blocked steps
- None.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 4 workers, reviewer) | ~0.55M | wall clock | ~30 min | tool calls (subagents) | ~150 |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.
- Improvements are deterministic vocabulary and trigger edits; a model-assisted rewrite stays out of scope.

## Security
PASS on commit 2935efd. Low notes: captured prompts stored verbatim in spec triggers (only vocabulary reaches the frontmatter today; a length cap would be sensible); challenge.md cells escape only pipes; small TOCTOU in promote; no size cap on captured.json records.
