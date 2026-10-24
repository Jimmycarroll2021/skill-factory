# Contributing

## Adding a skill

1. Read [docs/SKILL-STANDARD.md](docs/SKILL-STANDARD.md). Use `skills/demo-spec-lint/` as the reference for shape.
2. Write `evals/cases.json` first: a passing case, a case that must fail, and a bad-input case, each with a `why`.
3. Write the script so those cases hold. Exit codes follow the verdict; `--sample --output json` exits 0; no input means exit 3.
4. Write SKILL.md around the script's exact commands, then `evals/evals.json` with at least one negative assertion and one prompt that should not trigger the skill.
5. Run both commands below until they pass.
6. Run the evals with and without the skill in fresh sessions and put the result in the pull request.

```bash
python3 scripts/check.py
python3 -m unittest discover -s tests -v
```

## Changing a gate

- Write the failing self-test first, then change `scripts/check.py`.
- A gate may only get stricter without a stated reason. A change that lets a previously failing skill pass needs a very good reason in the pull request.
- Every check's docstring names the failure it exists to prevent. Keep that true.

## Exceptions

Add a line to `scripts/allowlist.txt`: `<check> <path> <text from the finding's message>  # reason`. It suppresses only that finding. The gate fails on an entry without a reason, with an unknown check name, or that no longer suppresses anything.

## Pull requests

- One logical change per pull request.
- Run `/code-review medium` and answer every finding before asking for review.
- A human merges; agents never do.
- Never paste secrets, `.env` contents or private transcripts.
