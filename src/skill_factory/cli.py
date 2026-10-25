"""The ``skillz`` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from skill_factory import (
    __version__,
    candidate,
    captured,
    challenge,
    evaluate,
    improve,
    install,
    judge,
    pipeline,
    promote,
    release,
    render,
    validate,
)
from skill_factory.capture import SpecError, capture
from skill_factory.generate import GenerateError, generate


def _add_new(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "new",
        help="capture a skill spec and generate skills/<name>/",
        description="Capture a skill from a paragraph or a .txt/.yaml/.yml/.json spec file.",
    )
    parser.add_argument("source", help="paragraph text, or a path to a .txt/.yaml/.yml/.json file")
    parser.add_argument("--name", help="skill name (kebab-case); default is derived from the source")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing skill")


def _run_new(args: argparse.Namespace) -> int:
    spec = capture(args.source, args.name)
    root = args.out if args.out is not None else Path.cwd()
    print(generate(spec, root, force=args.force))
    return 0


# Later missions register a subcommand by adding one function here and one entry in HANDLERS.
SUBCOMMANDS: tuple[Callable[[argparse._SubParsersAction], None], ...] = (
    _add_new,
    validate.add_parser,
    render.add_parser,
    evaluate.add_parser,
    release.add_package_parser,
    release.add_approve_parser,
    install.add_install_parser,
    install.add_rollback_parser,
    install.add_list_parser,
    pipeline.add_parser,
    captured.add_parser,
    improve.add_parser,
    candidate.add_parser,
    challenge.add_parser,
    promote.add_parser,
    judge.add_parser,
)
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "new": _run_new,
    "validate": validate.run,
    "render": render.run,
    "eval": evaluate.run,
    "package": release.run_package,
    "approve": release.run_approve,
    "install": install.run_install,
    "rollback": install.run_rollback,
    "list": install.run_list,
    "run": pipeline.run,
    "capture-failure": captured.run,
    "improve": improve.run,
    "candidate": candidate.run,
    "challenge": challenge.run,
    "promote": promote.run,
    "judge": judge.run,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillz", description="Capture, test, package and manage agent skills."
    )
    parser.add_argument("--version", action="version", version=f"skillz {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for add in SUBCOMMANDS:
        add(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (SpecError, GenerateError) as exc:
        print(f"skillz: error: {exc}", file=sys.stderr)
        return 2
