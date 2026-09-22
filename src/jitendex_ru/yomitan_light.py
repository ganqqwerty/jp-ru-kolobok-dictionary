from __future__ import annotations

import json
import os
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from .build_dictionary import _write_member
from .util import canonical_json, sha256_file
from .yomitan_plain import PlainTextRenderer, TERM_BANK_RE, _numbered_names, _normalize_text


PROFILE = "light-v1"
TITLE_SUFFIX = " — лёгкий"


def light_index(source: dict[str, Any]) -> dict[str, Any]:
    index = dict(source)
    index["title"] = str(source["title"]) + TITLE_SUFFIX
    index["revision"] = str(source["revision"]) + "-" + PROFILE
    index["description"] = (
        "Колобок 400k: формы слов, переходы и определения без примеров, "
        "таблиц и оформления. CC BY-SA 4.0."
    )
    index["attribution"] = (
        "Jitendex © Stephen Kraus 2023–2026, CC BY-SA 4.0. "
        "JMdict © Electronic Dictionary Research and Development Group. "
        "Tatoeba © участники, CC BY 2.0 FR. Русская редакция: Юрий Катков."
    )
    for key in ("isUpdatable", "indexUrl", "downloadUrl", "url"):
        index.pop(key, None)
    return index


def _children(node: Any) -> list[Any]:
    content = node.get("content") if isinstance(node, dict) else node
    if content is None:
        return []
    return content if isinstance(content, list) else [content]


class LightRenderer(PlainTextRenderer):
    def _render_link(self, node: dict[str, Any]) -> str:
        return self.render(node.get("content"))


def _selected(node: Any, renderer: LightRenderer, counts: Counter[str]) -> list[str]:
    if isinstance(node, list):
        output: list[str] = []
        for child in node:
            output.extend(_selected(child, renderer, counts))
        return output
    if not isinstance(node, dict):
        return []
    data = node.get("data")
    kind = data.get("content") if isinstance(data, dict) else None
    if kind in {"glossary", "redirect-glossary", "info-gloss-content"}:
        value = _normalize_text(renderer.render(node))
        if value:
            counts[kind] += 1
            return [value]
        return []
    if kind == "forms":
        forms: list[str] = []
        seen: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("tag") in {"th", "li"} and value.get("content") is not None:
                    form = _normalize_text(renderer.render(value.get("content")))
                    if form and form not in {"〃", "＊", "*"} and form not in seen:
                        seen.add(form)
                        forms.append(form)
                else:
                    for child in _children(value):
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        for child in _children(node):
            if isinstance(child, dict) and child.get("tag") in {"table", "ul"}:
                collect(child)
        if forms:
            counts["forms"] += 1
            return ["Формы: " + "; ".join(forms)]
        return []
    return _selected(_children(node), renderer, counts)


def light_glossary(value: Any, renderer: LightRenderer, counts: Counter[str]) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("term glossary must be an array")
    result: list[Any] = []
    for item in value:
        if isinstance(item, list):
            if len(item) != 2 or not isinstance(item[0], str) or not isinstance(item[1], list):
                raise ValueError("invalid deinflection entry")
            result.append(item)
        elif isinstance(item, dict):
            result.extend(_selected(item, renderer, counts))
        elif isinstance(item, str):
            result.append(item)
        else:
            raise ValueError("unsupported glossary entry")
    if not result:
        raise ValueError("light glossary became empty")
    return result


def convert_yomitan_to_light(source: Path, output: Path) -> dict[str, Any]:
    source, output = source.resolve(), output.resolve()
    if source == output:
        raise ValueError("light output must differ from source")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    counts: Counter[str] = Counter()
    renderer = LightRenderer()
    articles = 0
    try:
        with zipfile.ZipFile(source) as rich, zipfile.ZipFile(temporary, "w") as light:
            names = rich.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive members")
            banks = _numbered_names(names, TERM_BANK_RE)
            if not banks:
                raise ValueError("source has no term banks")
            _write_member(light, "index.json", canonical_json(light_index(json.loads(rich.read("index.json")))))
            if "tag_bank_1.json" in names:
                _write_member(light, "tag_bank_1.json", rich.read("tag_bank_1.json"))
            for bank in banks:
                rows = json.loads(rich.read(bank))
                output_rows = []
                for row in rows:
                    if not isinstance(row, list) or len(row) != 8:
                        raise ValueError(f"invalid term row in {bank}")
                    changed = list(row)
                    changed[5] = light_glossary(row[5], renderer, counts)
                    output_rows.append(changed)
                articles += len(rows)
                _write_member(light, bank, canonical_json(output_rows))
        os.replace(temporary, output)
        os.chmod(output, 0o644)
    finally:
        temporary.unlink(missing_ok=True)
    return {"articles": articles, "bytes": output.stat().st_size,
            "sha256": sha256_file(output), "sections": dict(counts)}


def verify_light_yomitan(path: Path, *, source: Path | None = None) -> dict[str, Any]:
    articles = 0
    sections: Counter[str] = Counter()
    with zipfile.ZipFile(path) as light:
        names = light.namelist()
        if len(names) != len(set(names)) or not names or names[0] != "index.json":
            raise ValueError("invalid light archive members")
        banks = _numbered_names(names, TERM_BANK_RE)
        if not banks or set(names) - {"index.json", "tag_bank_1.json", *banks}:
            raise ValueError("unexpected light archive members")
        index = json.loads(light.read("index.json"))
        if not index.get("title", "").endswith(TITLE_SUFFIX):
            raise ValueError("light title is missing")
        if {"isUpdatable", "indexUrl", "downloadUrl", "url"} & set(index):
            raise ValueError("light archive points to another update channel")
        if "http://" in json.dumps(index) or "https://" in json.dumps(index):
            raise ValueError("light archive metadata contains a link")
        rich = zipfile.ZipFile(source) if source else None
        try:
            if rich:
                if banks != _numbered_names(rich.namelist(), TERM_BANK_RE):
                    raise ValueError("term banks differ from source")
                if index != light_index(json.loads(rich.read("index.json"))):
                    raise ValueError("light index differs from source profile")
            for bank in banks:
                rows = json.loads(light.read(bank))
                source_rows = json.loads(rich.read(bank)) if rich else None
                if source_rows is not None and len(rows) != len(source_rows):
                    raise ValueError(f"row count differs in {bank}")
                for offset, row in enumerate(rows):
                    if not isinstance(row, list) or len(row) != 8 or not isinstance(row[5], list) or not row[5]:
                        raise ValueError(f"invalid light row in {bank}")
                    if source_rows is not None:
                        original = source_rows[offset]
                        if row[:5] != original[:5] or row[6:] != original[6:]:
                            raise ValueError(f"term metadata differs in {bank}")
                        if [x for x in row[5] if isinstance(x, list)] != [x for x in original[5] if isinstance(x, list)]:
                            raise ValueError(f"redirect metadata differs in {bank}")
                    for item in row[5]:
                        if isinstance(item, list):
                            sections["redirect_metadata"] += 1
                        elif isinstance(item, str) and item and "\x00" not in item:
                            if "http://" in item or "https://" in item:
                                raise ValueError(f"link remained in {bank}")
                            sections["text"] += 1
                        else:
                            raise ValueError(f"rich or empty glossary item in {bank}")
                    articles += 1
            if rich and "tag_bank_1.json" in names and light.read("tag_bank_1.json") != rich.read("tag_bank_1.json"):
                raise ValueError("tag bank differs from source")
        finally:
            if rich:
                rich.close()
    return {"verified": True, "articles": articles, "sha256": sha256_file(path),
            "sections": dict(sections), "source_verified": source is not None}
