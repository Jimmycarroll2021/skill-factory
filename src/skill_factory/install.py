"""Install approved skill versions, record them in ``skills/registry.json`` and roll them back.

Only a version with an ``approval.json`` whose status is ``approved`` is ever installed. The
install target is exactly ``.claude/skills/<name>/`` or ``.agents/skills/<name>/`` under the
project root or the user's home directory. Nothing under ``.factory/versions/`` is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from skill_factory.capture import NAME_RE
from skill_factory.render import TARGETS

PLATFORMS = ("claude", "codex")
SCOPES = ("project", "user")
AWAITING = "awaiting-approval"
APPROVED = "approved"

_SEMVER_RE = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", re.ASCII)
_ROW_GAP = "  "
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class InstallError(ValueError):
    """Raised when a skill cannot be installed or rolled back."""


class NotApproved(InstallError):
    """The requested version is not approved, or no version is approved."""


class VersionNotFound(InstallError):
    """The requested version directory does not exist."""


class NothingToRollBack(InstallError):
    """There is no current install, or no lower approved version to return to."""


class InstallIntegrityError(InstallError):
    """The version's files do not match the checksums in manifest.json."""


class UnsafeTarget(InstallError):
    """The install target, or a directory on the way to it, is a symlink."""


@dataclass
class InstallRecord:
    name: str
    version: str
    platform: str
    scope: str
    path: str
    installed_at: str
    approved_by: str | None
    approved_at: str | None
    action: str = "install"
    from_version: str | None = None
    evidence: str | None = None  # the version's receipt evidence level; None without a receipt
    regressions: int | None = None  # how many regressions that receipt lists

    def to_dict(self) -> dict:
        return asdict(self)


def _semver(text: str) -> tuple[int, int, int]:
    if not _SEMVER_RE.fullmatch(text):
        raise InstallError(f"invalid version {text!r}: expected X.Y.Z")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{secrets.token_hex(4)}"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _versions_dir(root: Path, name: str) -> Path:
    return Path(root) / "skills" / name / ".factory" / "versions"


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise InstallError(f"invalid skill name {name!r}")


def _approval(version_dir: Path) -> dict | None:
    data = _read_json(version_dir / "approval.json")
    return data if data is not None and data.get("status") == APPROVED else None


def _evidence(version_dir: Path, strict: bool = True) -> tuple[str | None, int | None]:
    """(evidence level, regression count) from the version's receipt.json; Nones without one.

    A receipt of an unknown schema is an integrity error for the version being installed
    (``strict``); when merely listing versions it reads as no evidence, so one bad version
    cannot block installing or rolling back to another.
    """
    from skill_factory import receipt  # the receipt module imports nothing from here

    try:
        data = receipt.load_receipt(version_dir)
    except ValueError as exc:
        if strict:
            raise InstallIntegrityError(str(exc)) from exc
        return None, None
    if data is None:
        return None, None
    regressions = data.get("regressions")
    return data.get("evidence"), len(regressions) if isinstance(regressions, list) else None


def version_status(root: Path, name: str) -> list[dict]:
    """Return the versions of ``name`` in ascending semver order, read from the version dirs."""
    _check_name(name)
    versions = _versions_dir(root, name)
    found: list[tuple[tuple[int, int, int], Path]] = []
    if versions.is_dir():
        for entry in versions.iterdir():
            if (
                _SEMVER_RE.fullmatch(entry.name)
                and entry.is_dir()
                and not entry.is_symlink()
            ):
                found.append((_semver(entry.name), entry))
    rows = []
    for _, entry in sorted(found):
        manifest = _read_json(entry / "manifest.json") or {}
        approval = _approval(entry)
        rows.append(
            {
                "version": entry.name,
                "status": APPROVED if approval else AWAITING,
                "created_at": manifest.get("created_at"),
                "approved_at": approval.get("approved_at") if approval else None,
                "approved_by": approval.get("approved_by") if approval else None,
                "evidence": _evidence(entry, strict=False)[0],
            }
        )
    return rows


def _base(root: Path, scope: str, home: Path | None) -> Path:
    return Path(root) if scope == "project" else Path(home) if home else Path.home()


def target_dir(
    root: Path, name: str, platform: str, scope: str, home: Path | None = None
) -> Path:
    """Return the directory a skill installs into for ``platform`` and ``scope``."""
    _check_name(name)
    if platform not in PLATFORMS:
        raise InstallError(f"unknown platform {platform!r}")
    if scope not in SCOPES:
        raise InstallError(f"unknown scope {scope!r}")
    return _base(root, scope, home) / TARGETS[platform].format(name=name).rstrip("/")


def load_registry(root: Path) -> dict:
    """Return the registry, or an empty one when ``skills/registry.json`` is absent."""
    path = Path(root) / "skills" / "registry.json"
    empty = {"schema": 1, "installs": [], "current": {}}
    if not path.exists():
        return empty
    data = _read_json(path)
    if data is None or not isinstance(data.get("installs"), list):
        raise InstallError(f"{path} is not a valid registry")
    if not isinstance(data.get("current"), dict):
        raise InstallError(f"{path} is not a valid registry")
    return {"schema": 1, **data}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dist_files(dist: Path) -> list[str]:
    """List regular files under ``dist`` as POSIX paths; a symlink or special file is an error."""
    files = []
    for dirpath, dirnames, filenames in os.walk(dist):
        here = Path(dirpath)
        for entry in (*dirnames, *filenames):
            path = here / entry
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise InstallIntegrityError(f"{path} is a symlink or special file")
        files += [(here / entry).relative_to(dist).as_posix() for entry in filenames]
    return sorted(files)


def _verify_version(version_dir: Path) -> None:
    """Refuse a version whose files (reports and receipt included) differ from its manifest."""
    from skill_factory import release  # release does not import this module

    bad = release.verify_version(version_dir)
    if bad:
        raise InstallIntegrityError(
            f"{version_dir} does not match its manifest: {', '.join(bad)}"
        )


def _verify_dist(version_dir: Path, platform: str) -> list[str]:
    """Check ``dist/<platform>/`` against manifest.json; return the files to copy."""
    manifest = _read_json(version_dir / "manifest.json")
    if manifest is None or not isinstance(manifest.get("files"), list):
        raise InstallIntegrityError(
            f"{version_dir / 'manifest.json'} is missing or unreadable"
        )
    prefix = f"dist/{platform}/"
    expected = {
        item["path"][len(prefix) :]: item.get("sha256")
        for item in manifest["files"]
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].startswith(prefix)
    }
    dist = version_dir / "dist" / platform
    if not dist.is_dir() or dist.is_symlink() or not expected:
        raise InstallIntegrityError(f"{dist} is missing or not listed in the manifest")
    actual = _dist_files(dist)
    if actual != sorted(expected):
        raise InstallIntegrityError(
            f"{dist} does not match the files listed in the manifest"
        )
    for rel in actual:
        if _sha256(dist / rel) != expected[rel]:
            raise InstallIntegrityError(f"checksum mismatch for {prefix}{rel}")
    return actual


def _check_no_symlinks(base: Path, target: Path) -> None:
    current = base
    for part in target.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise UnsafeTarget(
                f"{current} is a symlink; refusing to install through it"
            )


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


@dataclass
class _Plan:
    record: InstallRecord
    source: Path
    files: list[str]
    target: Path


def _plan(
    root: Path,
    name: str,
    platform: str,
    scope: str,
    version: str,
    home: Path | None,
    **extra,
) -> _Plan:
    """Check that ``version`` can be installed and describe the install; write nothing."""
    version_dir = _versions_dir(root, name) / version
    if not version_dir.is_dir() or version_dir.is_symlink():
        raise VersionNotFound(f"version {version} of {name!r} not found")
    approval = _approval(version_dir)
    if approval is None:
        raise NotApproved(f"version {version} of {name!r} is not approved")
    _verify_version(version_dir)
    files = _verify_dist(version_dir, platform)
    target = target_dir(root, name, platform, scope, home)
    _check_no_symlinks(_base(root, scope, home), target)
    evidence, regressions = _evidence(version_dir)
    record = InstallRecord(
        name=name,
        version=version,
        platform=platform,
        scope=scope,
        path=str(target.absolute()),
        installed_at=_now(),
        approved_by=approval.get("approved_by"),
        approved_at=approval.get("approved_at"),
        evidence=evidence,
        regressions=regressions,
        **extra,
    )
    return _Plan(record, version_dir / "dist" / platform, files, target)


def _swap_in(plan: _Plan) -> None:
    """Copy the dist files next to the target, then swap them in; the old dir is removed last."""
    target = plan.target
    token = secrets.token_hex(4)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp-{token}"
    old = target.parent / f".{target.name}.old-{token}"
    try:
        for rel in plan.files:
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(plan.source / rel, dest)
        moved = False
        if target.exists() or target.is_symlink():
            os.rename(target, old)
            moved = True
        try:
            os.rename(tmp, target)
        except OSError:
            if moved:
                os.rename(old, target)
            raise
        if moved:
            _remove(old)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _commit(root: Path, plans: list[_Plan], registry: dict) -> list[InstallRecord]:
    for plan in plans:
        _swap_in(plan)
        record = plan.record
        registry["installs"].append(record.to_dict())
        registry["current"][f"{record.name}:{record.platform}:{record.scope}"] = (
            record.to_dict()
        )
    _write_json(Path(root) / "skills" / "registry.json", registry)
    return [plan.record for plan in plans]


def install_skill(
    root: Path,
    name: str,
    platform: str,
    scope: str,
    version: str | None = None,
    home: Path | None = None,
) -> InstallRecord:
    """Install an approved version (default: the highest) and record it in the registry."""
    _check_name(name)
    if version is None:
        approved = [
            row["version"]
            for row in version_status(root, name)
            if row["status"] == APPROVED
        ]
        if not approved:
            raise NotApproved(f"{name!r} has no approved version")
        version = approved[-1]
    else:
        _semver(version)
    registry = load_registry(root)
    plan = _plan(root, name, platform, scope, version, home)
    return _commit(root, [plan], registry)[0]


def rollback_skill(
    root: Path,
    name: str,
    platform: str | None = None,
    scope: str | None = None,
    home: Path | None = None,
) -> list[InstallRecord]:
    """Reinstall the highest approved version below the current one for every matching install."""
    _check_name(name)
    registry = load_registry(root)
    current = [
        rec
        for key, rec in sorted(registry["current"].items())
        if rec.get("name") == name
        and key == f"{name}:{rec.get('platform')}:{rec.get('scope')}"
        and platform in (None, rec.get("platform"))
        and scope in (None, rec.get("scope"))
    ]
    if not current:
        raise NothingToRollBack(f"{name!r} has no current install to roll back")
    approved = [
        row["version"]
        for row in version_status(root, name)
        if row["status"] == APPROVED
    ]
    plans = []
    for rec in current:
        installed = _semver(rec["version"])
        lower = [v for v in approved if _semver(v) < installed]
        if not lower:
            raise NothingToRollBack(
                f"{name!r} {rec['platform']}/{rec['scope']}: no approved version below "
                f"{rec['version']}"
            )
        plans.append(
            _plan(
                root,
                name,
                rec["platform"],
                rec["scope"],
                lower[-1],
                home,
                action="rollback",
                from_version=rec["version"],
            )
        )
    return _commit(root, plans, registry)


def list_skills(root: Path) -> dict:
    """Return the current installs and the version history of every versioned skill."""
    registry = load_registry(root)
    skills: dict[str, list[dict]] = {}
    skills_dir = Path(root) / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if (
                NAME_RE.fullmatch(entry.name)
                and (entry / ".factory" / "versions").is_dir()
            ):
                skills[entry.name] = version_status(root, entry.name)
    installed = [rec for _, rec in sorted(registry["current"].items())]
    return {"installed": installed, "skills": skills}


def _printable(value: object) -> str:
    """Text for a table cell; control characters (including ESC) become ``\\xNN`` escapes."""
    if value is None:
        return "-"
    return _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", str(value))


def _table(headers: tuple[str, ...], rows: list[tuple]) -> list[str]:
    cells = [[_printable(c) for c in row] for row in rows]
    widths = [max([len(h), *(len(r[i]) for r in cells)]) for i, h in enumerate(headers)]
    lines = [
        _ROW_GAP.join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()
    ]
    lines += [
        _ROW_GAP.join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
        for r in cells
    ]
    return lines


def format_list(data: dict) -> str:
    """Render ``list_skills`` output as an installed table and a versions table."""
    installed = [
        (
            r["name"],
            r["version"],
            r["platform"],
            r["scope"],
            r.get("evidence"),  # registries written before receipts have no such key
            r["approved_by"],
            r["path"],
        )
        for r in data["installed"]
    ]
    versions = [
        (name, row["version"], row["status"], row["created_at"], row.get("evidence"))
        for name, rows in sorted(data["skills"].items())
        for row in rows
    ]
    lines = ["Installed", ""]
    lines += _table(
        ("NAME", "VERSION", "PLATFORM", "SCOPE", "EVIDENCE", "APPROVED_BY", "PATH"), installed
    )
    lines += ["", "Versions", ""]
    lines += _table(("NAME", "VERSION", "STATUS", "CREATED_AT", "EVIDENCE"), versions)
    return "\n".join(lines) + "\n"


def _fail(message: str, code: int = 2) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return code


def _root(args: argparse.Namespace) -> Path:
    return Path(args.out) if args.out is not None else Path.cwd()


def _add_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="root directory containing skills/ (default: cwd)",
    )


def add_install_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "install",
        help="install an approved skill version into a Claude or Codex skills directory",
        description="Install an approved version of skills/<name>/ and record it in the registry.",
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--platform", required=True, choices=PLATFORMS, help="target platform"
    )
    parser.add_argument(
        "--scope", required=True, choices=SCOPES, help="project or user install"
    )
    parser.add_argument(
        "--version", default=None, help="version X.Y.Z (default: highest approved)"
    )
    _add_out(parser)


def add_rollback_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "rollback",
        help="reinstall the previous approved version of an installed skill",
        description="Reinstall the highest approved version below the one currently installed.",
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--platform", choices=PLATFORMS, default=None, help="limit to a platform"
    )
    parser.add_argument(
        "--scope", choices=SCOPES, default=None, help="limit to a scope"
    )
    _add_out(parser)


def add_list_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "list",
        help="show installed skills and the version history of every skill",
        description="Show the install registry and each skill's versions with their status.",
    )
    _add_out(parser)
    parser.add_argument("--json", action="store_true", help="print the listing as JSON")


def _installed_line(record: InstallRecord) -> str:
    return f"{record.action} {record.name} {record.version} [{record.platform}/{record.scope}] -> {record.path}"


def run_install(args: argparse.Namespace) -> int:
    root = _root(args)
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    if not (root / "skills" / args.name).is_dir():
        return _fail(
            f"skill {args.name!r} not found: {root / 'skills' / args.name} does not exist"
        )
    try:
        record = install_skill(root, args.name, args.platform, args.scope, args.version)
    except (NotApproved, InstallIntegrityError) as exc:
        return _fail(str(exc), 1)
    except (InstallError, OSError) as exc:
        return _fail(str(exc))
    print(_installed_line(record))
    return 0


def run_rollback(args: argparse.Namespace) -> int:
    root = _root(args)
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    try:
        records = rollback_skill(root, args.name, args.platform, args.scope)
    except (NothingToRollBack, NotApproved, InstallIntegrityError) as exc:
        return _fail(str(exc), 1)
    except (InstallError, OSError) as exc:
        return _fail(str(exc))
    for record in records:
        print(_installed_line(record))
    return 0


def run_list(args: argparse.Namespace) -> int:
    try:
        data = list_skills(_root(args))
    except (InstallError, OSError) as exc:
        return _fail(str(exc))
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(format_list(data), end="")
    return 0
