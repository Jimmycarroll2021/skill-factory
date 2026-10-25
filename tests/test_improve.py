"""Tests for `skillz improve` (improve.py): candidates are built from proposals and failures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidates, captured, evalcases, improve, render
from skill_factory.capture import MAX_DESCRIPTION, SkillSpec, capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
MISSED = "Help me tidy up my spreadsheet export before the quarterly review"
OFF_TOPIC = "Design a logo for my bakery"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("improve must not spawn a process")

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


def cli(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    improve.add_parser(sub)
    args = parser.parse_args(["improve", *argv, "--out", str(root)])
    return improve.run(args)


def body_of(skill_md: Path) -> bytes:
    return improve._body_bytes(skill_md)


def test_vague_skill_gets_vocabulary_and_stable_files_stay_identical(vague):
    captured.append_captured(vague, MISSED, "positive")
    before = snapshot(vague)
    record = improve.improve_skill(vague)
    assert snapshot(vague) == before
    assert record["id"] == "c001"
    cdir = candidates.candidate_dir(vague, "c001")
    cskill = candidates.candidate_skill_dir(vague, "c001")
    assert cskill == cdir / "general-helper"
    assert (cskill / "SKILL.md").is_file()
    assert (cdir / "spec.json").is_file() and (cdir / "candidate.json").is_file()
    assert read_json(cdir / "candidate.json") == record

    meta = render._frontmatter(cskill / "SKILL.md")
    assert meta["name"] == "general-helper"
    assert meta["description"].startswith("Helps with things.")
    # a whole trigger situation, not a list of loose keywords
    assert "Also use for requests like " in meta["description"]
    assert f'"{MISSED}"' in meta["description"]
    assert "mentions:" not in meta["description"]
    assert len(meta["description"]) <= MAX_DESCRIPTION
    spec = candidates.load_candidate_spec(vague, "c001")
    assert spec.description == meta["description"]
    assert (
        evalcases.match_score(MISSED, spec.name, spec.description)
        >= evalcases.MATCH_THRESHOLD
    )
    assert body_of(cskill / "SKILL.md") == body_of(vague / "SKILL.md")


def test_candidate_json_records_applied_proposals_and_digests(vague):
    captured.append_captured(vague, MISSED, "positive")
    captured.append_captured(vague, OFF_TOPIC, "negative")
    captured.append_captured(vague, "Write a haiku", "behaviour", expected=["haiku"])
    record = improve.improve_skill(vague)
    assert record["schema"] == 1 and record["skill"] == "general-helper"
    assert record["created_at"].endswith("Z")
    assert record["base_version"] is None
    assert record["base_sha256"] == candidates.stable_digests(vague)
    areas = [a["area"] for a in record["applied"]]
    assert "trigger-vocabulary" in areas
    assert "captured:cap-001" in areas and "captured:cap-002" in areas
    assert record["captured"] == ["cap-001", "cap-002"]
    assert any(s["area"] == "captured:cap-003" for s in record["skipped"])
    for entry in record["applied"]:
        assert set(entry) == {"area", "action", "detail"}


def test_captured_prompts_land_in_the_candidate_spec(vague):
    captured.append_captured(vague, MISSED, "positive")
    captured.append_captured(vague, OFF_TOPIC, "negative")
    improve.improve_skill(vague)
    spec = candidates.load_candidate_spec(vague, "c001")
    assert MISSED in spec.triggers and spec.triggers[:2] == [
        "Remove duplicate rows from a spreadsheet",
        "Summarise the quarterly invoices",
    ]
    assert OFF_TOPIC in spec.non_triggers
    assert spec.non_triggers[0] == "Write a SQL query to join two database tables"
    # the stable spec is untouched
    stable = read_json(vague / ".factory" / "spec.json")
    assert MISSED not in stable["triggers"]


def test_ids_increment_and_the_same_inputs_give_identical_bytes(vague):
    captured.append_captured(vague, MISSED, "positive")
    first = improve.improve_skill(vague)
    second = improve.improve_skill(vague)
    assert (first["id"], second["id"]) == ("c001", "c002")
    for name in ("spec.json",):
        assert (candidates.candidate_dir(vague, "c001") / name).read_bytes() == (
            candidates.candidate_dir(vague, "c002") / name
        ).read_bytes()
    assert (
        candidates.candidate_skill_dir(vague, "c001") / "SKILL.md"
    ).read_bytes() == (
        candidates.candidate_skill_dir(vague, "c002") / "SKILL.md"
    ).read_bytes()


def test_nothing_to_improve_exits_1_and_writes_nothing(well, tmp_path):
    before = snapshot(well)
    with pytest.raises(improve.NothingToImprove):
        improve.improve_skill(well)
    assert cli(tmp_path / "project", "csv-data-cleaner") == 1
    assert snapshot(well) == before
    assert not candidates.candidates_root(well).exists()


def test_captured_trigger_on_a_good_skill_still_makes_a_candidate(well):
    captured.append_captured(well, "Tidy the spreadsheet before the review", "positive")
    record = improve.improve_skill(well)
    spec = candidates.load_candidate_spec(well, "c001")
    assert "Tidy the spreadsheet before the review" in spec.triggers
    assert record["captured"] == ["cap-001"]


def test_resources_are_copied_unchanged_and_body_is_byte_identical(tmp_path):
    spec = SkillSpec(
        name="helper",
        description="Helps with things.",
        triggers=["Summarise the quarterly invoices"],
        resources=["scripts", "references"],
    )
    skill = generate(spec, tmp_path)
    (skill / "scripts" / "tool.sh").write_bytes(b"#!/bin/sh\necho hi\r\n")
    (skill / "references" / "notes.md").write_bytes("café\n".encode())
    text = (skill / "SKILL.md").read_bytes() + b"\r\nextra   trailing  \r\n"
    (skill / "SKILL.md").write_bytes(text)
    before = snapshot(skill)
    improve.improve_skill(skill)
    assert snapshot(skill) == before
    cskill = candidates.candidate_skill_dir(skill, "c001")
    assert (cskill / "scripts" / "tool.sh").read_bytes() == b"#!/bin/sh\necho hi\r\n"
    assert (cskill / "references" / "notes.md").read_bytes() == "café\n".encode()
    assert (cskill / "SKILL.md").read_bytes().endswith(body_of(skill / "SKILL.md"))
    assert body_of(cskill / "SKILL.md") == body_of(skill / "SKILL.md")


def pad_description(skill: Path, length: int) -> None:
    """Rewrite the stable description (SKILL.md and spec.json) to ``length`` characters."""
    base = "Helps with things."
    description = base + " filler" * ((length - len(base)) // 7)
    description += "x" * (length - len(description))
    assert len(description) == length
    spec = read_json(skill / ".factory" / "spec.json")
    spec["description"] = description
    (skill / ".factory" / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    text = (
        (skill / "SKILL.md").read_text(encoding="utf-8").replace(base, description, 1)
    )
    (skill / "SKILL.md").write_text(text, encoding="utf-8")


def test_a_long_description_is_truncated_safely(vague):
    pad_description(vague, 970)
    improve.improve_skill(vague)
    description = candidates.load_candidate_spec(vague, "c001").description
    assert 970 < len(description) <= MAX_DESCRIPTION
    assert description.endswith(".")
    meta = render._frontmatter(
        candidates.candidate_skill_dir(vague, "c001") / "SKILL.md"
    )
    assert meta["description"] == description


def test_nothing_fitting_skips_the_description_step(vague):
    pad_description(vague, 1023)
    record = improve.improve_skill(vague)
    description = candidates.load_candidate_spec(vague, "c001").description
    assert len(description) == 1023
    assert any(s["area"] == "trigger-vocabulary" for s in record["skipped"])
    assert not any(a["action"] == "extend-description" for a in record["applied"])


def test_negative_overlap_adds_the_failing_negative_to_non_triggers(well):
    captured.append_captured(
        well, "Please clean this spreadsheet file for me", "negative"
    )
    record = improve.improve_skill(well)
    spec = candidates.load_candidate_spec(well, "c001")
    assert "Please clean this spreadsheet file for me" in spec.non_triggers
    assert any(a["area"] == "negative-overlap" for a in record["applied"])


def test_cli_prints_the_id_and_path(vague, tmp_path, capsys):
    captured.append_captured(vague, MISSED, "positive")
    assert cli(tmp_path / "project", "general-helper") == 0
    out = capsys.readouterr().out
    assert "c001" in out
    assert str(candidates.candidate_dir(vague, "c001")) in out


def test_cli_exit_2_for_invalid_name_and_missing_skill(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    assert cli(project, "Bad_Name") == 2
    assert cli(project, "nope") == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_a_symlinked_candidates_dir_is_refused(vague, tmp_path):
    captured.append_captured(vague, MISSED, "positive")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    candidates.candidates_root(vague).symlink_to(elsewhere, target_is_directory=True)
    assert cli(tmp_path / "project", "general-helper") == 2
    assert list(elsewhere.iterdir()) == []


def test_words_only_in_a_boundary_sentence_still_count_as_missing(vague):
    # The grader ignores boundary sentences, so improve must too: a request whose words
    # appear only in "Do not use it for ..." is still a missing trigger.
    base = "Helps with things."
    description = base + " Do not use it for spreadsheet exports."
    spec = read_json(vague / ".factory" / "spec.json")
    spec["description"] = description
    (vague / ".factory" / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    text = (vague / "SKILL.md").read_text(encoding="utf-8").replace(base, description, 1)
    (vague / "SKILL.md").write_text(text, encoding="utf-8")
    captured.append_captured(vague, "Spreadsheet exports", "positive")
    improve.improve_skill(vague)
    improved = candidates.load_candidate_spec(vague, "c001").description
    assert '"Spreadsheet exports"' in improved


def test_shortening_keeps_task_words_not_a_generic_prefix():
    request = "Please help me deduplicate spreadsheet rows"
    avoid = {"please", "help", "compose", "wedding", "vows"}  # also in a negative prompt
    room = len(improve._SENTENCE.format(phrases=improve._quoted(["deduplicate spreadsheet rows"])))
    description = "x" * (MAX_DESCRIPTION - room)
    kept = improve._fit(description, [(request, "trigger-vocabulary")], avoid)
    assert kept == [("deduplicate spreadsheet rows", "trigger-vocabulary")]
    # Too long to fit whole, and nothing but words the negatives share once shortened.
    generic = "Please help me, please help, please help"
    assert improve._fit(description, [(generic, "trigger-vocabulary")], avoid) == []
