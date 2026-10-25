"""End-to-end tests on a skill written by hand: ``tests/fixtures/foreign-skill`` has
``evals/evals.json`` and no ``.factory/``, and ``skillz`` evaluates, judges, packages and
challenges it on those evals. The committed meta-skill is checked the same way.

Only the fake runner and judge from ``test_judge`` are used; nothing here may start a process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_challenge import extend_description
from test_evals_source import BEHAVIOUR_IDS, FOREIGN, TRIGGER_IDS, _write_evals
from test_judge import FakeJudge, FakeRunner

from skill_factory import (
    candidate,
    candidates,
    captured,
    challenge,
    evaluate,
    judge,
    release,
    runner,
)
from skill_factory.capture import SpecError, capture_spec_file
from skill_factory.cli import main
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parent.parent
WHICH = "skill_factory.judge.shutil.which"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("a foreign skill must not spawn a process in tests")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(runner.subprocess, "run", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def foreign(root) -> Path:
    dest = root / "skills" / FOREIGN
    shutil.copytree(FIXTURES / FOREIGN, dest)
    return dest


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_fixture_has_no_factory_and_passes_its_own_evals(foreign):
    assert not (foreign / ".factory").exists()
    assert not (FIXTURES / FOREIGN / ".factory").exists()


def test_an_invalid_evals_json_fails_eval_loudly(root, foreign, capsys):
    (foreign / "evals" / "evals.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SpecError, match="evals.json"):
        evaluate.evaluate_skill(foreign)
    assert not (foreign / ".factory" / "eval.json").exists()
    assert main(["eval", FOREIGN, "--out", str(root)]) == 2
    err = capsys.readouterr().err
    assert "skillz: error:" in err and str(foreign / "evals" / "evals.json") in err


def test_a_captured_failure_matching_an_evals_entry_is_not_double_counted(foreign):
    captured.append_captured(foreign, "Draft release notes for version 2.3 from our CHANGELOG.md", "positive")
    captured.append_captured(foreign, "Something the evals do not cover", "positive")
    report = evaluate.evaluate_skill(foreign)
    results = [r["id"] for r in report["results"]]
    assert "cap-001" not in results and "evl-drafts-release-notes" in results
    assert "cap-002" in results
    stored = [c["id"] for c in _read_json(foreign / ".factory" / "cases.json")["cases"]]
    assert stored.count("evl-drafts-release-notes") == 1 and "cap-001" not in stored


# --- end to end on the hand-written skill -------------------------------------------------------


def test_eval_cli_grades_the_evals_of_a_skill_without_a_factory(root, foreign, capsys):
    assert not (foreign / ".factory").exists()
    assert main(["eval", FOREIGN, "--out", str(root)]) == 0
    report = _read_json(foreign / ".factory" / "eval.json")
    results = {r["id"]: r for r in report["results"]}
    for case_id in TRIGGER_IDS:
        assert results[case_id]["status"] in ("pass", "fail")
        assert results[case_id]["score"] is not None
    assert results["evl-drafts-release-notes"]["kind"] == "positive"
    assert results["evl-ci-pipeline"]["kind"] == "negative"
    for case_id in BEHAVIOUR_IDS:
        assert results[case_id] == {
            "id": case_id, "kind": "behaviour", "score": None, "matched": None, "status": "pending-runner",
        }
    assert report["summary"]["behaviour"]["pending"] == 2
    assert all(results[i]["status"] == "pass" for i in TRIGGER_IDS)
    stored = {c["id"]: c for c in _read_json(foreign / ".factory" / "cases.json")["cases"]}
    assert set(TRIGGER_IDS + BEHAVIOUR_IDS) <= set(stored)
    assert stored["evl-drafts-release-notes-beh"]["rubric"][0].startswith("Groups the notes")
    assert stored["evl-drafts-release-notes-beh"]["expected"] == []
    assert stored["evl-ci-pipeline"]["source"] == "evals.json"
    assert "eval foreign-skill" in capsys.readouterr().out


def test_eval_baseline_runs_the_evals_behaviour_cases(foreign):
    fake = FakeRunner()
    report = evaluate.evaluate_skill(foreign, "claude", runner_factory=lambda _name: fake)
    baseline = report["baseline"]
    assert baseline["status"] == "completed"
    assert [c["id"] for c in baseline["cases"]] == BEHAVIOUR_IDS
    assert all(c["verdict"] == "no-difference" for c in baseline["cases"])
    assert len(fake.prompts) == 4


def test_judge_skill_judges_the_evals_behaviour_cases(foreign, monkeypatch):
    runner_, judge_ = FakeRunner("claude"), FakeJudge("codex")
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    report = judge.judge_skill(
        foreign, "claude", runner_factory={"claude": runner_, "codex": judge_}.get
    )
    assert report["status"] == "completed" and report["skill"] == FOREIGN
    assert [c["id"] for c in report["cases"]] == BEHAVIOUR_IDS
    assert report["summary"] == {"cases": 2, "skill_better": 2, "skill_worse": 0, "tie": 0, "errors": 0}
    first = report["cases"][0]
    assert first["rubric"] == [
        "Groups the notes under the changelog's own section headings",
        "Does NOT invent an entry that is not in the changelog",
    ]
    assert first["source"] == "evals.json"
    assert "Groups the notes under the changelog" in judge_.prompts[0]
    assert len(runner_.prompts) == 4 and len(judge_.prompts) == 2
    assert _read_json(foreign / ".factory" / "judge.json")["summary"]["cases"] == 2


def test_package_skill_versions_a_skill_without_a_factory(foreign):
    version = release.package_skill(foreign)
    assert version.version == "0.1.0"
    reports = version.path / "reports"
    stored = {c["id"] for c in _read_json(reports / "cases.json")["cases"]}
    assert set(TRIGGER_IDS + BEHAVIOUR_IDS) <= stored
    results = {r["id"] for r in _read_json(reports / "eval.json")["results"]}
    assert set(TRIGGER_IDS) <= results
    assert _read_json(version.path / "regression.json")["status"] != "regressed"


def test_the_committed_meta_skill_yields_every_eval_as_a_case(root):
    source = REPO / "skills" / "skill-factory"
    dest = root / "skills" / "skill-factory"
    shutil.copytree(source, dest)
    had_factory = (source / ".factory").exists()  # a local skillz run may have left one
    report = evaluate.evaluate_skill(dest)
    entries = _read_json(source / "evals" / "evals.json")
    expected = {f"evl-{e['id']}" for e in entries}
    assert len(expected) >= 4
    results = {r["id"]: r for r in report["results"]}
    assert expected <= set(results)
    assert results["evl-not-a-skill-request"]["kind"] == "negative"
    assert results["evl-builds-and-reports"]["kind"] == "positive"
    behaviours = {f"evl-{e['id']}-beh" for e in entries if e["should_trigger"]}
    assert len(behaviours) >= 3 and behaviours <= set(results)
    assert all(results[b]["status"] == "pending-runner" for b in behaviours)
    stored = {c["id"]: c for c in _read_json(dest / ".factory" / "cases.json")["cases"]}
    by_id = {e["id"]: e for e in entries}
    assert stored["evl-builds-and-reports-beh"]["rubric"] == by_id["builds-and-reports"]["assertions"]
    assert (source / ".factory").exists() == had_factory  # evaluating the copy wrote nothing here


# --- challenge ----------------------------------------------------------------------------------


def test_challenge_grades_the_evals_on_both_sides(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    skill = generate(capture_spec_file(FIXTURES / "eval" / "vague.json"), project)
    missed = "Help me tidy up my spreadsheet export before the quarterly review"
    _write_evals(
        skill,
        [
            {"id": "tidy-export", "prompt": missed, "should_trigger": True, "assertions": ["Tidies it"]},
            {"id": "logo", "prompt": "Design a logo for my bakery", "should_trigger": False},
        ],
    )
    captured.append_captured(skill, missed, "positive")
    cid = candidate.create_candidate(skill)["id"]
    # A human edits the candidate: the missed prompt becomes a trigger and the description names it.
    spec_path = candidates.candidate_dir(skill, cid) / candidates.SPEC_JSON
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    data["triggers"].append(missed)
    spec_path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")
    extend_description(skill, cid, [missed])
    report = challenge.challenge_candidate(skill, cid)
    rows = {c["id"]: c for c in report["cases"]}
    # The edited candidate adds the missed prompt as a trigger, so it comes from three places.
    assert rows["evl-tidy-export"]["origins"] == ["evals", "captured", "candidate"]
    assert "cap-001" not in rows and "candidate/pos-001" not in rows
    assert rows["evl-tidy-export"]["candidate"] == "pass"
    assert rows["evl-logo"]["origins"] == ["evals"] and rows["evl-logo"]["kind"] == "negative"
    assert rows["evl-tidy-export-beh"]["stable"] == "pending-runner"
    assert len(report["cases"]) == len({c["id"] for c in report["cases"]})
