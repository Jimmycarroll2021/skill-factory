"""The challenger loop end to end through `skillz`: capture-failure, improve, challenge, promote."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidates, release
from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "csv-data-cleaner"
MISSED = "Help me tidy up my spreadsheet export before the quarterly review"
LOOP_COMMANDS = ["capture-failure", "improve", "challenge", "promote"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("the loop commands must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _packaged_and_approved(tmp_path: Path) -> tuple[str, Path]:
    project = tmp_path / "project"
    project.mkdir()
    out = str(project)
    assert main(["new", str(FIXTURES / "eval" / "well-described.json"), "--out", out]) == 0
    assert main(["package", NAME, "--out", out]) == 0
    assert main(["approve", NAME, "0.1.0", "--out", out]) == 0
    return out, project / "skills" / NAME


@pytest.mark.parametrize("command", LOOP_COMMANDS)
def test_loop_subcommand_help_exits_zero(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([command, "--help"])
    assert excinfo.value.code == 0
    assert "--out" in capsys.readouterr().out


def test_the_whole_loop_through_main_ends_awaiting_approval(tmp_path):
    out, skill = _packaged_and_approved(tmp_path)
    v1 = skill / ".factory" / "versions" / "0.1.0"
    before = _digests(v1)

    capture = ["capture-failure", NAME, "--prompt", MISSED, "--should-trigger", "--out", out]
    assert main(capture) == 0
    assert main(["eval", NAME, "--out", out]) == 0
    eval_json = json.loads((skill / ".factory" / "eval.json").read_text(encoding="utf-8"))
    assert "cap-001" in json.dumps(eval_json)

    assert main(["improve", NAME, "--out", out]) == 0
    assert main(["challenge", NAME, "c001", "--out", out]) == 0
    verdict = json.loads(
        (candidates.candidate_dir(skill, "c001") / "challenge.json").read_text(encoding="utf-8")
    )["verdict"]
    assert verdict in candidates.ACCEPTABLE
    assert main(["promote", NAME, "c001", "--out", out]) == 0

    versions = {v.version: v for v in release.load_versions(skill)}
    assert versions["0.1.1"].status == "awaiting-approval"
    assert versions["0.1.0"].status == "approved"
    assert _digests(v1) == before
    assert release.verify_version(v1) == []

    # A second promotion of the same candidate is refused and changes nothing.
    assert main(["promote", NAME, "c001", "--out", out]) == 1


def test_promoting_a_worse_candidate_through_the_cli_exits_one(tmp_path):
    out, skill = _packaged_and_approved(tmp_path)
    assert main(["capture-failure", NAME, "--prompt", MISSED, "--should-trigger", "--out", out]) == 0
    assert main(["improve", NAME, "--out", out]) == 0

    # Gut the candidate description so a trigger case that passes on stable now fails.
    cdir = candidates.candidate_dir(skill, "c001")
    md = candidates.candidate_skill_dir(skill, "c001") / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    lines = ["description: Helps with things." if x.startswith("description:") else x for x in lines]
    md.write_text("\n".join(lines), encoding="utf-8")
    spec_path = cdir / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["description"] = "Helps with things."
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    assert main(["challenge", NAME, "c001", "--out", out]) == 0
    report = json.loads((cdir / "challenge.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "candidate-worse"

    before = _digests(skill)
    assert main(["promote", NAME, "c001", "--out", out]) == 1
    assert _digests(skill) == before
    assert not (cdir / "promoted.json").exists()
