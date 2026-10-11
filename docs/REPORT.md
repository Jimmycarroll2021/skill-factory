# Mission 07 report: model-backed quality tier

## State
READY (lead verdict; the quickship controller did not run, see mission 01 report)

## Work
| task | outcome | commit |
|---|---|---|
| behaviour-cases | one behaviour case per spec behaviour (`beh-NNN`) and per trigger (`beh-tNNN`), each with a task prompt, a rubric and expected markers; deterministic; 18 tests | 34bdda4 |
| docs-quality-tier | README "Model-backed quality tier" (six-tier ladder), command rows, CHANGELOG, docs tests | 5b00b09 |
| judge-core | `judge.py` + `skillz judge`: with/without-skill transcripts per case, blind A/B labelling, strict JSON verdict parsing (`JudgeError` names the field), cross-model default when both CLIs exist, `skipped: no runner` / `skipped: no judge`; 53 tests | cad85a8 |
| quality-integration | `challenge --runner/--judge` folds behavioural regressions and improvements into the verdict; `package --runner` stores `reports/judge.json` and `regression_check` compares with-skill totals against the last approved version; `run` gains a `judge` stage and reuses its report in package; 37 tests | 11340e4 |
| runner-neutral-cwd (lead finding) | model CLIs now run in a fresh temp cwd with `CLAUDECODE` and `CLAUDE_CODE_SESSION_ID` dropped: a nested Claude in this repository had picked up the quickship CLAUDE.md and hooks and acted as a lead; 6 new tests | 92f5f4c |
| step L (lead) | real `skillz judge csv-data-cleaner --runner claude`: 5 cases, 5 skill-better, 0 errors, same-model (codex absent); committed as `docs/evidence/judge-claude.{json,md}` with temp paths in transcripts replaced by placeholders | 7f20f14 |

Gate after the final merge: `gate: PASS` (ruff, pytest 451 passed, uv build). Diff against main: 29 files, about +3060/-63. Over both targets: the judge, its integration into challenge/package/run and the richer cases are one outcome; shipping the judge without the integration would leave the brief's regression and challenge clauses untestable (docs/plan.md, assumption 6).

## Criteria
| # | kind | result | evidence |
|---|---|---|---|
| 0-3 | test (pytest, the two test files, `skillz judge --help`, `validate skill-factory`) | PASS | check_criteria.py: passed |
| 4 | file README "skillz judge" | PASS | check_criteria.py: passed |
| 5 | judge | PASS | reviewer ran the tests ("133 passed"), tied each clause to named tests, and quoted the committed evidence summary and a case's scores and reason; see docs/ledgers/criteria.json |

Requirements advanced: REQ-010.

## Assumptions
- The judge verdict follows the judge's declared winner; `score_delta` is informational.
- Transcripts are truncated to 4000 characters before judging and in the report.
- With `--runner`, the pipeline calls the model twice per case for the evaluate baseline and twice plus one judgement per case for the judge stage; package reuses the report.
- The committed evidence is same-model (Claude judging Claude) because Codex is not installed here; the report says so. Cross-model is exercised by tests with fakes.
- Local temp paths that the model echoed into transcripts were replaced with `<project>` and `<neutral-cwd>` placeholders in the committed evidence; nothing else was edited.

## Blocked steps
- None. The first evidence run was stopped and discarded after the nested Claude picked up the repository's harness; the runner fix above removed the cause and the run was repeated.

## Budget used (API-equivalent estimate)
| subagent tokens (planner, 5 workers, reviewer) | ~0.55M | wall clock | ~40 min | tool calls (subagents) | ~160 | real model calls for evidence | 15 (plus a discarded partial run) |

## Gaps
- No controller verification or per-mission PR (see mission 01 report). Security review follows on the final commit.
- Cross-model judging has not been run for real (no Codex here).
