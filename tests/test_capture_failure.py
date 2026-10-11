"""Tests for `skillz capture-failure` (captured.py) and the candidate layout helpers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidates, captured, evaluate, release
from skill_factory.capture import SpecError, capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "csv-data-cleaner"
MISSED = "Help me tidy up my spreadsheet export before the quarterly review"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("capture-failure must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), project)
    return project


def skill(root: Path) -> Path:
    return root / "skills" / NAME


def cli(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    captured.add_parser(sub)
    args = parser.parse_args(["capture-failure", *argv, "--out", str(root)])
    return captured.run(args)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_append_assigns_stable_ids_and_keeps_earlier_records(root):
    first = captured.append_captured(skill(root), MISSED, "positive", note="missed it")
    before = captured.captured_path(skill(root)).read_text(encoding="utf-8")
    second = captured.append_captured(
        skill(root), "Write a haiku about autumn", "behaviour", expected=["autumn", "haiku"]
    )
    third = captured.append_captured(skill(root), "What is the weather", "negative")
    assert [first["id"], second["id"], third["id"]] == ["cap-001", "cap-002", "cap-003"]
    path = skill(root) / ".factory" / "cases" / "captured.json"
    assert path == captured.captured_path(skill(root))
    data = read_json(path)
    assert data["schema"] == 1
    records = data["cases"]
    assert [r["kind"] for r in records] == ["positive", "behaviour", "negative"]
    assert records[0]["note"] == "missed it" and records[0]["expected"] == []
    assert records[1]["expected"] == ["autumn", "haiku"] and records[1]["note"] is None
    assert all(r["source"] == "captured" for r in records)
    assert all(r["captured_at"].endswith("Z") and len(r["captured_at"]) == 20 for r in records)
    assert records[0] == json.loads(before)["cases"][0]
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not [p for p in path.parent.iterdir() if p.name != "captured.json"]


def test_load_captured_in_order_and_empty_when_absent(root):
    assert captured.load_captured(skill(root)) == []
    captured.append_captured(skill(root), MISSED, "positive")
    captured.append_captured(skill(root), "Say hello", "behaviour", expected=["hello"])
    cases = captured.load_captured(skill(root))
    assert [(c.id, c.kind, c.prompt, c.source, c.expected) for c in cases] == [
        ("cap-001", "positive", MISSED, "captured", []),
        ("cap-002", "behaviour", "Say hello", "captured", ["hello"]),
    ]


@pytest.mark.parametrize("content", ["{not json", "[]", '{"cases": 3}', '{"cases": [3]}'])
def test_malformed_captured_json_raises(root, content):
    path = captured.captured_path(skill(root))
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SpecError):
        captured.load_captured(skill(root))
    with pytest.raises(SpecError):
        captured.append_captured(skill(root), MISSED, "positive")
    assert path.read_text(encoding="utf-8") == content


def test_append_rejects_bad_input(root):
    for prompt, kind, expected in [
        ("  ", "positive", None),
        (MISSED, "sideways", None),
        (MISSED, "behaviour", None),
        (MISSED, "behaviour", [" "]),
    ]:
        with pytest.raises(SpecError):
            captured.append_captured(skill(root), prompt, kind, expected)
    assert not captured.captured_path(skill(root)).exists()


def test_append_refuses_a_symlinked_factory(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    factory = skill(root) / ".factory"
    for child in sorted(factory.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    factory.rmdir()
    try:
        factory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(SpecError):
        captured.append_captured(skill(root), MISSED, "positive")
    assert list(outside.iterdir()) == []


def test_evaluate_includes_captured_cases(root):
    plain = evaluate.evaluate_skill(skill(root))
    assert all(not r["id"].startswith("cap-") for r in plain["results"])
    captured.append_captured(skill(root), MISSED, "positive")
    report = evaluate.evaluate_skill(skill(root))
    results = {r["id"]: r for r in report["results"]}
    assert results["cap-001"]["kind"] == "positive"
    assert results["cap-001"]["status"] == "fail" and results["cap-001"]["matched"] is False
    assert len(report["results"]) == len(plain["results"]) + 1
    factory = skill(root) / ".factory"
    stored = {c["id"]: c for c in read_json(factory / "cases.json")["cases"]}
    assert stored["cap-001"]["prompt"] == MISSED and stored["cap-001"]["source"] == "captured"
    assert read_json(factory / "eval.json")["results"] == report["results"]
    assert "cap-001" in (factory / "eval.md").read_text(encoding="utf-8")
    assert any("cap-001" in w["cases"] for w in report["weak_areas"])


def test_package_stores_captured_case_for_regression(root):
    captured.append_captured(skill(root), MISSED, "positive")
    release.package_skill(skill(root))
    version = skill(root) / ".factory" / "versions" / "0.1.0"
    stored = read_json(version / "reports" / "cases.json")["cases"]
    assert "cap-001" in [c["id"] for c in stored]
    assert "cap-001" in [r["id"] for r in read_json(version / "reports" / "eval.json")["results"]]


def test_cli_appends_and_prints_the_case_id(root, capsys):
    assert cli(root, NAME, "--prompt", MISSED, "--should-trigger", "--note", "why") == 0
    out = capsys.readouterr().out
    assert "cap-001" in out and "captured.json" in out
    assert cli(root, NAME, "--prompt", "Weather?", "--should-not-trigger") == 0
    assert cli(root, NAME, "--prompt", "Say hi", "--expect", "hi", "hello") == 0
    records = read_json(captured.captured_path(skill(root)))["cases"]
    assert [(r["id"], r["kind"], r["expected"]) for r in records] == [
        ("cap-001", "positive", []),
        ("cap-002", "negative", []),
        ("cap-003", "behaviour", ["hi", "hello"]),
    ]
    assert records[0]["note"] == "why"


def test_cli_input_errors_exit_two(root, capsys):
    assert cli(root, "Bad_Name", "--prompt", MISSED, "--should-trigger") == 2
    assert cli(root, "missing-skill", "--prompt", MISSED, "--should-trigger") == 2
    assert cli(root, NAME, "--prompt", "   ", "--should-trigger") == 2
    err = capsys.readouterr().err
    assert err.count("skillz: error:") == 3
    assert not captured.captured_path(skill(root)).exists()


def test_cli_kind_group_is_required_and_exclusive(root):
    with pytest.raises(SystemExit) as excinfo:
        cli(root, NAME, "--prompt", MISSED)
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli(root, NAME, "--prompt", MISSED, "--should-trigger", "--should-not-trigger")
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli(root, NAME, "--should-trigger")
    assert excinfo.value.code == 2


def test_candidate_layout_and_ids(tmp_path):
    skill_dir = tmp_path / "skills" / NAME
    assert candidates.candidates_root(skill_dir) == skill_dir / ".factory" / "candidates"
    assert candidates.candidate_dir(skill_dir, "c001") == skill_dir / ".factory/candidates/c001"
    assert candidates.candidate_skill_dir(skill_dir, "c012") == (
        skill_dir / ".factory/candidates/c012" / NAME
    )
    assert candidates.next_candidate_id(skill_dir) == "c001"
    (skill_dir / ".factory" / "candidates" / "c001").mkdir(parents=True)
    (skill_dir / ".factory" / "candidates" / "c009").mkdir()
    (skill_dir / ".factory" / "candidates" / "notes").mkdir()
    assert candidates.next_candidate_id(skill_dir) == "c010"


@pytest.mark.parametrize("bad", ["../x", "c1", "C001", "c001/..", "c0001x", "", "c001\n", "..", "c"])
def test_bad_candidate_ids_are_rejected(tmp_path, bad):
    with pytest.raises(candidates.CandidateError):
        candidates.candidate_dir(tmp_path, bad)
    with pytest.raises(candidates.CandidateError):
        candidates.candidate_skill_dir(tmp_path, bad)


def test_tree_digests_are_deterministic_and_skip_factory(root):
    skill_dir = skill(root)
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
    first = candidates.tree_digests(skill_dir)
    assert first == candidates.tree_digests(skill_dir)
    assert list(first) == sorted(first) and "SKILL.md" in first
    assert "references/notes.md" in first
    assert not any(key.startswith(".factory") for key in first)
    assert all(len(v) == 64 for v in first.values())
    (skill_dir / ".factory" / "extra.txt").write_text("x", encoding="utf-8")
    assert candidates.tree_digests(skill_dir) == first
    (skill_dir / "references" / "notes.md").write_text("changed\n", encoding="utf-8")
    assert candidates.tree_digests(skill_dir) != first


def test_stable_and_candidate_digests_add_the_spec(root):
    skill_dir = skill(root)
    stable = candidates.stable_digests(skill_dir)
    assert ".factory/spec.json" in stable
    assert stable["SKILL.md"] == candidates.tree_digests(skill_dir)["SKILL.md"]
    spec = (skill_dir / ".factory" / "spec.json").read_bytes()
    cdir = candidates.candidate_dir(skill_dir, "c001")
    (cdir / NAME).mkdir(parents=True)
    (cdir / NAME / "SKILL.md").write_bytes((skill_dir / "SKILL.md").read_bytes())
    with pytest.raises(candidates.CandidateError):
        candidates.candidate_digests(skill_dir, "c001")
    with pytest.raises(candidates.CandidateError):
        candidates.load_candidate_spec(skill_dir, "c001")
    (cdir / "spec.json").write_bytes(spec)
    digests = candidates.candidate_digests(skill_dir, "c001")
    assert digests == {k.removeprefix(".factory/"): v for k, v in stable.items()}
    assert candidates.load_candidate_spec(skill_dir, "c001").name == NAME


def test_verdict_ordering_returns_the_worst():
    assert candidates.VERDICTS == (
        "candidate-unsafe",
        "candidate-worse",
        "candidate-better",
        "no-difference",
    )
    assert candidates.ACCEPTABLE == ("candidate-better", "no-difference")
    worst = candidates.worst_verdict
    assert worst([]) == "no-difference"
    assert worst(["no-difference", "candidate-better"]) == "candidate-better"
    assert worst(["candidate-better", "candidate-worse", "no-difference"]) == "candidate-worse"
    assert worst(iter(["candidate-worse", "candidate-unsafe"])) == "candidate-unsafe"
    with pytest.raises(candidates.CandidateError):
        worst(["candidate-great"])
