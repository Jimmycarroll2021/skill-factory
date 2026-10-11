"""Tests for behaviour-case generation: prompts, rubrics, markers, ids and determinism."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skill_factory import evalcases as ev
from skill_factory.capture import SkillSpec, capture_spec_file

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
BEHAVIOUR_IDS = ["beh-001", "beh-002", "beh-t001", "beh-t002", "beh-t003"]


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("case generation must never spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


@pytest.fixture
def good() -> SkillSpec:
    return capture_spec_file(FIXTURES / "well-described.json")


def _behaviour(spec: SkillSpec) -> list[ev.Case]:
    return [c for c in ev.generate_cases(spec) if c.kind == "behaviour"]


def test_one_case_per_behaviour_and_per_trigger(good):
    cases = _behaviour(good)
    assert [c.id for c in cases] == BEHAVIOUR_IDS
    assert [c.source for c in cases] == [
        "behaviour:0",
        "behaviour:1",
        "trigger:0",
        "trigger:1",
        "trigger:2",
    ]
    assert all(c.kind == "behaviour" for c in cases)


def test_behaviour_cases_come_after_positives_and_negatives(good):
    kinds = [c.kind for c in ev.generate_cases(good)]
    first = kinds.index("behaviour")
    assert set(kinds[:first]) == {"positive", "negative"}
    assert kinds[first:] == ["behaviour"] * 5


def test_prompts_are_task_requests_without_the_behaviour_sentence(good):
    for case in _behaviour(good):
        assert case.prompt.strip()
        for behaviour in good.behaviours:
            assert behaviour.lower() not in case.prompt.lower()
    by_id = {c.id: c for c in _behaviour(good)}
    assert good.triggers[0].lower() in by_id["beh-001"].prompt.lower()
    assert good.triggers[1].lower() in by_id["beh-002"].prompt.lower()
    for i in range(3):
        assert good.triggers[i].lower() in by_id[f"beh-t{i + 1:03d}"].prompt.lower()


def test_behaviour_prompts_cycle_through_triggers():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean CSV files.",
        triggers=["Clean this CSV file"],
        behaviours=["Keep a backup", "Report every change"],
    )
    cases = _behaviour(spec)
    assert [c.id for c in cases] == ["beh-001", "beh-002", "beh-t001"]
    assert all("clean this csv file" in c.prompt.lower() for c in cases)


def test_behaviour_case_rubric(good):
    cases = {c.id: c for c in _behaviour(good)}
    for i, behaviour in enumerate(good.behaviours):
        case = cases[f"beh-{i + 1:03d}"]
        assert case.rubric[0] == behaviour
        assert case.rubric[1] == f"mentions: {', '.join(case.expected)}"
        assert len(case.rubric) == 3
        assert case.rubric[2].startswith("must not: ")
        assert good.non_triggers[0] in case.rubric[2]


def test_trigger_case_rubric_and_markers(good):
    cases = {c.id: c for c in _behaviour(good)}
    union: list[str] = []
    for behaviour in good.behaviours:
        for marker in ev._markers(behaviour):
            if marker not in union:
                union.append(marker)
    for i in range(3):
        case = cases[f"beh-t{i + 1:03d}"]
        assert case.expected == union
        assert case.rubric[: len(good.behaviours)] == good.behaviours
        assert case.rubric[len(good.behaviours)] == f"mentions: {', '.join(union)}"
        assert sum(c.startswith("must not:") for c in case.rubric) == 1


def test_every_behaviour_case_is_fully_populated(good):
    for case in _behaviour(good):
        assert case.rubric
        assert any(c.startswith("mentions: ") for c in case.rubric)
        assert sum(c.startswith("must not:") for c in case.rubric) == 1
        assert case.expected
        assert all(m and m == m.lower() for m in case.expected)
    for case in _behaviour(good)[:2]:
        assert case.expected == ev._markers(good.behaviours[int(case.id[-1]) - 1])


def test_no_must_not_without_non_triggers():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean CSV files.",
        triggers=["Clean this CSV file"],
        behaviours=["Keep a backup copy"],
    )
    for case in _behaviour(spec):
        assert not any(c.startswith("must not:") for c in case.rubric)
        assert case.rubric


def test_trigger_case_without_behaviours_uses_trigger_words():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean CSV files.",
        triggers=["Clean this messy CSV file for me"],
    )
    (case,) = _behaviour(spec)
    assert case.id == "beh-t001"
    assert case.expected == ev.content_words(spec.triggers[0])[:3]
    assert case.rubric[0] == f"Completes the request: {spec.triggers[0]}"
    assert case.rubric[1] == f"mentions: {', '.join(case.expected)}"


def test_behaviours_without_triggers_use_the_description_sentence():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean CSV files. Use it for spreadsheets.",
        behaviours=["Keep a backup copy"],
    )
    (case,) = _behaviour(spec)
    assert case.id == "beh-001"
    assert "clean csv files" in case.prompt.lower()
    assert "keep a backup copy" not in case.prompt.lower()


def test_no_behaviours_and_no_triggers_yield_no_behaviour_cases():
    spec = SkillSpec(name="csv-data-cleaner", description="Clean CSV files.")
    assert _behaviour(spec) == []


def test_prompt_never_contains_a_behaviour_sentence_even_inside_a_trigger():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean CSV files.",
        triggers=["Keep a backup copy of my CSV"],
        behaviours=["Keep a backup copy"],
    )
    for case in _behaviour(spec):
        assert "keep a backup copy" not in case.prompt.lower()
        assert case.prompt.strip()


def test_generation_and_cases_json_are_byte_identical(good):
    first = ev.generate_cases(good)
    second = ev.generate_cases(capture_spec_file(FIXTURES / "well-described.json"))
    assert first == second
    assert ev.cases_json(first) == ev.cases_json(second)
    assert ev.cases_json(first).encode("utf-8") == ev.cases_json(second).encode("utf-8")


def test_cases_json_carries_the_rubric_only_on_behaviour_cases(good):
    payload = json.loads(ev.cases_json(ev.generate_cases(good)))["cases"]
    for item in payload:
        if item["kind"] == "behaviour":
            assert set(item) == {"id", "kind", "prompt", "source", "expected", "rubric"}
            assert item["rubric"] and item["expected"]
        else:
            assert set(item) == {"id", "kind", "prompt", "source", "expected"}


def test_case_to_dict_omits_an_empty_rubric():
    assert "rubric" not in ev.Case("c-1", "positive", "p", "s").to_dict()
    assert ev.Case("c-1", "behaviour", "p", "s", ["x"], ["be good"]).to_dict()["rubric"] == [
        "be good"
    ]
    assert ev.Case("c-1", "behaviour", "p", "s", ["x"]).rubric == []


def test_adding_a_behaviour_changes_no_other_prompt(good):
    before = {c.id: c.prompt for c in ev.generate_cases(good)}
    more = SkillSpec.from_dict({**good.to_dict(), "behaviours": [*good.behaviours, "Extra thing"]})
    after = {c.id: c.prompt for c in ev.generate_cases(more)}
    assert set(after) - set(before) == {"beh-003"}
    assert {k: v for k, v in after.items() if k in before} == before


def test_adding_a_trigger_keeps_existing_behaviour_ids(good):
    more = SkillSpec.from_dict({**good.to_dict(), "triggers": [*good.triggers, "Fix my CSV"]})
    ids = [c.id for c in ev.generate_cases(more) if c.kind == "behaviour"]
    assert ids == [*BEHAVIOUR_IDS, "beh-t004"]


def test_behaviour_cases_stay_pending_for_offline_grading(good):
    cases = ev.generate_cases(good)
    grades = ev.grade_offline(cases, good)
    pending = [g for g in grades if g.kind == "behaviour"]
    assert [g.id for g in pending] == BEHAVIOUR_IDS
    assert all(g.status == "pending-runner" for g in pending)
