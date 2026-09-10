#!/usr/bin/env python3
"""PostgreSQL rich pipeline operations; no standalone SQLite path."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json, sha256_file
from jitendex_ru.wadoku_scope import inventory, store_scope
from jitendex_ru.wadoku_profile import DEFAULT_PROFILE, load_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--database-name', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    item = sub.add_parser('revalidate-rejected')
    item.add_argument('--run-id', type=int, required=True)
    item.add_argument('--attempt-id', required=True)
    item = sub.add_parser('assembly-preflight')
    item.add_argument('--run-id', type=int, required=True)
    item.add_argument('--output', type=Path)
    item = sub.add_parser('export-candidate')
    item.add_argument('--run-id', type=int, required=True)
    item.add_argument('--output', type=Path, required=True)
    item.add_argument('--license', type=Path, required=True)
    item.add_argument('--diagnostic', action='store_true',
                      help='Explicitly unreviewed full-coverage archive; not pilot approval')
    item = sub.add_parser('status')
    item.add_argument('--run-id', type=int, required=True)
    item = sub.add_parser('reorder-pending')
    item.add_argument('--run-id', type=int, required=True)
    item = sub.add_parser('inventory')
    item.add_argument('--source', type=Path, default=Path('work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml'))
    item.add_argument('--archive', type=Path, default=Path('work/wadoku-xml/source/wadoku-xml-20260705.tar.xz'))
    item.add_argument('--core', type=Path, default=Path('work/wadoku-xml/pilot/pilot-selection.json'))
    item.add_argument('--size', type=int, default=190)
    item.add_argument('--retained-scope', type=Path)
    item.add_argument('--required-owner-ids', type=int, nargs='*', default=[])
    item.add_argument('--output', type=Path, default=Path('work/wadoku-xml/pilot-v6/scope.json'))
    item = sub.add_parser('adopt-examples')
    item.add_argument('--review', type=Path, required=True)
    item.add_argument('--to-scope')
    item = sub.add_parser('adopt-ownership')
    item.add_argument('--review', type=Path, required=True)
    item.add_argument('--to-scope')
    item = sub.add_parser('classification-report')
    item.add_argument('--scope-id', required=True)
    item.add_argument('--entry-ids', type=int, nargs='+', required=True)
    item = sub.add_parser('reuse-classification')
    item.add_argument('--from-scope', required=True)
    item.add_argument('--to-scope', required=True)
    for command in ('window-report', 'analyze-window'):
        item = sub.add_parser(command)
        item.add_argument('--run-id', type=int, required=True)
        item.add_argument('--ordinal', type=int, required=True)
        if command == 'analyze-window':
            item.add_argument('--analysis', type=Path, required=True)
    args = parser.parse_args()
    config = load_profile(args.config)
    if config.db_backend != 'postgresql':
        raise ValueError('PostgreSQL required')
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname') != args.database_name:
        raise ValueError('configured database differs from explicit target')
    database = Database(config)
    started = time.monotonic()
    try:
        if args.command == 'revalidate-rejected':
            from jitendex_ru.wadoku_windows import revalidate_rejected
            connection = database.connect()
            try:
                result = revalidate_rejected(connection, args.run_id, args.attempt_id)
                connection.commit()
                print(json.dumps(result, ensure_ascii=False))
            finally:
                connection.close()
            return
        if args.command == 'assembly-preflight':
            from jitendex_ru.wadoku_assembly import assembly_preflight
            connection = database.connect()
            try:
                result = assembly_preflight(connection, config, args.run_id)
                if args.output:
                    atomic_write(args.output, canonical_json(result))
                print(json.dumps(result, ensure_ascii=False))
            finally:
                connection.close()
            return
        if args.command == 'export-candidate':
            from jitendex_ru.wadoku_assembly import export_candidate
            connection = database.connect()
            try:
                print(json.dumps(export_candidate(connection, config, args.run_id, args.output, args.license,
                                                 diagnostic=args.diagnostic),
                                 ensure_ascii=False))
            finally:
                connection.close()
            return
        if args.command == 'reorder-pending':
            from jitendex_ru.wadoku_windows import reorder_pending
            connection = database.connect()
            try:
                result = reorder_pending(connection, args.run_id)
                connection.commit()
                print(json.dumps(result, ensure_ascii=False))
            finally:
                connection.close()
            return
        if args.command not in {'classification-report', 'window-report', 'status'}:
            database.migrate()
        if args.command == 'status':
            from jitendex_ru.wadoku_windows import run_status
            connection = database.connect()
            try:
                print(json.dumps(run_status(connection, args.run_id), ensure_ascii=False, default=str))
            finally:
                connection.close()
            return
        if args.command in {'window-report', 'analyze-window'}:
            from jitendex_ru.wadoku_windows import window_report, record_analysis
            connection = database.connect()
            try:
                if args.command == 'analyze-window':
                    record_analysis(connection, args.run_id, args.ordinal,
                                    json.loads(args.analysis.read_text(encoding='utf-8')))
                    connection.commit()
                print(json.dumps(window_report(connection, args.run_id, args.ordinal),
                                 ensure_ascii=False, default=str))
            finally:
                connection.close()
            return
        if args.command == 'reuse-classification':
            from jitendex_ru.wadoku_classification import make_request, rebase_proposal
            from jitendex_ru.wadoku_quality import tree_paths
            from jitendex_ru.db import audit
            from jitendex_ru.util import sha256_bytes
            connection = database.connect()
            try:
                target = connection.execute('SELECT manifest_json FROM wadoku_scope WHERE id=?', (args.to_scope,)).fetchone()
                if not target or args.from_scope == args.to_scope:
                    raise ValueError('distinct existing target scope required')
                manifest = json.loads(target[0])
                rows = connection.execute('SELECT * FROM wadoku_scope_entry WHERE scope_id=? ORDER BY ordinal FOR UPDATE', (args.to_scope,)).fetchall()
                sources = {r['entry_id']:json.loads(r['source_json']) for r in rows}
                parents = {**manifest['parent_contexts'], **{str(k):v for k,v in sources.items()}}
                reused, skipped = [], []
                for row in rows:
                    if row['decision_json']:
                        continue
                    old = connection.execute('SELECT decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                                             (args.from_scope,row['entry_id'])).fetchone()
                    if not old or not old[0]:
                        continue
                    record = json.loads(old[0])
                    attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?', (record['attempt_id'],)).fetchone()
                    old_request = record.get('request') or json.loads(Path(attempt[0]).read_text())
                    entry = sources[row['entry_id']]
                    parent_ids = sorted({n['attributes']['id'] for n,p in tree_paths(entry['tree'])
                        if n['tag'] in {'ref','sref'} and n['attributes'].get('type')=='main' and n['attributes'].get('id')})
                    candidates = [json.loads(c[0]) for c in connection.execute('''SELECT candidate_json FROM wadoku_example_candidate
                        WHERE scope_id=? AND parent_id=? ORDER BY child_id,relation_path''', (args.to_scope,row['entry_id']))]
                    request = make_request(entry,[parents[k] for k in parent_ids],candidates,args.to_scope,old_request['prompt_sha256'])
                    try:
                        result, changes = rebase_proposal(old_request,record['result'],request)
                    except ValueError as error:
                        skipped.append({'entry_id':row['entry_id'],'reason':str(error)})
                        continue
                    derived = {**record,'method':'source-identical-scope-reuse','request':request,
                        'request_sha256':request['manifest_sha256'],'result':result,
                        'reuse':{'from_scope':args.from_scope,'original_record_sha256':sha256_bytes(canonical_json(record)),
                                 'normalizations':changes}}
                    connection.execute('UPDATE wadoku_scope_entry SET decision_json=? WHERE scope_id=? AND entry_id=?',
                                       (canonical_json(derived).decode(),args.to_scope,row['entry_id']))
                    for candidate,decision in zip(request['examples'],result['examples']):
                        connection.execute('''UPDATE wadoku_example_candidate SET decision_json=?
                            WHERE scope_id=? AND parent_id=? AND child_id=? AND relation_path=?''',
                            (canonical_json({**derived,'result':decision}).decode(),args.to_scope,row['entry_id'],candidate['child_id'],candidate['relation_path']))
                    reused.append(row['entry_id'])
                report={'from_scope':args.from_scope,'to_scope':args.to_scope,'reused':reused,'skipped':skipped}
                audit(connection,'wadoku_scope_classification_reuse','scope',args.to_scope,report)
                connection.commit()
                print(json.dumps(report,ensure_ascii=False))
            finally:
                connection.close()
        if args.command == 'classification-report':
            from jitendex_ru.wadoku_quality import tree_paths, plain
            connection = database.connect()
            try:
                for entry_id in args.entry_ids:
                    row = connection.execute('SELECT source_json,decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                                             (args.scope_id, entry_id)).fetchone()
                    if not row:
                        raise ValueError(f'entry outside scope: {entry_id}')
                    source = json.loads(row['source_json'])
                    proposal = json.loads(row['decision_json']) if row['decision_json'] else None
                    result = proposal['result'] if proposal else {}
                    candidates = {}
                    if proposal:
                        attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?', (proposal['attempt_id'],)).fetchone()
                        request = proposal.get('request') or json.loads(Path(attempt[0]).read_text())
                        candidates = {c['decision_id']: c for c in request['examples']}
                    examples = []
                    for decision in result.get('examples', []):
                        if decision['state'] not in {'accept', 'needs_review'}:
                            continue
                        candidate = candidates[decision['decision_id']]
                        examples.append({**decision, 'child_id': candidate['child_id'], 'japanese': candidate['japanese'],
                            'source': [b for b in candidate['source_blocks'] if b['xml_path'] == decision['source_path']]})
                    report = {'entry_id': entry_id, 'attempt_id': proposal['attempt_id'] if proposal else None,
                        'forms': [plain(n) for n,p in tree_paths(source['tree']) if n['tag']=='orth' and p.startswith('/entry[1]/form[1]/')],
                        'meanings': [{'path': b['xml_path'], 'text': b['source_text']} for b in source['blocks']],
                        'ownership': {k:result.get(k) for k in ('article_policy','parent_id','lookup_policy','reason','lookup_needs_review')},
                        'aliases': result.get('lookup_aliases', []), 'examples': examples,
                        'candidate_count':len(candidates), 'review_status':'proposal only; not an approval'}
                    print(json.dumps(report, ensure_ascii=False))
            finally:
                connection.close()
        if args.command in {'adopt-examples', 'adopt-ownership'}:
            from jitendex_ru.wadoku_pipeline import record_example_adoption, record_ownership_adoption
            connection = database.connect()
            try:
                operation = record_example_adoption if args.command == 'adopt-examples' else record_ownership_adoption
                review = json.loads(args.review.read_text())
                if args.to_scope:
                    from jitendex_ru.wadoku_classification import rebase_proposal
                    from jitendex_ru.util import sha256_bytes
                    old = connection.execute('SELECT decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                        (review['scope_id'],review['entry_id'])).fetchone()
                    new = connection.execute('SELECT decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                        (args.to_scope,review['entry_id'])).fetchone()
                    if not old or not old[0] or not new or not new[0]:
                        raise ValueError('both scopes need classification before review transfer')
                    old_record,new_record = json.loads(old[0]),json.loads(new[0])
                    if (old_record['attempt_id'] != review['attempt_id']
                            or new_record.get('reuse',{}).get('from_scope') != review['scope_id']
                            or new_record['reuse']['original_record_sha256'] != sha256_bytes(canonical_json(old_record))):
                        raise ValueError('review transfer source differs from reused proposal')
                    attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?',(review['attempt_id'],)).fetchone()
                    old_request = old_record.get('request') or json.loads(Path(attempt[0]).read_text())
                    new_request = new_record['request']
                    rebase_proposal(old_request,old_record['result'],new_request)
                    if args.command == 'adopt-examples':
                        mapping = {a['decision_id']:b['decision_id'] for a,b in zip(old_request['examples'],new_request['examples'])}
                        review['corrections'] = {mapping[k]:v for k,v in review.get('corrections',{}).items()}
                    review['note'] += f" Transferred from scope {review['scope_id']} after exact classification-context comparison."
                    review['scope_id'] = args.to_scope
                report = operation(connection, review)
                connection.commit()
                print(json.dumps(report, ensure_ascii=False))
            finally:
                connection.close()
        if args.command == 'inventory':
            if sha256_file(args.archive) != config.raw['source']['sha256']:
                raise ValueError('source archive hash differs')
            data = inventory(args.source, args.core, expected_sha256=config.raw['source']['xml_sha256'], size=args.size,
                             retained_scope=args.retained_scope, required_ids=args.required_owner_ids)
            connection = database.connect()
            try:
                row = connection.execute('''INSERT INTO source_snapshot(kind,version,url,sha256,local_path,
                    extractor_version,metadata_json) VALUES ('wadoku',?,?,?,?,?,?)
                    ON CONFLICT(kind,sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id''',
                    (config.raw['source']['version'], config.raw['source']['url'], config.raw['source']['sha256'],
                     str(args.archive.resolve()), 'wadoku-xml-v3', canonical_json({
                         'xml_path': str(args.source.resolve()), 'xml_sha256': config.raw['source']['xml_sha256']}).decode())).fetchone()
                report = store_scope(connection, int(row[0]), data)
                connection.commit()
            finally:
                connection.close()
            if args.output.exists() and args.output.read_bytes() != canonical_json(data) + b'\n':
                raise ValueError('refusing to overwrite a different inventory artifact')
            atomic_write(args.output, canonical_json(data) + b'\n')
            print(json.dumps({**report, 'seconds': round(time.monotonic()-started, 2)}, ensure_ascii=False))
    finally:
        database.close()


if __name__ == '__main__':
    main()
