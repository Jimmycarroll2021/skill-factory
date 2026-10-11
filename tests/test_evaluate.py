"""Tests for `skillz eval`: reports, advisory exit codes, determinism and the opt-in baseline."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from skill_factory import evaluate, runner
from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
EVAL_KEYS = {"skill", "method", "threshold", "summary", "results", "weak_areas", "baseline"}
SKILL_SAID = (
    "always ask which columns identify a duplicate before deleting rows "
    "must keep a backup copy of the original file"
)


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("eval must not spawn a process in tests")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(runner.subprocess, "run", boom)


class FakeRunner:
    name = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return SKILL_SAID if "<skill>" in prompt else "nothing relevant"


def _make(tmp_path: Path, fixture: str, name: str) -> Path:
    assert main(["new", str(FIXTURES / fixture), "--out", str(tmp_path)]) == 0
    return tmp_path / "skills" / name


def _read(skill_dir: Path, filename: str) -> str:
    return (skill_dir / ".factory" / filename).read_text(encoding="utf-8")


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["eval", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--runner" in out and "--out" in out and "--strict" in out


def test_well_described_end_to_end(tmp_path):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 0
    assert (skill_dir / ".factory" / "cases.json").is_file()
    report = json.loads(_read(skill_dir, "eval.json"))
    assert set(report) == EVAL_KEYS
    assert report["skill"] == "csv-data-cleaner"
    assert "heuristic" in report["method"] and "lexical overlap" in report["method"]
    assert report["baseline"]["status"] == "skipped: no runner"
    assert report["summary"]["positive"]["failed"] == 0
    assert report["summary"]["negative"]["failed"] == 0
    assert report["summary"]["behaviour"]["pending"] == 5
    assert report["weak_areas"] == []
    markdown = _read(skill_dir, "eval.md")
    assert "skipped: no runner" in markdown and "heuristic" in markdown


def test_cases_json_is_byte_identical_across_runs(tmp_path):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 0
    first = (skill_dir / ".factory" / "cases.json").read_bytes()
    first_eval = (skill_dir / ".factory" / "eval.json").read_bytes()
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 0
    assert (skill_dir / ".factory" / "cases.json").read_bytes() == first
    assert (skill_dir / ".factory" / "eval.json").read_bytes() == first_eval
    assert b"\r" not in first


def test_vague_skill_is_advisory_unless_strict(tmp_path):
    skill_dir = _make(tmp_path, "vague.json", "general-helper")
    assert main(["eval", "general-helper", "--out", str(tmp_path)]) == 0
    report = json.loads(_read(skill_dir, "eval.json"))
    failing = [r["id"] for r in report["results"] if r["kind"] == "positive" and r["status"] == "fail"]
    assert failing
    weak = {w["area"]: w for w in report["weak_areas"]}
    assert set(failing) <= set(weak["trigger-vocabulary"]["cases"])
    markdown = _read(skill_dir, "eval.md")
    assert "trigger-vocabulary" in markdown and "vague-description" in markdown
    assert weak["trigger-vocabulary"]["proposal"] in markdown
    for case_id in failing:
        assert case_id in markdown
    assert main(["eval", "general-helper", "--out", str(tmp_path), "--strict"]) == 1


def test_strict_passes_for_well_described(tmp_path):
    _make(tmp_path, "well-described.json", "csv-data-cleaner")
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path), "--strict"]) == 0


def test_runner_not_on_path_is_skipped(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    monkeypatch.setattr("skill_factory.runner.shutil.which", lambda _exe: None)
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "claude"]) == 0
    baseline = json.loads(_read(skill_dir, "eval.json"))["baseline"]
    assert baseline["status"] == "skipped: no runner"
    assert baseline["requested"] == "claude"
    assert "skipped: no runner" in _read(skill_dir, "eval.md")


def test_fake_runner_produces_baseline(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    monkeypatch.setattr("skill_factory.runner.shutil.which", lambda exe: f"/fake/bin/{exe}")
    fake = FakeRunner()
    requested: list[str] = []

    def factory(name: str):
        requested.append(name)
        return fake

    report = evaluate.evaluate_skill(skill_dir, "claude", runner_factory=factory)
    assert requested == ["claude"]
    baseline = report["baseline"]
    assert baseline["status"] == "completed" and baseline["runner"] == "fake"
    assert [c["id"] for c in baseline["cases"]] == ["beh-001", "beh-002", "beh-t001", "beh-t002", "beh-t003"]
    assert all(c["verdict"] == "skill-helps" for c in baseline["cases"])
    assert baseline["summary"]["skill_helps"] == 5
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8").strip()
    assert any(skill_text in p for p in fake.prompts)
    assert any("<skill>" not in p for p in fake.prompts)
    assert len(fake.prompts) == 10
    markdown = _read(skill_dir, "eval.md")
    assert "completed" in markdown and "skill-helps" in markdown
    assert json.loads(_read(skill_dir, "eval.json"))["baseline"] == baseline


def test_cli_uses_module_runner_factory(tmp_path, monkeypatch):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    monkeypatch.setattr(evaluate, "RUNNER_FACTORY", lambda _name: FakeRunner())
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path), "--runner", "codex"]) == 0
    assert json.loads(_read(skill_dir, "eval.json"))["baseline"]["status"] == "completed"


def test_factory_not_called_without_runner_flag(tmp_path, monkeypatch):
    _make(tmp_path, "well-described.json", "csv-data-cleaner")

    def forbidden(_name):
        raise AssertionError("runner factory must not be called without --runner")

    monkeypatch.setattr(evaluate, "RUNNER_FACTORY", forbidden)
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 0


def test_falls_back_to_skill_md_frontmatter(tmp_path):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    (skill_dir / ".factory" / "spec.json").unlink()
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 0
    report = json.loads(_read(skill_dir, "eval.json"))
    assert report["skill"] == "csv-data-cleaner"
    assert report["summary"]["behaviour"]["pending"] == 0
    assert any(w["area"] == "no-behaviours" for w in report["weak_areas"])


def test_invalid_spec_json_returns_two(tmp_path, capsys):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    (skill_dir / ".factory" / "spec.json").write_text("{not json", encoding="utf-8")
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_missing_skill_returns_two(tmp_path, capsys):
    assert main(["eval", "no-such-skill", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_missing_skill_md_returns_two(tmp_path, capsys):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    (skill_dir / "SKILL.md").unlink()
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_invalid_name_returns_two(tmp_path, capsys):
    assert main(["eval", "../escape", "--out", str(tmp_path)]) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_symlinked_factory_dir_is_refused(tmp_path, capsys):
    skill_dir = _make(tmp_path, "well-described.json", "csv-data-cleaner")
    target = tmp_path / "elsewhere"
    (skill_dir / ".factory").rename(target)
    try:
        os.symlink(target, skill_dir / ".factory", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    capsys.readouterr()
    assert main(["eval", "csv-data-cleaner", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "skillz: error:" in err and "symlink" in err
    for filename in ("eval.json", "cases.json", "eval.md"):
        assert not (target / filename).exists()
