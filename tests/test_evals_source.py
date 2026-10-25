"""Tests for ``evals/evals.json`` as a case source: loading, ids, validation and how the three
case sources (spec, evals, captured failures) are assembled and de-duplicated.

Nothing here may start a process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from skill_factory import captured, evalcases, evaluate, runner
from skill_factory.capture import SpecError, capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
FOREIGN = "foreign-skill"
TRIGGER_IDS = [
    "evl-drafts-release-notes",
    "evl-announcement-from-changelog",
    "evl-writes-changelog-entry",
    "evl-ci-pipeline",
]
BEHAVIOUR_IDS = ["evl-drafts-release-notes-beh", "evl-announcement-from-changelog-beh"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("evals must not spawn a process in tests")

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
    """The hand-written fixture copied under ``<root>/skills/`` so the fixture is never written."""
    dest = root / "skills" / FOREIGN
    shutil.copytree(FIXTURES / FOREIGN, dest)
    return dest


def _write_evals(skill_dir: Path, entries: object) -> Path:
    path = skill_dir / "evals" / "evals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = entries if isinstance(entries, str) else json.dumps(entries)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _bare(root: Path, name: str = "bare-skill") -> Path:
    skill = root / "skills" / name
    skill.mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: Does a thing. NOT for anything else.\n---\n\n# Body\n"
    (skill / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    return skill


# --- load_evals ---------------------------------------------------------------------------------


def test_missing_evals_file_gives_no_cases(root):
    assert evalcases.load_evals(_bare(root)) == []


def test_fixture_entries_map_to_trigger_and_behaviour_cases(foreign):
    cases = evalcases.load_evals(foreign)
    assert [c.id for c in cases] == [
        "evl-drafts-release-notes",
        "evl-drafts-release-notes-beh",
        "evl-announcement-from-changelog",
        "evl-announcement-from-changelog-beh",
        "evl-writes-changelog-entry",
        "evl-ci-pipeline",
    ]
    by_id = {c.id: c for c in cases}
    assert by_id["evl-drafts-release-notes"].kind == "positive"
    assert by_id["evl-writes-changelog-entry"].kind == "negative"
    assert by_id["evl-ci-pipeline"].kind == "negative"
    assert all(c.source == "evals.json" for c in cases)
    assert all(c.expected == [] for c in cases)
    behaviour = by_id["evl-drafts-release-notes-beh"]
    assert behaviour.kind == "behaviour"
    assert behaviour.prompt == by_id["evl-drafts-release-notes"].prompt
    assert behaviour.rubric == [
        "Groups the notes under the changelog's own section headings",
        "Does NOT invent an entry that is not in the changelog",
    ]
    assert by_id["evl-writes-changelog-entry"].rubric == []
    assert evalcases.load_evals(foreign) == cases


def test_ids_fall_back_to_the_index_and_are_sanitised(root):
    skill = _bare(root)
    _write_evals(
        skill,
        [
            {"prompt": "first", "assertions": ["Says hi"]},
            {"id": "has space/and:colon", "prompt": "second", "should_trigger": False},
            {"id": "no-assertions", "prompt": "third", "should_trigger": True, "assertions": []},
            {"id": "x", "prompt": "fourth", "should_trigger": True, "assertions": ["", 3]},
        ],
    )
    cases = evalcases.load_evals(skill)
    assert [c.id for c in cases] == [
        "evl-000",
        "evl-000-beh",
        "evl-has-space-and-colon",
        "evl-no-assertions",
        "evl-x",
    ]
    assert [c.kind for c in cases] == ["positive", "behaviour", "negative", "positive", "positive"]
    assert all(c.id.startswith("evl-") for c in cases)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", c.id) for c in cases)


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("{not json", "invalid JSON"),
        ('{"prompt": "x"}', "must be a JSON list"),
        ("[1]", "entry 0"),
        ('[{"prompt": "ok", "assertions": ["a"]}, {"prompt": 3}]', "entry 1"),
        ('[{"id": "a", "prompt": ""}]', "entry 0"),
        ('[{"id": "dup", "prompt": "p"}, {"id": "dup", "prompt": "q"}]', "duplicate"),
    ],
)
def test_invalid_evals_json_raises_spec_error_naming_the_file(root, content, fragment):
    skill = _bare(root)
    path = _write_evals(skill, content)
    with pytest.raises(SpecError) as excinfo:
        evalcases.load_evals(skill)
    message = str(excinfo.value)
    assert str(path) in message and fragment in message


# --- all_cases ----------------------------------------------------------------------------------


def test_all_cases_appends_evals_and_captured_after_the_generated_ones(foreign):
    spec = evaluate.load_spec(foreign)
    captured.append_captured(foreign, "Write release notes for the hotfix", "positive")
    cases = evalcases.all_cases(foreign, spec)
    ids = [c.id for c in cases]
    generated = [c.id for c in evalcases.generate_cases(spec)]
    assert ids[: len(generated)] == generated
    assert ids[len(generated) :] == [*TRIGGER_IDS[:1], BEHAVIOUR_IDS[0], TRIGGER_IDS[1], BEHAVIOUR_IDS[1], *TRIGGER_IDS[2:], "cap-001"]
    assert len(ids) == len(set(ids))
    assert evalcases.all_cases(foreign, spec) == cases


def test_an_evals_entry_wins_over_a_captured_failure_with_the_same_prompt(foreign):
    spec = evaluate.load_spec(foreign)
    prompt = "  draft RELEASE notes for version 2.3   from our changelog.md "
    captured.append_captured(foreign, prompt, "positive")
    captured.append_captured(foreign, "Draft release notes for version 2.3 from our CHANGELOG.md", "behaviour", expected=["x"])
    captured.append_captured(foreign, prompt, "negative")
    cases = evalcases.all_cases(foreign, spec)
    ids = [c.id for c in cases]
    assert "cap-001" not in ids  # same kind and prompt as evl-drafts-release-notes
    assert "cap-002" not in ids  # same kind and prompt as evl-drafts-release-notes-beh
    assert "cap-003" in ids  # a different kind is a different case
    assert "evl-drafts-release-notes" in ids and "evl-drafts-release-notes-beh" in ids


def test_a_generated_skill_without_evals_keeps_every_generated_case(root):
    skill = generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), root)
    spec = evaluate.load_spec(skill)
    generated = evalcases.generate_cases(spec)
    assert evalcases.all_cases(skill, spec) == generated
    # Trigger-derived behaviour cases repeat a behaviour-derived prompt and must all survive.
    assert {"beh-001", "beh-t001", "beh-t002", "beh-t003"} <= {c.id for c in generated}


def test_a_generic_negative_duplicated_in_evals_keeps_the_generated_case(root):
    skill = _bare(root)
    _write_evals(skill, [{"id": "weather", "prompt": evalcases.GENERIC_NEGATIVES[0], "should_trigger": False}])
    ids = [c.id for c in evalcases.all_cases(skill, evaluate.load_spec(skill))]
    assert "evl-weather" not in ids and "neg-001" in ids


def test_grade_markers_with_no_expected_markers_is_not_a_failure():
    assert runner.grade_markers("anything at all", []) == {"passed": True, "found": [], "missing": []}
