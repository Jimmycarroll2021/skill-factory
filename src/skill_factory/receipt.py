"""Receipts: every packaged version carries the evidence behind it, decomposed and immutable.

``receipt.json`` (schema ``skill-receipt/1``) and ``receipt.md`` are written into the version
directory by ``skillz package`` and listed in the manifest, so tampering with either fails
``verify_version``. A receipt says how the version was tested (``evidence``: ``verified`` by a
different judge model, ``self-judged`` by the same model, or ``unverified`` when only the lexical
heuristic ran), what the trigger and behaviour counts were, and which cases are regressions or
gains against the previous approved version. ``skillz receipt`` prints it. Nothing here spawns a
process or consults a model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from skill_factory import evalcases
from skill_factory.capture import NAME_RE

SCHEMA = "skill-receipt/1"
RECEIPT_JSON = "receipt.json"
RECEIPT_MD = "receipt.md"
VERIFIED = "verified"
SELF_JUDGED = "self-judged"
UNVERIFIED = "unverified"
CLEAN = "clean"
REGRESSED = "regressed"
NO_BASELINE = "no-baseline"
TRIGGER_METHOD = "lexical-overlap"
_ROW_KEYS = ("id", "kind", "prompt", "before", "after")
_SEMVER_RE = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", re.ASCII)


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _completed(judge_report: dict | None) -> bool:
    return judge_report is not None and judge_report.get("status") == "completed"


def _scored_cases(judge_report: dict) -> int:
    summary = judge_report.get("summary") or {}
    try:
        return int(summary.get("cases", 0)) - int(summary.get("errors", 0))
    except (TypeError, ValueError):
        return 0


def evidence_level(judge_report: dict | None) -> str:
    """``verified`` for a completed cross-model judge, ``self-judged`` for same-model, else not.

    A judge run that scored no case (none to run, or every model call failed) is no evidence of
    behaviour however it completed, so it stays ``unverified``.
    """
    if not _completed(judge_report) or _scored_cases(judge_report) <= 0:
        return UNVERIFIED
    return VERIFIED if judge_report.get("judge") != judge_report.get("runner") else SELF_JUDGED


def _trigger(eval_report: dict | None) -> dict:
    summary = (eval_report or {}).get("summary") or {}
    passed = failed = 0
    for kind in ("positive", "negative"):
        counts = summary.get(kind) or {}
        passed += int(counts.get("passed", 0))
        failed += int(counts.get("failed", 0))
    return {
        "method": TRIGGER_METHOD,
        "threshold": evalcases.MATCH_THRESHOLD,
        "checked": passed + failed,
        "passed": passed,
        "failed": failed,
    }


def _rows(cases: list[dict]) -> list[dict]:
    rows = [{key: case.get(key) for key in _ROW_KEYS} for case in cases]
    return sorted(rows, key=lambda row: str(row["id"]))


def build_receipt(
    *,
    name: str,
    version: str,
    created_at: str,
    skill_sha256: str,
    eval_report: dict | None,
    judge_report: dict | None,
    regression: dict,
) -> dict:
    """The receipt dict for a version being packaged; ``regression`` is its regression.json."""
    completed = _completed(judge_report)
    regressions = _rows(regression.get("newly_failing", []))
    if regression.get("baseline") is None:
        status = NO_BASELINE
    else:
        status = REGRESSED if regressions else CLEAN
    return {
        "schema": SCHEMA,
        "skill": name,
        "version": version,
        "created_at": created_at,
        "skill_sha256": skill_sha256,
        "evidence": evidence_level(judge_report),
        "runner": judge_report.get("runner") if completed else None,
        "judge": judge_report.get("judge") if completed else None,
        "baseline": regression.get("baseline"),
        "trigger": _trigger(eval_report),
        "behaviour": dict(judge_report["summary"]) if completed else None,
        "regressions": regressions,
        "gains": _rows(regression.get("newly_passing", [])),
        "status": status,
    }


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["None."]
    lines = ["| id | kind | prompt | before → after |", "| --- | --- | --- | --- |"]
    for row in rows:
        lines.append(
            f"| `{_cell(row['id'])}` | {_cell(row['kind'])} | {_cell(row['prompt'])} | "
            f"{_cell(row['before'])} → {_cell(row['after'])} |"
        )
    return lines


def receipt_markdown(data: dict) -> str:
    """A short human rendering of a receipt dict."""
    trigger = data["trigger"]
    trigger_line = (
        f"Trigger: {trigger['checked']} checked, {trigger['passed']} passed, "
        f"{trigger['failed']} failed ({trigger['method']}, threshold {trigger['threshold']})"
    )
    behaviour = data["behaviour"]
    if behaviour is None:
        behaviour_line = "Behaviour: no judge ran"
    else:
        behaviour_line = (
            f"Behaviour: {behaviour['cases']} cases; skill better {behaviour['skill_better']}, "
            f"skill worse {behaviour['skill_worse']}, tie {behaviour['tie']}, "
            f"errors {behaviour['errors']}"
        )
    lines = [
        f"# Receipt: {data['skill']} {data['version']} — evidence: {data['evidence']}",
        "",
        f"- Baseline: {data['baseline'] or 'none'}",
        f"- {trigger_line}",
        f"- {behaviour_line}",
        f"- Status: {data['status']}",
        "",
        "## Regressions",
        "",
        *_table(data["regressions"]),
        "",
        "## Gains",
        "",
        *_table(data["gains"]),
        "",
        f"Judge: {data['judge'] or 'none'}; runner: {data['runner'] or 'none'}.",
        "",
    ]
    return "\n".join(lines)


def write_receipt(version_dir: Path, data: dict) -> None:
    """Write receipt.json and receipt.md into a version directory being assembled."""
    _write(Path(version_dir) / RECEIPT_JSON, _dumps(data))
    _write(Path(version_dir) / RECEIPT_MD, receipt_markdown(data))


def load_receipt(version_dir: Path) -> dict | None:
    """The version's receipt.json, or None when it has none (made before receipts existed).

    A receipt written to a schema this version of skillz does not know raises ValueError
    rather than being read with guesses about what its fields mean.
    """
    data = _read_json(Path(version_dir) / RECEIPT_JSON)
    if data is not None and data.get("schema") != SCHEMA:
        raise ValueError(
            f"{Path(version_dir) / RECEIPT_JSON}: schema {data.get('schema')!r} is not {SCHEMA}"
        )
    return data


def latest_version(skill_dir: Path) -> str | None:
    """The highest version ``release.load_versions`` lists, approved or not; None without one."""
    # release imports this module, so the cycle is resolved lazily here.
    from skill_factory import release

    versions = release.load_versions(skill_dir)
    return versions[-1].version if versions else None


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "receipt",
        help="print the receipt of a packaged version",
        description=(
            "Print receipt.md (or receipt.json with --json) of a version under "
            "skills/<name>/.factory/versions/, as stored. Without a version the highest one is "
            "shown, approved or not."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "version", nargs="?", default=None, help="version X.Y.Z (default: the highest)"
    )
    parser.add_argument("--json", action="store_true", help="print receipt.json instead")
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
    skill_dir = Path(root) / "skills" / args.name
    if not (skill_dir / "SKILL.md").is_file():
        return _fail(f"skill {args.name!r} not found: {skill_dir / 'SKILL.md'} does not exist")
    version = args.version
    if version is None:
        version = latest_version(skill_dir)
        if version is None:
            return _fail(f"skill {args.name!r} has no packaged version")
    elif not _SEMVER_RE.fullmatch(version):
        return _fail(f"invalid version {version!r}: expected MAJOR.MINOR.PATCH")
    version_dir = skill_dir / ".factory" / "versions" / version
    if version_dir.is_symlink() or not version_dir.is_dir():
        return _fail(f"version {version} of {args.name!r} not found")
    path = version_dir / (RECEIPT_JSON if args.json else RECEIPT_MD)
    if path.is_symlink() or not path.is_file():
        return _fail(f"version {version} of {args.name!r} has no receipt", 1)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _fail(f"cannot read {path}: {exc}", 1)
    print(text, end="")
    return 0
