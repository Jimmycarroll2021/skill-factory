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
