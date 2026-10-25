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
uv run python scripts/check.py  # the skill-standard gate over skills/
uv build                      # package
```

Run all four before opening a pull request.

## Definition of done

- Every behaviour change ships with a test that fails without the change. Bug fixes include a
  regression test.
- Standard library only. Do not add runtime dependencies; dev dependencies are pytest and ruff.
- Tests are offline and deterministic. They never call a real `claude` or `codex` CLI, never
  use the network, and never touch the real home directory: redirect `HOME` and `USERPROFILE`
  to a temporary directory and use a fake runner.
- Paths use `pathlib`, files are read and written as UTF-8 with LF line endings, and nothing
  relies on symlinks, so the suite passes on Linux and Windows.
- The tool never runs a generated skill's scripts and never pushes or publishes anything;
  `skillz approve` and `skillz promote` are the human's commands.
- A receipt (`docs/RECEIPT.md`) records what ran; it never claims more than its `evidence` field.
  A change to package, approve, receipt or the regression report keeps every field of
  `skill-receipt/1` meaning what that document says, or defines a new schema id.
- A skill under `skills/` follows [docs/SKILL-STANDARD.md](docs/SKILL-STANDARD.md): copy the shape of
  `skills/demo-spec-lint/`, write its cases first (one that passes, one that must fail, one bad
  input), and pass both `scripts/check.py` and `skillz validate`. A wrong check is changed in its
  own commit with a self-test in `tests/test_check.py`; an exception goes in
  `scripts/allowlist.txt` with a reason.
- User-visible changes are noted in [CHANGELOG.md](CHANGELOG.md).

## Pull requests

- Keep a pull request small and about one outcome. Aim for roughly 500 changed lines and 10
  files at most; unrelated cleanup belongs in another pull request.
- CI must be green on Linux and Windows (ruff, pytest and `scripts/check.py`, see `.github/workflows/ci.yml`).
- Describe what changed, why, and how you checked it.

## Reporting problems

Open an issue with the command you ran, the output, and your Python and uv versions. Do not
include secrets or private skill content.
