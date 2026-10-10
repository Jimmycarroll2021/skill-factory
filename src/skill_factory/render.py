"""Render a canonical skill into Claude and Codex install variants under ``.factory/dist/``."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from skill_factory import yamlish
from skill_factory.capture import NAME_RE, RESOURCE_DIRS

# platform -> install target, relative to the project (or user) root
TARGETS = {"claude": ".claude/skills/{name}/", "codex": ".agents/skills/{name}/"}
OPENAI_YAML = "agents/openai.yaml"
_SHORT_DESCRIPTION = 120


class RenderError(ValueError):
    """Raised when a skill cannot be rendered."""


@dataclass
class Report:
    skill: str
    skill_dir: Path
    variants: dict[str, dict] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "variants": self.variants,
            "skipped_symlinks": list(self.skipped),
        }

    def to_markdown(self) -> str:
        lines = [f"# Render report: {self.skill}", ""]
        for platform, variant in self.variants.items():
            lines += [
                f"## {platform}",
                "",
                f"- dist: `{variant['dist']}`",
                f"- install target: `{variant['target']}`",
                f"- files: {len(variant['files'])}",
                "",
            ]
            lines += [f"- `{item['path']}` {item['sha256'][:12]}" for item in variant["files"]]
            lines.append("")
        if self.skipped:
            lines += ["## Skipped (symlinks and special files, not copied)", ""]
            lines += [f"- `{path}`" for path in self.skipped]
            lines.append("")
        return "\n".join(lines)

    def write(self, factory_dir: Path) -> tuple[Path, Path]:
        """Write render.json and render.md into ``factory_dir``; return both paths."""
        factory_dir = Path(factory_dir)
        factory_dir.mkdir(parents=True, exist_ok=True)
        json_path = factory_dir / "render.json"
        md_path = factory_dir / "render.md"
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        with md_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.to_markdown())
        return json_path, md_path


def _frontmatter(skill_md: Path) -> dict:
    try:
        text = skill_md.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RenderError(f"cannot read {skill_md}: {exc}") from exc
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0].rstrip() != "---":
        raise RenderError(f"{skill_md}: SKILL.md has no frontmatter")
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            break
    else:
        raise RenderError(f"{skill_md}: frontmatter is not closed with '---'")
    try:
        data = yamlish.loads("\n".join(lines[1:end]) + "\n")
    except ValueError as exc:
        raise RenderError(f"{skill_md}: invalid frontmatter: {exc}") from exc
    return data


def _read_identity(skill_dir: Path) -> tuple[str, str]:
    """Return the frontmatter (name, description) after checking them against the directory."""
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise RenderError(f"{skill_md} does not exist (or is not a regular file)")
    data = _frontmatter(skill_md)
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not name:
        raise RenderError("SKILL.md frontmatter needs a non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise RenderError("SKILL.md frontmatter needs a non-empty description")
    if name != skill_dir.name:
        raise RenderError(f"name {name!r} does not match the directory name {skill_dir.name!r}")
    return name, description


def _collect(directory: Path, prefix: str, files: list[str], skipped: list[str]) -> None:
    """List regular files under directory; symlinks and special files are skipped, not followed."""
    for entry in sorted(directory.iterdir()):
        rel = f"{prefix}/{entry.name}"
        if entry.is_symlink():
            skipped.append(rel)
        elif entry.is_dir():
            _collect(entry, rel, files, skipped)
        elif entry.is_file():
            files.append(rel)
        else:
            skipped.append(rel)


def _canonical_files(skill_dir: Path) -> tuple[list[str], list[str]]:
    files = ["SKILL.md"]
    skipped: list[str] = []
    for resource in RESOURCE_DIRS:
        path = skill_dir / resource
        if path.is_symlink():
            skipped.append(resource)
        elif path.is_dir():
            _collect(path, resource, files, skipped)
    return files, skipped


def _openai_yaml(name: str, description: str) -> str:
    short = " ".join(description.split())
    if len(short) > _SHORT_DESCRIPTION:
        short = short[: _SHORT_DESCRIPTION - 3].rstrip() + "..."
    return yamlish.dumps(
        {
            "name": name,
            "description": description,
            "interface": {
                "display_name": name.replace("-", " ").title(),
                "short_description": short,
            },
            "metadata": {"skill_file": "SKILL.md"},
        }
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clear(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _build_variant(skill_dir: Path, dist: Path, files: list[str], extra: dict[str, str]) -> list:
    _clear(dist)
    dist.mkdir(parents=True)
    for rel in files:
        target = dist / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_dir / rel, target, follow_symlinks=False)
    for rel, text in extra.items():
        target = dist / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    written = sorted([*files, *extra])
    return [{"path": rel, "sha256": _sha256(dist / rel)} for rel in written]


def render_skill(skill_dir: Path) -> Report:
    """Write the Claude and Codex variants under ``<skill>/.factory/dist/`` and report them."""
    skill_dir = Path(skill_dir).resolve()
    if not skill_dir.is_dir():
        raise RenderError(f"{skill_dir} is not a directory")
    name, description = _read_identity(skill_dir)
    files, skipped = _canonical_files(skill_dir)
    factory = skill_dir / ".factory"
    dist_root = factory / "dist"
    for guarded in (factory, dist_root):
        if guarded.is_symlink():
            raise RenderError(f"{guarded} is a symlink; refusing to write through it")
    extras = {"claude": {}, "codex": {OPENAI_YAML: _openai_yaml(name, description)}}
    report = Report(skill=name, skill_dir=skill_dir, skipped=skipped)
    try:
        for platform, target in TARGETS.items():
            listing = _build_variant(skill_dir, dist_root / platform, files, extras[platform])
            report.variants[platform] = {
                "dist": f".factory/dist/{platform}",
                "target": target.format(name=name),
                "files": listing,
            }
    except OSError as exc:
        raise RenderError(f"cannot write {dist_root}: {exc}") from exc
    return report


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "render",
        help="render Claude and Codex variants of skills/<name>/ into .factory/dist/",
        description="Render the Claude and Codex install layouts without touching canonical files.",
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )


def _fail(message: str) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return 2


def run(args: argparse.Namespace) -> int:
    root = args.out if args.out is not None else Path.cwd()
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    skill_dir = Path(root) / "skills" / args.name
    if not (skill_dir / "SKILL.md").is_file():
        return _fail(f"skill {args.name!r} not found: {skill_dir / 'SKILL.md'} does not exist")
    try:
        report = render_skill(skill_dir)
        json_path, md_path = report.write(skill_dir / ".factory")
    except (RenderError, OSError) as exc:
        return _fail(str(exc))
    counts = ", ".join(f"{p}: {len(v['files'])} files" for p, v in report.variants.items())
    print(f"rendered {report.skill} ({counts})")
    for platform, variant in report.variants.items():
        print(skill_dir / variant["dist"], f"[{platform}]")
    print(json_path)
    print(md_path)
    return 0
