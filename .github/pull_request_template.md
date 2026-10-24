## Problem

<!-- What was wrong or missing, and how to see it. -->

## Change

<!-- What behaviour this introduces or changes. -->

## Evidence

- [ ] A test that fails without this change was added or updated, and was seen to fail first
- [ ] `uv run ruff check src tests` passes
- [ ] `uv run pytest -q` passes
- [ ] `uv build` succeeds
- [ ] CI is green on the latest commit (Ubuntu and Windows)

```text
<relevant failing-then-passing output>
```

## Scope

- [ ] One logical outcome, roughly ≤500 changed lines and ≤10 files (or say why not)
- [ ] `CHANGELOG.md` updated for user-visible changes
- [ ] No credentials, `.env` contents or private transcripts included

## Risks and remaining work

<!-- Known limitations, compatibility concerns, anything not verified. -->
