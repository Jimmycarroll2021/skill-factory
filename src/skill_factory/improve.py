"""``skillz improve``: build a challenger candidate from eval proposals and captured failures.

The candidate lives in ``<skill>/.factory/candidates/<id>/`` and is a copy of the canonical skill
with a better description and spec. The stable skill is only read. The proposals are applied
deterministically: the same inputs give the same SKILL.md and spec.json bytes. The SKILL.md body
after the frontmatter is copied byte for byte. Nothing here spawns a process or consults a model.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from skill_factory import (
    candidates,
    captured,
    evalcases,
    evaluate,
    release,
    render,
    yamlish,
)
from skill_factory.candidates import CandidateError
from skill_factory.capture import (
    EXCLUSION_RE,
    MAX_DESCRIPTION,
    NAME_RE,
    SkillSpec,
    SpecError,
)

SCHEMA = 1
_SUGGESTED_PHRASES = 3  # at most three trigger situations for a vague description
# Whole requests, not loose keywords: a list of generic words ("help, write, file") makes a
# skill load on unrelated prompts, while a quoted request gives a model the situation.
_SENTENCE = " Also use for requests like {phrases}."
_MAX_SITUATION_WORDS = 12
_MIN_SITUATION_WORDS = 3


def _situation(text: str) -> str:
    """A request trimmed to at most _MAX_SITUATION_WORDS words, without trailing punctuation."""
    words = text.replace('"', "'").split()[:_MAX_SITUATION_WORDS]
    return " ".join(words).rstrip(".!?,;:")


def _quoted(phrases: list[str]) -> str:
    return "; ".join(f'"{p}"' for p in phrases)


class NothingToImprove(ValueError):
    """There are no eval proposals and no captured cases, so no candidate was created."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _body_bytes(skill_md: Path) -> bytes:
    """Everything after the closing ``---`` line of the frontmatter, byte for byte."""
    lines = skill_md.read_bytes().splitlines(keepends=True)
    if not lines or lines[0].lstrip(b"\xef\xbb\xbf").rstrip() != b"---":
        raise SpecError(f"{skill_md}: SKILL.md has no frontmatter")
    for index in range(1, len(lines)):
        if lines[index].rstrip() == b"---":
            return b"".join(lines[index + 1 :])
    raise SpecError(f"{skill_md}: frontmatter is not closed with '---'")


def _new_words(prompts: list[str], known: set[str]) -> list[str]:
    """Content words of the prompts that are not in ``known``, in order and deduplicated."""
    found: list[str] = []
    for prompt in prompts:
        for word in evalcases.content_words(prompt):
            if word not in known and word not in found:
                found.append(word)
    return found


def _task_word(word: str, avoid: set[str]) -> bool:
    found = evalcases.content_words(word)
    return bool(found) and found[0] not in avoid


def _fit(
    description: str, tagged: list[tuple[str, str]], avoid: set[str] = frozenset()
) -> list[tuple[str, str]]:
    """Drop requests from the end until the extended description is at most MAX_DESCRIPTION.

    If not even one whole request fits, shorten the first one rather than add nothing: keep
    only its task words (content words not in ``avoid``, the description's own words and
    those of prompts the skill must not take), then drop them from the end down to
    _MIN_SITUATION_WORDS. A request with no task words left is skipped, so a generic
    prefix such as "Please help me" is never added.
    """

    def fits(items: list[tuple[str, str]]) -> bool:
        return len(description + _SENTENCE.format(phrases=_quoted([w for w, _ in items]))) <= (
            MAX_DESCRIPTION
        )

    kept = list(tagged)
    while kept and not fits(kept):
        kept.pop()
    if not kept and tagged:
        phrase, area = tagged[0]
        words = [w for w in phrase.split() if _task_word(w, avoid)]
        if not words:
            return []
        while len(words) > _MIN_SITUATION_WORDS and not fits([(" ".join(words), area)]):
            words.pop()
        if fits([(" ".join(words), area)]):
            kept = [(" ".join(words).rstrip(".!?,;:"), area)]
    return kept


def _plan(
    skill_dir: Path,
) -> tuple[SkillSpec, SkillSpec, dict, list[dict], list[dict], list[str]]:
    """The stable spec, the improved spec, the frontmatter, and what was applied or skipped."""
    spec = evaluate.load_spec(skill_dir)
    try:
        meta = render._frontmatter(skill_dir / "SKILL.md")
    except render.RenderError as exc:
        raise SpecError(str(exc)) from exc
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SpecError("SKILL.md frontmatter needs a non-empty description")
    spec = dataclasses.replace(spec, description=description)

    extra = captured.load_captured(skill_dir)
    cases = evalcases.generate_cases(spec) + extra
    grades = evalcases.grade_offline(cases, spec.name, spec.description)
    proposals = evalcases.propose_improvements(spec, cases, grades)
    if not proposals and not extra:
        raise NothingToImprove(
            f"{skill_dir.name}: no improvement proposals and no captured cases"
        )

    by_id = {case.id: case for case in cases}
    # The same words the grader counts: boundary sentences are not trigger vocabulary.
    known = set(evalcases.content_words(f"{spec.name} {evalcases.trigger_text(spec.description)}"))
    areas = [p.area for p in proposals]
    described: list[dict] = []
    applied: list[dict] = []
    skipped: list[dict] = []
    tagged: list[tuple[str, str]] = []
    non_triggers = list(spec.non_triggers)
    triggers = list(spec.triggers)

    def tag(area: str, words: list[str]) -> None:
        tagged.extend((w, area) for w in words if w not in [t for t, _ in tagged])

    for proposal in proposals:
        if proposal.area == "trigger-vocabulary":
            prompts = [by_id[c].prompt for c in proposal.cases]
            # A request that itself reads as an exclusion would be dropped by the grader.
            tag(
                proposal.area,
                [
                    _situation(p)
                    for p in prompts
                    if _new_words([p], known) and not EXCLUSION_RE.search(p)
                ],
            )
        elif proposal.area == "vague-description":
            wanted = [t for t in spec.triggers if _new_words([t], known)]
            tag(proposal.area, [_situation(t) for t in wanted[:_SUGGESTED_PHRASES]])
        elif proposal.area == "negative-overlap":
            for case_id in proposal.cases:
                prompt = by_id[case_id].prompt
                if prompt not in non_triggers:
                    non_triggers.append(prompt)
                    applied.append(
                        {
                            "area": proposal.area,
                            "action": "add-non-trigger",
                            "detail": prompt,
                        }
                    )
        else:
            skipped.append(
                {"area": proposal.area, "reason": "needs human judgement; not applied"}
            )

    # Words of prompts the skill must not take: a shortened request keeping only these would
    # raise the negatives as much as the positives.
    negative = {
        w
        for case in cases
        if case.kind == "negative"
        for w in evalcases.content_words(case.prompt)
    }
    kept = _fit(spec.description, tagged, known | negative)
    for area in ("trigger-vocabulary", "vague-description"):
        if area not in areas:
            continue
        phrases = [w for w, a in kept if a == area]
        if phrases:
            described.append(
                {
                    "area": area,
                    "action": "extend-description",
                    "detail": _quoted(phrases),
                }
            )
        elif any(a == area for _, a in tagged):
            skipped.append(
                {
                    "area": area,
                    "reason": f"no request fits within {MAX_DESCRIPTION} characters",
                }
            )
        else:
            skipped.append(
                {"area": area, "reason": "no new requests to add to the description"}
            )
    new_description = spec.description
    if kept:
        new_description = spec.description.rstrip() + _SENTENCE.format(
            phrases=_quoted([w for w, _ in kept])
        )

    used: list[str] = []
    for case in extra:
        if case.kind == "positive":
            if case.prompt not in triggers:
                triggers.append(case.prompt)
            action, detail = "add-trigger", case.prompt
        elif case.kind == "negative":
            if case.prompt not in non_triggers:
                non_triggers.append(case.prompt)
            action, detail = "add-non-trigger", case.prompt
        else:
            skipped.append(
                {
                    "area": f"captured:{case.id}",
                    "reason": "behaviour cases stay eval cases and are not written to the spec",
                }
            )
            continue
        used.append(case.id)
        applied.append(
            {"area": f"captured:{case.id}", "action": action, "detail": detail}
        )

    improved = dataclasses.replace(
        spec, description=new_description, triggers=triggers, non_triggers=non_triggers
    )
    return spec, improved, meta, described + applied, skipped, used


def _skill_text(
    skill_md: Path, meta: dict, improved: SkillSpec, changed: bool
) -> bytes:
    """The candidate SKILL.md: new frontmatter plus the original body bytes."""
    if not changed:
        return skill_md.read_bytes()
    new_meta = dict(meta)
    new_meta["description"] = improved.description
    try:
        head = yamlish.frontmatter_dumps(new_meta)
    except ValueError as exc:
        raise SpecError(f"cannot write the candidate frontmatter: {exc}") from exc
    return head.encode("utf-8") + _body_bytes(skill_md)


def improve_skill(skill_dir: Path) -> dict:
    """Create the next candidate and return its candidate.json dict.

    Raises NothingToImprove (nothing written), SpecError for an unreadable skill and
    CandidateError when a write would go through a symlink.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise SpecError(f"skill directory not found: {skill_dir}")
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise SpecError(f"SKILL.md not found: {skill_md}")
    factory = skill_dir / candidates.FACTORY_DIR
    root = candidates.candidates_root(skill_dir)
    for guarded in (factory, root):
        if guarded.is_symlink():
            raise CandidateError(
                f"{guarded} is a symlink; refusing to write through it"
            )

    spec, improved, meta, applied, skipped, used = _plan(skill_dir)
    files, _skipped = render._canonical_files(skill_dir)
    versions = release.load_versions(skill_dir)
    cid = candidates.next_candidate_id(skill_dir)
    target = candidates.candidate_dir(skill_dir, cid)
    dest = candidates.candidate_skill_dir(skill_dir, cid)
    record = {
        "schema": SCHEMA,
        "id": cid,
        "skill": spec.name,
        "created_at": _now(),
        "base_version": versions[-1].version if versions else None,
        "base_sha256": candidates.stable_digests(skill_dir),
        "applied": applied,
        "skipped": skipped,
        "captured": used,
    }
    changed = improved.description != spec.description
    try:
        root.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        dest.mkdir()
        for rel in files:
            if rel == "SKILL.md":
                continue
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_dir / rel, dest / rel)
        (dest / "SKILL.md").write_bytes(_skill_text(skill_md, meta, improved, changed))
        _write(target / candidates.SPEC_JSON, _dumps(improved.to_dict()))
        _write(target / candidates.CANDIDATE_JSON, _dumps(record))
    except (OSError, SpecError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, SpecError):
            raise
        raise CandidateError(f"cannot write {target}: {exc}") from exc
    return record


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "improve",
        help="build a challenger candidate of skills/<name>/ from eval proposals and failures",
        description=(
            "Apply the eval proposals and captured failures to a copy of the skill under "
            ".factory/candidates/<id>/. The stable skill is never changed; run `skillz "
            "challenge` to compare the candidate with it."
        ),
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="root directory containing skills/ (default: cwd)",
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
        record = improve_skill(skill_dir)
    except NothingToImprove as exc:
        print(f"skillz: nothing to improve: {exc}", file=sys.stderr)
        return 1
    except (SpecError, CandidateError, release.ReleaseError) as exc:
        return _fail(str(exc))
    print(f"improve {args.name}: created candidate {record['id']}")
    print(candidates.candidate_dir(skill_dir, record["id"]))
    return 0
