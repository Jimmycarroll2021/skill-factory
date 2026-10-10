Assumptions: (1) `skillz eval` follows the validate/render pattern exactly: positional `name`, `--out PATH` (type Path, default None meaning cwd), and the skill directory is `<out>/skills/<name>/`. All outputs go to `<skill>/.factory/`: `cases.json`, `eval.json` and `eval.md`. (2) The spec comes from `<skill>/.factory/spec.json` (`SkillSpec.from_dict`). If that file is absent, the spec is built from the SKILL.md frontmatter `name` and `description` with empty triggers, non_triggers and behaviours. A missing skill dir or SKILL.md, an unreadable or invalid spec.json, or a frontmatter without a description exits 2 with a `skillz: error:` message. (3) Offline trigger matching is lexical overlap and nothing more. Prompt content words are lowercase alphanumeric tokens with stop-words removed (a light, deterministic suffix normalisation such as plural `s` is allowed). Skill words are the content words of the name (split on `-`) plus the description. Score = |prompt words ∩ skill words| / |prompt words|, or 0.0 when the prompt has no content words. A prompt matches when score >= `MATCH_THRESHOLD`, a module constant. No randomness, clocks, hashing of unordered sets or environment input. Reports call this heuristic. (4) Eval is advisory. It exits 0 even when trigger cases fail. `--strict` (optional flag, implemented here) exits 1 when any positive or negative case fails. (5) The baseline is opt-in. It runs only when `--runner claude|codex` is given AND `shutil.which(<executable>)` returns a path, looked up at call time so tests can monkeypatch `skill_factory.runner.shutil.which`. Otherwise eval.json has `"baseline": {"status": "skipped: no runner"}` (an extra `"requested"` key naming the requested runner is allowed) and the exit code is unchanged. (6) No test ever runs the real `claude` or `codex`. Runner tests fake `subprocess.run` or inject a fake object that satisfies the `Runner` protocol. End-to-end tests monkeypatch `subprocess.run` to raise, which proves nothing is spawned on the skipped paths. (7) There are no README changes. Mission 05 owns README. This mission touches 9 files, plus 2 small fixture files under the new `tests/fixtures/eval/`, which keeps it near the 10-file target. The fixtures are test data for the same cohesive outcome.

# Mission 03 plan: evaluation (REQ-004)

Tasks 1 and 2 are independent and run in parallel. Task 3 depends on both and is dispatched after both merge.

## Agreed interfaces (contract between tasks; implement exactly these names)

### `src/skill_factory/evalcases.py` (task 1)
- Constants: `MATCH_THRESHOLD: float`, `MIN_DESCRIPTION_WORDS: int` (a description with fewer content words is "vague"), and `GENERIC_NEGATIVES: tuple[str, ...]`, a fixed, ordered set of at least 4 unrelated prompts, e.g. weather, a haiku, translating a sentence, photosynthesis.
- `content_words(text: str) -> list[str]`: deterministic tokeniser, stop-words removed, order of first appearance, no duplicates. It may reuse `capture._words` and `capture._STOP_WORDS`.
- `match_score(prompt: str, name: str, description: str) -> float`: overlap ratio as in assumption (3), rounded to 4 decimals.
- `@dataclass Case(id: str, kind: str, prompt: str, source: str, expected: list[str] = [])`:
  - `kind` is `"positive"`, `"negative"` or `"behaviour"`.
  - `source` is `"trigger:<i>"`, `"description"`, `"non_trigger:<i>"`, `"generic:<i>"` or `"behaviour:<i>"`.
  - `expected` is a non-empty list of lowercase marker strings for behaviour cases and `[]` otherwise.
  - Has `to_dict()` with exactly the keys `id`, `kind`, `prompt`, `source`, `expected`.
- Ids are stable and positional: `pos-001`, `pos-002`, … for positives (triggers first in spec order, then the description case), `neg-001`, … (non_triggers in order, then GENERIC_NEGATIVES), and `beh-001`, … (behaviours in order).
- `generate_cases(spec: SkillSpec) -> list[Case]`: positives, then negatives, then behaviours. Any template filler words added to prompts must be stop-words so they never change a score.
- `cases_json(cases: list[Case]) -> str`: `json.dumps({"cases": [c.to_dict() ...]}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"`. The same spec gives byte-identical output.
- `@dataclass Grade(id: str, kind: str, score: float | None, matched: bool | None, status: str)`:
  - `status` is `"pass"`, `"fail"` or `"pending-runner"`.
  - Positives pass when matched. Negatives pass when not matched. Behaviour cases are `pending-runner`, with `score=None` and `matched=None`.
  - Has `to_dict()` with exactly those keys.
- `grade_offline(cases: list[Case], name: str, description: str) -> list[Grade]`: one grade per case, in case order.
- `@dataclass Proposal(area: str, problem: str, proposal: str, cases: list[str])`, with `to_dict()` having exactly those keys. `area` is one of the following:
  - `"trigger-vocabulary"`: failing positives. `problem` names them. `proposal` lists the missing words users say, e.g. "Add these words users will say to the description: csv, duplicate".
  - `"negative-overlap"`: matching negatives. `proposal` names the overlapping words and proposes narrowing the description or adding the prompt to non_triggers.
  - `"vague-description"`: the description has fewer than MIN_DESCRIPTION_WORDS content words. `proposal` is a concrete rewrite direction naming trigger words to include.
  - `"no-behaviours"`: the spec has no behaviours. `proposal` asks for expected behaviours.
- `propose_improvements(spec: SkillSpec, cases: list[Case], grades: list[Grade]) -> list[Proposal]`: deterministic order and an empty list when nothing is weak. Each proposal's `cases` lists the ids of the cases it covers.

### `src/skill_factory/runner.py` (task 2)
- `SKIPPED_NO_RUNNER = "skipped: no runner"`, `EXECUTABLES = {"claude": "claude", "codex": "codex"}`, `DEFAULT_TIMEOUT: float` (seconds).
- `class RunnerError(RuntimeError)`.
- `class Runner(typing.Protocol)`: attribute `name: str`, method `run(self, prompt: str) -> str` (returns the model's text output or raises RunnerError).
- `ClaudeRunner(executable: str, timeout: float = DEFAULT_TIMEOUT)` and `CodexRunner(executable: str, timeout: float = DEFAULT_TIMEOUT)`:
  - `argv(prompt) -> list[str]`: Claude is `[executable, "-p", prompt, ...]`. Codex is `[executable, "exec", ..., prompt]`.
  - `run(prompt)` calls `subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout, check=False, stdin=subprocess.DEVNULL)`. It never uses `shell=True`.
  - A non-zero exit, `TimeoutExpired` or `OSError` becomes RunnerError.
- `detect_runner(name: str | None, timeout: float = DEFAULT_TIMEOUT) -> Runner | None`:
  - Returns None when `name` is None or `shutil.which(EXECUTABLES[name])` returns None. `shutil.which` is looked up at call time; do not bind it as a default argument.
  - Otherwise returns the runner built with the resolved path as `executable`.
  - Raises ValueError for an unknown name.
- `skipped_baseline(requested: str | None = None) -> dict`: returns `{"status": SKIPPED_NO_RUNNER}`, plus `"requested": requested` when it is given.
- `with_skill_prompt(skill_text: str, prompt: str) -> str`: deterministic composition that wraps the SKILL.md text before the prompt.
- `grade_markers(output: str, expected: list[str]) -> dict`: returns `{"passed": bool, "found": [...], "missing": [...]}`. Matching is case-insensitive substring. It passes when nothing is missing.
- `run_baseline(runner: Runner, cases: list[dict], skill_text: str) -> dict`:
  - `cases` are `Case.to_dict()`-shaped dicts (keys `id`, `kind`, `prompt`, `expected`). Only `kind == "behaviour"` cases are run, each twice: `runner.run(with_skill_prompt(skill_text, prompt))` and `runner.run(prompt)`.
  - Returns `{"status": "completed", "runner": runner.name, "cases": [{"id", "with_skill": grade_markers-dict, "without_skill": grade_markers-dict, "verdict"}], "summary": {"skill_helps": n, "no_difference": n, "skill_hurts": n, "errors": n}}`.
  - `verdict` is `"skill-helps"`, `"no-difference"`, `"skill-hurts"` or `"error"`. A RunnerError is recorded on that case (`"error": message`, verdict `"error"`) rather than raised.
  - runner.py must not import evalcases.py, so tasks 1 and 2 stay independent.

### `src/skill_factory/evaluate.py` (task 3)
- `load_spec(skill_dir: Path) -> SkillSpec`: implements assumption (2) and raises `SpecError` on invalid input.
- `evaluate_skill(skill_dir: Path, runner_name: str | None = None, runner_factory: Callable[[str], Runner | None] | None = None) -> dict`:
  - Default factory is `runner.detect_runner`, called only when `runner_name` is not None.
  - Writes `cases.json` (via `evalcases.cases_json`), `eval.json` and `eval.md` into `<skill>/.factory/`, refusing to write if `.factory` is a symlink.
  - Returns the eval.json dict.
- eval.json keys (`sort_keys=True`, indent 2, trailing newline):
  - `skill` and `method`, where `method` contains "heuristic" and "lexical overlap".
  - `threshold`.
  - `summary`: `{"positive": {"passed", "failed"}, "negative": {"passed", "failed"}, "behaviour": {"pending"}}`.
  - `results`: list of `Grade.to_dict()`.
  - `weak_areas`: list of `Proposal.to_dict()`.
  - `baseline`: either `skipped_baseline(...)` or `run_baseline(...)`.
- eval.md contains "heuristic", the pass/fail counts, each weak area with its proposal, and the literal baseline status (e.g. `skipped: no runner`).
- `add_parser(sub)` registers `eval` with positional `name`, `--runner {claude,codex}`, `--out` and `--strict`. `run(args) -> int` returns 0 (advisory), 1 only with `--strict` and failing trigger cases, or 2 for a missing skill, missing or invalid spec, invalid name or symlinked `.factory`.

## Tasks

1. **slug:** `eval-cases`
   - **goal:** Add deterministic case generation, the offline lexical-overlap matcher and grading, and weak-area improvement proposals in `evalcases.py`, proven by tests on a well-described fixture spec and a deliberately vague one.
   - **owns:** `src/skill_factory/evalcases.py`, `tests/test_eval.py`, `tests/fixtures/eval/well-described.json`, `tests/fixtures/eval/vague.json`
   - **needs_web:** no
   - **notes:** Implement the evalcases interface above exactly. The fixtures are spec JSON files readable by `capture.capture_spec_file`:
     - `well-described.json` has rich description vocabulary, at least 2 triggers, at least 1 non_trigger and at least 2 behaviours.
     - `vague.json` has a description of only a few generic content words (e.g. "Helps with things.") but concrete triggers whose words are absent from it.
     
     tests/test_eval.py must show:
     - (a) `generate_cases` twice on the same spec gives equal case lists and byte-identical `cases_json`, with stable ids (`pos-001`, `neg-001`, `beh-001`).
     - (b) Every case kind is generated from the right source: triggers and description become positives, non_triggers and GENERIC_NEGATIVES become negatives, behaviours become behaviour cases with non-empty `expected`.
     - (c) For the well-described fixture, every positive passes and every negative (including generic ones) passes, i.e. does not match.
     - (d) For the vague fixture, at least one trigger positive fails, and `propose_improvements` returns a `trigger-vocabulary` proposal whose `cases` include those failing ids and whose `proposal` text names at least one missing trigger word, plus a `vague-description` proposal.
     - (e) A negative prompt that overlaps the description yields a `negative-overlap` proposal naming it.
     - (f) Behaviour grades are `pending-runner`.
     - (g) `match_score` returns 0.0 for an all-stop-word prompt and a value of at least `MATCH_THRESHOLD` for a prompt made of description words.
     
     `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_eval.py` exits 0

2. **slug:** `baseline-runner`
   - **goal:** Add the opt-in baseline runner in `runner.py`: the Runner protocol, ClaudeRunner and CodexRunner over subprocess with a timeout, call-time executable detection, marker grading and a with-skill versus no-skill baseline that records "skipped: no runner" when unavailable, proven with fake runners only.
   - **owns:** `src/skill_factory/runner.py`, `tests/test_runner.py`
   - **needs_web:** no
   - **notes:** Implement the runner interface above exactly. tests/test_runner.py must never run a real `claude` or `codex`. An autouse fixture monkeypatches `skill_factory.runner.subprocess.run` to fail the test unless a test overrides it. Tests must show:
     - (a) `detect_runner(None)` is None without calling `shutil.which`.
     - (b) `detect_runner("claude")` and `detect_runner("codex")` are None when `skill_factory.runner.shutil.which` is patched to return None.
     - (c) With which patched to `/fake/bin/claude`, `detect_runner("claude")` returns a ClaudeRunner whose `argv("hi")` starts with `["/fake/bin/claude", "-p"]`. Likewise CodexRunner's argv contains `"exec"`.
     - (d) `ClaudeRunner.run` passes a list argv, no shell and a timeout to a faked `subprocess.run` and returns its stdout. A non-zero exit and `TimeoutExpired` raise RunnerError.
     - (e) `skipped_baseline()["status"] == "skipped: no runner"`.
     - (f) `run_baseline` with a fake Runner, whose output contains the markers only when the skill text is in the prompt, runs each behaviour case exactly twice (once with the skill text, once without), skips non-behaviour cases and reports verdict `skill-helps`. A fake that raises RunnerError gives verdict `error` without raising.
     - (g) `grade_markers` is case-insensitive.
     - (h) `detect_runner("other")` raises ValueError.
     
     `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_runner.py` exits 0

3. **slug:** `eval-cli`
   - **depends on:** tasks 1 and 2 merged (it imports `evalcases` and `runner`).
   - **goal:** Add the `skillz eval <name> [--runner claude|codex] [--out PATH] [--strict]` subcommand in `evaluate.py`, which loads the spec, generates and grades cases, runs the optional baseline and writes cases.json, eval.json and eval.md under `.factory/`, wired into the CLI and proven end to end.
   - **owns:** `src/skill_factory/evaluate.py`, `src/skill_factory/cli.py`, `tests/test_evaluate.py`
   - **needs_web:** no
   - **notes:** cli.py only appends `evaluate.add_parser` to SUBCOMMANDS and `"eval": evaluate.run` to HANDLERS. No logic moves into cli.py. Tests build skills in tmp_path with `generate(capture_spec_file(FIXTURES/"eval"/...), tmp_path)`. They never write into tests/fixtures/. An autouse fixture monkeypatches `subprocess.run` (both `subprocess.run` and `skill_factory.runner.subprocess.run`) to raise. tests/test_evaluate.py must show:
     - (a) `main(["eval", "--help"])` exits 0 and mentions `--runner` and `--out`.
     - (b) `main(["eval", name, "--out", tmp])` returns 0 for the well-described skill. It writes `.factory/cases.json`, `.factory/eval.json` and `.factory/eval.md`. eval.json `baseline.status == "skipped: no runner"`, and eval.md contains "skipped: no runner" and "heuristic".
     - (c) Running eval twice gives byte-identical cases.json.
     - (d) For the vague skill, the command still returns 0. eval.json has failing positive results and a `trigger-vocabulary` weak area naming those case ids, and eval.md shows the proposal. With `--strict` it returns 1.
     - (e) `--runner claude` with `skill_factory.runner.shutil.which` patched to return None returns 0 and records "skipped: no runner".
     - (f) `--runner claude` with which patched to a fake path and a fake Runner injected (via `runner_factory` on `evaluate_skill`, or by monkeypatching `skill_factory.runner.detect_runner`) records `baseline.status == "completed"` with per-case with/without results, and the fake received prompts both with and without the SKILL.md text.
     - (g) Without `--runner`, the runner factory is never called.
     - (h) When `.factory/spec.json` is deleted, eval falls back to the SKILL.md frontmatter and returns 0.
     - (i) A missing skill returns 2 with `skillz: error:` on stderr.
     
     `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q` exits 0 (criterion 0) and `uv run skillz eval --help` exits 0 (criterion 2)

## Criterion coverage
- criterion 0 (`uv run pytest -q`): all tasks; final check after task 3.
- criterion 1 (`uv run pytest -q tests/test_eval.py tests/test_runner.py`): tasks 1 and 2.
- criterion 2 (`uv run skillz eval --help`): task 3.
- criterion 3 (judge rubric):
  - Task 1 covers deterministic generation, the well-described fixture (positives match, negatives do not), and the vague fixture's failing trigger cases with a proposal naming them.
  - Task 2 covers baseline only with a runner found on PATH, fake runners only, and the "skipped: no runner" literal.
  - Task 3 covers the JSON report and Markdown summary under `.factory/`, `--runner` explicit opt-in, "skipped: no runner" with exit 0 end to end, and which-None giving skipped.
- Lint (`uv run ruff check src tests`) and `uv build` must pass at every merge.
