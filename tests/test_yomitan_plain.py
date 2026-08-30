from __future__ import annotations

import json
import zipfile

import pytest

from jitendex_ru.yomitan_plain import (
    PLAIN_DESCRIPTION,
    PLAIN_TEXT_PROFILE,
    PLAIN_TITLE_SUFFIX,
    PlainTextRenderer,
    convert_yomitan_to_plain,
    verify_plain_yomitan,
)


def _rich_glossary() -> list[dict[str, object]]:
    return [{
        "type": "structured-content",
        "content": {
            "tag": "div",
            "content": [
                {
                    "tag": "span",
                    "data": {"class": "tag", "content": "part-of-speech-info"},
                    "title": "Нарицательное существительное",
                    "content": "сущ.",
                },
                {
                    "tag": "ol",
                    "content": [{
                        "tag": "li",
                        "style": {"listStyleType": "\"①\""},
                        "content": [
                            {
                                "tag": "ul",
                                "data": {"content": "glossary"},
                                "content": [
                                    {"tag": "li", "content": "кот"},
                                    {"tag": "li", "content": "кошка"},
                                ],
                            },
                            {
                                "tag": "div",
                                "data": {"content": "xref-content"},
                                "content": [
                                    {
                                        "tag": "span",
                                        "data": {"content": "reference-label"},
                                        "content": "См. также",
                                    },
                                    {
                                        "tag": "a",
                                        "href": "?query=%E7%8C%AB",
                                        "content": {
                                            "tag": "ruby",
                                            "content": [
                                                "猫",
                                                {"tag": "rt", "content": "ねこ"},
                                            ],
                                        },
                                    },
                                ],
                            },
                        ],
                    }],
                },
                {
                    "tag": "span",
                    "data": {"class": "tag", "content": "forms-label"},
                    "title": "Варианты написания и чтения",
                    "content": "формы",
                },
                {
                    "tag": "table",
                    "content": [
                        {
                            "tag": "tr",
                            "content": [
                                {"tag": "th", "content": "форма"},
                                {"tag": "th", "content": "обычная"},
                                {"tag": "th", "content": "редкая"},
                            ],
                        },
                        {
                            "tag": "tr",
                            "content": [
                                {"tag": "th", "content": "猫"},
                                {
                                    "tag": "td",
                                    "data": {"class": "form-valid"},
                                    "content": {"tag": "span", "title": "допустимое сочетание"},
                                },
                                {
                                    "tag": "td",
                                    "data": {"class": "form-rare"},
                                    "content": {"tag": "span", "title": "редко употребляемая форма"},
                                },
                            ],
                        },
                    ],
                },
                {
                    "tag": "div",
                    "data": {"content": "graphic"},
                    "content": {"tag": "img", "path": "media/cat.avif", "alt": "кот"},
                },
                {
                    "tag": "div",
                    "data": {"content": "attribution"},
                    "content": {
                        "tag": "a",
                        "href": "https://example.test/source",
                        "content": "Источник",
                    },
                },
            ],
        },
    }]


def _write_rich_archive(path) -> bytes:
    index = {
        "title": "Колобок 400k",
        "revision": "2026.08.21.0-jp-ru-kolobok-400k-v1.0.1-tags-ru-v1",
        "format": 3,
        "sequenced": True,
        "description": "Русский словарь.",
        "isUpdatable": True,
        "indexUrl": "https://example.test/index.json",
        "downloadUrl": "https://example.test/rich.zip",
    }
    tag_bank = json.dumps([["n", "default", 0, "Существительное", 0]], ensure_ascii=False).encode()
    glossary = [*_rich_glossary(), ["ねこ", ["вариант написания: 猫"]]]
    row = ["猫", "ねこ", "n", "", 5, glossary, 123, ""]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps(index, ensure_ascii=False))
        archive.writestr("styles.css", ".tag { color: red; }")
        archive.writestr("tag_bank_1.json", tag_bank)
        archive.writestr("media/cat.avif", b"not-needed-by-plain-output")
        archive.writestr("term_bank_1.json", json.dumps([row], ensure_ascii=False))
    return tag_bank


def test_plain_renderer_preserves_readable_substitutes():
    renderer = PlainTextRenderer()

    text = renderer.glossary(_rich_glossary())[0]

    assert "[сущ.]" in text
    assert "① кот; кошка" in text
    assert "См. также: 猫[ねこ]" in text
    assert "Формы:" in text
    assert "猫 │ ✓ │ редк." in text
    assert "[Изображение: кот]" in text
    assert "Источник <https://example.test/source>" in text
    assert renderer.counts["css_list_markers_preserved"] == 1
    assert renderer.counts["tables_flattened"] == 1
    assert renderer.counts["image_placeholders"] == 1


def test_plain_renderer_rejects_unknown_structured_content():
    renderer = PlainTextRenderer()

    with pytest.raises(ValueError, match="unsupported structured-content tag"):
        renderer.glossary([{"type": "structured-content", "content": {"tag": "video"}}])


def test_plain_renderer_keeps_markers_on_sense_group_lists():
    renderer = PlainTextRenderer()
    glossary = [{
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "data": {"content": "sense-groups"},
            "content": [
                {
                    "tag": "li",
                    "style": {"listStyleType": "\"①\""},
                    "content": {"tag": "div", "content": "первое значение"},
                },
                {"tag": "li", "content": {"tag": "div", "content": "формы"}},
            ],
        },
    }]

    text = renderer.glossary(glossary)[0]

    assert text == "① первое значение\nформы"
    assert renderer.counts["css_list_markers_preserved"] == 1


def test_plain_archive_is_deterministic_schema_shaped_and_source_faithful(tmp_path):
    source = tmp_path / "rich.zip"
    tag_bank = _write_rich_archive(source)
    first = tmp_path / "plain-first.zip"
    second = tmp_path / "plain-second.zip"

    first_result = convert_yomitan_to_plain(source, first)
    second_result = convert_yomitan_to_plain(source, second)
    verified = verify_plain_yomitan(first, source=source)

    assert first.read_bytes() == second.read_bytes()
    assert first_result["zip_sha256"] == second_result["zip_sha256"]
    assert first_result["articles"] == 1
    assert first_result["media_files_omitted"] == 1
    assert first_result["stylesheets_omitted"] == 1
    assert verified["verified"] is True
    assert verified["source_verified"] is True
    assert verified["deinflection_items"] == 1
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["index.json", "tag_bank_1.json", "term_bank_1.json"]
        assert archive.read("tag_bank_1.json") == tag_bank
        index = json.loads(archive.read("index.json"))
        assert index["title"] == "Колобок 400k" + PLAIN_TITLE_SUFFIX
        assert index["revision"].endswith("-" + PLAIN_TEXT_PROFILE)
        assert PLAIN_DESCRIPTION in index["description"]
        assert not {"isUpdatable", "indexUrl", "downloadUrl"} & set(index)
        row = json.loads(archive.read("term_bank_1.json"))[0]
        assert row[:5] == ["猫", "ねこ", "n", "", 5]
        assert row[6:] == [123, ""]
        assert isinstance(row[5], list)
        assert isinstance(row[5][0], str)
        assert row[5][1] == ["ねこ", ["вариант написания: 猫"]]
        assert "① кот; кошка" in row[5][0]
