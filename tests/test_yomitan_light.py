from __future__ import annotations

import json
import zipfile

from collections import Counter

from jitendex_ru.yomitan_light import (
    LightRenderer, convert_yomitan_to_light, light_glossary, verify_light_yomitan,
)


def test_light_export_keeps_only_forms_redirects_and_definitions(tmp_path):
    source = tmp_path / "rich.zip"
    output = tmp_path / "light.zip"
    article = {"type": "structured-content", "content": [
        {"tag": "div", "data": {"content": "sense"}, "content": [
            {"tag": "ul", "data": {"content": "glossary"}, "content":
                {"tag": "li", "content": "слово"}},
            {"tag": "div", "data": {"content": "example-sentence"}, "content": "пример"},
        ]},
        {"tag": "div", "data": {"content": "forms"}, "content": [
            {"tag": "span", "data": {"content": "forms-label"}, "content": "формы"},
            {"tag": "ul", "content": [{"tag": "li", "content": "字"},
                {"tag": "li", "content": "じ"}]},
        ]},
        {"tag": "div", "data": {"content": "redirect-glossary"}, "content": [
            "⟶", {"tag": "a", "href": "?query=字", "content": "字"},
        ]},
        {"tag": "div", "data": {"content": "attribution"}, "content":
            {"tag": "a", "href": "https://tatoeba.org/", "content": "Tatoeba"}},
    ]}
    row = ["字", "じ", "", "", 0, [article, ["字", ["variant"]]], 1, ""]
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": "Колобок 400k", "revision": "v1",
            "format": 3, "isUpdatable": True, "downloadUrl": "https://example.com/rich.zip"}))
        archive.writestr("tag_bank_1.json", "[]")
        archive.writestr("term_bank_1.json", json.dumps([row], ensure_ascii=False))
        archive.writestr("styles.css", "body{}")
    result = convert_yomitan_to_light(source, output)
    assert result["articles"] == 1
    assert verify_light_yomitan(output, source=source)["verified"] is True
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["index.json", "tag_bank_1.json", "term_bank_1.json"]
        index = json.loads(archive.read("index.json"))
        assert index["title"] == "Колобок 400k — лёгкий"
        assert "downloadUrl" not in index
        glossary = json.loads(archive.read("term_bank_1.json"))[0][5]
        assert glossary == ["слово", "Формы: 字; じ", "⟶字", ["字", ["variant"]]]
        assert "пример" not in str(glossary)
        assert "Tatoeba" not in str(glossary)


def test_form_table_becomes_a_short_word_list():
    forms = {"type": "structured-content", "content": {"tag": "div",
        "data": {"content": "forms"}, "content": {"tag": "table", "content": [
            {"tag": "tr", "content": [{"tag": "th", "content": "漢字"},
                {"tag": "th", "content": "かな"}]},
            {"tag": "tr", "content": [{"tag": "th", "content": "かんじ"},
                {"tag": "td", "data": {"class": "form-valid"}}]},
        ]}}}
    assert light_glossary([forms], LightRenderer(), Counter()) == [
        "Формы: 漢字; かな; かんじ"
    ]
