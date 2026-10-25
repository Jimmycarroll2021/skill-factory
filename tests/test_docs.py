"""Tests for the public docs and the factory's own meta-skill."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from skill_factory import cli, evalcases, validate, yamlish

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
# Captured with `skillz capture-failure --should-trigger` on skills/skill-factory (dogfooding).
CAPTURED_PROMPTS = ["Help me write a SKILL.md file so my agent learns this workflow"]
EVIDENCE = "docs/evidence/skill-factory-challenge.md"
# The fields of skill-receipt/1, as docs/RECEIPT.md must document them.
RECEIPT_FIELDS = [
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
    diagram = re.search(r'<img src="(docs/assets/pipeline\.svg)"', readme)
    assert diagram, "README needs the pipeline diagram"
    _in_order(_read(diagram.group(1)), STAGES)
    listing = readme.split("The same stages as a plain list:", 1)[1]
    _in_order(listing, STAGES[:-1])


def test_readme_diagrams_exist_and_parse():
    import xml.dom.minidom

    readme = _read("README.md")
    sources = re.findall(r'<img src="(docs/assets/[a-z]+\.svg)"', readme)
    assert len(sources) >= 6, sources
    for src in sources:
        path = REPO / src
        assert path.is_file(), src
        xml.dom.minidom.parse(str(path))


def test_readme_says_offline_grading_is_heuristic():
    readme = _read("README.md")
    assert "heuristic" in readme
    assert "skipped: no runner" in readme


def test_meta_skill_validates_cleanly():
    report = validate.validate_skill(SKILL_DIR)
    assert report.ok
    assert report.errors == 0 and report.warnings == 0


def test_every_committed_skill_validates_cleanly():
    # scripts/check.py gates skills/ too; both standards must accept every skill.
    skills = sorted(p.parent for p in (REPO / "skills").glob("*/SKILL.md"))
    assert len(skills) >= 2
    for skill in skills:
        report = validate.validate_skill(skill)
        assert report.findings == [], (skill.name, report.findings)


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


def test_readme_documents_the_challenger_loop():
    readme = _read("README.md")
    assert "## Self-improvement (the challenger loop)" in readme
    for command in ("capture-failure", "candidate", "challenge", "promote"):
        assert f"skillz {command}" in readme, command
    for verdict in ("candidate-unsafe", "candidate-worse", "candidate-better", "no-difference"):
        assert verdict in readme, verdict
    assert "never promote" in readme


def test_readme_documents_the_model_backed_quality_tier():
    readme = _read("README.md")
    heading = "## Model-backed quality tier"
    assert heading in readme
    section = readme.split(heading, 1)[1].split("\n## ", 1)[0]
    _in_order(
        section,
        [
            "static validation",
            "offline trigger heuristics",
            "Claude/Codex behavioural execution",
            "independent model judge",
            "cross-model evaluation",
            "human approval",
        ],
    )
    assert "skillz judge" in section
    assert "strict" in section
    assert "lexical tier is the default" in section
    assert "nothing calls a model without `--runner`" in section
    assert "docs/evidence/judge-claude.md" in section


def test_readme_command_table_lists_judge_and_runner_flags():
    rows = [line for line in _read("README.md").splitlines() if line.startswith("| `skillz ")]
    by_name = {row.split("`")[1].split()[1]: row for row in rows}
    assert "[--judge {claude,codex}]" in by_name["judge"]
    for name in ("run", "package", "challenge"):
        assert "[--runner {claude,codex}]" in by_name[name], name
        assert "[--judge {claude,codex}]" in by_name[name], name


def test_meta_skill_documents_the_loop_and_keeps_promote_human_only():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for command in ("capture-failure", "candidate", "challenge"):
        assert f"skillz {command}" in body, command
    assert "Never run `skillz promote" in body


def test_readme_documents_receipts():
    readme = _read("README.md")
    assert "skillz receipt" in readme
    assert "docs/RECEIPT.md" in readme
    assert (REPO / "docs" / "RECEIPT.md").is_file()
    for level in ("unverified", "self-judged", "verified"):
        assert level in readme, level


def test_receipt_standard_lists_every_schema_field():
    text = _read("docs/RECEIPT.md")
    assert "skill-receipt/1" in text
    for field in RECEIPT_FIELDS:
        assert f"| `{field}` |" in text, field
    for level in ("unverified", "self-judged", "verified"):
        assert f"`{level}`" in text, level
    for status in ("clean", "regressed", "no-baseline"):
        assert f"`{status}`" in text, status
    assert "arxiv.org/abs/2607.22520" in text
    assert len(text.splitlines()) < 150


def test_meta_skill_starts_from_the_receipt_and_refuses_unrelated_work():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "skillz receipt" in body
    assert "## Get a receipt for a skill" in body
    meta = yamlish.loads(body.split("---\n", 2)[1])
    assert "NOT for writing application code" in meta["description"]


def test_action_yml_is_a_composite_action_that_runs_the_gate():
    text = _read("action.yml")
    # The in-package YAML reader stops at one level of nesting, and PyYAML is not a
    # dependency, so check the structure line by line instead of parsing the document.
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert re.match(r"^ *(- )?[A-Za-z_-]+:( .*)?$", line), line
    assert re.search(r"^runs:$", text, re.MULTILINE)
    assert 'using: "composite"' in text
    assert "shell: bash" in text
    assert re.search(r"^inputs:\n  root:\n", text, re.MULTILINE)
    assert "${{ github.workspace }}" in text
    command = re.search(r"^ +run: (.+)$", text, re.MULTILINE)
    assert command, "action.yml needs one run step"
    assert '"${{ github.action_path }}/scripts/check.py" --root "${{ inputs.root }}"' in command.group(1)
    assert "uses: Jimmycarroll2021/skill-factory@main" in _read("README.md")


def test_action_command_passes_on_this_repository():
    # The same command action.yml runs, with the action path and root both set to this repo.
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check.py"), "--root", str(REPO)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().splitlines()[-1] == "PASS"


def test_dogfood_evidence_has_an_acceptable_verdict():
    evidence = REPO / EVIDENCE
    assert evidence.is_file()
    text = evidence.read_text(encoding="utf-8")
    assert "Verdict: **candidate-better**" in text or "Verdict: **no-difference**" in text
    for prompt in CAPTURED_PROMPTS:
        assert prompt in text


def test_captured_prompts_match_the_committed_meta_skill_description():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    meta = yamlish.loads(text.split("---\n", 2)[1])
    for prompt in CAPTURED_PROMPTS:
        case = evalcases.Case("cap", "positive", prompt, "captured")
        (grade,) = evalcases.grade_offline([case], meta["name"], meta["description"])
        assert grade.score >= evalcases.MATCH_THRESHOLD, prompt
