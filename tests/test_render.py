"""Tests for `skillz render`: Claude/Codex variants, canonical immutability and the report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pytest

from skill_factory import capture, generate, render, yamlish

FIXTURES = Path(__file__).parent / "fixtures"
BINARY = bytes(range(256)) + b"\x00\xff\r\n"


def _canonical_skill(root: Path) -> Path:
    """A generated skill with scripts/, references/ and a binary asset."""
    spec = capture.capture(str(FIXTURES / "spec.json"))
    skill_dir = generate.generate(spec, root)
    (skill_dir / "scripts").mkdir(exist_ok=True)
    (skill_dir / "scripts" / "clean.sh").write_text("#!/bin/sh\necho clean\n", encoding="utf-8")
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "references" / "guide.md").write_text("# Guide\n\ntext\n", encoding="utf-8")
    (skill_dir / "references" / "deep").mkdir()
    (skill_dir / "references" / "deep" / "more.md").write_text("more\n", encoding="utf-8")
    (skill_dir / "assets").mkdir(exist_ok=True)
    (skill_dir / "assets" / "logo.bin").write_bytes(BINARY)
    return skill_dir


def _relative_files(root: Path, exclude: str | None = None) -> set[str]:
    found = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if exclude is not None:
        found = {f for f in found if f != exclude and not f.startswith(exclude + "/")}
    return found


def _canonical_hashes(skill_dir: Path) -> dict[str, str]:
    return {
        rel: hashlib.sha256((skill_dir / rel).read_bytes()).hexdigest()
        for rel in _relative_files(skill_dir, exclude=".factory")
    }


def _args(name: str, out: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    render.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["render", name, "--out", str(out)])


def test_claude_variant_matches_canonical_files(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    render.render_skill(skill_dir)
    dist = skill_dir / ".factory" / "dist" / "claude"
    canonical = _relative_files(skill_dir, exclude=".factory")
    assert "assets/logo.bin" in canonical
    assert _relative_files(dist) == canonical
    for rel in canonical:
        assert (dist / rel).read_bytes() == (skill_dir / rel).read_bytes()


def test_codex_variant_adds_openai_yaml(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    render.render_skill(skill_dir)
    dist = skill_dir / ".factory" / "dist" / "codex"
    canonical = _relative_files(skill_dir, exclude=".factory")
    assert _relative_files(dist) == canonical | {"agents/openai.yaml"}
    for rel in canonical:
        assert (dist / rel).read_bytes() == (skill_dir / rel).read_bytes()
    data = yamlish.loads((dist / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    lines = (skill_dir / "SKILL.md").read_text(encoding="utf-8").split("\n")
    front = yamlish.loads("\n".join(lines[1 : lines.index("---", 1)]) + "\n")
    assert data["name"] == front["name"] == skill_dir.name
    assert data["description"] == front["description"]
    assert data["metadata"]["skill_file"] == "SKILL.md"
    assert isinstance(data["interface"], dict)
    assert b"\r" not in (dist / "agents" / "openai.yaml").read_bytes()


def test_canonical_files_are_byte_identical_across_renders(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    before = _canonical_hashes(skill_dir)
    render.render_skill(skill_dir)
    after_one = _canonical_hashes(skill_dir)
    render.render_skill(skill_dir)
    after_two = _canonical_hashes(skill_dir)
    assert before == after_one == after_two
    assert (skill_dir / ".factory" / "spec.json").is_file()


def test_rerender_removes_stale_dist_files(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    render.render_skill(skill_dir)
    dist = skill_dir / ".factory" / "dist"
    (dist / "claude" / "stale.txt").write_text("old", encoding="utf-8")
    (dist / "codex" / "agents" / "old.yaml").write_text("old: 1\n", encoding="utf-8")
    (skill_dir / "references" / "guide.md").unlink()
    render.render_skill(skill_dir)
    assert not (dist / "claude" / "stale.txt").exists()
    assert not (dist / "codex" / "agents" / "old.yaml").exists()
    assert not (dist / "claude" / "references" / "guide.md").exists()
    assert (dist / "claude" / "SKILL.md").is_file()


def test_dist_never_contains_factory_dir(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    render.render_skill(skill_dir)
    render.render_skill(skill_dir)
    for platform in ("claude", "codex"):
        assert not (skill_dir / ".factory" / "dist" / platform / ".factory").exists()


def test_name_mismatch_raises_and_run_returns_2(tmp_path, capsys):
    skill_dir = _canonical_skill(tmp_path)
    renamed = skill_dir.with_name("other-name")
    skill_dir.rename(renamed)
    with pytest.raises(render.RenderError, match="does not match"):
        render.render_skill(renamed)
    assert render.run(_args("other-name", tmp_path)) == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert not (renamed / ".factory" / "dist").exists()


@pytest.mark.parametrize(
    "content",
    ["no frontmatter here\n", "---\nname: csv-data-cleaner\n", "---\nname: [a\n---\n", "---\n---\n"],
)
def test_bad_frontmatter_raises_and_run_returns_2(tmp_path, content):
    skill_dir = _canonical_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    with pytest.raises(render.RenderError):
        render.render_skill(skill_dir)
    assert render.run(_args(skill_dir.name, tmp_path)) == 2


def test_missing_skill_md_and_missing_skill_return_2(tmp_path, capsys):
    skill_dir = _canonical_skill(tmp_path)
    (skill_dir / "SKILL.md").unlink()
    with pytest.raises(render.RenderError):
        render.render_skill(skill_dir)
    assert render.run(_args(skill_dir.name, tmp_path)) == 2
    assert render.run(_args("no-such-skill", tmp_path)) == 2
    assert render.run(_args("Bad_Name", tmp_path)) == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_report_files_list_both_variants(tmp_path, capsys):
    skill_dir = _canonical_skill(tmp_path)
    assert render.run(_args(skill_dir.name, tmp_path)) == 0
    out = capsys.readouterr().out
    assert "rendered csv-data-cleaner" in out
    factory = skill_dir / ".factory"
    assert str(factory / "render.json") in out
    assert str(factory / "render.md") in out
    data = json.loads((factory / "render.json").read_text(encoding="utf-8"))
    assert data["skill"] == skill_dir.name
    assert set(data["variants"]) == {"claude", "codex"}
    assert data["variants"]["claude"]["target"] == f".claude/skills/{skill_dir.name}/"
    assert data["variants"]["codex"]["target"] == f".agents/skills/{skill_dir.name}/"
    for platform, variant in data["variants"].items():
        dist = skill_dir / variant["dist"]
        assert dist == factory / "dist" / platform
        listed = {item["path"]: item["sha256"] for item in variant["files"]}
        assert set(listed) == _relative_files(dist)
        for rel, digest in listed.items():
            assert hashlib.sha256((dist / rel).read_bytes()).hexdigest() == digest
    assert "agents/openai.yaml" in {i["path"] for i in data["variants"]["codex"]["files"]}
    md = (factory / "render.md").read_text(encoding="utf-8")
    assert skill_dir.name in md
    assert "agents/openai.yaml" in md


def test_report_write_returns_paths(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    json_path, md_path = render.render_skill(skill_dir).write(tmp_path / "elsewhere")
    assert json_path.name == "render.json" and json_path.is_file()
    assert md_path.name == "render.md" and md_path.is_file()


def test_symlinks_are_skipped_and_recorded(tmp_path):
    skill_dir = _canonical_skill(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("outside", encoding="utf-8")
    try:
        os.symlink(secret, skill_dir / "references" / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    report = render.render_skill(skill_dir)
    assert report.skipped == ["references/link.txt"]
    for platform in ("claude", "codex"):
        assert not (skill_dir / ".factory" / "dist" / platform / "references" / "link.txt").exists()
    assert report.to_dict()["skipped_symlinks"] == ["references/link.txt"]
    assert secret.read_text(encoding="utf-8") == "outside"
