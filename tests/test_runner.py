"""Runner tests use fakes only: the real claude and codex must never run."""

from __future__ import annotations

import subprocess

import pytest

from skill_factory import runner as runner_mod
from skill_factory.runner import (
    DEFAULT_TIMEOUT,
    SKIPPED_NO_RUNNER,
    ClaudeRunner,
    CodexRunner,
    RunnerError,
    detect_runner,
    grade_markers,
    run_baseline,
    skipped_baseline,
    with_skill_prompt,
)

SKILL = "# CSV cleaner\nAlways dedupe rows and report a summary."


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(
            "subprocess.run must not be called for real in runner tests"
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", boom)


def _record(monkeypatch, *, returncode=0, stdout="out", stderr="", raises=None):
    calls = []

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(
            argv, returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", fake)
    return calls


def test_detect_runner_none_does_not_look_up_which(monkeypatch):
    def fail(_):
        raise AssertionError("which must not be called")

    monkeypatch.setattr(runner_mod.shutil, "which", fail)
    assert detect_runner(None) is None


@pytest.mark.parametrize("name", ["claude", "codex"])
def test_detect_runner_missing_executable_is_none(monkeypatch, name):
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: None)
    assert detect_runner(name) is None


def test_detect_runner_looks_up_which_at_call_time(monkeypatch):
    seen = []
    monkeypatch.setattr(
        runner_mod.shutil, "which", lambda exe: seen.append(exe) or None
    )
    detect_runner("codex")
    assert seen == ["codex"]


def test_detect_runner_builds_claude_and_codex(monkeypatch):
    monkeypatch.setattr(runner_mod.shutil, "which", lambda exe: f"/fake/bin/{exe}")
    claude = detect_runner("claude", timeout=5)
    codex = detect_runner("codex")
    assert isinstance(claude, ClaudeRunner)
    assert claude.name == "claude"
    assert claude.timeout == 5
    assert claude.argv("hi") == [
        "/fake/bin/claude", "-p", "--output-format", "text", "--", "hi"
    ]
    assert isinstance(codex, CodexRunner)
    assert codex.name == "codex"
    assert codex.timeout == DEFAULT_TIMEOUT
    assert codex.argv("hi") == ["/fake/bin/codex", "exec", "--", "hi"]


def test_detect_runner_unknown_name_raises():
    with pytest.raises(ValueError):
        detect_runner("other")


@pytest.mark.parametrize("cls", [ClaudeRunner, CodexRunner])
def test_run_uses_list_argv_timeout_and_no_shell(monkeypatch, cls):
    calls = _record(monkeypatch, stdout="hello")
    runner = cls("/x/tool", timeout=7)
    assert runner.run("do it") == "hello"
    argv, kwargs = calls[0]
    assert isinstance(argv, list) and "do it" in argv
    assert kwargs["timeout"] == 7
    assert not kwargs.get("shell", False)
    assert kwargs["capture_output"] is True and kwargs["text"] is True


@pytest.mark.parametrize("cls", [ClaudeRunner, CodexRunner])
def test_dash_prompt_is_passed_verbatim_after_double_dash(monkeypatch, cls):
    calls = _record(monkeypatch, stdout="ok")
    cls("/x/tool").run("--dangerously-skip-permissions")
    argv, _ = calls[0]
    assert argv[-2:] == ["--", "--dangerously-skip-permissions"]
    assert argv.count("--dangerously-skip-permissions") == 1


@pytest.mark.parametrize("cls", [ClaudeRunner, CodexRunner])
def test_run_failures_become_runner_error(monkeypatch, cls):
    _record(monkeypatch, returncode=1, stderr="boom")
    with pytest.raises(RunnerError, match="status 1"):
        cls("/x/tool").run("p")
    _record(monkeypatch, raises=subprocess.TimeoutExpired(cmd="x", timeout=1))
    with pytest.raises(RunnerError, match="timed out"):
        cls("/x/tool").run("p")
    _record(monkeypatch, raises=FileNotFoundError("gone"))
    with pytest.raises(RunnerError, match="cannot run"):
        cls("/x/tool").run("p")


def test_skipped_baseline_literal():
    assert SKIPPED_NO_RUNNER == "skipped: no runner"
    assert skipped_baseline()["status"] == "skipped: no runner"
    assert skipped_baseline() == {"status": "skipped: no runner"}
    assert skipped_baseline("claude")["requested"] == "claude"


def test_grade_markers_is_case_insensitive():
    graded = grade_markers(
        "Remove DUPLICATES and Summary", ["duplicates", "summary", "csv"]
    )
    assert graded == {
        "passed": False,
        "found": ["duplicates", "summary"],
        "missing": ["csv"],
    }


def test_grade_markers_edges():
    assert grade_markers("anything", []) == {"passed": True, "found": [], "missing": []}
    assert grade_markers("A B", ["a", "b"])["passed"] is True


class FakeRunner:
    name = "fake"

    def __init__(self):
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        if SKILL in prompt:
            return "I will DEDUPE the rows and write a summary."
        return "Sure, here is some text."


CASES = [
    {"id": "pos-001", "kind": "positive", "prompt": "clean csv", "expected": []},
    {"id": "neg-001", "kind": "negative", "prompt": "weather", "expected": []},
    {
        "id": "beh-001",
        "kind": "behaviour",
        "prompt": "Clean this file",
        "expected": ["dedupe", "summary"],
    },
    {
        "id": "beh-002",
        "kind": "behaviour",
        "prompt": "Tidy rows",
        "expected": ["dedupe"],
    },
]


def test_run_baseline_with_and_without_skill():
    fake = FakeRunner()
    result = run_baseline(fake, CASES, SKILL)
    assert result["status"] == "completed"
    assert result["runner"] == "fake"
    assert [c["id"] for c in result["cases"]] == ["beh-001", "beh-002"]
    assert all(c["verdict"] == "skill-helps" for c in result["cases"])
    first = result["cases"][0]
    assert first["with_skill"]["passed"] is True
    assert first["without_skill"]["missing"] == ["dedupe", "summary"]
    assert result["summary"] == {
        "skill_helps": 2,
        "no_difference": 0,
        "skill_hurts": 0,
        "errors": 0,
    }
    # two behaviour cases, each run exactly twice; non-behaviour cases never run
    assert len(fake.prompts) == 4
    assert sum(SKILL in p for p in fake.prompts) == 2
    assert "Clean this file" in fake.prompts[1] and fake.prompts[1] == "Clean this file"
    assert not any("weather" in p or "clean csv" in p for p in fake.prompts)


def test_run_baseline_no_difference_and_hurts():
    class Same:
        name = "same"

        def run(self, prompt):
            return "dedupe"

    class Worse:
        name = "worse"

        def run(self, prompt):
            return "bare" if SKILL in prompt else "dedupe"

    same = run_baseline(Same(), CASES, SKILL)
    assert same["summary"]["no_difference"] == 2
    worse = run_baseline(Worse(), CASES, SKILL)
    assert worse["summary"]["skill_hurts"] == 2
    assert worse["cases"][0]["verdict"] == "skill-hurts"


def test_run_baseline_records_runner_error():
    class Broken:
        name = "broken"

        def run(self, prompt):
            raise RunnerError("model unavailable")

    result = run_baseline(Broken(), CASES, SKILL)
    assert result["status"] == "completed"
    assert [c["verdict"] for c in result["cases"]] == ["error", "error"]
    assert result["cases"][0]["error"] == "model unavailable"
    assert result["summary"]["errors"] == 2


def test_with_skill_prompt_is_deterministic_and_contains_both():
    a = with_skill_prompt(SKILL, "task")
    assert a == with_skill_prompt(SKILL, "task")
    assert SKILL in a and a.endswith("task")
    assert a.index(SKILL) < a.rindex("task")
