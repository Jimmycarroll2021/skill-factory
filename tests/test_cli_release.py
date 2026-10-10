"""Tests for the release commands wired into `skillz`: package, approve, install, rollback, list."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import skill_factory
from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
SRC = Path(skill_factory.__file__).parent
NAME = "csv-data-cleaner"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("the release commands must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.mark.parametrize("command", ["package", "approve", "install", "rollback", "list"])
def test_subcommand_help_exits_zero(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([command, "--help"])
    assert excinfo.value.code == 0
    assert "--out" in capsys.readouterr().out


def _registry(project: Path) -> dict:
    return json.loads((project / "skills" / "registry.json").read_text(encoding="utf-8"))



def test_package_approve_install_rollback_list_flow(tmp_path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    out = str(project)
    skill = project / "skills" / NAME

    assert main(["new", str(FIXTURES / "eval" / "well-described.json"), "--out", out]) == 0
    assert main(["package", NAME, "--out", out]) == 0
    assert (skill / ".factory" / "versions" / "0.1.0" / "manifest.json").is_file()

    # Not approved yet: refused, and nothing is created.
    project_install = ["install", NAME, "--platform", "claude", "--scope", "project", "--out", out]
    assert main(project_install) == 1
    assert not (project / ".claude").exists()

    assert main(["approve", NAME, "0.1.0", "--out", out]) == 0
    assert main(project_install) == 0
    assert (project / ".claude" / "skills" / NAME / "SKILL.md").is_file()
    assert _registry(project)["current"][f"{NAME}:claude:project"]["version"] == "0.1.0"

    codex = ["install", NAME, "--platform", "codex", "--scope", "user", "--out", out]
    assert main(codex) == 0
    assert (home / ".agents" / "skills" / NAME / "agents" / "openai.yaml").is_file()

    assert main(["package", NAME, "--out", out]) == 0
    assert (skill / ".factory" / "versions" / "0.1.1").is_dir()
    assert main(["approve", NAME, "0.1.1", "--out", out]) == 0
    assert main(project_install) == 0
    assert _registry(project)["current"][f"{NAME}:claude:project"]["version"] == "0.1.1"

    rollback = ["rollback", NAME, "--platform", "claude", "--scope", "project", "--out", out]
    assert main(rollback) == 0
    assert _registry(project)["current"][f"{NAME}:claude:project"]["version"] == "0.1.0"
    assert (skill / ".factory" / "versions" / "0.1.1").is_dir()
    assert (project / ".claude" / "skills" / NAME / "SKILL.md").is_file()

    capsys.readouterr()
    assert main(["list", "--out", out]) == 0
    text = capsys.readouterr().out
    assert NAME in text
    assert "0.1.0" in text and "0.1.1" in text
    assert "approved" in text

    assert main(["list", "--json", "--out", out]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [row["version"] for row in data["skills"][NAME]] == ["0.1.0", "0.1.1"]
    assert {row["status"] for row in data["skills"][NAME]} == {"approved"}
    assert {rec["name"] for rec in data["installed"]} == {NAME}


def test_unknown_skill_exits_two(tmp_path, capsys):
    assert main(["package", "does-not-exist", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_source_never_mentions_git_push():
    offenders = [
        str(path)
        for path in SRC.rglob("*")
        if path.is_file() and "git push" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
