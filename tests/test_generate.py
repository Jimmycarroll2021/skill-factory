"""Tests for `skillz new`: SKILL.md generation, resource directories and overwrite rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from skill_factory import capture as cap
from skill_factory import generate as gen
from skill_factory import yamlish
from skill_factory.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# fixture file -> (expected skill name, resource directories it requests)
CASES = {
    "paragraph.txt": ("draft-release-notes-software", set()),
    "spec.yaml": ("api-doc-reviewer", {"references"}),
    "spec.json": ("csv-data-cleaner", {"scripts", "assets"}),
}


def _frontmatter(skill_md: Path) -> dict:
    lines = skill_md.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return yamlish.loads("\n".join(lines[1:end]) + "\n")


def _new(fixture: str, out: Path, *extra: str) -> int:
    return main(["new", str(FIXTURES / fixture), "--out", str(out), *extra])


@pytest.mark.parametrize("fixture", CASES)
def test_new_generates_compliant_skill(fixture, tmp_path):
    expected_name, resources = CASES[fixture]
    assert _new(fixture, tmp_path) == 0
    skill_dir = tmp_path / "skills" / expected_name
    front = _frontmatter(skill_dir / "SKILL.md")
    assert front["name"] == skill_dir.name == expected_name
    assert NAME_RE.match(front["name"])
    assert len(front["name"]) <= 64
    assert 0 < len(front["description"].strip()) <= 1024
    for resource in cap.RESOURCE_DIRS:
        assert (skill_dir / resource).is_dir() is (resource in resources)


@pytest.mark.parametrize("fixture", CASES)
def test_spec_json_round_trips(fixture, tmp_path):
    expected_name, _ = CASES[fixture]
    assert _new(fixture, tmp_path) == 0
    spec_path = tmp_path / "skills" / expected_name / ".factory" / "spec.json"
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    assert data == cap.capture(str(FIXTURES / fixture)).to_dict()
    assert cap.SkillSpec.from_dict(data) == cap.capture(str(FIXTURES / fixture))


def test_optional_frontmatter_only_when_given(tmp_path):
    assert _new("paragraph.txt", tmp_path) == 0
    plain = _frontmatter(tmp_path / "skills" / "draft-release-notes-software" / "SKILL.md")
    assert set(plain) == {"name", "description"}

    assert _new("spec.yaml", tmp_path) == 0
    rich = _frontmatter(tmp_path / "skills" / "api-doc-reviewer" / "SKILL.md")
    assert rich["license"] == "MIT"
    assert rich["allowed-tools"] == "Read Grep"
    assert rich["metadata"] == {"author": "skill-factory", "version": "1.0"}


def test_body_sections_and_size(tmp_path):
    assert _new("spec.json", tmp_path) == 0
    text = (tmp_path / "skills" / "csv-data-cleaner" / "SKILL.md").read_text(encoding="utf-8")
    assert "## When to use" in text
    assert "- clean this CSV file" in text
    assert "## When not to use" in text
    assert "## What to do" in text
    assert "`scripts/`" in text
    assert len(text.splitlines()) < 100


def test_body_stays_under_100_lines_for_large_specs(tmp_path):
    many = [f"item {i}" for i in range(200)]
    spec = cap.SkillSpec(
        name="big-skill", description="Big.", triggers=many, non_triggers=many, behaviours=many
    )
    skill_dir = gen.generate(spec, tmp_path)
    assert len((skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 100


def test_second_new_refuses_without_force(tmp_path, capsys):
    assert _new("spec.yaml", tmp_path) == 0
    skill_md = tmp_path / "skills" / "api-doc-reviewer" / "SKILL.md"
    skill_md.write_text("hand edited\n", encoding="utf-8", newline="\n")
    capsys.readouterr()
    assert _new("spec.yaml", tmp_path) == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert skill_md.read_text(encoding="utf-8") == "hand edited\n"
    assert _new("spec.yaml", tmp_path, "--force") == 0
    assert _frontmatter(skill_md)["name"] == "api-doc-reviewer"


def test_generate_refuses_existing_skill(tmp_path):
    spec = cap.capture(str(FIXTURES / "spec.yaml"))
    gen.generate(spec, tmp_path)
    with pytest.raises(gen.GenerateError):
        gen.generate(spec, tmp_path)
    assert gen.generate(spec, tmp_path, force=True).name == "api-doc-reviewer"


def test_generate_validates_spec(tmp_path):
    with pytest.raises(cap.SpecError):
        gen.generate(cap.SkillSpec(name="Bad Name", description="x"), tmp_path)
    assert not (tmp_path / "skills").exists()


def test_name_override(tmp_path):
    assert _new("paragraph.txt", tmp_path, "--name", "my-notes") == 0
    skill_dir = tmp_path / "skills" / "my-notes"
    assert _frontmatter(skill_dir / "SKILL.md")["name"] == "my-notes"
    assert not (tmp_path / "skills" / "draft-release-notes-software").exists()


def test_invalid_name_is_a_clean_error(tmp_path, capsys):
    assert _new("paragraph.txt", tmp_path, "--name", "Not Kebab") == 2
    assert "skillz: error:" in capsys.readouterr().err
    assert not (tmp_path / "skills").exists()


def test_paragraph_text_argument(tmp_path, capsys):
    text = "Summarise meeting notes into action items. Use it when the user pastes minutes."
    assert main(["new", text, "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out.strip()
    assert Path(printed).is_dir()
    assert (Path(printed) / "SKILL.md").is_file()


@pytest.mark.parametrize("argv", [["--help"], ["new", "--help"]])
def test_help_exits_zero(argv, capsys):
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 0
    assert "skillz" in capsys.readouterr().out


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("skillz ")


@pytest.mark.parametrize("fixture", CASES)
def test_generated_files_are_lf_only_utf8(fixture, tmp_path):
    expected_name, _ = CASES[fixture]
    assert _new(fixture, tmp_path) == 0
    skill_dir = tmp_path / "skills" / expected_name
    files = [p for p in skill_dir.rglob("*") if p.is_file()]
    assert files
    for path in files:
        data = path.read_bytes()
        assert b"\r\n" not in data
        data.decode("utf-8")
