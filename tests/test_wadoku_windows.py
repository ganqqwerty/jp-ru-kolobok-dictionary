from jitendex_ru.wadoku_windows import source_echo_warnings


def test_run_status_counts_latest_translation_and_missing_units():
    import sqlite3
    from jitendex_ru.wadoku_windows import run_status
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE run(id INTEGER,pipeline_version TEXT,limits_json TEXT);
        CREATE TABLE translation_unit(id TEXT,run_id INTEGER,article_id INTEGER);
        CREATE TABLE translation(id INTEGER,run_id INTEGER,unit_id TEXT,accepted INTEGER);
        CREATE TABLE batch(run_id INTEGER,kind TEXT,state TEXT);
        CREATE TABLE wadoku_window(run_id INTEGER,ordinal INTEGER,state TEXT);
        INSERT INTO run VALUES(1,'wadoku-xml-v3','{"scope_id":"whole-pilot"}');
        INSERT INTO translation_unit VALUES('a',1,1),('b',1,2),('c',1,3);
        INSERT INTO translation VALUES(1,1,'a',1),(2,1,'a',0),(3,1,'b',1);
    ''')
    status = run_status(c, 1)
    assert status['total_articles'] == 3
    assert status['translated_units'] == 2
    assert status['accepted_units'] == 1
    assert status['missing_units'] == 1
    assert status['unaccepted_units'] == 2
    c.close()


def test_source_echo_is_visible_even_at_high_confidence():
    warnings = source_echo_warnings([
        {'id': 1, 'source_text': '["Oberfläche"]',
         'target_text': '["поверхность (Oberfläche)"]', 'confidence': 'high'},
        {'id': 2, 'source_text': '["Brooklyn"]',
         'target_text': '["Бруклин (Brooklyn)"]', 'confidence': 'high'},
        {'id': 3, 'source_text': '["Wand"]', 'target_text': '["стена"]'},
    ])
    assert [w['translation_id'] for w in warnings] == [1, 2]
    assert all(w['requires_semantic_review'] for w in warnings)
    # A name is a review candidate, not an automatic rejection or text rewrite.
    assert warnings[1]['fragments'] == ['Brooklyn']


def test_source_echo_detects_untranslated_word_outside_parentheses():
    rows = [{'id': 1, 'source_text': 'trautes Heim', 'target_text': 'уютный Heim'},
            {'id': 2, 'source_text': 'Canis familiaris', 'target_text': 'Canis familiaris'},
            {'id': 3, 'source_text': 'auch ⟦WDXP0001⟧', 'target_text': 'также ⟦WDXP0001⟧'}]
    warnings = source_echo_warnings(rows)
    assert len(warnings) == 1
    assert warnings[0]['fragments'] == ['Heim']
