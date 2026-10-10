# skill-factory

skill-factory turns a plain-language description of a task into an agent skill: a
`skills/<name>/SKILL.md` directory that follows the Agent Skills layout. It checks the skill
statically (it never runs skill code) and renders install-ready variants for Claude and Codex.

## Install

```bash
uv sync --group dev
uv run skillz --help
```

## Commands so far

| Command | What it does |
| --- | --- |
| `skillz new <source>` | Capture a skill from a paragraph or a `.txt`/`.yaml`/`.yml`/`.json` spec and generate `skills/<name>/`. |
| `skillz validate <name>` | Statically check frontmatter, naming, paths, sizes, secrets, network calls and destructive shell. Writes `.factory/validate.json` and `validate.md`; exits 1 on errors. |
| `skillz render <name>` | Build the Claude and Codex variants under `.factory/dist/` and write `render.json` and `render.md`. Canonical files are never modified. |

All commands accept `--out <dir>` for the directory that contains `skills/` (default: the
current directory).

## Status

This is an interim README. The full README arrives with the pipeline in a later mission.
