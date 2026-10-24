"""Self-tests for scripts/check.py: a known-good skill passes, and every defect
class the gate exists for fails on its own check (the negative control the
study found missing from the source repo's gates).

Run: uv run pytest -q tests/test_check.py (or python3 -m unittest discover -s tests -p test_check.py)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check.py"

SKILL_MD = textwrap.dedent("""\
    ---
    name: tiny-gate
    description: >
      Use when someone needs a tiny gate checked. Exits 4 on a bad input file.
      NOT for anything real (use a real skill).
    ---

    # tiny-gate — the smallest skill that passes every check

    ```bash
    python3 ${CLAUDE_SKILL_DIR}/scripts/tiny.py --input assets/ok.txt
    ```

    ## Scripts

    | Script | Job | Exit codes |
    |---|---|---|
    | `scripts/tiny.py` | checks a file says ok | 0 PASS · 4 BLOCK |

    See `references/why.md`.
    """)

TINY_PY = textwrap.dedent("""\
    import argparse, json, sys
    from pathlib import Path

    def main():
        ap = argparse.ArgumentParser(description="tiny gate")
        ap.add_argument("--input")
        ap.add_argument("--sample", action="store_true")
        ap.add_argument("--output", choices=["text", "json"], default="text")
        a = ap.parse_args()
        text = "ok" if a.sample else Path(a.input).read_text().strip()
        code = 0 if text == "ok" else 4
        print(json.dumps({"exit": code}) if a.output == "json" else code)
        return code

    if __name__ == "__main__":
        sys.exit(main())
    """)

CASES = [
    {"script": "scripts/tiny.py", "args": ["--input", "assets/ok.txt"], "expect_exit": 0, "why": "ok passes"},
    {"script": "scripts/tiny.py", "args": ["--input", "assets/bad.txt"], "expect_exit": 4, "why": "bad blocks"},
]
EVALS = [
    {"prompt": "check ok.txt", "assertions": ["reports PASS"]},
    {"prompt": "check bad.txt", "assertions": ["reports BLOCK", "does NOT call it a pass"]},
    {"prompt": "write me a poem", "assertions": ["does not run tiny.py"]},
]


class GateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="skillz-gate-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.skill = self.make_skill("tiny-gate")

    def env(self):
        """The real environment with HOME and USERPROFILE moved into the temp root, so no script
        (the gate, or a skill script it runs) sees the developer's home."""
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        return {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}

    def make_skill(self, name, md=SKILL_MD):
        d = self.root / "skills" / name
        for sub in ("scripts", "assets", "references", "evals"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(md.replace("tiny-gate", name), encoding="utf-8")
        (d / "scripts" / "tiny.py").write_text(TINY_PY, encoding="utf-8")
        (d / "assets" / "ok.txt").write_text("ok\n", encoding="utf-8")
        (d / "assets" / "bad.txt").write_text("nope\n", encoding="utf-8")
        (d / "references" / "why.md").write_text("Because.\n", encoding="utf-8")
        (d / "evals" / "cases.json").write_text(json.dumps(CASES), encoding="utf-8")
        (d / "evals" / "evals.json").write_text(json.dumps(EVALS), encoding="utf-8")
        return d

    def edit(self, rel, old, new):
        p = self.skill / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text, f"fixture edit target missing in {rel}")
        p.write_text(text.replace(old, new), encoding="utf-8")

    def gate(self, *extra):
        r = subprocess.run([sys.executable, str(CHECK), "--root", str(self.root), *extra],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False, env=self.env())
        return r.returncode, r.stdout + r.stderr

    def assertFails(self, check, needle=""):
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.strip().splitlines()[-1], "FAIL", out)
        hits = [l for l in out.splitlines() if l.startswith(f"{check}:")]
        self.assertTrue(hits, f"expected a '{check}' finding:\n{out}")
        if needle:
            self.assertTrue(any(needle in l for l in hits), f"no '{check}' finding mentions {needle!r}:\n{out}")

    # -- known good ------------------------------------------------------------
    def test_good_skill_passes(self):
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "PASS")

    def test_this_repo_passes(self):
        r = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, check=False, env=self.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # -- refuse on empty ---------------------------------------------------------
    def test_empty_tree_fails(self):
        shutil.rmtree(self.root / "skills")
        self.assertFails("found", "refusing to pass")

    # -- frontmatter, names, boundary -------------------------------------------
    def test_unknown_frontmatter_key_fails(self):
        self.edit("SKILL.md", "name: tiny-gate", "name: tiny-gate\ntriggers: tiny")
        self.assertFails("frontmatter", "unknown key 'triggers'")

    def test_unclosed_frontmatter_fails(self):
        self.edit("SKILL.md", "\n---\n\n# tiny", "\n\n# tiny")
        self.assertFails("frontmatter", "not closed")

    def test_name_must_match_folder(self):
        self.edit("SKILL.md", "name: tiny-gate", "name: other-name")
        self.assertFails("frontmatter", "does not match folder")

    def test_overlong_description_fails(self):
        self.edit("SKILL.md", "NOT for anything real", "NOT for anything real " + "x" * 1600)
        self.assertFails("frontmatter", "truncates")

    def test_reserved_name_fails(self):
        shutil.rmtree(self.skill)
        self.skill = self.make_skill("verify")
        self.assertFails("reserved-name", "'verify'")

    def test_description_without_boundary_fails(self):
        self.edit("SKILL.md", "Exits 4 on a bad input file.\n  NOT for anything real (use a real skill).",
                  "Checks a file says ok.")
        self.assertFails("boundary")

    # -- wiring ----------------------------------------------------------------
    def test_documented_flag_missing_from_script_fails(self):
        self.edit("SKILL.md", "--input assets/ok.txt\n```", "--input assets/ok.txt --audience devs\n```")
        self.assertFails("contract", "--audience")

    def test_command_naming_missing_script_fails(self):
        self.edit("SKILL.md", "${CLAUDE_SKILL_DIR}/scripts/tiny.py --input", "${CLAUDE_SKILL_DIR}/scripts/ghost.py --input")
        self.assertFails("contract", "ghost.py")

    def test_script_without_entrypoint_fails(self):
        self.edit("scripts/tiny.py", 'if __name__ == "__main__":\n    sys.exit(main())\n', "main()\n")
        self.assertFails("entrypoint", "entrypoint")

    def test_orphan_reference_fails(self):
        (self.skill / "references" / "unused.md").write_text("never named\n", encoding="utf-8")
        self.assertFails("reachability", "unused.md")

    def test_phantom_path_fails(self):
        self.edit("SKILL.md", "See `references/why.md`.", "See `references/why.md` and `references/gone.md`.")
        self.assertFails("reachability", "gone.md")

    # -- behaviour -------------------------------------------------------------
    def test_verdict_that_exits_zero_fails(self):
        # the aeo_audit / phase_gate_scorer defect: a failing verdict that still exits 0
        self.edit("scripts/tiny.py", 'code = 0 if text == "ok" else 4', "code = 0")
        self.assertFails("cases", "expected 4")

    def test_documented_code_not_exercised_fails(self):
        self.edit("SKILL.md", "0 PASS · 4 BLOCK", "0 PASS · 3 bad input · 4 BLOCK")
        self.assertFails("cases", "documented exit codes")

    def test_no_negative_case_fails(self):
        (self.skill / "evals" / "cases.json").write_text(json.dumps(CASES[:1]), encoding="utf-8")
        self.assertFails("cases", "negative control")

    def test_broken_sample_fails(self):
        self.edit("scripts/tiny.py", 'text = "ok" if a.sample', 'text = "nope" if a.sample')
        self.assertFails("cases", "--sample --output json exited 4")

    def test_evals_without_negative_assertion_fails(self):
        evals = [{"prompt": f"p{i}", "assertions": ["reports PASS"]} for i in range(3)]
        (self.skill / "evals" / "evals.json").write_text(json.dumps(evals), encoding="utf-8")
        self.assertFails("evals", "negative assertion")

    # -- cruft and duplicates -----------------------------------------------------
    def test_persona_opener_fails(self):
        self.edit("SKILL.md", "# tiny-gate — the smallest", "You are an expert gatekeeper.\n\n# tiny-gate — the smallest")
        self.assertFails("cruft", "persona")

    def test_brochure_heading_fails(self):
        self.edit("SKILL.md", "## Scripts", "## Overview\n\nA gate.\n\n## Scripts")
        self.assertFails("cruft", "Overview")

    def test_hand_written_count_fails(self):
        (self.root / "README.md").write_text("We ship 12 skills.\n", encoding="utf-8")
        self.assertFails("cruft", "README.md:1")

    def test_duplicate_skill_fails(self):
        twin = self.make_skill("tiny-gate-copy")
        (twin / "SKILL.md").write_text((self.skill / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertFails("duplicates", "identical")

    # -- allowlist ---------------------------------------------------------------
    def allow(self, line):
        (self.root / "scripts").mkdir(exist_ok=True)
        (self.root / "scripts" / "allowlist.txt").write_text(line + "\n", encoding="utf-8")

    def test_allowlist_with_reason_suppresses(self):
        (self.skill / "references" / "unused.md").write_text("x\n", encoding="utf-8")
        self.allow("reachability skills/tiny-gate/references/unused.md orphan  # kept for a follow-up skill")
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_allowlist_without_reason_fails(self):
        (self.skill / "references" / "unused.md").write_text("x\n", encoding="utf-8")
        self.allow("reachability skills/tiny-gate/references/unused.md orphan")
        self.assertFails("allowlist", "reason")

    def test_stale_allowlist_entry_fails(self):
        self.allow("reachability skills/tiny-gate/references/long-gone.md orphan  # was an orphan")
        self.assertFails("allowlist", "stale")

    def test_unknown_check_in_allowlist_fails(self):
        self.allow("reachabilty skills/tiny-gate/SKILL.md orphan  # typo in the check name")
        self.assertFails("allowlist", "unknown check")


    # -- review round 1: gaps found by /code-review --------------------------------
    def test_lowercase_not_is_not_a_boundary(self):
        self.edit("SKILL.md", "Exits 4 on a bad input file.\n  NOT for anything real (use a real skill).",
                  "Checks a file that is not yet checked.")
        self.assertFails("boundary")

    def test_sentence_initial_never_is_a_boundary(self):
        self.edit("SKILL.md", "Exits 4 on a bad input file.\n  NOT for anything real (use a real skill).",
                  "Checks a file says ok. Never approves a file it has not read.")
        code, out = self.gate("--only", "boundary")
        self.assertEqual(code, 0, out)

    def test_incidental_lowercase_never_is_not_a_boundary(self):
        self.edit("SKILL.md", "Exits 4 on a bad input file.\n  NOT for anything real (use a real skill).",
                  "Checks files that never change.")
        self.assertFails("boundary")

    def test_bare_script_path_in_command_fails(self):
        self.edit("SKILL.md", "python3 ${CLAUDE_SKILL_DIR}/scripts/tiny.py", "python3 scripts/tiny.py")
        self.assertFails("contract", "CLAUDE_SKILL_DIR")

    def test_other_skill_paths_are_not_resolved_here(self):
        self.edit("SKILL.md", "--input assets/ok.txt\n```", "--input assets/ok.txt\n```\n\n```bash\npython3 ../other/scripts/tiny.py --whatever\n```")
        _code, out = self.gate("--only", "contract")
        self.assertNotIn("--whatever", out)

    def test_tilde_and_indented_fences_are_checked(self):
        self.edit("SKILL.md", "See `references/why.md`.",
                  "See `references/why.md`.\n\n1. Step\n\n   ~~~bash\n   python3 ${CLAUDE_SKILL_DIR}/scripts/tiny.py --nope\n   ~~~\n")
        self.assertFails("contract", "--nope")

    def test_exit_codes_come_from_the_exit_column_only(self):
        self.edit("SKILL.md", "See `references/why.md`.",
                  "| File | Load when |\n|---|---|\n| `scripts/tiny.py` | step 1 of 5 |\n\nSee `references/why.md`.")
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_allowlist_entry_does_not_hide_other_findings_on_the_same_path(self):
        self.edit("scripts/tiny.py", 'code = 0 if text == "ok" else 4', "code = 0")
        self.allow("cases skills/tiny-gate/evals/cases.json negative  # unrelated exception")
        self.assertFails("cases", "expected 4")

    def test_non_utf8_skill_is_a_finding_not_a_crash(self):
        (self.skill / "SKILL.md").write_bytes("---\nname: tiny-gate\ndescription: caf\xe9\n---\n".encode("cp1252"))
        self.assertFails("frontmatter", "UTF-8")

    def test_bom_is_a_finding(self):
        p = self.skill / "SKILL.md"
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
        self.assertFails("frontmatter", "BOM")

    def test_crlf_line_endings_pass(self):
        # Windows editors (and Python's text mode on Windows) write CRLF; the gate
        # must read the skill, not reject it as having no frontmatter.
        p = self.skill / "SKILL.md"
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_cases_json_not_a_list_is_a_finding(self):
        (self.skill / "evals" / "cases.json").write_text("5", encoding="utf-8")
        self.assertFails("cases", "list")

    def test_script_typo_flag_is_bad_input_in_exemplar(self):
        r = subprocess.run([sys.executable, str(ROOT / "skills/demo-spec-lint/scripts/spec_lint.py"), "--inptu", "x"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False, env=self.env())
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_sample_and_input_together_refuse_in_exemplar(self):
        r = subprocess.run([sys.executable, str(ROOT / "skills/demo-spec-lint/scripts/spec_lint.py"),
                            "--input", "/nonexistent.md", "--sample"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False, env=self.env())
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    # -- harness errors ------------------------------------------------------------
    def test_unknown_check_name_is_exit_2(self):
        code, out = self.gate("--only", "nonsense")
        self.assertEqual(code, 2, out)

    def test_unreadable_skill_md_is_exit_2_not_fail(self):
        (self.root / "skills" / "broken" / "SKILL.md").mkdir(parents=True)  # read_bytes raises
        code, out = self.gate()
        self.assertEqual(code, 2, out)
        self.assertIn("could not run", out)


if __name__ == "__main__":
    unittest.main()
