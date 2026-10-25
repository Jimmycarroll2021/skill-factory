"""Tests for `skillz package` and `skillz approve`: immutable versions, manifests, regression."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from skill_factory import release
from skill_factory.capture import capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
SRC = Path(release.__file__).parent
NAME = "csv-data-cleaner"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("release must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), project)
    return project


def cli(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    release.add_package_parser(sub)
    release.add_approve_parser(sub)
    args = parser.parse_args([*argv, "--out", str(root)])
    return {"package": release.run_package, "approve": release.run_approve}[args.command](args)


def skill_dir(root: Path) -> Path:
    return root / "skills" / NAME


def versions_dir(root: Path) -> Path:
    return skill_dir(root) / ".factory" / "versions"


def version_dirs(root: Path) -> list[str]:
    path = versions_dir(root)
    return sorted(p.name for p in path.iterdir() if p.is_dir()) if path.exists() else []


def snapshot(path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(path).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_description(root: Path, text: str) -> None:
    md = skill_dir(root) / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    out = [f"description: {json.dumps(text)}" if ln.startswith("description:") else ln for ln in lines]
    md.write_text("\n".join(out), encoding="utf-8", newline="\n")
    spec_path = skill_dir(root) / ".factory" / "spec.json"
    spec = read_json(spec_path)
    spec["description"] = text
    spec_path.write_text(json.dumps(spec), encoding="utf-8", newline="\n")


def test_first_package_writes_manifest(root):
    assert cli(root, "package", NAME) == 0
    version = versions_dir(root) / "0.1.0"
    manifest = read_json(version / "manifest.json")
    assert manifest["status"] == "awaiting-approval"
    assert manifest["version"] == "0.1.0" and manifest["name"] == NAME
    assert manifest["previous_approved"] is None
    paths = [item["path"] for item in manifest["files"]]
    assert paths == sorted(paths)
    for wanted in (
        "skill/SKILL.md",
        "dist/claude/SKILL.md",
        "dist/codex/agents/openai.yaml",
        "reports/cases.json",
        "reports/eval.json",
        "regression.json",
    ):
        assert wanted in paths
    assert "manifest.json" not in paths and "approval.json" not in paths
    on_disk = {p.relative_to(version).as_posix() for p in version.rglob("*") if p.is_file()}
    assert set(paths) == on_disk - {"manifest.json"}
    for item in manifest["files"]:
        digest = hashlib.sha256((version / item["path"]).read_bytes()).hexdigest()
        assert item["sha256"] == digest
    assert release.verify_version(version) == []
    regression = read_json(version / "regression.json")
    assert regression["status"] == "no-baseline" and regression["baseline"] is None
    assert not any(p.name.startswith(".tmp") for p in versions_dir(root).iterdir())
    index = read_json(versions_dir(root) / "index.json")
    assert index["name"] == NAME
    assert [(v["version"], v["status"]) for v in index["versions"]] == [("0.1.0", "awaiting-approval")]
    assert index["versions"][0]["approved_at"] is None


def test_next_version_bumps():
    assert release.next_version([]) == "0.1.0"
    assert release.next_version(["0.1.0"]) == "0.1.1"
    assert release.next_version(["0.1.0"], "minor") == "0.2.0"
    assert release.next_version(["0.1.0"], "major") == "1.0.0"
    assert release.next_version(["0.9.0", "0.10.0", "0.2.5"]) == "0.10.1"
    with pytest.raises(release.ReleaseError):
        release.next_version(["0.1.0"], "huge")
    with pytest.raises(release.ReleaseError):
        release.parse_semver("1.2")


def test_repackage_bumps_and_leaves_existing_version_untouched(root):
    assert cli(root, "package", NAME) == 0
    before = snapshot(versions_dir(root) / "0.1.0")
    assert cli(root, "package", NAME) == 0
    assert version_dirs(root) == ["0.1.0", "0.1.1"]
    assert snapshot(versions_dir(root) / "0.1.0") == before
    assert cli(root, "package", NAME, "--minor") == 0
    assert cli(root, "package", NAME, "--major") == 0
    assert version_dirs(root) == ["0.1.0", "0.1.1", "0.2.0", "1.0.0"]
    assert snapshot(versions_dir(root) / "0.1.0") == before
    index = read_json(versions_dir(root) / "index.json")
    assert [v["version"] for v in index["versions"]] == ["0.1.0", "0.1.1", "0.2.0", "1.0.0"]


def test_set_version_clash_exits_2_and_touches_nothing(root, capsys):
    assert cli(root, "package", NAME) == 0
    before = snapshot(versions_dir(root))
    assert cli(root, "package", NAME, "--set-version", "0.1.0") == 2
    assert "already exists" in capsys.readouterr().err
    assert snapshot(versions_dir(root)) == before
    assert cli(root, "package", NAME, "--set-version", "2.0.0") == 0
    assert cli(root, "package", NAME, "--set-version", "banana") == 2
    assert version_dirs(root) == ["0.1.0", "2.0.0"]


def test_tampered_file_is_detected_and_blocks_approval(root):
    assert cli(root, "package", NAME) == 0
    version = versions_dir(root) / "0.1.0"
    (version / "dist" / "claude" / "SKILL.md").write_text("tampered\n", encoding="utf-8")
    (version / "extra.txt").write_text("extra\n", encoding="utf-8")
    (version / "reports" / "eval.md").unlink()
    assert release.verify_version(version) == ["dist/claude/SKILL.md", "extra.txt", "reports/eval.md"]
    assert cli(root, "approve", NAME, "0.1.0") == 1
    assert not (version / "approval.json").exists()


def test_approve_records_user_and_time_and_is_idempotent(root, monkeypatch):
    monkeypatch.setattr(getpass, "getuser", lambda: "alice")
    assert cli(root, "package", NAME) == 0
    version = versions_dir(root) / "0.1.0"
    manifest_before = (version / "manifest.json").read_bytes()
    assert [v.status for v in release.load_versions(skill_dir(root))] == ["awaiting-approval"]
    assert cli(root, "approve", NAME, "0.1.0") == 0
    approval = read_json(version / "approval.json")
    assert approval["status"] == "approved" and approval["approved_by"] == "alice"
    assert approval["name"] == NAME and approval["version"] == "0.1.0"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", approval["approved_at"])
    assert (version / "manifest.json").read_bytes() == manifest_before
    assert read_json(version / "manifest.json")["status"] == "awaiting-approval"
    index = read_json(versions_dir(root) / "index.json")
    assert index["versions"][0]["status"] == "approved"
    assert index["versions"][0]["approved_by"] == "alice"
    assert [v.version for v in release.approved_versions(skill_dir(root))] == ["0.1.0"]
    approval_bytes = (version / "approval.json").read_bytes()
    monkeypatch.setattr(getpass, "getuser", lambda: "bob")
    assert cli(root, "approve", NAME, "0.1.0") == 0
    assert (version / "approval.json").read_bytes() == approval_bytes
    assert release.verify_version(version) == []


def test_approve_falls_back_to_unknown_user(root, monkeypatch):
    def broken():
        raise OSError("no user")

    monkeypatch.setattr(getpass, "getuser", broken)
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "0.1.0") == 0
    approval = read_json(versions_dir(root) / "0.1.0" / "approval.json")
    assert approval["approved_by"] == "unknown"


def test_approve_errors_exit_2(root):
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "9.9.9") == 2
    assert cli(root, "approve", NAME, "latest") == 2
    assert cli(root, "approve", "no-such-skill", "0.1.0") == 2
    assert cli(root, "approve", "Bad Name", "0.1.0") == 2
    assert not (versions_dir(root) / "0.1.0" / "approval.json").exists()


def test_regression_blocks_package_and_names_failing_cases(root):
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "0.1.0") == 0
    set_description(root, "Helps with things.")
    before = snapshot(versions_dir(root))
    assert cli(root, "package", NAME) == 1
    assert version_dirs(root) == ["0.1.0"]
    assert snapshot(versions_dir(root)) == before
    report = read_json(skill_dir(root) / ".factory" / "regression.json")
    assert report["status"] == "regressed"
    assert report["baseline"] == "0.1.0" and report["candidate"] == "0.1.1"
    failing = {case["id"]: case for case in report["newly_failing"]}
    assert "pos-002" in failing
    assert failing["pos-002"]["before"] == "pass" and failing["pos-002"]["after"] == "fail"
    assert failing["pos-002"]["prompt"] == "Remove duplicate rows from my spreadsheet"
    markdown = (skill_dir(root) / ".factory" / "regression.md").read_text(encoding="utf-8")
    assert "pos-002" in markdown and "Remove duplicate rows" in markdown

    assert cli(root, "package", NAME, "--allow-regression") == 0
    stored = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert stored["status"] == "regressed"
    assert {case["id"] for case in stored["newly_failing"]} == set(failing)
    assert read_json(versions_dir(root) / "0.1.1" / "manifest.json")["previous_approved"] == "0.1.0"


def test_unchanged_skill_passes_regression(root):
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "0.1.0") == 0
    assert cli(root, "package", NAME) == 0
    report = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert report["status"] == "pass" and report["baseline"] == "0.1.0"
    assert report["checked"] > 0 and report["newly_failing"] == []


def test_unapproved_version_is_not_a_baseline(root):
    assert cli(root, "package", NAME) == 0
    set_description(root, "Helps with things.")
    assert cli(root, "package", NAME) == 0  # 0.1.0 never approved, so no regression check
    report = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert report["status"] == "no-baseline" and report["baseline"] is None
    assert cli(root, "package", NAME, "--set-version", "0.0.9") == 0  # nothing approved below
    assert release.regression_check(skill_dir(root), "0.1.2")["status"] == "no-baseline"


def test_older_approved_version_is_the_baseline(root):
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "0.1.0") == 0
    assert cli(root, "package", NAME) == 0  # 0.1.1 stays unapproved
    set_description(root, "Helps with things.")
    assert cli(root, "package", NAME) == 1
    report = read_json(skill_dir(root) / ".factory" / "regression.json")
    assert report["baseline"] == "0.1.0" and report["candidate"] == "0.1.2"
    assert version_dirs(root) == ["0.1.0", "0.1.1"]


def test_validation_error_creates_no_version(root, capsys):
    scripts = skill_dir(root) / "scripts"
    scripts.mkdir()
    (scripts / "clean.sh").write_text("#!/bin/sh\nrm -rf /var/data\n", encoding="utf-8")
    assert cli(root, "package", NAME) == 1
    assert "destructive-shell" in capsys.readouterr().err
    assert not versions_dir(root).exists()


def test_missing_or_invalid_skill_exits_2(root):
    assert cli(root, "package", "no-such-skill") == 2
    assert cli(root, "package", "..") == 2
    assert not versions_dir(root).exists()


def test_release_source_never_mentions_subprocess_or_git():
    source = (SRC / "release.py").read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert not re.search(r"\bgit\b", source, re.IGNORECASE)
    for path in SRC.glob("*.py"):
        assert "git push" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("bad", ["\u0661.\u0662.\u0663", "1234567890.0.0", "0.0.1234567890", "1.2"])
def test_set_version_rejects_non_ascii_digits_and_long_parts(root, bad):
    assert cli(root, "package", NAME, "--set-version", bad) == 2
    assert version_dirs(root) == []
    with pytest.raises(release.ReleaseError):
        release.parse_semver(bad)


def test_set_version_accepts_nine_digit_parts(root):
    assert cli(root, "package", NAME, "--set-version", "999999999.0.1") == 0
    assert version_dirs(root) == ["999999999.0.1"]


@pytest.mark.parametrize(
    ("report", "payload"),
    [
        ("cases.json", {"cases": ["not", "dicts"]}),
        ("eval.json", {"results": ["not", "dicts"]}),
    ],
)
def test_malformed_baseline_reports_raise_integrity_error(root, report, payload):
    assert cli(root, "package", NAME) == 0
    assert cli(root, "approve", NAME, "0.1.0") == 0
    path = versions_dir(root) / "0.1.0" / "reports" / report
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(release.IntegrityError):
        release.regression_check(skill_dir(root), "0.1.1")


def describe(skill: Path, text: str) -> None:
    md = skill / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    out = [f"description: {json.dumps(text)}" if ln.startswith("description:") else ln for ln in lines]
    md.write_text("\n".join(out), encoding="utf-8", newline="\n")


def test_trigger_cases_that_failed_before_and_pass_now_are_gains(tmp_path):
    project = tmp_path / "vague"
    project.mkdir()
    skill = generate(capture_spec_file(FIXTURES / "eval" / "vague.json"), project)
    release.package_skill(skill)
    release.approve(skill, "0.1.0", user="tester")
    results = read_json(skill / ".factory" / "versions" / "0.1.0" / "reports" / "eval.json")["results"]
    failed_before = {r["id"] for r in results if r["status"] == "fail"}
    assert failed_before == {"pos-001", "pos-002"}
    describe(
        skill,
        "Helps with things: remove duplicate rows from a spreadsheet and summarise the "
        "quarterly invoices.",
    )
    report = release.regression_check(skill, "0.1.1")
    assert report["status"] == "pass" and report["newly_failing"] == []
    gains = {case["id"]: case for case in report["newly_passing"]}
    assert set(gains) == failed_before
    assert gains["pos-001"]["before"] == "fail" and gains["pos-001"]["after"] == "pass"
    assert gains["pos-001"]["kind"] == "positive"
    assert gains["pos-001"]["prompt"] == "Remove duplicate rows from a spreadsheet"
    assert gains["pos-001"]["score"] >= 0.3
    assert report["checked"] == 9  # every trigger case the baseline graded, not only its passes
    markdown = release._regression_markdown(report)
    assert "## Regressions" in markdown and "## Gains" in markdown
    assert markdown.index("## Regressions") < markdown.index("## Gains")
    assert "`pos-001`" in markdown.split("## Gains", 1)[1]
    assert "`pos-001`" not in markdown.split("## Gains", 1)[0]


def test_unchanged_skill_has_no_gains_and_no_baseline_has_empty_lists(root):
    assert cli(root, "package", NAME) == 0
    first = read_json(versions_dir(root) / "0.1.0" / "regression.json")
    assert first["newly_passing"] == [] and first["newly_failing"] == []
    assert cli(root, "approve", NAME, "0.1.0") == 0
    assert cli(root, "package", NAME) == 0
    report = read_json(versions_dir(root) / "0.1.1" / "regression.json")
    assert report["newly_passing"] == [] and report["newly_failing"] == []
    markdown = (versions_dir(root) / "0.1.1" / "regression.md").read_text(encoding="utf-8")
    assert "## Regressions" in markdown and "## Gains" in markdown

