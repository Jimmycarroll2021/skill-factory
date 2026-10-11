Assumptions: (1) Trigger-derived behaviour cases get their own positional ids `beh-tNNN` (behaviour-derived keep `beh-NNN`), so adding a behaviour never renumbers a trigger case and `test_ids_do_not_depend_on_other_kinds` keeps its meaning; both have `kind: "behaviour"`. (2) `rubric` is emitted by `Case.to_dict()` only when non-empty, so positive, negative and captured cases serialise exactly as today. (3) Existing tests that count behaviour cases (`tests/test_eval.py`, `tests/test_evaluate.py`), the challenge key set (`tests/test_challenge.py`) and the pipeline stage list (`tests/test_pipeline.py`) are updated by the task that deliberately changes that behaviour. (4) The single fake-runner seam for everything model-backed is `judge.RUNNER_FACTORY` (or an explicit `runner_factory` argument). (5) The real-Claude evidence is produced by the lead after tasks 1-4 merge; no task writes a placeholder. (6) The mission exceeds the 10-file / 500-line PR target (about 18 files): REQ-010 is one outcome (judge + the verdicts that consume it); shipping the judge without challenge/package integration, or integration without its evidence, would not be independently testable against the brief. The lead records this in docs/REPORT.md.

# Mission 07 plan: model-backed quality tier (REQ-010)

Merge order: 1 (behaviour-cases) and 2 (docs-quality-tier) first, in either order; then 3 (judge-core), which must merge only after 2 because `tests/test_docs.py::test_readme_documents_run_and_every_subcommand` requires `skillz judge` in README once the subcommand exists; then 4 (quality-integration), dispatched only after 1 and 3 are merged. Finally the lead step L.

## Shared contracts (all tasks follow these exactly)

### Cases (`src/skill_factory/evalcases.py`)
- `Case(id, kind, prompt, source, expected=[], rubric=[])`: `rubric: list[str] = field(default_factory=list)` is the LAST field, so every existing positional constructor (captured.py, release.py, tests) keeps working. `to_dict()` adds `"rubric"` only when the list is non-empty.
- `generate_cases(spec)` order: positives, negatives (unchanged, byte for byte), then behaviour-derived cases `beh-001..` (source `behaviour:<i>`, one per `spec.behaviours[i]`), then trigger-derived cases `beh-t001..` (source `trigger:<i>`, one per `spec.triggers[i]`). All have `kind == "behaviour"`.
- `prompt`: a realistic task request built deterministically from the trigger text (behaviour case i uses `spec.triggers[i % len(triggers)]`, or the first description sentence when there are no triggers; trigger case i uses trigger i). It must not contain the behaviour sentence verbatim (the skill, not the prompt, should supply the behaviour).
- `rubric` (list of strings, non-empty for every generated behaviour case): behaviour case = `[<behaviour sentence>, "mentions: <m1>, <m2>, ..."]`; trigger case = every spec behaviour sentence (or `"Completes the request: <trigger>"` when there are none) plus `"mentions: ..."`. When the spec has non-triggers, append at most one criterion `"must not: <text derived from the first non-trigger>"`.
- `expected`: behaviour case = `_markers(behaviour)` (unchanged); trigger case = ordered, de-duplicated union of all behaviours' markers, or content words of the trigger (max 3) when there are no behaviours.
- `cases_json` stays byte-identical for the same spec.

### Judge (`src/skill_factory/judge.py`)
- Literals: `SKIPPED_NO_RUNNER = runner.SKIPPED_NO_RUNNER` ("skipped: no runner"), `SKIPPED_NO_JUDGE = "skipped: no judge"`, `DIMENSIONS = ("instruction_following", "task_quality")`, scores are ints 0..5, `TRANSCRIPT_LIMIT = 4000` chars.
- `class JudgeError(ValueError)`.
- `RUNNER_FACTORY: Callable[[str], Runner | None] = runner.detect_runner` (looked up at call time).
- `JUDGE_PROMPT`: a `str.format` template with the case prompt, the rubric as a bullet list, transcript A and transcript B in clearly delimited blocks, scoring instructions for both dimensions, and the demand to reply with only `{"a": {"instruction_following": 0-5, "task_quality": 0-5}, "b": {...}, "winner": "a"|"b"|"tie", "reason": "..."}`.
- `with_skill_label(case_id: str) -> "a" | "b"`: deterministic from a SHA-256 of the case id (both labels occur across ids).
- `parse_verdict(text: str) -> dict`: extracts the first JSON object (code fences and prose around it tolerated), validates `a`/`b` objects with both dimensions as ints (bool rejected) in 0..5, `winner` in a/b/tie, `reason` a non-empty string; otherwise raises `JudgeError` with a message naming the field and problem (for example `judge reply: a.task_quality must be an integer 0-5, got 7`; `judge reply contains no JSON object`).
- `choose_judge(runner_name: str, judge_name: str | None = None) -> str`: explicit `judge_name` wins; else the other model when both `shutil.which("claude")` and `shutil.which("codex")` are truthy (looked up at call time); else `runner_name`.
- `resolve_runners(runner_name, judge_name=None, runner_factory=None) -> tuple[Runner | None, Runner | None, str | None]`: `(runner, judge_runner, skip_status)`; skip_status is `None` when both resolve, `SKIPPED_NO_RUNNER` when runner_name is None or not found, `SKIPPED_NO_JUDGE` when the chosen judge is not found. Reuses the runner object when judge == runner name.
- `judge_cases(runner, judge_runner, cases: list[dict], skill_text: str) -> dict`: only `kind == "behaviour"` dicts; for each runs `runner.run(runner.with_skill_prompt(skill_text, prompt))` and `runner.run(prompt)`, asks `judge_runner` with JUDGE_PROMPT (with-skill side under `with_skill_label(id)`), parses with `parse_verdict`. A case without a rubric uses `["mentions: <expected joined>"]`. `RunnerError` or `JudgeError` becomes a per-case `{"id", "prompt", "verdict": "error", "error": "<message>"}`; never raises for those.
- `skipped_judge(status=SKIPPED_NO_RUNNER, requested_runner=None, requested_judge=None) -> dict`.
- `judge_skill(skill_dir, runner_name=None, judge_name=None, runner_factory=None) -> dict`: loads spec via `evaluate.load_spec`, cases via `evalcases.generate_cases(spec) + captured.load_captured(skill_dir)`, resolves runners, writes `<skill>/.factory/judge.json` (sorted keys, indent 2, LF, no timestamps) and `judge.md`, returns the dict with `"skill"` added. Raises `SpecError` for a missing skill dir or SKILL.md, or a symlinked `.factory`.
- `summary_line(report: dict) -> str`: one line used by the CLI and the pipeline stage verdict (status, runner, judge, cross-model or same-model, the four counts).
- `add_parser(sub)` / `run(args)`: `skillz judge <name> [--runner claude|codex] [--judge claude|codex] [--out DIR]`; exit 0 always except 2 on invalid name, missing skill dir/SKILL.md or SpecError.

judge.json schema:
```
{"schema": 1, "skill": str, "status": "completed" | "skipped: no runner" | "skipped: no judge",
 "runner": str | null, "judge": str | null, "cross_model": bool,
 "requested": {"runner": str | null, "judge": str | null},
 "method": str, "dimensions": ["instruction_following", "task_quality"], "scale": [0, 5],
 "cases": [ {"id", "source"?, "prompt", "rubric", "with_skill_label": "a"|"b",
             "with_skill": {"instruction_following": int, "task_quality": int, "total": int},
             "without_skill": {... same ...},
             "winner": "with-skill" | "without-skill" | "tie",
             "verdict": "skill-better" | "skill-worse" | "tie",
             "reason": str, "score_delta": int,   # with total minus without total
             "transcripts": {"with_skill": str, "without_skill": str}}  # each <= TRANSCRIPT_LIMIT
           | {"id", "prompt", "verdict": "error", "error": str} ],
 "summary": {"cases": int, "skill_better": int, "skill_worse": int, "tie": int, "errors": int}}
```
`cross_model` is `runner.name != judge_runner.name`; `false` when skipped. `judge.md` names the runner, the judge and whether it was cross-model, lists per-case scores for both sides, winner, delta and reason, and lists errors.

### Integration
- Challenge: `challenge_candidate(skill_dir, cid, runner_name=None, judge_name=None, runner_factory=None)`. Resolves runners once via `judge.resolve_runners`; runs `judge.judge_cases(runner, judge_runner, behaviour_case_dicts, stable_skill_text)` and the same with the candidate SKILL.md text, on the same union behaviour cases with the same judge. Report always has a `behaviour` key: `{"status": <skip literal>, "requested": {...}}` without runners, else `{"status": "completed", "runner", "judge", "cross_model", "cases": [{"id", "prompt", "stable_total", "candidate_total", "change": "regression"|"improvement"|"unchanged"|"error"}], "summary": {"cases", "regressions", "improvements", "unchanged", "errors"}, "regressions": [ids], "improvements": [ids]}`. Change compares with-skill `total` (candidate lower = regression, higher = improvement; an error on either side = error, ignored by the verdict). Verdict: candidate-unsafe if validation fails; else candidate-worse if any trigger or behaviour regression; else candidate-better if any trigger or behaviour improvement; else no-difference. challenge.md gains a `## Behaviour` section and its note no longer says "no model was consulted" when the judge ran. CLI adds `--runner` and `--judge`; exit codes unchanged (0 for every verdict, 2 for bad input).
- Release: `package_skill(skill_dir, bump="patch", *, set_version=None, allow_regression=False, runner_name=None, judge_name=None, runner_factory=None, judge_report=None)`. When `judge_report` is None and `runner_name` is given, it calls `judge.judge_skill(...)` after evaluate. When a judge report exists for this call, the snapshot copies `.factory/judge.json` and `judge.md` into `reports/`. `regression_check(skill_dir, candidate, judge_report=None)`: in addition to the lexical comparison, when `judge_report` is completed and the baseline version has `reports/judge.json` with status completed, every case id present and error-free on both sides whose with-skill `total` is lower now is appended to `newly_failing` as `{"id", "kind": "behaviour", "prompt", "before": <old total>, "after": <new total>, "score": <new total>}`, which makes status `regressed`. Regression report always gains `"behaviour": {"status": "compared" | "skipped: no runner" | "skipped: no baseline judge", "checked": int, "baseline_judge": str | null}`. A malformed baseline judge.json raises `IntegrityError`. CLI `skillz package` adds `--runner` and `--judge`; exit codes unchanged (1 for a behavioural regression too).
- Pipeline: `STAGES` becomes capture, generate, validate, render, evaluate, judge, regression, package, approval. `run_pipeline(source, root, name=None, runner_name=None, force=False, runner_factory=None, judge_name=None)`. Without a runner name the judge entry is `{"stage": "judge", "status": "skipped", "verdict": "skipped: no runner", "report": null}`; with one it calls `judge.judge_skill` (status `ok` when completed, `skipped` otherwise, verdict `judge.summary_line(report)`, report `.factory/judge.json`) and passes the report to `release.package_skill(..., judge_report=report)` so the model is not called twice. CLI `skillz run` adds `--judge`.
- Every new or edited test module keeps an autouse fixture that patches `subprocess.run`, `subprocess.Popen` and `runner.subprocess.run` to raise, and model calls only go through fake runners.

## Tasks

1. **behaviour-cases**
   - slug: `behaviour-cases`
   - goal: Case generation yields one deterministic behaviour case per spec behaviour and per trigger, each with a realistic task prompt, a rubric list and expected markers, per the Cases contract.
   - owns: `src/skill_factory/evalcases.py`, `tests/test_behaviour_cases.py`, `tests/test_eval.py`, `tests/test_evaluate.py`
   - needs_web: no
   - depends on: none
   - tests must prove: one behaviour case per behaviour and per trigger with the right ids and sources; every one has a non-empty prompt without the behaviour sentence, a non-empty rubric containing the behaviour sentence and a `mentions:` criterion, a `must not:` criterion when non-triggers exist, and non-empty lowercase expected markers; generation and `cases_json` are byte-identical across runs; positive/negative dicts keep exactly five keys; adding a behaviour changes no other case's prompt. Existing counts in test_eval.py/test_evaluate.py are updated to the new totals.
   - done when: `uv run pytest -q` exits 0

2. **docs-quality-tier**
   - slug: `docs-quality-tier`
   - goal: README and CHANGELOG document the model-backed quality tier and `skillz judge`, with a docs test that fails without the section.
   - owns: `README.md`, `CHANGELOG.md`, `tests/test_docs.py`
   - needs_web: no
   - depends on: none (describes the contracts above)
   - content: README section `## Model-backed quality tier` with the six-tier ladder in order (static validation, offline trigger heuristics, Claude/Codex behavioural execution, independent model judge, cross-model evaluation, human approval); what `skillz judge` does (with/without-skill transcripts per behaviour case, rubric, 0-5 instruction_following and task_quality, winner and reason, cross-model by default when both CLIs are on PATH, same model otherwise, `skipped: no runner` without a runner, `.factory/judge.json` and `judge.md`); that the judge's JSON reply is parsed strictly and a malformed reply is recorded as an error; that the lexical tier remains the default and nothing calls a model without `--runner`; that challenge/package/run with `--runner` fold behavioural scores into verdicts and regressions; a pointer to `docs/evidence/judge-claude.md`. Command table: a `skillz judge <name> [--runner {claude,codex}] [--judge {claude,codex}]` row (exit 0, 2) and `[--runner] [--judge]` on the run, package and challenge rows. CHANGELOG Unreleased entry for mission 07. New test in test_docs.py asserting the section heading, the six tiers in order, `skillz judge`, "strict" and the default-lexical statement.
   - done when: `uv run pytest -q tests/test_docs.py` exits 0

3. **judge-core**
   - slug: `judge-core`
   - goal: `skillz judge` scores with-skill and without-skill transcripts of every behaviour case through a runner and a strictly parsed judge, cross-model by default, per the Judge contract.
   - owns: `src/skill_factory/judge.py`, `src/skill_factory/cli.py`, `tests/test_judge.py`
   - needs_web: no
   - depends on: none in code (works on cases with or without `rubric`); merge after task 2
   - tests must prove (fake runner and fake judge only, `judge.RUNNER_FACTORY` patched, `shutil.which` patched): `skillz judge` without `--runner`, and with `--runner` but nothing on PATH, writes judge.json with status `skipped: no runner` and exits 0; with fakes it writes judge.json and judge.md with per-case scores for both sides, winner mapped to with-skill/without-skill, reason and score_delta, and summary counts; with-skill label placement follows `with_skill_label` and both labels occur; `parse_verdict` rejects no JSON, invalid JSON, missing keys, out-of-range, non-int, bool, bad winner and empty reason with `JudgeError` and a field-naming message, and accepts fenced JSON; a malformed judge reply inside `judge_cases` and the CLI becomes a per-case `error` and exit 0 (no traceback); `choose_judge` returns the other model when both executables are found, the same model when only one is, and the explicit `--judge` otherwise; judge.json `judge` and `cross_model` and judge.md name which; missing skill exits 2; `skillz judge --help` exits 0; `ClaudeRunner`/`CodexRunner` are never executed and `judge.py` source never mentions `subprocess`.
   - done when: `uv run pytest -q tests/test_judge.py` exits 0

4. **quality-integration**
   - slug: `quality-integration`
   - goal: With `--runner`, `skillz challenge`, `skillz package` and `skillz run` fold judge scores into verdicts, version reports and regressions, per the Integration contract.
   - owns: `src/skill_factory/challenge.py`, `src/skill_factory/release.py`, `src/skill_factory/pipeline.py`, `tests/test_quality_tier.py`, `tests/test_challenge.py`, `tests/test_pipeline.py`
   - needs_web: no
   - depends on: tasks 1 and 3 (merged before dispatch)
   - tests must prove (fakes via `judge.RUNNER_FACTORY` or `runner_factory`; subprocess patched to raise): challenge with a runner marks a behaviour case the fake judge scores lower on the candidate as a regression (verdict candidate-worse) and higher as an improvement (verdict candidate-better), with the same judge on both sides, and challenge.json/md carry the behaviour section; challenge without a runner records `behaviour.status == "skipped: no runner"` and the old verdicts are unchanged; package with a runner stores `reports/judge.json` in the version (listed in the manifest, checksums verify); after approving that version, a package whose fake judge scores a case lower lists it in `newly_failing` with kind behaviour and exits 1 (passes with `--allow-regression`), and an unchanged score passes; regression without a baseline judge records `skipped: no baseline judge`; `skillz run --runner claude` with fakes records a completed judge stage and calls the model once per stage, and without a runner records the judge stage as `skipped: no runner`. test_challenge.py KEYS and test_pipeline.py STAGES are updated for the new key and stage.
   - done when: `uv run pytest -q` exits 0

L. **Lead step after tasks 1-4 merge (not a worker task): real Claude evidence**
   - owns: `docs/evidence/judge-claude.json`, `docs/evidence/judge-claude.md`
   - run with the real Claude CLI, outside tracked skill paths: `uv run skillz new tests/fixtures/eval/well-described.json --out .claude/worktrees/judge-evidence`, then `uv run skillz judge csv-data-cleaner --runner claude --out .claude/worktrees/judge-evidence` (about 15 model calls: 5 behaviour cases x 3), then copy `.claude/worktrees/judge-evidence/skills/csv-data-cleaner/.factory/judge.json` and `judge.md` to `docs/evidence/judge-claude.json` and `.md` unedited. If `claude` is not on PATH or the run is skipped/errors, record it as a block and do not submit READY.
   - done when: `grep -q '"runner": "claude"' docs/evidence/judge-claude.json` exits 0 and `grep -q '"status": "completed"' docs/evidence/judge-claude.json` exits 0

## Criteria map

- criterion 0 (`uv run pytest -q`): tasks 1 and 4 done-when; all tasks.
- criterion 1 (`tests/test_judge.py tests/test_behaviour_cases.py`): tasks 3 and 1.
- criterion 2 (`skillz judge --help`): task 3.
- criterion 3 (`skillz validate skill-factory`): untouched; no task edits skills/skill-factory.
- criterion 4 (README contains `skillz judge`): task 2.
- criterion 5 (judge rubric), clause by clause:
  - behaviour case with task prompt, rubric, expected markers for every behaviour and trigger, deterministically: task 1, tests/test_behaviour_cases.py.
  - `skillz judge` without a runner records `skipped: no runner` and exits 0: task 3, tests/test_judge.py.
  - fake runner and fake judge produce judge.json and judge.md with per-case scores for both sides, winner and reason: task 3, tests/test_judge.py.
  - malformed judge reply rejected with a clear error, not a crash: task 3, tests/test_judge.py (parse_verdict cases plus CLI exit 0 with per-case error).
  - judge defaults to the other model when both executables are on PATH, same model otherwise, report names which: task 3, tests/test_judge.py (patched `shutil.which`, `judge`/`cross_model` fields).
  - challenge with a runner: lower on candidate = regression, higher = improvement: task 4, tests/test_quality_tier.py.
  - package with a runner stores judge results in the version reports and the next regression compares them: task 4, tests/test_quality_tier.py.
  - no test spawns the real claude or codex: tasks 1, 3, 4 autouse subprocess fixtures plus the `subprocess`-free source assertion in tests/test_judge.py.
  - docs/evidence holds a real Claude CLI judge report whose runner field says claude: lead step L.
