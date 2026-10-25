"""Tests for receipts: every packaged version carries receipt.json and receipt.md."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from skill_factory import cli, receipt, release
from skill_factory.capture import capture_spec_file
from skill_factory.generate import generate

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "csv-data-cleaner"
TIMESTAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def boom(*args, **kwargs):
        raise AssertionError("receipts must not spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    generate(capture_spec_file(FIXTURES / "eval" / "well-described.json"), project)
    return project


class FakeModel:
    """A runner whose with-skill answers have a fixed quality, and a judge that reads it back."""

    def __init__(self, name: str, quality: int = 3) -> None:
        self.name = name
        self.quality = quality

    def run(self, prompt: str) -> str:
        if "<response_a>" not in prompt:
            return f"Q={self.quality}" if "<skill>" in prompt else "Q=2"
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


def skill_dir(root: Path) -> Path:
    return root / "skills" / NAME


def versions_dir(root: Path) -> Path:
    return skill_dir(root) / ".factory" / "versions"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_description(root: Path, text: str) -> None:
    md = skill_dir(root) / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    out = [f"description: {json.dumps(text)}" if ln.startswith("description:") else ln for ln in lines]
    md.write_text("\n".join(out), encoding="utf-8", newline="\n")


def package(root: Path, quality: int | None = None, judge: str = "claude", **kwargs) -> release.Version:
    """Package the skill; with ``quality`` a fake claude runs the cases and ``judge`` judges."""
    if quality is None:
        return release.package_skill(skill_dir(root), **kwargs)
    models = {"claude": FakeModel("claude", quality), "codex": FakeModel("codex", quality)}
    return release.package_skill(
        skill_dir(root),
        runner_name="claude",
        judge_name=judge,
        runner_factory=lambda name: models.get(name),
        **kwargs,
    )


def receipt_of(version: release.Version) -> dict:
    return read_json(version.path / "receipt.json")


# --- receipt v1 ---------------------------------------------------------------------------------


def test_package_writes_a_receipt_covered_by_the_manifest(root):
    version = package(root)
    manifest = read_json(version.path / "manifest.json")
    paths = [item["path"] for item in manifest["files"]]
    assert "receipt.json" in paths and "receipt.md" in paths
    assert release.verify_version(version.path) == []
    data = receipt_of(version)
    assert data["schema"] == "skill-receipt/1"
    assert data["skill"] == NAME and data["version"] == "0.1.0"
    assert data["created_at"] == manifest["created_at"]
    assert re.fullmatch(TIMESTAMP, data["created_at"])
    skill_md = (version.path / "skill" / "SKILL.md").read_bytes()
    assert data["skill_sha256"] == hashlib.sha256(skill_md).hexdigest()
    assert skill_md == (skill_dir(root) / "SKILL.md").read_bytes()
    assert list(data) == sorted(data)  # json is written with sorted keys like the manifest
    assert set(data) == {
        "schema",
        "skill",
        "version",
        "created_at",
        "skill_sha256",
        "evidence",
        "runner",
        "judge",
        "baseline",
        "trigger",
        "behaviour",
        "regressions",
        "gains",
        "status",
    }


def test_receipt_without_a_judge_is_unverified_and_has_no_baseline(root):
    data = receipt_of(package(root))
    assert data["evidence"] == "unverified"
    assert data["runner"] is None and data["judge"] is None
    assert data["baseline"] is None and data["status"] == "no-baseline"
    assert data["behaviour"] is None
    assert data["regressions"] == [] and data["gains"] == []
    summary = read_json(skill_dir(root) / ".factory" / "eval.json")["summary"]
    passed = summary["positive"]["passed"] + summary["negative"]["passed"]
    failed = summary["positive"]["failed"] + summary["negative"]["failed"]
    assert passed > 0
    assert data["trigger"] == {
        "method": "lexical-overlap",
        "threshold": 0.3,
        "checked": passed + failed,
        "passed": passed,
        "failed": failed,
    }


def test_same_model_judge_is_self_judged(root):
    data = receipt_of(package(root, quality=4, judge="claude"))
    assert data["evidence"] == "self-judged"
    assert (data["runner"], data["judge"]) == ("claude", "claude")
    stored = read_json(versions_dir(root) / "0.1.0" / "reports" / "judge.json")
    assert data["behaviour"] == stored["summary"]
    assert data["behaviour"]["cases"] > 0 and data["behaviour"]["skill_better"] == data["behaviour"]["cases"]


def test_cross_model_judge_is_verified(root):
    data = receipt_of(package(root, quality=4, judge="codex"))
    assert data["evidence"] == "verified"
    assert (data["runner"], data["judge"]) == ("claude", "codex")
    assert data["behaviour"]["cases"] > 0


def test_a_skipped_judge_is_unverified_with_no_behaviour(root):
    version = release.package_skill(skill_dir(root), runner_name="claude", runner_factory=lambda n: None)
    assert read_json(version.path / "reports" / "judge.json")["status"] == "skipped: no runner"
    data = receipt_of(version)
    assert data["evidence"] == "unverified" and data["behaviour"] is None
    assert data["runner"] is None and data["judge"] is None


def test_a_clean_second_version_names_its_baseline(root):
    package(root)
    release.approve(skill_dir(root), "0.1.0", user="tester")
    data = receipt_of(package(root))
    assert data["baseline"] == "0.1.0" and data["status"] == "clean"
    assert data["regressions"] == [] and data["gains"] == []


def test_a_regressed_version_lists_regressions_without_scores(root):
    package(root)
    release.approve(skill_dir(root), "0.1.0", user="tester")
    set_description(root, "Helps with things.")
    version = package(root, allow_regression=True)
    data = receipt_of(version)
    assert data["status"] == "regressed" and data["baseline"] == "0.1.0"
    stored = read_json(version.path / "regression.json")
    assert [r["id"] for r in data["regressions"]] == sorted(c["id"] for c in stored["newly_failing"])
    for row in data["regressions"]:
        assert set(row) == {"id", "kind", "prompt", "before", "after"}
        assert (row["before"], row["after"]) == ("pass", "fail")
    assert "pos-002" in {r["id"] for r in data["regressions"]}


def test_behaviour_gains_and_regressions_reach_the_receipt(root):
    package(root, quality=3)
    release.approve(skill_dir(root), "0.1.0", user="tester")
    gained = receipt_of(package(root, quality=5))
    assert gained["status"] == "clean" and gained["regressions"] == []
    assert gained["gains"] and all(r["kind"] == "behaviour" for r in gained["gains"])
    assert all((r["before"], r["after"]) == (6, 10) for r in gained["gains"])
    assert [r["id"] for r in gained["gains"]] == sorted(r["id"] for r in gained["gains"])
    release.approve(skill_dir(root), "0.1.1", user="tester")
    lost = receipt_of(package(root, quality=1, allow_regression=True))
    assert lost["status"] == "regressed" and lost["gains"] == []
    assert all((r["before"], r["after"]) == (10, 2) for r in lost["regressions"])


def test_receipt_markdown_is_a_short_human_rendering(root):
    package(root, quality=3)
    release.approve(skill_dir(root), "0.1.0", user="tester")
    set_description(root, "Helps with things.")
    version = package(root, quality=5, judge="codex", allow_regression=True)
    data = receipt_of(version)
    text = (version.path / "receipt.md").read_text(encoding="utf-8")
    assert text == receipt.receipt_markdown(data)
    assert text.splitlines()[0] == f"# Receipt: {NAME} 0.1.1 — evidence: verified"
    assert "Baseline: 0.1.0" in text
    trigger = data["trigger"]
    assert f"{trigger['checked']} checked, {trigger['passed']} passed, {trigger['failed']} failed" in text
    behaviour = data["behaviour"]
    assert f"{behaviour['cases']} cases" in text and f"skill better {behaviour['skill_better']}" in text
    regressions, gains = text.split("## Regressions", 1)[1].split("## Gains", 1)
    assert "| id | kind | prompt | before → after |" in regressions
    assert "| `pos-002` | positive | Remove duplicate rows from my spreadsheet | pass → fail |" in regressions
    assert all(f"| `{row['id']}` | behaviour |" in gains for row in data["gains"])
    assert "6 → 10" in gains
    assert text.rstrip().splitlines()[-1] == "Judge: codex; runner: claude."
    assert text.endswith("\n") and "\r" not in text


def test_receipt_markdown_without_a_judge_or_baseline(root):
    version = package(root)
    text = (version.path / "receipt.md").read_text(encoding="utf-8")
    assert text.splitlines()[0] == f"# Receipt: {NAME} 0.1.0 — evidence: unverified"
    assert "Baseline: none" in text and "Behaviour: no judge ran" in text
    assert text.count("None.") == 2  # empty Regressions and Gains sections
    assert text.rstrip().splitlines()[-1] == "Judge: none; runner: none."


def test_tampering_with_the_receipt_is_detected(root):
    version = package(root)
    data = receipt_of(version)
    data["evidence"] = "verified"
    (version.path / "receipt.json").write_text(json.dumps(data), encoding="utf-8")
    assert release.verify_version(version.path) == ["receipt.json"]
    with pytest.raises(release.IntegrityError):
        release.approve(skill_dir(root), "0.1.0", user="tester")
    (version.path / "receipt.md").write_text("# forged\n", encoding="utf-8")
    assert release.verify_version(version.path) == ["receipt.json", "receipt.md"]


def test_load_receipt_returns_none_for_a_version_without_one(root):
    version = package(root)
    assert receipt.load_receipt(version.path) == receipt_of(version)
    (version.path / "receipt.json").unlink()
    assert receipt.load_receipt(version.path) is None
    assert receipt.load_receipt(version.path / "missing") is None


# --- approval refuses regressions ---------------------------------------------------------------


def regressed_version(root: Path) -> release.Version:
    """Approve 0.1.0, then package a 0.1.1 whose trigger cases regressed."""
    package(root)
    release.approve(skill_dir(root), "0.1.0", user="tester")
    set_description(root, "Helps with things.")
    version = package(root, allow_regression=True)
    assert receipt_of(version)["status"] == "regressed"
    return version


def strip_receipt(version: release.Version) -> None:
    """Make a version look like one packaged before receipts existed."""
    manifest = read_json(version.path / "manifest.json")
    manifest["files"] = [f for f in manifest["files"] if not f["path"].startswith("receipt.")]
    (version.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (version.path / "receipt.json").unlink()
    (version.path / "receipt.md").unlink()
    assert release.verify_version(version.path) == []


def test_approve_refuses_a_regressed_version_without_a_reason(root, capsys):
    version = regressed_version(root)
    ids = [row["id"] for row in receipt_of(version)["regressions"]]
    with pytest.raises(release.RegressionsNotAccepted) as info:
        release.approve(skill_dir(root), "0.1.1", user="tester")
    assert isinstance(info.value, release.ReleaseError)
    message = str(info.value)
    assert all(case_id in message for case_id in ids) and "--accept-regressions" in message
    assert not (version.path / "approval.json").exists()
    assert cli.main(["approve", NAME, "0.1.1", "--out", str(root)]) == 1
    err = capsys.readouterr().err
    assert "skillz: error:" in err and "--accept-regressions" in err
    assert not (version.path / "approval.json").exists()
    assert version.path.name not in [v.version for v in release.approved_versions(skill_dir(root))]


def test_approve_with_a_reason_records_the_accepted_regressions(root, capsys):
    version = regressed_version(root)
    ids = sorted(row["id"] for row in receipt_of(version)["regressions"])
    receipt_bytes = (version.path / "receipt.json").read_bytes()
    code = cli.main(
        ["approve", NAME, "0.1.1", "--accept-regressions", "  renamed the skill on purpose ", "--out", str(root)]
    )
    assert code == 0
    assert "approved" in capsys.readouterr().out
    approval = read_json(version.path / "approval.json")
    assert approval["status"] == "approved"
    assert approval["accepted_regressions"] == {"reason": "renamed the skill on purpose", "ids": ids}
    assert approval["receipt"] == "regressed"
    assert (version.path / "receipt.json").read_bytes() == receipt_bytes
    assert release.verify_version(version.path) == []
    assert [v.version for v in release.approved_versions(skill_dir(root))] == ["0.1.0", "0.1.1"]
    # approving again changes nothing, with or without a reason
    before = (version.path / "approval.json").read_bytes()
    release.approve(skill_dir(root), "0.1.1", user="someone-else")
    assert (version.path / "approval.json").read_bytes() == before


def test_a_blank_reason_is_not_a_reason(root, capsys):
    version = regressed_version(root)
    with pytest.raises(release.ReleaseError):
        release.approve(skill_dir(root), "0.1.1", user="tester", accept_regressions="   ")
    assert cli.main(["approve", NAME, "0.1.1", "--accept-regressions", "", "--out", str(root)]) == 2
    assert "reason" in capsys.readouterr().err
    assert not (version.path / "approval.json").exists()


def test_a_clean_version_approves_without_a_reason_and_records_its_receipt_status(root):
    version = package(root)
    release.approve(skill_dir(root), "0.1.0", user="tester", accept_regressions="not needed")
    approval = read_json(version.path / "approval.json")
    assert approval["receipt"] == "no-baseline" and "accepted_regressions" not in approval
    second = package(root)
    release.approve(skill_dir(root), "0.1.1", user="tester")
    assert read_json(second.path / "approval.json")["receipt"] == "clean"


def test_a_version_without_a_receipt_approves_with_receipt_missing(root, capsys):
    version = package(root)
    strip_receipt(version)
    assert cli.main(["approve", NAME, "0.1.0", "--out", str(root)]) == 0
    approval = read_json(version.path / "approval.json")
    assert approval["status"] == "approved" and approval["receipt"] == "missing"
    assert "accepted_regressions" not in approval


def test_approve_help_documents_accept_regressions(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["approve", "--help"])
    assert info.value.code == 0
    assert "--accept-regressions REASON" in capsys.readouterr().out
