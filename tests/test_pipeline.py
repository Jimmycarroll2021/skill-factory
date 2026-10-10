"""Tests for `skillz run`: the whole pipeline, offline, stopping at awaiting approval."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from skill_factory import cli, pipeline, release, validate

FIXTURES = Path(__file__).parent / "fixtures"
PARAGRAPH = (FIXTURES / "paragraph.txt").read_text(encoding="utf-8")
STAGES = [
    "capture",
    "generate",
    "validate",
    "render",
    "evaluate",
    "regression",
    "package",
    "approval",
]
OTHER_PARAGRAPH = (
    "Convert quarterly sales spreadsheets into bar charts. Use it when the user asks for a "
    "revenue chart or a sales dashboard. Do not use it for release notes."
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", str(empty_path))

    def boom(*args, **kwargs):
        raise AssertionError("the pipeline must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return project


def run_cli(root: Path, *argv: str) -> int:
    return cli.main(["run", *argv, "--out", str(root)])


def only_skill(root: Path) -> Path:
    skills = [p for p in (root / "skills").iterdir() if p.is_dir()]
    assert len(skills) == 1
    return skills[0]


def read_pipeline(skill_dir: Path) -> dict:
    return json.loads((skill_dir / ".factory" / "pipeline.json").read_text(encoding="utf-8"))


def stage_of(result: dict, stage: str) -> dict:
    return next(item for item in result["stages"] if item["stage"] == stage)


def assert_nothing_installed(root: Path, tmp_path: Path) -> None:
    assert not (root / ".claude").exists()
    assert not (root / ".agents").exists()
    assert not (root / "skills" / "registry.json").exists()
    assert list((tmp_path / "home").iterdir()) == []


def test_paragraph_runs_to_awaiting_approval(root, tmp_path, capsys):
    assert run_cli(root, PARAGRAPH) == 0
    skill_dir = only_skill(root)
    name = skill_dir.name
    factory = skill_dir / ".factory"

    for report in (
        "spec.json",
        "validate.json",
        "render.json",
        "eval.json",
        "regression.json",
        "versions/0.1.0/manifest.json",
    ):
        assert (factory / report).is_file(), report

    baseline = json.loads((factory / "eval.json").read_text(encoding="utf-8"))["baseline"]
    assert baseline["status"] == "skipped: no runner"

    versions = release.load_versions(skill_dir)
    assert [(v.version, v.status) for v in versions] == [("0.1.0", "awaiting-approval")]
    assert not (versions[0].path / "approval.json").exists()
    assert_nothing_installed(root, tmp_path)

    result = read_pipeline(skill_dir)
    assert result["schema"] == 1
    assert result["name"] == name
    assert result["status"] == "awaiting-approval"
    assert result["stopped_at"] is None
    assert result["version"] == "0.1.0"
    assert [item["stage"] for item in result["stages"]] == STAGES
    assert all(item["status"] == "ok" for item in result["stages"])
    assert [stage_of(result, s)["report"] for s in STAGES] == [
        ".factory/spec.json",
        "SKILL.md",
        ".factory/validate.json",
        ".factory/render.json",
        ".factory/eval.json",
        ".factory/regression.json",
        ".factory/versions/0.1.0/manifest.json",
        None,
    ]
    assert stage_of(result, "validate")["verdict"] == "pass"
    assert stage_of(result, "regression")["verdict"] == "no-baseline"
    assert stage_of(result, "package")["verdict"] == "created 0.1.0"
    assert stage_of(result, "approval")["verdict"] == "awaiting approval"
    assert "baseline: skipped: no runner" in stage_of(result, "evaluate")["verdict"]
    assert result["next_steps"][0].startswith(f"skillz approve {name} 0.1.0")
    assert result["next_steps"][1].startswith(f"skillz install {name} --platform claude|codex")

    markdown = (factory / "pipeline.md").read_text(encoding="utf-8")
    for item in result["stages"]:
        assert f"{item['stage']}: {item['verdict']}" in markdown
    assert "skipped: no runner" in markdown
    assert f"skillz approve {name} 0.1.0" in markdown
    assert "skillz install" in markdown
    assert "heuristic" in markdown
    assert markdown.rstrip().splitlines()[-1].startswith("2. `skillz install")

    out = capsys.readouterr().out
    assert str(factory / "pipeline.md") in out
    assert f"skillz approve {name} 0.1.0" in out


def test_pipeline_json_style_and_line_endings(root):
    assert run_cli(root, PARAGRAPH) == 0
    factory = only_skill(root) / ".factory"
    raw = (factory / "pipeline.json").read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert b"\r" not in (factory / "pipeline.md").read_bytes()


def test_runner_requested_but_missing_is_recorded_as_skipped(root, tmp_path):
    assert run_cli(root, PARAGRAPH, "--runner", "claude") == 0
    skill_dir = only_skill(root)
    baseline = json.loads(
        (skill_dir / ".factory" / "eval.json").read_text(encoding="utf-8")
    )["baseline"]
    assert baseline["status"] == "skipped: no runner"
    result = read_pipeline(skill_dir)
    assert "baseline: skipped: no runner" in stage_of(result, "evaluate")["verdict"]
    markdown = (skill_dir / ".factory" / "pipeline.md").read_text(encoding="utf-8")
    assert "skipped: no runner" in markdown
    assert_nothing_installed(root, tmp_path)


def test_spec_file_source(root):
    assert run_cli(root, str(FIXTURES / "spec.json")) == 0
    skill_dir = only_skill(root)
    assert skill_dir.name == "csv-data-cleaner"
    assert read_pipeline(skill_dir)["version"] == "0.1.0"


def test_name_option_and_run_pipeline_api(root):
    result = pipeline.run_pipeline(PARAGRAPH, root, name="release-notes")
    assert result["name"] == "release-notes"
    assert (root / "skills" / "release-notes" / "SKILL.md").is_file()
    assert result["status"] == "awaiting-approval"


def test_validation_failure_stops_at_validate(root, monkeypatch, capsys):
    real = validate.validate_skill

    def failing(skill_dir):
        report = real(skill_dir)
        report.findings.append(validate.Finding("name-invalid", "error", "SKILL.md", "bad name"))
        return report

    monkeypatch.setattr(validate, "validate_skill", failing)
    assert run_cli(root, PARAGRAPH) == 1
    skill_dir = only_skill(root)
    result = read_pipeline(skill_dir)
    assert result["status"] == "stopped"
    assert result["stopped_at"] == "validate"
    assert result["version"] is None
    assert stage_of(result, "validate")["status"] == "failed"
    assert stage_of(result, "capture")["status"] == "ok"
    for later in STAGES[3:]:
        assert stage_of(result, later)["status"] == "not-run"
    assert not (skill_dir / ".factory" / "versions").exists()
    assert "stopped at validate" in (skill_dir / ".factory" / "pipeline.md").read_text(
        encoding="utf-8"
    )
    assert "stopped at validate" in capsys.readouterr().err


def test_real_regression_stops_at_regression(root):
    assert run_cli(root, PARAGRAPH, "--name", "my-skill") == 0
    skill_dir = only_skill(root)
    release.approve(skill_dir, "0.1.0", user="tester")

    assert run_cli(root, OTHER_PARAGRAPH, "--name", "my-skill", "--force") == 1
    result = read_pipeline(skill_dir)
    assert result["status"] == "stopped"
    assert result["stopped_at"] == "regression"
    assert result["version"] is None
    assert stage_of(result, "regression")["status"] == "failed"
    assert stage_of(result, "regression")["verdict"].startswith("regressed")
    assert stage_of(result, "package")["status"] == "not-run"
    assert [v.version for v in release.load_versions(skill_dir)] == ["0.1.0"]


def test_patched_regression_stops_at_regression(root, monkeypatch):
    assert run_cli(root, PARAGRAPH, "--name", "my-skill") == 0
    skill_dir = only_skill(root)
    release.approve(skill_dir, "0.1.0", user="tester")

    def regressed(skill, candidate):
        return {
            "candidate": candidate,
            "baseline": "0.1.0",
            "status": "regressed",
            "checked": 1,
            "newly_failing": [
                {
                    "id": "pos-1",
                    "kind": "positive",
                    "prompt": "p",
                    "before": "pass",
                    "after": "fail",
                    "score": 0.0,
                }
            ],
        }

    monkeypatch.setattr(release, "regression_check", regressed)
    assert run_cli(root, PARAGRAPH, "--name", "my-skill", "--force") == 1
    result = read_pipeline(skill_dir)
    assert result["stopped_at"] == "regression"
    assert "pos-1" in stage_of(result, "regression")["verdict"]
    assert [v.version for v in release.load_versions(skill_dir)] == ["0.1.0"]


def test_integrity_error_stops_at_regression(root, monkeypatch):
    def broken(skill, candidate):
        raise release.IntegrityError("baseline is damaged")

    monkeypatch.setattr(release, "regression_check", broken)
    assert run_cli(root, PARAGRAPH) == 1
    result = read_pipeline(only_skill(root))
    assert result["stopped_at"] == "regression"
    assert "baseline is damaged" in stage_of(result, "regression")["verdict"]
    assert stage_of(result, "regression")["report"] is None


def test_input_errors_return_2(root, capsys):
    assert run_cli(root, "   ") == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert run_cli(root, PARAGRAPH, "--name", "Not A Valid Name") == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert not (root / "skills").exists()

    assert run_cli(root, PARAGRAPH) == 0
    assert run_cli(root, PARAGRAPH) == 2
    assert "already exists" in capsys.readouterr().err
    assert [v.version for v in release.load_versions(only_skill(root))] == ["0.1.0"]


def test_version_exists_returns_2(root, monkeypatch, capsys):
    def exists(*args, **kwargs):
        raise release.VersionExists("version 0.1.0 already exists")

    monkeypatch.setattr(release, "package_skill", exists)
    assert run_cli(root, PARAGRAPH) == 2
    assert "already exists" in capsys.readouterr().err


def test_rerun_with_force_makes_next_version(root):
    assert run_cli(root, PARAGRAPH, "--name", "my-skill") == 0
    assert run_cli(root, PARAGRAPH, "--name", "my-skill", "--force") == 0
    skill_dir = only_skill(root)
    assert read_pipeline(skill_dir)["version"] == "0.1.1"
    assert [v.version for v in release.load_versions(skill_dir)] == ["0.1.0", "0.1.1"]


def test_next_steps_carry_out_option_for_other_directories(root):
    result = pipeline.run_pipeline(PARAGRAPH, root)
    suffix = f" --out {shlex.quote(str(root))}"
    assert all(step.endswith(suffix) for step in result["next_steps"])


def test_help_mentions_options(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["run", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--runner", "--out", "--name", "--force"):
        assert option in out
