"""Tests for the public docs and the factory's own meta-skill."""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from skill_factory import cli, validate, yamlish

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "skill-factory"
STAGES = [
    "describe",
    "capture",
    "generate",
    "validate",
    "render",
    "evaluate",
    "regression",
    "package",
    "human approval gate",
    "install",
    "registry",
]


def _read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def _subcommands() -> list[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            return sorted(action.choices)
    raise AssertionError("no subcommands found")


def _in_order(text: str, words: list[str]) -> None:
    position = 0
    for word in words:
        found = text.find(word, position)
        assert found != -1, f"{word!r} missing or out of order"
        position = found + len(word)


def test_readme_documents_run_and_every_subcommand():
    readme = _read("README.md")
    assert "skillz run" in readme
    for name in {*_subcommands(), "run"}:
        assert f"skillz {name}" in readme, name


def test_readme_has_ordered_pipeline_with_approval_gate_before_install():
    readme = _read("README.md")
    assert "approve" in readme
    mermaid = re.search(r"```mermaid\n(.*?)```", readme, re.DOTALL)
    assert mermaid, "README needs a Mermaid pipeline diagram"
    _in_order(mermaid.group(1), STAGES)
    listing = readme.split("The same stages as a plain list:", 1)[1]
    _in_order(listing, STAGES[:-1])


def test_readme_says_offline_grading_is_heuristic():
    readme = _read("README.md")
    assert "heuristic" in readme
    assert "skipped: no runner" in readme


def test_meta_skill_validates_cleanly():
    report = validate.validate_skill(SKILL_DIR)
    assert report.ok
    assert report.errors == 0 and report.warnings == 0


def test_meta_skill_frontmatter_name_matches_directory():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    meta = yamlish.loads(block)
    assert meta["name"] == SKILL_DIR.name == "skill-factory"
    assert 0 < len(meta["description"]) <= 1024
    assert len(text.splitlines()) < 100


def test_meta_skill_leaves_approval_to_the_human():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "never run" in body and "approve" in body


def test_meta_skill_validates_through_the_cli(tmp_path):
    shutil.copytree(SKILL_DIR, tmp_path / "skills" / "skill-factory")
    assert cli.main(["validate", "skill-factory", "--out", str(tmp_path)]) == 0


def test_changelog_has_first_release():
    assert "0.1.0" in _read("CHANGELOG.md")


def test_contributing_requires_tests():
    text = _read("CONTRIBUTING.md").lower()
    assert "test" in text and "fails without" in text


def test_pyproject_declares_readme_that_exists():
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    assert project["readme"] == "README.md"
    assert (REPO / project["readme"]).is_file()


def test_gitignore_hides_factory_output_of_skills():
    assert "skills/*/.factory/" in _read(".gitignore").splitlines()
