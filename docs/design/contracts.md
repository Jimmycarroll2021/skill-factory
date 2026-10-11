# quickship runtime contracts

## v0.3 controller contract (supersedes conflicting v0.2 sections below)

The supported entrypoint remains `bash scripts/run.sh` / `run.cmd`; it delegates to a Python supervisor.
Runtime schema is 3. The controller freezes the validated brief and harness hashes, enforces an absolute
deadline across resumes, and alone publishes. Agents submit `docs/RESULT.json` (`state`: READY,
DONE_PARTIAL, SAFE_STOP or HALT; `reason`: string). They do not write the authoritative RUN_STATE.
Actual security responses and reviewer judge grades are captured by SubagentStop hooks, not supplied by the lead.
Judge evidence must match the frozen rubric and the current deliverable hashes; security must match the commit at binding and completion.
DONE requires a fresh gate, complete criteria including evidenced judge grades, all deliverables,
security PASS on the final commit, a verified mission branch/PR, and an unchanged active harness.
Before creating a new PR, the controller renders `.github/pull_request_template.md` into
`.claude/state/pr-body.md`, fills the mission branch, the brief's `mission.requirements` identifiers, final
commit, reviewable diff size and controller-verified evidence, and embeds `docs/REPORT.md`. Reviewable line counts exclude known lockfiles, snapshots
and generated directories; the 500-line/10-file target is advisory, not a publication block. CI and human
review checkboxes remain unresolved until GitHub runs and the operator reviews the PR. Installed copies
include the PR template through `scripts/manifest.txt`.
Exit codes: 0 verified DONE; 2 invalid preflight/brief; 3 partial/safe stop; 4 HALT; 5 controller failure.
`docs/COMPLETION.json` carries `retryable` (boolean): true only when a plain rerun can resume the same run, that is
the lead session crashed or failed (`SAFE_STOP`) or an exception interrupted the controller (`ERROR`).

`bash scripts/run.sh --archive` is the operator's recovery command and makes no model call. With a terminal RUN_STATE
it runs `ledger.py archive-stale --force` and prints `archived docs/runs/<at>-<goal-slug>` (exit 0). With no run at all
it prints `run: nothing to archive` (exit 0). With an unfinished run it refuses with exit 2
(`run: the current run is not terminal and its session may still resume; cancel it first ...`). A failed archive
prints `run: archive failed: <error>` (exit 2); any other argument prints the usage line (exit 2).

`quality` is optional: `profile: code|docs` (default code), and `lint`, `test`, `build`, each either
a shell command string or `{skip: "human supplied reason"}`. Node defaults are the package-manager scripts;
Python defaults are Ruff, pytest and build when a build system exists. Missing required checks fail.
An unsupported code stack requires all three explicit entries. Docs mode permits only documentation changes.
`maintenance: true` permits staging owned task-worktree edits to harness files. Changing the active main-checkout harness halts publication.
Apply reviewed harness updates outside an active run, then start a fresh run with the updated safeguards.
Cost figures are API-equivalent estimates for subscription accounts. API-authenticated sessions also receive
the remaining `--max-budget-usd`; subscription sessions never switch billing or enable usage credits.

Runtime mutations are serialized with SQLite transactions; file replacements use unique temporary files.
Each step has a unique ID; subagents bind with `python scripts/ledger.py step-bind <id>` as their first command.
The guard binds the hook's agent_id to that registered step before allowing owned-file writes. The legacy
current_step file is a compatibility view only, never the authority for schema-3 hooks.
Publishing reservations are run-wide, persisted before external calls, and reconciled against GitHub on resume.
Unknown shell constructs, native PowerShell, external connectors, protected writes and agent-side publishing
are refused. In a controller run the shell is also allowlisted: an executable runs only if it is in
`policy.BASE_COMMANDS` (inspection, the gate and state CLIs, git and gh, and the package managers and test
runners the gate drives) or is the first word of a brief `quality` command or a `test` criterion; anything
else is denied with `not in this run's allowlist`. Leading `NAME=value` assignments are stripped before every
rule sees a command. Agent shell commands also run inside Claude Code's OS sandbox (`sandbox` in
`.claude/settings.json`: enabled, no unsandboxed retry, network limited to GitHub, the npm and PyPI
registries and Playwright's browser download hosts) on macOS, Linux and WSL2 when `bubblewrap` and `socat` are installed; native Windows runs them
unsandboxed and `preflight.py` reports which under `sandbox`. The hooks remain cooperative safeguards, not a
sandbox for hostile project code/dependencies.
Preflight is read-only (`python scripts/preflight.py`), supports Python 3.10+, Git Bash/Linux bash 4+,
and Claude Code >=2.1.288. Old active runtime state is preserved and requires explicit migration/restart.
A brief with `quality.profile: docs` and no `mission.base` fails preflight unless `origin/HEAD` resolves,
because the docs profile diffs against it and would otherwise fail the gate only after the model has run.

Every script and hook in this repo builds to these contracts. They are the interface between the lead loop in `CLAUDE.md`, the hooks in `scripts/hooks/`, the CLIs in `scripts/`, and the tests in `tests/`. Change a contract here first, then the code.

## Conventions (all scripts)

- Bash for hooks, Python (stdlib only, 3.10+) for CLIs. LF line endings (`.gitattributes` enforces).
- Python resolution in bash: `PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"`. Python on Windows emits CRLF: strip with `out="${out//$'\r'/}"` before parsing its output in bash.
- State dir: `S="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/.claude/state"` (gitignored). `CLAUDE_PROJECT_DIR` stays at the main checkout inside a worktree; the hook input `cwd` follows the worktree.
- Every hook reads its JSON from stdin and **fails closed**: malformed or missing input → exit 2 with a one-line reason on stderr. A hook that finds no active run (`$S/brief.json` absent) is a no-op: exit 0.
- Hook outcomes. Deny/block: exit 2 and reason on stderr. Allow: exit 0, no stdout. Add context: exit 0 and print exactly one JSON object `{"hookSpecificOutput":{"hookEventName":"<Event>","additionalContext":"<text>"}}`. Stop block alternative: `{"decision":"block","reason":"..."}` with exit 0.
- Hook input common fields: `hook_event_name`, `tool_name`, `tool_input` (`command` for Bash; `file_path` for Read/Write/Edit/MultiEdit), `tool_response` (PostToolUse only), `cwd`, `transcript_path`, `session_id`, and `source` on SessionStart (`startup|resume|clear|compact|fork`).
- Atomic file writes in Python: write to `<path>.tmp` then `os.replace`.
- Timestamps: ISO 8601 UTC with `Z`, e.g. `2026-09-30T09:00:00Z`.
- Tests: `tests/<name>.sh` sources `tests/lib.sh` and uses `ok`, `bad`, `expect_exit <label> <exit> <cmd...>` (captures combined output in `$OUT`), `expect_contains <label> <needle> <haystack>`, `hook <script-name> <json>` (feeds stdin to `scripts/hooks/<name>`), `tmpdir`, `finish`. Tests never call an LLM and never touch the repo's own `.claude/state`; set `export CLAUDE_PROJECT_DIR="$(tmpdir)"` first. `bash tests/run.sh` runs them all.

## Files

| Path | Written by | Read by |
|---|---|---|
| `BRIEF.yaml` | human, once | `scripts/brief.py` |
| `.claude/state/brief.json` | `brief.py validate` | every hook and script |
| `.claude/state/started_at` | `brief.py validate` (only if absent) | `budget.py` |
| `.claude/state/steps` | `hooks/budget.sh` (PostToolUse) | `budget.py` |
| `.claude/state/tier` | lead (`plan` or `act`) | `hooks/guard.sh` |
| `.claude/state/current_step.json` | lead at dispatch | `hooks/idem.sh`, `hooks/guard.sh` |
| `.claude/state/legs/<step-id>` | `hooks/guard.sh` (PostToolUse) | `hooks/guard.sh` |
| `.claude/state/idem.jsonl` | `hooks/idem.sh` | `hooks/idem.sh` |
| `.claude/state/cancel`, `.claude/state/force_replan` | overseer (existence is the signal) | lead, `hooks/guard.sh` |
| `.claude/state/stop_attempts`, `restarts`, `session_id`, `hook_log` | `hooks/stop.sh`, `run.sh`, `run.sh`, `hooks/guard.sh` | same |
| `docs/ledgers/task.json` | `scripts/ledger.py` | lead, `hooks/anchor.sh`, overseer |
| `docs/ledgers/progress.jsonl` | `scripts/ledger.py append` | lead, overseer |
| `docs/ledgers/handoff.md` | lead (`scripts/ledger.py handoff`) | `hooks/anchor.sh`, the lead's next context |
| `docs/ledgers/criteria.json` | `scripts/check_criteria.py` | lead, report |
| `docs/RUN_STATE`, `docs/COMPLETION.json` | the controller (`scripts/runner.py`) only; agents submit `docs/RESULT.json` instead | `hooks/stop.sh` (v0.2 path), `program.sh`, the controller on resume |
| `docs/REPORT.md` | lead, last | human |
| `docs/overseer.md` | overseer | lead, report |

## BRIEF.yaml → brief.json

`scripts/brief.py validate [--brief BRIEF.yaml]` parses the YAML (PyYAML if importable, else a built-in reader for the strict subset below), validates, writes `$S/brief.json` and `$S/started_at` (if absent), exit 0. Any missing key, non-positive budget, or unknown `kind` → exit 2, stderr names the key. `scripts/brief.py show` prints brief.json.

Strict YAML subset the built-in reader must accept: block mappings with 2-space indent, scalar values, block lists of scalars (`- item`), and block lists of single-line flow maps (`- {kind: test, cmd: "npm test", expect: 0}`). Strings may be quoted with `"` or `'`. No anchors, multi-line scalars or nested flow.

```yaml
mission:
  goal: "Add a README that describes quickship"
  deliverables:
    - README.md
success_criteria:
  - {kind: test, cmd: "bash tests/run.sh", expect: 0}
  - {kind: file, path: README.md, must_contain: "quickship"}
  - {kind: grep, pattern: "## Lead loop", path: CLAUDE.md}
  - {kind: judge, rubric: "README explains what a Mission Brief is"}
budgets:
  tokens: 5000000
  cost_usd: 40
  wall_clock_min: 480
  steps: 400
  stall_limit: 3
  replan_limit: 5
  critic_rounds: 2
permissions:
  irreversible:
    default: skip-and-record
    allow:
      - "git push origin mission/*"
      - "gh pr create*"
ambiguity_policy: choose-default-and-record
```

brief.json is the same structure as JSON. Required: `mission.goal` (string), `mission.deliverables` (list). Optional: `mission.requirements` (list of stable `REQ-xxx` identifiers from `docs/PRD.md`, rendered into the PR's Traceability section) and `mission.base` (branch name, set by `scripts/program.sh`). Required elsewhere: `success_criteria` (list, each with `kind` in `test|file|grep|judge` and that kind's fields: test → `cmd`, `expect` (int, default 0); file → `path`, optional `must_contain` regex; grep → `pattern`, `path`; judge → `rubric`), `budgets` (all seven integers/floats > 0; defaults: stall_limit 3, replan_limit 5, critic_rounds 2; `critic_rounds` bounds the lead's fix-and-re-review retries per task and the security fix loop), `permissions.irreversible.default` in `skip-and-record|allow`, `permissions.irreversible.allow` list of shell-glob patterns, `ambiguity_policy` = `choose-default-and-record`.


`validate` writes `brief.json`. Under the v0.3 controller a brief change is a mission boundary only through the controller: after a verified `DONE`, a brief with a new goal archives the finished run and starts fresh; a change to the `budgets` block alone keeps the same run with the new limits; any other change to an active or unfinished run is refused until the operator runs `bash scripts/run.sh --archive`. Archiving moves the run's documents and ledgers to `docs/runs/` and clears the gitignored runtime state (controller state, session, counters, flags, transcript record).
## Ledgers

`docs/ledgers/task.json`:
```json
{"goal": "...", 
 "plan": [{"slug": "add-readme", "goal": "...", "owns": ["README.md"], "status": "pending", "branch": "", "commit": ""}],
 "facts": [{"text": "...", "source": "...", "ts": "...", "valid": true}],
 "assumptions": [{"text": "...", "ts": "..."}],
 "blocked": [{"step": "...", "rule": "...", "ts": "..."}],
 "replan_count": 0, "stall_count": 0, "is_complete": false}
```
`status` ∈ `pending|dispatched|merged|failed|skipped`. Slugs are kebab-case, max 24 chars.

`docs/ledgers/progress.jsonl`, one object per line, append-only:
```json
{"ts": "...", "step": "s007", "slug": "add-readme", "event": "dispatched", "detail": "...", "state_hash": "<sha256 hex>", "tokens": 0, "cost_usd": 0.0}
```
`event` ∈ `dispatched|merged|failed|timeout|assumption|blocked|replan|stall|criteria|note`. `step` is `s` plus a zero-padded 3-digit counter, unique per run.

`scripts/ledger.py` subcommands (all exit 0 on success unless stated; 2 on bad args or missing files):
- `init --goal "<text>"` creates both ledgers (fails with exit 2 if task.json exists unless `--force`).
- `task-add <slug> --goal "<text>" --owns a,b,c` appends a pending task.
- `task-set <slug> <status> [--branch b] [--commit sha]`.
- `append <event> <slug> "<detail>" [--tokens N] [--cost F]` writes one progress line with the current `hash` and the next step id.
- `hash` prints sha256 of task.json with `replan_count`, `stall_count` and timestamps excluded.
- `stall-check` exits 1 and increments `stall_count` when any of: the last two progress lines with event `dispatched` have the same slug and the same task goal; the `state_hash` of the last progress line equals the one before it; the last three `failed` lines share the same `detail`; the last line is a `timeout`. Exit 0 otherwise. Prints the reason.
- `replan` increments `replan_count`, resets `stall_count` to 0, writes a `replan` progress line and deletes `$S/force_replan` when the overseer set it (printing `cleared force_replan`); exit 3 if the new `replan_count` exceeds `budgets.replan_limit` from brief.json.
- `handoff "<done>" --next "<next>" [--note "<caution>"]...` appends a section `## <utc ts>` with `Done:`, `Next:` and any `Note:` lines to `docs/ledgers/handoff.md` (created with a header on first use) and prints the path. Prose for the next context window; the machine state stays in task.json and progress.jsonl. The lead writes one after every merge or failure and before synthesis.
- `step-start <slug> --legs a,b` writes `$S/current_step.json` with the next step id; exit 2 if legs contain both `untrusted_content` and `outbound`.
- `facts-invalidate "<substring>"` marks matching facts `valid: false`.
- `tier [plan|act]` sets or prints `$S/tier` (the lead must never write it with a shell redirect: Claude Code protects `.claude/` paths).

## Budgets

`scripts/budget.py [--transcript <path>] [--exhausted-only]` reads brief.json (exit 2 if absent), `started_at`, `steps`, and sums usage from the transcript JSONL (each line may have `message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, and `message.model`) plus any `*.jsonl` files in the same directory whose name starts with the transcript's basename and every `<basename>/subagents/*.jsonl` (subagent transcripts in the current Claude Code layout), so worker, planner and reviewer spend counts against the mission. Cost = tokens × per-model rates from a small built-in table (USD per million: opus in 15 / out 75; sonnet in 3 / out 15; haiku in 1 / out 5; cache read at 10% of input; a model not in the table is priced at the highest rate in it, so the cost cap errs high; the table is an estimate, edit `RATES` in `budget.py` for current prices). Prints:
```json
{"tokens": 0, "cache_read_tokens": 0, "tokens_total": 0, "cost_usd": 0.0, "elapsed_min": 0.0, "steps": 0, "limits": {"tokens": 0, "cost_usd": 0, "wall_clock_min": 0, "steps": 0}, "exhausted": []}
```
`tokens` = input + output + cache-creation tokens; cache reads are re-reads of context the run already paid for and are reported separately as `cache_read_tokens` (`tokens_total` is all four summed). Cost prices cache reads at 10% and cache creation at 125% of the input rate. Cost and wall clock are the limits that bite in practice; `tokens` is a backstop.

`exhausted` lists every dimension at or over its limit. With `--exhausted-only` prints only the comma-separated names (empty output when none). Exit 0 in both forms.

`scripts/hooks/budget.sh` (delegating to `scripts/budget_hook.py`) runs on PreToolUse, PostToolUse and PostToolUseFailure (matcher `.*`). PostToolUse counts the call in `$S/steps` and, on every tenth step and whenever `near` or `exhausted` is non-empty, prints additionalContext `BUDGET tokens=<n>/<limit> cost=<f>/<limit> min=<f>/<limit> steps=<n>/<limit>`, followed by ` near=<names>: stop dispatching; finish the current task, commit, write a handoff note, go to synthesis` when `near` is non-empty. A quiet step prints nothing: the line is context the lead pays for on every call, and a readout that has not changed is not signal. PreToolUse: once any dimension is exhausted, only reads, `Write`/`Edit` of `docs/RESULT.json` or `docs/REPORT.md`, and the wrap-up commands are allowed: a single `git add` or `git commit` (optionally `git -C <dir>`) or `python scripts/ledger.py handoff`, with no `;`, `&&`, `|`, backtick, `$(` or newline, so saved work is never lost to the cut-off and nothing else rides along. Wrap-up commands and report writes are not counted as steps. Everything else is denied with `budget exhausted (<names>): write docs/RESULT.json and docs/REPORT.md, then stop`. The hook fails closed: an error reading budget state denies the call.

`budget.py` also prints `near`: the dimensions at or past `NEAR_FRACTION` (75%) of their limit but not over it. That is the one wrap-up number: `CLAUDE.md` step 3, the controller's "reserve 25% for synthesis" prompt and the BUDGET line all mean it. The lead treats a non-empty `near` as the signal to stop dispatching, finish the current task, commit, write a handoff note and go to synthesis, because once a dimension is exhausted `budget.sh` allows only the wrap-up commands and the report files.

## Termination

`docs/RUN_STATE` is exactly one line: `{"state": "DONE|DONE_PARTIAL|SAFE_STOP|HALT", "reason": "...", "at": "..."}`.

A merged mission PR carries `docs/RUN_STATE`, `REPORT.md`, `plan.md` and `docs/ledgers/` into the next mission's checkout. `ledger.py archive-stale` (run by `run.sh` after the brief validates, and by the lead at step 1) prints `current` when there is no terminal RUN_STATE, or its ledgers carry the brief's goal, or `controller.json`'s frozen brief carries it (a lead that crashed before `ledger.py init` leaves no task.json); otherwise it moves those files to `docs/runs/<at>-<goal-slug>/`, deletes the runtime state (`session_id`, `steps`, `restarts`, `stop_attempts`, `idem.jsonl`, `current_step.json`, `tier`, `cancel`, `force_replan`, `transcript_path`, `last_run.json`, `legs/`), rewrites `started_at`, and prints `archived <dir>`. `--force` archives a terminal run whatever its goal; `run.sh --archive` uses it.

`scripts/hooks/stop.sh` (Stop hook): the overseer and strategist roles exit 0. Under the v0.3 controller (`controller.json` present) it blocks the stop until `docs/RESULT.json` exists with `state` READY, DONE_PARTIAL, SAFE_STOP or HALT; the controller then verifies and decides. Without a controller and without `brief.json` it runs the gate. The v0.2 path below (RUN_STATE reminders, SAFE_STOP on the third attempt) applies only to runs with `brief.json` and no controller.

## Idempotency

`scripts/hooks/idem.sh` runs on PreToolUse and PostToolUse with matcher `Bash|mcp__.*`. For Bash it matches subcommands starting with `git push`, `git -C <dir> push` or `gh (pr|issue|release) create`; for an MCP tool named `mcp__<server>__(create_pull_request|create_issue|create_release)` the key is sha256 of `<step id>
<tool name>:<JSON of tool_input head, base, title>` and the ledger `cmd` is `<tool name> head=<head> base=<base>` (this covers cloud sessions, which have no `gh`); every other command or tool → exit 0 immediately. Key = sha256 of `<step id from current_step.json, or "nostep">\n<command with whitespace runs collapsed>`. Ledger `$S/idem.jsonl` lines: `{"key": "...", "step": "s007", "cmd": "...", "status": "pending|done", "exit": 0, "ts": "..."}`. PreToolUse: if a line with this key has `status: done` → exit 2 with stderr `already executed at <ts> (exit <n>); use the recorded result, do not retry`; else append `pending`. PostToolUse: append a `done` line with the exit code from `tool_response` (best effort; 0 if absent).

## Anchoring

`scripts/hooks/anchor.sh` (SessionStart, matcher `compact|resume`): no-op without brief.json. Prints additionalContext (under 9000 characters) containing: `<mission-brief>` with goal, deliverables, success_criteria one per line, budgets, permissions; `<plan>` as a table slug|status|goal from task.json; `<handoff>` with the newest `## ` section of `docs/ledgers/handoff.md` (at most 1200 characters, `none yet` when absent); `<progress>` with the last five progress.jsonl events as `ts step slug event: detail` (details cut at 200 characters, malformed lines skipped, `none yet` when empty); `<recent-files>` with at most five paths the previous context touched last, uncommitted changes first (`git status --porcelain`) then the files of the last three commits, deduplicated, `none yet` outside a git repository; `<assumptions>`, `<blocked>` lists; the `budget.py` one-line summary; `flags: cancel=<yes|no> force_replan=<yes|no>`; and a closing instruction that begins `You never ask a question.`, says the files above outrank memory, tells the lead to read `<handoff>` and `<recent-files>` before anything else in the code and fetch the rest only as needed, then to continue the lead loop from step 1, run the gate before dispatching anything new, and pick up the `<handoff>` Next line. Compaction is not the continuity mechanism; these files are. The pack is small on purpose: identifiers and the last few artefacts up front, everything else retrieved just in time.

## Criteria

`scripts/check_criteria.py [--cwd <dir>]` reads brief.json, runs each criterion: `test` → subprocess `bash -c cmd` with a 600 s timeout, pass when exit == expect; `file` → path exists and, if `must_contain`, the regex matches the content; `grep` → regex matches the file; `judge` → `{"status": "deferred"}`. Writes `docs/ledgers/criteria.json` as `{"results": [{"kind": ..., "status": "pass|fail|deferred", "detail": "..."}], "passed": n, "failed": n, "deferred": n}`. Exit 0 when `failed == 0`, else 2.


`check_criteria.py --judge <index> PASS|FAIL --evidence "<quoted output>"` records the reviewer's grade for the judge criterion at that 0-based index: the entry's `status` becomes `pass` or `fail`, its `detail` becomes `<grade> by reviewer; evidence: <text>`, counts are recomputed and the exit is 2 if any criterion is then failed. `--evidence` is mandatory; a grade without quoted command output is rejected.
## Guard: per-agent clauses and the decision log

`hooks/guard.sh` reads `agent_type` from the hook input (absent on the lead's own calls). With `agent_type: reviewer` the reviewer may only run read-only commands (the plan-tier set), `python3 scripts/check_criteria.py`, the project's test or eval runners (`pytest`, `npm test`, `npm run <script>`, `pnpm test`, `yarn test`, `uv run ...`, `make test`, `cargo test`, `go test`) and any `success_criteria` `test` command from brief.json verbatim; no redirects, no `tee`, no git writes, no `gh`; Write/Edit are denied. With `QS_ROLE=overseer` writes are limited to `docs/overseer.md` and the two flag files and Bash to `overseer_status.py`, `budget.py` and read-only commands. MCP tools take part in the trifecta rule: `mcp__*__(create_pull_request|update_pull_request|merge_pull_request|create_or_update_file|push_files|create_issue|add_issue_comment|create_release|create_branch)` is an `outbound` leg, `mcp__*__(get|list|search|read|fetch|download)_*` is `untrusted_content`.

Every denial appends `<utc ts>	DENY	<tool>	<reason>	<arg>` (five tab-separated fields) to `$S/hook_log`; allowed PreToolUse calls keep the three-field line `<ts>	<tool>	<arg>`. `overseer_status.py` counts trailing consecutive DENY lines with the same reason as `repeated_denials`.

## Patch release validation and cancellation

The controller checks `.claude/state/cancel` before final verification, after the gate and criteria checks,
before each publication command, and before recording DONE. Cancellation records non-retryable SAFE_STOP.
Successful push and PR-create commands update the publication receipt (`pushed` or `pr-created`); SAFE_STOP
includes any recorded receipt, so completed side effects are retained for reconciliation rather than erased.

The documentation-only profile compares against `mission.base`, otherwise `origin/HEAD`. Failure to resolve
or compare that base fails the profile; an empty working-tree diff is not an alternative baseline.

Standalone harness self-test commands have a 3600-second timeout. Ordinary project checks keep their
900-second default. When the controller supplies a deadline, its remaining time governs every check;
an already-expired deadline records exit 124 without launching a subprocess. Gate exits remain 0 or 2.

`tests/run.sh` accepts `QS_TEST_JOBS` as an integer from 1 to 1024. Its default is 1 on Windows and at most
4 elsewhere. Installed-copy tests inherit this limit and set `QS_INIT_NESTED=1` to prevent recursion.
An optional `QS_TEST_LOG_DIR` retains a unique `run.*` directory per suite with `jobs`, `<script>.out`
and `<script>.rc`. Without it, temporary logs are deleted as before. Test results stay filename-ordered.
Logs may contain private data and must not be uploaded without review.

## Install and upgrade behavior

`scripts/init.sh <target-dir> [--force] [--upgrade]` installs the files listed in `scripts/manifest.txt` into a project (git-initialising it if needed), appends the four ignore lines (`.claude/worktrees/`, `.claude/state/`, `work/_untrusted/`, `__pycache__/`), creates `docs/decisions.md` and `BRIEF.yaml` when absent, and records `.quickship/VERSION` and `.quickship/manifest.sha256`. A target file that differs from the source is skipped unless `--force`; `--upgrade` replaces only files whose current sha still matches the recorded one. `CLAUDE.md` is never overwritten. `run.cmd` and `init.cmd` are Windows wrappers that locate Git Bash.

`.quickship/VERSION` also tells `scripts/gate.sh` it is running in an installed copy: it then skips `bash tests/run.sh` (the harness self-tests) with the notice `gate: harness self-tests skipped in an installed copy (QS_SELFTEST=1 runs them)`. `QS_SELFTEST=1` runs them anyway. In the quickship repo itself, where the file is absent, the gate always runs them.

## Overseer

`scripts/overseer.sh` runs one overseer tick: `QS_ROLE=overseer claude -p "<prefix + .claude/agents/overseer.md body without frontmatter>" --max-turns 8 --permission-mode acceptEdits --permission-prompts none --allowedTools "Read,Glob,Grep,Write,Edit,Bash(python3 scripts/overseer_status.py *),Bash(python scripts/overseer_status.py *),Bash(python3 scripts/budget.py *),Bash(python scripts/budget.py *)" --mcp-config '{"mcpServers":{}}' --strict-mcp-config --output-format json`. The overseer runs exactly one command, `python3 scripts/overseer_status.py`, which prints one JSON object (`goal, run_state, tasks{pending,dispatched,merged,failed,skipped}, replan_count, replan_limit, stall_count, stall_limit, newest_progress_ts, newest_progress_age_min, last3_same_slug_and_hash, repeated_denials{reason,count}, budget, flags{cancel,force_replan}, progress_tail, warnings`) and decides from it alone; it never chains commands. Flags are created with `python3 scripts/overseer_status.py --set-flag cancel|force_replan [--reason "..."]` (the Write tool is refused on `.claude/` paths in an unattended session); an existing flag is left untouched. `last3_same_slug_and_hash` considers only work events (`dispatched`, `failed`, `timeout`, `retry`, `replan`): assumption, note, blocked, merged and criteria lines repeat a hash legitimately. Its write scope is enforced by `hooks/guard.sh` (QS_ROLE=overseer): only `docs/overseer.md`, `.claude/state/force_replan` and `.claude/state/cancel`, exits 0 when the JSON result has `is_error: false`. It must be testable with a stub `claude` on PATH. `.claude/agents/overseer.md` tells the overseer: read both ledgers, the progress tail, `budget.py` output and `docs/decisions.md`; append a dated note to `docs/overseer.md`; create `.claude/state/force_replan` when the last 3 progress lines share slug and state_hash; create `.claude/state/cancel` when `replan_count >= replan_limit`, when no progress line is newer than 45 minutes, or when `hook_log` shows the same denied command 5 times in a row; never do anything else.

## Idea to missions

`scripts/idea.sh [idea-file]` (default `IDEA.md`) runs the `strategist` agent headlessly (`QS_ROLE=strategist`; the Stop hook skips the gate for it; the guard's hard rules still apply). It writes `docs/PRD.md` and two to five briefs `docs/missions/NN-<slug>.yaml`, then runs `brief.py check` on them; one retry with the errors in the prompt, then exit 2. Exit 0 prints the mission files and `bash scripts/program.sh`.

`scripts/brief.py check <file>...` validates brief files with the same rules as `validate` but writes nothing; exit 2 names the file and key. A brief may set `mission.base` (a branch name): the branch a chained mission starts from and opens its PR against. `brief.json` carries `mission.base` only when set.

## Mission chains

`scripts/program.sh [missions-dir]` (default `docs/missions`) runs `NN-*.yaml` in order. It checks every brief first (exit 2 on any failure, nothing runs). For each mission not already `DONE` in `.claude/state/program.tsv` (`file<TAB>state<TAB>branch`): check out the previous mission's branch detached (the current HEAD for the first), write `BRIEF.yaml` from the mission file with `mission.base` set to that branch (none for the first), commit it on the detached HEAD, and call `scripts/run.sh` (`QS_RUN` overrides) up to six times. A RUN_STATE counts for this mission only when this mission's runs wrote it: it is newer than `.claude/state/program-mark` (touched before each invocation) or differs from the copy taken before the invocation and the copy in `.claude/state/program-baseline` (taken when the mission began), so a predecessor's `DONE` left behind by a run.sh that failed early is never read as this mission's. It re-invokes run.sh while no fresh terminal state exists or `docs/COMPLETION.json` says `"retryable": true`; the runner's own five-launch limit still applies. Exit 2 from run.sh (brief rejected) stops at once. `DONE` counts only with run.sh exit 0; anything else is recorded as the state, or `FAILED rc=<n>` when no fresh state exists. It appends a row to `docs/PROGRAM.md` (mission, state, branch, PR URL from `docs/REPORT.md`). A state other than `DONE` stops the chain with exit 3. Re-running skips `DONE` missions. `main` never receives a commit.

`scripts/diffbase.sh` prefers the brief's `mission.base` (`origin/<base>`, else `<base>`) over `origin/main`, so the reviewer and `security` see only the current mission's diff.

## Security review

`.claude/agents/security.md` is read-only. The guard applies the reviewer clause (1b) to `agent_type == security` as well. The lead runs it in synthesis after the critic: high and medium findings become tasks while `critic_rounds` remain, and any left open make the run `DONE_PARTIAL` and are listed in the report.
