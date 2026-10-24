# skillz-factory

Claude Code skills built to one evidence-based standard. Each skill's script is the judge: it documents its exit codes, refuses bad input, and is run by the repo gate on every change, including a case that must fail.

## Why this repo looks the way it does

The standard comes from a study of a large public skills collection ([docs/research/claude-skills-study.md](docs/research/claude-skills-study.md)). The short version:

- What predicted a good skill was wiring and honesty: scripts whose documented flags exist, exit codes that follow the verdict, and a description that says what the skill refuses.
- Template sections, size limits and "You are an expert" openers did not help, and the openers predicted worse skills.
- The most common failure was self-reported verification: checks that passed on any text an agent wrote, or verdicts that exited 0 on a fail.

## Use a skill

Copy a folder from `skills/` into your project's `.claude/skills/`, or into `~/.claude/skills/` for every project. Each SKILL.md lists its exact commands and what each exit code means.

| Skill | What it does |
|---|---|
| [demo-spec-lint](skills/demo-spec-lint/SKILL.md) | Blocks a demo spec whose golden-path steps name no observable result, before anything is built. |

## Check the repo

```bash
python3 scripts/check.py                     # the gate: last line is PASS or FAIL
python3 -m unittest discover -s tests -v     # the gate's own self-tests
```

`check.py` exits 0 on PASS, 1 on FAIL and 2 when it could not run. It needs Python 3.10 or later and nothing else.

## Contribute

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/SKILL-STANDARD.md](docs/SKILL-STANDARD.md).
