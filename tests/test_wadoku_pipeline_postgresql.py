"""Real PostgreSQL contract test; never drops schemas or rewrites old runs."""
import json
import os
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

import pytest

from jitendex_ru.batch import claim, make_batches
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.util import canonical_json, atomic_write
from jitendex_ru.validate_response import ingest_response
from jitendex_ru.wadoku_pipeline import prepare_run, load_projection, localized_projection
from jitendex_ru.wadoku_xml import canonical_entry, yomitan_rows, label_catalog
from jitendex_ru.wadoku_windows import begin_window, finish_window, record_analysis

ENV = 'WADOKU_TEST_POSTGRES_URL'
pytestmark = pytest.mark.skipif(not os.environ.get(ENV), reason='isolated Wadoku test PostgreSQL not configured')


def test_revalidate_rejected_preserves_history_and_refuses_invalid_or_repeated_replay(tmp_path, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    import jitendex_ru.validate_response as validator
    from jitendex_ru.wadoku_windows import revalidate_rejected
    assert conninfo_to_dict(os.environ[ENV])['dbname'] == 'wadoku_rich_test'
    database = Database(Config(tmp_path, {'database': {'backend': 'postgresql', 'url_env': ENV},
                                        'project': {'work_dir': str(tmp_path), 'dist_dir': str(tmp_path)}}))
    database.migrate()
    c = database.connect()
    try:
        snapshot = c.execute("INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version) VALUES ('wadoku','fixture','fixture',?,'fixture','wadoku-xml-v3') RETURNING id", (uuid.uuid4().hex,)).fetchone()[0]
        entry = canonical_entry(ET.fromstring('<entry id="1"><form><orth>窩</orth><reading><hira>か</hira></reading></form><sense><trans langdesc="scientific"><tr>Fossa</tr></trans></sense></entry>'))
        versions = {k: 'test-v1' for k in ('labels','morphology','examples','corrections','prompt','schema')}
        run_id = prepare_run(c, snapshot_id=snapshot, entries=[(1, entry)], versions=versions, limits={})['run_id']
        make_batches(c, run_id, tmp_path/'inbox', {}, 5, 24000, 20, 16000, 49000, 60)
        c.commit()
        item = claim(c, 'fixture', tmp_path/'outbox', run_id=run_id, kind='translation', model_id='fixture', reasoning_effort='medium', transport='codex-agent')
        manifest = json.loads(Path(item['request_path']).read_text())
        unit = manifest['articles'][0]['units'][0]
        payload = {'schema_version': 2, 'batch_id': item['batch_id'], 'manifest_sha256': manifest['manifest_sha256'],
                   'translations': [{'unit_id': unit['unit_id'], 'source_sha256': unit['source_sha256'], 'target_text': 'Fossa', 'confidence': 'high', 'review_reason': None}]}
        atomic_write(Path(item['response_path']), canonical_json(payload))
        original_bytes = Path(item['response_path']).read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(validator, 'wadoku_scientific_source', lambda u: False)
            with pytest.raises(validator.ValidationFailure):
                ingest_response(c, Path(item['response_path']))
            c.commit()
            with pytest.raises(ValueError, match='still fails'):
                revalidate_rejected(c, run_id, item['attempt_id'])
            c.rollback()
        result = revalidate_rejected(c, run_id, item['attempt_id'])
        c.commit()
        assert result['new_model_call'] is False
        assert c.execute('SELECT outcome FROM attempt WHERE id=?', (item['attempt_id'],)).fetchone()[0] == 'rejected'
        assert Path(item['response_path']).read_bytes() == original_bytes
        assert c.execute('SELECT target_text FROM translation WHERE run_id=?', (run_id,)).fetchone()[0] == 'Fossa'
        with pytest.raises(ValueError, match='inactive'):
            revalidate_rejected(c, run_id, item['attempt_id'])
        c.rollback()
    finally:
        c.close()
        database.close()


def test_prepare_manifest_ingest_and_render(tmp_path, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(os.environ[ENV])['dbname'] == 'wadoku_rich_test'
    database = Database(Config(tmp_path, {'database': {'backend': 'postgresql', 'url_env': ENV},
                                        'project': {'work_dir': str(tmp_path), 'dist_dir': str(tmp_path)}}))
    database.migrate()
    connection = database.connect()
    try:
        snapshot_id = connection.execute(
            """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
            VALUES ('wadoku','fixture','fixture',?,'fixture','wadoku-xml-v3') RETURNING id""",
            (uuid.uuid4().hex,),
        ).fetchone()[0]
        value = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>語</orth><reading><hira>ご</hira>
        <accent>1</accent></reading></form><sense><trans><tr>Wort</tr></trans>
        <trans><tr>Sprache</tr></trans><expl>mit <jap>語</jap></expl></sense></entry>'''))
        versions = {k: 'test-v1' for k in ('labels','morphology','examples','corrections','prompt','schema')}
        report = prepare_run(connection, snapshot_id=snapshot_id, entries=[(1,value)], versions=versions, limits={})
        run_id = report['run_id']
        from wadoku_translate_window import resume_prepared
        assert resume_prepared(connection, run_id, 'test-v1') == {
            'run_id': run_id, 'created': False, 'resumed': True}
        with pytest.raises(ValueError, match='prompt differs'):
            resume_prepared(connection, run_id, 'different-prompt')
        with pytest.raises(ValueError, match='prepared rich'):
            resume_prepared(connection, -1, 'test-v1')
        assert report['articles'] == 1 and report['units'] == 2
        from jitendex_ru.wadoku_pipeline import localized_run
        with pytest.raises(ValueError, match='latest translation missing or unaccepted'):
            localized_run(connection, run_id, allow_unreviewed=True)
        assert not prepare_run(connection, snapshot_id=snapshot_id, entries=[(1,value)], versions=versions, limits={})['created']
        article_id = connection.execute('SELECT article_id FROM run_article WHERE run_id=?', (run_id,)).fetchone()[0]
        assert json.loads(connection.execute('SELECT raw_json FROM article WHERE id=?', (article_id,)).fetchone()[0]) == value
        batches = make_batches(connection, run_id, tmp_path / 'inbox', {}, 5, 24000, 20, 16000, 49000, 60)
        assert batches['batches_created'] == 1
        connection.commit()
        window = begin_window(connection, run_id, 1)
        assert window['ordinal'] == 1
        connection.commit()
        assert begin_window(connection, run_id, 1)['ordinal'] == 1
        attempt = claim(connection, 'fixture', tmp_path / 'outbox', run_id=run_id, kind='translation',
                        model_id='fixture-no-model-call', reasoning_effort='medium', transport='codex-agent')
        manifest = json.loads(Path(attempt['request_path']).read_text())
        assert manifest['pipeline'] == 'wadoku-xml-v3'
        targets = {}
        translations = []
        for article in manifest['articles']:
            for unit in article['units']:
                target = ['слово', 'язык'] if unit['role'] == 'glossary_set' else 'с ⟦WDXP0001⟧'
                targets[unit['unit_id']] = target
                translations.append({'unit_id': unit['unit_id'], 'source_sha256': unit['source_sha256'],
                                     'target_text': target, 'confidence': 'medium', 'review_reason': 'Проверка очереди сомнений.'})
        response = {'schema_version': 2, 'batch_id': manifest['batch_id'],
                    'manifest_sha256': manifest['manifest_sha256'], 'translations': translations}
        from jitendex_ru.validate_response import validate_worker_payload
        stored_attempt = connection.execute('SELECT * FROM attempt WHERE id=?', (attempt['attempt_id'],)).fetchone()
        reversed_response = {**response, 'translations': list(reversed(translations))}
        assert validate_worker_payload(connection, stored_attempt, reversed_response) == []
        assert validate_worker_payload(connection, stored_attempt, {**response, 'translations': translations[:1]})
        assert validate_worker_payload(connection, stored_attempt, {**response, 'translations': [translations[0], translations[0]]})
        bad_hash = {**translations[0], 'source_sha256': 'wrong'}
        assert any(issue['code'] == 'source_hash_mismatch' for issue in validate_worker_payload(
            connection, stored_attempt, {**response, 'translations': [translations[1], bad_hash]}))
        response = reversed_response
        path = Path(attempt['response_path'])
        atomic_write(path, canonical_json(response))
        ingest_response(connection, path)
        stored = connection.execute('SELECT target_text FROM translation WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
        assert len(stored) == 2 and any(row[0] == '["слово","язык"]' for row in stored)
        projection = load_projection(connection, run_id, article_id)
        rendered, block_targets = localized_projection(projection, targets)
        rows, metadata = yomitan_rows(rendered, 'ru', label_catalog(Path('terminology/wadoku-xml-labels-v2.json')), block_targets)
        assert 'слово; язык' in json.dumps(rows, ensure_ascii=False)
        assert metadata[0][2]['pitches'] == [{'position': 1}]
        report = finish_window(connection, run_id, 1)
        assert len(report['review_queue']) == 2
        assert all(row['source_text'] and row['target_text'] for row in report['review_queue'])
        with pytest.raises(ValueError, match='analysis'):
            begin_window(connection, run_id, 1)
        with pytest.raises(ValueError, match='stale'):
            record_analysis(connection, run_id, 1, {'result_sha256': 'old'})
        with pytest.raises(ValueError, match='review queue'):
            record_analysis(connection, run_id, 1, {'result_sha256': report['result_sha256'],
                'actor': 'fixture', 'summary': 'Cannot ignore uncertain results.',
                'reviewed_article_ids': [article_id], 'action': 'continue', 'blocking_issues': []})
        record_analysis(connection, run_id, 1, {'result_sha256': report['result_sha256'],
                        'actor': 'automated-fixture', 'summary': 'Fixture assertions passed, no linguistic claim.',
                        'reviewed_translation_ids': [row['id'] for row in report['review_queue']],
                        'reviewed_article_ids': [article_id], 'action': 'continue', 'blocking_issues': []})
        assert begin_window(connection, run_id, 1)['state'] == 'no_ready_batches'
        from jitendex_ru.review import make_review_batches, ingest_review
        import jitendex_ru.review as review_module
        original_envelope = review_module._article_envelope
        def legacy_order(*args, **kwargs):
            envelope = original_envelope(*args, **kwargs)
            envelope['units'].reverse()
            return envelope
        with monkeypatch.context() as patch:
            patch.setattr(review_module, '_article_envelope', legacy_order)
            made = make_review_batches(connection, run_id, tmp_path / 'review-inbox', review_prompt_sha256='fixture-review')
        assert made['review_batches_created'] == 1
        assert make_review_batches(connection, run_id, tmp_path / 'review-inbox', review_prompt_sha256='fixture-review')['review_batches_created'] == 0
        review_attempt = claim(connection, 'independent-review-fixture', tmp_path / 'review-outbox',
                               run_id=run_id, kind='review', model_id='fixture-no-model-call',
                               reasoning_effort='medium', transport='codex-agent')
        review_manifest = json.loads(Path(review_attempt['request_path']).read_text())
        assert review_manifest['review_prompt_sha256'] == 'fixture-review'
        connection.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?', ('fixture-review', review_attempt['attempt_id']))
        assert review_manifest['pipeline'] == 'wadoku-xml-v3'
        assert review_manifest['schema_version'] == 2
        assert review_manifest['articles'][0]['read_only_context']['pipeline'] == 'wadoku-xml-v3'
        review_units = [u for a in review_manifest['articles'] for u in a['units']]
        assert [u['unit_id'] for u in review_units] == [u['unit_id'] for u in projection['units']]
        review_payload = {'schema_version': 2, 'batch_id': review_manifest['batch_id'],
            'manifest_sha256': review_manifest['manifest_sha256'], 'reviews': [
                {'unit_id': u['unit_id'], 'source_sha256': u['source_sha256'],
                 'decision': 'replace' if u['role'] == 'glossary_set' else 'accept',
                 'replacement_target': ['слово', 'язык'] if u['role'] == 'glossary_set' else None,
                 'reason': 'Fixture verifies typed review ingestion, not linguistic quality.'}
                for u in review_units]}
        # Reject the last item after the first item has already written a review.
        # The driver must roll back those partial writes and release its lease.
        from wadoku_review_window import ingest_or_retry
        connection.commit()
        bad_review = json.loads(json.dumps(review_payload))
        bad_review['reviews'][-1]['source_sha256'] = 'wrong-source'
        atomic_write(Path(review_attempt['response_path']), canonical_json(bad_review))
        rejected = ingest_or_retry(connection, review_attempt)
        assert rejected['next_state'] == 'ready'
        assert connection.execute('SELECT COUNT(*) FROM review WHERE attempt_id=?',
                                  (review_attempt['attempt_id'],)).fetchone()[0] == 0
        assert connection.execute('SELECT outcome FROM attempt WHERE id=?',
                                  (review_attempt['attempt_id'],)).fetchone()[0] == 'rejected'
        connection.commit()
        review_attempt = claim(connection, 'independent-review-fixture', tmp_path / 'review-outbox',
                               run_id=run_id, kind='review', model_id='fixture-no-model-call',
                               reasoning_effort='medium', transport='codex-agent')
        assert review_attempt is not None
        connection.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?',
                           ('fixture-review', review_attempt['attempt_id']))
        atomic_write(Path(review_attempt['response_path']), canonical_json(review_payload))
        assert ingest_review(connection, Path(review_attempt['response_path']))['accepted'] == 2
        from jitendex_ru.wadoku_pipeline import localized_run
        assembled = localized_run(connection, run_id)
        assert len(assembled) == 1
        assert assembled[0] == localized_projection(projection, targets)
        from jitendex_ru.jpdb_scope import accept_deterministic_translations
        assert accept_deterministic_translations(connection, run_id)['translations_accepted'] == 0
        connection.commit()
        from wadoku_xml_dictionary import replace_target
        config = Config(tmp_path, {'database': {'backend': 'postgresql', 'url_env': ENV},
                                  'project': {'work_dir': str(tmp_path), 'dist_dir': str(tmp_path)}})
        glossary_id = next(key for key, target in targets.items() if isinstance(target, list))
        replacement = tmp_path / 'replacement.json'
        payload = {'unit_id': glossary_id, 'target_text': ['речь', 'слово'],
                   'actor': 'automated-fixture', 'reason': 'Test typed revision, not a linguistic approval.'}
        atomic_write(replacement, canonical_json(payload))
        assert replace_target(config, run_id, replacement)['applied']
        assert not replace_target(config, run_id, replacement)['applied']
        history = connection.execute('''SELECT previous_target_text,canonical_target_text
            FROM translation_canonicalization_history WHERE run_id=? AND unit_id=?''',
            (run_id, glossary_id)).fetchall()
        assert [(row[0], row[1]) for row in history] == [('["слово","язык"]', '["речь","слово"]')]
        for invalid in ('слово', [], ['слово', 'слово'], [12]):
            atomic_write(replacement, canonical_json({**payload, 'target_text': invalid}))
            with pytest.raises(ValueError, match='validation'):
                replace_target(config, run_id, replacement)
        scalar_id = next(key for key, target in targets.items() if isinstance(target, str))
        atomic_write(replacement, canonical_json({**payload, 'unit_id': scalar_id,
                                                 'target_text': 'вместе с ⟦WDXP0001⟧'}))
        assert replace_target(config, run_id, replacement)['applied']
        atomic_write(replacement, canonical_json({**payload, 'unit_id': scalar_id,
                                                 'target_text': 'без защищённого фрагмента'}))
        with pytest.raises(ValueError, match='validation'):
            replace_target(config, run_id, replacement)
        made = make_review_batches(connection, run_id, tmp_path/'recheck-inbox',
                                   review_prompt_sha256='fixture-recheck', recheck=True)
        assert made['review_batches_created'] == 1
        task = claim(connection, 'second-independent-review-fixture', tmp_path/'recheck-outbox',
                     run_id=run_id, kind='review', model_id='fixture-no-model-call',
                     reasoning_effort='medium', transport='codex-agent')
        connection.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?', ('fixture-recheck', task['attempt_id']))
        manifest2 = json.loads(Path(task['request_path']).read_text())
        assert manifest2['recheck'] is True
        units2 = [u for a in manifest2['articles'] for u in a['units']]
        assert len(units2) == 2
        response2 = {'schema_version': 2, 'batch_id': manifest2['batch_id'],
            'manifest_sha256': manifest2['manifest_sha256'], 'reviews': [
                {'unit_id': u['unit_id'], 'source_sha256': u['source_sha256'],
                 'decision': 'needs_adjudication' if u['role']=='glossary_set' else 'accept',
                 'replacement_target': None, 'reason': 'Fixture checks that a renewed uncertainty revokes acceptance.'}
                for u in units2]}
        atomic_write(Path(task['response_path']), canonical_json(response2))
        assert ingest_review(connection, Path(task['response_path']))['needs_adjudication'] == 1
        assert connection.execute('SELECT COUNT(*) FROM translation WHERE run_id=? AND unit_id=? AND accepted=1',
                                  (run_id, glossary_id)).fetchone()[0] == 0
        with pytest.raises(ValueError, match='latest translation missing or unaccepted'):
            localized_run(connection, run_id)
        assert len(localized_run(connection, run_id, allow_unreviewed=True)) == 1
        repeated = make_review_batches(connection, run_id, tmp_path/'unchanged-unresolved',
                                       review_prompt_sha256='fixture-recheck', unresolved_only=True)
        assert repeated == {'review_batches_created': 0, 'units': 0}
        made = make_review_batches(connection, run_id, tmp_path/'unresolved-inbox',
                                   review_prompt_sha256='fixture-resolve', unresolved_only=True)
        assert made['units'] == 1 and made['review_batches_created'] == 1
        connection.commit()
        task = claim(connection, 'third-independent-review-fixture', tmp_path/'unresolved-outbox',
                     run_id=run_id, kind='review', model_id='fixture-no-model-call',
                     reasoning_effort='medium', transport='codex-agent')
        connection.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?', ('fixture-resolve', task['attempt_id']))
        manifest = json.loads(Path(task['request_path']).read_text())
        unit = manifest['articles'][0]['units'][0]
        assert unit['unit_id'] == glossary_id and unit['prior_review_reasons']
        response = {'schema_version': 2, 'batch_id': manifest['batch_id'],
            'manifest_sha256': manifest['manifest_sha256'], 'reviews': [{
                'unit_id': unit['unit_id'], 'source_sha256': unit['source_sha256'],
                'decision': 'accept', 'replacement_target': None,
                'reason': 'Fixture verifies exception resolution, not language quality.'}]}
        atomic_write(Path(task['response_path']), canonical_json(response))
        assert ingest_review(connection, Path(task['response_path']))['accepted'] == 1
        assert len(localized_run(connection, run_id)) == 1
        with pytest.raises(ValueError, match='outside the run'):
            make_review_batches(connection, run_id, tmp_path/'subset', recheck=True,
                                review_prompt_sha256='subset-test', article_ids=[article_id, 999999999])
        with pytest.raises(ValueError, match='explicit recheck'):
            make_review_batches(connection, run_id, tmp_path/'subset',
                                review_prompt_sha256='subset-test', article_ids=[article_id])
        subset = make_review_batches(connection, run_id, tmp_path/'subset', recheck=True,
                                    review_prompt_sha256='subset-test', article_ids=[article_id])
        assert subset == {'review_batches_created': 1, 'units': 2}
        assert make_review_batches(connection, run_id, tmp_path/'subset', recheck=True,
                                   review_prompt_sha256='subset-test', article_ids=[article_id])['review_batches_created'] == 0
        assert connection.execute("SELECT count(*) FROM validation_issue WHERE run_id=? AND code='needs_adjudication' AND resolved_at IS NULL",
                                  (run_id,)).fetchone()[0] == 0
    finally:
        connection.close()
        database.close()
