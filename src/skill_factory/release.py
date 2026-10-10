"""Versioned release: ``skillz package`` snapshots a skill, ``skillz approve`` is the only gate.

A version lives in ``<skill>/.factory/versions/<semver>/`` and is never modified once renamed into
place, except that ``approve`` adds ``approval.json``. ``manifest.json`` lists a SHA-256 for every
other file and is itself immutable, so the effective status of a version is read from
``approval.json`` and never from the manifest. Nothing here spawns a process.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from skill_factory import evalcases, evaluate, render, validate
from skill_factory.capture import NAME_RE, SpecError

FACTORY_DIR = ".factory"
VERSIONS_DIR = "versions"
MANIFEST = "manifest.json"
APPROVAL = "approval.json"
INDEX = "index.json"
AWAITING = "awaiting-approval"
APPROVED = "approved"
_SEMVER_RE = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", re.ASCII)
_REPORT_FILES = (
    "validate.json",
    "validate.md",
    "render.json",
    "render.md",
    "eval.json",
    "eval.md",
    "cases.json",
)


class ReleaseError(ValueError):
    """Base class for release failures."""


class VersionExists(ReleaseError):
    """The requested version directory already exists; nothing was touched."""


class VersionNotFound(ReleaseError):
    """The requested version directory does not exist."""


class IntegrityError(ReleaseError):
    """A version directory does not match its manifest."""


class ValidationFailed(ReleaseError):
    """The skill has error-level validation findings."""


class RegressionFailed(ReleaseError):
    """Previously passing cases now fail; ``report`` is the regression.json dict."""

    def __init__(self, message: str, report: dict) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class Version:
    name: str
    version: str
    path: Path
    status: str
    created_at: str
    approved_at: str | None = None
    approved_by: str | None = None
    regression: dict | None = None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read_json(path: Path) -> dict | None:
    """The JSON object in path, or None when it is missing, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_semver(text: str) -> tuple[int, int, int]:
    if not isinstance(text, str) or not _SEMVER_RE.fullmatch(text):
        raise ReleaseError(f"invalid version {text!r}: expected MAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def next_version(existing: list[str], bump: str = "patch") -> str:
    if bump not in ("patch", "minor", "major"):
        raise ReleaseError(f"invalid bump {bump!r}: expected patch, minor or major")
    if not existing:
        return "0.1.0"
    major, minor, patch = max(parse_semver(v) for v in existing)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _versions_root(skill_dir: Path) -> Path:
    return Path(skill_dir) / FACTORY_DIR / VERSIONS_DIR


def _approval(version_dir: Path) -> dict | None:
    data = _read_json(version_dir / APPROVAL)
    return data if data is not None and data.get("status") == APPROVED else None


def load_versions(skill_dir: Path) -> list[Version]:
    """Every semver directory under ``versions/``, ascending; temp and other entries are ignored."""
    skill_dir = Path(skill_dir)
    root = _versions_root(skill_dir)
    if root.is_symlink() or not root.is_dir():
        return []
    found: list[tuple[tuple[int, int, int], Path]] = []
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not _SEMVER_RE.fullmatch(entry.name):
            continue
        found.append((parse_semver(entry.name), entry))
    versions = []
    for _, entry in sorted(found):
        manifest = _read_json(entry / MANIFEST) or {}
        approval = _approval(entry)
        versions.append(
            Version(
                name=str(manifest.get("name", skill_dir.name)),
                version=entry.name,
                path=entry,
                status=APPROVED if approval else AWAITING,
                created_at=str(manifest.get("created_at", "")),
                approved_at=approval.get("approved_at") if approval else None,
                approved_by=approval.get("approved_by") if approval else None,
                regression=_read_json(entry / "regression.json"),
            )
        )
    return versions


def approved_versions(skill_dir: Path) -> list[Version]:
    return [v for v in load_versions(skill_dir) if v.status == APPROVED]


def verify_version(version_dir: Path) -> list[str]:
    """Paths that are missing, extra or whose sha256 differs from the manifest; [] means intact."""
    version_dir = Path(version_dir)
    manifest = _read_json(version_dir / MANIFEST)
    files = manifest.get("files") if manifest else None
    if not isinstance(files, list):
        return [MANIFEST]
    expected: dict[str, object] = {}
    for item in files:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            expected[item["path"]] = item.get("sha256")
    actual: set[str] = set()
    bad: set[str] = set()
    for current, dirnames, filenames in os.walk(version_dir, followlinks=False):
        here = Path(current)
        for entry in dirnames:  # a symlinked directory is listed here and never descended into
            if (here / entry).is_symlink():
                bad.add((here / entry).relative_to(version_dir).as_posix())
        for entry in filenames:
            path = here / entry
            rel = path.relative_to(version_dir).as_posix()
            if rel in (MANIFEST, APPROVAL):
                continue
            actual.add(rel)
            if path.is_symlink():
                bad.add(rel)
    for rel, digest in expected.items():
        parts = rel.split("/")
        unsafe = rel.startswith("/") or ".." in parts or "\\" in rel
        if unsafe or rel not in actual or (rel not in bad and _sha256(version_dir / rel) != digest):
            bad.add(rel)
    bad.update(actual - expected.keys())
    return sorted(bad)


def write_index(skill_dir: Path) -> Path:
    """Rebuild ``versions/index.json`` from the version directories (informational only)."""
    skill_dir = Path(skill_dir)
    root = _versions_root(skill_dir)
    root.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "version": v.version,
            "status": v.status,
            "created_at": v.created_at,
            "approved_at": v.approved_at,
            "approved_by": v.approved_by,
        }
        for v in load_versions(skill_dir)
    ]
    temp = root / f".index-{secrets.token_hex(4)}.tmp"
    try:
        _write(temp, _dumps({"name": skill_dir.name, "versions": entries}))
        os.replace(temp, root / INDEX)
    finally:
        temp.unlink(missing_ok=True)
    return root / INDEX


def _regression_markdown(report: dict) -> str:
    baseline = report["baseline"] or "none"
    lines = [
        f"# Regression: {report['candidate']}",
        "",
        f"Baseline (previous approved version): {baseline}",
        f"Status: **{report['status']}** ({report['checked']} trigger cases compared)",
        "",
    ]
    if report["status"] == "no-baseline":
        lines.append("No previous approved version, so there is nothing to compare against.")
    elif not report["newly_failing"]:
        lines.append("No newly failing cases.")
    for case in report["newly_failing"]:
        lines.append(
            f"- `{case['id']}` ({case['kind']}, score {case['score']}): "
            + " ".join(case["prompt"].split())
        )
    return "\n".join(lines) + "\n"


def _baseline_cases(baseline: Version) -> tuple[list[evalcases.Case], set[str]]:
    """The baseline's stored cases and the ids of its trigger cases that passed."""
    cases_data = _read_json(baseline.path / "reports" / "cases.json")
    eval_data = _read_json(baseline.path / "reports" / "eval.json")
    if cases_data is None or eval_data is None:
        raise IntegrityError(f"version {baseline.version} has no readable reports/cases.json")
    try:
        cases = [
            evalcases.Case(
                id=item["id"],
                kind=item["kind"],
                prompt=item["prompt"],
                source=item.get("source", ""),
                expected=list(item.get("expected", [])),
            )
            for item in cases_data["cases"]
        ]
        passed = {r["id"] for r in eval_data["results"] if r.get("status") == "pass"}
    except (KeyError, TypeError, AttributeError) as exc:
        raise IntegrityError(f"version {baseline.version} has malformed reports: {exc}") from exc
    return cases, passed


def regression_check(skill_dir: Path, candidate: str) -> dict:
    """Re-grade the previous approved version's stored cases against the current SKILL.md."""
    skill_dir = Path(skill_dir)
    limit = parse_semver(candidate)
    earlier = [v for v in approved_versions(skill_dir) if parse_semver(v.version) < limit]
    report: dict = {
        "candidate": candidate,
        "baseline": earlier[-1].version if earlier else None,
        "status": "no-baseline",
        "checked": 0,
        "newly_failing": [],
    }
    if not earlier:
        return report
    try:
        meta = render._frontmatter(skill_dir / "SKILL.md")
    except render.RenderError as exc:
        raise ReleaseError(str(exc)) from exc
    cases, passed_before = _baseline_cases(earlier[-1])
    trigger = [c for c in cases if c.kind != "behaviour" and c.id in passed_before]
    grades = evalcases.grade_offline(
        trigger, str(meta.get("name", "")), str(meta.get("description", ""))
    )
    by_id = {c.id: c for c in trigger}
    report["checked"] = len(trigger)
    for grade in grades:
        if grade.status == "fail":
            case = by_id[grade.id]
            report["newly_failing"].append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "prompt": case.prompt,
                    "before": "pass",
                    "after": "fail",
                    "score": grade.score,
                }
            )
    report["status"] = "regressed" if report["newly_failing"] else "pass"
    return report


def _snapshot(skill_dir: Path, tmp: Path, regression: dict, name: str, version: str) -> None:
    """Fill tmp with skill/, dist/, reports/ and regression files, then write the manifest last."""
    factory = skill_dir / FACTORY_DIR
    files, _ = render._canonical_files(skill_dir)
    for rel in files:
        target = tmp / "skill" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_dir / rel, target, follow_symlinks=False)
    for platform in render.TARGETS:
        shutil.copytree(factory / "dist" / platform, tmp / "dist" / platform, symlinks=True)
    (tmp / "reports").mkdir()
    for filename in _REPORT_FILES:
        shutil.copy2(factory / filename, tmp / "reports" / filename)
    _write(tmp / "regression.json", _dumps(regression))
    _write(tmp / "regression.md", _regression_markdown(regression))
    entries = [
        {"path": path.relative_to(tmp).as_posix(), "sha256": _sha256(path)}
        for path in tmp.rglob("*")
        if path.is_file()
    ]
    manifest = {
        "schema": 1,
        "name": name,
        "version": version,
        "created_at": _now(),
        "status": AWAITING,
        "previous_approved": regression["baseline"],
        "files": sorted(entries, key=lambda item: item["path"]),
    }
    _write(tmp / MANIFEST, _dumps(manifest))


def package_skill(
    skill_dir: Path,
    bump: str = "patch",
    *,
    set_version: str | None = None,
    allow_regression: bool = False,
) -> Version:
    """Validate, render and evaluate the skill, then snapshot it as a new immutable version."""
    skill_dir = Path(skill_dir)
    if not (skill_dir / "SKILL.md").is_file():
        raise ReleaseError(f"{skill_dir / 'SKILL.md'} does not exist")
    factory = skill_dir / FACTORY_DIR
    root = _versions_root(skill_dir)
    for guarded in (factory, root):
        if guarded.is_symlink():
            raise ReleaseError(f"{guarded} is a symlink; refusing to write through it")
    existing = [v.version for v in load_versions(skill_dir)]
    if set_version is not None:
        parse_semver(set_version)
        version = set_version
    else:
        version = next_version(existing, bump)
    if (root / version).exists() or (root / version).is_symlink():
        raise VersionExists(f"version {version} already exists; versions are immutable")

    # The three stages write their usual reports into .factory/ so the snapshot is consistent.
    report = validate.validate_skill(skill_dir)
    report.write(factory)
    if not report.ok:
        problems = "; ".join(
            f"{f.rule} {f.path}" for f in report.findings if f.severity == "error"
        )
        raise ValidationFailed(f"validation failed with {report.errors} error(s): {problems}")
    try:
        render.render_skill(skill_dir).write(factory)
        evaluate.evaluate_skill(skill_dir)
    except (render.RenderError, SpecError) as exc:
        raise ReleaseError(str(exc)) from exc

    regression = regression_check(skill_dir, version)
    _write(factory / "regression.json", _dumps(regression))
    _write(factory / "regression.md", _regression_markdown(regression))
    if regression["status"] == "regressed" and not allow_regression:
        ids = ", ".join(case["id"] for case in regression["newly_failing"])
        raise RegressionFailed(
            f"{len(regression['newly_failing'])} case(s) that passed in "
            f"{regression['baseline']} now fail: {ids} (use --allow-regression to override)",
            regression,
        )

    name = skill_dir.name
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".tmp-{version}-{secrets.token_hex(4)}"
    tmp.mkdir()
    try:
        _snapshot(skill_dir, tmp, regression, name, version)
        final = root / version
        if final.exists() or final.is_symlink():
            raise VersionExists(f"version {version} already exists; versions are immutable")
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    write_index(skill_dir)
    return next(v for v in load_versions(skill_dir) if v.version == version)


def approve(skill_dir: Path, version: str, user: str | None = None) -> Version:
    """Record approval of a version after verifying its checksums; idempotent."""
    skill_dir = Path(skill_dir)
    parse_semver(version)
    version_dir = _versions_root(skill_dir) / version
    if version_dir.is_symlink() or not version_dir.is_dir():
        raise VersionNotFound(f"version {version} of {skill_dir.name} not found")
    mismatched = verify_version(version_dir)
    if mismatched:
        raise IntegrityError(
            f"version {version} does not match its manifest: " + ", ".join(mismatched)
        )
    if _approval(version_dir) is None:
        if user is None:
            try:
                user = getpass.getuser()
            except Exception:  # noqa: BLE001  any getuser failure means unknown
                user = "unknown"
        record = {
            "name": skill_dir.name,
            "version": version,
            "status": APPROVED,
            "approved_at": _now(),
            "approved_by": user or "unknown",
        }
        temp = version_dir / f".approval-{secrets.token_hex(4)}.tmp"
        try:
            _write(temp, _dumps(record))
            os.replace(temp, version_dir / APPROVAL)
        finally:
            temp.unlink(missing_ok=True)
    write_index(skill_dir)
    return next(v for v in load_versions(skill_dir) if v.version == version)


def _fail(message: str, code: int = 2) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return code


def _skill_dir(args: argparse.Namespace) -> Path | int:
    if not NAME_RE.fullmatch(args.name):
        return _fail(f"invalid skill name {args.name!r}")
    root = args.out if args.out is not None else Path.cwd()
    skill_dir = Path(root) / "skills" / args.name
    if not (skill_dir / "SKILL.md").is_file():
        return _fail(f"skill {args.name!r} not found: {skill_dir / 'SKILL.md'} does not exist")
    return skill_dir


def add_package_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "package",
        help="snapshot skills/<name>/ as a new immutable, checksummed version",
        description=(
            "Validate, render and evaluate the skill, compare it with the last approved version "
            "and write skills/<name>/.factory/versions/<semver>/. Existing versions are never "
            "modified; a new version waits for `skillz approve`."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    bump = parser.add_mutually_exclusive_group()
    bump.add_argument("--minor", action="store_true", help="bump the minor version")
    bump.add_argument("--major", action="store_true", help="bump the major version")
    parser.add_argument("--set-version", default=None, metavar="X.Y.Z", help="use this version")
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        help="create the version even when previously passing cases now fail",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )


def run_package(args: argparse.Namespace) -> int:
    skill_dir = _skill_dir(args)
    if isinstance(skill_dir, int):
        return skill_dir
    bump = "major" if args.major else "minor" if args.minor else "patch"
    try:
        result = package_skill(
            skill_dir,
            bump,
            set_version=args.set_version,
            allow_regression=args.allow_regression,
        )
    except (ValidationFailed, RegressionFailed, IntegrityError) as exc:
        return _fail(str(exc), 1)
    except (ReleaseError, OSError) as exc:  # includes VersionExists and invalid versions
        return _fail(str(exc))
    regression = result.regression or {}
    print(f"packaged {args.name} {result.version} ({result.status})")
    print(result.path)
    print(f"regression: {regression.get('status')}")
    return 0


def add_approve_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "approve",
        help="approve a packaged version so it can be installed",
        description=(
            "Verify the version's checksums and record approval (UTC time and local user) in "
            "approval.json. Approving an approved version changes nothing."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument("version", help="version to approve, MAJOR.MINOR.PATCH")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )


def run_approve(args: argparse.Namespace) -> int:
    skill_dir = _skill_dir(args)
    if isinstance(skill_dir, int):
        return skill_dir
    try:
        result = approve(skill_dir, args.version)
    except IntegrityError as exc:
        return _fail(str(exc), 1)
    except (ReleaseError, OSError) as exc:  # includes VersionNotFound and invalid versions
        return _fail(str(exc))
    print(f"approved {args.name} {result.version} by {result.approved_by} at {result.approved_at}")
    return 0
