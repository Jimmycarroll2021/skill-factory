# Program

Five chained quickship missions built skill-factory from `IDEA.md` on 2026-10-10, and two post-MVP missions on 2026-10-11 added the challenger loop and the model-backed quality tier. The quickship controller
(`scripts/run.sh`) could not run in the build environment because preflight requires an authenticated `gh`,
so the lead loop was driven by hand with the planner, worker, reviewer and security subagents, the ledgers,
the gate and `check_criteria.py`. Each mission's plan, ledgers, criteria evidence and report are archived
under `docs/runs/`. All missions were delivered on one branch and one pull request instead of a PR stack.

| Mission | State | Branch | Evidence |
|---|---|---|---|
| 01-skeleton-capture | READY (lead) | mission/01-skeleton-capture | docs/runs/2026-10-10-01-skeleton-capture/REPORT.md |
| 02-validate-render | READY (lead) | mission/02-validate-render | docs/runs/2026-10-10-02-validate-render/REPORT.md |
| 03-evaluation | READY (lead) | mission/03-evaluation | docs/runs/2026-10-10-03-evaluation/REPORT.md |
| 04-release-install | READY (lead) | mission/04-release-install | docs/runs/2026-10-10-04-release-install/REPORT.md |
| 05-pipeline-docs | READY (lead) | mission/05-pipeline-docs | docs/runs/2026-10-10-05-pipeline-docs/REPORT.md |
| 06-self-improve (post-MVP) | READY (lead) | mission/06-self-improve | docs/runs/2026-10-11-06-self-improve/REPORT.md |
| 07-quality-tier (post-MVP) | READY (lead) | mission/07-quality-tier | docs/runs/2026-10-11-07-quality-tier/REPORT.md |

Every mission: gate PASS (ruff, pytest, uv build), all objective criteria PASS, judge rubric graded PASS by a
reviewer on evidence it produced itself, security PASS on the final commit (low notes only, recorded in each
report and in `docs/decisions.md`). Final test count: 451.
