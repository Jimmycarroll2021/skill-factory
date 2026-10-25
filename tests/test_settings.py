"""CLAUDE.md says .claude/settings.json backs its hard rules: a deny list for the plain and
`git <options> ...` spellings (which covers `git -C <dir> ...`), the sandbox, and the guard hook
for what a prefix cannot match (tests/test_guard.py tests the hook itself)."""

from __future__ import annotations

import json
import re
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
CONFIG = json.loads(SETTINGS.read_text(encoding="utf-8"))
DENY = set(CONFIG["permissions"]["deny"])
PREFIXES = ("git push", "git * push")


def test_force_push_is_denied_in_every_form():
    for prefix in PREFIXES:
        for flag in ("--force*", "-f*", "* --force*", "* -f*", "* +*"):
            assert f"Bash({prefix} {flag})" in DENY


def test_push_to_main_is_denied_in_every_form():
    for prefix in PREFIXES:
        for branch in ("main", "master"):
            assert f"Bash({prefix} * {branch}*)" in DENY


def test_history_rewrites_are_denied_in_every_form():
    for prefix in ("git", "git *"):
        for command in ("reset --hard*", "filter-branch*", "filter-repo*", "branch -D*", "checkout -- *"):
            assert f"Bash({prefix} {command})" in DENY


def test_merges_and_env_files_are_denied():
    assert "Bash(gh pr merge*)" in DENY
    assert "mcp__github__merge_pull_request" in DENY
    for tool in ("Read", "Edit", "Write"):
        for name in (".env", ".env.local", ".env.production", ".envrc"):
            assert f"{tool}(**/{name})" in DENY


def test_powershell_is_denied():
    assert "PowerShell(*)" in DENY


def test_sandbox_stays_on_with_a_network_allowlist():
    sandbox = CONFIG["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert {"github.com", "pypi.org"} <= set(sandbox["network"]["allowedDomains"])


def test_guard_hook_runs_before_bash_and_file_tools():
    (entry,) = CONFIG["hooks"]["PreToolUse"]
    for tool in ("Bash", "Read", "Edit", "Write", "Grep", "mcp__github_enterprise__merge_pull_request"):
        assert re.fullmatch(entry["matcher"], tool)
    (hook,) = entry["hooks"]
    assert hook["command"] == 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py"'
    assert (SETTINGS.parent / "hooks" / "guard.py").is_file()
    assert set(CONFIG["hooks"]) == {"PreToolUse"}  # no hook points at a deleted harness script
