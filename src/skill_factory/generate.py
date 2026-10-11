"""Generate a canonical skill directory (SKILL.md, .factory/spec.json, resources) from a spec."""

from __future__ import annotations

import json
from pathlib import Path

from skill_factory import yamlish
from skill_factory.capture import RESOURCE_DIRS, SkillSpec, validate_spec

_MAX_BULLETS = 20

_RESOURCE_NOTES = {
    "scripts": "Executable helpers the skill can run. Add scripts here.",
    "references": "Longer reference material loaded on demand. Add documents here.",
    "assets": "Static files used in outputs (templates, images, data). Add assets here.",
}


class GenerateError(ValueError):
    """Raised when a skill directory cannot be generated."""


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _bullets(items: list[str]) -> list[str]:
    return [f"- {' '.join(item.split())}" for item in items[:_MAX_BULLETS] if item.strip()]


def _frontmatter(spec: SkillSpec) -> str:
    meta: dict = {"name": spec.name, "description": spec.description}
    if spec.license:
        meta["license"] = spec.license
    if spec.allowed_tools:
        meta["allowed-tools"] = " ".join(spec.allowed_tools)
    if spec.metadata:
        meta["metadata"] = dict(spec.metadata)
    return yamlish.frontmatter_dumps(meta)


def render_skill_md(spec: SkillSpec) -> str:
    """The SKILL.md text for a validated spec: frontmatter plus a short body."""
    lines = [f"# {spec.name}", "", " ".join(spec.description.split())]
    sections = (
        ("When to use", spec.triggers),
        ("When not to use", spec.non_triggers),
        ("What to do", spec.behaviours),
    )
    for title, items in sections:
        bullets = _bullets(items)
        if bullets:
            lines += ["", f"## {title}", "", *bullets]
    resources = [r for r in RESOURCE_DIRS if r in spec.resources]
    if resources:
        pointer = ", ".join(f"`{r}/`" for r in resources)
        lines += ["", f"See {pointer} in this directory for supporting files."]
    return _frontmatter(spec) + "\n" + "\n".join(lines) + "\n"


def generate(spec: SkillSpec, root: Path, force: bool = False) -> Path:
    """Create ``<root>/skills/<name>/`` for the spec and return that directory."""
    validate_spec(spec)
    skill_dir = Path(root) / "skills" / spec.name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists() and not force:
        raise GenerateError(f"{skill_md} already exists (use --force to overwrite)")
    try:
        (skill_dir / ".factory").mkdir(parents=True, exist_ok=True)
        _write(skill_md, render_skill_md(spec))
        _write(
            skill_dir / ".factory" / "spec.json",
            json.dumps(spec.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        for resource in RESOURCE_DIRS:
            if resource not in spec.resources:
                continue
            (skill_dir / resource).mkdir(exist_ok=True)
            readme = skill_dir / resource / "README.md"
            if not readme.exists():
                _write(readme, f"# {resource}\n\n{_RESOURCE_NOTES[resource]}\n")
    except OSError as exc:
        raise GenerateError(f"cannot write {skill_dir}: {exc}") from exc
    return skill_dir
