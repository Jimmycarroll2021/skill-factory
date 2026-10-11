"""Tests for `skillz promote` (promote.py): refusals, atomic replacement, rollback, isolation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidates, cli, pipeline, promote, release
from skill_factory.capture import capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
SRC = Path(promote.__file__).parent
NAME = "csv-data-cleaner"
CID = "c001"
PARAGRAPH = (
    "Summarise release notes from a changelog. Use it when the user asks for release notes. "
    "It should always list breaking changes first."
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("promote must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), project)
    return project


@pytest.fixture
def skill(root):
    return root / "skills" / NAME


@pytest.fixture
def released(skill):
    """The skill with an approved 0.1.0 already packaged."""
    release.package_skill(skill)
    release.approve(skill, "0.1.0", user="tester")
    return skill


def snapshot(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cdir(skill: Path, cid: str = CID) -> Path:
    return candidates.candidate_dir(skill, cid)


def make_candidate(
    skill: Path,
    cid: str,
    verdict: str,
    stale: bool = False,
    files: dict[str, str] | None = None,
) -> Path:
    """Build a candidate and its challenge.json by hand, to the plan's on-disk contract.

    The candidate is the stable skill plus a body note, one more trigger in its spec, and any
    extra ``files`` (relative to the skill). With ``stale`` the candidate is edited after the
    challenge digests were recorded.
    """
    directory = cdir(skill, cid)
    copy = candidates.candidate_skill_dir(skill, cid)
    for rel in candidates.tree_digests(skill):
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_bytes((skill / rel).read_bytes())
    with (copy / "SKILL.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\nNote added by the candidate.\n")
    for rel, text in (files or {}).items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_text(text, encoding="utf-8", newline="\n")
    spec = read_json(skill / ".factory" / "spec.json")
    spec["triggers"] = [*spec["triggers"], "Tidy my spreadsheet export"]
    (directory / "spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    challenge = {
        "schema": 1,
        "skill": skill.name,
        "candidate": cid,
        "created_at": "2026-01-01T00:00:00Z",
        "verdict": verdict,
        "sha256": {
            "stable": candidates.stable_digests(skill),
            "candidate": candidates.candidate_digests(skill, cid),
        },
    }
    (directory / "candidate.json").write_text('{"schema": 1}\n', encoding="utf-8")
    (directory / "challenge.json").write_text(
        json.dumps(challenge, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if stale:
        with (copy / "SKILL.md").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("Edited after the challenge.\n")
    return directory


def cli_promote(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    promote.add_parser(sub)
    args = parser.parse_args(["promote", *argv, "--out", str(root)])
    return promote.run(args)


@pytest.mark.parametrize("verdict", ["candidate-worse", "candidate-unsafe"])
def test_unacceptable_verdict_is_refused_and_changes_nothing(root, released, verdict, capsys):
    make_candidate(released, CID, verdict)
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert "not one of" in capsys.readouterr().err
    assert snapshot(released) == before
    assert not (cdir(released) / "promoted.json").exists()


def test_missing_challenge_json_is_refused(root, released, capsys):
    directory = make_candidate(released, CID, "candidate-better")
    (directory / "challenge.json").unlink()
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert "challenge" in capsys.readouterr().err
    assert snapshot(released) == before


def test_unreadable_challenge_json_is_refused(root, released):
    directory = make_candidate(released, CID, "candidate-better")
    (directory / "challenge.json").write_text("{not json", encoding="utf-8")
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert snapshot(released) == before


def test_candidate_edited_after_challenge_is_stale(root, released, capsys):
    make_candidate(released, CID, "candidate-better", stale=True)
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert "stale" in capsys.readouterr().err
    assert snapshot(released) == before


def test_candidate_spec_edited_after_challenge_is_stale(root, released):
    directory = make_candidate(released, CID, "candidate-better")
    with (directory / "spec.json").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert snapshot(released) == before


@pytest.mark.parametrize("target", ["SKILL.md", ".factory/spec.json"])
def test_stable_edited_after_challenge_is_stale(root, released, target, capsys):
    make_candidate(released, CID, "candidate-better")
    with (released / target).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n")
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert "stale" in capsys.readouterr().err
    assert snapshot(released) == before


def test_challenge_without_digests_is_refused(root, released):
    directory = make_candidate(released, CID, "candidate-better")
    data = read_json(directory / "challenge.json")
    del data["sha256"]
    (directory / "challenge.json").write_text(json.dumps(data), encoding="utf-8")
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert snapshot(released) == before


@pytest.mark.parametrize("verdict", ["candidate-better", "no-difference"])
def test_acceptable_verdict_promotes_and_packages_a_new_version(root, released, verdict, capsys):
    directory = make_candidate(released, CID, verdict)
    old_version = snapshot(released / ".factory" / "versions" / "0.1.0")
    candidate_skill = (candidates.candidate_skill_dir(released, CID) / "SKILL.md").read_bytes()
    candidate_spec = (directory / "spec.json").read_bytes()
    stable_before = candidates.stable_digests(released)

    assert cli_promote(root, NAME, CID) == 0
    assert "0.1.1" in capsys.readouterr().out

    assert (released / "SKILL.md").read_bytes() == candidate_skill
    assert (released / ".factory" / "spec.json").read_bytes() == candidate_spec
    versions = {v.version: v for v in release.load_versions(released)}
    assert sorted(versions) == ["0.1.0", "0.1.1"]
    assert versions["0.1.0"].status == release.APPROVED
    assert versions["0.1.1"].status == release.AWAITING
    assert not (versions["0.1.1"].path / release.APPROVAL).exists()
    assert release.verify_version(versions["0.1.1"].path) == []
    assert release.verify_version(versions["0.1.0"].path) == []
    assert snapshot(versions["0.1.0"].path) == old_version

    record = read_json(directory / "promoted.json")
    assert record["schema"] == 1
    assert record["skill"] == NAME
    assert record["candidate"] == CID
    assert record["verdict"] == verdict
    assert record["version"] == "0.1.1"
    assert record["previous_versions"] == ["0.1.0"]
    assert record["replaced"] == [".factory/spec.json", "SKILL.md"]
    assert record["removed"] == []
    assert record["from_sha256"] == stable_before
    assert record["to_sha256"] == candidates.stable_digests(released)
    assert record["promoted_at"].endswith("Z")
    assert not [p for p in directory.iterdir() if p.name.startswith(".")]


def test_first_version_is_0_1_0_and_awaits_approval(root, skill, capsys):
    make_candidate(skill, CID, "no-difference")
    assert cli_promote(root, NAME, CID) == 0
    assert "0.1.0" in capsys.readouterr().out
    versions = release.load_versions(skill)
    assert [(v.version, v.status) for v in versions] == [("0.1.0", release.AWAITING)]
    assert not (versions[0].path / release.APPROVAL).exists()
    assert read_json(cdir(skill) / "promoted.json")["previous_versions"] == []


def test_files_are_created_and_removed_to_match_the_candidate(root, skill):
    (skill / "references").mkdir()
    (skill / "references" / "old.md").write_text("old\n", encoding="utf-8")
    make_candidate(skill, CID, "candidate-better", files={"scripts/new.sh": "echo new\n"})
    (candidates.candidate_skill_dir(skill, CID) / "references" / "old.md").unlink()
    # The recorded digests must describe the candidate as it now is.
    directory = cdir(skill)
    data = read_json(directory / "challenge.json")
    data["sha256"]["candidate"] = candidates.candidate_digests(skill, CID)
    (directory / "challenge.json").write_text(json.dumps(data), encoding="utf-8")

    assert cli_promote(root, NAME, CID) == 0
    assert (skill / "scripts" / "new.sh").read_text(encoding="utf-8") == "echo new\n"
    assert not (skill / "references" / "old.md").exists()
    record = read_json(directory / "promoted.json")
    assert "scripts/new.sh" in record["replaced"]
    assert record["removed"] == ["references/old.md"]
    assert candidates.tree_digests(skill) == candidates.tree_digests(
        candidates.candidate_skill_dir(skill, CID)
    )


def test_a_candidate_is_promoted_only_once(root, released, capsys):
    make_candidate(released, CID, "no-difference")
    assert cli_promote(root, NAME, CID) == 0
    capsys.readouterr()
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 1
    assert "already promoted" in capsys.readouterr().err
    assert snapshot(released) == before


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: release.RegressionFailed("2 cases regressed", {"newly_failing": []}),
        lambda: release.ValidationFailed("validation failed"),
        lambda: release.IntegrityError("version 0.1.0 does not match its manifest"),
    ],
)
def test_package_failure_restores_the_stable_files(root, released, monkeypatch, make_error, capsys):
    (released / "references").mkdir()
    (released / "references" / "old.md").write_text("old\n", encoding="utf-8")
    make_candidate(released, CID, "candidate-better", files={"scripts/new.sh": "echo new\n"})
    candidate_skill = candidates.candidate_skill_dir(released, CID)
    (candidate_skill / "references" / "old.md").unlink()
    directory = cdir(released)
    data = read_json(directory / "challenge.json")
    data["sha256"]["candidate"] = candidates.candidate_digests(released, CID)
    (directory / "challenge.json").write_text(json.dumps(data), encoding="utf-8")
    before = snapshot(released)
    seen = {}

    def failing(skill_dir, *args, **kwargs):
        # By now the candidate's files are in place, which is what a real failure would see.
        seen["skill"] = (Path(skill_dir) / "SKILL.md").read_bytes()
        raise make_error()

    monkeypatch.setattr(release, "package_skill", failing)
    assert cli_promote(root, NAME, CID) == 1
    assert seen["skill"] == (candidate_skill / "SKILL.md").read_bytes()
    assert capsys.readouterr().err.startswith("skillz: error:")
    assert snapshot(released) == before
    assert not (released / "scripts").exists()
    assert not (directory / "promoted.json").exists()
    assert [v.version for v in release.load_versions(released)] == ["0.1.0"]


def test_real_regression_rolls_back(root, released, capsys):
    """A candidate whose description loses the words an approved case needs is not promoted."""
    directory = make_candidate(released, CID, "candidate-better")
    copy = candidates.candidate_skill_dir(released, CID) / "SKILL.md"
    text = copy.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    copy.write_text(
        "".join(
            'description: "Plants and weather."\n' if line.startswith("description:") else line
            for line in lines
        ),
        encoding="utf-8",
        newline="\n",
    )
    data = read_json(directory / "challenge.json")
    data["sha256"]["candidate"] = candidates.candidate_digests(released, CID)
    (directory / "challenge.json").write_text(json.dumps(data), encoding="utf-8")
    stable = {rel: (released / rel).read_bytes() for rel in candidates.stable_digests(released)}

    assert cli_promote(root, NAME, CID) == 1
    for rel, content in stable.items():
        assert (released / rel).read_bytes() == content
    assert [v.version for v in release.load_versions(released)] == ["0.1.0"]
    assert not (directory / "promoted.json").exists()
    assert capsys.readouterr().err.count("now fail") == 1


@pytest.mark.parametrize("candidate_id", ["../x", "c1", "C001", "c0001x", ""])
def test_bad_candidate_id_exits_2(root, released, candidate_id, capsys):
    assert cli_promote(root, NAME, candidate_id) == 2
    assert "invalid candidate id" in capsys.readouterr().err


def test_invalid_name_and_missing_skill_exit_2(root, released):
    assert cli_promote(root, "Bad Name", CID) == 2
    assert cli_promote(root, "no-such-skill", CID) == 2


def test_missing_candidate_directory_exits_2(root, released, capsys):
    assert cli_promote(root, NAME, "c007") == 2
    assert "not found" in capsys.readouterr().err


def test_candidate_without_files_exits_2(root, released):
    cdir(released).mkdir(parents=True)
    (cdir(released) / "challenge.json").write_text(
        '{"sha256": {"stable": {}, "candidate": {}}}', encoding="utf-8"
    )
    before = snapshot(released)
    assert cli_promote(root, NAME, CID) == 2
    assert snapshot(released) == before


def test_symlinked_target_directory_is_refused_with_exit_2(root, skill, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, skill / "scripts", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    make_candidate(skill, CID, "candidate-better", files={"scripts/new.sh": "echo new\n"})
    assert cli_promote(root, NAME, CID) == 2
    assert list(outside.iterdir()) == []
    assert not (skill / ".factory" / "versions").exists()


def test_pipeline_and_run_never_mention_promote():
    source = (SRC / "pipeline.py").read_text(encoding="utf-8")
    assert "promote" not in source.lower()
    assert cli.HANDLERS["run"] is pipeline.run


def test_pipeline_does_not_call_promote(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the pipeline must never promote")

    monkeypatch.setattr(promote, "promote_candidate", boom)
    project = tmp_path / "pipeline-project"
    project.mkdir()
    result = pipeline.run_pipeline(PARAGRAPH, project)
    assert result["status"] == "awaiting-approval"
