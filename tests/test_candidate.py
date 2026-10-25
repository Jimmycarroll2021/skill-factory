"""Tests for `skillz candidate` (candidate.py): a candidate starts as a copy of the stable skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidate, candidates, release
from skill_factory.candidates import CandidateError
from skill_factory.capture import SkillSpec, SpecError, capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
RECORD_KEYS = {"schema", "id", "skill", "created_at", "base_version", "base_sha256", "source"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("candidate must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


def make(tmp_path: Path, fixture: str) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return generate(capture_spec_file(FIXTURES / "eval" / fixture), project)


@pytest.fixture
def vague(tmp_path):
    return make(tmp_path, "vague.json")


@pytest.fixture
def well(tmp_path):
    return make(tmp_path, "well-described.json")


def snapshot(skill_dir: Path) -> dict[str, str]:
    """sha256 of every file outside .factory/candidates/."""
    found = {}
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir).as_posix()
        if path.is_file() and not rel.startswith(".factory/candidates/"):
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cli(root: Path | None, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    candidate.add_parser(sub)
    out = [] if root is None else ["--out", str(root)]
    args = parser.parse_args(["candidate", *argv, *out])
    return candidate.run(args)


def test_every_canonical_file_is_copied_byte_for_byte_and_stable_files_stay(tmp_path):
    spec = SkillSpec(
        name="helper",
        description="Helps with things.",
        triggers=["Summarise the quarterly invoices"],
        resources=["scripts", "references"],
    )
    skill = generate(spec, tmp_path)
    (skill / "scripts" / "tool.sh").write_bytes(b"#!/bin/sh\necho hi\r\n")
    (skill / "references" / "notes.md").write_bytes("café\n".encode())
    (skill / "SKILL.md").write_bytes(
        (skill / "SKILL.md").read_bytes() + b"\r\nextra   trailing  \r\n"
    )
    before = snapshot(skill)
    record = candidate.create_candidate(skill)
    assert snapshot(skill) == before
    cskill = candidates.candidate_skill_dir(skill, record["id"])
    assert cskill == candidates.candidate_dir(skill, "c001") / "helper"
    assert candidates.tree_digests(cskill) == candidates.tree_digests(skill)
    assert set(candidates.tree_digests(cskill)) == {
        "SKILL.md",
        "scripts/tool.sh",
        "references/notes.md",
    }
    assert (cskill / "SKILL.md").read_bytes() == (skill / "SKILL.md").read_bytes()


def test_spec_json_is_the_stable_spec_when_it_exists(vague):
    candidate.create_candidate(vague)
    cdir = candidates.candidate_dir(vague, "c001")
    assert (cdir / "spec.json").read_bytes() == (vague / ".factory" / "spec.json").read_bytes()
    spec = candidates.load_candidate_spec(vague, "c001")
    assert spec.name == "general-helper" and spec.triggers == [
        "Remove duplicate rows from a spreadsheet",
        "Summarise the quarterly invoices",
    ]


def test_spec_json_falls_back_to_the_frontmatter_without_a_stable_spec(vague):
    (vague / ".factory" / "spec.json").unlink()
    candidate.create_candidate(vague)
    spec = candidates.load_candidate_spec(vague, "c001")
    assert (spec.name, spec.description) == ("general-helper", "Helps with things.")
    assert spec.triggers == []


def test_candidate_json_records_the_base_and_a_manual_source(well):
    record = candidate.create_candidate(well)
    cdir = candidates.candidate_dir(well, "c001")
    assert read_json(cdir / "candidate.json") == record
    assert set(record) == RECORD_KEYS
    assert record["schema"] == 1 and record["id"] == "c001"
    assert record["skill"] == "csv-data-cleaner"
    assert record["created_at"].endswith("Z")
    assert record["base_version"] is None
    assert record["base_sha256"] == candidates.stable_digests(well)
    assert ".factory/spec.json" in record["base_sha256"]
    assert record["source"] == "manual"
    assert (cdir / "candidate.json").read_bytes().endswith(b"}\n")
    assert b"\r" not in (cdir / "candidate.json").read_bytes()


def test_base_version_is_the_latest_packaged_version(well):
    release.package_skill(well)
    assert candidate.create_candidate(well)["base_version"] == "0.1.0"


def test_ids_increment_and_the_same_inputs_give_identical_bytes(vague):
    first = candidate.create_candidate(vague)
    second = candidate.create_candidate(vague)
    assert (first["id"], second["id"]) == ("c001", "c002")
    assert candidates.next_candidate_id(vague) == "c003"
    for cid in ("c001", "c002"):
        cdir = candidates.candidate_dir(vague, cid)
        assert cdir.is_dir() and (cdir / "spec.json").is_file()
    assert (candidates.candidate_dir(vague, "c001") / "spec.json").read_bytes() == (
        candidates.candidate_dir(vague, "c002") / "spec.json"
    ).read_bytes()
    assert (candidates.candidate_skill_dir(vague, "c001") / "SKILL.md").read_bytes() == (
        candidates.candidate_skill_dir(vague, "c002") / "SKILL.md"
    ).read_bytes()


def test_a_symlinked_factory_or_candidates_dir_is_refused(vague, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    candidates.candidates_root(vague).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(CandidateError):
        candidate.create_candidate(vague)
    assert list(elsewhere.iterdir()) == []
    assert cli(tmp_path / "project", "general-helper") == 1

    (tmp_path / "other").mkdir()
    other = make(tmp_path / "other", "well-described.json")
    factory = other / ".factory"
    moved = tmp_path / "moved-factory"
    factory.rename(moved)
    factory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(CandidateError):
        candidate.create_candidate(other)
    assert not (moved / "candidates").exists()


def test_a_missing_skill_md_is_a_spec_error_and_nothing_is_written(tmp_path):
    skill = tmp_path / "skills" / "empty"
    skill.mkdir(parents=True)
    with pytest.raises(SpecError):
        candidate.create_candidate(skill)
    with pytest.raises(SpecError):
        candidate.create_candidate(tmp_path / "skills" / "nope")
    assert not (skill / ".factory").exists()


def test_cli_prints_the_skill_md_to_edit_and_the_next_command(vague, tmp_path, capsys):
    project = tmp_path / "project"
    assert cli(project, "general-helper") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        str(project / "skills" / "general-helper" / ".factory" / "candidates" / "c001"
            / "general-helper" / "SKILL.md"),
        "edit it, then: skillz challenge general-helper c001",
    ]
    assert Path(lines[0]).is_file()
    assert cli(project, "general-helper") == 0
    assert "skillz challenge general-helper c002" in capsys.readouterr().out


def test_cli_without_out_prints_a_path_relative_to_the_cwd(vague, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path / "project")
    assert cli(None, "general-helper") == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == str(
        Path("skills") / "general-helper" / ".factory" / "candidates" / "c001" / "general-helper"
        / "SKILL.md"
    )
    assert Path(first).is_file()


def test_cli_exit_2_for_invalid_name_and_missing_skill(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    assert cli(project, "Bad_Name") == 2
    assert cli(project, "nope") == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert not (project / "skills").exists()
