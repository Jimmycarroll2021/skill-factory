"""Tests for `skillz challenge` (challenge.py): verdicts, reports and what gets written."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import candidate, candidates, captured, challenge, render, yamlish
from skill_factory.candidates import CandidateError
from skill_factory.capture import capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
MISSED = "Help me tidy up my spreadsheet export before the quarterly review"
OFF_TOPIC = "Design a logo for my bakery"
DEDUPE = "Remove duplicate rows from my spreadsheet"
VAGUE_TRIGGERS = ["Remove duplicate rows from a spreadsheet", "Summarise the quarterly invoices"]
KEYS = {
    "schema", "skill", "candidate", "created_at", "method", "threshold", "verdict",
    "validation", "cases", "summary", "regressions", "improvements", "behaviour", "weak_areas",
    "sha256",
}  # fmt: skip


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("challenge must not spawn a process")

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


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cli(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    challenge.add_parser(sub)
    args = parser.parse_args(["challenge", *argv, "--out", str(root)])
    return challenge.run(args)


def gut(skill: Path, cid: str, description: str = "Helps with things.") -> None:
    """Replace the candidate description in both its SKILL.md and its spec."""
    md = candidates.candidate_skill_dir(skill, cid) / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    lines = [
        f"description: {description}" if x.startswith("description:") else x
        for x in lines
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    spec_path = candidates.candidate_dir(skill, cid) / "spec.json"
    spec = read_json(spec_path)
    spec["description"] = description
    spec_path.write_text(json.dumps(spec), encoding="utf-8")


def case_by_prompt(report: dict, prompt: str) -> dict:
    return next(c for c in report["cases"] if c["prompt"] == prompt)


def tree_hashes(project: Path, skip: set[str]) -> dict[str, str]:
    found = {}
    for path in sorted(project.rglob("*")):
        if path.is_file() and path.name not in skip:
            found[path.relative_to(project).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


def extend_description(skill: Path, cid: str, prompts: list[str]) -> None:
    """Name the prompts in the candidate's frontmatter description; the body stays as it is."""
    md = candidates.candidate_skill_dir(skill, cid) / "SKILL.md"
    meta = render._frontmatter(md)
    _head, body = md.read_text(encoding="utf-8").split("\n---\n", 1)
    quoted = "; ".join(f'"{p}"' for p in prompts)
    meta["description"] = f"{meta['description']} Also use for requests like {quoted}."
    md.write_text(yamlish.frontmatter_dumps(meta) + body, encoding="utf-8", newline="\n")


def better_vague(vague: Path) -> str:
    """A hand-edited candidate whose description names the missed prompt and the spec's triggers."""
    captured.append_captured(vague, MISSED, "positive")
    cid = candidate.create_candidate(vague)["id"]
    extend_description(vague, cid, [MISSED, *VAGUE_TRIGGERS])
    return cid


def test_an_edited_candidate_is_better_and_both_reports_are_written(vague):
    cid = better_vague(vague)
    report = challenge.challenge_candidate(vague, cid)
    assert report["verdict"] == "candidate-better"
    assert set(report) == KEYS
    assert report["schema"] == 1 and report["skill"] == "general-helper"
    assert report["candidate"] == cid and report["created_at"].endswith("Z")
    assert report["threshold"] == 0.3 and "lexical overlap" in report["method"]
    assert report["regressions"] == []
    assert report["improvements"]
    improved_ids = {case_by_prompt(report, MISSED)["id"]}
    assert improved_ids <= set(report["improvements"])
    assert case_by_prompt(report, MISSED)["id"] == "cap-001"
    summary = report["summary"]
    assert summary["cases"] == len(report["cases"])
    assert summary["improvements"] == len(report["improvements"])
    assert (
        summary["cases"]
        == summary["regressions"] + summary["improvements"] + summary["unchanged"]
    )
    assert set(report["weak_areas"]) == {
        "stable",
        "candidate",
        "resolved",
        "introduced",
    }
    assert "trigger-vocabulary" in report["weak_areas"]["resolved"]
    assert report["weak_areas"]["introduced"] == []

    cdir = candidates.candidate_dir(vague, cid)
    assert read_json(cdir / "challenge.json") == report
    text = (cdir / "challenge.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[0] == f"# Challenge: general-helper {cid}"
    assert "Verdict: **candidate-better**" in lines
    for heading in ("## Validation", "## Cases", "## Weak areas", "## Notes"):
        assert heading in text
    for case_id in report["improvements"]:
        assert f"`{case_id}`" in text
    assert f"skillz promote general-helper {cid}" in text
    assert "lexical-overlap" in text


def test_challenge_json_carries_digests_for_both_sides(vague):
    cid = better_vague(vague)
    report = challenge.challenge_candidate(vague, cid)
    assert report["sha256"]["stable"] == candidates.stable_digests(vague)
    assert report["sha256"]["candidate"] == candidates.candidate_digests(vague, cid)
    assert "SKILL.md" in report["sha256"]["stable"]
    assert ".factory/spec.json" in report["sha256"]["stable"]
    assert "spec.json" in report["sha256"]["candidate"]


def test_a_gutted_candidate_is_worse_and_names_the_regressed_case(well):
    captured.append_captured(well, "Tidy the spreadsheet before the review", "positive")
    cid = candidate.create_candidate(well)["id"]
    gut(well, cid)
    report = challenge.challenge_candidate(well, cid)
    assert report["verdict"] == "candidate-worse"
    regressed = case_by_prompt(report, DEDUPE)
    assert regressed["change"] == "regression"
    assert regressed["stable"] == "pass" and regressed["candidate"] == "fail"
    assert regressed["id"] in report["regressions"]
    text = (candidates.candidate_dir(well, cid) / "challenge.md").read_text(
        encoding="utf-8"
    )
    assert "Verdict: **candidate-worse**" in text
    assert f"`{regressed['id']}`" in text and "regression" in text


def test_a_destructive_script_makes_the_candidate_unsafe_even_with_regressions(well):
    captured.append_captured(well, OFF_TOPIC, "negative")
    cid = candidate.create_candidate(well)["id"]
    gut(well, cid)
    scripts = candidates.candidate_skill_dir(well, cid) / "scripts"
    scripts.mkdir()
    (scripts / "clean.sh").write_text("#!/bin/sh\nrm -rf /\n", encoding="utf-8")
    report = challenge.challenge_candidate(well, cid)
    assert report["regressions"]
    assert report["verdict"] == "candidate-unsafe"
    assert report["validation"]["stable"]["ok"] is True
    assert report["validation"]["candidate"]["ok"] is False
    new_errors = report["validation"]["new_errors"]
    assert new_errors and all(f["severity"] == "error" for f in new_errors)
    assert any(f["path"] == "scripts/clean.sh" for f in new_errors)
    text = (candidates.candidate_dir(well, cid) / "challenge.md").read_text(
        encoding="utf-8"
    )
    assert "Verdict: **candidate-unsafe**" in text and "scripts/clean.sh" in text


def test_a_name_mismatch_is_unsafe(well):
    captured.append_captured(well, OFF_TOPIC, "negative")
    cid = candidate.create_candidate(well)["id"]
    md = candidates.candidate_skill_dir(well, cid) / "SKILL.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace(
            "name: csv-data-cleaner", "name: other", 1
        )
    )
    report = challenge.challenge_candidate(well, cid)
    assert report["verdict"] == "candidate-unsafe"
    assert any(f["rule"] == "name-mismatch" for f in report["validation"]["new_errors"])


def test_an_unchanged_candidate_is_no_difference(well):
    captured.append_captured(well, OFF_TOPIC, "negative")
    cid = candidate.create_candidate(well)["id"]
    report = challenge.challenge_candidate(well, cid)
    assert report["verdict"] == "no-difference"
    assert report["regressions"] == [] and report["improvements"] == []
    assert report["summary"]["unchanged"] == report["summary"]["cases"]


def test_a_failure_captured_after_the_candidate_is_graded_on_both_sides(vague):
    cid = better_vague(vague)
    captured.append_captured(
        vague, "Summarise the quarterly invoices please", "positive"
    )
    report = challenge.challenge_candidate(vague, cid)
    case = case_by_prompt(report, "Summarise the quarterly invoices please")
    assert case["id"] == "cap-002" and case["origins"] == ["captured"]
    assert case["stable"] in ("pass", "fail") and case["candidate"] == "pass"
    assert case["stable_score"] is not None and case["candidate_score"] is not None


def test_the_union_has_stable_and_candidate_origins_and_both_validations(vague):
    cid = better_vague(vague)
    report = challenge.challenge_candidate(vague, cid)
    origins = {o for c in report["cases"] for o in c["origins"]}
    assert origins == {"captured", "stable", "candidate"}
    ids = [c["id"] for c in report["cases"]]
    assert len(ids) == len(set(ids))
    assert any(i.startswith("stable/") for i in ids)
    assert report["validation"]["stable"]["skill"] == "general-helper"
    assert report["validation"]["candidate"]["skill"] == "general-helper"
    shared = case_by_prompt(report, "Remove duplicate rows from a spreadsheet")
    assert shared["origins"] == ["stable", "candidate"]
    assert shared["change"] == "improvement"
    behaviour = next(c for c in report["cases"] if c["kind"] == "behaviour")
    assert (
        behaviour["stable"] == "pending-runner" and behaviour["change"] == "unchanged"
    )


def test_challenge_writes_only_the_two_reports_inside_the_candidate(vague):
    cid = better_vague(vague)
    project = vague.parent.parent
    before = tree_hashes(project, {"challenge.json", "challenge.md"})
    challenge.challenge_candidate(vague, cid)
    assert tree_hashes(project, {"challenge.json", "challenge.md"}) == before
    cdir = candidates.candidate_dir(vague, cid)
    assert (cdir / "challenge.json").is_file() and (cdir / "challenge.md").is_file()
    written = {p for p in project.rglob("challenge.*")}
    assert written == {cdir / "challenge.json", cdir / "challenge.md"}


def test_challenge_is_deterministic(vague):
    cid = better_vague(vague)
    cdir = candidates.candidate_dir(vague, cid)
    first = challenge.challenge_candidate(vague, cid)
    md_first = (cdir / "challenge.md").read_bytes()
    second = challenge.challenge_candidate(vague, cid)
    first.pop("created_at"), second.pop("created_at")
    assert first == second
    assert (cdir / "challenge.md").read_bytes() == md_first


def test_missing_or_invalid_candidates_raise_candidate_error(vague):
    with pytest.raises(CandidateError):
        challenge.challenge_candidate(vague, "c009")
    with pytest.raises(CandidateError):
        challenge.challenge_candidate(vague, "../x")
    cid = better_vague(vague)
    (candidates.candidate_dir(vague, cid) / "spec.json").unlink()
    with pytest.raises(CandidateError):
        challenge.challenge_candidate(vague, cid)


def test_cli_prints_the_verdict_and_exits_0_for_every_verdict(
    vague, well, tmp_path, capsys
):
    project = tmp_path / "project"
    cid = better_vague(vague)
    assert cli(project, "general-helper", cid) == 0
    out = capsys.readouterr().out
    assert (
        "candidate-better" in out and "challenge.json" in out and "challenge.md" in out
    )

    captured.append_captured(well, OFF_TOPIC, "negative")
    wid = candidate.create_candidate(well)["id"]
    gut(well, wid)
    assert cli(project, "csv-data-cleaner", wid) == 0
    assert "candidate-worse" in capsys.readouterr().out
    scripts = candidates.candidate_skill_dir(well, wid) / "scripts"
    scripts.mkdir()
    (scripts / "clean.sh").write_text("rm -rf /\n", encoding="utf-8")
    assert cli(project, "csv-data-cleaner", wid) == 0
    assert "candidate-unsafe" in capsys.readouterr().out


def test_cli_exit_2_for_bad_inputs(vague, tmp_path, capsys):
    project = tmp_path / "project"
    assert cli(project, "general-helper", "c001") == 2
    assert cli(project, "general-helper", "../x") == 2
    assert cli(project, "general-helper", "C001") == 2
    assert cli(project, "Bad_Name", "c001") == 2
    assert cli(project, "nope", "c001") == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_a_symlinked_candidate_dir_is_refused(vague, tmp_path):
    cid = better_vague(vague)
    cdir = candidates.candidate_dir(vague, cid)
    moved = tmp_path / "moved"
    cdir.rename(moved)
    cdir.symlink_to(moved, target_is_directory=True)
    assert cli(tmp_path / "project", "general-helper", cid) == 2
    assert not (moved / "challenge.json").exists()
