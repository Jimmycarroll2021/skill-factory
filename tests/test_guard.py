"""The PreToolUse guard in .claude/hooks/guard.py: what it denies, what it lets through."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard.py"
_spec = importlib.util.spec_from_file_location("guard", GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin topic",
        "git -C /repo push -uf origin topic",
        "git -C /repo push --force-with-lease",
        "git push origin +topic",
        "git push --mirror",
        "git push origin main",
        "git -C /repo push origin HEAD:main",
        "git push origin topic:refs/heads/master",
        "git push origin --delete main",
        "git push --all",
        "git -C /repo reset --hard HEAD~1",
        "git branch -D topic",
        "git -C /repo branch --delete --force topic",
        "git checkout -- src/x.py",
        "git filter-repo --path x",
        "rm -rf build",
        "rm -r -f build",
        "rm -vRf build",
        "rm --recursive --force build",
        "pip install requests",
        "python3 -m pip install requests",
        "gh pr merge 4",
        "cat .env",
        "grep KEY config/.env.local",
        "docker run --env-file=.env img",
        "cd /repo && git status",
        "uv run pytest -q; git -C /repo push -f",
        "bash -c 'git push --force'",
        "eval git push origin main",
    ],
)
def test_denied(command):
    assert guard.bash_reason(command)


@pytest.mark.parametrize(
    "command",
    [
        "git -C /repo push -u origin ccr-topic",
        "git push origin topic --follow-tags",
        "git -C /repo status",
        "git -C /repo merge origin/main",
        "git -C /repo checkout topic",
        "git branch -d topic",
        "rm -f build/x.txt",
        "uv run pytest -q && uv run ruff check src tests",
        "cat .env.example",
        'git -C /repo commit -m "Deny git push --force and cat .env"',
        "git -C /repo commit -m \"$(cat <<'EOF'\nsubject\n\nbody\nEOF\n)\"",
        "cat <<EOF > notes.txt\ndon't stop\nEOF",
    ],
)
def test_allowed(command):
    assert guard.bash_reason(command) is None


@pytest.mark.parametrize(
    ("tool", "key", "path"),
    [("Read", "file_path", "/repo/.env"), ("Edit", "file_path", "/repo/.env.production"),
     ("Write", "file_path", ".envrc"), ("Grep", "path", "/repo/.env.test")],
)
def test_file_tools_cannot_touch_env_files(tool, key, path):
    assert guard.reason_for({"tool_name": tool, "tool_input": {key: path}}) == ".env access"
    allowed = {key: path + ".example"}
    assert guard.reason_for({"tool_name": tool, "tool_input": allowed}) is None


def _run(stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=stdin, capture_output=True, text=True, check=False
    )


def test_hook_exit_codes():
    allow = {"tool_name": "Bash", "tool_input": {"command": "git -C . status"}}
    deny = {"tool_name": "Bash", "tool_input": {"command": "git push -f"}}
    assert _run(json.dumps(allow)).returncode == 0
    denied = _run(json.dumps(deny))
    assert denied.returncode == 2
    assert "force push" in denied.stderr
    assert _run("{not json").returncode == 2
    assert _run("[]").returncode == 2
