# skill-factory

`skillz` turns a paragraph into a tested, versioned Agent Skill for Claude Code and Codex. The package is `src/skill_factory/`; `skills/skill-factory/` is the meta-skill that drives it. Python 3.12, standard library only, offline by default.

## Checks (all must pass before a commit)
```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest -q
uv build
```
CI runs ruff and pytest on Ubuntu and Windows. Green CI on the latest commit is the evidence; no agent's report is.

## Definition of done
- Every behaviour change ships with a test that fails without it. Write the failing test first.
- Never skip, delete or weaken a test to get green.
- Tests are offline and deterministic: no real `claude` or `codex`, no network, `HOME` and `USERPROFILE` redirected to a temp directory.
- Paths use `pathlib`; files are UTF-8 with LF endings; nothing relies on symlinks, so the suite passes on Windows.
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
