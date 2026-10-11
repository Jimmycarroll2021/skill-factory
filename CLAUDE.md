# quickship: agent instruction contract v0.3

## What this is
A cooperative Claude Code harness for trusted development projects. The Python controller
owns the deadline, frozen policy, final verification, publishing and authoritative RUN_STATE.
Hooks are safeguards, not an OS sandbox; Claude Code's own Bash sandbox wraps agent shell commands where the
platform supports it. A controller run executes only a base command set plus the brief's quality and test
commands. See docs/design/contracts.md for interfaces.

## Commands
`bash scripts/gate.sh` runs the gate. Missing required checks fail. A brief may supply
quality.lint/test/build commands or explicit skip reasons; docs mode requires an explicit
quality.profile: docs and cannot change application code. Installed copies skip harness
self-tests unless QS_SELFTEST=1. `python scripts/preflight.py` is read-only.
One command per Bash call. Use relative script paths, no inline Python, nested shell -c,
redirects outside the repo or `${PIPESTATUS[0]}`. Use git -C <worktree>, never cd before git.

## Definition of done
Every behaviour change ships with a test that fails without it. Lint, tests and build must
pass or have explicit operator-supplied skip reasons. The controller independently checks the
final gate, criteria, deliverables, security response and GitHub PR. Agent verdicts do not establish DONE.

## PR sizing and evidence
A mission should normally produce one independently reviewable PR with one logical outcome.
Target no more than 500 reviewable changed lines and 10 changed files. Generated files, lockfiles,
snapshots and other mechanically produced artifacts do not count towards the line target. If a mission
must exceed either target, record why splitting it would reduce safety, testability or reviewability;
do not split a cohesive change merely to satisfy a number. Unrelated cleanup belongs in another mission.
The lead must provide the evidence required by `.github/pull_request_template.md` in `docs/REPORT.md`.
The controller owns rendering and publishing the final PR body; the human owns the merge decision.

## Hard rules
Never merge PRs, push, publish packages, deploy or call write-side GitHub tools.
Never force-push, rewrite history, change main/master, access real .env files or write production config.
Never modify the active harness, settings, BRIEF.yaml or runtime state directly.
Runtime state changes go through ledger.py and overseer_status.py only.
Use a mission/<goal-slug> branch, kebab-case, max 24 characters after mission/.
If mission.base exists, build from the current HEAD supplied by program.sh.
Maintenance mode permits harness changes only in owned task-worktree files; main safeguards stay frozen.

## Subagents
Planner creates file-disjoint tasks; worker implements one task in its assigned worktree.
Researcher has web tools and writes one work/_untrusted/<slug>.md. Reviewer and security are read-only.
Strategist prepares briefs before the mission; overseer sets flags.
Before dispatching ANY subagent (planner/reviewer/security included), call
`python scripts/ledger.py step-start <slug> --legs none` (research uses untrusted_content).
Tell it the returned ID. Its FIRST command must be `python scripts/ledger.py step-bind <id>`.
It may then read instructions and work. Each ID belongs to one agent; independent workers may run concurrently.

## Lead loop
Nobody answers questions. Choose a default and record it, skip a block, or submit a safe stop.

0. Anchor: read the frozen .claude/state/brief.json, budgets and this contract.
1. Resume: inspect docs/COMPLETION.json, the ledgers and docs/ledgers/handoff.md, whose last `Next:`
   line is where the previous context stopped. Never overwrite a controller result.
   Create/check out the mission branch from current HEAD. If no ledger exists,
   `python scripts/ledger.py init --goal "<goal>"`. Reuse task branches/worktrees on resume.
   On a resume run `bash scripts/gate.sh` before dispatching anything; a failing gate is the first task.
2. Plan: `python scripts/ledger.py tier plan`; register a planning step and dispatch planner
   with the mandatory bind command. Then `python scripts/ledger.py tier act` separately.
3. Budget: run `python scripts/budget.py` before dispatch. When it reports `near` (75% of any budget,
   also shown as `near=` on the BUDGET line, printed every tenth tool call and whenever a budget is near)
   stop new work: finish the current task,
   commit, write a handoff note and keep the rest for review and the report. Once a budget is exhausted
   only commits, the handoff note, RESULT and REPORT are allowed. Exhaustion means DONE_PARTIAL; cancel
   means SAFE_STOP.
4. Dispatch: register a pending task's step, mark dispatched, append the event and create
   .claude/worktrees/<slug> on <mission-branch>--<slug> only if absent. Give worker its owned files,
   exact worktree and bind ID. Research gets its own distinct registered step and findings path.
5. Integrate: merge each task branch, dispatch a bound reviewer, run the gate. Record merged status
   and commit or retry failures at most budgets.critic_rounds times, then mark failed. Record assumptions and blocked
   steps in ledgers/decisions; workers return those to you rather than writing unowned files.
   After each merge or failure write `python scripts/ledger.py handoff "<done>" --next "<next>"`
   so a fresh context can continue without re-deriving where you were.
6. Replan: use stall-check/replan within limits, invalidate disproved facts. When the flags show
   `force_replan=yes`, run `ledger.py replan` (it clears the flag) and re-dispatch the planner with the
   reason. After merge clean up task worktrees with git worktree remove and task branches with git branch -d.
7. Criteria: `python scripts/check_criteria.py`; fix failures while resources allow. Dispatch a
   bound reviewer to independently grade judge criteria and quote evidence. Tell the reviewer each judge's
   zero-based index in the entire success_criteria array, not its ordinal among judges. Record its grades with
   `python scripts/check_criteria.py --judge <index> PASS|FAIL --evidence "<quoted output>"`.
8. Report: write docs/REPORT.md with work, criteria, assumptions, blocks, API-equivalent budget
   estimates and gaps. Commit intended code, ledger and report changes on the mission branch.
   Dispatch a bound security agent on that FINAL commit. Its actual response is captured by the hook.
   If it fails, fix findings, commit and dispatch security again, within the same critic_rounds bound.
   Never fabricate its verdict.
9. Submit: write docs/RESULT.json as {"state":"READY","reason":"implementation and reviews complete"}.
   Alternatives are DONE_PARTIAL, SAFE_STOP or HALT with a reason. RESULT may remain untracked.
   Never write RUN_STATE or COMPLETION.json, never push or open a PR. Stop after submitting.
   The controller reruns verification and alone publishes. Missing deliverables, unresolved
   high/medium findings or deferred judge grades mean incomplete, never READY.

.claude/state/, .claude/worktrees/, work/_untrusted/ and Python caches are ignored. Never add them.
