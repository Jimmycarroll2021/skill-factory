"""``skillz candidate``: start a challenger candidate as an exact copy of the stable skill.

The candidate lives in ``<skill>/.factory/candidates/<id>/``. Its canonical files are the stable
skill's, byte for byte, and its ``spec.json`` is the stable spec. A human (or an agent on their
behalf) edits the copy; ``skillz challenge`` then compares it with the stable skill and refuses
regressions, and ``skillz promote`` is the only way it replaces the stable skill. The stable skill
is only read. Nothing here rewrites a description, spawns a process or consults a model.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from skill_factory import candidates, evaluate, release, render
from skill_factory.candidates import CandidateError
from skill_factory.capture import NAME_RE, SpecError

SCHEMA = 1
SOURCE = "manual"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def create_candidate(skill_dir: Path) -> dict:
    """Create the next candidate as a copy of the stable skill; return its candidate.json dict.

    Raises SpecError for a missing or unreadable skill and CandidateError when a write would go
    through a symlink or fails; a failed candidate directory is removed again.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise SpecError(f"skill directory not found: {skill_dir}")
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise SpecError(f"SKILL.md not found: {skill_md}")
    factory = skill_dir / candidates.FACTORY_DIR
    root = candidates.candidates_root(skill_dir)
    for guarded in (factory, root):
        if guarded.is_symlink():
            raise CandidateError(f"{guarded} is a symlink; refusing to write through it")

    spec = evaluate.load_spec(skill_dir)
    files, _skipped = render._canonical_files(skill_dir)
    versions = release.load_versions(skill_dir)
    cid = candidates.next_candidate_id(skill_dir)
    target = candidates.candidate_dir(skill_dir, cid)
    dest = candidates.candidate_skill_dir(skill_dir, cid)
    stable_spec = factory / candidates.SPEC_JSON
    record = {
        "schema": SCHEMA,
        "id": cid,
        "skill": spec.name,
        "created_at": _now(),
        "base_version": versions[-1].version if versions else None,
        "base_sha256": candidates.stable_digests(skill_dir),
        "source": SOURCE,
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        dest.mkdir()
        for rel in files:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_dir / rel, dest / rel)
        if stable_spec.is_file() and not stable_spec.is_symlink():
            shutil.copy2(stable_spec, target / candidates.SPEC_JSON)
        else:
            _write(target / candidates.SPEC_JSON, _dumps(spec.to_dict()))
        _write(target / candidates.CANDIDATE_JSON, _dumps(record))
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise CandidateError(f"cannot write {target}: {exc}") from exc
    return record


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "candidate",
        help="copy skills/<name>/ into a challenger candidate for you to edit",
        description=(
            "Copy the stable skill and its spec into .factory/candidates/<id>/ and print the "
            "candidate SKILL.md to edit. The stable skill is never changed; run `skillz "
            "challenge` to compare the edited candidate with it."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="root directory containing skills/ (default: cwd)",
    )


def _fail(message: str, code: int = 2) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return code


def run(args: argparse.Namespace) -> int:
    root = args.out if args.out is not None else Path(".")
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    skill_dir = Path(root) / "skills" / args.name
    if not skill_dir.is_dir():
        return _fail(f"skill directory not found: {skill_dir}")
    try:
        record = create_candidate(skill_dir)
    except CandidateError as exc:
        return _fail(str(exc), 1)
    except (SpecError, release.ReleaseError) as exc:
        return _fail(str(exc))
    cid = record["id"]
    print(candidates.candidate_skill_dir(skill_dir, cid) / "SKILL.md")
    print(f"edit it, then: skillz challenge {args.name} {cid}")
    return 0
