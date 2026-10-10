"""Tests for the `skillz` command line: validate and render wired in beside new."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "release-notes"


@pytest.mark.parametrize("command", ["validate", "render"])
def test_subcommand_help_exits_zero(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([command, "--help"])
    assert excinfo.value.code == 0
    assert "--out" in capsys.readouterr().out


def test_new_validate_render_flow(tmp_path):
    out = str(tmp_path)
    assert main(["new", str(FIXTURES / "paragraph.txt"), "--name", NAME, "--out", out]) == 0

    assert main(["validate", NAME, "--out", out]) == 0
    factory = tmp_path / "skills" / NAME / ".factory"
    assert json.loads((factory / "validate.json").read_text(encoding="utf-8"))["ok"] is True

    assert main(["render", NAME, "--out", out]) == 0
    assert (factory / "dist" / "claude" / "SKILL.md").is_file()
    assert (factory / "dist" / "codex" / "agents" / "openai.yaml").is_file()


def test_validate_bad_fixture_exits_one(tmp_path):
    shutil.copytree(
        FIXTURES / "skills" / "destructive-shell", tmp_path / "skills" / "destructive-shell"
    )
    assert main(["validate", "destructive-shell", "--out", str(tmp_path)]) == 1


@pytest.mark.parametrize("command", ["validate", "render"])
def test_missing_skill_exits_two(command, tmp_path, capsys):
    assert main([command, "does-not-exist", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err
