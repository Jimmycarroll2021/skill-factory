"""``skillz challenge``: compare a candidate with the stable skill and give a verdict.

Both sides are validated and graded against the same union of cases (the stable skill's
``evals/evals.json``, captured failures, the stable skill's cases and the candidate's cases). The
result is written to ``challenge.json`` and ``challenge.md`` inside the candidate directory only;
the stable skill is never written. Grading is the offline lexical-overlap heuristic. With a
runner (``--runner``) the behaviour cases are also scored by the model judge on both sides and
folded into the verdict; without one no model is consulted and no process is spawned.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from skill_factory import (
    candidates,
    captured,
    evalcases,
    evaluate,
    judge,
    render,
    validate,
)
from skill_factory.candidates import CandidateError
from skill_factory.capture import NAME_RE, SkillSpec, SpecError
from skill_factory.runner import EXECUTABLES, Runner

SCHEMA = 1


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _identity(skill_md: Path, spec: SkillSpec) -> SkillSpec:
    """The spec with the name and description the SKILL.md frontmatter actually carries."""
    try:
        meta = render._frontmatter(skill_md)
    except render.RenderError:
        return spec  # validation reports the unreadable frontmatter as an error
    name, description = meta.get("name"), meta.get("description")
    return dataclasses.replace(
        spec,
        name=name if isinstance(name, str) and name else spec.name,
        description=description
        if isinstance(description, str) and description
        else spec.description,
    )


def _key(case: evalcases.Case) -> tuple:
    return evalcases.case_key(case)  # kind, prompt and criteria: a second rubric is a second case


def _union(
    evals: list[evalcases.Case],
    extra: list[evalcases.Case],
    stable: list[evalcases.Case],
    candidate: list[evalcases.Case],
) -> list[tuple[evalcases.Case, list[str]]]:
    """Cases in order evals, captured, stable, candidate, deduplicated; each with its origins."""
    merged: dict[tuple, tuple[evalcases.Case, list[str]]] = {}
    groups = (
        ("evals", "", evals),
        ("captured", "", extra),
        ("stable", "stable/", stable),
        ("candidate", "candidate/", candidate),
    )
    for origin, prefix, cases in groups:
        for case in cases:
            key = _key(case)
            if key in merged:
                if origin not in merged[key][1]:
                    merged[key][1].append(origin)
                continue
            merged[key] = (dataclasses.replace(case, id=f"{prefix}{case.id}"), [origin])
    return list(merged.values())


def _areas(
    spec: SkillSpec, cases: list[evalcases.Case], grades: list[evalcases.Grade]
) -> list[str]:
    return list(
        dict.fromkeys(
            p.area for p in evalcases.propose_improvements(spec, cases, grades)
        )
    )


def _total(entry: dict) -> int | None:
    """The with-skill total of one judged case, or None when that case errored."""
    if entry.get("verdict") == "error":
        return None
    return entry["with_skill"]["total"]


def _behaviour_change(before: int | None, after: int | None) -> str:
    if before is None or after is None:
        return "error"
    if after < before:
        return "regression"
    if after > before:
        return "improvement"
    return "unchanged"


def _behaviour(
    cases: list[evalcases.Case],
    stable_md: Path,
    candidate_md: Path,
    runner_name: str | None,
    judge_name: str | None,
    runner_factory: Callable[[str], Runner | None] | None,
) -> dict:
    """Judge the union behaviour cases with both skill texts and the same judge."""
    found, judge_runner, skip = judge.resolve_runners(runner_name, judge_name, runner_factory)
    if skip is not None:
        return {
            "status": skip,
            "requested": {"runner": runner_name, "judge": judge_name},
        }
    try:
        stable_text = evaluate._read_text(stable_md, "SKILL.md")
        candidate_text = evaluate._read_text(candidate_md, "SKILL.md")
    except SpecError as exc:
        raise CandidateError(str(exc)) from exc
    behaviours = [c.to_dict() for c in cases if c.kind == "behaviour"]
    stable = judge.judge_cases(found, judge_runner, behaviours, stable_text)
    candidate = judge.judge_cases(found, judge_runner, behaviours, candidate_text)
    rows, regressions, improvements = [], [], []
    counts = {"regression": 0, "improvement": 0, "unchanged": 0, "error": 0}
    for before, after in zip(stable["cases"], candidate["cases"], strict=True):
        change = _behaviour_change(_total(before), _total(after))
        counts[change] += 1
        if change == "regression":
            regressions.append(before["id"])
        elif change == "improvement":
            improvements.append(before["id"])
        rows.append(
            {
                "id": before["id"],
                "prompt": before["prompt"],
                "stable_total": _total(before),
                "candidate_total": _total(after),
                "change": change,
            }
        )
    return {
        "status": "completed",
        "requested": {"runner": runner_name, "judge": judge_name},
        "runner": found.name,
        "judge": judge_runner.name,
        "cross_model": found.name != judge_runner.name,
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "regressions": counts["regression"],
            "improvements": counts["improvement"],
            "unchanged": counts["unchanged"],
            "errors": counts["error"],
        },
        "regressions": regressions,
        "improvements": improvements,
    }


def _behaviour_markdown(behaviour: dict) -> list[str]:
    lines = ["", "## Behaviour", ""]
    if behaviour["status"] != "completed":
        note = (
            f"Not judged: {behaviour['status']}. Pass `--runner claude|codex` to score the "
            "behaviour cases with a judge model."
        )
        return [*lines, note]
    counts = behaviour["summary"]
    mode = "cross-model" if behaviour["cross_model"] else "same-model"
    summary = (
        f"Runner {behaviour['runner']}, judge {behaviour['judge']} ({mode}). "
        f"{counts['cases']} cases: {counts['regressions']} regressions, "
        f"{counts['improvements']} improvements, {counts['unchanged']} unchanged, "
        f"{counts['errors']} errors."
    )
    lines += [
        summary,
        "",
        "| id | prompt | stable | candidate | change |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in behaviour["cases"]:
        stable = "error" if case["stable_total"] is None else case["stable_total"]
        candidate = "error" if case["candidate_total"] is None else case["candidate_total"]
        lines.append(
            f"| `{case['id']}` | {_cell(case['prompt'])} | {stable} | {candidate} "
            f"| {case['change']} |"
        )
    return lines


def _load_report(directory: Path) -> validate.Report:
    try:
        return validate.validate_skill(directory)
    except (FileNotFoundError, OSError) as exc:
        raise CandidateError(f"cannot validate {directory}: {exc}") from exc


def _markdown(report: dict) -> str:
    stable, candidate = (
        report["validation"]["stable"],
        report["validation"]["candidate"],
    )
    lines = [
        f"# Challenge: {report['skill']} {report['candidate']}",
        "",
        f"Verdict: **{report['verdict']}**",
        "",
        "## Validation",
        "",
        (
            f"- stable: {'ok' if stable['ok'] else 'FAILED'} "
            f"({stable['errors']} errors, {stable['warnings']} warnings)"
        ),
        (
            f"- candidate: {'ok' if candidate['ok'] else 'FAILED'} "
            f"({candidate['errors']} errors, {candidate['warnings']} warnings)"
        ),
    ]
    new_errors = report["validation"]["new_errors"]
    if new_errors:
        lines += ["", "New errors in the candidate:", ""]
        for finding in new_errors:
            where = (
                finding["path"]
                if finding["line"] is None
                else f"{finding['path']}:{finding['line']}"
            )
            lines.append(f"- `{finding['rule']}` {where}: {_cell(finding['message'])}")
    else:
        lines += ["- new candidate errors: none"]
    summary = report["summary"]
    lines += [
        "",
        "## Cases",
        "",
        (
            f"{summary['cases']} cases: {summary['regressions']} regressions, "
            f"{summary['improvements']} improvements, {summary['unchanged']} unchanged."
        ),
        "",
        "| id | kind | prompt | stable | candidate | change |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        lines.append(
            f"| `{case['id']}` | {case['kind']} | {_cell(case['prompt'])} | {case['stable']} "
            f"| {case['candidate']} | {case['change']} |"
        )
    lines += _behaviour_markdown(report["behaviour"])
    weak = report["weak_areas"]
    lines += ["", "## Weak areas", ""]
    for label in ("stable", "candidate", "resolved", "introduced"):
        lines.append(f"- {label}: {', '.join(weak[label]) if weak[label] else 'none'}")
    behaviour = report["behaviour"]
    if behaviour["status"] == "completed":
        consulted = (
            f"behaviour cases were scored by the {behaviour['judge']} judge on the "
            f"{behaviour['runner']} runner."
        )
    else:
        consulted = "no model was consulted."
    lines += [
        "",
        "## Notes",
        "",
        (
            f"Trigger grading is the offline lexical-overlap heuristic "
            f"(threshold {report['threshold']}); {consulted}"
        ),
        (
            "Promotion is a separate human command: "
            f"`skillz promote {report['skill']} {report['candidate']}`."
        ),
        "",
    ]
    return "\n".join(lines)


def challenge_candidate(
    skill_dir: Path,
    cid: str,
    runner_name: str | None = None,
    judge_name: str | None = None,
    runner_factory: Callable[[str], Runner | None] | None = None,
) -> dict:
    """Challenge candidate ``cid``, write its reports and return the challenge.json dict.

    With ``runner_name`` the behaviour cases are also judged on both sides by a model.
    Raises CandidateError for a bad id or a missing or unreadable input.
    """
    skill_dir = Path(skill_dir)
    cdir = candidates.candidate_dir(skill_dir, cid)
    cskill = candidates.candidate_skill_dir(skill_dir, cid)
    if not skill_dir.is_dir():
        raise CandidateError(f"skill directory not found: {skill_dir}")
    if not cdir.is_dir():
        raise CandidateError(f"candidate {cid} not found: {cdir}")
    for guarded in (
        skill_dir / candidates.FACTORY_DIR,
        candidates.candidates_root(skill_dir),
        cdir,
    ):
        if guarded.is_symlink():
            raise CandidateError(
                f"{guarded} is a symlink; refusing to write through it"
            )
    if not (cskill / "SKILL.md").is_file():
        raise CandidateError(f"candidate {cid} has no SKILL.md: {cskill}")

    try:
        stable_spec = evaluate.load_spec(skill_dir)
        evals = evalcases.load_evals(skill_dir)
        extra = captured.load_captured(skill_dir)
    except SpecError as exc:
        raise CandidateError(str(exc)) from exc
    candidate_spec = candidates.load_candidate_spec(skill_dir, cid)
    stable_id = _identity(skill_dir / "SKILL.md", stable_spec)
    candidate_id = _identity(cskill / "SKILL.md", candidate_spec)

    stable_report = _load_report(skill_dir)
    candidate_report = _load_report(cskill)
    stable_dict, candidate_dict = stable_report.to_dict(), candidate_report.to_dict()
    known = {
        (f["rule"], f["path"])
        for f in stable_dict["findings"]
        if f["severity"] == "error"
    }
    new_errors = [
        f
        for f in candidate_dict["findings"]
        if f["severity"] == "error" and (f["rule"], f["path"]) not in known
    ]

    union = _union(
        evals,
        extra,
        evalcases.generate_cases(stable_spec),
        evalcases.generate_cases(candidate_spec),
    )
    cases = [case for case, _ in union]
    stable_grades = evalcases.grade_offline(
        cases, stable_id.name, stable_id.description
    )
    candidate_grades = evalcases.grade_offline(
        cases, candidate_id.name, candidate_id.description
    )

    rows, regressions, improvements = [], [], []
    for (case, origins), before, after in zip(
        union, stable_grades, candidate_grades, strict=True
    ):
        if before.status == "pass" and after.status == "fail":
            change = "regression"
            regressions.append(case.id)
        elif before.status == "fail" and after.status == "pass":
            change = "improvement"
            improvements.append(case.id)
        else:
            change = "unchanged"
        rows.append(
            {
                "id": case.id,
                "kind": case.kind,
                "prompt": case.prompt,
                "expected": list(case.expected),
                "origins": origins,
                "stable": before.status,
                "candidate": after.status,
                "stable_score": before.score,
                "candidate_score": after.score,
                "change": change,
            }
        )

    behaviour = _behaviour(
        cases,
        skill_dir / "SKILL.md",
        cskill / "SKILL.md",
        runner_name,
        judge_name,
        runner_factory,
    )
    behaviour_regressions = behaviour.get("regressions", [])
    behaviour_improvements = behaviour.get("improvements", [])

    if not candidate_report.ok:
        verdict = "candidate-unsafe"
    elif regressions or behaviour_regressions:
        verdict = "candidate-worse"
    elif improvements or behaviour_improvements:
        verdict = "candidate-better"
    else:
        verdict = "no-difference"

    stable_areas = _areas(stable_id, cases, stable_grades)
    candidate_areas = _areas(candidate_id, cases, candidate_grades)
    report = {
        "schema": SCHEMA,
        "skill": skill_dir.name,
        "candidate": cid,
        "created_at": _now(),
        "method": evalcases.METHOD,
        "threshold": evalcases.MATCH_THRESHOLD,
        "verdict": verdict,
        "validation": {
            "stable": stable_dict,
            "candidate": candidate_dict,
            "new_errors": new_errors,
        },
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "regressions": len(regressions),
            "improvements": len(improvements),
            "unchanged": len(rows) - len(regressions) - len(improvements),
        },
        "regressions": regressions,
        "improvements": improvements,
        "behaviour": behaviour,
        "weak_areas": {
            "stable": stable_areas,
            "candidate": candidate_areas,
            "resolved": [a for a in stable_areas if a not in candidate_areas],
            "introduced": [a for a in candidate_areas if a not in stable_areas],
        },
        "sha256": {
            "stable": candidates.stable_digests(skill_dir),
            "candidate": candidates.candidate_digests(skill_dir, cid),
        },
    }
    try:
        _write(cdir / candidates.CHALLENGE_JSON, _dumps(report))
        _write(cdir / candidates.CHALLENGE_MD, _markdown(report))
    except OSError as exc:
        raise CandidateError(f"cannot write {cdir}: {exc}") from exc
    return report


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "challenge",
        help="compare a candidate of skills/<name>/ with the stable skill and give a verdict",
        description=(
            "Validate the stable skill and a candidate, grade the union of their eval cases "
            "against both and write challenge.json and challenge.md into the candidate "
            "directory. Verdicts: candidate-unsafe, candidate-worse, candidate-better, "
            "no-difference."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument("candidate", help="candidate id, for example c001")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="root directory containing skills/ (default: cwd)",
    )
    parser.add_argument(
        "--runner",
        choices=sorted(EXECUTABLES),
        default=None,
        help="also score the behaviour cases on both sides with this CLI, if it is on PATH",
    )
    parser.add_argument(
        "--judge",
        choices=sorted(EXECUTABLES),
        default=None,
        help="CLI that judges (default: the other model if both are on PATH, else the runner)",
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
    try:
        report = challenge_candidate(
            skill_dir, args.candidate, getattr(args, "runner", None), getattr(args, "judge", None)
        )
    except (CandidateError, SpecError) as exc:
        return _fail(str(exc))
    print(f"challenge {args.name} {args.candidate}: {report['verdict']}")
    if getattr(args, "runner", None) is not None:
        behaviour = report["behaviour"]
        counts = behaviour.get("summary")
        detail = (
            f" ({counts['regressions']} regressions, {counts['improvements']} improvements)"
            if counts
            else ""
        )
        print(f"behaviour: {behaviour['status']}{detail}")
    cdir = candidates.candidate_dir(skill_dir, args.candidate)
    print(cdir / candidates.CHALLENGE_JSON)
    print(cdir / candidates.CHALLENGE_MD)
    return 0
