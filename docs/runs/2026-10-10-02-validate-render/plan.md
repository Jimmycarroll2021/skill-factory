Assumptions: (1) `validate` and `render` reuse `new`'s root option exactly: `--out PATH` (type Path, default None meaning cwd), and the skill directory is `<out>/skills/<name>/`. (2) The Claude variant is the contents of `.claude/skills/<name>/`, written to `skills/<name>/.factory/dist/claude/` (SKILL.md plus any scripts/, references/, assets/). The Codex variant is the contents of `.agents/skills/<name>/`, written to `skills/<name>/.factory/dist/codex/`, which has the same files plus `agents/openai.yaml`. Neither variant contains `.factory/`. (3) Per the brief goal, render writes both `.factory/render.json` and a short `.factory/render.md` summary. (4) `.factory/` is the factory's own output. It counts as allowed at the top level, but it is skipped by the pattern scan and the size totals, so earlier reports and dist copies cannot trigger findings. (5) Secret-looking test inputs and files over 1 MB / 5 MB are created at test time in `tmp_path`, never committed, per `.claude/rules/no-secrets.md`. A fake token is built by string concatenation, e.g. `"AKIA" + "Z" * 16`. Every other bad case is a committed fixture directory. (6) Tests copy fixtures into `tmp_path` before running validate or render, so nothing writes into `tests/fixtures/`. (7) Fixture directories push this mission over the 10-file target. They are test data for one cohesive validate+render outcome, and splitting them out would ship rules with no tests. The lead records this in REPORT.

# Mission 02 plan: validate + render

Tasks 1 and 2 are independent and run in parallel. Task 3 depends on both and is dispatched after they merge.

## Agreed interfaces (contract between tasks; implement exactly these names)

`src/skill_factory/validate.py`:
- `@dataclass Finding(rule: str, severity: str, path: str, message: str, line: int | None = None)`. `severity` is `"error"` or `"warning"`. `path` is POSIX and relative to the skill dir. Messages redact matched secret values and never echo the full secret.
- Rule ids (stable strings that tests assert on): `frontmatter-missing`, `frontmatter-invalid`, `name-invalid`, `name-too-long`, `name-mismatch`, `description-empty`, `description-too-long`, `path-not-allowed`, `body-too-long` (warning, more than 500 body lines), `file-too-large` (single file over 1 MB = 1_048_576 bytes), `skill-too-large` (total over 5 MB), `secret`, `network-call`, `destructive-shell`.
- `@dataclass Report(skill: str, skill_dir: Path, findings: list[Finding])` with property `ok` (True when there are no error-level findings), `to_dict() -> dict`, and `write(factory_dir: Path) -> tuple[Path, Path]`, which writes `validate.json` and `validate.md` and returns their paths.
- `validate_skill(skill_dir: Path) -> Report`: read-only. It reads files as bytes/text only, never imports, executes or spawns anything (no subprocess, os.system, exec, runpy, importlib on skill files), and does not follow symlinks out of the skill dir.
- `add_parser(sub: argparse._SubParsersAction) -> None`: registers subcommand `validate` with positional `name` and `--out` (same help/default as `new`).
- `run(args: argparse.Namespace) -> int`: resolves `<out or cwd>/skills/<name>`, validates, writes the report into `<skill>/.factory/`, prints a one-line verdict plus the report path, and returns 0 if `ok`, 1 on any error-level finding, or 2 with a `skillz: error:` message on stderr if the skill dir or SKILL.md does not exist.

`src/skill_factory/render.py`:
- `class RenderError(ValueError)`.
- `@dataclass Report(skill: str, skill_dir: Path, variants: dict[str, dict])`, where each variant records its dist path, its install target (`.claude/skills/<name>/` or `.agents/skills/<name>/`) and its files with sha256. It has `to_dict() -> dict` and `write(factory_dir: Path) -> tuple[Path, Path]`, which writes `render.json` and `render.md`.
- `render_skill(skill_dir: Path) -> Report`: reads SKILL.md frontmatter (via `skill_factory.yamlish`) for `name`/`description`, raising RenderError if SKILL.md or the frontmatter is missing or invalid. It clears and rebuilds only `<skill>/.factory/dist/{claude,codex}/`, and copies SKILL.md and the allowed resource dirs, excluding `.factory/`, refusing symlinks and copying in binary mode. It writes `dist/codex/agents/openai.yaml` with at least `name` and `description` equal to the frontmatter values (yamlish.dumps output, readable back by yamlish.loads; an `interface:` block with display_name/short_description is optional). It never writes, renames or touches any canonical file outside `.factory/dist/` and the two report files.
- `add_parser(sub)`: registers subcommand `render` with positional `name` and `--out`.
- `run(args) -> int`: resolves the skill dir, renders, writes the report into `<skill>/.factory/`, prints the dist paths, and returns 0, or returns 2 with a `skillz: error:` message on RenderError or a missing skill.

## Tasks

1. **slug:** `validate-skill`
   - **goal:** Add a static, never-executing `validate_skill` with every REQ-002 rule, JSON and Markdown reports and the CLI handler functions, proven by bad-skill fixtures that each fire their rule with exit 1 while a clean fixture exits 0. Also anchor capture's NAME_RE with fullmatch.
   - **owns:** `src/skill_factory/validate.py`, `tests/test_validate.py`, `tests/fixtures/skills/` (new directory tree: one clean fixture plus one minimal directory per committed bad case: `clean-skill/`, bad name, name/directory mismatch, empty description, overlong description, disallowed top-level path, missing frontmatter, network call in `scripts/` (curl and urllib), destructive shell in `scripts/` (rm -rf)), `src/skill_factory/capture.py`, `tests/test_capture.py`
   - **needs_web:** no
   - **notes:** capture.py: `NAME_RE.match` becomes `NAME_RE.fullmatch` (or `\Z`-anchored) everywhere it is used. Add a test in tests/test_capture.py that fails on the old regex (e.g. `"abc\n"` is rejected). validate.py may import `NAME_RE`, `MAX_NAME`, `MAX_DESCRIPTION`, `RESOURCE_DIRS` from capture. Tests call `validate.run` through a local `argparse` parser built with `validate.add_parser`, or call `run(Namespace(...))` directly, so they do not depend on cli.py. The no-execution test must prove at runtime that validation spawns nothing: monkeypatch `subprocess.Popen`, `os.system` and `os.exec*`/`os.spawn*` to raise, and include a fixture script that would create a sentinel file if run, then assert the sentinel is absent. Overlong name, oversized file (over 1 MB), oversized skill (over 5 MB total) and fake secret cases (AKIA, sk-, ghp_ and a PEM private-key header, all built by concatenation) are generated in tmp_path. Each rule test asserts the rule id, severity and `run(...) == 1`. The clean test asserts `run(...) == 0` and that validate.json/validate.md exist.
   - **done when:** `uv run pytest -q tests/test_validate.py tests/test_capture.py` exits 0

2. **slug:** `render-variants`
   - **goal:** Add `render_skill` producing the Claude and Codex install variants (Codex with agents/openai.yaml) under `.factory/dist/`, with render.json/render.md and the CLI handler functions, proven to leave canonical files byte-identical. Also add read-only permissions to CI.
   - **owns:** `src/skill_factory/render.py`, `tests/test_render.py`, `.github/workflows/ci.yml`
   - **needs_web:** no
   - **notes:** Tests build their own canonical skill in tmp_path, either with `skill_factory.generate.generate` from a SkillSpec that has resources, or by writing files directly. They must not depend on task 1's fixtures. Include a scripts/ file, a references/ file and a binary asset. Required tests:
     - (a) Claude layout: the set of relative paths under `dist/claude/` equals the canonical set minus `.factory/`, and each file's bytes match.
     - (b) Codex layout: the same set plus `agents/openai.yaml`, whose yamlish-parsed name/description equal the frontmatter values.
     - (c) Byte-identical canonical files: sha256 of every canonical file (excluding `.factory/`) before and after `render_skill`, and after a second render, is identical, and no canonical file is added or removed.
     - (d) Re-render replaces stale dist files.
     - (e) Missing SKILL.md or bad frontmatter gives RenderError and `run(...) == 2`.
     - (f) render.json lists both variants.

     ci.yml gets a top-level `permissions:` block with `contents: read`; nothing else in it changes.
   - **done when:** `uv run pytest -q tests/test_render.py` exits 0

3. **slug:** `cli-validate-render`
   - **depends on:** tasks 1 and 2 merged (it imports `validate.add_parser/run` and `render.add_parser/run`).
   - **goal:** Wire `validate` and `render` into the `skillz` CLI via `SUBCOMMANDS`/`HANDLERS`, with end-to-end tests through `main()` and passing `--help` for both.
   - **owns:** `src/skill_factory/cli.py`, `tests/test_cli.py`, `README.md`
   - **needs_web:** no
   - **notes:** cli.py only appends `validate.add_parser`, `render.add_parser` to SUBCOMMANDS and `"validate": validate.run`, `"render": render.run` to HANDLERS, and adds `RenderError` to the caught exceptions if needed. No logic moves into cli.py. tests/test_cli.py covers:
     - `main(["validate", "--help"])` and `main(["render", "--help"])` exit 0.
     - `new` followed by `validate <name> --out tmp` returns 0 and writes `.factory/validate.json`.
     - A skill with an added `scripts/x.sh` containing `curl` returns 1.
     - `render <name> --out tmp` returns 0 and creates `dist/claude/SKILL.md` and `dist/codex/agents/openai.yaml`.
     - A missing skill returns 2.

     README gets a short usage section for both commands.
   - **done when:** `uv run pytest -q` exits 0 (criterion 0), and `uv run skillz validate --help` and `uv run skillz render --help` exit 0 (criteria 2 and 3)

## Criterion coverage
- criterion 0 (`uv run pytest -q`): all tasks; final check after task 3.
- criterion 1 (`pytest tests/test_validate.py tests/test_render.py`): tasks 1 and 2.
- criteria 2, 3 (`--help`): task 3.
- criterion 4 (judge rubric): validate rules, the clean fixture and no-execution proof in task 1; Claude layout, openai.yaml and byte-identical canonical files in task 2.
- Lint (`uv run ruff check src tests`) must pass in every task before handback.
