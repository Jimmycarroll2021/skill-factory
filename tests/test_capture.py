"""Tests for paragraph/YAML/JSON capture and the yamlish subset."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from skill_factory import capture as cap
from skill_factory import yamlish

FIXTURES = Path(__file__).parent / "fixtures"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _paragraph() -> str:
    return (FIXTURES / "paragraph.txt").read_text(encoding="utf-8")


def _good_spec(**overrides) -> cap.SkillSpec:
    base = {"name": "good-name", "description": "Does a useful thing."}
    base.update(overrides)
    return cap.SkillSpec(**base)


def test_paragraph_yields_valid_name_and_description():
    spec = cap.capture_paragraph(_paragraph())
    assert NAME_RE.match(spec.name)
    assert len(spec.name) <= 64
    assert spec.name == "draft-release-notes-software"
    assert 0 < len(spec.description) <= 1024
    assert spec.description.startswith("Draft release notes")
    assert spec.resources == []
    assert spec.source_kind == "paragraph"


def test_paragraph_phrase_extraction():
    spec = cap.capture_paragraph(_paragraph())
    assert any("changelog entry" in t for t in spec.triggers)
    assert any("list breaking changes first" in b for b in spec.behaviours)
    assert any("internal design documents" in n for n in spec.non_triggers)


def test_capture_accepts_path_and_text():
    from_path = cap.capture(str(FIXTURES / "paragraph.txt"))
    from_text = cap.capture(_paragraph())
    assert from_path == from_text


def test_name_override():
    spec = cap.capture_paragraph(_paragraph(), name="my-notes")
    assert spec.name == "my-notes"
    assert cap.capture(str(FIXTURES / "spec.yaml"), name="other-name").name == "other-name"
    with pytest.raises(cap.SpecError):
        cap.capture_paragraph(_paragraph(), name="Not Kebab")


def test_long_paragraph_is_truncated_and_name_is_bounded():
    long_word = "x" * 40
    text = f"{long_word} {long_word} {long_word} " + "word " * 400 + "."
    spec = cap.capture_paragraph(text)
    assert len(spec.name) <= 64
    assert NAME_RE.match(spec.name)
    assert 0 < len(spec.description) <= 1024


def test_yaml_fixture():
    spec = cap.capture(str(FIXTURES / "spec.yaml"))
    assert spec.name == "api-doc-reviewer"
    assert spec.resources == ["references"]
    assert spec.source_kind == "yaml"
    assert spec.allowed_tools == ["Read", "Grep"]
    assert spec.metadata == {"author": "skill-factory", "version": "1.0"}
    assert spec.license == "MIT"
    assert len(spec.triggers) == 2


def test_json_fixture():
    spec = cap.capture_spec_file(FIXTURES / "spec.json")
    assert spec.name == "csv-data-cleaner"
    assert spec.resources == ["scripts", "assets"]
    assert spec.source_kind == "json"
    assert spec.behaviours


def test_fixtures_have_distinct_names():
    names = {
        cap.capture(str(FIXTURES / "paragraph.txt")).name,
        cap.capture(str(FIXTURES / "spec.yaml")).name,
        cap.capture(str(FIXTURES / "spec.json")).name,
    }
    assert len(names) == 3


def test_spec_file_without_name_derives_one(tmp_path):
    path = tmp_path / "s.yml"
    path.write_text("description: Summarise meeting transcripts into action items.\n", "utf-8")
    spec = cap.capture(str(path))
    assert spec.name == "summarise-meeting-transcripts-action"


def test_bad_spec_files_raise_spec_error(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(cap.SpecError):
        cap.capture_spec_file(bad_json)
    typo = tmp_path / "typo.yaml"
    typo.write_text("description: Fine.\nresource: [scripts]\n", encoding="utf-8")
    with pytest.raises(cap.SpecError, match="unknown spec field"):
        cap.capture_spec_file(typo)


def test_determinism():
    for source in (_paragraph(), str(FIXTURES / "spec.yaml"), str(FIXTURES / "spec.json")):
        assert cap.capture(source) == cap.capture(source)
        assert cap.capture(source).to_dict() == cap.capture(source).to_dict()


def test_to_dict_from_dict_round_trip():
    spec = cap.capture(str(FIXTURES / "spec.json"))
    assert cap.SkillSpec.from_dict(spec.to_dict()) == dataclasses.replace(spec)


def test_yamlish_round_trips_a_spec_dict():
    for source in (_paragraph(), str(FIXTURES / "spec.yaml"), str(FIXTURES / "spec.json")):
        data = cap.capture(source).to_dict()
        assert yamlish.loads(yamlish.dumps(data)) == data


def test_yamlish_round_trips_awkward_values():
    data = {
        "a": "yes",
        "b": "123",
        "c": "key: value # not a comment",
        "d": "",
        "e": "it's \"quoted\"\nsecond line",
        "f": [True, False, None, 7, "- dash", "[x]", "é ünïcode"],
        "g": {"k": "1.5", "z": None, "n": 3},
        "h": [],
        "i": {},
        "quoted key": "v",
    }
    assert yamlish.loads(yamlish.dumps(data)) == data


def test_yamlish_loads_flow_block_and_comments():
    text = (
        "---\n"
        "# comment\n"
        "name: demo  # trailing\n"
        "tags: [a, 'b, c', \"d\"]\n"
        "items:\n"
        "- one\n"
        "- 'two'\n"
        "meta:\n"
        "  author: me\n"
        "  count: 2\n"
        "flag: true\n"
        "nothing:\n"
        "ratio: 1.5\n"
    )
    assert yamlish.loads(text) == {
        "name": "demo",
        "tags": ["a", "b, c", "d"],
        "items": ["one", "two"],
        "meta": {"author": "me", "count": 2},
        "flag": True,
        "nothing": None,
        "ratio": "1.5",
    }


@pytest.mark.parametrize(
    "text",
    [
        "a: |\n  block\n",
        "a: &anchor x\n",
        "a:\n  b:\n    c: 1\n",
        "a:\n  - k: v\n",
        "a: {x: 1}\n",
        "a: [b, [c]]\n",
        "a: 1\na: 2\n",
        "a:\n\tb: 1\n",
        "just a line\n",
    ],
)
def test_yamlish_rejects_unsupported_syntax(text):
    with pytest.raises(ValueError):
        yamlish.loads(text)


def test_frontmatter_dumps():
    block = yamlish.frontmatter_dumps({"name": "x", "description": "Does: things"})
    assert block == '---\nname: x\ndescription: "Does: things"\n---\n'
    assert yamlish.loads(block) == {"name": "x", "description": "Does: things"}


def test_validate_rejects_bad_name():
    for bad in ("Bad Name", "UPPER", "-lead", "trail-", "dou--ble", "", "a" * 65):
        with pytest.raises(cap.SpecError):
            cap.validate_spec(_good_spec(name=bad))
    cap.validate_spec(_good_spec(name="a" * 64))


def test_validate_rejects_bad_description():
    with pytest.raises(cap.SpecError):
        cap.validate_spec(_good_spec(description="   "))
    with pytest.raises(cap.SpecError):
        cap.validate_spec(_good_spec(description="x" * 1025))
    cap.validate_spec(_good_spec(description="x" * 1024))


def test_validate_rejects_unknown_resource_and_bad_metadata():
    with pytest.raises(cap.SpecError, match="resource"):
        cap.validate_spec(_good_spec(resources=["docs"]))
    with pytest.raises(cap.SpecError, match="metadata"):
        cap.validate_spec(_good_spec(metadata={"version": 1}))
    cap.validate_spec(_good_spec(resources=["scripts", "references", "assets"]))
