"""Deterministic evaluation cases, an offline lexical-overlap matcher, grading and proposals.

Cases come from three sources, assembled by ``all_cases``: the spec (``generate_cases``), the
skill's committed ``evals/evals.json`` (``load_evals``), and captured failures. A skill written by
hand has no spec, so its evals are what gets graded. Nothing here runs a model or spawns a
process. The trigger matcher is a heuristic: it measures how many of a prompt's content words
also appear in the skill's name and description.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from skill_factory.capture import (
    _STOP_WORDS,
    EXCLUSION_RE,
    SkillSpec,
    SpecError,
    _sentences,
    _words,
)

MATCH_THRESHOLD = 0.3
MIN_DESCRIPTION_WORDS = 6
METHOD = (
    "heuristic lexical overlap: a prompt matches when the share of its content words found "
    "in the skill name and description reaches the threshold; no model is consulted"
)

GENERIC_NEGATIVES: tuple[str, ...] = (
    "What is the weather forecast for tomorrow in Paris?",
    "Write a haiku about autumn leaves.",
    "Translate the sentence 'good morning, my friend' into Spanish.",
    "Explain how photosynthesis works in plants.",
    "Suggest a recipe for a vegetarian lasagne.",
)

_MAX_MARKERS = 3
_MAX_MISSING_WORDS = 8
_MAX_SUGGESTED_REQUESTS = 3

EVALS_FILE = Path("evals") / "evals.json"
EVALS_SOURCE = "evals.json"
_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _normalise(word: str) -> str:
    """Light, deterministic suffix normalisation: a plural trailing 's' is dropped."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def content_words(text: str) -> list[str]:
    """Lowercase content words in order of first appearance, stop-words and duplicates removed."""
    seen: list[str] = []
    for word in _words(text):
        if word in _STOP_WORDS:
            continue
        word = _normalise(word)
        if word and word not in _STOP_WORDS and word not in seen:
            seen.append(word)
    return seen


def trigger_text(description: str) -> str:
    """The description without its boundaries ("Do not use it for X", "Never Y"), each cut from
    where the boundary starts to the end of its sentence.

    Those sentences tell the model to stay away from X, so their words are not trigger
    vocabulary and must not make a request about X look like a match.
    """
    kept = []
    for sentence in _sentences(description):
        match = EXCLUSION_RE.search(sentence)
        # "Convert exports to CSV; not for PDFs": the clause before the boundary still counts.
        kept.append(sentence[: match.start()] if match else sentence)
    return " ".join(k for k in kept if k.strip())


def match_score(prompt: str, name: str, description: str) -> float:
    """Share of the prompt's content words that also occur in the name plus description,
    leaving out the description's boundary sentences."""
    prompt_words = content_words(prompt)
    if not prompt_words:
        return 0.0
    skill_words = set(content_words(f"{name} {trigger_text(description)}"))
    hits = sum(1 for word in prompt_words if word in skill_words)
    return round(hits / len(prompt_words), 4)


@dataclass
class Case:
    id: str
    kind: str
    prompt: str
    source: str
    expected: list[str] = field(default_factory=list)
    rubric: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "prompt": self.prompt,
            "source": self.source,
            "expected": list(self.expected),
        }
        if self.rubric:
            data["rubric"] = list(self.rubric)
        return data


@dataclass
class Grade:
    id: str
    kind: str
    score: float | None
    matched: bool | None
    status: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "score": self.score,
            "matched": self.matched,
            "status": self.status,
        }


@dataclass
class Proposal:
    area: str
    problem: str
    proposal: str
    cases: list[str]

    def to_dict(self) -> dict:
        return {
            "area": self.area,
            "problem": self.problem,
            "proposal": self.proposal,
            "cases": list(self.cases),
        }


def _first_sentence(text: str) -> str:
    sentences = _sentences(text)
    return sentences[0] if sentences else text.strip()


def _markers(behaviour: str) -> list[str]:
    words = content_words(behaviour)[:_MAX_MARKERS]
    return words or [" ".join(behaviour.lower().split())]


def _task_prompt(trigger: str, behaviours: list[str], name: str) -> str:
    """A task request built from the trigger text; no behaviour sentence appears in it."""
    text = " ".join(trigger.split()).rstrip(".!?:; ") or name
    prompt = f"{text}. Please work through the request end to end and show me the result."
    squashed = prompt.lower()
    if any(" ".join(b.lower().split()) in squashed for b in behaviours):
        prompt = f"Please help me with a task related to {name}."
    return prompt


def _mentions(markers: list[str]) -> str:
    return f"mentions: {', '.join(markers)}"


def _must_not(spec: SkillSpec) -> list[str]:
    """At most one criterion, derived from the first non-trigger."""
    if not spec.non_triggers:
        return []
    return [f'must not: act on out-of-scope requests such as "{spec.non_triggers[0]}"']


def _behaviour_cases(spec: SkillSpec) -> list[Case]:
    """One case per spec behaviour, then one per trigger, with prompts, rubrics and markers."""
    fallback = _first_sentence(spec.description) or spec.name
    tasks = spec.triggers or [fallback]
    must_not = _must_not(spec)
    cases: list[Case] = []
    for i, behaviour in enumerate(spec.behaviours):
        markers = _markers(behaviour)
        cases.append(
            Case(
                f"beh-{i + 1:03d}",
                "behaviour",
                _task_prompt(tasks[i % len(tasks)], [behaviour], spec.name),
                f"behaviour:{i}",
                markers,
                [behaviour, _mentions(markers), *must_not],
            )
        )
    union: list[str] = []
    for behaviour in spec.behaviours:
        for marker in _markers(behaviour):
            if marker not in union:
                union.append(marker)
    for i, trigger in enumerate(spec.triggers):
        markers = union or content_words(trigger)[:_MAX_MARKERS] or _markers(trigger)
        goals = list(spec.behaviours) or [f"Completes the request: {trigger}"]
        cases.append(
            Case(
                f"beh-t{i + 1:03d}",
                "behaviour",
                _task_prompt(trigger, spec.behaviours, spec.name),
                f"trigger:{i}",
                markers,
                [*goals, _mentions(markers), *must_not],
            )
        )
    return cases


def generate_cases(spec: SkillSpec) -> list[Case]:
    """Positive, negative, behaviour-derived, then trigger-derived cases with stable ids."""
    cases: list[Case] = []
    positives = [(trigger, f"trigger:{i}") for i, trigger in enumerate(spec.triggers)]
    positives.append((_first_sentence(spec.description), "description"))
    for n, (prompt, source) in enumerate(positives, start=1):
        cases.append(Case(f"pos-{n:03d}", "positive", prompt, source))
    negatives = [(text, f"non_trigger:{i}") for i, text in enumerate(spec.non_triggers)]
    negatives += [(text, f"generic:{i}") for i, text in enumerate(GENERIC_NEGATIVES)]
    for n, (prompt, source) in enumerate(negatives, start=1):
        cases.append(Case(f"neg-{n:03d}", "negative", prompt, source))
    cases.extend(_behaviour_cases(spec))
    return cases


def evals_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / EVALS_FILE


def _read_evals(path: Path) -> list[dict]:
    """The entries of evals.json, each with a non-empty string prompt; ``[]`` without the file."""
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
    if not isinstance(data, list):
        raise SpecError(f"{path} must be a JSON list of eval entries")
    for i, entry in enumerate(data):
        prompt = entry.get("prompt") if isinstance(entry, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            raise SpecError(f"{path}: entry {i} needs a non-empty string prompt")
    return data


def _eval_id(entry: dict, index: int) -> str:
    """``evl-<id>`` with the id reduced to ``[A-Za-z0-9_-]``.

    Without an id the case is named after what it checks (the first 10 hex digits of the sha256
    of the whitespace-normalised prompt and its sorted, normalised assertions, joined by
    newlines), never after its position: a receipt compares cases by id across versions, and a
    positional id would hand one prompt's history to another whenever the file is reordered or
    an entry is inserted. Two id-less entries on one prompt with different assertions are two
    cases. ``index`` is only used for error messages.
    """
    raw = entry.get("id")
    cleaned = _ID_UNSAFE_RE.sub("-", raw).strip("-") if isinstance(raw, str) else ""
    if cleaned:
        return f"evl-{cleaned}"
    parts = [_norm(str(entry.get("prompt", ""))), *sorted(_norm(a) for a in _assertions(entry))]
    return "evl-" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:10]


def _assertions(entry: dict) -> list[str]:
    items = entry.get("assertions")
    if not isinstance(items, list):
        return []
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


def load_evals(skill_dir: Path) -> list[Case]:
    """Cases from ``<skill>/evals/evals.json``: one trigger case per entry and, for an entry that
    should trigger and has assertions, a behaviour case whose rubric is those assertions.

    A missing file gives ``[]``; an unreadable or malformed one raises SpecError naming the path.
    """
    path = evals_path(skill_dir)
    cases: list[Case] = []
    seen: set[str] = set()
    for i, entry in enumerate(_read_evals(path)):
        case_id = _eval_id(entry, i)
        prompt = entry["prompt"].strip()
        triggers = bool(entry.get("should_trigger", True))
        rubric = _assertions(entry)
        new = [Case(case_id, "positive" if triggers else "negative", prompt, EVALS_SOURCE)]
        if triggers and rubric:
            new.append(Case(f"{case_id}-beh", "behaviour", prompt, EVALS_SOURCE, [], rubric))
        for case in new:  # an entry's behaviour case id must not collide with another entry's id
            if case.id in seen:
                raise SpecError(f"{path}: entry {i} has a duplicate id {case.id!r}")
            seen.add(case.id)
        cases.extend(new)
    return cases


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def case_key(case: Case) -> tuple:
    """What a case checks: its kind, prompt and criteria (rubric and expected markers)."""
    return (
        case.kind,
        _norm(case.prompt),
        tuple(sorted(_norm(item) for item in case.rubric)),
        tuple(sorted(_norm(item) for item in case.expected)),
    )


class CaseIndex:
    """The cases already kept, by what they check, for de-duplication across sources.

    Two cases with one prompt but different criteria are two checks and both are kept. The same
    criteria on the same prompt is one check, and the first source wins. A case with no criteria
    of its own (a bare trigger prompt, or a captured behaviour failure without a rubric) adds
    nothing to a case already kept for that kind and prompt, so it is covered by whichever came
    first.
    """

    def __init__(self) -> None:
        self._keys: set[tuple] = set()
        self._first: dict[tuple, tuple] = {}

    def covering(self, case: Case) -> tuple | None:
        """The key of the kept case that already checks what ``case`` checks, or None."""
        key = case_key(case)
        if key in self._keys:
            return key
        if key[2:] == ((), ()):
            return self._first.get(key[:2])
        return None

    def add(self, case: Case) -> tuple:
        key = case_key(case)
        self._keys.add(key)
        self._first.setdefault(key[:2], key)
        return key


def _merge(generated: list[Case], *extra: list[Case]) -> list[Case]:
    """The generated cases unchanged, then each extra case that ``CaseIndex`` does not cover.

    Generated ids are pinned by the spec and may legitimately repeat a prompt (a trigger-derived
    behaviour case beside a behaviour-derived one), so only the extra sources are de-duplicated,
    first occurrence winning: an evals entry beats a captured failure with the same prompt.
    """
    merged = list(generated)
    index = CaseIndex()
    for case in merged:
        index.add(case)
    for cases in extra:
        for case in cases:
            if index.covering(case) is None:
                index.add(case)
                merged.append(case)
    return merged


def all_cases(skill_dir: Path, spec: SkillSpec) -> list[Case]:
    """Every case for a skill: generated from the spec, then evals.json, then captured failures,
    the first case that checks the same thing (see ``CaseIndex``) winning across sources."""
    # captured imports Case from this module, so it is resolved at call time, not at import.
    from skill_factory import captured

    return _merge(generate_cases(spec), load_evals(skill_dir), captured.load_captured(skill_dir))


def cases_json(cases: list[Case]) -> str:
    """Byte-identical JSON for the same cases."""
    payload = {"cases": [case.to_dict() for case in cases]}
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def grade_offline(
    cases: list[Case], name: str | SkillSpec, description: str | None = None
) -> list[Grade]:
    """Grade each case with the lexical matcher; behaviour cases wait for a runner."""
    if isinstance(name, SkillSpec):
        name, description = name.name, name.description
    description = description or ""
    grades: list[Grade] = []
    for case in cases:
        if case.kind == "behaviour":
            grades.append(Grade(case.id, case.kind, None, None, "pending-runner"))
            continue
        score = match_score(case.prompt, name, description)
        matched = score >= MATCH_THRESHOLD
        passed = matched if case.kind == "positive" else not matched
        grades.append(Grade(case.id, case.kind, score, matched, "pass" if passed else "fail"))
    return grades


def _join(words: list[str]) -> str:
    return ", ".join(words)


def propose_improvements(
    spec: SkillSpec, cases: list[Case], grades: list[Grade]
) -> list[Proposal]:
    """Weak areas with concrete changes, in a fixed order; empty when nothing is weak."""
    by_id = {case.id: case for case in cases}
    skill_words = set(content_words(f"{spec.name} {trigger_text(spec.description)}"))
    proposals: list[Proposal] = []

    failing = [g for g in grades if g.kind == "positive" and g.status == "fail"]
    if failing:
        missing: list[str] = []
        for grade in failing:
            for word in content_words(by_id[grade.id].prompt):
                if word not in skill_words and word not in missing:
                    missing.append(word)
        ids = [g.id for g in failing]
        proposals.append(
            Proposal(
                "trigger-vocabulary",
                f"{len(ids)} trigger prompt(s) do not match the skill description: {_join(ids)}",
                "Add these words users will say to the description: "
                + _join(missing[:_MAX_MISSING_WORDS]),
                ids,
            )
        )

    matching = [g for g in grades if g.kind == "negative" and g.status == "fail"]
    if matching:
        overlap: list[str] = []
        for grade in matching:
            for word in content_words(by_id[grade.id].prompt):
                if word in skill_words and word not in overlap:
                    overlap.append(word)
        ids = [g.id for g in matching]
        proposals.append(
            Proposal(
                "negative-overlap",
                f"{len(ids)} prompt(s) that should not trigger the skill match it: {_join(ids)}",
                f"The prompts share these words with the description: {_join(overlap)}. "
                "Narrow the description to avoid them, or add the prompts to non_triggers "
                "so the skill states when not to use it.",
                ids,
            )
        )

    if len(content_words(spec.description)) < MIN_DESCRIPTION_WORDS:
        wanted = [t for t in spec.triggers if t.strip()][:_MAX_SUGGESTED_REQUESTS]
        direction = (
            "Rewrite the description to say what the skill does and when to use it, "
            "naming whole requests such as: " + "; ".join(f'"{t.strip()}"' for t in wanted)
            if wanted
            else "Rewrite the description to say what the skill does and when to use it, "
            "including the words users will say when they need it"
        )
        proposals.append(
            Proposal(
                "vague-description",
                f"The description has fewer than {MIN_DESCRIPTION_WORDS} content words.",
                direction,
                [],
            )
        )

    if not spec.behaviours:
        proposals.append(
            Proposal(
                "no-behaviours",
                "The spec lists no expected behaviours, so no behavioural cases exist.",
                "Add expected behaviours, each a sentence naming what the skill must do or "
                "produce, so they can be checked once a runner is available.",
                [],
            )
        )
    return proposals
