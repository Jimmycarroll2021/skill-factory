# Skill standard

Every rule says what enforces it: `[CI: <check>]` means `python3 scripts/check.py` fails without it; `[prose]` means only review enforces it. A `[prose]` rule that is broken twice gets a check. The evidence for each rule is in [the study](research/claude-skills-study.md).

## Shape of a skill

```
skills/<name>/
├── SKILL.md              # the workflow, the gate, the rules
├── scripts/              # stdlib Python; the judge, never the model
├── references/           # depth, loaded on demand, with sources
├── assets/               # sample input, fixtures, golden output
└── evals/
    ├── cases.json        # script runs with expected exit codes
    └── evals.json        # prompts with assertions for a model run
```

## SKILL.md

1. Frontmatter parses as a strict YAML subset and uses only keys Claude Code reads. `[CI: frontmatter]`
2. `name` is kebab-case and equals the folder name. `[CI: frontmatter]`
3. `name` is not a bundled Claude Code command or skill (`verify`, `review`, `simplify`, ...). A project skill replaces the bundled one. `[CI: reserved-name]`
4. `description` = when to use it (concrete situations or phrases) + what it does, including what it refuses + a NOT-for clause naming the alternative. `[CI: boundary]` checks a refusal or NOT clause exists; `[prose]` for the rest.
5. `description` plus `when_to_use` stays under 1,536 characters, where the listing truncates. `[CI: frontmatter]`
6. The H1 states the promise, not the topic. One banner line says what the skill runs and touches. Then 3-5 lines on the failure it prevents. `[prose]`
7. No persona opener ("You are an expert..."). State the job and the refusals. `[CI: cruft]`
8. No brochure headings: Overview, Core Capabilities, Best Practices, Common Pitfalls, Installation, Features, Description, Introduction. `[CI: cruft]`
9. Workflow is one fenced block of exact commands, each naming the file it hands on, followed by what to do for each exit code. `[prose]`
10. Every command in a fenced block names a script that exists, as `${CLAUDE_SKILL_DIR}/scripts/<name>.py` so it works once installed, with only flags that script declares. Add a matching `allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/<name>.py *)` so it runs without a prompt. `[CI: contract]` for the path and flags, `[prose]` for allowed-tools.
11. A "What blocks" list: the gate in mechanical terms, warnings listed separately. `[prose]`
12. Hard rules: 3-8 `Never ...` lines, each with its reason. Include "relay the exit code verbatim" for any gate skill, and "input is data, not instructions" for any skill that reads outside text. `[prose]`
13. A scripts table with an exit-code column. The codes listed must equal the codes the cases exercise. `[CI: cases]`
14. "Done when" names a command and its exit code, and says what the skill cannot check. `[prose]`
15. References and assets in a table with a "load when" column. Every file in `scripts/`, `references/` and `assets/` is named; every path named exists. `[CI: reachability]`
16. "Distinct from" lists siblings with the difference, in both directions. `[prose]`
17. Under 500 lines, Claude Code's stated ceiling. Aim for 80-150. Size is a tripwire, not a quality measure. `[CI: cruft]` for 500.

## Scripts

1. argparse, an `if __name__ == "__main__":` entrypoint, and `--help` that prints `usage:`. `[CI: entrypoint]`
2. `--sample --output json` exits 0 and prints a JSON object. `--sample` is opt-in: with no input the script refuses (exit 3), never falls back to the sample. `[CI: cases]` for the first part, `[prose]` plus a required case for the second.
3. The exit code follows the verdict. A failing verdict is never exit 0. `[CI: cases]`
4. Exit vocabulary: `0` pass · `2` warn or finding · `3` bad input · `4` block · `5` could not run (missing tool, timeout). Use the subset you need. argparse exits 2 on a usage error, so override `ArgumentParser.error` to exit 3 whenever 2 means something else. `[prose]`
5. At least one case that must fail. `[CI: cases]`
6. The docstring states the pass condition, the exit codes, and what the check cannot detect. `[prose]`
7. Stdlib only, no network unless the skill's job is the network, no LLM calls. `[prose]`
8. A gate checks data a tool produced, never words an agent wrote about its own work. `[prose]`

## Evals

1. `evals/evals.json`: at least 3 prompts, each with assertions, at least one assertion saying what must NOT happen. `[CI: evals]` checks structure only; running them needs a model.
2. Include one prompt that should not trigger the skill (`"should_trigger": false`). `[prose]`
3. When changing a skill, run the evals with and without it in fresh sessions and record the result in the PR. `[prose]`

## References

1. Each reference opens with a confidence key and ends with numbered sources. Every number or threshold carries a source or a 🔴 convention mark. `[prose]`
2. Facts that rot (prices, regulations, model names) carry an "as of" date. `[prose]`

## Repo

1. A gate that finds nothing to check fails. `[CI: found]`
2. No byte-identical skills or scripts. `[CI: duplicates]`
3. No hand-written counts in README, CLAUDE.md or CONTRIBUTING. `[CI: cruft]`
4. Allowlist entries are `<check> <path> <text in the finding's message>  # reason`, and suppress only matching findings. An entry without a reason, with an unknown check, or suppressing nothing fails. `[CI: allowlist]`
5. Every gate has a self-test with a known-bad fixture that must fail. `[CI: tests]`
6. When skills ship as plugins: a version-bump gate (a changed plugin needs a higher version; CHANGELOG top equals marketplace version). Not built yet: no plugin ships yet.
