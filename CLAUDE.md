# skill-factory

`skillz` turns a paragraph into a tested, versioned Agent Skill for Claude Code and Codex. The package is `src/skill_factory/`; `skills/skill-factory/` is the meta-skill that drives it. Python 3.12, standard library only, offline by default.

## Where things are
- `docs/SKILL-STANDARD.md`: the rules for a committed skill, each tagged with what enforces it. Read it before writing or editing anything under `skills/`.
- `docs/research/claude-skills-study.md`: the evidence behind the standard.
- `scripts/check.py`: the skill-standard gate over `skills/<name>/`; `scripts/allowlist.txt` holds reasoned exceptions; `tests/test_check.py` is its self-tests, each check with a known-bad fixture that must fail.
- `skills/demo-spec-lint/`: the reference example. Copy its shape, not its content.

## Checks (all must pass before a commit)
```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest -q
uv run python scripts/check.py
uv build
```
CI runs ruff, pytest and `scripts/check.py` on Ubuntu and Windows. Green CI on the latest commit is the evidence; no agent's report is.

## Definition of done
- Every behaviour change ships with a test that fails without it. Write the failing test first.
- Never skip, delete or weaken a test to get green.
- Tests are offline and deterministic: no real `claude` or `codex`, no network, `HOME` and `USERPROFILE` redirected to a temp directory.
- Paths use `pathlib`; files are UTF-8 with LF endings; nothing relies on symlinks, so the suite passes on Windows.
- If a check is wrong, change it in its own commit with a self-test that proves the change; never weaken it to get green. Add an allowlist entry with a reason instead, and say why in the PR.
- A changed skill under `skills/` gets its evals run with and without the skill; note the result in the PR.
- User-visible changes go in `CHANGELOG.md`. Keep README examples that claim to be real output in step with a real run.

## Hard rules
Enforced by `.claude/settings.json` (deny list, sandbox) and its hook `.claude/hooks/guard.py`; `tests/test_settings.py` and `tests/test_guard.py` pin both:
- Never push to `main`, never force-push, never rewrite history (`reset --hard`, `branch -D`, `checkout --`, `filter-*`), never merge a pull request.
- Never read or write `.env` files (see `.claude/rules/no-secrets.md`); `*.example`, `*.sample` and `*.template` are fine.
- Never `rm -rf` or `pip install`.
- Use `git -C <dir>`, never `cd` before git.

Followed, not enforced (review catches these):
- Never add a runtime dependency.
- The tool never runs a generated skill's scripts and never pushes or publishes anything; `skillz approve` and `skillz promote` are the human's commands.

## Working style
- One logical change per pull request, roughly 500 changed lines and 10 files at most.
- Run `/code-review medium` before opening a pull request and answer every finding.

## Lessons (each names its enforcer; add a line when Claude gets something wrong)
- A skill named like a bundled command replaces it for everyone. `[CI: reserved-name]`
- A verdict script that exits 0 on a failing verdict cannot gate anything. `[CI: cases]`
- A documented flag the script does not declare fails for the user, not for `--help`. `[CI: contract]`
- A gate that greps an agent's own report for words is not a gate. `[prose]`
- Add a lesson only if a competent agent with the current instructions would still get it wrong. Replace rather than append. `[prose]`
