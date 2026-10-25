"""Evaluate a skill offline: assemble cases, grade them heuristically, optionally run a baseline.

Cases come from the spec, the skill's ``evals/evals.json`` and captured failures
(``evalcases.all_cases``), so a skill written by hand is graded on the evals it ships. Writes
``cases.json``, ``eval.json`` and ``eval.md`` into ``<skill>/.factory/``. The trigger grading is a
lexical-overlap heuristic; the baseline needs an explicit ``--runner`` whose executable is on
PATH, and is recorded as skipped otherwise. Eval is advisory.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path

from skill_factory import evalcases, render, runner
from skill_factory.capture import NAME_RE, SkillSpec, SpecError
from skill_factory.runner import EXECUTABLES, Runner

FACTORY_DIR = ".factory"

# Looked up at call time, so a test can substitute a fake factory by patching this name.
RUNNER_FACTORY: Callable[[str], Runner | None] = runner.detect_runner


def _read_text(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise SpecError(f"{label} not found: {path}")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc


def load_spec(skill_dir: Path) -> SkillSpec:
    """Load ``.factory/spec.json`` for the cases; the name and description graded are always the
    ones SKILL.md carries, so an edited skill is never judged against a stale spec. Without a
    spec.json the frontmatter alone is the spec."""
    skill_dir = Path(skill_dir)
    spec_path = skill_dir / FACTORY_DIR / "spec.json"
    skill_md = skill_dir / "SKILL.md"
    if spec_path.exists() or spec_path.is_symlink():
        try:
            data = json.loads(_read_text(spec_path, "spec"))
        except json.JSONDecodeError as exc:
            raise SpecError(f"invalid JSON in {spec_path}: {exc}") from exc
        return _from_skill_md(SkillSpec.from_dict(data), skill_md)
    _read_text(skill_md, "SKILL.md")
    try:
        data = render._frontmatter(skill_md)
    except render.RenderError as exc:
        raise SpecError(str(exc)) from exc
    name, description = data.get("name"), data.get("description")
    if not isinstance(name, str) or not name:
        raise SpecError("SKILL.md frontmatter needs a non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise SpecError("SKILL.md frontmatter needs a non-empty description")
    return SkillSpec(name=name, description=description, source_kind="yaml")


def _from_skill_md(spec: SkillSpec, skill_md: Path) -> SkillSpec:
    """``spec`` with the name and description the SKILL.md frontmatter actually carries."""
    if not skill_md.is_file() or skill_md.is_symlink():
        return spec
    try:
        meta = render._frontmatter(skill_md)
    except render.RenderError:
        return spec  # validation reports the unreadable frontmatter as an error
    name, description = meta.get("name"), meta.get("description")
    return dataclasses.replace(
        spec,
        name=name if isinstance(name, str) and name else spec.name,
        description=description
        if isinstance(description, str) and description.strip()
        else spec.description,
    )


def _summary(grades: list[evalcases.Grade]) -> dict:
    def count(kind: str, status: str) -> int:
        return sum(1 for g in grades if g.kind == kind and g.status == status)

    return {
        "positive": {"passed": count("positive", "pass"), "failed": count("positive", "fail")},
        "negative": {"passed": count("negative", "pass"), "failed": count("negative", "fail")},
        "behaviour": {"pending": count("behaviour", "pending-runner")},
    }


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _to_markdown(report: dict, prompts: dict[str, str]) -> str:
    summary = report["summary"]
    lines = [
        f"# Eval report: {report['skill']}",
        "",
        f"Method: {report['method']}.",
        f"Threshold: {report['threshold']}",
        "",
        "## Summary",
        "",
        "| Kind | Passed | Failed | Pending |",
        "| --- | --- | --- | --- |",
        f"| positive | {summary['positive']['passed']} | {summary['positive']['failed']} | 0 |",
        f"| negative | {summary['negative']['passed']} | {summary['negative']['failed']} | 0 |",
        f"| behaviour | 0 | 0 | {summary['behaviour']['pending']} |",
        "",
        "## Failing cases",
        "",
    ]
    failing = [r for r in report["results"] if r["status"] == "fail"]
    for result in failing:
        expect = "match" if result["kind"] == "positive" else "no match"
        lines.append(
            f"- `{result['id']}` ({result['kind']}, expected {expect}, score {result['score']}): "
            f"{_cell(prompts[result['id']])}"
        )
    if not failing:
        lines.append("None.")
    lines += ["", "## Weak areas", ""]
    for weak in report["weak_areas"]:
        lines += [f"### {weak['area']}", "", weak["problem"], "", f"Proposal: {weak['proposal']}"]
        if weak["cases"]:
            lines += ["", "Cases: " + ", ".join(f"`{c}`" for c in weak["cases"])]
        lines.append("")
    if not report["weak_areas"]:
        lines += ["None.", ""]
    baseline = report["baseline"]
    lines += ["## Baseline", "", f"Status: {baseline['status']}", ""]
    if baseline["status"] == "completed":
        counts = baseline["summary"]
        lines += [
            f"Runner: {baseline['runner']}",
            "",
            f"- skill helps: {counts['skill_helps']}",
            f"- no difference: {counts['no_difference']}",
            f"- skill hurts: {counts['skill_hurts']}",
            f"- errors: {counts['errors']}",
            "",
        ]
        lines += [f"- `{case['id']}`: {case['verdict']}" for case in baseline["cases"]]
        lines.append("")
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def evaluate_skill(
    skill_dir: Path,
    runner_name: str | None = None,
    runner_factory: Callable[[str], Runner | None] | None = None,
) -> dict:
    """Write the eval files under ``<skill>/.factory/`` and return the eval.json dict."""
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise SpecError(f"skill directory not found: {skill_dir}")
    skill_text = _read_text(skill_dir / "SKILL.md", "SKILL.md")
    spec = load_spec(skill_dir)
    factory = skill_dir / FACTORY_DIR
    if factory.is_symlink():
        raise SpecError(f"{factory} is a symlink; refusing to write through it")

    cases = evalcases.all_cases(skill_dir, spec)
    grades = evalcases.grade_offline(cases, spec.name, spec.description)
    weak_areas = evalcases.propose_improvements(spec, cases, grades)

    baseline = runner.skipped_baseline(runner_name)
    if runner_name is not None:
        found = (runner_factory or RUNNER_FACTORY)(runner_name)
        if found is not None:
            behaviours = [c.to_dict() for c in cases if c.kind == "behaviour"]
            baseline = runner.run_baseline(found, behaviours, skill_text)

    report = {
        "skill": spec.name,
        "method": evalcases.METHOD,
        "threshold": evalcases.MATCH_THRESHOLD,
        "summary": _summary(grades),
        "results": [g.to_dict() for g in grades],
        "weak_areas": [w.to_dict() for w in weak_areas],
        "baseline": baseline,
    }
    try:
        factory.mkdir(parents=True, exist_ok=True)
        _write(factory / "cases.json", evalcases.cases_json(cases))
        _write(
            factory / "eval.json",
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        _write(factory / "eval.md", _to_markdown(report, {c.id: c.prompt for c in cases}))
    except OSError as exc:
        raise SpecError(f"cannot write {factory}: {exc}") from exc
    return report


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "eval",
        help="evaluate skills/<name>/ offline and write a report",
        description=(
            "Generate test cases, grade them with an offline lexical-overlap heuristic and "
            "propose improvements. A baseline against a real model runs only with --runner."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--runner",
        choices=sorted(EXECUTABLES),
        default=None,
        help="opt in to a with/without-skill baseline using this CLI, if it is on PATH",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 when any trigger case fails (default: advisory)"
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
        report = evaluate_skill(skill_dir, args.runner)
    except (SpecError, ValueError) as exc:
        return _fail(str(exc))
    summary = report["summary"]
    failed = summary["positive"]["failed"] + summary["negative"]["failed"]
    passed = summary["positive"]["passed"] + summary["negative"]["passed"]
    print(
        f"eval {report['skill']}: {passed} passed, {failed} failed (heuristic); "
        f"baseline: {report['baseline']['status']}"
    )
    factory = skill_dir / FACTORY_DIR
    for filename in ("cases.json", "eval.json", "eval.md"):
        print(factory / filename)
    return 1 if args.strict and failed else 0
