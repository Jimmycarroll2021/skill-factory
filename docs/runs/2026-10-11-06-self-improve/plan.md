Assumptions: (1) Captured cases live in a new module `src/skill_factory/captured.py`, not in evalcases.py, so evalcases stays free of file I/O. `evaluate.evaluate_skill` appends `captured.load_captured(skill_dir)` to `evalcases.generate_cases(spec)`. That one change is enough for every later consumer. `skillz eval` and `skillz run` see captured cases directly. `package_skill` calls `evaluate_skill`, so a new version's `reports/cases.json`/`eval.json` contain them, and `release.regression_check` re-grades those stored cases against the next candidate. release.py therefore does not change. `challenge` calls `load_captured` itself. (2) Candidate layout helpers (paths, ids, sha256 maps, verdict literals) go in a new module `src/skill_factory/candidates.py`, built in task 1. improve, challenge and promote then share one implementation. This lets task 2 (improve and challenge) and task 3 (promote) run in parallel against the contract below. (3) improve applies proposals deterministically and only to the frontmatter `description` and to the spec. The SKILL.md body after the closing `---` is copied byte for byte. Resource directories are copied unchanged. Proposals that need human judgement (`no-behaviours`) are recorded as skipped. Captured `--expect` cases are kept as eval cases but are not written into the spec. (4) The stable side of a challenge is the current canonical `skills/<name>/` plus `.factory/spec.json`, or the SKILL.md fallback of `evaluate.load_spec`. It is not a packaged version. challenge never writes into the stable skill or its `.factory/` reports. It writes only under `.factory/candidates/<id>/`. (5) promote is atomic per file: it writes temp siblings and `os.replace`s them, keeping backups. If `release.package_skill` then fails (validation or regression against an approved version), promote restores the backups and exits 1, so a refused or failed promote changes no canonical file. Verdicts candidate-better and no-difference are both acceptable (PRD REQ-009). (6) Dogfooding runs in task 4's worktree, where `skills/skill-factory/.factory/` is gitignored and fresh. The meta-skill body edits (loop instructions) are made first. The loop then improves the frontmatter description on top of them, so the promoted SKILL.md has both. The challenge report is copied to tracked `docs/evidence/skill-factory-challenge.md` and `.json`. (7) PR size: about 17 files and probably more than 500 lines, about a third of it prose and tests. REQ-009 is one cohesive feature. Splitting the four commands from their docs, or from the dogfood evidence, would ship a README describing commands that do not exist, or a loop no test has exercised end to end. The lead records this in REPORT.md.

# Mission 06 plan: the challenger loop (REQ-009)

Order: task 1 first. Tasks 2 and 3 run in parallel after task 1 is merged. Task 4 runs after tasks 2 and 3 are merged. No task edits a file another task owns. Only task 4 touches cli.py, which is why the README must change in the same task: `tests/test_docs.py::test_readme_documents_run_and_every_subcommand` fails as soon as a subcommand is registered without being documented.

## Shared contract (fixed; all tasks follow it)

General: everything is offline and deterministic, and no subprocess is spawned. JSON is written in the release.py style: `json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"`, UTF-8, `newline="\n"`. Timestamps are UTC `%Y-%m-%dT%H:%M:%SZ`. Every CLI module follows `add_parser(sub)` / `run(args) -> int`, takes `--out DIR` (the root containing `skills/`, default cwd) and the positional `name` checked with `NAME_RE.fullmatch`. Errors print `skillz: error: ...` on stderr. Writes never go through a symlinked `.factory/`, `candidates/` or candidate dir: refuse with exit 2.

### On-disk layout under `skills/<name>/.factory/`

```
cases/captured.json                    # task 1
candidates/<id>/<name>/SKILL.md        # canonical copy; dir named <name> so validate's name-mismatch rule holds
candidates/<id>/<name>/{scripts,references,assets}/...   # copied unchanged when present
candidates/<id>/spec.json              # candidate spec (SkillSpec.to_dict)
candidates/<id>/candidate.json         # task 2 (improve)
candidates/<id>/challenge.json         # task 2 (challenge)
candidates/<id>/challenge.md           # task 2 (challenge)
candidates/<id>/promoted.json          # task 3 (promote)
```

Candidate ids match `c[0-9]{3,9}` (re.ASCII): `c001`, `c002`, ... The next id is max existing + 1. Any id argument that does not match is an input error (exit 2), which also guards against path traversal.

### `captured.json` (task 1)

`{"schema": 1, "cases": [{"id": "cap-001", "kind": "positive"|"negative"|"behaviour", "prompt": str, "expected": [str], "note": str|null, "captured_at": ts, "source": "captured"}]}`. Ids are `cap-NNN` (3-digit zero padding, wider if needed), next = max + 1, never reused. Records are appended and never rewritten. The file is replaced atomically (temp sibling + `os.replace`). `--should-trigger` gives positive, `--should-not-trigger` gives negative, `--expect M [M ...]` gives behaviour with `expected=[M...]`.

### Module APIs

- `captured.py` (task 1): `CAPTURED_FILE = "captured.json"`. `captured_path(skill_dir) -> Path` returns `.factory/cases/captured.json`. `load_captured(skill_dir) -> list[evalcases.Case]` returns `[]` when the file is absent, raises `SpecError` when it is malformed, and builds each Case as `Case(id, kind, prompt, "captured", expected)`. `append_captured(skill_dir, prompt, kind, expected=None, note=None) -> dict` returns the new record and raises `SpecError` on an empty prompt or a bad kind. CLI `capture-failure`: positional `name`; `--prompt TEXT` (required); a required mutually exclusive group of `--should-trigger`, `--should-not-trigger` and `--expect MARKER...` (nargs="+"); `--note TEXT`; `--out`. It prints the case id and the file path. Exit 0 appended, 2 input error (invalid name, missing skill, empty prompt).
- `candidates.py` (task 1): constants `CANDIDATES_DIR="candidates"`, `CANDIDATE_JSON="candidate.json"`, `CHALLENGE_JSON="challenge.json"`, `CHALLENGE_MD="challenge.md"`, `PROMOTED_JSON="promoted.json"`, `CANDIDATE_ID_RE`, `VERDICTS = ("candidate-unsafe", "candidate-worse", "candidate-better", "no-difference")` (precedence order), `ACCEPTABLE = ("candidate-better", "no-difference")`. Class `CandidateError(ValueError)`. Functions `candidates_root(skill_dir)`, `candidate_dir(skill_dir, cid) -> Path` (raises CandidateError on a bad id), `candidate_skill_dir(skill_dir, cid) -> Path` (`.../<id>/<name>`), `next_candidate_id(skill_dir) -> str`, `tree_digests(root) -> dict[str, str]` (sha256 hex of SKILL.md and every regular file under scripts/, references/ and assets/, keyed by POSIX path relative to root, via `render._canonical_files`), `stable_digests(skill_dir) -> dict` (`tree_digests(skill_dir)` plus key `".factory/spec.json"` when that file exists), `candidate_digests(skill_dir, cid) -> dict` (`tree_digests(candidate_skill_dir)` plus key `"spec.json"`), `load_candidate_spec(skill_dir, cid) -> SkillSpec`.
- `improve.py` (task 2): `improve_skill(skill_dir) -> dict` returns the candidate.json dict. CLI `improve <name> [--out]` prints the id and candidate dir. Exit 0 created (also when there is nothing to apply; then `applied` is `[]`), 2 input error.
- `challenge.py` (task 2): `challenge_candidate(skill_dir, cid) -> dict` returns the challenge.json dict and raises `CandidateError` on missing inputs. CLI `challenge <name> <candidate-id> [--out]` prints the verdict and both report paths. Exit 0 for every verdict, 2 on missing or invalid inputs.
- `promote.py` (task 3): `promote_candidate(skill_dir, cid) -> dict` returns the promoted.json dict. It raises `PromoteRefused(ValueError)` (exit 1) when challenge.json is missing, stale or has a non-acceptable verdict, or when packaging failed and was rolled back. It raises `CandidateError` (exit 2) on a bad id or missing candidate. CLI `promote <name> <candidate-id> [--out]`. Exit 0 promoted and packaged, 1 refused, 2 input error. pipeline.py and the `run` command never import or call it.

### improve rules (task 2)

1. Stable spec = `evaluate.load_spec(skill_dir)`. Cases = `generate_cases(spec) + load_captured(skill_dir)`. Grade with `grade_offline`, then compute `propose_improvements(spec, cases, grades)` in memory. improve does not rewrite the stable `.factory/eval.json`.
2. `trigger-vocabulary`: for the failing positive cases named in `proposal.cases`, collect the content words missing from name and description, in order and deduplicated, with the same algorithm as evalcases. Append one sentence `" Also use when the user mentions: w1, w2, ...."` to the description. Drop words from the end until the description is at most 1024 chars, and skip the step if none fit.
3. `vague-description`: append the suggested trigger words in the same way, unless the step already appended them.
4. `negative-overlap`: add the failing negative prompts to `non_triggers` (deduplicated).
5. Captured positive prompts are appended to `triggers`, and captured negative prompts to `non_triggers`, deduplicated and order-preserving.
6. `no-behaviours` and anything else go to `skipped` with a reason.
7. Write the candidate SKILL.md as `yamlish.frontmatter_dumps(meta with new description, other keys in original order)` followed by the original body bytes. Write `spec.json`. Copy the resource directories. Never write outside `candidates/<id>/`.

`candidate.json` keys: `schema` (1), `id`, `skill`, `created_at`, `base_version` (the highest version in `release.load_versions`, or null), `base_sha256` (`stable_digests`), `applied` (list of `{"area", "action", "detail"}`), `skipped` (list of `{"area", "reason"}`), `captured` (captured case ids used).

### challenge rules (task 2)

1. Inputs: run `validate.validate_skill` on `skills/<name>/` and on `candidate_skill_dir`. Stable spec: `evaluate.load_spec`. Candidate spec: `load_candidate_spec`. Candidate identity: the name and description from the candidate SKILL.md frontmatter.
2. Union of cases: captured cases first (ids kept, `cap-NNN`), then `generate_cases(stable_spec)` (ids prefixed `stable/`), then `generate_cases(candidate_spec)` (ids prefixed `candidate/`). Deduplicate on (kind, lowercased whitespace-collapsed prompt, tuple(expected)). Each case keeps the first id and an `origins` list (`captured`/`stable`/`candidate`).
3. Grade the union on both sides with `grade_offline`. Per case, `change` is `regression` (stable pass, candidate fail), `improvement` (fail to pass) or `unchanged`. Behaviour cases are `pending-runner` on both sides and therefore unchanged.
4. Verdict, by precedence: `candidate-unsafe` if the candidate report is not ok (has any error-level finding); else `candidate-worse` if there is any regression; else `candidate-better` if there is any improvement; else `no-difference`.

`challenge.json` keys: `schema` (1), `skill`, `candidate`, `created_at`, `method`, `threshold`, `verdict`. `validation`: `{"stable": Report.to_dict(), "candidate": Report.to_dict(), "new_errors": [error findings of the candidate whose (rule, path) is absent from stable]}`. `cases`: a list of `{"id", "kind", "prompt", "expected", "origins", "stable": status, "candidate": status, "stable_score", "candidate_score", "change"}`. `summary`: `{"cases", "regressions", "improvements", "unchanged"}`. `regressions` and `improvements`: id lists. `weak_areas`: `{"stable": [areas], "candidate": [areas], "resolved": [...], "introduced": [...]}`, from `propose_improvements` over the union per side. `sha256`: `{"stable": stable_digests(...), "candidate": candidate_digests(...)}`.

`challenge.md`: first line `# Challenge: <skill> <id>`, then a line `Verdict: **<verdict>**`, then sections `## Validation`, `## Cases` (a table of id, kind, prompt, stable, candidate, change), `## Weak areas` and `## Notes` (states that grading is the offline lexical-overlap heuristic and that promotion is a separate human command, `skillz promote <name> <id>`).

### promote rules (task 3)

1. Read `challenge.json`. Exit 1 if it is missing or unreadable. Recompute `stable_digests` and `candidate_digests`. Exit 1 (stale) if either differs from `sha256` in the report.
2. Exit 1 when the verdict is not in ACCEPTABLE. In both refusal cases nothing is written, not even promoted.json.
3. Otherwise:
   - Back up and replace every stable canonical file with the candidate's. Write each to a temp sibling in the same directory, then `os.replace`. Remove stable canonical files absent from the candidate.
   - Write `.factory/spec.json` from the candidate's spec.json the same way.
   - Call `release.package_skill(skill_dir)` (patch bump), which creates a new `awaiting-approval` version. Existing version dirs are not touched.
   - On a `release.ReleaseError`, restore the backups, write nothing else and raise PromoteRefused.
   - On success, delete the backups and write `promoted.json`: `schema` (1), `skill`, `candidate`, `promoted_at`, `verdict`, `version` (the new semver), `previous_versions` (version list before packaging), `replaced` and `removed` (relative paths).

## Tasks

1. **slug:** `loop-foundation`
   - **goal:** Persist captured failures as eval cases (`captured.py` with `load_captured`/`append_captured` and the `capture-failure` subcommand module), wire them into every eval through `evaluate.evaluate_skill`, and provide the shared candidate layout helpers in `candidates.py`.
   - **owns:** `src/skill_factory/captured.py`, `src/skill_factory/candidates.py`, `src/skill_factory/evaluate.py`, `tests/test_capture_failure.py`
   - **needs_web:** no
   - **depends on:** nothing.
   - **notes:**
     - Do not edit cli.py. Tests call `captured.add_parser` on a fresh `argparse` subparsers object and then `captured.run(args)`, or call the API directly.
     - tests/test_capture_failure.py must cover these cases (all in `tmp_path`):
       - (a) Two appends produce `cap-001` and `cap-002` with the right kind, expected, note and captured_at, and the first record is unchanged after the second append.
       - (b) After a capture, `evaluate.evaluate_skill` writes `cases.json` and `eval.json` containing `cap-001` with its grade.
       - (c) After a capture, `release.package_skill` stores the captured case in `versions/<v>/reports/cases.json`, so the next regression sees it.
       - (d) `load_captured` on a skill without the file returns `[]`. Malformed JSON raises SpecError.
       - (e) CLI exit 2 for an invalid name, a missing skill and an empty prompt. The mutually exclusive group is required.
       - (f) The candidates.py helpers: `next_candidate_id`, the bad-id `CandidateError` (`../x`, `c1`, `C001`), and `tree_digests` ignoring `.factory/`.
     - Existing tests must keep passing. cases.json is unchanged when no captured.json exists.
   - **done when:** `uv run pytest -q tests/test_capture_failure.py tests/test_evaluate.py tests/test_release.py` exits 0

2. **slug:** `improve-challenge`
   - **goal:** Add `improve.py`, which builds a candidate under `.factory/candidates/<id>/` from eval proposals and captured failures without touching stable files, and `challenge.py`, which validates and grades the union of cases on both sides and writes challenge.json/challenge.md with the contracted verdict.
   - **owns:** `src/skill_factory/improve.py`, `src/skill_factory/challenge.py`, `tests/test_improve.py`, `tests/test_challenge.py`
   - **needs_web:** no
   - **depends on:** task 1 (`captured.py`, `candidates.py`, evaluate wiring). Runs in parallel with task 3.
   - **notes:**
     - Do not edit cli.py. Tests use the module API or `add_parser`/`run` with a local subparsers object.
     - tests/test_improve.py must show:
       - (a) In a tmp skill with a failing captured should-trigger prompt, improve writes `candidates/c001/<name>/SKILL.md`, `spec.json` and `candidate.json`. Every stable canonical file and `.factory/spec.json` are byte-identical before and after (compare bytes or sha256 of every file outside `.factory/candidates/`).
       - (b) The candidate description gains the missing words, stays at most 1024 chars, and the captured prompt now scores at or above `MATCH_THRESHOLD`. The captured positive is in the candidate's `triggers`, and a captured negative is in its `non_triggers`.
       - (c) The body after the frontmatter is byte-identical to the stable body.
       - (d) A second improve gives `c002`. The same inputs give identical SKILL.md and spec.json bytes.
       - (e) A near-1024-char description is truncated safely.
       - (f) Exit 2 for an invalid name or a missing skill.
     - tests/test_challenge.py must show:
       - (a) An improved candidate gives verdict `candidate-better`. challenge.json has every contracted key, and challenge.md has `Verdict: **candidate-better**`.
       - (b) A candidate whose SKILL.md description is hand-edited so that a trigger case passing on stable fails on the candidate gives `candidate-worse`, and the case appears in `regressions`.
       - (c) A candidate with a validation error (for example `scripts/clean.sh` containing `rm -rf /`, or a name mismatch) gives `candidate-unsafe`, with `new_errors` non-empty. This holds even when it also has regressions.
       - (d) An unchanged copy gives `no-difference`.
       - (e) Capture a failure after improve, then challenge: the union includes `cap-NNN` and both sides were graded for it. Together with test_capture_failure (b), this proves a later eval and challenge both include captured cases.
       - (f) The union contains cases from both `stable/` and `candidate/` origins, and both validation reports are present.
       - (g) challenge writes nothing outside `candidates/<id>/`.
       - (h) Exit 2 for a missing candidate or an invalid id. Exit 0 for the worse and unsafe verdicts.
   - **done when:** criterion 1: `uv run pytest -q tests/test_improve.py tests/test_challenge.py` exits 0

3. **slug:** `promote-cmd`
   - **goal:** Add `promote.py`, which promotes a challenged candidate only for an acceptable, non-stale verdict. It replaces the stable canonical files atomically, updates spec.json, packages a new awaiting-approval version and records promoted.json. pipeline/run never call it.
   - **owns:** `src/skill_factory/promote.py`, `tests/test_promote.py`
   - **needs_web:** no
   - **depends on:** task 1 (`candidates.py` digests and layout). Runs in parallel with task 2. Its tests build the candidate dir and `challenge.json` by hand from the contract, using `candidates.stable_digests`/`candidate_digests` for the `sha256` block, so they do not import challenge.py.
   - **notes:**
     - Do not edit cli.py or pipeline.py.
     - tests/test_promote.py must show (all in `tmp_path`):
       - (a) For verdicts `candidate-worse` and `candidate-unsafe`, promote exits 1. A full byte snapshot of `skills/<name>/` (including `.factory/versions/` and spec.json) is identical afterwards, and there is no promoted.json.
       - (b) A missing challenge.json exits 1. A stale challenge.json exits 1 with nothing changed: a candidate file edited after the challenge, or the stable SKILL.md edited.
       - (c) Acceptable verdicts (`candidate-better`, and separately `no-difference`) with an existing packaged (and approved) 0.1.0:
         - exit 0;
         - stable SKILL.md and spec.json now equal the candidate's;
         - `release.load_versions` shows 0.1.1 `awaiting-approval` with no approval.json;
         - every file of `versions/0.1.0/` is byte-identical and `release.verify_version` returns `[]`;
         - promoted.json has the contracted keys.
       - (d) When `release.package_skill` is patched to raise `release.RegressionFailed`, the stable files are restored byte-identical and promote exits 1.
       - (e) `src/skill_factory/pipeline.py` source does not contain "promote", and `cli.HANDLERS["run"] is pipeline.run`. A `pipeline.run_pipeline` run with `promote.promote_candidate` monkeypatched to raise still completes at awaiting-approval.
       - (f) Exit 2 for an invalid name, a bad id and a missing candidate dir.
   - **done when:** `uv run pytest -q tests/test_promote.py` exits 0

4. **slug:** `loop-cli-docs`
   - **goal:** Wire capture-failure, improve, challenge and promote into `skillz`, prove the whole loop end to end through `cli.main`, document it (README section, command table, diagram, CHANGELOG, meta-skill), and dogfood it on `skills/skill-factory` with the challenge report committed as evidence.
   - **owns:** `src/skill_factory/cli.py`, `tests/test_loop.py`, `tests/test_docs.py`, `README.md`, `CHANGELOG.md`, `skills/skill-factory/SKILL.md`, `docs/evidence/skill-factory-challenge.md`, `docs/evidence/skill-factory-challenge.json`
   - **needs_web:** no
   - **depends on:** tasks 2 and 3 merged.
   - **notes:**
     - **cli.py:** four SUBCOMMANDS entries (`captured.add_parser`, `improve.add_parser`, `challenge.add_parser`, `promote.add_parser`) and HANDLERS `"capture-failure"`, `"improve"`, `"challenge"`, `"promote"`. No logic goes in cli.py.
     - **tests/test_loop.py:**
       - (a) `main([cmd, "--help"])` exits 0 and mentions `--out`, for each of the four commands.
       - (b) An end-to-end run in `tmp_path` via `cli.main`: `new`, `package`, `approve` 0.1.0, `capture-failure --should-trigger`, `eval`, `improve`, `challenge`, `promote`. Returns 0, 0, 0, 0, 0, 0, 0, 0. eval.json contains `cap-001`. A new version exists and is awaiting approval, and 0.1.0 is intact.
       - (c) Promoting a candidate-worse candidate through the CLI exits 1.
     - **Docs:**
       - README gets a section titled `Self-improvement (the challenger loop)` with the four commands (`skillz capture-failure`, `skillz improve`, `skillz challenge`, `skillz promote`), the verdict literals and the rule that promotion is an explicit human command that the pipeline and `skillz run` never perform. Add the four commands to the command table with their exit codes.
       - Extend the Mermaid diagram after the registry node with the loop (capture-failure, improve, challenge, promote back to package). Keep the existing STAGES order intact, because test_docs checks it.
       - CHANGELOG gets an `## Unreleased` entry for mission 06.
     - **SKILL.md:** tell the agent about capture-failure, improve and challenge, and say to run promote only on an explicit human instruction naming the skill and candidate. Keep it under 100 lines and keep the existing "never run ... approve" wording.
     - **Dogfood**, in the worktree root, after the SKILL.md body edits are written:
       - Capture one or two realistic failures. At least one must be a should-trigger prompt that fails on stable according to `uv run skillz eval skill-factory --out .` and `.factory/eval.md`. Note that "turn this paragraph into a Claude Code skill" already passes (turn/claude/code overlap), so choose a prompt that is really missed, for example "Help me write a SKILL.md file so my agent learns this workflow".
       - Run `improve`, `challenge` and, if the verdict is acceptable, `promote`.
       - Copy `.factory/candidates/<id>/challenge.md` and `challenge.json` to `docs/evidence/`.
       - If the verdict is no-difference or worse, still commit the evidence and report it to the lead instead of hand-editing the description.
     - **tests/test_docs.py** additions:
       - the README contains `skillz challenge` and the loop section heading;
       - `docs/evidence/skill-factory-challenge.md` exists and contains `Verdict: **candidate-better**` or `Verdict: **no-difference**`;
       - each captured prompt (a module constant matching what was captured and appearing in the evidence file) scores at or above `evalcases.MATCH_THRESHOLD` against the committed SKILL.md name and description;
       - the existing meta-skill validation tests still pass.
     - `uv run skillz validate skill-factory` must exit 0.
     - Return to the lead: the captured prompts, the verdict and the promoted version.
   - **done when:** criterion 0: `uv run pytest -q` exits 0. Criteria 2 to 7 (the four `--help` commands, `uv run skillz validate skill-factory`, and the README containing `skillz challenge`) must also pass on the merged branch.

## Rubric (criterion 8) clause to evidence map

- improve writes the candidate under `.factory/candidates/` and leaves stable files byte-identical: tests/test_improve.py (a).
- capture-failure appends a persistent case that a later eval and challenge both include: tests/test_capture_failure.py (a, b, c) and tests/test_challenge.py (e).
- challenge runs validation and the union of cases on both sides, and writes both reports: tests/test_challenge.py (a, f).
- candidate-worse on a trigger regression: tests/test_challenge.py (b).
- candidate-unsafe on a validation error: tests/test_challenge.py (c).
- promote refuses worse or unsafe with a non-zero exit and changes nothing: tests/test_promote.py (a), tests/test_loop.py (c).
- promote replaces stable, packages a new awaiting-approval version and keeps the previous version intact: tests/test_promote.py (c), tests/test_loop.py (b).
- Nothing in the pipeline or run command calls promote: tests/test_promote.py (e).
- The meta-skill was improved through the loop, the evidence is committed and it still validates: docs/evidence/skill-factory-challenge.md, tests/test_docs.py, criterion 6.
