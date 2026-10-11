# Decision log

One line per merged task. Append only; never edit past entries.

| date | task | change | why |
|---|---|---|---|
| 2026-10-10 | pkg-skeleton | smoke test tests/test_package.py added to task ownership | gate needs a real pytest test before the other tasks merge |
| 2026-10-10 | capture-spec | spec files are strict (unknown keys and non-string metadata raise SpecError); fixture names draft-release-notes-software, api-doc-reviewer, csv-data-cleaner | worker default, recorded |
| 2026-10-10 | generate-cli | allowed-tools written as space-delimited string per Agent Skills spec; --force rewrites only SKILL.md and .factory/spec.json | worker default, recorded |
| 2026-10-10 | security-01 | low notes carried to later missions: NAME_RE fullmatch, overwrite guard for .factory/spec.json, ci.yml permissions block | security PASS, low severity |
| 2026-10-10 | rename | repository target changed to Jimmycarroll2021/skill-factory; package renamed skill_factory, meta-skill skill-factory, CLI stays skillz | user instruction; keep names consistent with the public repo |
| 2026-10-10 | validate-skill | network-call and destructive-shell scanned only under scripts/; secret scanned in every text file except .factory/; symlinks and special files are path-not-allowed | worker default, recorded |
| 2026-10-10 | render-variants | symlinks skipped and recorded in render.json; openai.yaml carries name, description, interface and metadata.skill_file | worker default, recorded |
| 2026-10-10 | security-02 | low notes: skill dir symlink not rejected; pin CI actions by SHA (carried to mission 05) | security PASS, low severity |
| 2026-10-10 | eval-cases | MATCH_THRESHOLD 0.3, MIN_DESCRIPTION_WORDS 6; plan signatures win over dispatch text: grade_offline(cases, name, description), propose_improvements(spec, cases, grades) | worker followed plan contract |
| 2026-10-10 | baseline-runner | skill text embedded in the prompt inside <skill> tags for both CLIs; run_baseline(runner, cases, skill_text) per plan; verdicts skill-helps/skill-hurts/no-difference/error | worker followed plan contract |
| 2026-10-10 | eval-cli | spec fallback from SKILL.md frontmatter yields empty trigger lists; --strict exits 1 on failing trigger cases; baseline records requested runner name when skipped | worker default, recorded |
| 2026-10-10 | security-03 | low notes: add -- before prompts in runner argv (carry to mission 05); .factory inner-file symlinks; runner stderr excerpt in eval.json | security PASS, low severity |
| 2026-10-10 | install-registry | InstallIntegrityError and UnsafeTarget added (exit 1 vs 2); install/rollback verify dist files against the manifest; rollback plans all installs first then swaps; registry written atomically | worker default, recorded |
| 2026-10-10 | release-package | first package regression status is "no-baseline"; approve verifies checksums before anything else; version chosen and clash checked before any stage writes | worker default, recorded |
| 2026-10-10 | security-04 | low notes carried to mission 05: semver regex re.ASCII and digit cap; _baseline_cases AttributeError; escape raw registry fields in list output | security PASS, low severity |
| 2026-10-10 | meta-skill-docs | README documents skillz run from the plan contract; dogfooding install path writes .claude/skills and skills/registry.json into the repo (not ignored) | worker default, recorded |
| 2026-10-10 | security-polish | control chars in list cells escaped as \xNN; CI actions kept at tags with a pin-by-SHA TODO because no SHA is available offline | worker default, recorded |
| 2026-10-10 | pipeline-run | regression stage outcome comes from package_skill; next_steps commands append --out when root is not cwd; symlinked .factory refused before generating | worker default, recorded |
| 2026-10-10 | security-05 | low notes left open: .factory inner-file symlinks; SKILL.md install wording; CI SHA pinning | security PASS, low severity |
| 2026-10-11 | loop-foundation | candidates.worst_verdict added; captured.json validated strictly on load and append; eval.json keys unchanged | worker default, recorded |
| 2026-10-11 | promote-cmd | refuses a second promotion of the same candidate; only differing files replaced, removed files listed; backups under the candidate dir; any exception during replace or package restores stable files and exits 1 | worker default, recorded |
| 2026-10-11 | improve-challenge | improve exits 1 when nothing to improve (NothingToImprove); SKILL.md body copied byte for byte, only the description changes; candidate-unsafe whenever the candidate has any error finding; grading uses frontmatter name and description | worker default, recorded |
| 2026-10-11 | loop-cli-docs | dogfood captured cap-001 "Help me write a SKILL.md file so my agent learns this workflow" (score 0.14 on stable); challenge candidate-better; promoted skill-factory as 0.1.0 awaiting approval, not approved | worker report, recorded |
| 2026-10-11 | security-06 | low notes left open: cap captured prompt length; escape Markdown in challenge.md cells; promote TOCTOU | security PASS, low severity |
| 2026-10-11 | behaviour-cases | prompt template "<trigger>. Please work through the request end to end and show me the result."; guard falls back to a generic prompt if a behaviour sentence appears verbatim; specs with no triggers and no behaviours produce no behaviour cases | worker default, recorded |
| 2026-10-11 | docs-quality-tier | README quality-tier section placed before Quickstart; evidence files referenced as code spans until the lead produces them | worker default, recorded |
| 2026-10-11 | judge-core | verdict follows the judge winner not the score delta; transcripts truncated to TRANSCRIPT_LIMIT before judging; runner found but judge missing gives skipped: no judge | worker default, recorded |
| 2026-10-11 | lead-07 | runner.py runs claude/codex with the inherited cwd and environment, so a model call made from a quickship project picks up CLAUDE.md and hooks (the nested lead wrote RUN_STATE/RESULT/REPORT); fix task runner-neutral-cwd: run model CLIs from a fresh temp cwd with CLAUDECODE unset | lead finding during step L |
| 2026-10-11 | quality-integration | package with a missing runner still stores the skipped judge.json in the version; corrupt baseline judge.json raises IntegrityError only when the current call judged; pipeline makes 2 runs per case for evaluate baseline and 2 runs plus 1 judgement per case for the judge stage, package reuses the report | worker default, recorded |
| 2026-10-11 | runner-neutral-cwd | model CLIs run in a fresh skillz-run- temp cwd removed afterwards; child env drops CLAUDECODE and CLAUDE_CODE_SESSION_ID; caller env untouched | lead fix, recorded |
| 2026-10-11 | security-07 | low notes left open: judge prompt-injection surface is bounded by strict parsing; cap JSON nesting in parse_verdict; env still inherited by model CLIs | security PASS, low severity |
