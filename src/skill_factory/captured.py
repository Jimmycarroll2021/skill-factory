"""Captured failures: real prompts that a skill got wrong, kept as persistent eval cases.

``skillz capture-failure`` appends a record to ``<skill>/.factory/cases/captured.json``. Records are
never rewritten and ids are never reused. ``evaluate.evaluate_skill`` adds the stored cases to the
generated ones, so every later eval, package, regression and challenge includes them. Nothing here
spawns a process.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from skill_factory.capture import NAME_RE, SpecError
from skill_factory.evalcases import Case

FACTORY_DIR = ".factory"
CASES_DIR = "cases"
CAPTURED_FILE = "captured.json"
SCHEMA = 1
SOURCE = "captured"
KINDS = ("positive", "negative", "behaviour")
_ID_RE = re.compile(r"cap-([0-9]{3,})", re.ASCII)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def captured_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / FACTORY_DIR / CASES_DIR / CAPTURED_FILE


def _read_records(path: Path) -> list[dict]:
    """The validated records in captured.json; ``[]`` when the file does not exist."""
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or not path.is_file():
        raise SpecError(f"{path} is not a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise SpecError(f"{path} must be an object with a 'cases' list")
    seen: set[str] = set()
    for record in data["cases"]:
        if not isinstance(record, dict):
            raise SpecError(f"{path}: every case must be an object")
        case_id = record.get("id")
        if not isinstance(case_id, str) or not _ID_RE.fullmatch(case_id) or case_id in seen:
            raise SpecError(f"{path}: invalid or duplicate case id {case_id!r}")
        seen.add(case_id)
        if record.get("kind") not in KINDS:
            raise SpecError(f"{path}: {case_id} has an invalid kind {record.get('kind')!r}")
        prompt = record.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SpecError(f"{path}: {case_id} needs a non-empty prompt")
        expected = record.get("expected")
        if not isinstance(expected, list) or not all(isinstance(m, str) for m in expected):
            raise SpecError(f"{path}: {case_id} expected must be a list of strings")
    return data["cases"]


def load_captured(skill_dir: Path) -> list[Case]:
    """Captured cases in capture order; ``[]`` when none were captured."""
    return [
        Case(r["id"], r["kind"], r["prompt"], SOURCE, list(r["expected"]))
        for r in _read_records(captured_path(skill_dir))
    ]


def append_captured(
    skill_dir: Path,
    prompt: str,
    kind: str,
    expected: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Append one failure as ``cap-NNN`` and return the new record."""
    if kind not in KINDS:
        raise SpecError(f"invalid kind {kind!r}: choose from {', '.join(KINDS)}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SpecError("the prompt must not be empty")
    markers = [m.strip() for m in (expected or []) if isinstance(m, str) and m.strip()]
    if kind == "behaviour" and not markers:
        raise SpecError("a behaviour failure needs at least one expected marker")
    if kind != "behaviour":
        markers = []
    path = captured_path(skill_dir)
    factory = path.parent.parent
    for guarded in (factory, path.parent):
        if guarded.is_symlink():
            raise SpecError(f"{guarded} is a symlink; refusing to write through it")
    records = _read_records(path)
    numbers = [int(_ID_RE.fullmatch(r["id"]).group(1)) for r in records]
    record = {
        "id": f"cap-{max(numbers, default=0) + 1:03d}",
        "kind": kind,
        "prompt": prompt.strip(),
        "expected": markers,
        "note": note.strip() if isinstance(note, str) and note.strip() else None,
        "captured_at": _now(),
        "source": SOURCE,
    }
    temp = path.with_name(f".{CAPTURED_FILE}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(_dumps({"schema": SCHEMA, "cases": [*records, record]}))
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SpecError(f"cannot write {path}: {exc}") from exc
    return record


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "capture-failure",
        help="record a real failure of skills/<name>/ as a persistent eval case",
        description=(
            "Append a prompt the skill got wrong to .factory/cases/captured.json. Every later "
            "eval, package, regression check and challenge includes it."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument("--prompt", required=True, help="the prompt that went wrong")
    kind = parser.add_mutually_exclusive_group(required=True)
    kind.add_argument(
        "--should-trigger",
        action="store_true",
        help="the skill should have triggered on the prompt but did not",
    )
    kind.add_argument(
        "--should-not-trigger",
        action="store_true",
        help="the skill triggered on the prompt but should not have",
    )
    kind.add_argument(
        "--expect",
        nargs="+",
        metavar="MARKER",
        help="words a correct answer to the prompt must contain (a behaviour case)",
    )
    parser.add_argument("--note", default=None, help="why this was a failure (optional)")
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
    if not skill_dir.is_dir():
        return _fail(f"skill directory not found: {skill_dir}")
    if args.should_trigger:
        kind, expected = "positive", None
    elif args.should_not_trigger:
        kind, expected = "negative", None
    else:
        kind, expected = "behaviour", args.expect
    try:
        record = append_captured(skill_dir, args.prompt, kind, expected, args.note)
    except SpecError as exc:
        return _fail(str(exc))
    print(f"captured {record['id']} ({record['kind']}) for {args.name}")
    print(captured_path(skill_dir))
    return 0
