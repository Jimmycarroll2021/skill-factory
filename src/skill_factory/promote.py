"""``skillz promote``: the only way a challenged candidate replaces the stable skill.

promote is an explicit human command. It refuses unless ``challenge.json`` exists, still matches
the stable skill and the candidate byte for byte, and carries an acceptable verdict
(candidate-better or no-difference). It then replaces the canonical files and ``.factory/spec.json``
file by file (temp sibling + ``os.replace``, with backups), packages a new awaiting-approval
version and records ``promoted.json``. If packaging fails the backups are restored. The pipeline and
``skillz run`` never import this module. Nothing here spawns a process.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from skill_factory import candidates, release
from skill_factory.candidates import CandidateError
from skill_factory.capture import NAME_RE

SCHEMA = 1


class PromoteRefused(ValueError):
    """Promotion was refused or rolled back; no canonical file of the stable skill changed."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp-{secrets.token_hex(4)}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _copy_into_place(source: Path, target: Path) -> None:
    """Copy source over target through a temp sibling so target is never half-written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.promote-{secrets.token_hex(4)}")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _read_challenge(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise PromoteRefused(f"{path} does not exist: run `skillz challenge` first")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PromoteRefused(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromoteRefused(f"{path} is not a challenge report")
    return data


def _check_unlinked(skill_dir: Path, rel: str) -> None:
    """Refuse to write through a symlink anywhere between the skill directory and rel."""
    current = skill_dir
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise CandidateError(f"{current} is a symlink; refusing to write through it")


def _restore(backup_dir: Path, skill_dir: Path, done: list[tuple[str, str]]) -> list[str]:
    """Undo ``done`` (newest first); return the paths that could not be restored."""
    failed: list[str] = []
    for rel, action in reversed(done):
        target = skill_dir / rel
        try:
            if action == "created":
                target.unlink(missing_ok=True)
            else:
                _copy_into_place(backup_dir / rel, target)
        except OSError:
            failed.append(rel)
    for rel, action in reversed(done):  # drop directories that only the failed attempt created
        if action == "created":
            for parent in Path(rel).parents:
                if parent == Path("."):
                    break
                try:
                    (skill_dir / parent).rmdir()
                except OSError:
                    break
    return failed


def promote_candidate(skill_dir: Path, cid: str) -> dict:
    """Promote candidate ``cid`` over the stable skill and return the promoted.json dict.

    Raises ``CandidateError`` for a bad id, a missing candidate or a symlinked path, and
    ``PromoteRefused`` when the challenge is missing, stale or not acceptable, or when packaging
    failed and the stable files were restored.
    """
    skill_dir = Path(skill_dir)
    cdir = candidates.candidate_dir(skill_dir, cid)
    for guarded in (skill_dir / candidates.FACTORY_DIR, candidates.candidates_root(skill_dir), cdir):
        if guarded.is_symlink():
            raise CandidateError(f"{guarded} is a symlink; refusing to write through it")
    if not cdir.is_dir():
        raise CandidateError(f"candidate {cid} not found: {cdir} does not exist")
    cskill = candidates.candidate_skill_dir(skill_dir, cid)

    if (cdir / candidates.PROMOTED_JSON).exists():
        raise PromoteRefused(f"{cid} was already promoted")

    challenge = _read_challenge(cdir / candidates.CHALLENGE_JSON)
    recorded = challenge.get("sha256")
    if not isinstance(recorded, dict):
        raise PromoteRefused(f"{candidates.CHALLENGE_JSON} has no sha256 block; challenge again")
    before = candidates.stable_digests(skill_dir)
    after = candidates.candidate_digests(skill_dir, cid)
    if recorded.get("stable") != before or recorded.get("candidate") != after:
        raise PromoteRefused(
            f"the challenge of {cid} is stale: the stable skill or the candidate changed "
            f"since it ran; run `skillz challenge {skill_dir.name} {cid}` again"
        )
    verdict = challenge.get("verdict")
    if verdict not in candidates.ACCEPTABLE:
        raise PromoteRefused(
            f"verdict {verdict!r} is not one of {', '.join(candidates.ACCEPTABLE)}; "
            f"{cid} is not promoted"
        )

    # Plan every change first so a symlink or an unreadable file refuses before anything moves.
    stable_tree = candidates.tree_digests(skill_dir)
    candidate_tree = candidates.tree_digests(cskill)
    sources = {rel: cskill / rel for rel in candidate_tree}
    spec_rel = f"{candidates.FACTORY_DIR}/{candidates.SPEC_JSON}"
    sources[spec_rel] = cdir / candidates.SPEC_JSON
    removed = sorted(set(stable_tree) - set(candidate_tree))
    wanted = {rel: after[rel] for rel in candidate_tree}
    wanted[spec_rel] = after[candidates.SPEC_JSON]
    replaced = sorted(rel for rel, digest in wanted.items() if before.get(rel) != digest)
    for rel in [*replaced, *removed]:
        _check_unlinked(skill_dir, rel)

    previous = [v.version for v in release.load_versions(skill_dir)]
    backup_dir = cdir / f".promote-backup-{secrets.token_hex(4)}"
    done: list[tuple[str, str]] = []
    try:
        for rel in [*replaced, *removed]:
            target = skill_dir / rel
            if target.exists():
                (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_dir / rel)
        for rel in replaced:
            action = "replaced" if (skill_dir / rel).exists() else "created"
            done.append((rel, action))
            _copy_into_place(sources[rel], skill_dir / rel)
        for rel in removed:
            done.append((rel, "removed"))
            (skill_dir / rel).unlink()
        version = release.package_skill(skill_dir)
    except BaseException as exc:
        failed = _restore(backup_dir, skill_dir, done)
        if failed:
            raise PromoteRefused(
                f"promotion failed ({exc}) and these files could not be restored: "
                f"{', '.join(failed)}; backups are in {backup_dir}"
            ) from exc
        shutil.rmtree(backup_dir, ignore_errors=True)
        if isinstance(exc, release.ReleaseError | OSError):
            raise PromoteRefused(
                f"packaging {skill_dir.name} failed, stable files restored: {exc}"
            ) from exc
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)

    record = {
        "schema": SCHEMA,
        "skill": skill_dir.name,
        "candidate": cid,
        "promoted_at": _now(),
        "verdict": verdict,
        "version": version.version,
        "previous_versions": previous,
        "replaced": replaced,
        "removed": removed,
        "from_sha256": before,
        "to_sha256": candidates.stable_digests(skill_dir),
    }
    _write(cdir / candidates.PROMOTED_JSON, _dumps(record))
    return record


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "promote",
        help="replace skills/<name>/ with a challenged candidate and package a new version",
        description=(
            "Explicit human command. Refuses unless the candidate's challenge is current and its "
            "verdict is candidate-better or no-difference. Replaces the canonical files and "
            ".factory/spec.json, then packages a new version that waits for `skillz approve`. "
            "Earlier versions are never modified."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument("candidate_id", metavar="candidate-id", help="candidate id, e.g. c001")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )


def _fail(message: str, code: int = 2) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return code


def run(args: argparse.Namespace) -> int:
    root = args.out if args.out is not None else Path.cwd()
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    if not isinstance(args.candidate_id, str) or not candidates.CANDIDATE_ID_RE.fullmatch(
        args.candidate_id
    ):
        return _fail(f"invalid candidate id {args.candidate_id!r}: expected c001, c002, ...")
    skill_dir = Path(root) / "skills" / args.name
    if not (skill_dir / "SKILL.md").is_file():
        return _fail(f"skill {args.name!r} not found: {skill_dir / 'SKILL.md'} does not exist")
    try:
        record = promote_candidate(skill_dir, args.candidate_id)
    except CandidateError as exc:
        return _fail(str(exc))
    except (PromoteRefused, OSError) as exc:
        return _fail(str(exc), 1)
    print(f"promoted {args.name} {args.candidate_id} as {record['version']} (awaiting-approval)")
    print(f"next: skillz approve {args.name} {record['version']}")
    return 0
