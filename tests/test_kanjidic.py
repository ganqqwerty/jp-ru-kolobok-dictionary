import json

from jitendex_ru.extract_units import extract_article_units, kanjidic_context


def test_kanjidic_extracts_one_meaning_set_at_pointer_four():
    row = ["亜", "ア", "つ.ぐ", "jouyou", ["Asia", "rank next"], {"strokes": "7"}]

    units = extract_article_units(row, "kanjidic-v1")

    assert len(units) == 1
    assert units[0].pointer == "/4"
    assert units[0].role == "glossary_set"
    assert json.loads(units[0].source_text) == ["Asia", "rank next"]


def test_kanjidic_context_keeps_readings_and_metadata_as_evidence():
    row = ["亜", "ア", "つ.ぐ", "jouyou", ["Asia"], {"strokes": "7"}]

    context = kanjidic_context(row)

    assert context["kanji"] == "亜"
    assert context["on_readings"] == "ア"
    assert context["kun_readings"] == "つ.ぐ"
    assert context["metadata"] == {"strokes": "7"}
