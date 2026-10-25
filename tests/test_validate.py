"""Tests for the static skill validator."""

from __future__ import annotations

import argparse
import builtins
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from skill_factory import validate as val

FIXTURES = Path(__file__).parent / "fixtures" / "skills"
MB = 1_048_576


def _copy(tmp_path: Path, case: str) -> Path:
    """Copy a committed fixture into tmp_path/skills/<case>/ so fixtures are never written to."""
    dest = tmp_path / "skills" / case
    shutil.copytree(FIXTURES / case, dest)
    return dest


def _make(
    tmp_path: Path,
    name: str,
    description: str = "Does a thing. NOT for anything else.",
    body: str = "# Body\n",
):
    skill = tmp_path / "skills" / name
    skill.mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    (skill / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    return skill


def _run(tmp_path: Path, name: str) -> int:
    return val.run(argparse.Namespace(name=name, out=tmp_path))


def _rules(report: val.Report) -> set[str]:
    return {f.rule for f in report.findings}


def _only(report: val.Report, rule: str) -> list[val.Finding]:
    return [f for f in report.findings if f.rule == rule]


def test_clean_fixture_is_ok(tmp_path, capsys):
    skill = _copy(tmp_path, "clean-skill")
    report = val.validate_skill(skill)
    assert report.ok
    assert report.findings == []
    assert _run(tmp_path, "clean-skill") == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert str(skill / ".factory" / "validate.json") in out
    assert (skill / ".factory" / "validate.json").is_file()
    assert (skill / ".factory" / "validate.md").is_file()


@pytest.mark.parametrize(
    ("case", "rule"),
    [
        ("name-invalid", "name-invalid"),
        ("name-mismatch", "name-mismatch"),
        ("description-empty", "description-empty"),
        ("path-not-allowed", "path-not-allowed"),
        ("frontmatter-missing", "frontmatter-missing"),
        ("frontmatter-invalid", "frontmatter-invalid"),
        ("network-call", "network-call"),
        ("destructive-shell", "destructive-shell"),
    ],
)
def test_fixture_fires_its_rule(tmp_path, case, rule):
    skill = _copy(tmp_path, case)
    report = val.validate_skill(skill)
    hits = _only(report, rule)
    assert hits, f"{rule} not reported: {report.findings}"
    assert all(f.severity == "error" for f in hits)
    assert not report.ok
    assert _run(tmp_path, case) == 1


def test_network_fixture_covers_curl_and_urllib(tmp_path):
    report = val.validate_skill(_copy(tmp_path, "network-call"))
    paths = {f.path for f in _only(report, "network-call")}
    assert paths == {"scripts/fetch.sh", "scripts/fetch-urllib.sh"}
    assert all(f.line for f in _only(report, "network-call"))


def test_destructive_finding_points_at_the_script(tmp_path):
    report = val.validate_skill(_copy(tmp_path, "destructive-shell"))
    (finding,) = _only(report, "destructive-shell")
    assert finding.path == "scripts/clean.sh"
    assert finding.line == 2


def test_path_not_allowed_names_the_entry(tmp_path):
    report = val.validate_skill(_copy(tmp_path, "path-not-allowed"))
    assert [f.path for f in _only(report, "path-not-allowed")] == ["src"]


def test_name_too_long(tmp_path):
    name = "a" * 65
    _make(tmp_path, name)
    report = val.validate_skill(tmp_path / "skills" / name)
    assert _rules(report) == {"name-too-long"}
    assert _run(tmp_path, name) == 1


def test_name_with_trailing_newline_is_invalid(tmp_path):
    skill = tmp_path / "skills" / "abc"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: "abc\\n"\ndescription: Fine.\n---\n\nbody\n', encoding="utf-8"
    )
    assert "name-invalid" in _rules(val.validate_skill(skill))


def test_description_too_long(tmp_path):
    _make(tmp_path, "long-desc", description="d" * 1025)
    report = val.validate_skill(tmp_path / "skills" / "long-desc")
    assert _rules(report) == {"description-too-long"}
    assert _run(tmp_path, "long-desc") == 1
    _make(tmp_path, "max-desc", description="d" * 1024)
    assert val.validate_skill(tmp_path / "skills" / "max-desc").ok


def test_body_too_long_is_only_a_warning(tmp_path, capsys):
    _make(tmp_path, "long-body", body="line\n" * 501)
    report = val.validate_skill(tmp_path / "skills" / "long-body")
    (finding,) = report.findings
    assert (finding.rule, finding.severity) == ("body-too-long", "warning")
    assert report.ok
    assert _run(tmp_path, "long-body") == 0
    assert "1 warnings" in capsys.readouterr().out
    _make(tmp_path, "ok-body", body="line\n" * 500)
    assert val.validate_skill(tmp_path / "skills" / "ok-body").findings == []


def test_file_too_large(tmp_path):
    skill = _make(tmp_path, "big-file")
    (skill / "assets").mkdir()
    (skill / "assets" / "ok.bin").write_bytes(b"x" * MB)
    (skill / "assets" / "big.bin").write_bytes(b"x" * (MB + 1))
    report = val.validate_skill(skill)
    (finding,) = _only(report, "file-too-large")
    assert finding.path == "assets/big.bin"
    assert finding.severity == "error"
    assert _run(tmp_path, "big-file") == 1


def test_skill_too_large(tmp_path):
    body = "".join(f"See `references/part{index}.md`.\n" for index in range(6))
    skill = _make(tmp_path, "big-skill", body=body)
    (skill / "references").mkdir()
    for index in range(6):  # six files of exactly 1 MB: each is fine, the sum is over 5 MB
        (skill / "references" / f"part{index}.md").write_bytes(b"x" * MB)
    report = val.validate_skill(skill)
    assert _rules(report) == {"skill-too-large"}
    assert _run(tmp_path, "big-skill") == 1


def test_size_limits_are_module_constants(tmp_path, monkeypatch):
    monkeypatch.setattr(val, "MAX_FILE_BYTES", 10)
    skill = _make(tmp_path, "tiny-limit")
    (skill / "references").mkdir()
    (skill / "references" / "a.md").write_text("0123456789ab", encoding="utf-8")
    assert "file-too-large" in _rules(val.validate_skill(skill))


def _secret_cases() -> dict[str, str]:
    return {
        "aws": "AKIA" + "Z" * 16,
        "openai": "sk-" + "a1B2" * 8,
        "github": "ghp_" + "a" * 36,
        "pem": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "assignment": "api_key = " + "q" * 24,
        "url": "postgres://user:" + "hunter2hunter2" + "@db.example.com/app",
    }


@pytest.mark.parametrize("kind", sorted(_secret_cases()))
def test_secret_is_reported_and_redacted(tmp_path, kind):
    secret = _secret_cases()[kind]
    skill = _make(tmp_path, "leaky")
    (skill / "references").mkdir()
    (skill / "references" / "notes.md").write_text(f"intro\n{secret}\n", encoding="utf-8")
    report = val.validate_skill(skill)
    (finding,) = _only(report, "secret")
    assert finding.severity == "error"
    assert (finding.path, finding.line) == ("references/notes.md", 2)
    assert _run(tmp_path, "leaky") == 1
    value = secret.split("=")[-1].strip() if kind == "assignment" else secret
    if kind == "url":
        value = "hunter2hunter2"
    for blob in (
        finding.message,
        (skill / ".factory" / "validate.json").read_text(encoding="utf-8"),
        (skill / ".factory" / "validate.md").read_text(encoding="utf-8"),
    ):
        assert value not in blob


def test_secret_in_skill_md_and_scripts_is_found(tmp_path):
    token = "ghp_" + "b" * 36
    skill = _make(tmp_path, "leaky-two", body=f"token: {token}\n")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.sh").write_text(f"export X={token}\n", encoding="utf-8")
    paths = {f.path for f in _only(val.validate_skill(skill), "secret")}
    assert paths == {"SKILL.md", "scripts/run.sh"}


def test_urllib_script_is_a_network_call(tmp_path):
    skill = _make(tmp_path, "py-net")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "get.py").write_text(
        "import urllib.request\nurllib.request.urlopen('https://example.com')\n", encoding="utf-8"
    )
    assert {f.line for f in _only(val.validate_skill(skill), "network-call")} == {1, 2}


def test_prose_mentioning_curl_outside_scripts_is_allowed(tmp_path):
    skill = _make(tmp_path, "docs-only")
    (skill / "references").mkdir()
    (skill / "references" / "faq.md").write_text("Do not run rm -rf or curl.\n", encoding="utf-8")
    assert val.validate_skill(skill).ok


def test_factory_dir_is_skipped(tmp_path):
    skill = _copy(tmp_path, "clean-skill")
    factory = skill / ".factory"
    (factory / "dist").mkdir(parents=True)
    (factory / "dist" / "leak.txt").write_text("AKIA" + "Z" * 16 + "\n", encoding="utf-8")
    (factory / "big.bin").write_bytes(b"x" * (6 * MB))
    assert val.validate_skill(skill).ok


def test_rerun_ignores_its_own_reports(tmp_path):
    _copy(tmp_path, "network-call")
    assert _run(tmp_path, "network-call") == 1
    first = json.loads((tmp_path / "skills/network-call/.factory/validate.json").read_text("utf-8"))
    assert _run(tmp_path, "network-call") == 1
    second = json.loads((tmp_path / "skills/network-call/.factory/validate.json").read_text("utf-8"))
    assert first == second


def test_symlinks_are_refused_and_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("AKIA" + "Z" * 16 + "\n", encoding="utf-8")
    skill = _make(tmp_path, "linky")
    try:
        (skill / "references").symlink_to(outside, target_is_directory=True)
        (skill / "scripts").mkdir()
        (skill / "scripts" / "s.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    report = val.validate_skill(skill)
    assert {f.path for f in _only(report, "path-not-allowed")} == {"references", "scripts/s.txt"}
    assert "secret" not in _rules(report)


def test_validation_never_executes_the_skill(tmp_path, monkeypatch):
    skill = _copy(tmp_path, "no-exec")
    sentinel = tmp_path / "SENTINEL"
    (skill / "scripts" / "evil.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n", encoding="utf-8"
    )
    (skill / "scripts" / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n", encoding="utf-8"
    )

    def boom(*args, **kwargs):
        raise AssertionError("the validator must not execute anything")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(os, "system", boom)
    monkeypatch.setattr(os, "popen", boom)
    monkeypatch.setattr(builtins, "exec", boom)
    for prefix in ("exec", "spawn"):
        for attr in dir(os):
            if attr.startswith(prefix):
                monkeypatch.setattr(os, attr, boom)
    monkeypatch.setattr(os, "posix_spawn", boom, raising=False)
    monkeypatch.setattr(os, "posix_spawnp", boom, raising=False)

    assert _run(tmp_path, "no-exec") == 0
    assert not sentinel.exists()
    assert not (skill / "SENTINEL").exists()


def test_report_json_round_trips_and_markdown_names_rules(tmp_path):
    skill = _copy(tmp_path, "network-call")
    token = "ghp_" + "c" * 36
    (skill / "references").mkdir()
    (skill / "references" / "n.md").write_text(token + "\n", encoding="utf-8")
    report = val.validate_skill(skill)
    json_path, md_path = report.write(skill / ".factory")
    assert (json_path.name, md_path.name) == ("validate.json", "validate.md")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data == report.to_dict()
    assert data["skill"] == "network-call"
    assert data["ok"] is False
    assert data["errors"] == sum(f.severity == "error" for f in report.findings)
    assert [val.Finding(**f) for f in data["findings"]] == report.findings
    markdown = md_path.read_text(encoding="utf-8")
    for rule in _rules(report):
        assert f"`{rule}`" in markdown
    assert "FAIL" in markdown
    assert token not in markdown
    assert "\r" not in markdown


def test_clean_markdown_says_pass(tmp_path):
    skill = _copy(tmp_path, "clean-skill")
    _, md_path = val.validate_skill(skill).write(skill / ".factory")
    assert "PASS" in md_path.read_text(encoding="utf-8")


def test_missing_skill_or_skill_md_exits_2(tmp_path, capsys):
    assert _run(tmp_path, "nope") == 2
    assert "skillz: error:" in capsys.readouterr().err
    (tmp_path / "skills" / "empty").mkdir(parents=True)
    assert _run(tmp_path, "empty") == 2
    assert "SKILL.md" in capsys.readouterr().err
    assert not (tmp_path / "skills" / "empty" / ".factory").exists()


def test_path_traversal_name_exits_2(tmp_path, capsys):
    assert _run(tmp_path, "..") == 2
    assert _run(tmp_path, "a/b") == 2
    assert "skillz: error:" in capsys.readouterr().err


def test_validate_skill_raises_for_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        val.validate_skill(tmp_path / "missing")


def test_add_parser_registers_validate_with_out(tmp_path):
    parser = argparse.ArgumentParser(prog="skillz")
    sub = parser.add_subparsers(dest="command", required=True)
    val.add_parser(sub)
    args = parser.parse_args(["validate", "some-skill"])
    assert (args.command, args.name, args.out) == ("validate", "some-skill", None)
    args = parser.parse_args(["validate", "some-skill", "--out", str(tmp_path)])
    assert args.out == tmp_path
    _copy(tmp_path, "clean-skill")
    assert val.run(parser.parse_args(["validate", "clean-skill", "--out", str(tmp_path)])) == 0


def test_run_defaults_to_cwd(tmp_path, monkeypatch):
    _copy(tmp_path, "clean-skill")
    monkeypatch.chdir(tmp_path)
    assert val.run(argparse.Namespace(name="clean-skill", out=None)) == 0


def test_unreadable_encoding_is_frontmatter_invalid(tmp_path):
    skill = tmp_path / "skills" / "bad-utf8"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"---\nname: bad-utf8\n---\n\xff\xfe\n")
    assert "frontmatter-invalid" in _rules(val.validate_skill(skill))


# -- quality rules from the claude-skills study --------------------------------------
# Warnings are signals that predicted weaker skills; errors are skills that are broken.

BOUNDED = "Summarise notes. Use when the user pastes notes. NOT for writing minutes."


def _warned(report: val.Report, rule: str) -> bool:
    return any(f.rule == rule and f.severity == "warning" for f in report.findings)


def _errored(report: val.Report, rule: str) -> bool:
    return any(f.rule == rule and f.severity == "error" for f in report.findings)


def test_description_without_a_boundary_warns(tmp_path):
    report = val.validate_skill(_make(tmp_path, "no-bound", "Summarise notes. Use when asked."))
    assert _warned(report, "description-no-boundary")
    assert report.ok
    clean = val.validate_skill(_make(tmp_path, "bounded", BOUNDED))
    assert "description-no-boundary" not in _rules(clean)


def test_sentence_initial_never_counts_as_a_boundary(tmp_path):
    report = val.validate_skill(_make(tmp_path, "never-skill", "Builds X. Never approves Y."))
    assert "description-no-boundary" not in _rules(report)


def test_keyword_list_in_description_warns(tmp_path):
    desc = BOUNDED + " Also use when the user mentions: help, write, md, file, learn."
    report = val.validate_skill(_make(tmp_path, "stuffed", f'"{desc}"'))
    assert _warned(report, "description-keyword-list")


def test_persona_opener_warns(tmp_path):
    report = val.validate_skill(_make(tmp_path, "persona", BOUNDED, "You are an expert note taker.\n"))
    assert _warned(report, "persona-opener")


def test_brochure_heading_warns(tmp_path):
    report = val.validate_skill(_make(tmp_path, "brochure", BOUNDED, "## Overview\n\nNotes.\n"))
    assert _warned(report, "brochure-heading")


def test_reserved_name_is_an_error(tmp_path):
    report = val.validate_skill(_make(tmp_path, "verify", BOUNDED))
    assert _errored(report, "reserved-name")
    assert not report.ok


def test_phantom_path_in_prose_warns(tmp_path):
    report = val.validate_skill(_make(tmp_path, "phantom", BOUNDED, "See `references/gone.md`.\n"))
    assert _warned(report, "phantom-path")


def test_orphan_resource_file_warns(tmp_path):
    skill = _make(tmp_path, "orphan", BOUNDED, "No files named here.\n")
    (skill / "references").mkdir()
    (skill / "references" / "notes.md").write_text("x\n", encoding="utf-8")
    report = val.validate_skill(skill)
    assert _warned(report, "orphan-file")
    named = _make(tmp_path, "named", BOUNDED, "See `references/notes.md`.\n")
    (named / "references").mkdir()
    (named / "references" / "notes.md").write_text("x\n", encoding="utf-8")
    assert "orphan-file" not in _rules(val.validate_skill(named))


def test_empty_resource_directory_warns(tmp_path):
    skill = _make(tmp_path, "empty-dir", BOUNDED)
    (skill / "scripts").mkdir()
    assert _warned(val.validate_skill(skill), "empty-resource-dir")


def test_documented_flag_missing_from_script_is_an_error(tmp_path):
    body = "```bash\npython3 scripts/tool.py --input notes.md --nope\n```\n"
    skill = _make(tmp_path, "flags", BOUNDED, body)
    (skill / "scripts").mkdir()
    (skill / "scripts" / "tool.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--input')\np.parse_args()\n",
        encoding="utf-8",
    )
    report = val.validate_skill(skill)
    hits = [f for f in report.findings if f.rule == "script-flag-missing"]
    assert hits and "--nope" in hits[0].message and hits[0].severity == "error"
    assert not any("--input" in f.message for f in hits)


# -- review round: false errors and escapes --------------------------------------------


def _script(skill: Path, rel: str, text: str) -> None:
    path = skill / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_script_without_argparse_is_not_flag_checked(tmp_path):
    body = "```bash\npython3 scripts/raw.py --in x\n```\n"
    skill = _make(tmp_path, "raw-argv", BOUNDED, body)
    _script(skill, "scripts/raw.py", "import sys\nprint(sys.argv)\n")
    report = val.validate_skill(skill)
    assert "script-flag-missing" not in _rules(report)
    assert report.ok


def test_dot_slash_script_paths_are_named_and_checked(tmp_path):
    body = "```bash\npython3 ./scripts/run.py --in x\n```\n"
    skill = _make(tmp_path, "dot-slash", BOUNDED, body)
    _script(skill, "scripts/run.py", "import argparse\nargparse.ArgumentParser().add_argument('--out')\n")
    report = val.validate_skill(skill)
    assert "orphan-file" not in _rules(report)
    assert _errored(report, "script-flag-missing")


def test_script_paths_outside_the_skill_are_never_read(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("import argparse\nargparse.ArgumentParser().add_argument('--y')\n", encoding="utf-8")
    body = "```bash\npython3 scripts/../../../outside.py --x\n```\n"
    skill = _make(tmp_path, "escape", BOUNDED, body)
    (skill / "scripts").mkdir()  # so the ../ path resolves to the outside file
    assert (skill / "scripts" / ".." / ".." / ".." / "outside.py").is_file()
    report = val.validate_skill(skill)
    assert "script-flag-missing" not in _rules(report)


def test_missing_script_in_a_command_is_an_error_but_a_prose_output_path_only_warns(tmp_path):
    cmd = _make(tmp_path, "no-script", BOUNDED, "```bash\npython3 scripts/gone.py\n```\n")
    assert _errored(val.validate_skill(cmd), "missing-script")
    prose = _make(tmp_path, "output-path", BOUNDED, "The script writes `assets/report.png`.\n")
    report = val.validate_skill(prose)
    assert _warned(report, "phantom-path")
    assert report.ok


def test_caches_and_imported_helpers_are_not_orphans(tmp_path):
    skill = _make(tmp_path, "helpers", BOUNDED, "```bash\npython3 scripts/main.py\n```\n")
    _script(skill, "scripts/main.py", "import argparse\nimport helper\nargparse.ArgumentParser().parse_args()\n")
    _script(skill, "scripts/helper.py", "X = 1\n")
    (skill / "scripts" / "__pycache__").mkdir()
    (skill / "scripts" / "__pycache__" / "helper.cpython-312.pyc").write_bytes(b"\0")
    (skill / "scripts" / ".DS_Store").write_bytes(b"\0")
    assert "orphan-file" not in _rules(val.validate_skill(skill))


def test_a_plain_negative_sentence_counts_as_a_boundary(tmp_path):
    report = val.validate_skill(_make(tmp_path, "limits", "Converts CSV to JSON. It does not handle PDFs."))
    assert "description-no-boundary" not in _rules(report)


# -- Codex review round ---------------------------------------------------------------


def test_a_script_path_written_by_example_code_is_not_a_missing_script(tmp_path):
    body = '```python\nPath("scripts/generated.py").write_text("print(123)\\n")\n```\n'
    report = val.validate_skill(_make(tmp_path, "writes-a-script", BOUNDED, body))
    assert "missing-script" not in _rules(report)
    assert report.ok


def test_boolean_optional_action_and_abbreviated_flags_are_declared(tmp_path):
    body = "```bash\npython3 scripts/tool.py --no-cache --verb\n```\n"
    skill = _make(tmp_path, "bool-flags", BOUNDED, body)
    _script(
        skill,
        "scripts/tool.py",
        "import argparse\np = argparse.ArgumentParser()\n"
        "p.add_argument('--cache', action=argparse.BooleanOptionalAction)\n"
        "p.add_argument('--verbose', action='store_true')\np.parse_args()\n",
    )
    assert "script-flag-missing" not in _rules(val.validate_skill(skill))


def test_a_quoted_value_that_looks_like_a_flag_is_not_one(tmp_path):
    body = '```bash\npython3 scripts/tool.py --title "notes --draft"\n```\n'
    skill = _make(tmp_path, "quoted-value", BOUNDED, body)
    _script(skill, "scripts/tool.py", "import argparse\np = argparse.ArgumentParser()\np.add_argument('--title')\n")
    assert "script-flag-missing" not in _rules(val.validate_skill(skill))


def test_options_declared_through_a_variable_are_not_flag_checked(tmp_path):
    body = "```bash\npython3 scripts/tool.py --input notes.md\n```\n"
    skill = _make(tmp_path, "var-flag", BOUNDED, body)
    _script(
        skill,
        "scripts/tool.py",
        "import argparse\nflag = '--input'\np = argparse.ArgumentParser()\np.add_argument(flag)\n",
    )
    assert "script-flag-missing" not in _rules(val.validate_skill(skill))
    inherited = _make(tmp_path, "parent-flag", BOUNDED, body)
    _script(
        inherited,
        "scripts/tool.py",
        "import argparse\nfrom common import base\n"
        "p = argparse.ArgumentParser(parents=[base])\np.add_argument('--out')\n",
    )
    assert "script-flag-missing" not in _rules(val.validate_skill(inherited))


def test_words_after_a_double_dash_are_not_flags(tmp_path):
    body = "```bash\npython3 scripts/tool.py -- --draft\n```\n"
    skill = _make(tmp_path, "double-dash", BOUNDED, body)
    _script(skill, "scripts/tool.py", "import argparse\np = argparse.ArgumentParser()\np.add_argument('values', nargs='*')\n")
    assert "script-flag-missing" not in _rules(val.validate_skill(skill))


def test_a_remainder_argument_is_not_flag_checked(tmp_path):
    body = "```bash\npython3 scripts/tool.py echo --draft\n```\n"
    skill = _make(tmp_path, "remainder", BOUNDED, body)
    _script(
        skill,
        "scripts/tool.py",
        "import argparse\np = argparse.ArgumentParser()\n"
        "p.add_argument('command', nargs=argparse.REMAINDER)\n",
    )
    assert "script-flag-missing" not in _rules(val.validate_skill(skill))


def test_a_quoted_script_path_still_has_its_flags_checked(tmp_path):
    body = '```bash\npython3 "${CLAUDE_SKILL_DIR}/scripts/tool.py" --bogus\n```\n'
    skill = _make(tmp_path, "quoted-path", BOUNDED, body)
    _script(skill, "scripts/tool.py", "import argparse\np = argparse.ArgumentParser()\np.add_argument('--input')\n")
    assert _errored(val.validate_skill(skill), "script-flag-missing")

