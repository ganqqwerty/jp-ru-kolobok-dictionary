"""Persistent bounded batch windows; semantic analysis belongs to the orchestrator."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .db import audit
from .util import canonical_json, sha256_bytes
from .wadoku_pipeline import PIPELINE


def revalidate_rejected(connection: Any, run_id: int, attempt_id: str) -> dict[str, Any]:
    """Replay unchanged rejected output under current validation; keep original history."""
    from .batch import claim
    from .db import record_attempt_usage
    from .util import atomic_write, sha256_file
    from .validate_response import validate_worker_payload, ingest_response
    connection.execute('SELECT id FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    old = connection.execute('SELECT * FROM attempt WHERE id=?', (attempt_id,)).fetchone()
    if not old or old['outcome'] != 'rejected':
        raise ValueError('revalidation requires a rejected attempt')
    batch = connection.execute('SELECT * FROM batch WHERE id=? FOR UPDATE', (old['batch_id'],)).fetchone()
    run = connection.execute('SELECT pipeline_version FROM run WHERE id=?', (run_id,)).fetchone()
    if (not run or run[0] != PIPELINE or batch['run_id'] != run_id
            or batch['kind'] != 'translation' or batch['state'] not in {'blocked', 'retryable'}):
        raise ValueError('revalidation requires an inactive rich translation batch in this run')
    if connection.execute('SELECT 1 FROM translation t JOIN batch_item bi ON bi.unit_id=t.unit_id WHERE bi.batch_id=?',
                          (batch['id'],)).fetchone():
        raise ValueError('revalidation cannot replace existing translations')
    path = Path(old['response_path'])
    data = path.read_bytes()
    issues = validate_worker_payload(connection, old, json.loads(data))
    if issues:
        raise ValueError(f'saved response still fails validation: {issues}')
    connection.execute("UPDATE batch SET state='ready' WHERE id=?", (batch['id'],))
    item = claim(connection, 'wadoku-validator-replay', path.parent, run_id=run_id,
                 kind='translation', model_id=old['model'], reasoning_effort=old['reasoning_effort'],
                 transport=old['transport'], batch_id=batch['id'])
    if not item:
        raise ValueError('revalidation could not claim exact batch')
    atomic_write(Path(item['response_path']), data)
    result = ingest_response(connection, Path(item['response_path']))
    record_attempt_usage(connection, item['attempt_id'], effective_model_id=old['model'],
                         reasoning_effort=old['reasoning_effort'], transport=old['transport'],
                         input_tokens=0, cached_input_tokens=0, output_tokens=0, total_tokens=0,
                         finish_reason='validator_replay', status_reason='No model call; usage belongs to original attempt')
    audit(connection, 'wadoku_validator_replay', 'attempt', item['attempt_id'], {
        'original_attempt_id': attempt_id, 'response_sha256': sha256_bytes(data),
        'validator_sha256': sha256_file(Path(__file__).with_name('validate_response.py')),
        'new_model_call': False, 'original_history_preserved': True})
    return {'attempt_id': item['attempt_id'], 'original_attempt_id': attempt_id,
            'new_model_call': False, 'ingest': result}


def reorder_pending(connection: Any, run_id: int) -> dict[str, Any]:
    """Replace unattempted, unassigned requests; preserve old files and all units."""
    from .batch import _manifest
    from .util import atomic_write
    run = connection.execute('SELECT pipeline_version FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    if not run or run[0] != PIPELINE:
        raise ValueError('reordering requires a rich Wadoku run')
    assigned = set()
    for row in connection.execute('SELECT batch_ids_json FROM wadoku_window WHERE run_id=?', (run_id,)):
        assigned.update(json.loads(row[0]))
    changed = []
    for batch in connection.execute("SELECT * FROM batch WHERE run_id=? AND kind='translation' AND state='ready' ORDER BY id FOR UPDATE", (run_id,)).fetchall():
        if batch['id'] in assigned:
            continue
        if connection.execute('SELECT 1 FROM attempt WHERE batch_id=?', (batch['id'],)).fetchone():
            continue
        original = json.loads(Path(batch['manifest_path']).read_text())
        if original['manifest_sha256'] != batch['manifest_sha256']:
            raise ValueError('stored manifest hash differs')
        _, original_bytes = _manifest(original['batch_id'], original['articles'], original.get('terminology', {}))
        if json.loads(original_bytes) != original:
            raise ValueError('manifest content differs from canonical hash')
        articles = json.loads(json.dumps(original['articles']))
        for article in articles:
            article_id = int(article['article_id'].removeprefix('a-'))
            row = connection.execute('SELECT projection_json FROM wadoku_projection WHERE run_id=? AND article_id=?', (run_id, article_id)).fetchone()
            projection = json.loads(row[0])
            order = {u['unit_id']: i for i, u in enumerate(projection['units'])}
            article['units'].sort(key=lambda u: order[u['unit_id']])
        if articles == original['articles']:
            continue
        child_id = 'b-' + sha256_bytes(canonical_json({'parent': batch['id'], 'operation': 'source-order-v1'}))[:24]
        manifest, data = _manifest(child_id, articles, original.get('terminology', {}))
        path = Path(batch['manifest_path']).with_name(child_id + '.json')
        if path.exists() and path.read_bytes() != data + b'\n':
            raise ValueError('replacement manifest already differs')
        atomic_write(path, data + b'\n')
        units = [u for a in articles for u in a['units']]
        connection.execute('''INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,article_count,unit_count,manifest_path)
            VALUES (?,?,?,?,?,?,?,?)''', (child_id, run_id, 'translation', manifest['manifest_sha256'], len(data), len(articles), len(units), str(path)))
        connection.executemany('INSERT INTO batch_item(batch_id,unit_id,ordinal) VALUES (?,?,?)',
                               [(child_id,u['unit_id'],i) for i,u in enumerate(units)])
        connection.execute("UPDATE batch SET state='blocked' WHERE id=?", (batch['id'],))
        record = {'parent': batch['id'], 'replacement': child_id, 'reason': 'numeric source-unit order', 'units': len(units)}
        audit(connection, 'wadoku_request_reordered', 'batch', batch['id'], record)
        changed.append(record)
    return {'run_id': run_id, 'replacements': changed, 'changed_batches': len(changed)}


def source_echo_warnings(translations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag source words left in Russian text; do not reject names or etymons."""
    warnings = []
    for row in translations:
        source = row['source_text'].casefold()
        fragments = re.findall(r'\(([^()]+)\)', row['target_text'])
        matches = [s for s in fragments if re.search(r'[A-Za-zÀ-ž]', s)
                   and s.strip().casefold() in source]
        if re.search(r'[А-Яа-яЁё]', row['target_text']):
            source_words = set(re.findall(r'\b[A-Za-zÀ-ž]{3,}\b', source))
            matches.extend(word for word in re.findall(r'\b[A-Za-zÀ-ž]{3,}\b', row['target_text'])
                           if word.casefold() in source_words)
        matches = list(dict.fromkeys(matches))
        if matches:
            warnings.append({'code': 'possible_source_echo', 'translation_id': row['id'],
                             'fragments': matches, 'requires_semantic_review': True})
    return warnings


def invalidate_accepted_alignment_failures(
    connection: Any, run_id: int, attempt_ids: list[str],
) -> dict[str, Any]:
    """Reopen exact accepted attempts that fail the current alignment gate."""
    from .validate_response import validate_worker_payload

    if not attempt_ids or len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError('supply distinct accepted attempt IDs')
    connection.execute('SELECT id FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    repaired = []
    for attempt_id in attempt_ids:
        attempt = connection.execute('SELECT * FROM attempt WHERE id=?', (attempt_id,)).fetchone()
        if not attempt or attempt['outcome'] != 'accepted':
            raise ValueError(f'alignment repair requires an accepted attempt: {attempt_id}')
        batch = connection.execute('SELECT * FROM batch WHERE id=? FOR UPDATE', (attempt['batch_id'],)).fetchone()
        if (not batch or batch['run_id'] != run_id or batch['kind'] != 'translation'
                or batch['state'] != 'deterministic_validated'):
            raise ValueError(f'alignment repair requires the active validated batch: {attempt_id}')
        response_path = Path(attempt['response_path'])
        payload = json.loads(response_path.read_text(encoding='utf-8'))
        issues = validate_worker_payload(connection, attempt, payload)
        alignment = [i for i in issues if i['code'] == 'wadoku_cross_article_duplicate_target']
        if not alignment or len(alignment) != len(issues):
            raise ValueError(f'attempt does not fail only the alignment gate: {attempt_id}: {issues}')
        translations = connection.execute(
            'SELECT id,accepted FROM translation WHERE attempt_id=? ORDER BY id', (attempt_id,),
        ).fetchall()
        expected = int(batch['unit_count'])
        if len(translations) != expected or any(row['accepted'] for row in translations):
            raise ValueError(f'alignment repair found reviewed or incomplete output: {attempt_id}')
        marks = ','.join('?' for _ in translations)
        if translations and connection.execute(
            f'SELECT 1 FROM review WHERE translation_id IN ({marks}) LIMIT 1',
            tuple(row['id'] for row in translations),
        ).fetchone():
            raise ValueError(f'alignment repair cannot replace reviewed output: {attempt_id}')
        connection.execute('DELETE FROM translation WHERE attempt_id=?', (attempt_id,))
        connection.execute(
            """UPDATE translation_unit SET status='ready' WHERE id IN
            (SELECT unit_id FROM batch_item WHERE batch_id=?)""", (batch['id'],),
        )
        error_json = canonical_json(alignment).decode()
        connection.execute(
            "UPDATE attempt SET outcome='rejected',error_json=? WHERE id=?",
            (error_json, attempt_id),
        )
        connection.execute(
            """UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL
            WHERE id=?""", (batch['id'],),
        )
        for issue in alignment:
            connection.execute(
                """INSERT INTO validation_issue
                (run_id,unit_id,attempt_id,validator,severity,code,details_json)
                VALUES (?,NULL,?,'deterministic-alignment-v1','error',?,?)""",
                (run_id, attempt_id, issue['code'], canonical_json(issue).decode()),
            )
        connection.execute(
            """UPDATE wadoku_window SET state='repair',analysis_json=NULL
            WHERE run_id=? AND state<>'continue' AND batch_ids_json LIKE ?""",
            (run_id, f'%"{batch["id"]}"%'),
        )
        record = {
            'attempt_id': attempt_id, 'batch_id': batch['id'], 'translations_removed': expected,
            'issues': alignment, 'response_path': str(response_path),
        }
        audit(connection, 'wadoku_alignment_invalidation', 'attempt', attempt_id, record)
        repaired.append(record)
    return {'run_id': run_id, 'invalidated_attempts': repaired}


def begin_window(connection: Any, run_id: int, count: int) -> dict[str, Any]:
    if count < 1:
        raise ValueError('window batch count must be positive')
    run = connection.execute('SELECT pipeline_version FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    if not run or run[0] != PIPELINE:
        raise ValueError('window requires a rich Wadoku run')
    previous = connection.execute('SELECT * FROM wadoku_window WHERE run_id=? ORDER BY ordinal DESC LIMIT 1', (run_id,)).fetchone()
    if previous and previous['state'] != 'continue':
        if previous['state'] == 'running':
            return dict(previous)
        raise ValueError('previous window requires analysis or repair')
    # Accepted split children never become a new root window.
    assigned = set()
    for row in connection.execute('SELECT batch_ids_json FROM wadoku_window WHERE run_id=?', (run_id,)):
        assigned.update(json.loads(row[0]))
    candidates = connection.execute("SELECT id FROM batch WHERE run_id=? AND kind='translation' AND state='ready' ORDER BY created_at,id", (run_id,)).fetchall()
    roots = [row[0] for row in candidates if row[0] not in assigned][:count]
    if not roots:
        return {'run_id': run_id, 'state': 'no_ready_batches'}
    ordinal = int(previous['ordinal']) + 1 if previous else 1
    connection.execute('INSERT INTO wadoku_window(run_id,ordinal,state,batch_ids_json) VALUES (?, ?, ?, ?)',
                       (run_id, ordinal, 'running', canonical_json(roots).decode()))
    audit(connection, 'wadoku_window_begin', 'run', run_id, {'ordinal': ordinal, 'root_batches': roots})
    return {'run_id': run_id, 'ordinal': ordinal, 'state': 'running', 'batch_ids_json': canonical_json(roots).decode()}


def run_status(connection: Any, run_id: int) -> dict[str, Any]:
    """Read whole-run coverage, not just the latest successful window."""
    run = connection.execute('SELECT pipeline_version,limits_json FROM run WHERE id=?', (run_id,)).fetchone()
    if not run or run['pipeline_version'] != PIPELINE:
        raise ValueError('status requires a rich Wadoku run')
    coverage = connection.execute('''SELECT COUNT(*) AS total_units,
        COUNT(DISTINCT tu.article_id) AS total_articles,
        COUNT(t.id) AS translated_units,
        COALESCE(SUM(CASE WHEN t.accepted=1 THEN 1 ELSE 0 END),0) AS accepted_units
        FROM translation_unit tu LEFT JOIN translation t ON t.id=(
          SELECT MAX(t2.id) FROM translation t2 WHERE t2.run_id=tu.run_id AND t2.unit_id=tu.id)
        WHERE tu.run_id=?''', (run_id,)).fetchone()
    result = {'run_id': run_id, **dict(coverage)}
    result['missing_units'] = result['total_units'] - result['translated_units']
    result['unaccepted_units'] = result['total_units'] - result['accepted_units']
    result['scope_id'] = json.loads(run['limits_json']).get('scope_id')
    result['batches'] = [dict(r) for r in connection.execute(
        'SELECT kind,state,COUNT(*) AS count FROM batch WHERE run_id=? GROUP BY kind,state ORDER BY kind,state', (run_id,))]
    result['windows'] = [dict(r) for r in connection.execute(
        'SELECT ordinal,state FROM wadoku_window WHERE run_id=? ORDER BY ordinal', (run_id,))]
    result['note'] = 'Acceptance is not proof of grouping, export or Yomitan quality.'
    return result


def window_batches(connection: Any, run_id: int, ordinal: int) -> list[Any]:
    row = connection.execute('SELECT batch_ids_json FROM wadoku_window WHERE run_id=? AND ordinal=?', (run_id, ordinal)).fetchone()
    if not row:
        raise ValueError('unknown window')
    roots = json.loads(row[0])
    marks = ','.join('?' for _ in roots)
    # Membership is by exact frozen root units, including retry/split descendants.
    return connection.execute(f'''WITH scope AS (
        SELECT DISTINCT unit_id FROM batch_item WHERE batch_id IN ({marks})
    ) SELECT b.* FROM batch b WHERE b.run_id=? AND b.kind='translation'
    AND EXISTS (SELECT 1 FROM batch_item bi JOIN scope s ON s.unit_id=bi.unit_id WHERE bi.batch_id=b.id)
    AND NOT EXISTS (SELECT 1 FROM batch_item bi WHERE bi.batch_id=b.id
                    AND bi.unit_id NOT IN (SELECT unit_id FROM scope))
    ORDER BY b.created_at,b.id''', (*roots, run_id)).fetchall()


def window_report(connection: Any, run_id: int, ordinal: int) -> dict[str, Any]:
    batches = window_batches(connection, run_id, ordinal)
    ids = [row['id'] for row in batches]
    marks = ','.join('?' for _ in ids)
    units = connection.execute(f'''SELECT DISTINCT tu.id,tu.article_id,tu.role FROM batch_item bi
        JOIN translation_unit tu ON tu.id=bi.unit_id WHERE bi.batch_id IN ({marks}) ORDER BY tu.id''', tuple(ids)).fetchall()
    translations = connection.execute(f'''SELECT DISTINCT ON (t.unit_id) t.id,t.unit_id,t.target_sha256,t.confidence,t.review_reason,
        t.target_text,tu.source_text,tu.article_id,tu.role,a.expression
        FROM translation t JOIN translation_unit tu ON tu.id=t.unit_id
        JOIN article a ON a.id=tu.article_id WHERE t.run_id=? AND t.unit_id IN (
        SELECT unit_id FROM batch_item WHERE batch_id IN ({marks})) ORDER BY t.unit_id,t.id DESC''', (run_id, *ids)).fetchall()
    attempts = connection.execute(f'''SELECT id,batch_id,outcome,input_tokens,output_tokens,total_tokens,error_json
        FROM attempt WHERE batch_id IN ({marks}) ORDER BY created_at,id''', tuple(ids)).fetchall()
    covered = {row['unit_id'] for row in translations}
    report = {'run_id': run_id, 'ordinal': ordinal, 'articles': len({row['article_id'] for row in units}),
              'article_ids': sorted({row['article_id'] for row in units}),
              'units': len(units), 'translated_units': len(covered),
              'missing_units': sorted({row['id'] for row in units} - covered),
              'batches': [{'id': row['id'], 'state': row['state']} for row in batches],
              'attempts': [dict(row) for row in attempts],
              'known_tokens': sum(row['total_tokens'] or 0 for row in attempts),
              'unknown_usage_attempts': [row['id'] for row in attempts if row['total_tokens'] is None],
              'translations': [dict(row) for row in translations],
              'review_queue': [dict(row) for row in translations if row['confidence'] != 'high'],
              'source_echo_warnings': source_echo_warnings([dict(row) for row in translations])}
    report['result_sha256'] = sha256_bytes(canonical_json([report, [dict(row) for row in translations]]))
    return report


def finish_window(connection: Any, run_id: int, ordinal: int) -> dict[str, Any]:
    connection.execute('SELECT id FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    report = window_report(connection, run_id, ordinal)
    if any(row['state'] in {'ready', 'leased', 'retryable'} for row in report['batches']):
        raise ValueError('window still has live or retryable work')
    updated = connection.execute("UPDATE wadoku_window SET state='awaiting_analysis',report_json=? WHERE run_id=? AND ordinal=? AND state='running'",
                                (canonical_json(report).decode(), run_id, ordinal)).rowcount
    if updated != 1:
        raise ValueError('window is not running')
    return report


def record_analysis(connection: Any, run_id: int, ordinal: int, analysis: dict[str, Any]) -> None:
    """No human approval field: record an actual source-bound orchestrator analysis."""
    connection.execute('SELECT id FROM run WHERE id=? FOR UPDATE', (run_id,)).fetchone()
    report = window_report(connection, run_id, ordinal)
    if analysis.get('result_sha256') != report['result_sha256']:
        raise ValueError('analysis is stale')
    if analysis.get('action') not in {'continue', 'repair', 'stopped'}:
        raise ValueError('invalid analysis action')
    if not all(analysis.get(key) for key in ('actor', 'summary', 'reviewed_article_ids')):
        raise ValueError('analysis must identify its actual sample and conclusion')
    sample = analysis['reviewed_article_ids']
    if not isinstance(sample, list) or not set(sample) <= set(report['article_ids']):
        raise ValueError('analysis sample is outside the window')
    if any(row['state'] in {'ready', 'leased', 'retryable'} for row in report['batches']):
        raise ValueError('window has unfinished work')
    if analysis['action'] == 'continue' and (report['missing_units'] or analysis.get('blocking_issues')):
        raise ValueError('cannot continue with unresolved window defects')
    if analysis['action'] == 'continue':
        reviewed = analysis.get('reviewed_translation_ids', [])
        if not isinstance(reviewed, list) or not all(isinstance(i, int) for i in reviewed):
            raise ValueError('reviewed_translation_ids must be integer IDs')
        if not {row['id'] for row in report['review_queue']} <= set(reviewed):
            raise ValueError('review queue requires explicit analysis of uncertain translations')
    updated = connection.execute("UPDATE wadoku_window SET state=?,analysis_json=? WHERE run_id=? AND ordinal=? AND state IN ('awaiting_analysis','repair')",
                                (analysis['action'], canonical_json(analysis).decode(), run_id, ordinal)).rowcount
    if updated != 1:
        raise ValueError('window is not awaiting analysis')
    audit(connection, 'wadoku_window_analysis', 'run', run_id, {'ordinal': ordinal, **analysis})
