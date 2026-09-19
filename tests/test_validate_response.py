import json
import pytest

from jitendex_ru.db import connect, initialize
from jitendex_ru.validate_response import (
    _plain_text_issues, allows_japanese_grammar_label, ingest_response, validate_worker_payload,
    wadoku_cross_article_duplicate_issues,
    wadoku_target_issues, wadoku_xml_block_allows_exact_source,
    wadoku_xml_block_is_scientific,
)


def test_wadoku_cross_article_duplicate_gate_targets_shift_signature():
    expected = [
        {"id": "u1", "article_id": 1, "role": "glossary_set", "source_text": '["Imid"]'},
        {"id": "u2", "article_id": 2, "role": "glossary_set", "source_text": '["Anwendung"]'},
    ]
    translations = [
        {"target_text": ["применение", "использование"]},
        {"target_text": ["применение", "использование"]},
    ]
    assert wadoku_cross_article_duplicate_issues(expected, translations) == [{
        "code": "wadoku_cross_article_duplicate_target",
        "unit_ids": ["u1", "u2"],
        "article_ids": [1, 2],
        "role": "glossary_set",
        "target_text": ["применение", "использование"],
    }]


def test_wadoku_cross_article_duplicate_gate_allows_shared_etymology():
    expected = [
        {"id": "u1", "article_id": 1, "role": "etymology", "source_text": "von engl. x"},
        {"id": "u2", "article_id": 2, "role": "etymology", "source_text": "aus d. Engl. x"},
    ]
    translations = [
        {"target_text": "из английского языка x"},
        {"target_text": "из английского языка x"},
    ]
    assert wadoku_cross_article_duplicate_issues(expected, translations) == []


def fixture_db(tmp_path):
    path = tmp_path / "db.sqlite3"
    initialize(path)
    connection = connect(path)
    connection.execute("INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version) VALUES ('jitendex','v','u','h','p','e')")
    connection.execute("INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version) VALUES ('kaishi','v','u','k','p','e')")
    connection.execute("INSERT INTO run(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json) VALUES (1,2,?,?,?,?,?,?)", ("s", "e", "p", "rp", "t", "{}"))
    row = json.dumps(["x", "x", "", "", 0, [], 1, ""])
    connection.execute("INSERT INTO article(snapshot_id,bank_number,entry_ordinal,expression,reading,sequence,raw_json,source_sha256,selected) VALUES (1,1,0,'x','x',1,?,'a',1)", (row,))
    connection.execute("INSERT INTO translation_unit(id,run_id,article_id,json_pointer,role,source_text,source_sha256,protected_tokens_json,byte_count) VALUES ('u1',1,1,'/5/0','glossary','Hello JMdict','sh','[\"JMdict\"]',12)")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"articles": []}), encoding="utf-8")
    connection.execute("INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,unit_count,manifest_path) VALUES ('b1',1,?,1,1,1,?)", ("f" * 64, str(manifest_path)))
    connection.execute("INSERT INTO batch_item VALUES ('b1','u1',0)")
    connection.execute("INSERT INTO attempt(id,batch_id,worker_id,model,prompt_sha256,request_path,response_path) VALUES ('a1','b1','w','m','p','m','o')")
    connection.commit()
    return connection, connection.execute("SELECT * FROM attempt WHERE id='a1'").fetchone()


def test_valid_response(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "привет JMdict", "confidence": "high", "review_reason": None}
    ]}
    assert validate_worker_payload(connection, attempt, payload) == []


def test_rejects_adjacent_mixed_alphabets_but_allows_hyphenated_terms():
    assert "mixed_alphabet_token" in _plain_text_issues("ошибка гikun", [])
    assert "mixed_alphabet_token" in _plain_text_issues("это emphатично", [])
    assert "mixed_alphabet_token" in _plain_text_issues("сталagmíт", [])
    assert "mixed_alphabet_token" not in _plain_text_issues(
        "JIT-компилятор, 3D-принтер и USB-концентратор", ["JIT", "3D", "USB"],
    )


def test_stale_attempt_cannot_ingest_after_lease_changes(tmp_path):
    connection, _ = fixture_db(tmp_path)
    response_path = tmp_path / "stale.json"
    connection.execute(
        "UPDATE attempt SET response_path=?,lease_token='old' WHERE id='a1'", (str(response_path),)
    )
    connection.execute(
        "UPDATE batch SET state='leased',lease_token='new' WHERE id='b1'"
    )
    connection.commit()
    response_path.write_text(json.dumps({
        "schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64,
        "translations": [{
            "unit_id": "u1", "source_sha256": "sh", "target_text": "привет JMdict",
            "confidence": "high", "review_reason": None,
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="stale attempt"):
        ingest_response(connection, response_path)
    assert connection.execute("SELECT COUNT(*) FROM translation").fetchone()[0] == 0


def test_source_acronyms_are_required_but_not_counted_as_english(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE translation_unit SET role='xref_gloss',source_text='sentence structures (SV, SVC, SVO, SVOO, SVOC)',protected_tokens_json='[]' WHERE id='u1'"
    )
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "структуры предложения (SV, SVC, SVO, SVOO, SVOC)", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "структуры предложения"
    assert "protected_token_missing" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }


def test_rejects_order_markup_and_lost_token(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "<b>перевод</b>", "confidence": "high", "review_reason": None}
    ]}
    codes = {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}
    assert {"markup_detected", "protected_token_missing"} <= codes


def test_allows_parenthesized_scientific_taxon(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute("UPDATE translation_unit SET protected_tokens_json='[]' WHERE id='u1'")
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "собака (Canis lupus familiaris)", "confidence": "high", "review_reason": None}
    ]}
    assert validate_worker_payload(connection, attempt, payload) == []

    payload["translations"][0]["target_text"] = "перилла (Perilla frutescens var. crispa)"
    assert validate_worker_payload(connection, attempt, payload) == []

    payload["translations"][0]["target_text"] = "японский волк (Canis lupus hodophilax; вымерший вид)"
    assert validate_worker_payload(connection, attempt, payload) == []

    payload["translations"][0]["target_text"] = "юдзу (Citrus ichangensis x C. reticulata)"
    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "юдзу (Citrus ichangensis × C. reticulata)"
    assert validate_worker_payload(connection, attempt, payload) == []


def test_allows_exact_language_origin_citation_but_still_protects_it(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE translation_unit SET role='note',source_text='Thai: \"khao man kai\"',protected_tokens_json='[]' WHERE id='u1'"
    )
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "тайск.: «khao man kai»", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "тайское происхождение"
    assert "protected_token_missing" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }


def test_allows_and_protects_keyboard_chord(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE translation_unit SET role='example',source_text='Push Ctrl+Alt+Del to log on',protected_tokens_json='[]' WHERE id='u1'"
    )
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "Нажмите Ctrl+Alt+Del, чтобы войти в систему.", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "Нажмите клавиши, чтобы войти в систему."
    assert "protected_token_missing" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }


def test_allows_and_protects_source_taxa_in_cross_reference(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE translation_unit SET role='xref_gloss',source_text='Hexacentrus japonicus; Hexacentrus unicolor',protected_tokens_json='[]' WHERE id='u1'"
    )
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "Hexacentrus japonicus (вид кузнечиков); Hexacentrus unicolor (вид кузнечиков)", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "Hexacentrus japonicus (вид кузнечиков)"
    assert "protected_token_missing" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }

    connection.execute(
        "UPDATE translation_unit SET source_text='any turtle (esp. the Japanese pond turtle, Mauremys japonica)' WHERE id='u1'"
    )
    payload["translations"][0]["target_text"] = (
        "любая черепаха рода Mauremys (особенно японская прудовая черепаха, Mauremys japonica)"
    )
    assert validate_worker_payload(connection, attempt, payload) == []

    connection.execute(
        "UPDATE translation_unit SET source_text='Japanese sea bass (Lateolabrax japonicus)' WHERE id='u1'"
    )
    payload["translations"][0]["target_text"] = "морской судак (Lateolabrax japonicus)"
    assert validate_worker_payload(connection, attempt, payload) == []

    connection.execute("UPDATE translation_unit SET source_text='See also' WHERE id='u1'")
    payload["translations"][0]["target_text"] = "см. также"
    assert validate_worker_payload(connection, attempt, payload) == []

    connection.execute("UPDATE translation_unit SET source_text='Naruto wakame' WHERE id='u1'")
    payload["translations"][0]["target_text"] = "вакамэ из Наруто"
    assert validate_worker_payload(connection, attempt, payload) == []

    for source, target in (
        ("washi; Japanese paper", "васи; японская бумага"),
        ("Morse code (esp. signalling)", "азбука Морзе (особенно сигналы)"),
        ("Akihabara style; nerdy", "в стиле Акихабары; гиковский"),
        (
            "③ government office related to finances (Kamakura and Muromachi periods)",
            "③ финансовое ведомство (периоды Камакура и Муромати)",
        ),
        (
            "mix of peanuts and mochi chips in the shape of kaki (Japanese persimmon) seeds",
            "смесь арахиса и рисовых крекеров в форме семян японской хурмы",
        ),
    ):
        connection.execute("UPDATE translation_unit SET source_text=? WHERE id='u1'", (source,))
        payload["translations"][0]["target_text"] = target
        assert validate_worker_payload(connection, attempt, payload) == []


def test_lexicographer_accepts_variable_length_glossary_but_rejects_duplicates(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute("UPDATE run SET pipeline_version='lexicographer-v2' WHERE id=1")
    connection.execute("UPDATE translation_unit SET role='glossary_set',protected_tokens_json='[]' WHERE id='u1'")
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": ["начинать", "приступать к"], "confidence": "high", "review_reason": None}
    ]}
    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = ["начинать", "начинать"]
    assert "duplicate_glossary_definition" in {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}


def test_wadoku_profile_allows_broad_glossaries_translated_acronyms_and_taxa(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE run SET pipeline_version='lexicographer-v2',extractor_version='extractor-plain-glossary-v1' WHERE id=1"
    )
    connection.execute(
        "UPDATE translation_unit SET role='glossary_set',source_text=?,protected_tokens_json=? WHERE id='u1'",
        (json.dumps(["UNESCO", "Helicobacter pylori"]), json.dumps(["UNESCO"])),
    )
    definitions = [f"русское значение {index}" for index in range(13)]
    definitions.append("бактерия Helicobacter pylori")
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": definitions,
         "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []


def test_wadoku_profile_allows_exact_source_scientific_name_definitions(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE run SET pipeline_version='lexicographer-v2',extractor_version='extractor-plain-glossary-v1' WHERE id=1"
    )
    connection.execute(
        "UPDATE translation_unit SET role='glossary_set',source_text=?,protected_tokens_json='[]' WHERE id='u1'",
        (json.dumps([
            "Wolf\nCanis lupusRaubtier aus der Familie der Hundeartigen",
            "Brassica campestris var. hakabura",
            "Oecobius navus\nOecobius annulipeseine Webspinne",
        ]),),
    )
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh",
         "target_text": [
             "волк", "Canis lupus", "разновидность капусты Brassica campestris var. hakabura",
             "Oecobius navus", "Oecobius annulipes",
         ],
         "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []


def test_wadoku_profile_does_not_treat_person_names_as_scientific_names(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE run SET pipeline_version='lexicographer-v2',extractor_version='extractor-plain-glossary-v1' WHERE id=1"
    )
    connection.execute(
        "UPDATE translation_unit SET role='glossary_set',source_text=?,protected_tokens_json='[]' WHERE id='u1'",
        (json.dumps(["Mino Monta"]),),
    )
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": ["Mino Monta"],
         "confidence": "high", "review_reason": None}
    ]}

    codes = {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}
    assert "no_cyrillic" in codes


def test_wadoku_profile_allows_exact_neutral_tokens_only_beside_russian_text(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE run SET pipeline_version='lexicographer-v2',extractor_version='extractor-plain-glossary-v1' WHERE id=1"
    )
    connection.execute(
        "UPDATE translation_unit SET role='glossary_set',source_text=?,protected_tokens_json='[]' WHERE id='u1'",
        (json.dumps(["System-on-a-Chip", "SoC", "Pikofarad", "pF", "♣", "47", "Eurosat"]),),
    )
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh",
         "target_text": ["система на кристалле", "SoC", "pF", "♣", "сорок семь", "47"],
         "confidence": "high", "review_reason": None}
    ]}
    assert validate_worker_payload(connection, attempt, payload) == []

    payload["translations"][0]["target_text"] = ["Eurosat"]
    codes = {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}
    assert "no_cyrillic" in codes

    payload["translations"][0]["target_text"] = ["47"]
    codes = {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}
    assert "no_cyrillic" in codes

    connection.execute(
        "UPDATE translation_unit SET source_text=? WHERE id='u1'", (json.dumps(["PIM"]),),
    )
    payload["translations"][0]["target_text"] = ["PIM"]
    assert validate_worker_payload(connection, attempt, payload) == []

    connection.execute(
        "UPDATE translation_unit SET source_text=? WHERE id='u1'", (json.dumps(["1988"]),),
    )
    payload["translations"][0]["target_text"] = ["1988"]
    assert validate_worker_payload(connection, attempt, payload) == []


def test_glossary_accepts_exact_source_acronym_alongside_russian_definition(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute("UPDATE run SET pipeline_version='lexicographer-v2' WHERE id=1")
    connection.execute(
        "UPDATE translation_unit SET role='glossary_set',source_text=?,protected_tokens_json='[]' WHERE id='u1'",
        ('[{"content":"ETD"},{"content":"estimated time of departure"}]',),
    )
    payload = {"schema_version": 2, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": ["ETD", "расчётное время отправления"], "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = ["ETD"]
    assert "no_cyrillic" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }


def test_example_allows_source_english_grammar_tokens_but_not_prose(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    connection.execute(
        "UPDATE translation_unit SET role='example',source_text=?,protected_tokens_json='[]' WHERE id='u1'",
        ("When the antecedent is this, that, these or those it is usual to use 'which'.",),
    )
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "Если антецедент — this, that, these или those, обычно употребляют which.", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []
    payload["translations"][0]["target_text"] = "Это a very bad translation of source."
    assert "too_much_english" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, payload)
    }


def test_japanese_suru_pos_label_is_a_narrow_cyrillic_exception():
    assert allows_japanese_grammar_label("pos", "suru")
    assert _plain_text_issues("する", [], allow_no_cyrillic=True) == []
    assert not allows_japanese_grammar_label("tooltip", "suru")
    assert _plain_text_issues("する", []) == ["no_cyrillic"]


def test_allows_intermediate_variation_from_approved_terminology(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    manifest = {"articles": [{"units": [{
        "unit_id": "u1",
        "required_terminology": {"target_text": "гл. годан с окончанием на «ぶ»"},
    }]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "Глагол годан с окончанием на бу", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []


def test_deferred_terminology_still_requires_valid_russian_output(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    manifest = {"articles": [{"units": [{
        "unit_id": "u1",
        "required_terminology": {"target_text": "гл. годан"},
    }]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "godan verb", "confidence": "high", "review_reason": None}
    ]}

    codes = {issue["code"] for issue in validate_worker_payload(connection, attempt, payload)}

    assert "no_cyrillic" in codes


def test_approved_terminology_overrides_conflicting_protected_tokens(tmp_path):
    connection, attempt = fixture_db(tmp_path)
    manifest = {"articles": [{"units": [{
        "unit_id": "u1",
        "required_terminology": {"target_text": "гл. итидан"},
    }]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    payload = {"schema_version": 1, "batch_id": "b1", "manifest_sha256": "f" * 64, "translations": [
        {"unit_id": "u1", "source_sha256": "sh", "target_text": "гл. итидан", "confidence": "high", "review_reason": None}
    ]}

    assert validate_worker_payload(connection, attempt, payload) == []


def test_wadoku_allows_unchanged_chemical_formula_only():
    assert wadoku_target_issues("PbS", "PbS", []) == []
    assert {issue["code"] for issue in wadoku_target_issues("Blei", "Blei", [])} == {
        "no_cyrillic",
    }


def test_wadoku_allows_exact_scientific_names_and_compact_notation():
    for source in (
        "Sargassum fulvellum", "Anorexia nervosa", "CH₃COOC₅H₁₁", "(NH₄)₂S",
        "mCi", "Tyr", "EloGM", "L", "Barnard 72",
    ):
        assert wadoku_target_issues(source, source, [], "u1") == []


def test_wadoku_preserves_proper_foreign_forms_but_not_german_text():
    for source in (
        "Erwin Chargaff", "Tosho Shinbun", "Hōjō",
        "Fridays for Future", "Ebi·ne",
        "United Kingdom of Great Britain and Northern Ireland",
    ):
        assert wadoku_target_issues(source, source, [], "u1") == []
    for source in ("Blei", "eine große Menge"):
        assert "no_cyrillic" in {
            issue["code"] for issue in wadoku_target_issues(source, source, [], "u1")
        }


def test_wadoku_preserves_taxonomy_latin_and_standard_ids():
    for source in (
        "Degeneriaceae", "Batidales", "Facies articularis carpea radii",
        "JIS X 0201", "ISBN 4-479-", "ICD10: D58.1", "1 Oe = 79,577 A/m",
    ):
        assert wadoku_target_issues(source, source, [], "u1") == []
    assert wadoku_target_issues("Tǒngzi", "Tǒngzi", [], "u1") == []
    assert wadoku_target_issues(
        "engl. International Convention for Human Rights",
        "англ. International Convention for Human Rights", [], "u1",
    ) == []


def test_wadoku_preserves_unicode_formulas_and_neutral_normalizations():
    for source in (
        "²²²Rn", "i² = ‑1", "1 Ω = 1 V/A", "‑C₅H₁₁", "°C", "n+½",
        "SiₙH₂ₙ₊₂", "Mᵤ", "§ 424 jBGB", "a:b", "b′",
    ):
        assert wadoku_target_issues(source, source, [], "u1") == []
    assert wadoku_target_issues("8:30 Uhr", "8:30", [], "u1") == []
    assert wadoku_target_issues("Nr. 113", "№ 113", [], "u1") == []
    assert wadoku_target_issues("AT & T", "AT&T", [], "u1", allow_exact_source=True) == []
    assert wadoku_target_issues(
        "Sony Playstation", "Sony PlayStation", [], "u1", allow_exact_source=True,
    ) == []


def test_wadoku_preserves_narrow_undomained_foreign_labels():
    for source in ("Chkdsk", "Gamescom", "gamescom", "Dir en grey", "Hypera", "… vulgaris"):
        assert wadoku_target_issues(source, source, [], "u1") == []
    assert "no_cyrillic" in {
        issue["code"] for issue in wadoku_target_issues("Menge", "Menge", [], "u1")
    }


def test_wadoku_allows_named_foreign_terms_inside_russian_text():
    cases = (
        (
            "SUMP steht für Suzuki's universal micro-printing",
            "SUMP — сокращение от Suzuki's universal micro-printing",
        ),
        (
            "von engl. non-territorial office",
            "из англ. non-territorial office",
        ),
        (
            "Betriebssystem des iPhone und iPod touch von Apple",
            "операционная система для iPhone и iPod touch компании Apple",
        ),
        (
            "Trigonometrie: sem = (versin a) / 2 = (1 − cos a) / 2",
            "Тригонометрия: sem = (versin a) / 2 = (1 − cos a) / 2",
        ),
    )
    for source, target in cases:
        assert wadoku_target_issues(source, target, [], "u1") == []
    assert "too_much_english" in {
        issue["code"] for issue in wadoku_target_issues(
            "eine deutsche Erklärung mit vielen Wörtern",
            "перевод mit vielen deutschen Wörtern", [], "u1",
        )
    }


def test_wadoku_scientific_block_uses_canonical_xml_ancestry():
    raw = json.dumps({
        "blocks": [{
            "xml_path": "/entry[1]/sense[1]/trans[2]/tr[1]",
            "source_text": "Centropus",
        }],
        "tree": {"tag": "entry", "attributes": {}, "children": [{
            "tag": "sense", "attributes": {}, "children": [
                {"tag": "trans", "attributes": {}, "children": []},
                {"tag": "trans", "attributes": {"langdesc": "scientific"}, "children": [
                    {"tag": "tr", "attributes": {}, "children": []},
                ]},
            ],
        }]},
    })
    assert wadoku_xml_block_is_scientific(raw, "/blocks/0") is True


def test_wadoku_name_domain_allows_exact_untagged_foreign_form():
    raw = json.dumps({
        "blocks": [{"xml_path": "/entry[1]/sense[1]/trans[1]/tr[1]"}],
        "tree": {"tag": "entry", "attributes": {}, "children": [{
            "tag": "sense", "attributes": {}, "children": [
                {"tag": "usg", "attributes": {"type": "dom"}, "text": "Stadtn.", "children": []},
                {"tag": "trans", "attributes": {}, "children": [
                    {"tag": "tr", "attributes": {}, "children": []},
                ]},
            ],
        }]},
    })
    assert wadoku_xml_block_allows_exact_source(raw, "/blocks/0", "Harrisburg") is True


def test_wadoku_foreign_etymology_allows_exact_title():
    raw = json.dumps({
        "blocks": [{"xml_path": "/entry[1]/sense[1]/trans[1]/tr[1]"}],
        "tree": {"tag": "entry", "attributes": {}, "children": [
            {"tag": "etym", "attributes": {}, "text": "von engl. ", "children": [
                {"tag": "foreign", "attributes": {}, "text": "Fridays for Future",
                 "tail": "", "children": []},
            ]},
            {"tag": "sense", "attributes": {}, "children": [
                {"tag": "trans", "attributes": {}, "children": [
                    {"tag": "tr", "attributes": {}, "children": []},
                ]},
            ]},
        ]},
    })
    assert wadoku_xml_block_allows_exact_source(
        raw, "/blocks/0", "Fridays for Future",
    ) is True


def test_wadoku_allows_source_proper_names_in_russian_sentence():
    source = (
        "gegründet durch Zusammenschluss von Kensei·hontō mit Yushinkai, "
        "Mumeikai und anderen Gruppen"
    )
    target = (
        "основана в результате объединения Kensei·hontō с Yushinkai, "
        "Mumeikai и другими группами"
    )
    assert wadoku_target_issues(source, target, []) == []
