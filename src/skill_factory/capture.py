"""Capture a skill spec from a paragraph or a small YAML/JSON spec file."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, fields
from pathlib import Path

from skill_factory import yamlish

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
RESOURCE_DIRS = ("scripts", "references", "assets")
SOURCE_KINDS = ("paragraph", "yaml", "json")
SPEC_SUFFIXES = {".txt", ".yaml", ".yml", ".json"}

_NAME_WORDS = 4
_MAX_PHRASES = 8
_MAX_PHRASE_LEN = 200

_STOP_WORDS = frozenset(
    """a an the and or but nor of to in on at by for from with without into onto over under
    as is are was were be been being it its this that these those there their them they we
    you your our i me my he she his her if then than so such can could may might must shall
    should will would do does did doing done have has had having not no never also just very
    each every any some all more most other about up out off per via use used uses using
    skill skills when whenever which who whom what while where how why""".split()  # noqa: SIM905
)

_NEGATIVE_RE = re.compile(r"\b(?:not|never|nor|cannot)\b|n't\b", re.IGNORECASE)
# Any negative statement: what capture files as a non-trigger and what the validator accepts
# as the description saying where the skill stops.
NEGATIVE_RE = _NEGATIVE_RE
# An explicit exclusion ("Do not use it for X", "NOT for X", "Never Y", "It does not handle X"). The grader drops these
# sentences from a description's trigger words; a plain negation such as "files that do not
# parse" describes the job and stays.
EXCLUSION_RE = re.compile(
    r"\bNOT\b|(?:^|[.!?]\s+)Never\b|(?i:\bnot for\b|\bdo not use\b|\bdon't use\b"
    r"|\bnever use\b|\brefuses?\b|\bdistinct from\b|\b(?:use|see) [a-z0-9-]+ instead\b"
    # "It does not handle PDFs": what the skill will not take. "files that do not parse" stays.
    r"|\b(?:(?:does|do|will|can) not|doesn't|don't|won't|can't|cannot)"
    r" (?:handle|support|cover|accept|process|work (?:on|with)|apply to|deal with)\b)"
)
_TRIGGER_RE = re.compile(
    r"\bwhen(?:ever)?\b|\buse (?:it|this)\b|\bif the user\b|\btriggers? (?:on|when)\b",
    re.IGNORECASE,
)
_BEHAVIOUR_RE = re.compile(r"\b(?:should|must|always|will)\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class SpecError(ValueError):
    """Raised when a skill spec is malformed or violates the Agent Skills limits."""


@dataclass
class SkillSpec:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    non_triggers: list[str] = field(default_factory=list)
    behaviours: list[str] = field(default_factory=list)
    license: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)
    source_kind: str = "paragraph"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": list(self.triggers),
            "non_triggers": list(self.non_triggers),
            "behaviours": list(self.behaviours),
            "license": self.license,
            "allowed_tools": list(self.allowed_tools),
            "metadata": dict(self.metadata),
            "resources": list(self.resources),
            "source_kind": self.source_kind,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SkillSpec:
        if not isinstance(data, dict):
            raise SpecError("a spec must be a mapping")
        known = {f.name for f in fields(cls)}
        normalised: dict = {}
        for key, value in data.items():
            norm = str(key).replace("-", "_")
            if norm not in known:
                raise SpecError(f"unknown spec field: {key!r}")
            normalised[norm] = value
        metadata = normalised.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise SpecError("metadata must be a mapping")
        return cls(
            name=_as_text(normalised.get("name"), "name"),
            description=_as_text(normalised.get("description"), "description"),
            triggers=_as_list(normalised.get("triggers"), "triggers"),
            non_triggers=_as_list(normalised.get("non_triggers"), "non_triggers"),
            behaviours=_as_list(normalised.get("behaviours"), "behaviours"),
            license=normalised.get("license"),
            allowed_tools=_as_list(normalised.get("allowed_tools"), "allowed_tools", split=True),
            metadata=dict(metadata),
            resources=_as_list(normalised.get("resources"), "resources"),
            source_kind=normalised.get("source_kind") or "yaml",
        )


def _as_text(value, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpecError(f"{label} must be a string")
    return value


def _as_list(value, label: str, split: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.replace(",", " ").split() if split else [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{label} must be a list of strings")
    return list(value)


def validate_spec(spec: SkillSpec) -> SkillSpec:
    """Raise SpecError unless the spec respects the Agent Skills naming and size limits."""
    if not isinstance(spec.name, str) or not NAME_RE.fullmatch(spec.name):
        raise SpecError(f"invalid name {spec.name!r}: must be kebab-case (a-z, 0-9, hyphens)")
    if len(spec.name) > MAX_NAME:
        raise SpecError(f"name is {len(spec.name)} characters, the limit is {MAX_NAME}")
    if not isinstance(spec.description, str) or not spec.description.strip():
        raise SpecError("description must not be empty")
    if len(spec.description) > MAX_DESCRIPTION:
        raise SpecError(
            f"description is {len(spec.description)} characters, the limit is {MAX_DESCRIPTION}"
        )
    for label in ("triggers", "non_triggers", "behaviours", "allowed_tools"):
        value = getattr(spec, label)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SpecError(f"{label} must be a list of strings")
    if spec.license is not None and not isinstance(spec.license, str):
        raise SpecError("license must be a string")
    if not isinstance(spec.metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in spec.metadata.items()
    ):
        raise SpecError("metadata keys and values must all be strings (quote numbers)")
    if not isinstance(spec.resources, list):
        raise SpecError("resources must be a list")
    for resource in spec.resources:
        if resource not in RESOURCE_DIRS:
            raise SpecError(
                f"unknown resource directory {resource!r}: choose from {', '.join(RESOURCE_DIRS)}"
            )
    if spec.source_kind not in SOURCE_KINDS:
        raise SpecError(f"unknown source_kind {spec.source_kind!r}")
    return spec


def _words(text: str) -> list[str]:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9]+", ascii_text.lower())


def derive_name(text: str) -> str:
    """Kebab-case name from the first meaningful words of the text."""
    picked = [w for w in _words(text) if w not in _STOP_WORDS][:_NAME_WORDS]
    name = "-".join(picked)[:MAX_NAME].rstrip("-")
    return name or "new-skill"


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s for s in _SENTENCE_SPLIT_RE.split(flat) if s]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip()


def derive_description(text: str) -> str:
    """The first one or two sentences, truncated to the description limit."""
    sentences = _sentences(text)
    if not sentences:
        return ""
    chosen = sentences[0]
    if len(sentences) > 1 and len(chosen) + len(sentences[1]) < 400:
        chosen = f"{chosen} {sentences[1]}"
    return _truncate(chosen, MAX_DESCRIPTION)


def extract_phrases(text: str) -> tuple[list[str], list[str], list[str]]:
    """Return (triggers, non_triggers, behaviours) found by simple sentence keywords."""
    triggers: list[str] = []
    non_triggers: list[str] = []
    behaviours: list[str] = []
    for sentence in _sentences(text):
        phrase = _truncate(sentence.rstrip(".!?").strip(), _MAX_PHRASE_LEN)
        if not phrase:
            continue
        if _NEGATIVE_RE.search(sentence):
            targets = [non_triggers]
        else:
            targets = []
            if _TRIGGER_RE.search(sentence):
                targets.append(triggers)
            if _BEHAVIOUR_RE.search(sentence):
                targets.append(behaviours)
        for target in targets:
            if phrase not in target and len(target) < _MAX_PHRASES:
                target.append(phrase)
    return triggers, non_triggers, behaviours


def compose_description(base: str, triggers: list[str], non_triggers: list[str]) -> str:
    """The description Claude reads when deciding whether to load the skill.

    Claude sees only the frontmatter description at that point, so the "use it when" and
    "do not use it" sentences belong in it, not only in the body. The first trigger and the
    first boundary go in before any others; each sentence is kept whole and added only if
    it fits within MAX_DESCRIPTION.
    """
    ordered = [*triggers[:1], *non_triggers[:1], *triggers[1:], *non_triggers[1:]]
    text = " ".join(base.split())
    for phrase in ordered:
        phrase = " ".join(phrase.split()).rstrip(".!?")
        if not phrase or phrase in text:
            continue
        candidate = f"{text} {phrase}.".strip()
        if len(candidate) <= MAX_DESCRIPTION:
            text = candidate
    return text


def capture_paragraph(text: str, name: str | None = None) -> SkillSpec:
    """Build a validated spec from free-form paragraph text."""
    if not text or not text.strip():
        raise SpecError("the paragraph is empty")
    triggers, non_triggers, behaviours = extract_phrases(text)
    spec = SkillSpec(
        name=name if name is not None else derive_name(text),
        description=compose_description(derive_description(text), triggers, non_triggers),
        triggers=triggers,
        non_triggers=non_triggers,
        behaviours=behaviours,
        source_kind="paragraph",
    )
    return validate_spec(spec)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc


def capture_spec_file(path: str | Path, name: str | None = None) -> SkillSpec:
    """Build a validated spec from a .txt paragraph, .yaml/.yml or .json spec file."""
    path = Path(path)
    suffix = path.suffix.lower()
    text = _read_text(path)
    if suffix == ".txt":
        return capture_paragraph(text, name)
    if suffix == ".json":
        kind = "json"
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise SpecError(f"{path}: invalid JSON: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        kind = "yaml"
        try:
            data = yamlish.loads(text)
        except ValueError as exc:
            raise SpecError(f"{path}: unsupported or invalid YAML: {exc}") from exc
    else:
        raise SpecError(f"unsupported spec file type {suffix!r}: use .txt, .yaml, .yml or .json")
    if not isinstance(data, dict):
        raise SpecError(f"{path}: the spec must be a mapping at the top level")
    data = dict(data)
    data.pop("source_kind", None)
    description = data.get("description")
    if name is not None:
        data["name"] = name
    elif not data.get("name") and isinstance(description, str):
        data["name"] = derive_name(description)
    if isinstance(description, str) and not any(
        key in data for key in ("triggers", "non_triggers", "non-triggers", "behaviours")
    ):
        data["triggers"], data["non_triggers"], data["behaviours"] = extract_phrases(description)
    spec = SkillSpec.from_dict(data)
    spec.source_kind = kind
    return validate_spec(spec)


def _existing_spec_file(source: str) -> Path | None:
    if "\n" in source or "\x00" in source or len(source) > 1024:
        return None
    candidate = Path(source.strip())
    if candidate.suffix.lower() not in SPEC_SUFFIXES:
        return None
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def capture(source: str, name: str | None = None) -> SkillSpec:
    """Capture from a path to an existing .txt/.yaml/.yml/.json file, else from paragraph text."""
    path = _existing_spec_file(source)
    if path is not None:
        return capture_spec_file(path, name)
    return capture_paragraph(source, name)
