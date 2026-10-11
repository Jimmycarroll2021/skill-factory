"""Opt-in model baseline: run behaviour cases with and without a skill through claude or codex.

Nothing here runs unless the caller passes a runner. `detect_runner` only returns one when the
executable is on PATH, and every run goes through `subprocess.run` with an argv list, a timeout
and no shell. The skill text reaches the model by being embedded in the prompt
(`with_skill_prompt`), so both CLIs are driven identically. This module takes plain case dicts
and does not import evalcases.py.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Protocol

SKIPPED_NO_RUNNER = "skipped: no runner"
EXECUTABLES = {"claude": "claude", "codex": "codex"}
DEFAULT_TIMEOUT = 120.0  # seconds per model call


class RunnerError(RuntimeError):
    """A model call failed: non-zero exit, timeout or the executable could not be started."""


class Runner(Protocol):
    name: str

    def run(self, prompt: str) -> str:
        """Return the model's text output, or raise RunnerError."""
        ...


def _execute(argv: list[str], timeout: float) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"{argv[0]} timed out after {timeout} seconds") from exc
    except OSError as exc:
        raise RunnerError(f"cannot run {argv[0]}: {exc}") from exc
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or "").split())[:200]
        raise RunnerError(f"{argv[0]} exited with status {proc.returncode}: {detail}")
    return proc.stdout or ""


class ClaudeRunner:
    name = "claude"

    def __init__(self, executable: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.executable = executable
        self.timeout = timeout

    def argv(self, prompt: str) -> list[str]:
        return [self.executable, "-p", "--output-format", "text", "--", prompt]

    def run(self, prompt: str) -> str:
        return _execute(self.argv(prompt), self.timeout)


class CodexRunner:
    name = "codex"

    def __init__(self, executable: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.executable = executable
        self.timeout = timeout

    def argv(self, prompt: str) -> list[str]:
        return [self.executable, "exec", "--", prompt]

    def run(self, prompt: str) -> str:
        return _execute(self.argv(prompt), self.timeout)


_RUNNER_CLASSES = {"claude": ClaudeRunner, "codex": CodexRunner}


def detect_runner(name: str | None, timeout: float = DEFAULT_TIMEOUT) -> Runner | None:
    """Return a runner for `name` when its executable is on PATH, else None.

    shutil.which is looked up at call time so tests can patch it. Raises ValueError for a name
    other than claude or codex.
    """
    if name is None:
        return None
    if name not in EXECUTABLES:
        raise ValueError(
            f"unknown runner {name!r}; expected one of {sorted(EXECUTABLES)}"
        )
    path = shutil.which(EXECUTABLES[name])
    if not path:
        return None
    return _RUNNER_CLASSES[name](path, timeout)


def skipped_baseline(requested: str | None = None) -> dict:
    result: dict = {"status": SKIPPED_NO_RUNNER}
    if requested is not None:
        result["requested"] = requested
    return result


def with_skill_prompt(skill_text: str, prompt: str) -> str:
    """Wrap the SKILL.md text before the prompt, deterministically."""
    return (
        "You have the following skill available. Follow it when it applies to the task.\n\n"
        "<skill>\n"
        f"{skill_text.strip()}\n"
        "</skill>\n\n"
        f"Task: {prompt}"
    )


def grade_markers(output: str, expected: list[str]) -> dict:
    """Case-insensitive substring check of each expected marker against the output."""
    haystack = output.casefold()
    found = [m for m in expected if m.casefold() in haystack]
    missing = [m for m in expected if m.casefold() not in haystack]
    return {"passed": not missing, "found": found, "missing": missing}


def _verdict(with_skill: dict, without_skill: dict) -> str:
    gained, base = len(with_skill["found"]), len(without_skill["found"])
    if gained > base:
        return "skill-helps"
    if gained < base:
        return "skill-hurts"
    return "no-difference"


_SUMMARY_KEYS = {
    "skill-helps": "skill_helps",
    "no-difference": "no_difference",
    "skill-hurts": "skill_hurts",
    "error": "errors",
}


def run_baseline(runner: Runner, cases: list[dict], skill_text: str) -> dict:
    """Run each behaviour case with and without the skill text and compare marker coverage."""
    summary = {key: 0 for key in _SUMMARY_KEYS.values()}
    results = []
    for case in cases:
        if case.get("kind") != "behaviour":
            continue
        prompt = case["prompt"]
        expected = list(case.get("expected") or [])
        try:
            with_out = runner.run(with_skill_prompt(skill_text, prompt))
            without_out = runner.run(prompt)
        except RunnerError as exc:
            results.append({"id": case["id"], "verdict": "error", "error": str(exc)})
            summary["errors"] += 1
            continue
        with_skill = grade_markers(with_out, expected)
        without_skill = grade_markers(without_out, expected)
        verdict = _verdict(with_skill, without_skill)
        results.append(
            {
                "id": case["id"],
                "with_skill": with_skill,
                "without_skill": without_skill,
                "verdict": verdict,
            }
        )
        summary[_SUMMARY_KEYS[verdict]] += 1
    return {
        "status": "completed",
        "runner": runner.name,
        "cases": results,
        "summary": summary,
    }
