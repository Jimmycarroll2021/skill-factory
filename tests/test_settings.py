"""The hard rules in CLAUDE.md that claim settings.json enforcement must be in
the deny list in every spelling an agent can use: plain, `git -C <dir> ...`
(the form CLAUDE.md requires), and `git <opts> ...`."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
DENY = set(SETTINGS["permissions"]["deny"])


class SettingsTest(unittest.TestCase):
    def test_force_push_denied_in_every_form(self):
        for prefix in ("git push", "git -C * push", "git * push"):
            for flag in ("--force*", "-f*", "* --force*", "* -f*", "* +*"):
                self.assertIn(f"Bash({prefix} {flag})", DENY)

    def test_push_to_main_denied_in_every_form(self):
        for prefix in ("git push", "git -C * push", "git * push"):
            for branch in ("main", "master"):
                self.assertIn(f"Bash({prefix} * {branch}*)", DENY)

    def test_history_rewrite_denied_in_every_form(self):
        for prefix in ("git", "git -C *", "git *"):
            for cmd in ("reset --hard*", "filter-branch*", "filter-repo*"):
                self.assertIn(f"Bash({prefix} {cmd})", DENY)

    def test_git_option_bypasses_denied(self):
        self.assertIn("Bash(git --git-dir*)", DENY)
        self.assertIn("Bash(git -c *)", DENY)

    def test_merge_denied(self):
        self.assertIn("Bash(gh pr merge*)", DENY)
        self.assertIn("mcp__github__merge_pull_request", DENY)

    def test_env_files_denied_for_read_edit_write(self):
        for tool in ("Read", "Edit", "Write"):
            for name in (".env", ".env.local", ".env.production", ".env.development", ".env.test", ".envrc"):
                self.assertIn(f"{tool}(**/{name})", DENY)


if __name__ == "__main__":
    unittest.main()
