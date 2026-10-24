# skillz-factory

A repo of Claude Code skills built to one standard, where a script, not the model, decides whether a skill is right.

## The one rule
`python3 scripts/check.py` is the only thing that says PASS. Run it before every commit. No agent declares a skill done, and no report counts as evidence. If it fails, fix the cause, never the check. If a check is wrong, change it in its own commit with a self-test that proves the change.

## Where things are
- `docs/SKILL-STANDARD.md`: the rules for a skill, each tagged with what enforces it. Read it before writing or editing a skill.
- `docs/research/claude-skills-study.md`: the evidence behind the standard.
- `skills/<name>/`: one folder per skill. `skills/demo-spec-lint/` is the reference example.
- `scripts/check.py`: the gate. `scripts/allowlist.txt`: reasoned exceptions.
- `tests/test_check.py`: the gate's self-tests. Every check has a known-bad fixture that must fail.

## Adding or changing a skill
1. Copy the shape of `skills/demo-spec-lint/`, not its content.
2. Write the cases first: one that passes, one that must fail, one bad-input case.
3. Write the script, then SKILL.md around its exact commands and exit codes.
4. Run `python3 scripts/check.py` and `python3 -m unittest discover -s tests`.
5. Run the skill's evals with and without the skill and note the result in the PR.

## Hard rules (also enforced by `.claude/settings.json`)
- Never push to `main`, never force-push, never rewrite history, never merge a pull request.
- Never skip, delete or weaken a check or a test to get green. Add an allowlist entry with a reason instead, and say why in the PR.
- Never read or write `.env` files (templates such as `*.example` are fine).
- Scripts are stdlib Python. No `pip install` in skills or gates.
- Use `git -C <dir>`, never `cd` before git.

## Working style
- One logical change per commit. Plan in plain words before writing code.
- Before opening a PR: run `/code-review medium` and answer every finding.

## Lessons (each names its enforcer; add a line when Claude gets something wrong)
- A skill named like a bundled command replaces it for everyone. `[CI: reserved-name]`
- A verdict script that exits 0 on a failing verdict cannot gate anything. `[CI: cases]`
- A documented flag the script does not declare fails for the user, not for `--help`. `[CI: contract]`
- A gate that greps an agent's own report for words is not a gate. `[prose]`
- Add a lesson only if a competent agent with the current instructions would still get it wrong. Replace rather than append. `[prose]`
