"""Shared layout of challenger candidates under ``<skill>/.factory/candidates/<id>/``.

candidate, challenge and promote all locate candidates, check ids and fingerprint trees through
these helpers. Ids are ``c001``, ``c002``, ... and the id check also blocks path traversal.
Nothing here writes a file or spawns a process.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path

from skill_factory import render
from skill_factory.capture import SkillSpec, SpecError

FACTORY_DIR = ".factory"
CANDIDATES_DIR = "candidates"
CANDIDATE_JSON = "candidate.json"
CHALLENGE_JSON = "challenge.json"
CHALLENGE_MD = "challenge.md"
PROMOTED_JSON = "promoted.json"
SPEC_JSON = "spec.json"
CANDIDATE_ID_RE = re.compile(r"c[0-9]{3,9}", re.ASCII)

# Worst first: the verdict of several results is the earliest one present.
VERDICTS = ("candidate-unsafe", "candidate-worse", "candidate-better", "no-difference")
ACCEPTABLE = ("candidate-better", "no-difference")


class CandidateError(ValueError):
    """A candidate id is invalid or a candidate input is missing or unreadable."""


def worst_verdict(verdicts: Iterable[str]) -> str:
    """The verdict with the highest precedence; ``no-difference`` when there are none."""
    found = list(verdicts)
    for verdict in found:
        if verdict not in VERDICTS:
            raise CandidateError(f"unknown verdict {verdict!r}")
    return next((v for v in VERDICTS if v in found), "no-difference")


def candidates_root(skill_dir: Path) -> Path:
    return Path(skill_dir) / FACTORY_DIR / CANDIDATES_DIR


def candidate_dir(skill_dir: Path, cid: str) -> Path:
    if not isinstance(cid, str) or not CANDIDATE_ID_RE.fullmatch(cid):
        raise CandidateError(f"invalid candidate id {cid!r}: expected c001, c002, ...")
    return candidates_root(skill_dir) / cid


def candidate_skill_dir(skill_dir: Path, cid: str) -> Path:
    """The candidate's canonical copy, in a directory named like the skill."""
    return candidate_dir(skill_dir, cid) / Path(skill_dir).name


def next_candidate_id(skill_dir: Path) -> str:
    root = candidates_root(skill_dir)
    numbers = [0]
    if root.is_dir():
        numbers += [int(p.name[1:]) for p in root.iterdir() if CANDIDATE_ID_RE.fullmatch(p.name)]
    return f"c{max(numbers) + 1:03d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CandidateError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


def tree_digests(root: Path) -> dict[str, str]:
    """sha256 of SKILL.md and every regular file under scripts/, references/ and assets/."""
    root = Path(root)
    files, _skipped = render._canonical_files(root)
    return {rel: _sha256(root / rel) for rel in sorted(files)}


def stable_digests(skill_dir: Path) -> dict[str, str]:
    """The canonical tree plus ``.factory/spec.json`` when the stable skill has one."""
    digests = tree_digests(skill_dir)
    spec = Path(skill_dir) / FACTORY_DIR / SPEC_JSON
    if spec.is_file() and not spec.is_symlink():
        digests[f"{FACTORY_DIR}/{SPEC_JSON}"] = _sha256(spec)
    return digests


def candidate_digests(skill_dir: Path, cid: str) -> dict[str, str]:
    """The candidate's canonical tree plus its ``spec.json``."""
    digests = tree_digests(candidate_skill_dir(skill_dir, cid))
    spec = candidate_dir(skill_dir, cid) / SPEC_JSON
    if not spec.is_file() or spec.is_symlink():
        raise CandidateError(f"candidate {cid} has no {SPEC_JSON}")
    digests[SPEC_JSON] = _sha256(spec)
    return digests


def load_candidate_spec(skill_dir: Path, cid: str) -> SkillSpec:
    path = candidate_dir(skill_dir, cid) / SPEC_JSON
    if not path.is_file() or path.is_symlink():
        raise CandidateError(f"candidate {cid} has no {SPEC_JSON}")
    try:
        return SkillSpec.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, ValueError, SpecError) as exc:
        raise CandidateError(f"cannot load {path}: {exc}") from exc
