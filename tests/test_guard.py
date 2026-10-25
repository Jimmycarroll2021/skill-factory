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
        # Codex review: bypasses the first version allowed
        "git -C /repo --work-tree /repo push -uf origin HEAD:topic",
        "env -i rm -r -f build",
        "sudo -u me git push --force",
        "git.exe -C . push -uf origin topic",
        "pwsh -Command \"git push origin HEAD:main\"",
        "cmd /c dir",
        "true # comment\nrm -r -f build",
        "printf x#y; cat .env",
        "cat .en\\\nv",
        "git -C . push \\\n origin HEAD:main",
        "if true; then rm -r -f build; fi",
        "for d in a b; do git push -f origin $d; done",
        "{ rm -rf build; }",
        "find . -name x -exec rm -rf {} +",
        "gh --repo o/r pr merge 4",
        "gh pr --repo o/r merge 4",
        "gh api -X PUT repos/o/r/pulls/4/merge",
        "cat .ENV",
        "cat config/.Env.Production",
        "echo $(git push -f origin topic)",
        'echo "$(cat .env)"',
        "echo `rm -rf build`",
        "echo deploy > infra/main.tf",
        # Codex review round 2
        "bash -lc 'true; rm -r -f build'",
        "sh -ec 'git push -f origin topic'",
        "# '\nrm -r -f build\n# '",
        "gh api graphql -F query=@request.graphql",
        "gh api repos/o/r/issues -f title=x",
        # Codex review round 3: what the guard cannot read is denied
        "cat <<'EOF'\n'\nEOF\nr\\m -r -f build",
        "cat <<EOF > notes.txt\ndon't stop\nEOF",
        "printf '%s\\n' \\ #literal; rm -r -f build",
        # Codex review round 4
        "bash -c -- 'true; rm -r -f build'",
        "bash -c -o pipefail 'git push -f origin topic'",
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
        "git -C /repo log --grep '#123'",
        "cat infra/main.tf",
        "uv run pytest -q tests/test_guard.py",
        "git -C /repo rev-parse HEAD # where are we",
        "gh api repos/o/r/pulls/4",
        "echo 'a # not a comment' && git -C /repo status",
    ],
)
def test_allowed(command):
    assert guard.bash_reason(command) is None


def test_write_tools_cannot_touch_production_config():
    for path in ("infra/main.tf", "vercel.json", ".github/workflows/deploy.yml", "Dockerfile.prod"):
        assert guard.reason_for({"tool_name": "Write", "tool_input": {"file_path": path}})
        assert guard.reason_for({"tool_name": "Read", "tool_input": {"file_path": path}}) is None


@pytest.mark.parametrize(
    "event",
    [
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": {"command": ["git", "push", "-f"]}},
        {"tool_name": "Read", "tool_input": {}},
        {"tool_name": "Edit", "tool_input": {"file_path": 7}},
        {"tool_name": "Bash"},
        {"tool_input": {"command": "ls"}},
    ],
)
def test_malformed_events_are_denied(event):
    assert _run(json.dumps(event)).returncode == 2


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


def test_merge_tools_are_denied_whatever_the_mcp_server_is_called():
    for tool in (
        "mcp__github__merge_pull_request",
        "mcp__github_enterprise__merge_pull_request",
        "mcp__gh__enable_pr_auto_merge",
    ):
        assert guard.reason_for({"tool_name": tool, "tool_input": {}})
    assert guard.reason_for({"tool_name": "mcp__github__pull_request_read", "tool_input": {}}) is None


def test_shell_nesting_past_the_inspection_limit_is_denied():
    import shlex

    command = "true; rm -r -f build"
    for _ in range(4):
        command = "bash -c " + shlex.quote(command)
    assert guard.bash_reason(command)
    command = "eval eval eval eval git push -f"
    assert guard.bash_reason(command)
