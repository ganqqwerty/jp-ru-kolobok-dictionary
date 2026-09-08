from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from jitendex_ru.db import connect, initialize
from jitendex_ru.schema_validation import validate_archive

from jitendex_ru.wadoku_xml import (
    WADOKU_ARCHIVE_SHA256,
    canonical_entry,
    canonical_identity,
    build_rich_archive,
    decode_v1_single_block,
    extract_blocks,
    label_catalog,
    load_subentry_group_policies,
    lossless_node,
    structured_entry,
    translation_unit_id,
    localized_archive_shape,
    yomitan_inflection_rules,
)


NS = "http://www.wadoku.de/xml/entry"


def entry(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_canonical_entry_keeps_forms_senses_and_mixed_text():
    node = entry(f'''<entry xmlns="{NS}" id="52"><form>
      <orth midashigo="true">人買(い)</orth><orth>人買い</orth>
      <reading><hira>ひとかい</hira><hatsuon>[Dev]ひと･かい</hatsuon>
      <accent>0</accent><accent>3</accent></reading></form>
      <gramGrp><meishi/></gramGrp><sense><trans><tr>idealer <foreign>dealer</foreign></tr></trans>
      <def>eine <scientif>Species name</scientif></def><ref id="1" type="main"><jap>人</jap></ref></sense>
      <sense><expl>zweiter Sinn</expl></sense></entry>''')
    value = canonical_entry(node)
    assert value["entry_id"] == 52
    assert canonical_identity(value) == ("人買い", "ひとかい", 52)
    assert [block["role"] for block in value["blocks"]] == [
        "translation", "definition", "explanation",
    ]
    assert value["blocks"][0]["source_text"] == "idealer dealer"
    assert value["blocks"][0]["prompt_text"] == "idealer ⟦WDXP0001⟧"
    assert value["blocks"][0]["protected_fragments"][0]["text"] == "dealer"
    assert lossless_node(node)["children"][0]["children"][0]["tail"] == ""


def test_nested_selected_block_creates_only_outer_block():
    node = entry(f'''<entry xmlns="{NS}" id="1"><form><orth>語</orth>
      <reading><hira>ご</hira></reading></form><sense><expl>außen <descr>innen</descr></expl></sense>
      </entry>''')
    blocks = extract_blocks(node)
    assert len(blocks) == 1
    assert blocks[0]["role"] == "explanation"
    assert blocks[0]["source_text"] == "außen innen"


def test_wadoku_grammar_maps_to_yomitan_deinflection_rules():
    godan = canonical_entry(entry(f'''<entry xmlns="{NS}" id="9858285"><form>
      <orth>知る</orth><reading><hira>しる</hira></reading></form>
      <gramGrp><doushi level="5" transitivity="trans" godanrow="ra"/></gramGrp>
      <sense><trans><tr>wissen</tr></trans></sense></entry>'''))
    ichidan = canonical_entry(entry(f'''<entry xmlns="{NS}" id="2"><form>
      <orth>食べる</orth><reading><hira>たべる</hira></reading></form>
      <gramGrp><doushi level="1e" transitivity="trans"/></gramGrp>
      <sense><trans><tr>essen</tr></trans></sense></entry>'''))
    adjective = canonical_entry(entry(f'''<entry xmlns="{NS}" id="3"><form>
      <orth>高い</orth><reading><hira>たかい</hira></reading></form>
      <gramGrp><keiyoushi/></gramGrp><sense><trans><tr>hoch</tr></trans></sense></entry>'''))
    unsupported = canonical_entry(entry(f'''<entry xmlns="{NS}" id="4"><form>
      <orth>古語</orth><reading><hira>こご</hira></reading></form>
      <gramGrp><doushi level="4" transitivity="intrans"/></gramGrp>
      <sense><trans><tr>archaisch</tr></trans></sense></entry>'''))

    assert yomitan_inflection_rules(godan) == "v5"
    assert yomitan_inflection_rules(ichidan) == "v1"
    assert yomitan_inflection_rules(adjective) == "adj-i"
    assert yomitan_inflection_rules(unsupported) == ""


def test_subentry_group_policy_marks_usage_examples_as_luna_classification():
    policies = load_subentry_group_policies(
        Path("terminology/wadoku-subentry-groups-v1.json")
    )

    assert len(policies) == 26
    assert policies["VwBsp"]["prior"] == "mixed"
    assert policies["VwBsp"]["decision_mode"] == "luna_required"
    assert policies["WIdiom"]["allowed_lookup_policies"] == ["independent_direct"]
    assert policies["saseru"]["decision_mode"] == "deterministic"


def test_translation_unit_id_is_stable_and_sensitive_to_path():
    block = {
        "role": "translation", "xml_path": "/entry[1]/sense[1]/trans[1]/tr[1]",
        "prompt_text": "Wort", "has_translatable_text": True,
    }
    first = translation_unit_id(WADOKU_ARCHIVE_SHA256, 15, block)
    second = translation_unit_id(WADOKU_ARCHIVE_SHA256, 15, block)
    assert first == second
    assert first.startswith("wdx-") and len(first) == 36
    changed = dict(block, xml_path="/entry[1]/sense[2]/trans[1]/tr[1]")
    assert translation_unit_id(WADOKU_ARCHIVE_SHA256, 15, changed) != first


def test_sqlite_migration_0010_supports_wadoku_run_and_reuse(tmp_path: Path):
    database = tmp_path / "migration.sqlite3"
    initialize(database)
    with connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0] == 10
        connection.execute(
            """INSERT INTO source_snapshot
            (kind,version,url,sha256,local_path,extractor_version)
            VALUES ('wadoku','2026-07-05','u','s','p','wadoku-xml-v2')"""
        )
        snapshot_id = connection.execute(
            "SELECT id FROM source_snapshot WHERE kind='wadoku'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO run
            (dictionary_snapshot_id,selection_sha256,extractor_version,prompt_sha256,
             review_prompt_sha256,terminology_sha256,limits_json,pipeline_version,
             run_identity_sha256) VALUES (?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, "selection", "wadoku-xml-v2", "prompt", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "terms", "{}", "wadoku-xml-v2", "a" * 64),
        )
        row = connection.execute(
            "SELECT jitendex_snapshot_id,kaishi_snapshot_id,dictionary_snapshot_id FROM run"
        ).fetchone()
        assert tuple(row) == (None, None, snapshot_id)


def test_sqlite_migration_0010_backfills_legacy_acceptance(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    initialize(database)
    with connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(translation)")}
        assert {"accepted_at", "acceptance_method"} <= columns
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(attempt)")}
        assert "dispatched_at" in attempt_columns
        run_article_columns = {row[1] for row in connection.execute("PRAGMA table_info(run_article)")}
        assert "prepared_at" in run_article_columns


def test_structured_russian_overlay_keeps_protected_fragment_shape():
    node = entry(f'''<entry xmlns="{NS}" id="73"><form><orth>バーン</orth>
      <reading><hira>ばーん</hira><hatsuon>ばーん</hatsuon><accent>1</accent></reading></form>
      <gramGrp><meishi/></gramGrp><sense><trans><tr>Edward <foreign>Burne</foreign></tr></trans>
      <def>engl. Maler</def></sense></entry>''')
    value = canonical_entry(node)
    labels = label_catalog(Path("terminology/wadoku-xml-labels-v1.json"))
    de = structured_entry(value, "de", labels)
    ru = structured_entry(value, "ru", labels, {0: "Эдвард ⟦WDXP0001⟧", 1: "английский художник"})
    assert "Burne" in str(de) and "Burne" in str(ru)
    assert "engl. Maler" in str(de)
    assert "английский художник" in str(ru)


def test_rich_archive_is_deterministic_and_matches_four_schemas(tmp_path: Path):
    node = entry(f'''<entry xmlns="{NS}" id="15"><form><orth>インスリン</orth>
      <reading><hira>いんすりん</hira><hatsuon>いんすりん</hatsuon><accent>0</accent></reading></form>
      <gramGrp><meishi/></gramGrp><sense><trans><tr>Insulin</tr></trans></sense></entry>''')
    value = canonical_entry(node)
    labels = label_catalog(Path("terminology/wadoku-xml-labels-v1.json"))
    outputs = [tmp_path / "one.zip", tmp_path / "two.zip"]
    reports = [
        build_rich_archive(
            iter([(value, None)]), output, language="de", labels=labels,
            license_text=b"license\n", title="Wadoku test", revision="test-1",
            source_url="https://www.wadoku.de/", source_sha256="a" * 64,
            export_audit_id=1,
        )
        for output in outputs
    ]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert reports[0]["zip_sha256"] == reports[1]["zip_sha256"]
    schema = Path("schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162")
    assert validate_archive(outputs[0], schema) == {
        "schema_validated_banks": 3, "term_banks": 1,
        "term_meta_banks": 1, "tag_banks": 1,
    }


def test_v1_reuse_accepts_only_exact_single_string_shapes():
    assert decode_v1_single_block('["Wort"]', '["слово"]') == ("Wort", "слово")
    assert decode_v1_single_block('["Wort", "Begriff"]', '["слово"]') is None
    assert decode_v1_single_block('["Wort"]', '["слово", "понятие"]') is None
    assert decode_v1_single_block('["Zeile\\nZwei"]', '["строка"]') is None
    assert decode_v1_single_block('["Wort"]', '[""]') is None


def test_archive_shape_ignores_localized_language_and_heading_text():
    de = {"tag": "div", "content": [
        {"tag": "span", "content": "Übersetzung: ", "style": {"fontWeight": "bold"}},
        {"tag": "span", "content": "Insulin", "lang": "de"},
        {"tag": "span", "content": "insulin"},
    ]}
    ru = {"tag": "div", "content": [
        {"tag": "span", "content": "Перевод: ", "style": {"fontWeight": "bold"}},
        {"tag": "span", "content": "инсулин", "lang": "ru"},
        {"tag": "span", "content": "insulin"},
    ]}
    assert localized_archive_shape(de) == localized_archive_shape(ru)
    ru["content"][2]["content"] = "different protected text"
    assert localized_archive_shape(de) != localized_archive_shape(ru)


def test_archive_shape_ignores_localized_text_span_positions():
    de = [
        {"tag": "span", "content": "usually ", "lang": "de"},
        {"tag": "span", "content": "carbon black"},
        {"tag": "span", "content": " pronounced", "lang": "de"},
    ]
    ru = [
        {"tag": "span", "content": "обычно произносится ", "lang": "ru"},
        {"tag": "span", "content": "carbon black"},
    ]
    assert localized_archive_shape(de) == localized_archive_shape(ru)
