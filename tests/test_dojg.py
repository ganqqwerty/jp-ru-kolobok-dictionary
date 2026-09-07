import json
from pathlib import Path

from jitendex_ru.apply_translations import apply_article
from jitendex_ru.batch import claim, close_superseded_batches, make_batches
from jitendex_ru.db import connect, initialize
from jitendex_ru.dojg import (
    DOJG_JAPANESE_TEXT_RE, DOJG_PLACEHOLDER_RE, dojg_protected_tokens, extract_dojg_segments,
    japanese_text_signature, parse_dojg_pointer, rebuild_dojg_segment,
)
from jitendex_ru.extract_units import extract_article_units, extract_selected
from jitendex_ru.jpdb_scope import accept_deterministic_translations
from jitendex_ru.util import canonical_json, sha256_bytes
from jitendex_ru.validate_response import (
    _plain_text_issues,
    allows_dojg_notation_only,
    dojg_allowed_english,
    dojg_untranslated_english,
    ingest_response,
    validate_worker_payload,
)


SOURCE_TEXT = """文法項目 | て | 基本
 [解説]
 The て form connects clauses.
 [例文A]
 (ks).　私はパンを食べる。
 [例文B]
 The child eats bread.
 [接続]
 Adjective い stem | 高くて | Something is expensive and
 """


def source_row():
    return ["て", "て", "", "", 0, [SOURCE_TEXT], 1, "DOJG基本"]


def target_for(unit):
    protected = json.loads(unit["protected_tokens_json"])
    return " ".join([*protected, "перевод"])


def setup_run(tmp_path: Path):
    db_path = tmp_path / "dojg.sqlite3"
    initialize(db_path)
    connection = connect(db_path)
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES ('jitendex','dojg','', 'source', 'source.zip', 'extractor-dojg-v1')"""
    )
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES ('kaishi','dojg','', 'scope', 'source.zip', 'extractor-dojg-v1')"""
    )
    raw = canonical_json(source_row()).decode()
    connection.execute(
        """INSERT INTO article
        (snapshot_id,bank_number,entry_ordinal,expression,reading,sequence,raw_json,source_sha256,selected)
        VALUES (1,1,0,'て','て',1,?,?,1)""",
        (raw, sha256_bytes(raw.encode())),
    )
    connection.execute(
        """INSERT INTO run
        (jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
         prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,pipeline_version)
        VALUES (1,2,'selection','extractor-dojg-v1','prompt','review','terms','{}','dojg-v1')"""
    )
    extract_selected(connection, 1)
    made = make_batches(connection, 1, tmp_path / "inbox", {}, 4, 32768, 80, 24576, 98304, 240)
    assert made["batches_created"] == 1
    connection.commit()
    task = claim(
        connection, "dojg-test", tmp_path / "outbox", run_id=1, kind="translation",
        model_id="gpt-5.6-luna", reasoning_effort="medium", transport="codex-agent",
    )
    connection.commit()
    return connection, task


def test_dojg_extractor_emits_english_scaffolds_and_read_only_japanese_placeholders():
    units = extract_article_units(source_row(), "dojg-v1")

    assert len(units) == 4
    assert all(unit.role == "dojg_text" for unit in units)
    assert all(DOJG_JAPANESE_TEXT_RE.search(unit.source_text) is None for unit in units)
    assert any("The ⟦J0001⟧ form connects clauses." == unit.source_text for unit in units)
    assert not any("私はパンを食べる" in unit.source_text for unit in units)
    assert all(parse_dojg_pointer(unit.pointer) is not None for unit in units)


def test_dojg_extractor_ignores_japanese_examples_with_latin_formula_variables():
    row = [
        "によらず", "によらず", "", "", 0,
        ["(f).　QRの長さはPの位置によらず常に一定である。"], 1, "DOJG上級編",
    ]

    assert extract_dojg_segments(row) == []


def test_dojg_rebuild_requires_exact_placeholder_order():
    original = "The て form is used with な adjectives."
    segment = extract_dojg_segments(["x", "x", "", "", 0, [original], 1, "DOJG基本"])[0]

    rebuilt = rebuild_dojg_segment(
        original, segment.source_text, "Форма ⟦J0001⟧ употребляется с ⟦J0002⟧-прилагательными.",
    )

    assert rebuilt == "Форма て употребляется с な-прилагательными."


def test_dojg_does_not_protect_english_articles_or_pronoun_i():
    assert dojg_protected_tokens("I sent a letter") == ()
    assert dojg_protected_tokens("Noun+copula in the U.S.") == ()
    assert dojg_protected_tokens("Vinformal past") == ()


def test_placeholder_removal_does_not_create_a_false_mixed_alphabet_token():
    assert _plain_text_issues(
        "неформальная форма⟦J0001⟧V2",
        ["⟦J0001⟧"],
    ) == []


def test_dojg_allows_only_unchanged_notation_without_cyrillic():
    ph_source = "(c).⟦J0001⟧pH⟦J0002⟧"
    dose_source = "(f).⟦J0001⟧1⟦J0002⟧50mg⟦J0003⟧"
    dose_table_source = (
        "(f).⟦J0001⟧1⟦J0002⟧50mg⟦J0003⟧2⟦J0004⟧1"
        "⟦J0005⟧1⟦J0006⟧1⟦J0007⟧2⟦J0008⟧"
    )
    sentence_formula = "(S2…) ⟦J0001⟧ Sn"

    assert allows_dojg_notation_only(ph_source, ph_source)
    assert allows_dojg_notation_only(dose_source, dose_source)
    assert allows_dojg_notation_only(dose_table_source, dose_table_source)
    assert allows_dojg_notation_only(sentence_formula, sentence_formula)
    quoted_source = '(f).⟦J0001⟧""Sorry""⟦J0002⟧'
    assert allows_dojg_notation_only(quoted_source, quoted_source)
    assert allows_dojg_notation_only(quoted_source, '(f).⟦J0001⟧«Sorry»⟦J0002⟧')
    assert not allows_dojg_notation_only("Vinformal nonpast", "Vinformal nonpast")
    assert not allows_dojg_notation_only("If", "If")
    assert not allows_dojg_notation_only(ph_source, "pH")


def test_dojg_allows_source_names_but_not_english_grammar_labels():
    allowed = dojg_allowed_english(
        'The New York Times explains "Sorry" and "moushiwake arimasen". '
        'Vinformal past uses Adjective stems and BMWs.'
    )

    assert {"New", "York", "Times", "Sorry", "moushiwake", "arimasen", "BMWs"} <= set(allowed)
    assert "Adjective" not in allowed
    assert "Sentence" not in allowed
    assert "The" not in allowed


def test_dojg_residual_english_gate_allows_names_notation_and_quoted_terms():
    source = (
        'The New York Times compares V-ing with Sn, pH, 50mg, BMWs, '
        'Mercedes Benzes, Peanuts, and "moushiwake arimasen" in the XXI century.'
    )
    target = (
        'New York Times сравнивает V-ing с Sn, pH, 50mg, BMW, '
        'Mercedes-Benz, Peanuts и «moushiwake arimasen» в XXI веке.'
    )

    assert dojg_untranslated_english(source, target) == []
    assert dojg_untranslated_english(
        "The New York Times has subscribers.",
        "The New York Times имеет подписчиков.",
    ) == []


def test_dojg_residual_english_gate_rejects_prose_labels_and_unquoted_romaji():
    source = "The Adjective sentence emphatically explains sabishii and bekida for five hundred years."
    target = "Adjective Sentence emphatically sabishii bekida hundred"

    assert dojg_untranslated_english(source, target) == [
        "Adjective", "Sentence", "emphatically", "sabishii", "bekida", "hundred",
    ]


def test_dojg_manifest_validation_and_application_preserve_japanese(tmp_path):
    connection, task = setup_run(tmp_path)
    manifest = json.loads(Path(task["request_path"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["pipeline"] == "dojg-v1"
    assert manifest["articles"][0]["read_only_context"]["dictionary"] == "Dictionary of Japanese Grammar"
    units = connection.execute(
        """SELECT tu.* FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
        WHERE bi.batch_id=? ORDER BY bi.ordinal""", (task["batch_id"],),
    ).fetchall()
    payload = {
        "schema_version": 2,
        "batch_id": task["batch_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "translations": [
            {
                "unit_id": unit["id"], "source_sha256": unit["source_sha256"],
                "target_text": target_for(unit), "confidence": "high", "review_reason": None,
            }
            for unit in units
        ],
    }
    attempt = connection.execute("SELECT * FROM attempt WHERE id=?", (task["attempt_id"],)).fetchone()
    assert validate_worker_payload(connection, attempt, payload) == []
    placeholder_index = next(
        index for index, item in enumerate(payload["translations"])
        if DOJG_PLACEHOLDER_RE.search(item["target_text"])
    )
    broken = json.loads(json.dumps(payload, ensure_ascii=False))
    broken["translations"][placeholder_index]["target_text"] = DOJG_PLACEHOLDER_RE.sub(
        "", broken["translations"][placeholder_index]["target_text"], count=1,
    )
    assert "dojg_placeholder_order_or_set_mismatch" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, broken)
    }
    broken = json.loads(json.dumps(payload, ensure_ascii=False))
    broken["translations"][0]["target_text"] += " | лишняя ячейка"
    assert "dojg_structural_delimiter_added" in {
        issue["code"] for issue in validate_worker_payload(connection, attempt, broken)
    }
    response_path = Path(task["response_path"])
    response_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ingest_response(connection, response_path)
    accept_deterministic_translations(connection, 1)
    connection.commit()

    article = connection.execute("SELECT * FROM article WHERE id=1").fetchone()
    translated = apply_article(connection, 1, article)

    assert japanese_text_signature(translated[5][0]) == japanese_text_signature(SOURCE_TEXT)
    assert translated[5][0].count("\n") == SOURCE_TEXT.count("\n")
    assert translated[5][0].count("|") == SOURCE_TEXT.count("|")
    assert "The child eats bread." not in translated[5][0]
    assert "私はパンを食べる" in translated[5][0]
    connection.close()


def test_revalidation_closes_queued_split_batch_when_all_units_are_translated(tmp_path):
    connection, task = setup_run(tmp_path)
    manifest = json.loads(Path(task["request_path"]).read_text(encoding="utf-8"))
    units = connection.execute(
        """SELECT tu.* FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
        WHERE bi.batch_id=? ORDER BY bi.ordinal""", (task["batch_id"],),
    ).fetchall()
    payload = {
        "schema_version": 2,
        "batch_id": task["batch_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "translations": [
            {
                "unit_id": unit["id"], "source_sha256": unit["source_sha256"],
                "target_text": target_for(unit), "confidence": "high", "review_reason": None,
            }
            for unit in units
        ],
    }
    response_path = Path(task["response_path"])
    response_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ingest_response(connection, response_path)
    connection.execute("UPDATE batch SET state='ready' WHERE id=?", (task["batch_id"],))
    connection.execute(
        """INSERT INTO validation_issue
        (run_id,unit_id,attempt_id,validator,severity,code,details_json)
        VALUES (1,?,?, 'deterministic-v1','error','no_cyrillic','{}')""",
        (units[0]["id"], task["attempt_id"]),
    )
    connection.commit()

    assert close_superseded_batches(connection, 1) == [task["batch_id"]]
    assert connection.execute(
        "SELECT state FROM batch WHERE id=?", (task["batch_id"],),
    ).fetchone()[0] == "deterministic_validated"
    assert connection.execute(
        "SELECT COUNT(*) FROM validation_issue WHERE resolved_at IS NULL",
    ).fetchone()[0] == 0
    connection.close()
