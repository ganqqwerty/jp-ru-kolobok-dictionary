from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .build_dictionary import MEDIA_SUFFIXES, _write_member
from .util import canonical_json, sha256_file


PLAIN_TEXT_PROFILE = "plain-text-v1"
PLAIN_TITLE_SUFFIX = " — простой текст"
PLAIN_DESCRIPTION = (
    "Определения преобразованы в простой текст для приложений без полной поддержки "
    "структурированного содержимого Yomitan."
)
TERM_BANK_RE = re.compile(r"^term_bank_(\d+)\.json$")
DATA_BANK_RE = re.compile(
    r"^(tag_bank|term_meta_bank|kanji_bank|kanji_meta_bank)_(\d+)\.json$"
)
HORIZONTAL_SPACE_RE = re.compile(r"[\t\f\v ]+")

ALLOWED_TAGS = {
    "a", "br", "details", "div", "img", "li", "ol", "rp", "rt", "ruby",
    "span", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
FORM_MARKERS = {
    "form-valid": "✓",
    "form-pri": "★",
    "form-rare": "редк.",
    "form-irr": "нерег.",
    "form-old": "стар.",
    "form-out": "устар.",
}


def _numbered_names(names: Sequence[str], pattern: re.Pattern[str]) -> list[str]:
    matches = [(int(pattern.fullmatch(name).group(1)), name) for name in names if pattern.fullmatch(name)]
    matches.sort()
    if matches and [number for number, _name in matches] != list(range(1, len(matches) + 1)):
        raise ValueError(f"non-contiguous archive banks for {pattern.pattern}")
    return [name for _number, name in matches]


def _data_bank_names(names: Sequence[str]) -> list[str]:
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        match = DATA_BANK_RE.fullmatch(name)
        if match:
            groups.setdefault(match.group(1), []).append((int(match.group(2)), name))
    output: list[str] = []
    for prefix in sorted(groups):
        matches = sorted(groups[prefix])
        if [number for number, _name in matches] != list(range(1, len(matches) + 1)):
            raise ValueError(f"non-contiguous archive banks for {prefix}")
        output.extend(name for _number, name in matches)
    return output


def _normalize_text(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = HORIZONTAL_SPACE_RE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _inline(value: str) -> str:
    return HORIZONTAL_SPACE_RE.sub(" ", value.replace("\r", " ").replace("\n", " ")).strip()


def _children(node: Mapping[str, Any]) -> list[Any]:
    content = node.get("content")
    if content is None:
        return []
    return content if isinstance(content, list) else [content]


def _data_value(node: Mapping[str, Any], key: str) -> str | None:
    data = node.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) else None


class PlainTextRenderer:
    """Flatten trusted Yomitan structured content into readable Unicode text."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def glossary(self, value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError("Yomitan glossary must be an array")
        output: list[Any] = []
        for item in value:
            if isinstance(item, list):
                if (
                    len(item) != 2
                    or not isinstance(item[0], str)
                    or not isinstance(item[1], list)
                    or not all(isinstance(rule, str) for rule in item[1])
                ):
                    raise ValueError("invalid deinflection glossary entry")
                output.append(item)
                self.counts["deinflection_entries_preserved"] += 1
                continue
            text = _normalize_text(self.render(item))
            if not text:
                raise ValueError("structured glossary became empty after plain-text conversion")
            output.append(text)
            self.counts["plain_glossary_items"] += 1
        if not output:
            raise ValueError("Yomitan glossary is empty")
        return output

    def render(self, node: Any) -> str:
        if node is None:
            return ""
        if isinstance(node, (str, int, float)) and not isinstance(node, bool):
            return str(node)
        if isinstance(node, list):
            return "".join(self.render(item) for item in node)
        if not isinstance(node, dict):
            raise ValueError(f"unsupported structured-content value: {type(node).__name__}")

        item_type = node.get("type")
        if item_type == "structured-content":
            self.counts["structured_glossary_items"] += 1
            return self.render(node.get("content"))
        if item_type == "text":
            text = node.get("text")
            if not isinstance(text, str):
                raise ValueError("text glossary item has no text")
            self.counts["text_glossary_items"] += 1
            return text
        if item_type == "image":
            return self._render_image(node)
        if item_type is not None:
            raise ValueError(f"unsupported glossary item type: {item_type!r}")

        tag = node.get("tag")
        if tag not in ALLOWED_TAGS:
            raise ValueError(f"unsupported structured-content tag: {tag!r}")
        self.counts[f"tag:{tag}"] += 1
        if isinstance(node.get("style"), dict):
            self.counts["styled_nodes_flattened"] += 1

        if tag == "br":
            return "\n"
        if tag == "ruby":
            return self._render_ruby(node)
        if tag == "a":
            return self._render_link(node)
        if tag == "img":
            return self._render_image(node)
        if tag == "ol":
            return self._render_list(node, ordered=True)
        if tag == "ul":
            return self._render_list(node, ordered=False)
        if tag == "table":
            return self._render_table(node)
        if tag == "span":
            return self._render_span(node)
        if tag == "div":
            return "\n" + self.render(node.get("content")) + "\n"
        if tag in {"details", "summary"}:
            self.counts["collapsible_content_expanded"] += int(tag == "details")
            return "\n" + self.render(node.get("content")) + "\n"
        if tag in {"tbody", "thead", "tfoot", "tr", "td", "th", "li", "rt", "rp"}:
            return self.render(node.get("content"))
        raise AssertionError(tag)

    def _render_span(self, node: Mapping[str, Any]) -> str:
        body = self.render(node.get("content"))
        selector = _data_value(node, "content")
        class_name = _data_value(node, "class")
        title = node.get("title")
        if title is not None and not isinstance(title, str):
            raise ValueError("structured-content title must be text")

        if class_name == "tag":
            if title:
                self.counts["tag_tooltips_shortened"] += 1
            if selector == "forms-label":
                return f"\n{body[:1].upper() + body[1:]}:\n" if body else "\nФормы:\n"
            return f" [{body}] " if body else ""
        if selector == "reference-label":
            return body.rstrip(" :") + ": "
        if class_name in {"form-special", "old-character"} and body and title:
            self.counts["special_form_tooltips_made_visible"] += 1
            return f"{body} ({title})"
        if not body and title:
            self.counts["empty_tooltips_flattened"] += 1
            return f"[{title}]"
        if title:
            self.counts["tooltips_omitted"] += 1
        return body

    def _render_ruby(self, node: Mapping[str, Any]) -> str:
        base: list[str] = []
        readings: list[str] = []
        for child in _children(node):
            if isinstance(child, dict) and child.get("tag") == "rt":
                self.counts["tag:rt"] += 1
                reading = _inline(self.render(child.get("content")))
                if reading:
                    readings.append(reading)
            elif isinstance(child, dict) and child.get("tag") == "rp":
                self.counts["ruby_fallback_parentheses_removed"] += 1
            else:
                base.append(self.render(child))
        self.counts["ruby_readings_bracketed"] += 1
        suffix = f"[{' / '.join(readings)}]" if readings else ""
        return "".join(base) + suffix

    def _render_link(self, node: Mapping[str, Any]) -> str:
        body = self.render(node.get("content"))
        href = node.get("href")
        if not isinstance(href, str):
            raise ValueError("structured-content link has no href")
        split = urlsplit(href)
        if not split.scheme and not split.netloc:
            self.counts["internal_links_unlinked"] += 1
            if body:
                return body
            query = parse_qs(split.query).get("query")
            return query[0] if query and query[0] else href
        if split.scheme in {"http", "https"}:
            self.counts["external_links_made_visible"] += 1
            return href if not body or _inline(body) == href else f"{body} <{href}>"
        self.counts["other_links_unlinked"] += 1
        return body or href

    def _render_image(self, node: Mapping[str, Any]) -> str:
        label = next(
            (
                value.strip()
                for key in ("alt", "title", "description")
                if isinstance((value := node.get(key)), str) and value.strip()
            ),
            "",
        )
        self.counts["image_placeholders"] += 1
        if label:
            self.counts["images_with_text_alternative"] += 1
            return f"[Изображение: {label}]"
        self.counts["images_without_text_alternative"] += 1
        return "[Изображение]"

    def _render_list(self, node: Mapping[str, Any], *, ordered: bool) -> str:
        items = _children(node)
        selector = _data_value(node, "content")
        if not ordered and selector == "glossary":
            values = [_inline(self._render_list_item(item)) for item in items]
            self.counts["glossary_lists_joined"] += 1
            return "; ".join(value for value in values if value)
        if not ordered and selector == "sense-groups":
            values: list[str] = []
            for item in items:
                body = _normalize_text(self._render_list_item(item))
                marker = self._explicit_list_marker(item)
                if body:
                    values.append(f"{marker} {body}" if marker else body)
            self.counts["sense_group_lists_flattened"] += 1
            return "\n".join(value for value in values if value)

        lines: list[str] = []
        for index, item in enumerate(items, 1):
            body = _normalize_text(self._render_list_item(item))
            if not body:
                continue
            marker = self._ordered_marker(item, index) if ordered else "•"
            lines.append(f"{marker} {body}")
        self.counts["ordered_lists_flattened" if ordered else "unordered_lists_flattened"] += 1
        return "\n" + "\n".join(lines) + "\n"

    def _render_list_item(self, item: Any) -> str:
        if isinstance(item, dict) and item.get("tag") == "li":
            self.counts["tag:li"] += 1
            return self.render(item.get("content"))
        return self.render(item)

    def _ordered_marker(self, item: Any, index: int) -> str:
        return self._explicit_list_marker(item) or f"{index}."

    def _explicit_list_marker(self, item: Any) -> str | None:
        if isinstance(item, dict):
            style = item.get("style")
            raw = style.get("listStyleType") if isinstance(style, dict) else None
            if isinstance(raw, str) and raw:
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                    raw = raw[1:-1]
                if raw:
                    self.counts["css_list_markers_preserved"] += 1
                    return raw
        return None

    def _render_table(self, node: Mapping[str, Any]) -> str:
        rows = self._table_rows(node.get("content"))
        pending_row_spans: dict[int, int] = {}
        output: list[str] = []
        for row in rows:
            cells = [child for child in _children(row) if isinstance(child, dict) and child.get("tag") in {"td", "th"}]
            rendered: list[str] = []
            column = 0
            for cell in cells:
                while pending_row_spans.get(column, 0):
                    rendered.append("")
                    pending_row_spans[column] -= 1
                    if pending_row_spans[column] == 0:
                        del pending_row_spans[column]
                    column += 1
                body = _inline(self.render(cell.get("content")))
                class_name = _data_value(cell, "class")
                if class_name in FORM_MARKERS:
                    body = FORM_MARKERS[class_name]
                    self.counts[f"form_marker:{class_name}"] += 1
                column_span = cell.get("colSpan", 1)
                row_span = cell.get("rowSpan", 1)
                if not isinstance(column_span, int) or column_span < 1:
                    raise ValueError("invalid table column span")
                if not isinstance(row_span, int) or row_span < 1:
                    raise ValueError("invalid table row span")
                rendered.append(body)
                rendered.extend("" for _index in range(column_span - 1))
                if column_span > 1:
                    self.counts["table_column_spans_expanded"] += 1
                if row_span > 1:
                    self.counts["table_row_spans_expanded"] += 1
                    for offset in range(column_span):
                        pending_row_spans[column + offset] = row_span - 1
                column += column_span
            while pending_row_spans.get(column, 0):
                rendered.append("")
                pending_row_spans[column] -= 1
                if pending_row_spans[column] == 0:
                    del pending_row_spans[column]
                column += 1
            output.append(" │ ".join(rendered).rstrip())
        self.counts["tables_flattened"] += 1
        return "\n" + "\n".join(output) + "\n"

    def _table_rows(self, node: Any) -> list[Mapping[str, Any]]:
        output: list[Mapping[str, Any]] = []
        values = node if isinstance(node, list) else [node]
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("table contains a non-element child")
            tag = value.get("tag")
            if tag == "tr":
                output.append(value)
            elif tag in {"thead", "tbody", "tfoot"}:
                output.extend(self._table_rows(value.get("content")))
            else:
                raise ValueError(f"table contains unsupported child tag: {tag!r}")
        return output


def plain_index(source: Mapping[str, Any]) -> dict[str, Any]:
    title = source.get("title")
    revision = source.get("revision")
    if not isinstance(title, str) or not title:
        raise ValueError("source Yomitan index has no title")
    if not isinstance(revision, str) or not revision:
        raise ValueError("source Yomitan index has no revision")
    index = dict(source)
    if not title.endswith(PLAIN_TITLE_SUFFIX):
        index["title"] = title + PLAIN_TITLE_SUFFIX
    if not revision.endswith(f"-{PLAIN_TEXT_PROFILE}"):
        index["revision"] = revision + f"-{PLAIN_TEXT_PROFILE}"
    description = str(source.get("description", "")).strip()
    if PLAIN_DESCRIPTION not in description:
        index["description"] = f"{description} {PLAIN_DESCRIPTION}".strip()
    for key in ("isUpdatable", "indexUrl", "downloadUrl"):
        index.pop(key, None)
    return index


def convert_yomitan_to_plain(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("plain-text output must differ from the rich source archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    renderer = PlainTextRenderer()
    article_count = 0
    media_omitted = 0
    styles_omitted = 0
    copied_banks = 0

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(source) as source_archive, zipfile.ZipFile(temporary, "w") as target_archive:
            names = source_archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("source archive contains duplicate members")
            if "index.json" not in names:
                raise ValueError("source archive has no index.json")
            term_names = _numbered_names(names, TERM_BANK_RE)
            if not term_names:
                raise ValueError("source archive has no term banks")
            data_names = _data_bank_names(names)
            recognized = {"index.json", "styles.css", *term_names, *data_names}
            for name in names:
                if name in recognized or name.endswith("/"):
                    continue
                if Path(name).suffix.lower() in MEDIA_SUFFIXES:
                    media_omitted += 1
                else:
                    raise ValueError(f"unsupported source archive member: {name}")
            styles_omitted = int("styles.css" in names)

            index = plain_index(json.loads(source_archive.read("index.json")))
            _write_member(target_archive, "index.json", canonical_json(index))
            for name in data_names:
                _write_member(target_archive, name, source_archive.read(name))
                copied_banks += 1
            for name in term_names:
                rows = json.loads(source_archive.read(name))
                if not isinstance(rows, list):
                    raise ValueError(f"{name} is not an array")
                transformed: list[list[Any]] = []
                for row in rows:
                    if not isinstance(row, list) or len(row) != 8:
                        raise ValueError(f"invalid term row in {name}")
                    plain_row = list(row)
                    plain_row[5] = renderer.glossary(row[5])
                    transformed.append(plain_row)
                article_count += len(transformed)
                _write_member(target_archive, name, canonical_json(transformed))
        os.replace(temporary, output)
        os.chmod(output, 0o644)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "profile": PLAIN_TEXT_PROFILE,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "output": str(output),
        "zip_sha256": sha256_file(output),
        "bytes": output.stat().st_size,
        "articles": article_count,
        "data_banks_copied": copied_banks,
        "media_files_omitted": media_omitted,
        "stylesheets_omitted": styles_omitted,
        "transform_counts": dict(sorted(renderer.counts.items())),
    }


def verify_plain_yomitan(path: Path, *, source: Path | None = None) -> dict[str, Any]:
    path = path.resolve()
    article_count = 0
    glossary_items = 0
    deinflection_items = 0
    characters = 0
    lines = 0
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or names[0] != "index.json":
            raise ValueError("index.json is not the first archive member")
        if len(names) != len(set(names)):
            raise ValueError("plain archive contains duplicate members")
        unsupported = [name for name in names if name != "index.json" and not TERM_BANK_RE.fullmatch(name) and not DATA_BANK_RE.fullmatch(name)]
        if unsupported:
            raise ValueError(f"plain archive contains non-data members: {unsupported[:3]}")
        term_names = _numbered_names(names, TERM_BANK_RE)
        if not term_names:
            raise ValueError("plain archive has no term banks")
        index = json.loads(archive.read("index.json"))
        if not str(index.get("title", "")).endswith(PLAIN_TITLE_SUFFIX):
            raise ValueError("plain archive title lacks the plain-text suffix")
        if not str(index.get("revision", "")).endswith(f"-{PLAIN_TEXT_PROFILE}"):
            raise ValueError("plain archive revision lacks the conversion profile")
        if {"isUpdatable", "indexUrl", "downloadUrl"} & set(index):
            raise ValueError("plain archive must not update itself with the rich release channel")
        for name in term_names:
            rows = json.loads(archive.read(name))
            if not isinstance(rows, list):
                raise ValueError(f"{name} is not an array")
            for row in rows:
                if not isinstance(row, list) or len(row) != 8:
                    raise ValueError(f"invalid term row in {name}")
                glossary = row[5]
                if not isinstance(glossary, list) or not glossary:
                    raise ValueError(f"plain glossary is empty or invalid in {name}")
                for item in glossary:
                    if isinstance(item, list):
                        if (
                            len(item) != 2
                            or not isinstance(item[0], str)
                            or not isinstance(item[1], list)
                            or not all(isinstance(rule, str) for rule in item[1])
                        ):
                            raise ValueError(f"plain glossary contains an invalid deinflection in {name}")
                        deinflection_items += 1
                        continue
                    if not isinstance(item, str) or not item or "\x00" in item:
                        raise ValueError(f"plain glossary contains a non-text value in {name}")
                    glossary_items += 1
                    characters += len(item)
                    lines += item.count("\n") + 1
            article_count += len(rows)

        source_verified = False
        source_sha256: str | None = None
        if source is not None:
            source = source.resolve()
            source_sha256 = sha256_file(source)
            with zipfile.ZipFile(source) as rich_archive:
                rich_names = rich_archive.namelist()
                rich_term_names = _numbered_names(rich_names, TERM_BANK_RE)
                if rich_term_names != term_names:
                    raise ValueError("plain and rich archives have different term banks")
                expected_index = plain_index(json.loads(rich_archive.read("index.json")))
                if index != expected_index:
                    raise ValueError("plain archive metadata does not match its rich source")
                data_names = _data_bank_names(names)
                rich_data_names = _data_bank_names(rich_names)
                if data_names != rich_data_names:
                    raise ValueError("plain and rich archives have different copied data banks")
                for name in data_names:
                    if name not in rich_names or archive.read(name) != rich_archive.read(name):
                        raise ValueError(f"plain archive changed copied data bank {name}")
                for name in term_names:
                    plain_rows = json.loads(archive.read(name))
                    rich_rows = json.loads(rich_archive.read(name))
                    if len(plain_rows) != len(rich_rows):
                        raise ValueError(f"plain archive changed row count in {name}")
                    for plain_row, rich_row in zip(plain_rows, rich_rows, strict=True):
                        if plain_row[:5] != rich_row[:5] or plain_row[6:] != rich_row[6:]:
                            raise ValueError(f"plain archive changed term metadata in {name}")
                        plain_redirects = [item for item in plain_row[5] if isinstance(item, list)]
                        rich_redirects = [item for item in rich_row[5] if isinstance(item, list)]
                        if plain_redirects != rich_redirects:
                            raise ValueError(f"plain archive changed deinflection metadata in {name}")
                source_verified = True

    return {
        "verified": True,
        "profile": PLAIN_TEXT_PROFILE,
        "articles": article_count,
        "plain_glossary_items": glossary_items,
        "deinflection_items": deinflection_items,
        "definition_characters": characters,
        "definition_lines": lines,
        "files": len(names),
        "zip_sha256": sha256_file(path),
        "source_verified": source_verified,
        "source_sha256": source_sha256,
    }
