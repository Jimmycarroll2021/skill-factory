Assumptions: (1) Every new subcommand follows the existing pattern: positional `name`, `--out PATH` (type Path, default None meaning cwd) as the project root containing `skills/`, errors printed as `skillz: error: ...` on stderr. Because release.py and install.py each host more than one subcommand, they expose `add_<cmd>_parser(sub)` / `run_<cmd>(args) -> int` pairs, and cli.py adds one line per subcommand to SUBCOMMANDS and one to HANDLERS. (2) Package always regenerates its inputs so the snapshot is internally consistent: it runs `validate.validate_skill` (any error-level finding exits 1 and creates no version), `render.render_skill` (fresh `.factory/dist/`) and `evaluate.evaluate_skill(skill_dir)` offline with no runner (fresh `cases.json`/`eval.json`), writes their reports into `<skill>/.factory/` as those commands do, then snapshots. (3) The first version is always `0.1.0`. Later versions bump the highest existing semver directory: patch by default, `--minor` gives `X.(Y+1).0`, `--major` gives `(X+1).0.0`. `--set-version X.Y.Z` picks an explicit version; it exists so a clash can be tested. An existing version directory is never modified: a clash exits 2 and touches nothing. (4) Regression baseline = the highest APPROVED version below the candidate. "Passed before" means a case with status `pass` in that version's stored `reports/eval.json` results. "Now" means `evalcases.grade_offline` of that version's stored `reports/cases.json` against the candidate's SKILL.md frontmatter name and description. Behaviour cases stay `pending-runner` and are not compared. With no approved version the regression status is `no-baseline`. When newly failing cases exist and `--allow-regression` is absent, package exits 1, writes `regression.json`/`regression.md` to `<skill>/.factory/` only and creates NO version directory. With `--allow-regression` the version is created and its own `regression.json` records status `regressed`. (5) manifest.json is immutable, so its `status` is always `"awaiting-approval"`. A version's effective status is `"approved"` exactly when `<version>/approval.json` exists with `"status": "approved"`. Approval is idempotent: approving an approved version exits 0 and leaves the original approval.json byte-identical. Approve verifies the manifest checksums first and exits 1 on a mismatch. (6) Rollback target = the highest approved version strictly lower (semver) than the version currently installed for that name+platform+scope. Exit 1 when there is no current install or no such version. With `--platform`/`--scope` omitted, rollback applies to every current install of the name that matches the given filters. All selected installs are checked before any is changed, and if any of them cannot roll back, nothing changes and the exit is 1. (7) No module added by this mission imports `subprocess` or `os.system` or calls git in any form. The optional "source commit" manifest field is omitted. (8) The mission touches 6 files: release.py, install.py, cli.py and three test files. It will likely exceed the 500-line target, roughly 500 lines of source and 450 of tests. PRD "Delivery slicing" already records why: the approval gate and the installer that enforces it are only testable together. The lead records this in REPORT.md. (9) No README or CHANGELOG changes; mission 05 owns them.

# Mission 04 plan: versioned release, approval, install, registry, rollback (REQ-005, REQ-006)

Tasks 1 and 2 are independent and run in parallel. Each builds only against the on-disk contract below, and neither imports the other. Task 3 depends on both and is dispatched after both merge.

## Agreed on-disk contract (both tasks implement exactly this)

All JSON is written UTF-8 with `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False) + "\n"` and LF newlines. Timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`. Every path stored in JSON is POSIX and relative to the version directory unless stated otherwise. A semver is the strict `^\d+\.\d+\.\d+$` and is ordered numerically by tuple, never as a string.

```
<root>/skills/<name>/                          canonical skill (SKILL.md, scripts/, references/, assets/)
<root>/skills/<name>/.factory/versions/
    index.json                                 derived summary, rewritten by package/approve (informational)
    <semver>/                                  immutable once renamed into place
        manifest.json                          written last; covers every file below except itself and approval.json
        approval.json                          only after `skillz approve`; the sole approval signal
        regression.json, regression.md
        skill/SKILL.md, skill/scripts/..., ... canonical snapshot (same file set as render._canonical_files)
        dist/claude/...                        rendered Claude variant (copy of .factory/dist/claude/)
        dist/codex/...                         rendered Codex variant incl. agents/openai.yaml
        reports/validate.json validate.md render.json render.md eval.json eval.md cases.json
<root>/skills/registry.json                    registry of installs (project AND user scope)
```

- Package builds a version in a sibling temp dir, `versions/.tmp-<semver>-<token>/`, then `os.rename`s it to `<semver>/`. If the target already exists it refuses and removes its temp dir. A failure leaves no partial `<semver>/`.
- **manifest.json** keys: `schema` (1), `name`, `version`, `created_at`, `status` (always `"awaiting-approval"`), `previous_approved` (semver or null), `files` (a list of `{"path", "sha256"}` sorted by path, covering every file in the version dir except `manifest.json` and `approval.json`).
- **approval.json** keys: `name`, `version`, `status` (`"approved"`), `approved_at`, `approved_by`. `approved_by` comes from `getpass.getuser()`, falling back to `"unknown"` on any exception, or from an explicit `user=` argument.
- **index.json** keys: `name` and `versions` (ascending semver list of `{"version", "status", "created_at", "approved_at", "approved_by"}`). The approval fields are null when the version is unapproved. Readers must NOT depend on index.json: the source of truth is the version dirs plus approval.json.
- **regression.json** keys: `candidate`, `baseline` (semver or null), `status` (`"no-baseline"`, `"pass"` or `"regressed"`), `checked` (number of trigger cases compared), and `newly_failing` (a list of `{"id", "kind", "prompt", "before": "pass", "after": "fail", "score"}`). regression.md names every newly failing case id and prompt.
- Status literals: `"awaiting-approval"` and `"approved"`. Platforms: `"claude"` maps to `.claude/skills/<name>/` and `"codex"` maps to `.agents/skills/<name>/` (reuse `render.TARGETS`). Scopes: `"project"` installs under `<root>`. `"user"` installs under `home`, where `home` defaults to `Path.home()`, resolved at call time.
- **registry.json** keys:
  - `schema` (1).
  - `installs`: append-only history list of records.
  - `current`: a dict keyed `"<name>:<platform>:<scope>"` whose value is the record now installed there.
  - A record has the keys `name`, `version`, `platform`, `scope`, `path` (the absolute install dir as a string), `installed_at`, `approved_by`, `approved_at`, `action` (`"install"` or `"rollback"`) and `from_version` (null for installs).
  - The registry is written atomically: temp file in `skills/`, then `os.replace`.

## Agreed Python interfaces

### `src/skill_factory/release.py` (task 1)
- `class ReleaseError(ValueError)`, `class VersionExists(ReleaseError)`, `class VersionNotFound(ReleaseError)`, `class IntegrityError(ReleaseError)`, `class ValidationFailed(ReleaseError)`, and `class RegressionFailed(ReleaseError)`, which has a `.report: dict` attribute.
- `@dataclass Version(name: str, version: str, path: Path, status: str, created_at: str, approved_at: str | None = None, approved_by: str | None = None, regression: dict | None = None)`.
- `parse_semver(text) -> tuple[int, int, int]` (raises ReleaseError) and `next_version(existing: list[str], bump: str = "patch") -> str`, where `bump` is `"patch"`, `"minor"` or `"major"` and an empty `existing` gives `"0.1.0"`.
- `load_versions(skill_dir) -> list[Version]` (ascending; ignores temp and non-semver dirs) and `approved_versions(skill_dir) -> list[Version]`.
- `verify_version(version_dir) -> list[str]`: the paths whose sha256 mismatch or that are missing or extra compared with the manifest. An empty list means intact.
- `regression_check(skill_dir, candidate: str) -> dict`: returns the regression.json dict described above.
- `package_skill(skill_dir, bump="patch", *, set_version=None, allow_regression=False) -> Version`. It raises ValidationFailed, VersionExists or RegressionFailed, writes nothing under `versions/` on any of them, and rewrites index.json on success.
- `approve(skill_dir, version, user=None) -> Version`. It raises VersionNotFound or IntegrityError, never touches manifest.json, and rewrites index.json.
- CLI functions:
  - `add_package_parser(sub)` / `run_package(args)`: `package <name> [--minor | --major] [--set-version X.Y.Z] [--allow-regression] [--out]`. Exit 0 on success. Exit 1 on validation errors, newly failing regression cases without `--allow-regression`, or an integrity failure. Exit 2 on an invalid name, a missing skill or a version clash. On success it prints the version dir and the regression status.
  - `add_approve_parser(sub)` / `run_approve(args)`: `approve <name> <version> [--out]`. Exit 0, also when already approved. Exit 1 on an integrity mismatch. Exit 2 on a missing skill or version or an invalid semver.

### `src/skill_factory/install.py` (task 2; must not import release.py)
- `PLATFORMS = ("claude", "codex")`, `SCOPES = ("project", "user")`.
- `class InstallError(ValueError)`, `class NotApproved(InstallError)`, `class VersionNotFound(InstallError)`, `class NothingToRollBack(InstallError)`.
- `@dataclass InstallRecord`, with exactly the registry record keys as fields and `to_dict()`.
- `version_status(root, name) -> list[dict]`: an ascending list of `{"version", "status", "created_at", "approved_at", "approved_by"}` read from the version dirs, manifest.json and approval.json.
- `target_dir(root, name, platform, scope, home=None) -> Path`.
- `load_registry(root) -> dict`: returns `{"schema": 1, "installs": [], "current": {}}` when the file is absent.
- `install_skill(root, name, platform, scope, version=None, home=None) -> InstallRecord`:
  - Version selection: `version=None` picks the highest approved version. NotApproved when that version is unapproved or no approved version exists. VersionNotFound when the version dir is absent.
  - It verifies the sha256 of every `dist/<platform>/` file against manifest.json before copying, raising InstallError on a mismatch.
  - It copies `<version>/dist/<platform>/` into a sibling temp dir `.<name>.tmp-<token>` next to the target. It then renames any existing target to `.<name>.old-<token>`, renames temp to target and removes the old dir.
  - It refuses (InstallError) if the target or any directory between the scope base and the target is a symlink.
  - It appends to `installs`, updates `current`, and writes nothing else.
- `rollback_skill(root, name, platform=None, scope=None, home=None) -> list[InstallRecord]`: implements assumption (6). Records have `action="rollback"` and `from_version` set. It never deletes or alters anything under `versions/`.
- `list_skills(root) -> dict`: returns `{"installed": [current records sorted by key], "skills": {name: version_status(...)}}`, covering every `skills/*/` that has a `.factory/versions/` dir.
- CLI functions:
  - `add_install_parser(sub)` / `run_install(args)`: `install <name> --platform {claude,codex} --scope {project,user} [--version X.Y.Z] [--out]`. Exit 0 on success. Exit 1 on NotApproved, no approved version or an integrity failure; in each case nothing is written. Exit 2 on an invalid name, a missing skill, a missing version or a symlink refusal.
  - `add_rollback_parser(sub)` / `run_rollback(args)`: `rollback <name> [--platform] [--scope] [--out]`. Exit 0 or 1 (nothing to roll back).
  - `add_list_parser(sub)` / `run_list(args)`: `list [--out] [--json]`. It prints an installed table (NAME VERSION PLATFORM SCOPE APPROVED_BY PATH) and a versions table (NAME VERSION STATUS CREATED_AT), or the `list_skills` dict as JSON with `--json`. Always exit 0.

## Tasks

1. **slug:** `release-package`
   - **goal:** Add `release.py`: `skillz package` creates an immutable semver version with a SHA-256 manifest and a regression comparison against the last approved version, and `skillz approve` is the only way a version becomes approved, proven by tests/test_release.py.
   - **owns:** `src/skill_factory/release.py`, `tests/test_release.py`
   - **needs_web:** no
   - **notes:** Implement the contract and the release.py interface exactly. Tests build skills in tmp_path with `generate(capture_spec_file(FIXTURES/"eval"/"well-described.json"), tmp_path)` and never write into tests/fixtures/. They call `run_package`/`run_approve` through a local `argparse` parser built with `add_package_parser`/`add_approve_parser`, because cli.py is task 3's file. Autouse fixtures do two things. First, `monkeypatch.setenv` HOME and USERPROFILE to `tmp_path/"home"`. Second, monkeypatch `subprocess.run`, `subprocess.Popen` and `os.system` to fail the test. tests/test_release.py must show:
     - (a) The first package creates `versions/0.1.0/`, with status `awaiting-approval` and a manifest.json whose `files` cover `skill/SKILL.md`, `dist/claude/SKILL.md`, `dist/codex/agents/openai.yaml`, `reports/cases.json` and `reports/eval.json`, and every listed sha256 equals `hashlib.sha256` of the file. `verify_version` returns [] for it.
     - (b) `next_version` gives patch, `--minor` and `--major` bumps (0.1.0 to 0.1.1, 0.2.0 and 1.0.0).
     - (c) Packaging again creates 0.1.1. A recursive (path, bytes) snapshot of `0.1.0/` is identical before and after.
     - (d) `--set-version 0.1.0` when it exists returns 2 and the 0.1.0 snapshot is unchanged.
     - (e) Tampering a file in a version dir makes `verify_version` report it, and approve exits 1.
     - (f) Approve writes approval.json with `approved_at` ending in `Z` and `approved_by` (getpass patched, plus the fallback path when getuser raises). manifest.json is byte-identical after approve. index.json shows `approved`. Re-approving exits 0 and leaves approval.json byte-identical. Approving a missing version exits 2.
     - (g) Regression: package 0.1.0 and approve it, then rewrite the SKILL.md frontmatter description so a previously passing positive case no longer matches. Package then returns 1, creates no `0.1.1/`, and `.factory/regression.json` has status `regressed` with that case id in `newly_failing`. With `--allow-regression` it returns 0 and `0.1.1/regression.json` lists the same id.
     - (h) An unapproved previous version is not a baseline: regression status is `no-baseline`, or the older approved version is used when one exists.
     - (i) A skill with an error-level validation finding (copy `tests/fixtures/skills/destructive-shell` into tmp) returns 1 with no `versions/` entry. A missing skill returns 2.
     - (j) release.py's source matches neither `subprocess` nor the whole word `\bgit\b`, and no `.py` file under `src/skill_factory` contains the string `git push`.

     `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_release.py` exits 0

2. **slug:** `install-registry`
   - **goal:** Add `install.py`: `skillz install` installs only approved versions into exactly the Claude or Codex project or user skills directory, records them in `skills/registry.json`, `skillz rollback` reinstalls the previous approved version, and `skillz list` shows the registry and version history, proven by tests/test_install.py.
   - **owns:** `src/skill_factory/install.py`, `tests/test_install.py`
   - **needs_web:** no
   - **notes:** Implement the contract and the install.py interface exactly. Do not import release.py; it does not exist on this branch. tests/test_install.py has a fixture helper `make_version(root, name, version, approved=True)`. The helper writes `skills/<name>/SKILL.md`, then `.factory/versions/<v>/dist/claude/SKILL.md` and `dist/codex/SKILL.md` plus `dist/codex/agents/openai.yaml`, with content that embeds the version string so tests can tell versions apart. It then writes a contract-shaped manifest.json with real sha256 values and, when `approved`, approval.json. Tests call the `run_*` functions through a local argparse parser built from the `add_*_parser` functions. Autouse fixtures do two things. First, `monkeypatch.setenv` HOME and USERPROFILE to `tmp_path/"home"`. Second, monkeypatch `subprocess.run`, `subprocess.Popen` and `os.system` to fail the test. A `tree(path)` helper returns `{relative posix path: bytes}` for every file, covering both the project root and the fake home. tests/test_install.py must show:
     - (a) Installing an unapproved version (explicit `--version`, and the default when nothing is approved) returns 1. `tree(root)` and `tree(home)` are identical before and after, so no target, no registry and no temp dirs are left.
     - (b) An approved install with claude/project lands exactly in `<root>/.claude/skills/<name>/`, with files equal to `dist/claude/`. The tree diff is exactly those files plus `skills/registry.json`, and `.agents/` and `home/.claude` do not exist. The same holds for codex/project into `.agents/skills/<name>/`, including `agents/openai.yaml`.
     - (c) user scope with `home=None` lands in `tmp_path/"home"/.claude/skills/<name>/`, which proves Path.home() is honoured via the HOME/USERPROFILE redirect. Explicit `home=` works for codex/user. Nothing is created under `<root>/.claude` or `<root>/.agents`, and the project registry records the user install.
     - (d) registry.json `current["<name>:claude:project"]` and the last `installs` entry contain name, version, platform, scope, path, approved_by and approved_at.
     - (e) Installing 0.1.1 over 0.1.0 replaces the dir completely, so a stale file that existed only in 0.1.0 is gone, and no `.tmp-`/`.old-` dirs remain.
     - (f) Rollback after 0.1.0 then 0.1.1 reinstalls 0.1.0 (content check), records `action: rollback` with `from_version: 0.1.1`, and leaves every version dir's tree byte-identical. A second rollback, with no lower approved version, returns 1 and changes nothing. An unapproved 0.1.1-style middle version is skipped. Rollback with no install returns 1.
     - (g) A tampered dist file makes install return non-zero and write nothing.
     - (h) `list --json` shows the current installs and each version's status (`approved`/`awaiting-approval`). Plain `list` prints a table containing the name and version. An empty project returns 0.
     - (i) install.py's source matches neither `subprocess` nor the whole word `\bgit\b`, and no `.py` file under `src/skill_factory` contains the string `git push`.
     - (j) POSIX only (skip on Windows): a symlinked `.claude/skills` makes install return 2 and write nothing through it.

     `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q tests/test_install.py` exits 0

3. **slug:** `release-cli`
   - **depends on:** tasks 1 and 2 merged (it imports release and install).
   - **goal:** Wire `package`, `approve`, `install`, `rollback` and `list` into the `skillz` CLI and prove the whole flow end to end through `cli.main`: package, refused install, approve, install, package again, approve, install, rollback, list.
   - **owns:** `src/skill_factory/cli.py`, `tests/test_cli_release.py`
   - **needs_web:** no
   - **notes:**
     - cli.py only imports release and install and adds five SUBCOMMANDS entries and five HANDLERS entries. No logic moves into cli.py. If the main() exception tuple needs ReleaseError/InstallError for safety, add them there.
     - Tests use the same HOME/USERPROFILE redirect and subprocess-forbidding autouse fixtures as tasks 1 and 2.
     - tests/test_cli_release.py must show:
       - (a) `main([cmd, "--help"])` raises SystemExit(0) for each of package, approve, install, rollback and list, and the help text mentions `--out`. Install's help also mentions `--platform` and `--scope`.
       - (b) The end-to-end flow on a skill generated from `tests/fixtures/eval/well-described.json` in tmp_path:
         - package returns 0 and creates 0.1.0.
         - install claude/project returns 1 and `.claude/` does not exist.
         - approve 0.1.0 returns 0.
         - install returns 0 and the installed SKILL.md equals `versions/0.1.0/dist/claude/SKILL.md`.
         - an edit to the SKILL.md body only (description unchanged), then package, returns 0 and creates 0.1.1.
         - approve, then install, gives 0.1.1 installed.
         - rollback returns 0 and the installed SKILL.md equals 0.1.0's again, with both version dirs still present and unchanged.
         - `list --json` (capsys) shows both versions `approved` and the current claude/project install at 0.1.0.
       - (c) A user-scope install through main lands under the redirected HOME and nowhere in tmp project dirs.
     - `uv run ruff check src tests` must pass.
   - **done when:** `uv run pytest -q` exits 0 (criterion 0), and criteria 2 to 6 (`uv run skillz package|approve|install|rollback|list --help` each exit 0)

## Criterion coverage
- criterion 0 (`uv run pytest -q`): all tasks; final check after task 3.
- criterion 1 (`uv run pytest -q tests/test_release.py tests/test_install.py`): tasks 1 and 2.
- criteria 2 to 6 (`uv run skillz <cmd> --help` for package, approve, install, rollback and list): parsers come from tasks 1 and 2, wiring and help tests from task 3.
- criterion 7 (judge rubric), clause by clause:
  - HOME/USERPROFILE redirected temp dirs: autouse fixtures in all three test files.
  - Package creates a new semver version with a SHA-256 manifest and never modifies an existing one: task 1 (a)-(d).
  - Regression re-runs the previous approved version's cases and reports a newly failing case: task 1 (g) and (h).
  - Install refuses an unapproved version with non-zero exit and writes nothing: task 2 (a); end to end in task 3 (b).
  - An approved install lands exactly in `.claude/skills/<name>/`, `.agents/skills/<name>/` or the home equivalent, and nowhere else: task 2 (b) and (c), task 3 (c).
  - registry.json records name, version, platform, scope and approval: task 2 (d).
  - Rollback reinstalls the previous approved version without deleting any version: task 2 (f), task 3 (b).
  - No code path calls git push: task 1 (j) and task 2 (i) grep `src/skill_factory`, and the subprocess-forbidding fixtures prove nothing is spawned at runtime.
- Lint (`uv run ruff check src tests`) and `uv build` must pass at every merge.
