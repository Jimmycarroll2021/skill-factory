Assumptions: (1) `skillz run` lives in a new `src/skill_factory/pipeline.py` and calls the existing Python functions (`capture.capture`, `generate.generate`, `validate.validate_skill`, `render.render_skill`, `evaluate.evaluate_skill`, `release.package_skill`), not the CLI. It never calls `release.approve` or anything in install.py. (2) The pipeline runs validate, render and evaluate itself so it can report and stop per stage, then calls `release.package_skill`, which regenerates its own offline validate/render/eval reports and runs the regression check (package_skill stays unchanged). The offline grading is deterministic, so the two eval runs agree. When `--runner` is given and found, the pipeline's evaluate stage records that baseline in pipeline.json, but the packaged `reports/eval.json` is package_skill's offline re-run. With no runner on PATH the evaluate verdict includes `skipped: no runner`. (3) The CI action SHAs are not available offline: nothing on this machine contains a 40-hex pin for actions/checkout or astral-sh/setup-uv, and workers have no web access. So ci.yml keeps `@v4`/`@v5`, with a comment saying they should be pinned by SHA. The lead records this as a carried-over low note. No SHA is guessed. (4) The runner separator puts `--` immediately before the prompt and keeps existing flags first: claude `[exe, "-p", "--output-format", "text", "--", prompt]`, codex `[exe, "exec", "--", prompt]`. The real CLIs are not run in tests. (5) Semver hardening: release.py and install.py both use `re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", re.ASCII)`. Non-ASCII digits and any part longer than 9 digits are rejected with the module's existing error type. (6) The meta-skill ships as `skills/skill-factory/SKILL.md` only, with no `.factory/spec.json`. `skills/*/.factory/` is added to .gitignore so running `skillz validate skill-factory` from the repo root leaves the tree clean. (7) The mission touches about 17 files and likely more than 500 lines. Most of that is prose (README, CONTRIBUTING, CHANGELOG, SKILL.md). The security notes were carried by contract from missions 01 to 04 into this mission. The lead records this in REPORT.md. Splitting the docs from the pipeline they describe would ship a README describing a command that does not exist.

# Mission 05 plan: `skillz run` pipeline, meta-skill and public docs (REQ-007, REQ-008)

All three tasks are independent and file-disjoint, so they run in parallel. Task 2 documents the CLI contract below and must not edit cli.py. Task 3 changes internals only, and no public signature task 1 calls changes.

## Agreed `skillz run` contract (task 1 implements, task 2 documents)

`skillz run <source> [--name NAME] [--out DIR] [--runner {claude,codex}] [--force]`
- `source`: paragraph text or a path to a .txt/.yaml/.yml/.json spec (same as `skillz new`). `--out`: the root containing `skills/` (default cwd). `--runner`: opt-in baseline, same choices as `eval`. `--force`: regenerate an existing skill (same as `new --force`).
- Stages in order: `capture`, `generate`, `validate`, `render`, `evaluate`, `regression`, `package`, then `approval` (always `awaiting-approval` on success). It never approves and never installs: no `.claude/`, no `.agents/`, no `skills/registry.json`.
- The pipeline writes `skills/<name>/.factory/pipeline.json`, using the release.py JSON style (indent 2, sort_keys, ensure_ascii False, LF). Keys:
  - `schema` (1), `name`, `status` (`"awaiting-approval"` or `"stopped"`), `stopped_at` (a stage name or null), `version` (the semver created, or null).
  - `stages`: a list in order of `{"stage", "status", "verdict", "report"}`. `status` is one of `ok`, `failed` or `not-run`. `report` is a POSIX path relative to the skill dir: `.factory/spec.json`, `SKILL.md`, `.factory/validate.json`, `.factory/render.json`, `.factory/eval.json`, `.factory/regression.json`, `.factory/versions/<v>/manifest.json`, or null for approval.
  - `next_steps`: a list of command strings.
- It also writes `skills/<name>/.factory/pipeline.md`, a readable summary. It has one line or table row per stage naming its verdict (for example `validate: pass`, `evaluate: N passed, M failed (heuristic); baseline: skipped: no runner`, `regression: no-baseline`, `package: created 0.1.0`, `approval: awaiting approval`). It ends with the next steps, `skillz approve <name> <version>` then `skillz install <name> --platform claude|codex --scope project|user`, and states that offline grading is heuristic.
- Exit codes:
  - 0: reached awaiting-approval.
  - 1: stopped by validation errors, by `release.RegressionFailed`, or by `release.IntegrityError`. pipeline.json and pipeline.md are still written, with `status: stopped` and `stopped_at` set.
  - 2: input errors (`SpecError`, `GenerateError` such as an existing skill without `--force`, `VersionExists`, `RenderError`). These print `skillz: error: ...` on stderr.
- stdout on success prints the pipeline.md path and the approve command.

## Tasks

1. **slug:** `pipeline-run`
   - **goal:** Add `skillz run`, which takes a paragraph or spec through capture, generate, validate, render, evaluate, regression and package, stops at awaiting approval, and writes `.factory/pipeline.json` and `.factory/pipeline.md`, proven end to end by tests/test_pipeline.py.
   - **owns:** `src/skill_factory/pipeline.py`, `src/skill_factory/cli.py`, `tests/test_pipeline.py`
   - **needs_web:** no
   - **notes:**
     - Follow the module pattern: `add_parser(sub)` / `run(args) -> int`, plus a Python API such as `run_pipeline(source, root, name=None, runner_name=None, force=False, runner_factory=None) -> dict`, which returns the pipeline.json dict. Wire it into cli.py with one SUBCOMMANDS entry and one HANDLERS entry (`"run"`). No pipeline logic goes into cli.py.
     - Tests use autouse fixtures with these effects:
       - HOME/USERPROFILE point to `tmp_path/"home"`.
       - PATH points to an empty tmp dir, so no model runner is found.
       - `subprocess.run`, `subprocess.Popen` and `os.system` are monkeypatched to fail the test.
     - tests/test_pipeline.py must show these cases:
       - (a) `cli.main(["run", <tests/fixtures/paragraph.txt contents>, "--out", tmp])` returns 0.
       - (b) For that run:
         - Every stage report exists under `skills/<name>/.factory/`: spec.json, validate.json, render.json, eval.json, regression.json and `versions/0.1.0/manifest.json`.
         - eval.json's baseline status is `skipped: no runner`. No runner is on PATH. Both the default run and a run with `--runner claude` must record `skipped: no runner`, in eval.json and in the evaluate verdict in pipeline.json/pipeline.md.
         - `release.load_versions` shows 0.1.0 with status `awaiting-approval` and no approval.json.
         - `.claude/`, `.agents/`, `skills/registry.json` and `home/` contain nothing installed.
         - pipeline.json has every stage in order with its status and verdict, `version` 0.1.0 and `status` awaiting-approval.
         - pipeline.md names every stage with its verdict, includes `skipped: no runner`, and includes `skillz approve <name> 0.1.0` and `skillz install`.
       - (c) A spec-file source (`tests/fixtures/spec.json`) also returns 0.
       - (d) A validation failure returns 1. pipeline.json has `stopped_at: validate` and no `versions/` entry. Monkeypatching `validate.validate_skill` to return an error report is acceptable.
       - (e) A regression returns 1 with `stopped_at: regression`. One way: run, approve 0.1.0, then rerun with `--force` and a changed source; monkeypatching `release.regression_check` is acceptable.
       - (f) Input errors return 2: an empty source, an invalid `--name`, and rerunning on an existing skill without `--force`.
       - (g) `main(["run", "--help"])` exits 0 and the help mentions `--runner`, `--out`, `--name` and `--force`.
     - `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_pipeline.py` exits 0 (criterion 1; criterion 2 `uv run skillz run --help` exits 0 follows from the cli wiring)

2. **slug:** `meta-skill-docs`
   - **goal:** Ship the factory's own meta-skill `skills/skill-factory/SKILL.md` and public-ready README.md, CONTRIBUTING.md and CHANGELOG.md (with `readme = "README.md"` in pyproject.toml), proven by tests/test_docs.py.
   - **owns:** `skills/skill-factory/SKILL.md`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `pyproject.toml`, `.gitignore`, `tests/test_docs.py`
   - **needs_web:** no
   - **notes:**
     - Do NOT edit cli.py. Document `skillz run` from the contract above. Document every other command from its real `--help` output (`uv run skillz <cmd> --help`).
     - **SKILL.md:**
       - Frontmatter has `name: skill-factory` and a quoted description under 1024 chars that triggers on "create a skill", "make a skill for X", "test/evaluate a skill" and "install a skill for Claude or Codex". The frontmatter is parsed by the in-package yamlish subset, so quote any value containing a colon.
       - The body is short (well under 100 lines). It tells Claude Code or Codex how to drive `skillz`: run, read `.factory/pipeline.md`, approve, install, rollback, list.
       - It calls out explicitly that approval is a human decision the agent must not take on its own.
       - No scripts/ dir is needed.
     - **README.md:**
       - What it is.
       - A pipeline diagram listing the stages in order: capture, generate, validate, render, evaluate, regression, package, human approval gate, install.
       - A quickstart a new user can follow: install with uv, `skillz run`, read pipeline.md, `skillz approve`, `skillz install` into Claude or Codex, `skillz rollback`.
       - A command table covering every subcommand.
       - The offline/deterministic design and the heuristic nature of offline grading. `--runner` is opt-in.
       - How to add the meta-skill to Claude Code (`.claude/skills/skill-factory/`) and Codex (`.agents/skills/skill-factory/`).
       - Development with uv, ruff, pytest and `bash scripts/gate.sh`.
       - A note on the committed quickship harness in scripts/ and docs/runs/.
       - Non-goals: no push or publish.
     - **CONTRIBUTING.md:** dev setup, tests, ruff, PR expectations, and the definition of done (every behaviour change ships with a test).
     - **CHANGELOG.md:** Keep a Changelog style, `## [0.1.0]` (the text must contain `0.1.0`), summarising the outcomes of missions 01 to 05 from docs/runs/*/REPORT.md plus this mission.
     - **.gitignore:** add `skills/*/.factory/`.
     - **tests/test_docs.py** must assert:
       - (a) README contains `skillz run` and the name of every subcommand from `cli.build_parser()`, plus `run`.
       - (b) The README's pipeline stage names appear in order, including an approval gate before install.
       - (c) `validate.validate_skill(REPO/"skills"/"skill-factory")` has 0 errors and 0 warnings, and its frontmatter name is `skill-factory`.
       - (d) After copying `skills/skill-factory` to `tmp_path/skills/`, `cli.main(["validate", "skill-factory", "--out", tmp])` returns 0. This mirrors criterion 3 without writing into the repo.
       - (e) CHANGELOG contains `0.1.0`.
       - (f) CONTRIBUTING.md exists and mentions a test requirement.
       - (g) `tomllib` reads `project.readme == "README.md"`.
     - `uv run ruff check src tests` and `uv build` must pass.
   - **done when:** `uv run pytest -q tests/test_docs.py` exits 0 (also moves criteria 3, 4, 5, 6 and the docs/meta-skill clauses of criterion 7)

3. **slug:** `security-polish`
   - **goal:** Fold the carried low security notes into release.py, runner.py and install.py, each behaviour change proven by a failing-without-it test, and add SHA-pin guidance to ci.yml without adding any deploy or publish step.
   - **owns:** `src/skill_factory/release.py`, `src/skill_factory/runner.py`, `src/skill_factory/install.py`, `tests/test_release.py`, `tests/test_runner.py`, `tests/test_install.py`, `.github/workflows/ci.yml`
   - **needs_web:** no
   - **notes:**
     - Keep every public signature unchanged, because task 1 calls `package_skill`, `load_versions` and `regression_check`.
     - The changes:
       - (a) Semver handling as in assumption (5), in both release.py `_SEMVER_RE`/`parse_semver` and install.py `_SEMVER_RE`/`_semver`. Tests: Arabic-Indic digits (`"١.٢.٣"`) and a 10-digit part are rejected, `"1.2.3"` and `"999999999.0.0"` are accepted, and a non-ASCII-digit version dir is ignored by `load_versions`.
       - (b) runner argv puts `--` immediately before the prompt as in assumption (4). The test uses a prompt starting with `-` (for example `"--dangerous"`), which must appear only after `"--"`. The existing `[:2]` and `[-1]` assertions still hold.
       - (c) `_baseline_cases` also catches `AttributeError`, for example `cases` items that are lists, or `results` items that are strings. Test: a malformed approved baseline makes `regression_check` raise `IntegrityError` (not AttributeError), and `package` through the CLI returns 1.
       - (d) `install.format_list` escapes control characters (ESC, CR, LF, other C0/C1) in every raw registry or version field before tabulating. Test: a registry `approved_by`/`path` containing `"\x1b[31m"` and `"\n"` yields output with no raw `\x1b` and no extra lines, while ordinary names print unchanged.
       - (e) ci.yml keeps `actions/checkout@v4` and `astral-sh/setup-uv@v5` per assumption (3), with a comment noting they should be pinned to full commit SHAs. It keeps `permissions: contents: read`, and adds no deploy, publish or release job.
     - `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_release.py tests/test_runner.py tests/test_install.py` exits 0

## Criterion coverage
- criterion 0 (`uv run pytest -q`): all tasks; final check after all three merge.
- criterion 1 (`uv run pytest -q tests/test_pipeline.py`): task 1.
- criterion 2 (`uv run skillz run --help`): task 1.
- criterion 3 (`uv run skillz validate skill-factory` from repo root): task 2 (meta-skill, plus test (c)/(d)).
- criterion 4 (`uv build`): task 2 (the readme field must resolve); all tasks keep it green.
- criterion 5 (README contains "skillz run"): task 2.
- criterion 6 (CHANGELOG contains "0.1.0"): task 2.
- criterion 7 (judge): the pipeline test clauses come from task 1 (a)-(b). The README quickstart and ordered diagram with the approval gate, and the short valid SKILL.md, come from task 2.
- Security-04 and earlier carried low notes: task 3.
