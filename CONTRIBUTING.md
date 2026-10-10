# Contributing

Thanks for helping. skill-factory is small on purpose: Python 3.12, no runtime dependencies,
offline by default.

## Development setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Jimmycarroll2021/skill-factory
cd skill-factory
uv sync --group dev
```

## Checks

```bash
uv run pytest -q              # tests
uv run ruff check src tests   # lint
bash scripts/gate.sh          # lint, tests and build together, as the harness runs them
```

Run all three before opening a pull request. `uv build` must also succeed.

## Definition of done

- Every behaviour change ships with a test that fails without the change. Bug fixes include a
  regression test.
- Standard library only. Do not add runtime dependencies; dev dependencies are pytest and ruff.
- Tests are offline and deterministic. They never call a real `claude` or `codex` CLI, never
  use the network, and never touch the real home directory: redirect `HOME` and `USERPROFILE`
  to a temporary directory and use a fake runner.
- Paths use `pathlib`, files are read and written as UTF-8 with LF line endings, and nothing
  relies on symlinks, so the suite passes on Linux and Windows.
- The tool never runs a generated skill's scripts and never pushes or publishes anything.
- User-visible changes are noted in [CHANGELOG.md](CHANGELOG.md).

## Pull requests

- Keep a pull request small and about one outcome. Aim for roughly 500 changed lines and 10
  files at most; unrelated cleanup belongs in another pull request.
- CI must be green on Linux and Windows (ruff and pytest, see `.github/workflows/ci.yml`).
- Describe what changed, why, and how you checked it.
- Do not edit the quickship harness files (`scripts/`, `tests/*.sh`, `CLAUDE.md`, `BRIEF*.yaml`,
  `docs/runs/`) unless the pull request is about the harness.

## Reporting problems

Open an issue with the command you ran, the output, and your Python and uv versions. Do not
include secrets or private skill content.
