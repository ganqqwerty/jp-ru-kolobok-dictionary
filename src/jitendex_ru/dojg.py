from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


DOJG_ROLE = "dojg_text"
DOJG_BASE_POINTER = "/5/0"
DOJG_POINTER_RE = re.compile(
    r"^(?P<base>/[^#]+)#dojg=(?P<start>\d{8}):(?P<end>\d{8})$"
)
DOJG_JAPANESE_BLOCK_RE = re.compile(
    r"[\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff01-\uff60]+"
)
DOJG_JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
DOJG_PLACEHOLDER_RE = re.compile(r"⟦J\d{4}⟧")
DOJG_ENGLISH_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’.-]*")
DOJG_CELL_RE = re.compile(r"[^|\n]+")
DOJG_LEADING_LABEL_RE = re.compile(
    r"^\s*(\((?:ks[a-z0-9]*|[a-z]|[ivx]+|\d+)\)\.?)",
    re.IGNORECASE,
)
DOJG_DIALOGUE_LABEL_RE = re.compile(r"(?<![A-Za-z])[AB]:")
DOJG_SECTION_MARKERS = ("[解説]", "[意味]", "[例文A]", "[例文B]", "[接続]")
DOJG_NOTATION_WORDS = frozenset({
    "a", "b", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "ks", "ksa", "ksb", "ksc", "ksd", "kse", "ksf", "ksg", "ksh", "ksi",
    "prt", "sinformal", "vconditional", "vinformal", "vneg", "vnegative", "vstem",
})


@dataclass(frozen=True)
class DojgSegment:
    pointer: str
    source_text: str
    protected_tokens: tuple[str, ...]
    start: int
    end: int
    original_text: str


def mask_dojg_japanese(value: str) -> tuple[str, tuple[str, ...]]:
    protected: list[str] = []

    def replace(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"⟦J{len(protected):04d}⟧"

    return DOJG_JAPANESE_BLOCK_RE.sub(replace, value), tuple(protected)


def restore_dojg_japanese(value: str, protected: tuple[str, ...]) -> str:
    expected = [f"⟦J{index:04d}⟧" for index in range(1, len(protected) + 1)]
    actual = DOJG_PLACEHOLDER_RE.findall(value)
    if actual != expected:
        raise ValueError("DOJG Japanese placeholders changed")
    output = value
    for placeholder, original in zip(expected, protected, strict=True):
        output = output.replace(placeholder, original)
    return output


def make_dojg_pointer(start: int, end: int, base: str = DOJG_BASE_POINTER) -> str:
    if start < 0 or end <= start or end > 99_999_999:
        raise ValueError("invalid DOJG text span")
    return f"{base}#dojg={start:08d}:{end:08d}"


def parse_dojg_pointer(pointer: str) -> tuple[str, int, int] | None:
    match = DOJG_POINTER_RE.fullmatch(pointer)
    if match is None:
        return None
    return match.group("base"), int(match.group("start")), int(match.group("end"))


def translation_pointer_base(pointer: str) -> str:
    parsed = parse_dojg_pointer(pointer)
    return parsed[0] if parsed is not None else pointer


def _notation_word(word: str) -> bool:
    normalized = word.rstrip(".").lower()
    return normalized in DOJG_NOTATION_WORDS or re.fullmatch(r"ks[a-z]?\d*", normalized) is not None


def is_dojg_translatable(source_text: str) -> bool:
    words = DOJG_ENGLISH_WORD_RE.findall(DOJG_PLACEHOLDER_RE.sub(" ", source_text))
    return any(
        len(word.rstrip(".")) >= 2 and not word.rstrip(".").isupper() and not _notation_word(word)
        for word in words
    )


def dojg_protected_tokens(source_text: str) -> tuple[str, ...]:
    tokens = list(DOJG_PLACEHOLDER_RE.findall(source_text))
    if match := DOJG_LEADING_LABEL_RE.match(source_text):
        tokens.append(match.group(1))
    tokens.extend(DOJG_DIALOGUE_LABEL_RE.findall(source_text))
    return tuple(dict.fromkeys(tokens))


def extract_dojg_segments(row: list[Any]) -> list[DojgSegment]:
    if len(row) < 8 or not isinstance(row[5], list) or not row[5] or not isinstance(row[5][0], str):
        return []
    text = row[5][0]
    segments: list[DojgSegment] = []
    for match in DOJG_CELL_RE.finditer(text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = match.start() + leading
        end = match.end() - trailing
        if start >= end:
            continue
        original = text[start:end]
        source, _protected_japanese = mask_dojg_japanese(original)
        if not is_dojg_translatable(source):
            continue
        segments.append(DojgSegment(
            pointer=make_dojg_pointer(start, end),
            source_text=source,
            protected_tokens=dojg_protected_tokens(source),
            start=start,
            end=end,
            original_text=original,
        ))
    return segments


def dojg_segment_context(row: list[Any], pointer: str) -> dict[str, str]:
    parsed = parse_dojg_pointer(pointer)
    if parsed is None:
        raise ValueError(f"not a DOJG pointer: {pointer}")
    base, start, end = parsed
    if base != DOJG_BASE_POINTER:
        raise ValueError(f"unsupported DOJG pointer base: {base}")
    text = row[5][0]
    original = text[start:end]
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    section = "entry"
    last_position = -1
    for marker in DOJG_SECTION_MARKERS:
        position = text.rfind(marker, 0, start)
        if position > last_position:
            section = marker
            last_position = position
    return {
        "dictionary": "Dictionary of Japanese Grammar",
        "volume": str(row[7]),
        "section": section,
        "original_cell": original,
        "original_line": text[line_start:line_end].strip(),
        "instruction": "Translate only the English scaffold and keep every ⟦J0000⟧ placeholder unchanged.",
    }


def rebuild_dojg_segment(original: str, source_text: str, target_text: str) -> str:
    actual_source, protected = mask_dojg_japanese(original)
    if actual_source != source_text:
        raise ValueError("DOJG source segment changed")
    return restore_dojg_japanese(target_text, protected)


def japanese_text_signature(value: str) -> str:
    return "".join(DOJG_JAPANESE_BLOCK_RE.findall(value))
