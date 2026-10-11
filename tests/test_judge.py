"""Tests for `skillz judge`: strict verdict parsing, judge choice, reports and the CLI paths.

Only fake runners and fake judges are used; nothing here may start a process.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from skill_factory import evalcases, evaluate, judge, runner
from skill_factory.capture import SpecError
from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
WHICH = "skill_factory.judge.shutil.which"


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("judge must not spawn a process in tests")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(runner.subprocess, "run", boom)


class FakeRunner:
    """Answers `WITH` when the skill text is in the prompt, `WITHOUT` otherwise."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "WITH skill answer" if "<skill>" in prompt else "WITHOUT skill answer"


def _reply(a: tuple[int, int], b: tuple[int, int], winner: str, reason: str = "because") -> str:
    return json.dumps(
        {
            "a": {"instruction_following": a[0], "task_quality": a[1]},
            "b": {"instruction_following": b[0], "task_quality": b[1]},
            "winner": winner,
            "reason": reason,
        }
    )


class FakeJudge:
    """Prefers the response that starts with WITH (4/4 against 2/1) unless told otherwise."""

    def __init__(self, name: str = "judge-fake", reply: str | None = None) -> None:
        self.name = name
        self.reply = reply
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.reply is not None:
            return self.reply
        first = re.search(r"<response_a>\n(.*?)\n</response_a>", prompt, re.DOTALL)
        assert first is not None
        if first.group(1).startswith("WITH skill"):
            return _reply((4, 4), (2, 1), "a", "A follows the rubric")
        return _reply((2, 1), (4, 4), "b", "B follows the rubric")


def _case(case_id: str, **extra) -> dict:
    return {"id": case_id, "kind": "behaviour", "prompt": f"do {case_id}", "expected": ["x"], **extra}


def _ids_for(label: str, count: int = 1) -> list[str]:
    found = []
    for number in range(500):
        case_id = f"beh-{number:03d}"
        if judge.with_skill_label(case_id) == label:
            found.append(case_id)
            if len(found) == count:
                return found
    raise AssertionError("no id found")


def _make(tmp_path: Path) -> Path:
    assert main(["new", str(FIXTURES / "well-described.json"), "--out", str(tmp_path)]) == 0
    return tmp_path / "skills" / "csv-data-cleaner"


def _factory(monkeypatch, runners: dict[str, object]):
    monkeypatch.setattr(judge, "RUNNER_FACTORY", lambda name: runners.get(name))


# --- contract constants and source hygiene ------------------------------------------------------


def test_constants_follow_the_contract():
    assert judge.SKIPPED_NO_RUNNER == runner.SKIPPED_NO_RUNNER == "skipped: no runner"
    assert judge.SKIPPED_NO_JUDGE == "skipped: no judge"
    assert judge.DIMENSIONS == ("instruction_following", "task_quality")
    assert judge.TRANSCRIPT_LIMIT == 4000
    assert issubclass(judge.JudgeError, ValueError)
    assert judge.RUNNER_FACTORY is runner.detect_runner


def test_judge_source_never_mentions_subprocess():
    source = Path(judge.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source


def test_prompt_template_formats_with_all_parts():
    text = judge.JUDGE_PROMPT.format(
        prompt="P-TASK", rubric="- R-CRIT", transcript_a="T-AAA", transcript_b="T-BBB"
    )
    for part in ("P-TASK", "R-CRIT", "T-AAA", "T-BBB", "instruction_following", "task_quality"):
        assert part in text
    assert '"winner"' in text and "only a JSON object" in text


# --- with_skill_label ---------------------------------------------------------------------------


def test_with_skill_label_is_deterministic_and_uses_both_labels():
    ids = [f"beh-{n:03d}" for n in range(40)]
    labels = [judge.with_skill_label(i) for i in ids]
    assert labels == [judge.with_skill_label(i) for i in ids]
    assert set(labels) == {"a", "b"}


# --- parse_verdict ------------------------------------------------------------------------------


def test_parse_verdict_accepts_plain_and_fenced_json():
    reply = _reply((5, 4), (1, 0), "a", "  clearer  ")
    expected = {
        "a": {"instruction_following": 5, "task_quality": 4},
        "b": {"instruction_following": 1, "task_quality": 0},
        "winner": "a",
        "reason": "clearer",
    }
    assert judge.parse_verdict(reply) == expected
    assert judge.parse_verdict(f"Here you go:\n```json\n{reply}\n```\nHope that helps") == expected
    assert judge.parse_verdict(f"{{not json}} then {reply}") == expected


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("I think A is better.", "contains no JSON object"),
        ("", "contains no JSON object"),
        ('{"a": {"instruction_following": 1,', "not valid JSON"),
        ("{oops}", "not valid JSON"),
        ('{"b": {}, "winner": "a", "reason": "r"}', "a is missing"),
        ('{"a": 3, "b": {}, "winner": "a", "reason": "r"}', "a must be an object"),
        (
            '{"a": {"instruction_following": 1}, "b": {}, "winner": "a", "reason": "r"}',
            "a.task_quality is missing",
        ),
    ],
)
def test_parse_verdict_rejects_unusable_replies(text, fragment):
    with pytest.raises(judge.JudgeError, match=re.escape(fragment)):
        judge.parse_verdict(text)


def _with(**overrides) -> str:
    data = json.loads(_reply((3, 3), (2, 2), "a"))
    for key, value in overrides.items():
        if "__" in key:
            outer, inner = key.split("__")
            data[outer][inner] = value
        elif value is None:
            data.pop(key)
        else:
            data[key] = value
    return json.dumps(data)


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"a__task_quality": 7}, "a.task_quality must be an integer 0-5, got 7"),
        ({"b__instruction_following": -1}, "b.instruction_following must be an integer 0-5, got -1"),
        ({"a__task_quality": 2.5}, "a.task_quality must be an integer 0-5, got 2.5"),
        ({"a__task_quality": "4"}, "a.task_quality must be an integer 0-5, got '4'"),
        ({"b__task_quality": True}, "b.task_quality must be an integer 0-5, got True"),
        ({"winner": "c"}, "winner must be 'a', 'b' or 'tie', got 'c'"),
        ({"winner": None}, "winner is missing"),
        ({"reason": ""}, "reason must be a non-empty string"),
        ({"reason": "   "}, "reason must be a non-empty string"),
        ({"reason": None}, "reason must be a non-empty string"),
        ({"reason": 5}, "reason must be a non-empty string"),
    ],
)
def test_parse_verdict_names_the_bad_field(overrides, fragment):
    with pytest.raises(judge.JudgeError) as excinfo:
        judge.parse_verdict(_with(**overrides))
    assert fragment in str(excinfo.value)
    assert str(excinfo.value).startswith("judge reply: ")


def test_parse_verdict_accepts_tie_and_boundary_scores():
    verdict = judge.parse_verdict(_reply((0, 5), (5, 0), "tie"))
    assert verdict["winner"] == "tie"
    assert verdict["a"] == {"instruction_following": 0, "task_quality": 5}


# --- choose_judge and resolve_runners -----------------------------------------------------------


def test_choose_judge_is_cross_model_when_both_executables_exist(monkeypatch):
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    assert judge.choose_judge("claude") == "codex"
    assert judge.choose_judge("codex") == "claude"


@pytest.mark.parametrize("only", ["claude", "codex"])
def test_choose_judge_is_same_model_when_one_executable_exists(monkeypatch, only):
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}" if exe == only else None)
    assert judge.choose_judge("claude") == "claude"
    assert judge.choose_judge("codex") == "codex"


def test_choose_judge_explicit_wins(monkeypatch):
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    assert judge.choose_judge("claude", "claude") == "claude"
    monkeypatch.setattr(WHICH, lambda exe: None)
    assert judge.choose_judge("claude", "codex") == "codex"


def test_resolve_runners_paths(monkeypatch):
    claude, codex = FakeRunner("claude"), FakeRunner("codex")
    factory = {"claude": claude, "codex": codex}.get
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    assert judge.resolve_runners("claude", None, factory) == (claude, codex, None)
    assert judge.resolve_runners("claude", "claude", factory) == (claude, claude, None)
    monkeypatch.setattr(WHICH, lambda exe: None)
    got, judge_runner, skip = judge.resolve_runners("claude", None, factory)
    assert got is claude and judge_runner is claude and skip is None
    assert judge.resolve_runners(None, None, factory) == (None, None, judge.SKIPPED_NO_RUNNER)
    assert judge.resolve_runners("codex", None, {}.get) == (None, None, judge.SKIPPED_NO_RUNNER)
    only_claude = {"claude": claude}.get
    assert judge.resolve_runners("claude", "codex", only_claude) == (
        claude,
        None,
        judge.SKIPPED_NO_JUDGE,
    )


def test_resolve_runners_uses_the_patched_factory_at_call_time(monkeypatch):
    fake = FakeRunner("claude")
    monkeypatch.setattr(WHICH, lambda exe: None)
    _factory(monkeypatch, {"claude": fake})
    assert judge.resolve_runners("claude") == (fake, fake, None)


# --- judge_cases --------------------------------------------------------------------------------


def test_judge_cases_maps_scores_labels_and_winners():
    a_id, b_id = _ids_for("a")[0], _ids_for("b")[0]
    cases = [
        _case(a_id, source="behaviour:0", rubric=["Asks first", "mentions: x"]),
        _case(b_id),
        {"id": "pos-001", "kind": "positive", "prompt": "ignored", "expected": []},
    ]
    runner_, judge_ = FakeRunner(), FakeJudge()
    report = judge.judge_cases(runner_, judge_, cases, "SKILL TEXT")
    assert report["status"] == "completed"
    assert (report["runner"], report["judge"], report["cross_model"]) == ("fake", "judge-fake", True)
    assert report["summary"] == {
        "cases": 2, "skill_better": 2, "skill_worse": 0, "tie": 0, "errors": 0,
    }
    first, second = report["cases"]
    assert [first["id"], second["id"]] == [a_id, b_id]
    assert first["with_skill_label"] == "a" and second["with_skill_label"] == "b"
    assert first["source"] == "behaviour:0" and "source" not in second
    assert first["rubric"] == ["Asks first", "mentions: x"]
    assert second["rubric"] == ["mentions: x"]  # fallback when the case has no rubric
    for result in (first, second):
        assert result["with_skill"] == {"instruction_following": 4, "task_quality": 4, "total": 8}
        assert result["without_skill"] == {"instruction_following": 2, "task_quality": 1, "total": 3}
        assert result["winner"] == "with-skill" and result["verdict"] == "skill-better"
        assert result["score_delta"] == 5
        assert result["reason"].endswith("follows the rubric")
        assert result["transcripts"] == {
            "with_skill": "WITH skill answer",
            "without_skill": "WITHOUT skill answer",
        }
    # Two runner calls per case (with and without the skill), one judge call per case.
    assert len(runner_.prompts) == 4 and len(judge_.prompts) == 2
    assert "<skill>\nSKILL TEXT\n</skill>" in runner_.prompts[0]
    assert runner_.prompts[1] == f"do {a_id}"
    assert "- Asks first\n- mentions: x" in judge_.prompts[0]


def test_judge_cases_maps_without_skill_winner_and_tie():
    cases = [_case("beh-001"), _case("beh-002")]
    label = judge.with_skill_label("beh-001")
    other = "b" if label == "a" else "a"
    scores = {label: (1, 1), other: (3, 3)}  # the without-skill side wins
    worse = _reply(scores["a"], scores["b"], other, "skill hurt")
    replies = iter([worse, _reply((3, 3), (3, 3), "tie", "equal")])

    class Scripted:
        name = "scripted"

        def run(self, prompt: str) -> str:
            return next(replies)

    report = judge.judge_cases(FakeRunner(), Scripted(), cases, "S")
    worse_case, tie_case = report["cases"]
    assert worse_case["winner"] == "without-skill" and worse_case["verdict"] == "skill-worse"
    assert worse_case["score_delta"] == -4
    assert tie_case["winner"] == "tie" and tie_case["verdict"] == "tie"
    assert tie_case["score_delta"] == 0
    assert report["summary"] == {
        "cases": 2, "skill_better": 0, "skill_worse": 1, "tie": 1, "errors": 0,
    }


def test_judge_cases_both_labels_occur_and_fake_judge_sees_blind_transcripts():
    ids = _ids_for("a", 2) + _ids_for("b", 2)
    judge_ = FakeJudge()
    report = judge.judge_cases(FakeRunner(), judge_, [_case(i) for i in ids], "S")
    assert {c["with_skill_label"] for c in report["cases"]} == {"a", "b"}
    assert all(c["verdict"] == "skill-better" for c in report["cases"])
    for prompt in judge_.prompts:
        assert "with skill" not in prompt.lower().split("<response_a>")[0]


def test_judge_cases_truncates_transcripts():
    class Chatty(FakeRunner):
        def run(self, prompt: str) -> str:
            return "x" * (judge.TRANSCRIPT_LIMIT + 500)

    report = judge.judge_cases(Chatty(), FakeJudge(reply=_reply((1, 1), (1, 1), "tie")), [_case("c")], "S")
    transcripts = report["cases"][0]["transcripts"]
    assert len(transcripts["with_skill"]) == judge.TRANSCRIPT_LIMIT
    assert len(transcripts["without_skill"]) == judge.TRANSCRIPT_LIMIT


@pytest.mark.parametrize(
    "reply, fragment",
    [
        ("A is better", "contains no JSON object"),
        (_reply((9, 1), (1, 1), "a"), "a.instruction_following must be an integer 0-5, got 9"),
    ],
)
def test_malformed_judge_reply_is_a_per_case_error(reply, fragment):
    report = judge.judge_cases(FakeRunner(), FakeJudge(reply=reply), [_case("c1"), _case("c2")], "S")
    assert report["status"] == "completed"
    assert report["summary"] == {
        "cases": 2, "skill_better": 0, "skill_worse": 0, "tie": 0, "errors": 2,
    }
    for entry in report["cases"]:
        assert set(entry) == {"id", "prompt", "verdict", "error"}
        assert entry["verdict"] == "error" and fragment in entry["error"]


def test_runner_and_judge_failures_are_per_case_errors():
    class Failing(FakeRunner):
        def run(self, prompt: str) -> str:
            raise runner.RunnerError("claude timed out")

    report = judge.judge_cases(Failing(), FakeJudge(), [_case("c1")], "S")
    assert report["cases"][0]["error"] == "claude timed out"

    class FailingJudge(FakeJudge):
        def run(self, prompt: str) -> str:
            raise runner.RunnerError("judge exited with status 1")

    report = judge.judge_cases(FakeRunner(), FailingJudge(), [_case("c1")], "S")
    assert report["cases"][0]["verdict"] == "error"
    assert report["summary"]["errors"] == 1


# --- summary_line and skipped_judge -------------------------------------------------------------


def test_skipped_judge_shape():
    report = judge.skipped_judge()
    assert report["status"] == "skipped: no runner"
    assert report["runner"] is None and report["judge"] is None and report["cross_model"] is False
    assert report["requested"] == {"runner": None, "judge": None}
    assert report["cases"] == []
    assert report["dimensions"] == ["instruction_following", "task_quality"]
    assert report["scale"] == [0, 5] and report["schema"] == 1
    assert report["summary"] == {"cases": 0, "skill_better": 0, "skill_worse": 0, "tie": 0, "errors": 0}
    named = judge.skipped_judge(judge.SKIPPED_NO_JUDGE, "claude", "codex")
    assert named["status"] == "skipped: no judge"
    assert named["requested"] == {"runner": "claude", "judge": "codex"}


def test_summary_line_names_models_and_counts():
    cross = judge.judge_cases(FakeRunner("claude"), FakeJudge("codex"), [_case("c1")], "S")
    line = judge.summary_line(cross)
    assert "\n" not in line
    assert "runner claude" in line and "judge codex" in line and "cross-model" in line
    assert "skill better 1" in line and "errors 0" in line
    same = judge.judge_cases(FakeRunner("claude"), FakeJudge("claude"), [_case("c1")], "S")
    assert "same-model" in judge.summary_line(same)
    assert judge.summary_line(judge.skipped_judge()).startswith("skipped: no runner")


# --- judge_skill and the CLI --------------------------------------------------------------------


def _read_json(skill_dir: Path) -> dict:
    return json.loads((skill_dir / ".factory" / "judge.json").read_text(encoding="utf-8"))


def _behaviour_count(skill_dir: Path) -> int:
    spec = evaluate.load_spec(skill_dir)
    return sum(1 for c in evalcases.generate_cases(spec) if c.kind == "behaviour")


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["judge", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--runner" in out and "--judge" in out and "--out" in out


def test_cli_without_runner_is_skipped_and_exits_zero(tmp_path, capsys):
    skill_dir = _make(tmp_path)
    assert main(["judge", "csv-data-cleaner", "--out", str(tmp_path)]) == 0
    report = _read_json(skill_dir)
    assert report["status"] == "skipped: no runner"
    assert report["skill"] == "csv-data-cleaner"
    assert report["runner"] is None and report["judge"] is None and report["cross_model"] is False
    assert report["requested"] == {"runner": None, "judge": None}
    assert report["cases"] == []
    markdown = (skill_dir / ".factory" / "judge.md").read_text(encoding="utf-8")
    assert "skipped: no runner" in markdown
    assert "skipped: no runner" in capsys.readouterr().out


def test_cli_runner_not_on_path_is_skipped(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path)
    monkeypatch.setattr("skill_factory.runner.shutil.which", lambda _exe: None)
    monkeypatch.setattr(WHICH, lambda _exe: None)
    assert main(["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]) == 0
    report = _read_json(skill_dir)
    assert report["status"] == "skipped: no runner"
    assert report["requested"] == {"runner": "claude", "judge": None}


def test_cli_judge_not_on_path_is_skipped_no_judge(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path)
    _factory(monkeypatch, {"claude": FakeRunner("claude")})
    monkeypatch.setattr(WHICH, lambda _exe: None)
    args = ["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude", "--judge", "codex"]
    assert main(args) == 0
    report = _read_json(skill_dir)
    assert report["status"] == "skipped: no judge"
    assert report["runner"] == "claude" and report["judge"] is None
    assert report["requested"] == {"runner": "claude", "judge": "codex"}


def test_cli_with_fakes_writes_scores_for_both_sides(tmp_path, monkeypatch, capsys):
    skill_dir = _make(tmp_path)
    runner_, judge_ = FakeRunner("claude"), FakeJudge("codex")
    _factory(monkeypatch, {"claude": runner_, "codex": judge_})
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    assert main(["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]) == 0
    report = _read_json(skill_dir)
    total = _behaviour_count(skill_dir)
    assert report["schema"] == 1 and report["status"] == "completed"
    assert (report["runner"], report["judge"], report["cross_model"]) == ("claude", "codex", True)
    assert report["requested"] == {"runner": "claude", "judge": None}
    assert report["dimensions"] == ["instruction_following", "task_quality"]
    assert report["scale"] == [0, 5]
    assert report["summary"]["cases"] == total >= 2
    assert report["summary"]["skill_better"] == total and report["summary"]["errors"] == 0
    assert len(report["cases"]) == total
    for case in report["cases"]:
        assert case["with_skill_label"] == judge.with_skill_label(case["id"])
        assert case["with_skill"]["total"] == 8 and case["without_skill"]["total"] == 3
        assert case["winner"] == "with-skill" and case["score_delta"] == 5
        assert case["rubric"] and case["reason"] and case["source"]
        assert set(case["transcripts"]) == {"with_skill", "without_skill"}
    assert len(runner_.prompts) == 2 * total and len(judge_.prompts) == total
    raw = (skill_dir / ".factory" / "judge.json").read_text(encoding="utf-8")
    assert raw.endswith("\n") and "\r" not in raw and '"skill": "csv-data-cleaner"' in raw
    markdown = (skill_dir / ".factory" / "judge.md").read_text(encoding="utf-8")
    for needle in ("Runner: claude", "Judge: codex", "cross-model", "with skill:", "without skill:",
                   "score delta +5", "reason:", report["cases"][0]["id"]):
        assert needle in markdown
    out = capsys.readouterr().out
    assert "cross-model" in out and "judge.json" in out


def test_same_model_judge_is_recorded(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path)
    monkeypatch.setattr(WHICH, lambda exe: "/bin/claude" if exe == "claude" else None)

    # The same object runs the cases and judges them, so it replies with a fixed tie verdict.
    class SelfJudge(FakeRunner):
        def run(self, prompt: str) -> str:
            if "<response_a>" in prompt:
                return _reply((3, 3), (3, 3), "tie", "equal")
            return super().run(prompt)

    _factory(monkeypatch, {"claude": SelfJudge("claude")})
    assert main(["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]) == 0
    report = _read_json(skill_dir)
    assert report["judge"] == "claude" and report["cross_model"] is False
    assert report["summary"]["tie"] == report["summary"]["cases"]
    markdown = (skill_dir / ".factory" / "judge.md").read_text(encoding="utf-8")
    assert "Judge: claude" in markdown and "same-model" in markdown


def test_cli_malformed_judge_reply_is_per_case_error_with_exit_zero(tmp_path, monkeypatch, capsys):
    skill_dir = _make(tmp_path)
    _factory(monkeypatch, {"claude": FakeRunner("claude"), "codex": FakeJudge("codex", reply="no idea")})
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    assert main(["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]) == 0
    report = _read_json(skill_dir)
    total = _behaviour_count(skill_dir)
    assert report["summary"]["errors"] == total
    assert all(c["verdict"] == "error" and "no JSON object" in c["error"] for c in report["cases"])
    markdown = (skill_dir / ".factory" / "judge.md").read_text(encoding="utf-8")
    assert "## Errors" in markdown and "no JSON object" in markdown
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err


def test_judge_output_is_deterministic(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path)
    _factory(monkeypatch, {"claude": FakeRunner("claude"), "codex": FakeJudge("codex")})
    monkeypatch.setattr(WHICH, lambda exe: f"/bin/{exe}")
    args = ["judge", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]
    assert main(args) == 0
    first = [(skill_dir / ".factory" / f).read_bytes() for f in ("judge.json", "judge.md")]
    assert main(args) == 0
    assert first == [(skill_dir / ".factory" / f).read_bytes() for f in ("judge.json", "judge.md")]


def test_judge_skill_runner_factory_argument_wins(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path)
    monkeypatch.setattr(judge, "RUNNER_FACTORY", lambda name: pytest.fail("global factory used"))
    monkeypatch.setattr(WHICH, lambda exe: None)

    class SelfJudge(FakeRunner):
        def run(self, prompt: str) -> str:
            return _reply((1, 1), (2, 2), "b", "r") if "<response_a>" in prompt else "t"

    fake = SelfJudge("claude")
    report = judge.judge_skill(skill_dir, "claude", runner_factory={"claude": fake}.get)
    assert report["status"] == "completed" and report["judge"] == "claude"


def test_missing_skill_exits_two(tmp_path, capsys):
    assert main(["judge", "no-such-skill", "--out", str(tmp_path)]) == 2
    assert "skill directory not found" in capsys.readouterr().err


def test_invalid_name_exits_two(tmp_path, capsys):
    assert main(["judge", "Bad_Name", "--out", str(tmp_path)]) == 2
    assert "invalid skill name" in capsys.readouterr().err


def test_missing_skill_md_exits_two(tmp_path, capsys):
    (tmp_path / "skills" / "empty-skill").mkdir(parents=True)
    assert main(["judge", "empty-skill", "--out", str(tmp_path)]) == 2
    assert "SKILL.md not found" in capsys.readouterr().err
    with pytest.raises(SpecError):
        judge.judge_skill(tmp_path / "skills" / "empty-skill")


def test_symlinked_factory_is_refused(tmp_path):
    skill_dir = _make(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (skill_dir / ".factory" / "spec.json").rename(target / "spec.json")
    (skill_dir / ".factory").rmdir()
    try:
        (skill_dir / ".factory").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(SpecError, match="symlink"):
        judge.judge_skill(skill_dir)
    assert not (target / "judge.json").exists()


def test_real_runners_are_never_executed(monkeypatch):
    def boom(self, prompt):
        raise AssertionError("a real runner was executed")

    monkeypatch.setattr(runner.ClaudeRunner, "run", boom)
    monkeypatch.setattr(runner.CodexRunner, "run", boom)
    monkeypatch.setattr(WHICH, lambda exe: None)
    monkeypatch.setattr("skill_factory.runner.shutil.which", lambda exe: None)
    assert judge.resolve_runners("claude") == (None, None, judge.SKIPPED_NO_RUNNER)
