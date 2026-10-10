"""Tests for skillz install, rollback and list: approval gate, exact targets, registry, rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from skill_factory import install

NAME = "demo-skill"
SRC = Path(install.__file__).parent


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("install must never spawn a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    return path


@pytest.fixture
def root(tmp_path, home):
    path = tmp_path / "project"
    path.mkdir()
    return path


def tree(path: Path) -> dict[str, bytes]:
    """Every file (bytes) and directory (empty bytes, trailing slash) under path."""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        for entry in dirnames:
            out[(here / entry).relative_to(path).as_posix() + "/"] = b""
        for entry in filenames:
            out[(here / entry).relative_to(path).as_posix()] = (
                here / entry
            ).read_bytes()
    return out


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_version(root: Path, name: str, version: str, approved: bool = True) -> Path:
    """Write a contract-shaped version directory by hand."""
    _write(root / "skills" / name / "SKILL.md", f"---\nname: {name}\n---\n")
    vdir = root / "skills" / name / ".factory" / "versions" / version
    files = {
        "dist/claude/SKILL.md": f"claude skill {version}\n",
        "dist/codex/SKILL.md": f"codex skill {version}\n",
        "dist/codex/agents/openai.yaml": f"name: {name}\nversion: {version}\n",
        f"only-{version}.txt": "marker\n",
    }
    listing = [
        {"path": rel, "sha256": _write(vdir / rel, text)} for rel, text in files.items()
    ]
    manifest = {
        "schema": 1,
        "name": name,
        "version": version,
        "created_at": "2026-01-01T00:00:00Z",
        "status": "awaiting-approval",
        "previous_approved": None,
        "files": sorted(listing, key=lambda item: item["path"]),
    }
    _write(vdir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    if approved:
        approval = {
            "name": name,
            "version": version,
            "status": "approved",
            "approved_at": "2026-01-02T00:00:00Z",
            "approved_by": "alice",
        }
        _write(vdir / "approval.json", json.dumps(approval, indent=2) + "\n")
    return vdir


def run(root: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    install.add_install_parser(sub)
    install.add_rollback_parser(sub)
    install.add_list_parser(sub)
    args = parser.parse_args([argv[0], *argv[1:], "--out", str(root)])
    handlers = {
        "install": install.run_install,
        "rollback": install.run_rollback,
        "list": install.run_list,
    }
    return handlers[args.command](args)


def _install(root, platform="claude", scope="project", *extra):
    return run(root, "install", NAME, "--platform", platform, "--scope", scope, *extra)


def test_unapproved_explicit_version_installs_nothing(root, home):
    make_version(root, NAME, "0.1.0", approved=False)
    before = (tree(root), tree(home))
    assert _install(root, "claude", "project", "--version", "0.1.0") == 1
    assert _install(root, "claude", "user", "--version", "0.1.0") == 1
    assert (tree(root), tree(home)) == before


def test_default_with_nothing_approved_installs_nothing(root, home):
    make_version(root, NAME, "0.1.0", approved=False)
    before = (tree(root), tree(home))
    assert _install(root) == 1
    assert (tree(root), tree(home)) == before


def test_default_skips_unapproved_newer_version(root):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.2.0", approved=False)
    assert _install(root) == 0
    skill = root / ".claude" / "skills" / NAME / "SKILL.md"
    assert skill.read_text(encoding="utf-8") == "claude skill 0.1.0\n"


@pytest.mark.parametrize(
    ("platform", "base", "files"),
    [
        ("claude", ".claude/skills", {"SKILL.md": "claude skill 0.1.0\n"}),
        (
            "codex",
            ".agents/skills",
            {
                "SKILL.md": "codex skill 0.1.0\n",
                "agents/openai.yaml": f"name: {NAME}\nversion: 0.1.0\n",
            },
        ),
    ],
)
def test_project_install_lands_exactly_in_target(root, home, platform, base, files):
    make_version(root, NAME, "0.1.0")
    before, home_before = tree(root), tree(home)
    assert _install(root, platform, "project") == 0
    after = tree(root)
    added = {path for path in after if path not in before}
    target = f"{base}/{NAME}"
    expected = {f"{target}/{rel}" for rel in files} | {"skills/registry.json"}
    expected |= {f"{base.split('/')[0]}/", f"{base}/", f"{target}/"}
    expected |= {f"{target}/agents/"} if platform == "codex" else set()
    assert added == expected
    for rel, text in files.items():
        assert after[f"{target}/{rel}"] == text.encode("utf-8")
    assert {p: after[p] for p in before} == before
    assert tree(home) == home_before
    other = ".agents" if platform == "claude" else ".claude"
    assert not (root / other).exists()


def test_user_scope_uses_home_and_nothing_in_project(root, home):
    make_version(root, NAME, "0.1.0")
    before = tree(root)
    assert _install(root, "claude", "user") == 0
    assert (home / ".claude" / "skills" / NAME / "SKILL.md").read_text(
        encoding="utf-8"
    ) == ("claude skill 0.1.0\n")
    assert sorted(p for p in tree(home) if not p.endswith("/")) == [
        f".claude/skills/{NAME}/SKILL.md"
    ]
    assert not (root / ".claude").exists()
    assert not (root / ".agents").exists()
    added = {p for p in tree(root) if p not in before}
    assert added == {"skills/registry.json"}
    current = install.load_registry(root)["current"][f"{NAME}:claude:user"]
    assert Path(current["path"]) == home / ".claude" / "skills" / NAME


def test_explicit_home_for_codex_user(root, tmp_path):
    make_version(root, NAME, "0.1.0")
    other = tmp_path / "elsewhere"
    record = install.install_skill(root, NAME, "codex", "user", home=other)
    assert Path(record.path) == other / ".agents" / "skills" / NAME
    assert (other / ".agents" / "skills" / NAME / "agents" / "openai.yaml").is_file()
    assert not (tmp_path / "home" / ".agents").exists()
    assert not (root / ".agents").exists()


def test_registry_records_install_and_approval(root):
    make_version(root, NAME, "0.1.0")
    assert _install(root) == 0
    registry = json.loads(
        (root / "skills" / "registry.json").read_text(encoding="utf-8")
    )
    assert registry["schema"] == 1
    record = registry["current"][f"{NAME}:claude:project"]
    assert record == registry["installs"][-1]
    assert record["name"] == NAME
    assert record["version"] == "0.1.0"
    assert record["platform"] == "claude"
    assert record["scope"] == "project"
    assert record["path"] == str((root / ".claude" / "skills" / NAME).absolute())
    assert record["approved_by"] == "alice"
    assert record["approved_at"] == "2026-01-02T00:00:00Z"
    assert record["action"] == "install"
    assert record["from_version"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["installed_at"])


def test_newer_install_replaces_previous_completely(root):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.1.1")
    assert _install(root, "claude", "project", "--version", "0.1.0") == 0
    target = root / ".claude" / "skills" / NAME
    (target / "only-0.1.0.txt").write_text("stale\n", encoding="utf-8")
    assert _install(root) == 0
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "claude skill 0.1.1\n"
    assert not (target / "only-0.1.0.txt").exists()
    assert sorted(p.name for p in target.parent.iterdir()) == [NAME]
    assert len(install.load_registry(root)["installs"]) == 2


def test_rollback_reinstalls_previous_approved_version(root):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.1.1", approved=False)
    make_version(root, NAME, "0.2.0")
    assert _install(root, "claude", "project", "--version", "0.1.0") == 0
    assert _install(root, "claude", "project", "--version", "0.2.0") == 0
    versions_before = tree(root / "skills" / NAME / ".factory")
    assert run(root, "rollback", NAME) == 0
    skill = root / ".claude" / "skills" / NAME / "SKILL.md"
    assert skill.read_text(encoding="utf-8") == "claude skill 0.1.0\n"
    assert tree(root / "skills" / NAME / ".factory") == versions_before
    registry = install.load_registry(root)
    last = registry["installs"][-1]
    assert last["action"] == "rollback"
    assert last["from_version"] == "0.2.0"
    assert last["version"] == "0.1.0"
    assert registry["current"][f"{NAME}:claude:project"] == last
    assert sorted(p.name for p in skill.parent.parent.iterdir()) == [NAME]


def test_rollback_with_nothing_lower_changes_nothing(root, home):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.1.1")
    assert _install(root, "claude", "project", "--version", "0.1.0") == 0
    assert _install(root) == 0
    assert run(root, "rollback", NAME) == 0
    before = (tree(root), tree(home))
    assert run(root, "rollback", NAME) == 1
    assert (tree(root), tree(home)) == before


def test_rollback_without_install_exits_1(root, home):
    make_version(root, NAME, "0.1.0")
    before = (tree(root), tree(home))
    assert run(root, "rollback", NAME) == 1
    assert (tree(root), tree(home)) == before


def test_rollback_is_all_or_nothing(root, home):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.2.0")
    assert _install(root, "claude", "project", "--version", "0.1.0") == 0
    assert _install(root, "claude", "project", "--version", "0.2.0") == 0
    assert _install(root, "codex", "project", "--version", "0.1.0") == 0
    before = (tree(root), tree(home))
    assert run(root, "rollback", NAME) == 1
    assert (tree(root), tree(home)) == before
    assert run(root, "rollback", NAME, "--platform", "claude") == 0
    assert (root / ".claude/skills" / NAME / "SKILL.md").read_text(
        encoding="utf-8"
    ) == ("claude skill 0.1.0\n")


def test_tampered_dist_file_installs_nothing(root, home):
    vdir = make_version(root, NAME, "0.1.0")
    (vdir / "dist" / "claude" / "SKILL.md").write_text("tampered\n", encoding="utf-8")
    before = (tree(root), tree(home))
    assert _install(root) == 1
    assert (tree(root), tree(home)) == before


def test_extra_dist_file_installs_nothing(root, home):
    vdir = make_version(root, NAME, "0.1.0")
    (vdir / "dist" / "claude" / "extra.txt").write_text("sneaky\n", encoding="utf-8")
    before = (tree(root), tree(home))
    assert _install(root) == 1
    assert (tree(root), tree(home)) == before


def test_list_json_shows_installs_and_statuses(root, capsys):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.1.1", approved=False)
    assert _install(root, "claude", "project", "--version", "0.1.0") == 0
    capsys.readouterr()
    assert run(root, "list", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    [current] = data["installed"]
    assert (current["name"], current["version"], current["platform"]) == (
        NAME,
        "0.1.0",
        "claude",
    )
    rows = data["skills"][NAME]
    assert [(r["version"], r["status"]) for r in rows] == [
        ("0.1.0", "approved"),
        ("0.1.1", "awaiting-approval"),
    ]
    assert rows[0]["approved_by"] == "alice"
    assert rows[1]["approved_by"] is None


def test_list_table_names_skill_version_and_status(root, capsys):
    make_version(root, NAME, "0.1.0")
    make_version(root, NAME, "0.1.1", approved=False)
    assert _install(root) == 0
    capsys.readouterr()
    assert run(root, "list") == 0
    out = capsys.readouterr().out
    assert "NAME" in out
    assert "STATUS" in out
    installed_row = next(
        line for line in out.splitlines() if "claude" in line and "project" in line
    )
    assert NAME in installed_row
    assert "0.1.0" in installed_row
    assert "alice" in installed_row
    assert re.search(rf"{NAME}\s+0\.1\.1\s+awaiting-approval\s", out)
    assert re.search(rf"{NAME}\s+0\.1\.0\s+approved\s", out)


def test_list_table_escapes_control_characters_but_json_is_unchanged(root, capsys):
    make_version(root, NAME, "0.1.0")
    assert _install(root) == 0
    registry_path = root / "skills" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    evil = "/tmp/\x1b[31mred\x07\x7f"
    for record in (*registry["installs"], *registry["current"].values()):
        record["path"] = evil
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    capsys.readouterr()
    assert run(root, "list") == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "\x7f" not in out
    assert "/tmp/\\x1b[31mred\\x07\\x7f" in out
    assert run(root, "list", "--json") == 0
    assert json.loads(capsys.readouterr().out)["installed"][0]["path"] == evil


def test_list_empty_project_exits_0(root, capsys):
    assert run(root, "list") == 0
    assert run(root, "list", "--json") == 0
    assert not (root / "skills").exists()
    out = capsys.readouterr().out
    assert '"installed": []' in out


@pytest.mark.parametrize("bad", ["..", "../evil", "a/b", "a\\b", "Upper", ""])
def test_invalid_names_exit_2_and_write_nothing(root, home, bad):
    make_version(root, NAME, "0.1.0")
    before = (tree(root), tree(home))
    assert run(root, "install", bad, "--platform", "claude", "--scope", "project") == 2
    assert run(root, "rollback", bad) == 2
    assert (tree(root), tree(home)) == before
    with pytest.raises(install.InstallError):
        install.install_skill(root, bad, "claude", "project")


def test_non_ascii_or_overlong_versions_exit_2_and_install_nothing(root, home):
    make_version(root, NAME, "1234567890.0.0")
    make_version(root, NAME, "0.1.0")
    before = (tree(root), tree(home))
    for bad in ("\u0661.\u0662.\u0663", "1234567890.0.0", "0.0.1234567890", "1.2"):
        assert _install(root, "claude", "project", "--version", bad) == 2
    assert (tree(root), tree(home)) == before
    assert _install(root, "claude", "project") == 0  # the overlong directory is ignored


def test_missing_skill_and_missing_version_exit_2(root, home):
    make_version(root, NAME, "0.1.0")
    before = (tree(root), tree(home))
    assert (
        run(root, "install", "other", "--platform", "claude", "--scope", "project") == 2
    )
    assert _install(root, "claude", "project", "--version", "9.9.9") == 2
    assert _install(root, "claude", "project", "--version", "latest") == 2
    assert (tree(root), tree(home)) == before


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
def test_symlinked_skills_dir_is_refused(root, tmp_path, home):
    make_version(root, NAME, "0.1.0")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "skills").symlink_to(outside, target_is_directory=True)
    assert _install(root) == 2
    assert tree(outside) == {}
    assert not (root / "skills" / "registry.json").exists()


def test_install_source_has_no_process_or_vcs_access():
    source = (SRC / "install.py").read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source
    assert re.search(r"\bgit\b", source, re.IGNORECASE) is None
    assert "release" not in re.findall(
        r"^(?:from|import) skill_factory\.?(\w*)", source, re.MULTILINE
    )
    for path in SRC.rglob("*.py"):
        assert "git push" not in path.read_text(encoding="utf-8"), path
