import json

from jitendex_ru.extract_units import extract_article_units, protected_tokens


def test_lexicographer_extracts_plain_yomitan_glossary_as_one_set():
    row = ["人買い", "ひとかい", "", "", 0, ["Menschenhandel", "Sklavenhandel"], 0, "和独辞典"]

    units = extract_article_units(row, "lexicographer-v2")

    assert len(units) == 1
    assert units[0].pointer == "/5"
    assert units[0].role == "glossary_set"
    assert json.loads(units[0].source_text) == ["Menschenhandel", "Sklavenhandel"]


def test_scalar_protected_tokens_are_available_to_the_model_and_validator():
    assert protected_tokens("example", "Press Ctrl+Alt+Del") == ("Ctrl+Alt+Del",)
    assert protected_tokens("note", 'Thai: "khao man kai"') == ("khao man kai",)
    assert protected_tokens(
        "xref_gloss", "Japanese sea bass (Lateolabrax japonicus)"
    ) == ("Lateolabrax japonicus",)


def test_source_acronyms_are_visible_to_the_model_before_validation():
    assert protected_tokens(
        "glossary_set", '[{"content":"head-mounted display"},{"content":"HMD"}]'
    ) == ("HMD",)


def test_common_xref_phrases_are_not_mistaken_for_taxa():
    assert protected_tokens("xref_gloss", "washi; Japanese paper") == ()
    assert protected_tokens("xref_gloss", "Morse code (esp. signalling)") == ()
    assert protected_tokens("xref_gloss", "Akihabara style; nerdy") == ()
    assert protected_tokens(
        "xref_gloss",
        "③ government office related to finances (Kamakura and Muromachi periods)",
    ) == ()


def test_japanese_monolingual_yomitan_units_are_extracted_for_translation():
    row = [
        "ぴくり", "ぴくり", "", "", 0,
        [{"type": "structured-content", "content": [
            {"data": {"content": "glossaryShortDefinition"}, "content": [
                {"tag": "li", "content": "体が一度だけ小さく動くさま。"},
            ]},
            {"data": {"content": "examples"}, "content": [
                {"tag": "li", "content": "眉がぴくりと動く。"},
            ]},
        ]}], 1, "",
    ]

    units = extract_article_units(row, "lexicographer-v2")

    assert [(unit.role, unit.source_text) for unit in units] == [
        ("glossary", "体が一度だけ小さく動くさま。"),
        ("example", "眉がぴくりと動く。"),
    ]
    assert all(unit.protected_tokens == () for unit in units)
