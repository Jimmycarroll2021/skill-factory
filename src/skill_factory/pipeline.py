"""One-command pipeline: ``skillz run`` goes from a paragraph or spec to a packaged version.

The stages run in order (capture, generate, validate, render, evaluate, judge, regression,
package) and the run then stops at ``awaiting-approval``. It never approves and never installs.
The result is written to ``<skill>/.factory/pipeline.json`` and ``pipeline.md``. Nothing here
spawns a process; a model runner is only used when ``--runner`` is given and it is found on PATH,
by the evaluate baseline and by the judge stage (whose report package reuses).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from skill_factory import judge, release, render, validate
from skill_factory.capture import capture
from skill_factory.evaluate import evaluate_skill
from skill_factory.generate import GenerateError, generate
from skill_factory.runner import EXECUTABLES, Runner

FACTORY_DIR = ".factory"
SCHEMA = 1
AWAITING = "awaiting-approval"
STOPPED = "stopped"
STAGES = (
    "capture",
    "generate",
    "validate",
    "render",
    "evaluate",
    "judge",
    "regression",
    "package",
    "approval",
)
_HEURISTIC_NOTE = (
    "Offline grading is a heuristic (lexical overlap between test prompts and the description), "
    "not proof that a model triggers the skill. Review the reports before approving."
)


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _entry(stage: str, status: str, verdict: str, report: str | None) -> dict:
    return {"stage": stage, "status": status, "verdict": verdict, "report": report}


def _markdown(result: dict) -> str:
    if result["status"] == AWAITING:
        headline = "awaiting approval"
    else:
        headline = f"stopped at {result['stopped_at']}"
    lines = [f"# Pipeline: {result['name']}", "", f"Status: **{headline}**", "", "## Stages", ""]
    for item in result["stages"]:
        line = f"- {item['stage']}: {item['verdict']}"
        if item["report"]:
            line += f" (report: `{item['report']}`)"
        lines.append(line)
    lines += ["", "## Notes", "", _HEURISTIC_NOTE, "", "## Next steps", ""]
    lines += [f"{number}. `{step}`" for number, step in enumerate(result["next_steps"], start=1)]
    return "\n".join(lines) + "\n"


def _out_suffix(root: Path) -> str:
    """` --out <root>` when root is not the current directory, so printed commands work as is."""
    try:
        same = root.resolve() == Path.cwd().resolve()
    except OSError:
        same = False
    return "" if same else f" --out {shlex.quote(str(root))}"


def _finish(
    name: str,
    factory: Path,
    entries: dict[str, dict],
    stopped_at: str | None,
    version: str | None,
    next_steps: list[str],
) -> dict:
    """Fill in the stages that did not run, then write pipeline.json and pipeline.md."""
    stages = [entries.get(s) or _entry(s, "not-run", "not run", None) for s in STAGES]
    result = {
        "schema": SCHEMA,
        "name": name,
        "status": STOPPED if stopped_at else AWAITING,
        "stopped_at": stopped_at,
        "version": version,
        "stages": stages,
        "next_steps": next_steps,
    }
    try:
        factory.mkdir(parents=True, exist_ok=True)
        _write(factory / "pipeline.json", _dumps(result))
        _write(factory / "pipeline.md", _markdown(result))
    except OSError as exc:
        raise GenerateError(f"cannot write {factory}: {exc}") from exc
    return result


def _eval_verdict(report: dict) -> str:
    summary = report["summary"]
    failed = summary["positive"]["failed"] + summary["negative"]["failed"]
    passed = summary["positive"]["passed"] + summary["negative"]["passed"]
    return f"{passed} passed, {failed} failed (heuristic); baseline: {report['baseline']['status']}"


def _count(items: list, noun: str) -> str:
    return f"{len(items)} {noun}" + ("" if len(items) == 1 else "s")


def _regression_verdict(report: dict) -> str:
    failing = report.get("newly_failing", [])
    counts = f"{_count(report.get('newly_passing', []), 'gain')}, {_count(failing, 'regression')}"
    if report.get("status") == "regressed":
        ids = ", ".join(sorted(case["id"] for case in failing))
        return f"regressed ({counts} vs {report.get('baseline')}: {ids})"
    if report.get("status") == "pass":
        return f"pass ({counts} vs {report.get('baseline')}; {report.get('checked', 0)} cases compared)"
    return str(report.get("status"))


def run_pipeline(
    source: str,
    root: Path,
    name: str | None = None,
    runner_name: str | None = None,
    force: bool = False,
    runner_factory: Callable[[str], Runner | None] | None = None,
    judge_name: str | None = None,
) -> dict:
    """Run every stage and return the pipeline.json dict.

    A stop caused by the skill itself (validation errors, a regression, a failed integrity check)
    is returned with ``status: stopped``. Input errors (SpecError, GenerateError, RenderError,
    release.VersionExists and other ReleaseError) are raised without writing pipeline files.
    """
    root = Path(root)
    out = _out_suffix(root)
    entries: dict[str, dict] = {}

    spec = capture(source, name)
    entries["capture"] = _entry(
        "capture", "ok", f"captured {spec.name} ({spec.source_kind})", f"{FACTORY_DIR}/spec.json"
    )
    skill_dir = root / "skills" / spec.name
    factory = skill_dir / FACTORY_DIR
    if factory.is_symlink():
        raise GenerateError(f"{factory} is a symlink; refusing to write through it")

    generate(spec, root, force=force)
    entries["generate"] = _entry("generate", "ok", f"generated skills/{spec.name}/", "SKILL.md")
    name = spec.name

    def stop(stage: str, verdict: str, report: str | None, steps: list[str]) -> dict:
        entries[stage] = _entry(stage, "failed", verdict, report)
        return _finish(name, factory, entries, stage, None, steps)

    checked = validate.validate_skill(skill_dir)
    checked.write(factory)
    if not checked.ok:
        return stop(
            "validate",
            f"fail ({checked.errors} errors, {checked.warnings} warnings)",
            f"{FACTORY_DIR}/validate.json",
            [f"skillz validate {name}{out}"],
        )
    verdict = "pass" if not checked.warnings else f"pass ({checked.warnings} warnings)"
    entries["validate"] = _entry("validate", "ok", verdict, f"{FACTORY_DIR}/validate.json")

    rendered = render.render_skill(skill_dir)
    rendered.write(factory)
    entries["render"] = _entry(
        "render", "ok", "rendered " + ", ".join(rendered.variants), f"{FACTORY_DIR}/render.json"
    )

    report = evaluate_skill(skill_dir, runner_name, runner_factory)
    entries["evaluate"] = _entry("evaluate", "ok", _eval_verdict(report), f"{FACTORY_DIR}/eval.json")

    if runner_name is None:
        judged = None
        entries["judge"] = _entry("judge", "skipped", judge.SKIPPED_NO_RUNNER, None)
    else:
        judged = judge.judge_skill(skill_dir, runner_name, judge_name, runner_factory)
        entries["judge"] = _entry(
            "judge",
            "ok" if judged["status"] == "completed" else "skipped",
            judge.summary_line(judged),
            f"{FACTORY_DIR}/judge.json",
        )

    # package_skill re-validates, re-renders and re-evaluates offline, then runs the regression
    # check (including the judge report, so the model is not called twice); its two outcomes
    # become the regression and package stages.
    retry = [f"skillz package {name}{out}"]
    if runner_name is not None:
        retry[0] += f" --runner {runner_name}" + (f" --judge {judge_name}" if judge_name else "")
    try:
        version = release.package_skill(skill_dir, judge_report=judged)
    except release.RegressionFailed as exc:
        return stop(
            "regression", _regression_verdict(exc.report), f"{FACTORY_DIR}/regression.json", retry
        )
    except release.IntegrityError as exc:
        return stop("regression", f"integrity error: {exc}", None, retry)
    except release.ValidationFailed as exc:
        return stop("package", f"validation failed: {exc}", f"{FACTORY_DIR}/validate.json", retry)

    entries["regression"] = _entry(
        "regression",
        "ok",
        _regression_verdict(version.regression or {}),
        f"{FACTORY_DIR}/regression.json",
    )
    entries["package"] = _entry(
        "package",
        "ok",
        f"created {version.version}",
        f"{FACTORY_DIR}/versions/{version.version}/manifest.json",
    )
    entries["approval"] = _entry("approval", "ok", "awaiting approval", None)
    steps = [
        f"skillz approve {name} {version.version}{out}",
        f"skillz install {name} --platform claude|codex --scope project|user{out}",
    ]
    return _finish(name, factory, entries, None, version.version, steps)


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "run",
        help="run the whole pipeline and stop at awaiting approval",
        description=(
            "Capture, generate, validate, render, evaluate, judge, check regression and package a "
            "skill from a paragraph or a .txt/.yaml/.yml/.json spec, then stop at awaiting "
            "approval. "
            "It never approves or installs. Writes skills/<name>/.factory/pipeline.json and "
            "pipeline.md."
        ),
    )
    parser.add_argument("source", help="paragraph text, or a path to a .txt/.yaml/.yml/.json file")
    parser.add_argument("--name", help="skill name (kebab-case); default is derived from the source")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )
    parser.add_argument(
        "--runner",
        choices=sorted(EXECUTABLES),
        default=None,
        help="opt in to a with/without-skill baseline and a judge using this CLI, if on PATH",
    )
    parser.add_argument(
        "--judge",
        choices=sorted(EXECUTABLES),
        default=None,
        help="CLI that judges (default: the other model if both are on PATH, else the runner)",
    )
    parser.add_argument("--force", action="store_true", help="regenerate an existing skill")


def run(args: argparse.Namespace) -> int:
    root = args.out if args.out is not None else Path.cwd()
    try:
        result = run_pipeline(
            args.source, root, args.name, args.runner, args.force, judge_name=args.judge
        )
    except (ValueError, OSError) as exc:  # SpecError, GenerateError, RenderError, ReleaseError
        print(f"skillz: error: {exc}", file=sys.stderr)
        return 2
    print(Path(root) / "skills" / result["name"] / FACTORY_DIR / "pipeline.md")
    if result["status"] == STOPPED:
        stage = next(s for s in result["stages"] if s["stage"] == result["stopped_at"])
        print(f"skillz: stopped at {stage['stage']}: {stage['verdict']}", file=sys.stderr)
        return 1
    print(result["next_steps"][0])
    return 0
