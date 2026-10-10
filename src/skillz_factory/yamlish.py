"""A small YAML subset reader and writer (standard library only).

Supported: top-level mappings whose values are scalars (str, int, bool, null), lists of
scalars (block ``- item`` form or flow ``[a, b]``) or one level of nested mappings with
scalar values. Strings may be plain, 'single' or "double" quoted. Plain numbers with a
fraction or exponent are read as strings. Anything else (anchors, tags, block scalars,
nested lists or mappings, mappings inside lists, tabs) raises ``ValueError``.
"""

# ruff: noqa: TRY004  (the contract is ValueError for every unsupported input)
from __future__ import annotations

import json
import re

__all__ = ["dumps", "frontmatter_dumps", "loads"]

_INT_RE = re.compile(r"^[-+]?\d+$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_RESERVED = {"true", "false", "null", "~", "yes", "no", "on", "off", "y", "n"}
_SPECIAL_START = set("-?:,[]{}#&*!|>'\"%@`")
_OPENERS = " \t[,"


def _is_opening_quote(text: str, i: int) -> bool:
    """A quote only opens a quoted string at the start of a token."""
    return text[i] in "'\"" and (i == 0 or text[i - 1] in _OPENERS)


def _strip_comment(line: str) -> str:
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif _is_opening_quote(line, i):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _logical_lines(text: str) -> list[tuple[int, int, str]]:
    """Return (line number, indent, content) for each non-blank, non-comment line."""
    out: list[tuple[int, int, str]] = []
    opened = False
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, raw in enumerate(raw_lines, start=1):
        if number == 1 and raw.startswith("﻿"):
            raw = raw[1:]
        body = _strip_comment(raw)
        if not body.strip():
            continue
        if "\t" in body[: len(body) - len(body.lstrip())]:
            raise ValueError(f"line {number}: tabs are not allowed for indentation")
        if body.strip() in ("---", "..."):
            if not out and not opened and body.strip() == "---":
                opened = True
                continue
            if opened and not any(r.strip() for r in raw_lines[number:]):
                break  # closing marker of a frontmatter block
            raise ValueError(f"line {number}: multiple documents are not supported")
        out.append((number, len(body) - len(body.lstrip(" ")), body.strip()))
    return out


def _parse_scalar(token: str, number: int):
    token = token.strip()
    if token.startswith('"'):
        try:
            value, end = json.JSONDecoder().raw_decode(token)
        except ValueError as exc:
            raise ValueError(f"line {number}: bad double-quoted string: {token}") from exc
        if end != len(token) or not isinstance(value, str):
            raise ValueError(f"line {number}: unexpected text after quoted string: {token}")
        return value
    if token.startswith("'"):
        if len(token) < 2 or not token.endswith("'") or re.search(r"[^']'[^']", token[1:-1]):
            raise ValueError(f"line {number}: bad single-quoted string: {token}")
        return token[1:-1].replace("''", "'")
    if token and token[0] in _SPECIAL_START:
        raise ValueError(f"line {number}: unsupported YAML syntax: {token}")
    if ": " in token or token.endswith(":"):
        raise ValueError(f"line {number}: nested mapping in this position is not supported")
    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "~") or token == "":
        return None
    if _INT_RE.match(token):
        return int(token)
    return token


def _split_flow(inner: str, number: int) -> list[str]:
    items: list[str] = []
    quote = ""
    start = 0
    i = 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif _is_opening_quote(inner, i) or (i == 0 and ch in "'\""):
            quote = ch
        elif ch in "[{":
            raise ValueError(f"line {number}: nested flow collections are not supported")
        elif ch == ",":
            items.append(inner[start:i])
            start = i + 1
        i += 1
    if quote:
        raise ValueError(f"line {number}: unterminated quoted string in flow list")
    items.append(inner[start:])
    return items


def _parse_inline(token: str, number: int):
    token = token.strip()
    if token == "{}":
        return {}
    if token.startswith("["):
        if not token.endswith("]"):
            raise ValueError(f"line {number}: unterminated flow list: {token}")
        inner = token[1:-1]
        if not inner.strip():
            return []
        parts = _split_flow(inner, number)
        if parts and not parts[-1].strip():
            parts.pop()
        return [_parse_scalar(part, number) for part in parts]
    if token.startswith("{"):
        raise ValueError(f"line {number}: flow mappings are not supported: {token}")
    return _parse_scalar(token, number)


def _split_key(content: str, number: int) -> tuple[str, str]:
    if content[0] == '"':
        try:
            key, end = json.JSONDecoder().raw_decode(content)
        except ValueError as exc:
            raise ValueError(f"line {number}: bad quoted key: {content}") from exc
    elif content[0] == "'":
        end = content.find("'", 1)
        if end == -1:
            raise ValueError(f"line {number}: bad quoted key: {content}")
        key, end = content[1:end], end + 1
    else:
        match = re.match(r"^(.*?):(?:\s+(.*))?$", content)
        if not match or not match.group(1).strip():
            raise ValueError(f"line {number}: expected 'key: value', got: {content}")
        return match.group(1).strip(), (match.group(2) or "").strip()
    rest = content[end:]
    if not rest.startswith(":"):
        raise ValueError(f"line {number}: expected ':' after key: {content}")
    return key, rest[1:].strip()


def _parse_list(lines, i: int, indent: int):
    items = []
    while i < len(lines) and lines[i][1] == indent and (
        lines[i][2] == "-" or lines[i][2].startswith("- ")
    ):
        number, _, content = lines[i]
        items.append(_parse_scalar(content[1:].strip(), number))
        i += 1
    return items, i


def _parse_map(lines, i: int, indent: int, depth: int):
    result: dict = {}
    while i < len(lines) and lines[i][1] >= indent:
        number, line_indent, content = lines[i]
        if line_indent != indent:
            raise ValueError(f"line {number}: unexpected indentation")
        if content == "-" or content.startswith("- "):
            raise ValueError(f"line {number}: unexpected list item")
        key, rest = _split_key(content, number)
        if key in result:
            raise ValueError(f"line {number}: duplicate key {key!r}")
        i += 1
        if rest:
            if rest[0] in "|>":
                raise ValueError(f"line {number}: block scalars are not supported")
            result[key] = _parse_inline(rest, number)
            continue
        nxt = lines[i] if i < len(lines) else None
        is_item = nxt is not None and (nxt[2] == "-" or nxt[2].startswith("- "))
        if nxt is not None and is_item and nxt[1] >= indent:
            result[key], i = _parse_list(lines, i, nxt[1])
        elif nxt is not None and nxt[1] > indent:
            if depth >= 1:
                raise ValueError(f"line {nxt[0]}: only one level of nested mappings is supported")
            result[key], i = _parse_map(lines, i, nxt[1], depth + 1)
            for sub_key, value in result[key].items():
                if isinstance(value, (list, dict)):
                    raise ValueError(f"line {nxt[0]}: {sub_key!r}: nested values must be scalars")
        else:
            result[key] = None
    return result, i


def loads(text: str) -> dict:
    """Parse a YAML-subset document into a dict."""
    lines = _logical_lines(text)
    if not lines:
        return {}
    result, i = _parse_map(lines, 0, lines[0][1], 0)
    if i < len(lines):
        raise ValueError(f"line {lines[i][0]}: unexpected indentation")
    return result


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _scalar_dump(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise ValueError(f"unsupported value type {type(value).__name__}: {value!r}")
    if _needs_quotes(value):
        return _quote(value)
    return value


def _needs_quotes(text: str) -> bool:
    if not text or text != text.strip() or text[0] in _SPECIAL_START:
        return True
    if any(ord(ch) < 0x20 or ord(ch) in (0x7F, 0x2028, 0x2029) for ch in text):
        return True
    if ": " in text or text.endswith(":") or " #" in text:
        return True
    if text.lower() in _RESERVED:
        return True
    try:
        float(text)
    except ValueError:
        return False
    return True


def _key_dump(key) -> str:
    if not isinstance(key, str):
        raise ValueError(f"mapping keys must be strings, got {key!r}")
    if _KEY_RE.match(key) and key.lower() not in _RESERVED:
        return key
    return _quote(key)


def dumps(obj: dict) -> str:
    """Serialise a dict (scalars, lists of scalars, one nested mapping level) to YAML."""
    if not isinstance(obj, dict):
        raise ValueError("dumps() needs a mapping at the top level")
    out: list[str] = []
    for key, value in obj.items():
        head = _key_dump(key)
        if isinstance(value, dict):
            if not value:
                out.append(f"{head}: {{}}")
                continue
            out.append(f"{head}:")
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (dict, list, tuple)):
                    raise ValueError(f"{key}.{sub_key}: only one level of nesting is supported")
                out.append(f"  {_key_dump(sub_key)}: {_scalar_dump(sub_value)}")
        elif isinstance(value, (list, tuple)):
            if not value:
                out.append(f"{head}: []")
                continue
            out.append(f"{head}:")
            for item in value:
                if isinstance(item, (dict, list, tuple)):
                    raise ValueError(f"{key}: list items must be scalars")
                out.append(f"  - {_scalar_dump(item)}")
        else:
            out.append(f"{head}: {_scalar_dump(value)}")
    return "".join(f"{line}\n" for line in out)


def frontmatter_dumps(mapping: dict) -> str:
    """Return a ``---`` delimited frontmatter block for the mapping."""
    return f"---\n{dumps(mapping)}---\n"
