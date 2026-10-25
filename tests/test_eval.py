"""Tests for evaluation case generation, offline grading and improvement proposals."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skill_factory import evalcases as ev
from skill_factory.capture import SkillSpec, capture_spec_file

FIXTURES = Path(__file__).parent / "fixtures" / "eval"


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("evalcases must never spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


@pytest.fixture
def good() -> SkillSpec:
    return capture_spec_file(FIXTURES / "well-described.json")


@pytest.fixture
def vague() -> SkillSpec:
    return capture_spec_file(FIXTURES / "vague.json")


def _grades(spec: SkillSpec):
    cases = ev.generate_cases(spec)
    return cases, ev.grade_offline(cases, spec.name, spec.description)


def test_generation_is_deterministic_and_ids_are_stable(good):
    first = ev.generate_cases(good)
    second = ev.generate_cases(good)
    assert first == second
    assert ev.cases_json(first) == ev.cases_json(second)
    assert ev.cases_json(first).endswith("\n")
    assert json.loads(ev.cases_json(first))["cases"][0]["id"] == "pos-001"
    ids = [c.id for c in first]
    assert ids[0] == "pos-001"
    assert "neg-001" in ids
    assert "beh-001" in ids
    assert len(set(ids)) == len(ids)


def test_ids_do_not_depend_on_other_kinds(good):
    base = {c.id: c.prompt for c in ev.generate_cases(good)}
    more = SkillSpec.from_dict({**good.to_dict(), "behaviours": good.behaviours + ["Extra thing"]})
    after = {c.id: c.prompt for c in ev.generate_cases(more)}
    assert {k: v for k, v in after.items() if k != "beh-003"} == base


def test_cases_come_from_the_right_sources(good):
    cases = ev.generate_cases(good)
    positives = [c for c in cases if c.kind == "positive"]
    negatives = [c for c in cases if c.kind == "negative"]
    behaviours = [c for c in cases if c.kind == "behaviour"]
    assert [c.prompt for c in positives[:-1]] == good.triggers
    assert [c.source for c in positives] == ["trigger:0", "trigger:1", "trigger:2", "description"]
    assert [c.prompt for c in negatives[: len(good.non_triggers)]] == good.non_triggers
    assert negatives[0].source == "non_trigger:0"
    assert len(behaviours) == len(good.behaviours) + len(good.triggers)
    assert [c.id for c in behaviours] == ["beh-001", "beh-002", "beh-t001", "beh-t002", "beh-t003"]
    for case in behaviours:
        assert case.expected
        assert all(m == m.lower() and m for m in case.expected)
    assert all(c.expected == [] for c in positives + negatives)
    assert [c.kind for c in cases] == (
        ["positive"] * len(positives) + ["negative"] * len(negatives) + ["behaviour"] * 5
    )
    assert set(positives[0].to_dict()) == {"id", "kind", "prompt", "source", "expected"}


def test_generic_negatives_are_included(good):
    assert len(ev.GENERIC_NEGATIVES) >= 4
    cases = ev.generate_cases(good)
    generic = [c for c in cases if c.source.startswith("generic:")]
    assert [c.prompt for c in generic] == list(ev.GENERIC_NEGATIVES)
    assert all(c.kind == "negative" for c in generic)
    assert generic[0].id == f"neg-{len(good.non_triggers) + 1:03d}"


def test_well_described_positives_match_and_negatives_do_not(good):
    cases, grades = _grades(good)
    assert len(grades) == len(cases)
    for case, grade in zip(cases, grades, strict=True):
        assert grade.id == case.id
        if case.kind == "positive":
            assert grade.matched is True, case
            assert grade.status == "pass"
        elif case.kind == "negative":
            assert grade.matched is False, case
            assert grade.status == "pass"
    assert ev.propose_improvements(good, cases, grades) == []


def test_behaviour_grades_are_pending_runner(good):
    _, grades = _grades(good)
    behaviour = [g for g in grades if g.kind == "behaviour"]
    assert len(behaviour) == 5
    for grade in behaviour:
        assert grade.status == "pending-runner"
        assert grade.score is None
        assert grade.matched is None
        assert set(grade.to_dict()) == {"id", "kind", "score", "matched", "status"}


def test_grade_offline_accepts_a_spec(good):
    cases = ev.generate_cases(good)
    assert ev.grade_offline(cases, good) == ev.grade_offline(cases, good.name, good.description)


def test_vague_fixture_fails_triggers_and_gets_proposals(vague):
    cases, grades = _grades(vague)
    failing = [g.id for g in grades if g.kind == "positive" and g.status == "fail"]
    assert failing == ["pos-001", "pos-002"]
    proposals = {p.area: p for p in ev.propose_improvements(vague, cases, grades)}
    vocab = proposals["trigger-vocabulary"]
    assert set(failing) <= set(vocab.cases)
    assert all(case_id in vocab.problem for case_id in failing)
    assert "duplicate" in vocab.proposal
    assert "vague-description" in proposals
    assert "duplicate" in proposals["vague-description"].proposal
    assert "negative-overlap" not in proposals
    assert set(vocab.to_dict()) == {"area", "problem", "proposal", "cases"}


def test_negative_overlap_proposal_names_the_case():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean and tidy CSV spreadsheet files by removing duplicate rows.",
        triggers=["Clean this CSV file"],
        non_triggers=["Clean the duplicate rows in my spreadsheet files"],
        behaviours=["Always keep a backup of the original file"],
    )
    cases, grades = _grades(spec)
    proposals = ev.propose_improvements(spec, cases, grades)
    assert [p.area for p in proposals] == ["negative-overlap"]
    assert proposals[0].cases == ["neg-001"]
    assert "neg-001" in proposals[0].problem
    assert "duplicate" in proposals[0].proposal
    assert "non_triggers" in proposals[0].proposal


def test_no_behaviours_proposal():
    spec = SkillSpec(
        name="csv-data-cleaner",
        description="Clean and tidy CSV spreadsheet files by removing duplicate rows.",
        triggers=["Clean this CSV file"],
    )
    cases, grades = _grades(spec)
    assert [p.area for p in ev.propose_improvements(spec, cases, grades)] == ["no-behaviours"]


def test_match_score(good):
    assert ev.match_score("What is the of and to a", good.name, good.description) == 0.0
    assert ev.match_score("", good.name, good.description) == 0.0
    words = ev.content_words(good.description)[:6]
    assert ev.match_score(" ".join(words), good.name, good.description) >= ev.MATCH_THRESHOLD
    score = ev.match_score("clean the weather", good.name, good.description)
    assert score == round(score, 4)
    assert 0.0 < score < 1.0


def test_content_words_are_ordered_unique_and_without_stop_words():
    assert ev.content_words("The Rows, the rows and a CSV; csv!") == ["row", "csv"]


def test_method_says_heuristic():
    assert "heuristic" in ev.METHOD
    assert "lexical overlap" in ev.METHOD


def test_boundary_sentences_in_the_description_are_not_trigger_words():
    # "Do not use it for X" tells the model to stay away from X; its words must not make
    # a request about X look like a match.
    description = (
        "Draft release notes from merged pull requests. Use it when the user asks for a "
        "changelog entry. Do not use it for internal design documents."
    )
    off_topic = "Write internal design documents for the team"
    on_topic = "Write a changelog entry from the merged pull requests"
    assert ev.match_score(off_topic, "release-notes", description) < ev.MATCH_THRESHOLD
    assert ev.match_score(on_topic, "release-notes", description) >= ev.MATCH_THRESHOLD


def test_a_negation_in_the_main_sentence_keeps_its_trigger_words():
    description = "Fixes CSV files that do not parse. Use when asked."
    assert ev.match_score("fix csv files", "tool", description) >= ev.MATCH_THRESHOLD


def test_vague_proposal_suggests_whole_requests_not_a_keyword_list():
    spec = SkillSpec(
        name="general-helper",
        description="Helps.",
        triggers=["Remove duplicate rows from a spreadsheet"],
    )
    cases = ev.generate_cases(spec)
    proposals = {p.area: p for p in ev.propose_improvements(spec, cases, ev.grade_offline(cases, spec))}
    text = proposals["vague-description"].proposal
    assert "trigger words such as" not in text
    assert '"Remove duplicate rows from a spreadsheet"' in text


def test_an_unsupported_input_sentence_is_not_trigger_vocabulary():
    description = "Convert tabular exports. Use it when processing CSV tables. It does not handle PDF documents."
    assert ev.match_score("Handle PDF documents", "tabular", description) < ev.MATCH_THRESHOLD
    assert ev.match_score("Process CSV tables", "tabular", description) >= ev.MATCH_THRESHOLD


def test_a_boundary_clause_keeps_the_positive_clause_before_it():
    description = "Convert spreadsheet exports to CSV; not for PDF documents."
    assert ev.match_score("Convert spreadsheet exports to CSV", "table-tool", description) >= ev.MATCH_THRESHOLD
    assert ev.match_score("Read PDF documents", "table-tool", description) < ev.MATCH_THRESHOLD


def test_refuse_describing_the_problem_is_not_a_boundary():
    description = "Troubleshoot servers that refuse TCP connections."
    assert ev.match_score("Investigate TCP connection failures", "network-debugger", description) >= ev.MATCH_THRESHOLD
    bounded = "Draft release notes. Refuses to invent changes that are not in the input."
    assert ev.match_score("invent changes", "release-notes", bounded) < ev.MATCH_THRESHOLD

