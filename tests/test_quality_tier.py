"""Tests for the model-backed quality tier in challenge, package and run.

Only fake runners and fake judges are used (patched in through the runner factories); nothing
here may start a process.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

from skill_factory import (
    candidates,
    captured,
    challenge,
    cli,
    evaluate,
    improve,
    judge,
    pipeline,
    release,
    runner,
)
from skill_factory.capture import capture_spec_file
from skill_factory.generate import generate
from skill_factory.runner import RunnerError

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "csv-data-cleaner"
MISSED = "Help me tidy up my spreadsheet export before the quarterly review"
OFF_TOPIC = "Design a logo for my bakery"
PARAGRAPH = (FIXTURES / "paragraph.txt").read_text(encoding="utf-8")
WHICH = "skill_factory.judge.shutil.which"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(WHICH, lambda exe: None)  # no real claude or codex, same-model judging

    def boom(*args, **kwargs):
        raise AssertionError("the quality tier must not spawn a process in tests")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(runner.subprocess, "run", boom)


class Fake:
    """Runner and judge in one. With-skill quality comes from a marker in the skill text.

    `[[Q1]]`..`[[Q5]]` in the prompt set the with-skill quality (default 3), `[[ERR]]` makes the
    model call fail; without the skill the quality is 2. A judge prompt is answered by reading
    the `Q=n` of each response, so the verdict follows the markers.
    """

    def __init__(self, name: str = "claude", skill_quality: int | None = None) -> None:
        self.name = name
        self.skill_quality = skill_quality
        self.runs = 0
        self.judgements = 0

    def run(self, prompt: str) -> str:
        if "<response_a>" in prompt:
            self.judgements += 1
            return self._judge(prompt)
        self.runs += 1
        if "<skill>" not in prompt:
            return "Q=2"
        if "[[ERR]]" in prompt:
            raise RunnerError("model call failed")
        if self.skill_quality is not None:
            return f"Q={self.skill_quality}"
        marker = re.search(r"\[\[Q([1-5])\]\]", prompt)
        return f"Q={marker.group(1) if marker else 3}"

    @staticmethod
    def _judge(prompt: str) -> str:
        scores = {}
        for label in ("a", "b"):
            block = re.search(rf"<response_{label}>\n(.*?)\n</response_{label}>", prompt, re.DOTALL)
            scores[label] = int(re.search(r"Q=(\d)", block.group(1)).group(1))
        winner = "tie" if scores["a"] == scores["b"] else max(scores, key=scores.get)
        return json.dumps(
            {
                "a": {"instruction_following": scores["a"], "task_quality": scores["a"]},
                "b": {"instruction_following": scores["b"], "task_quality": scores["b"]},
                "winner": winner,
                "reason": "follows the rubric better",
            }
        )

    @property
    def calls(self) -> int:
        return self.runs + self.judgements


def install(monkeypatch, fake: Fake) -> Fake:
    """Make the fake the only runner anywhere a factory is looked up."""

    def factory(name):
        return fake if name == fake.name else None

    monkeypatch.setattr(judge, "RUNNER_FACTORY", factory)
    monkeypatch.setattr(evaluate, "RUNNER_FACTORY", factory)
    return fake


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- challenge ----------------------------------------------------------------------------------


def make(tmp_path: Path, fixture: str) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return generate(capture_spec_file(FIXTURES / "eval" / fixture), project)


@pytest.fixture
def well(tmp_path):
    return make(tmp_path, "well-described.json")


@pytest.fixture
def vague(tmp_path):
    return make(tmp_path, "vague.json")


def same_text_candidate(skill: Path, marker: str = "") -> str:
    """A candidate that is lexically identical to the stable skill, plus a body marker."""
    captured.append_captured(skill, OFF_TOPIC, "negative")
    cid = improve.improve_skill(skill)["id"]
    if marker:
        md = candidates.candidate_skill_dir(skill, cid) / "SKILL.md"
        with md.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n{marker}\n")
    return cid


def behaviour_rows(report: dict) -> dict[str, dict]:
    return {row["id"]: row for row in report["behaviour"]["cases"]}


def test_challenge_without_a_runner_skips_behaviour_and_keeps_old_verdicts(well):
    cid = same_text_candidate(well)
    report = challenge.challenge_candidate(well, cid)
    assert report["verdict"] == "no-difference"
    assert report["behaviour"] == {
        "status": "skipped: no runner",
        "requested": {"runner": None, "judge": None},
    }
    markdown = (candidates.candidate_dir(well, cid) / "challenge.md").read_text(encoding="utf-8")
    assert "## Behaviour" in markdown and "skipped: no runner" in markdown
    assert "no model was consulted" in markdown


def test_challenge_with_a_missing_runner_or_judge_records_why(well):
    cid = same_text_candidate(well)
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda name: None)
    assert report["behaviour"]["status"] == "skipped: no runner"
    assert report["behaviour"]["requested"] == {"runner": "claude", "judge": None}
    fake = Fake("claude")
    report = challenge.challenge_candidate(
        well, cid, "claude", "codex", runner_factory=lambda name: fake if name == "claude" else None
    )
    assert report["behaviour"]["status"] == "skipped: no judge"
    assert report["verdict"] == "no-difference"
    assert fake.calls == 0


def test_a_lower_candidate_score_is_a_regression_and_candidate_worse(well):
    cid = same_text_candidate(well, "[[Q1]]")
    fake = Fake()
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda n: fake)
    assert report["verdict"] == "candidate-worse"
    behaviour = report["behaviour"]
    assert behaviour["status"] == "completed"
    assert behaviour["runner"] == "claude" and behaviour["judge"] == "claude"
    assert behaviour["cross_model"] is False
    assert behaviour["summary"]["cases"] > 0
    assert behaviour["summary"]["regressions"] == behaviour["summary"]["cases"]
    assert behaviour["summary"]["improvements"] == 0 and behaviour["summary"]["errors"] == 0
    assert behaviour["regressions"] == [row["id"] for row in behaviour["cases"]]
    for row in behaviour["cases"]:
        assert set(row) == {"id", "prompt", "stable_total", "candidate_total", "change"}
        assert (row["stable_total"], row["candidate_total"]) == (6, 2)
        assert row["change"] == "regression"
    assert report["regressions"] == []  # the lexical tier saw nothing wrong
    # both skill texts are judged on the same cases by the same judge
    assert fake.judgements == 2 * behaviour["summary"]["cases"]
    on_disk = read_json(candidates.candidate_dir(well, cid) / "challenge.json")
    assert on_disk["behaviour"] == behaviour
    markdown = (candidates.candidate_dir(well, cid) / "challenge.md").read_text(encoding="utf-8")
    assert "## Behaviour" in markdown and "regression" in markdown
    assert "no model was consulted" not in markdown


def test_a_higher_candidate_score_is_an_improvement_and_candidate_better(well):
    cid = same_text_candidate(well, "[[Q5]]")
    fake = Fake()
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda n: fake)
    assert report["verdict"] == "candidate-better"
    behaviour = report["behaviour"]
    assert behaviour["summary"]["improvements"] == behaviour["summary"]["cases"] > 0
    assert behaviour["regressions"] == []
    assert behaviour["improvements"] == [row["id"] for row in behaviour["cases"]]
    assert all((r["stable_total"], r["candidate_total"]) == (6, 10) for r in behaviour["cases"])
    assert report["regressions"] == [] and report["improvements"] == []


def test_equal_scores_are_unchanged_and_no_difference(well):
    cid = same_text_candidate(well, "[[Q3]]")
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda n: Fake())
    assert report["verdict"] == "no-difference"
    behaviour = report["behaviour"]
    assert behaviour["summary"]["unchanged"] == behaviour["summary"]["cases"] > 0
    assert all(row["change"] == "unchanged" for row in behaviour["cases"])


def test_a_behaviour_regression_outweighs_a_trigger_improvement(vague):
    captured.append_captured(vague, MISSED, "positive")
    cid = improve.improve_skill(vague)["id"]
    md = candidates.candidate_skill_dir(vague, cid) / "SKILL.md"
    with md.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n[[Q1]]\n")
    lexical = challenge.challenge_candidate(vague, cid)
    assert lexical["verdict"] == "candidate-better"
    report = challenge.challenge_candidate(vague, cid, "claude", runner_factory=lambda n: Fake())
    assert report["improvements"] and report["behaviour"]["regressions"]
    assert report["verdict"] == "candidate-worse"


def test_a_candidate_that_fails_validation_stays_unsafe(well):
    cid = same_text_candidate(well, "[[Q5]]")
    md = candidates.candidate_skill_dir(well, cid) / "SKILL.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace("name: csv-data-cleaner", "name: other", 1),
        encoding="utf-8",
    )
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda n: Fake())
    assert report["behaviour"]["summary"]["improvements"] > 0
    assert report["verdict"] == "candidate-unsafe"


def test_a_failed_side_is_an_error_and_ignored_by_the_verdict(well):
    cid = same_text_candidate(well, "[[ERR]]")
    report = challenge.challenge_candidate(well, cid, "claude", runner_factory=lambda n: Fake())
    behaviour = report["behaviour"]
    assert behaviour["summary"]["errors"] == behaviour["summary"]["cases"] > 0
    assert all(row["change"] == "error" for row in behaviour["cases"])
    assert all(row["candidate_total"] is None and row["stable_total"] == 6 for row in behaviour["cases"])
    assert behaviour["regressions"] == [] and behaviour["improvements"] == []
    assert report["verdict"] == "no-difference"


def test_challenge_cli_takes_runner_and_judge(well, monkeypatch, capsys):
    cid = same_text_candidate(well, "[[Q1]]")
    install(monkeypatch, Fake())
    code = cli.main(
        ["challenge", NAME, cid, "--runner", "claude", "--judge", "claude", "--out", str(well.parent.parent)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert f"challenge {NAME} {cid}: candidate-worse" in out
    assert "behaviour: completed" in out
    assert read_json(candidates.candidate_dir(well, cid) / "challenge.json")["behaviour"]["judge"] == "claude"


def test_challenge_cli_without_a_runner_exits_0_with_the_old_output(well, capsys):
    cid = same_text_candidate(well)
    assert cli.main(["challenge", NAME, cid, "--out", str(well.parent.parent)]) == 0
    out = capsys.readouterr().out
    assert f"challenge {NAME} {cid}: no-difference" in out and "behaviour:" not in out


# --- package and regression ---------------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), project)
    return project


def skill_dir(root: Path) -> Path:
    return root / "skills" / NAME


def versions_dir(root: Path) -> Path:
    return skill_dir(root) / ".factory" / "versions"


def mark(root: Path, marker: str) -> None:
    """Replace the quality marker line at the end of SKILL.md."""
    md = skill_dir(root) / "SKILL.md"
    text = re.sub(r"\n\[\[Q\d\]\]\n$", "\n", md.read_text(encoding="utf-8"))
    md.write_text(f"{text}\n{marker}\n", encoding="utf-8", newline="\n")


def package(root: Path, *argv: str) -> int:
    return cli.main(["package", NAME, *argv, "--out", str(root)])


def approve(root: Path, version: str) -> None:
    assert cli.main(["approve", NAME, version, "--out", str(root)]) == 0


def test_package_with_a_runner_stores_the_judge_report_in_the_version(root, monkeypatch):
    fake = install(monkeypatch, Fake())
    assert package(root, "--runner", "claude") == 0
    version = versions_dir(root) / "0.1.0"
    stored = read_json(version / "reports" / "judge.json")
    assert stored["status"] == "completed" and stored["runner"] == "claude"
    assert stored["skill"] == NAME and stored["summary"]["cases"] > 0
    assert (version / "reports" / "judge.md").is_file()
    assert (version / "reports" / "judge.json").read_bytes() == (
        skill_dir(root) / ".factory" / "judge.json"
    ).read_bytes()
    manifest = read_json(version / "manifest.json")
    paths = [item["path"] for item in manifest["files"]]
    assert "reports/judge.json" in paths and "reports/judge.md" in paths
    assert release.verify_version(version) == []
    assert fake.judgements == stored["summary"]["cases"]  # judged once, not twice
    regression = read_json(version / "regression.json")
    assert regression["status"] == "no-baseline"
    assert regression["behaviour"]["status"] == "skipped: no baseline judge"


def test_package_without_a_runner_has_no_judge_report_even_if_a_stale_one_exists(root, monkeypatch):
    install(monkeypatch, Fake())
    assert package(root, "--runner", "claude") == 0
    assert (skill_dir(root) / ".factory" / "judge.json").is_file()
    assert package(root) == 0
    version = versions_dir(root) / "0.1.1"
    assert not (version / "reports" / "judge.json").exists()
    assert not (version / "reports" / "judge.md").exists()
    assert read_json(version / "regression.json")["behaviour"]["status"] == "skipped: no runner"
    assert release.verify_version(version) == []


def test_package_with_a_missing_runner_stores_the_skipped_report(root):
    assert package(root, "--runner", "claude") == 0  # nothing on PATH: factories find nothing
    stored = read_json(versions_dir(root) / "0.1.0" / "reports" / "judge.json")
    assert stored["status"] == "skipped: no runner"
    assert stored["requested"] == {"runner": "claude", "judge": None}


def test_a_lower_score_than_the_approved_version_is_a_behaviour_regression(root, monkeypatch, capsys):
    install(monkeypatch, Fake())
    mark(root, "[[Q5]]")
    assert package(root, "--runner", "claude") == 0
    approve(root, "0.1.0")
    mark(root, "[[Q2]]")
    capsys.readouterr()
    assert package(root, "--runner", "claude") == 1
    assert "now fail" in capsys.readouterr().err
    assert not (versions_dir(root) / "0.1.1").exists()
    regression = read_json(skill_dir(root) / ".factory" / "regression.json")
    assert regression["status"] == "regressed" and regression["baseline"] == "0.1.0"
    assert regression["behaviour"]["status"] == "compared"
    assert regression["behaviour"]["baseline_judge"] == "claude"
    failing = regression["newly_failing"]
    assert failing and regression["behaviour"]["checked"] == len(failing)
    for item in failing:
        assert item["kind"] == "behaviour" and item["id"].startswith("beh-")
        assert (item["before"], item["after"], item["score"]) == (10, 4, 4)
        assert item["prompt"]
    assert "- `beh-" in (skill_dir(root) / ".factory" / "regression.md").read_text(encoding="utf-8")


def test_allow_regression_creates_the_version_and_records_the_regression(root, monkeypatch):
    install(monkeypatch, Fake())
    mark(root, "[[Q5]]")
    assert package(root, "--runner", "claude") == 0
    approve(root, "0.1.0")
    mark(root, "[[Q2]]")
    assert package(root, "--runner", "claude", "--allow-regression") == 0
    regression = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert regression["status"] == "regressed"
    assert all(item["kind"] == "behaviour" for item in regression["newly_failing"])


def test_an_unchanged_or_higher_score_passes(root, monkeypatch):
    install(monkeypatch, Fake())
    mark(root, "[[Q3]]")
    assert package(root, "--runner", "claude") == 0
    approve(root, "0.1.0")
    assert package(root, "--runner", "claude") == 0
    regression = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert regression["status"] == "pass" and regression["newly_failing"] == []
    assert regression["behaviour"]["status"] == "compared" and regression["behaviour"]["checked"] > 0
    approve(root, "0.1.1")
    mark(root, "[[Q5]]")
    assert package(root, "--runner", "claude") == 0
    assert read_json(versions_dir(root) / "0.1.2" / "regression.json")["status"] == "pass"


def test_a_baseline_without_a_judge_report_is_skipped_not_failed(root, monkeypatch):
    install(monkeypatch, Fake())
    assert package(root) == 0
    approve(root, "0.1.0")
    mark(root, "[[Q1]]")
    assert package(root, "--runner", "claude") == 0
    regression = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert regression["status"] == "pass"
    assert regression["behaviour"] == {
        "status": "skipped: no baseline judge",
        "checked": 0,
        "baseline_judge": None,
    }


def test_a_skipped_baseline_judge_report_is_also_no_baseline_judge(root):
    assert package(root, "--runner", "claude") == 0  # stores a skipped judge report
    approve(root, "0.1.0")
    report = judge.judge_skill(skill_dir(root), "claude", runner_factory=lambda n: Fake())
    regression = release.regression_check(skill_dir(root), "0.1.1", report)
    assert regression["behaviour"]["status"] == "skipped: no baseline judge"
    assert regression["status"] == "pass"


def test_without_a_judge_report_regression_check_skips_behaviour(root):
    assert release.regression_check(skill_dir(root), "0.1.0")["behaviour"] == {
        "status": "skipped: no runner",
        "checked": 0,
        "baseline_judge": None,
    }
    assert package(root) == 0
    approve(root, "0.1.0")
    report = release.regression_check(skill_dir(root), "0.1.1")
    assert report["status"] == "pass"
    assert report["behaviour"]["status"] == "skipped: no runner"


def test_errored_cases_are_not_compared(root, monkeypatch):
    install(monkeypatch, Fake())
    mark(root, "[[Q5]]")
    assert package(root, "--runner", "claude") == 0
    approve(root, "0.1.0")
    mark(root, "[[ERR]]")
    assert package(root, "--runner", "claude") == 0
    regression = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert regression["behaviour"]["status"] == "compared" and regression["behaviour"]["checked"] == 0
    assert regression["status"] == "pass"


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: "not json at all",
        lambda data: json.dumps([1, 2]),
        lambda data: json.dumps({"cases": []}),
        lambda data: json.dumps({**data, "cases": "oops"}),
        lambda data: json.dumps({**data, "cases": [{"id": "beh-001", "verdict": "tie"}]}),
        lambda data: json.dumps({**data, "cases": [5]}),
    ],
)
def test_a_malformed_baseline_judge_report_is_an_integrity_error(root, monkeypatch, capsys, damage):
    install(monkeypatch, Fake())
    assert package(root, "--runner", "claude") == 0
    approve(root, "0.1.0")
    path = versions_dir(root) / "0.1.0" / "reports" / "judge.json"
    path.write_text(damage(read_json(path)), encoding="utf-8")
    capsys.readouterr()
    assert package(root, "--runner", "claude") == 1
    assert "judge.json" in capsys.readouterr().err
    report = judge.judge_skill(skill_dir(root), "claude", runner_factory=lambda n: Fake())
    with pytest.raises(release.IntegrityError):
        release.regression_check(skill_dir(root), "0.1.1", report)


def test_a_passed_judge_report_means_the_model_is_not_called_again(root):
    fake = Fake()
    report = judge.judge_skill(skill_dir(root), "claude", runner_factory=lambda n: fake)
    calls = fake.calls
    assert calls > 0
    version = release.package_skill(skill_dir(root), judge_report=report, runner_name="claude")
    assert fake.calls == calls
    assert (version.path / "reports" / "judge.json").is_file()


def test_package_skill_keeps_the_old_signature_and_adds_keyword_only_options():
    params = inspect.signature(release.package_skill).parameters
    for added in ("runner_name", "judge_name", "runner_factory", "judge_report"):
        assert params[added].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[added].default is None
    assert list(params)[:2] == ["skill_dir", "bump"]
    assert inspect.signature(release.regression_check).parameters["judge_report"].default is None
    assert inspect.signature(pipeline.run_pipeline).parameters["judge_name"].default is None
    assert inspect.signature(challenge.challenge_candidate).parameters["runner_name"].default is None


def test_package_runner_factory_argument_is_used(root):
    fake = Fake()
    version = release.package_skill(skill_dir(root), runner_name="claude", runner_factory=lambda n: fake)
    assert fake.judgements > 0
    assert (version.path / "reports" / "judge.json").is_file()


def test_package_cli_help_lists_runner_and_judge(capsys):
    for command in ("challenge", "package", "run"):
        with pytest.raises(SystemExit) as info:
            cli.main([command, "--help"])
        assert info.value.code == 0
        out = capsys.readouterr().out
        assert "--runner" in out and "--judge" in out


def test_release_parser_accepts_the_new_options():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    release.add_package_parser(sub)
    args = parser.parse_args(["package", NAME, "--runner", "claude", "--judge", "codex"])
    assert (args.runner, args.judge) == ("claude", "codex")


# --- pipeline -----------------------------------------------------------------------------------


def stage_of(result: dict, stage: str) -> dict:
    return next(item for item in result["stages"] if item["stage"] == stage)


def test_pipeline_stage_order_has_judge_between_evaluate_and_regression():
    stages = list(pipeline.STAGES)
    assert stages.index("judge") == stages.index("evaluate") + 1
    assert stages.index("regression") == stages.index("judge") + 1


def test_run_without_a_runner_records_the_judge_stage_as_skipped(tmp_path):
    result = pipeline.run_pipeline(PARAGRAPH, tmp_path)
    assert stage_of(result, "judge") == {
        "stage": "judge",
        "status": "skipped",
        "verdict": "skipped: no runner",
        "report": None,
    }
    skill = tmp_path / "skills" / result["name"]
    assert not (skill / ".factory" / "judge.json").exists()
    version = skill / ".factory" / "versions" / result["version"]
    assert not (version / "reports" / "judge.json").exists()


def test_run_with_a_runner_judges_once_and_packages_the_report(tmp_path, monkeypatch, capsys):
    fake = install(monkeypatch, Fake())
    code = cli.main(["run", PARAGRAPH, "--runner", "claude", "--out", str(tmp_path)])
    assert code == 0
    skills = [p for p in (tmp_path / "skills").iterdir() if p.is_dir()]
    assert len(skills) == 1
    factory = skills[0] / ".factory"
    result = read_json(factory / "pipeline.json")
    judged = stage_of(result, "judge")
    assert judged["status"] == "ok" and judged["report"] == ".factory/judge.json"
    assert judged["verdict"] == judge.summary_line(read_json(factory / "judge.json"))
    assert judged["verdict"].startswith("completed; runner claude, judge claude (same-model)")
    cases = read_json(factory / "judge.json")["summary"]["cases"]
    assert cases > 0
    # evaluate's baseline: 2 runs per case; judge stage: 2 runs and 1 judgement per case; the
    # package stage reuses the judge report and must not call the model again
    assert (fake.runs, fake.judgements) == (4 * cases, cases)
    version = factory / "versions" / result["version"]
    assert (version / "reports" / "judge.json").read_bytes() == (factory / "judge.json").read_bytes()
    assert release.verify_version(version) == []


def test_run_pipeline_passes_judge_name_through(tmp_path):
    claude, codex = Fake("claude"), Fake("codex")
    result = pipeline.run_pipeline(
        PARAGRAPH,
        tmp_path,
        runner_name="claude",
        runner_factory=lambda name: {"claude": claude, "codex": codex}[name],
        judge_name="codex",
    )
    judged = stage_of(result, "judge")
    assert "judge codex (cross-model)" in judged["verdict"]
    assert codex.judgements > 0 and claude.judgements == 0


def test_run_with_a_missing_runner_skips_the_judge_stage(tmp_path):
    result = pipeline.run_pipeline(PARAGRAPH, tmp_path, runner_name="claude")
    judged = stage_of(result, "judge")
    assert judged["status"] == "skipped" and judged["verdict"].startswith("skipped: no runner")
    assert result["status"] == "awaiting-approval"


def test_a_behaviour_regression_stops_the_pipeline_at_regression(tmp_path):
    first = pipeline.run_pipeline(
        PARAGRAPH, tmp_path, runner_name="claude", runner_factory=lambda n: Fake(skill_quality=5)
    )
    skill = tmp_path / "skills" / first["name"]
    release.approve(skill, first["version"], user="tester")
    second = pipeline.run_pipeline(
        PARAGRAPH,
        tmp_path,
        name=first["name"],
        runner_name="claude",
        force=True,
        runner_factory=lambda n: Fake(skill_quality=1),
    )
    assert second["status"] == "stopped" and second["stopped_at"] == "regression"
    assert "newly failing" in stage_of(second, "regression")["verdict"]
    assert stage_of(second, "judge")["status"] == "ok"
    assert [v.version for v in release.load_versions(skill)] == [first["version"]]
