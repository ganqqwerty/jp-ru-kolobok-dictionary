#!/usr/bin/env python3
"""Run at most N source-classification tasks with shared Luna CLI/attempt storage."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from jitendex_ru.batch import claim
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit, record_attempt_usage
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes
from jitendex_ru.wadoku_quality import tree_paths
from jitendex_ru.wadoku_classification import make_request, response_schema, validate_decision, normalize_decision, source_context, parse_response, REQUEST_VERSION, RESPONSE_SCHEMA_VERSION
from run_codex_batches import dispatch_one
from jitendex_ru.wadoku_profile import DEFAULT_PROFILE, load_profile, resolve_prompt
from jitendex_ru.wadoku_retry import retry_prompt, save_runtime_prompt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--scope-id', required=True)
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--concurrency', type=int, default=5)
    parser.add_argument('--context-budget', type=int, default=96000)
    parser.add_argument('--entry-ids', nargs='*', type=int)
    parser.add_argument('--prompt', type=Path)
    parser.add_argument('--work-dir', type=Path, default=Path('work/wadoku-xml/pilot-v6/classification'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--revalidate-entry', type=int)
    args=parser.parse_args()
    if not 1<=args.limit<=10:
        raise ValueError('choose a bounded window of 1–10 tasks')
    if not 1<=args.concurrency<=5:
        raise ValueError('pilot concurrency must be 1–5')
    if not 1<=args.context_budget<=128000:
        raise ValueError('pilot context budget must be at most 128000')
    if args.context_budget>96000:
        cache=json.loads((Path.home()/'.codex/models_cache.json').read_text())
        model=next(item for item in cache['models'] if item['slug']=='gpt-5.6-luna')
        effective=model['context_window']*model['effective_context_window_percent']//100
        if args.context_budget>effective//2:
            raise ValueError('requested budget exceeds half the locally configured effective model context')
    config=load_profile(args.config)
    from psycopg.conninfo import conninfo_to_dict
    if config.db_backend!='postgresql' or conninfo_to_dict(config.database_url()).get('dbname')!='wadoku_rich_pilot':
        raise ValueError('this pilot command requires wadoku_rich_pilot PostgreSQL')
    args.prompt = resolve_prompt(config, 'classification', args.prompt)
    prompt=args.prompt.read_text()
    prompt_hash=sha256_bytes(prompt.encode())
    database=Database(config)
    connection=database.connect()
    executor=ThreadPoolExecutor(max_workers=args.concurrency)
    try:
        scope=connection.execute('SELECT * FROM wadoku_scope WHERE id=?',(args.scope_id,)).fetchone()
        if not scope:
            raise ValueError('unknown scope')
        manifest=json.loads(scope['manifest_json'])
        rows=connection.execute('SELECT * FROM wadoku_scope_entry WHERE scope_id=? ORDER BY ordinal',(args.scope_id,)).fetchall()
        by_id={row['entry_id']:json.loads(row['source_json']) for row in rows}
        parents={**manifest['parent_contexts'],**{str(k):v for k,v in by_id.items()}}
        if args.revalidate_entry is not None:
            entry_id=args.revalidate_entry
            if entry_id not in by_id:
                raise ValueError('entry outside frozen scope')
            recovered=None
            for attempt in connection.execute("SELECT a.* FROM attempt a JOIN batch b ON b.id=a.batch_id WHERE b.kind='classification' AND a.outcome='rejected' ORDER BY a.created_at DESC").fetchall():
                request=json.loads(Path(attempt['request_path']).read_text())
                if request.get('scope_id')!=args.scope_id or request['entry']['entry_id']!=entry_id:
                    continue
                if (request['entry'].get('tree')!=by_id[entry_id]['tree']
                        and request['entry']!=source_context(by_id[entry_id])):
                    raise ValueError('saved classification source differs')
                raw=parse_response(Path(attempt['response_path']).read_text())
                payload,changes=normalize_decision(raw,request)
                errors=validate_decision(request,payload)
                if errors:
                    raise ValueError(f'saved response still fails: {errors}')
                record={'method':'luna-proposal-revalidated','attempt_id':attempt['id'],
                        'prompt_sha256':attempt['prompt_sha256'],'request_sha256':request['manifest_sha256'],
                        'normalizations':changes,'result':payload}
                if connection.execute('UPDATE wadoku_scope_entry SET decision_json=? WHERE scope_id=? AND entry_id=? AND decision_json IS NULL',
                    (canonical_json(record).decode(),args.scope_id,entry_id)).rowcount!=1:
                    raise ValueError('entry already has a proposal')
                for candidate,decision in zip(request['examples'],payload['examples']):
                    connection.execute('''UPDATE wadoku_example_candidate SET decision_json=? WHERE scope_id=? AND parent_id=? AND child_id=? AND relation_path=?''',
                        (canonical_json({**record,'result':decision}).decode(),args.scope_id,candidate['parent_id'],candidate['child_id'],candidate['relation_path']))
                audit(connection,'wadoku_classification_revalidation','attempt',attempt['id'],record)
                connection.commit()
                recovered={'entry_id':entry_id,'attempt_id':attempt['id'],'normalizations':changes,'new_luna_calls':0}
                break
            if recovered is None:
                raise ValueError('no rejected saved attempt for this scope entry')
            print(json.dumps(recovered,ensure_ascii=False))
            return
        pending=[row for row in rows if row['decision_json'] is None and (not args.entry_ids or row['entry_id'] in args.entry_ids)]
        requests=[]
        for row in pending:
            entry=by_id[row['entry_id']]
            parent_ids={node['attributes']['id'] for node,_ in tree_paths(entry['tree'])
                        if node['tag'] in {'ref','sref'} and node['attributes'].get('type')=='main' and node['attributes'].get('id')}
            if parent_ids-set(parents):
                raise ValueError('missing parent context')
            candidates=[json.loads(c[0]) for c in connection.execute(
                'SELECT candidate_json FROM wadoku_example_candidate WHERE scope_id=? AND parent_id=? ORDER BY child_id,relation_path',
                (args.scope_id,row['entry_id']))]
            request=make_request(entry,[parents[k] for k in sorted(parent_ids)],candidates,args.scope_id,prompt_hash)
            previous_count=connection.execute('SELECT count(*) FROM attempt WHERE batch_id=?', (request['batch_id'],)).fetchone()[0]
            if previous_count >= 3:
                continue  # Exhausted work must not starve untouched entries in later windows.
            schema=response_schema(request)
            # Bound supplied text by UTF-8 bytes; CLI also adds runtime instructions.
            # Keep a separate 32k margin and compare it with actual reported usage.
            input_bound=len(prompt.encode())+len(canonical_json(request))+len(canonical_json(schema))+32768
            output_reserve=max(4096,400*len(candidates))
            if input_bound+output_reserve>args.context_budget:
                raise ValueError(f"entry {row['entry_id']} exceeds complete-request budget: {input_bound}+{output_reserve}")
            requests.append((request,schema,input_bound,output_reserve))
            if len(requests) >= args.limit:
                break
        if args.dry_run:
            print(json.dumps({'tasks':[{'entry_id':r['entry']['entry_id'],'examples':len(r['examples']),
                'input_budget_estimate':i,'output_reserve':o} for r,s,i,o in requests]}))
            return
        identity=sha256_bytes(canonical_json([args.scope_id,prompt_hash,REQUEST_VERSION,RESPONSE_SCHEMA_VERSION]))
        connection.execute('SELECT pg_advisory_xact_lock(?)',(int(identity[:15],16),))
        run=connection.execute('SELECT id FROM run WHERE run_identity_sha256=?',(identity,)).fetchone()
        if run:
            run_id=int(run[0])
        else:
            run_id=int(connection.execute('''INSERT INTO run(dictionary_snapshot_id,selection_sha256,extractor_version,
                prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,pipeline_version,run_identity_sha256)
                VALUES (?,?,?,?,?,?,?,?,?) RETURNING id''',(scope['snapshot_id'],args.scope_id,'wadoku-structure-v2',
                prompt_hash,sha256_bytes(b''),sha256_bytes(b''),'{}','wadoku-structure-v2',identity)).fetchone()[0])
        connection.commit()
        futures={}
        for request,schema,input_bound,output_reserve in requests:
            path=args.work_dir/'inbox'/f"{request['batch_id']}.json"
            if path.exists() and path.read_bytes()!=canonical_json(request):
                raise ValueError('frozen request differs')
            atomic_write(path,canonical_json(request))
            connection.execute('''INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,article_count,
                unit_count,manifest_path) VALUES (?,?,'classification',?,?,1,0,?) ON CONFLICT(id) DO NOTHING''',
                (request['batch_id'],run_id,request['manifest_sha256'],len(canonical_json(request)),str(path.resolve())))
            connection.commit()
            state = connection.execute('SELECT state FROM batch WHERE id=?', (request['batch_id'],)).fetchone()[0]
            if state == 'blocked':
                failures = connection.execute('SELECT count(*) FROM attempt WHERE batch_id=?', (request['batch_id'],)).fetchone()[0]
                if failures >= 3:
                    print(json.dumps({'entry_id': request['entry']['entry_id'], 'retry_exhausted': True}), flush=True)
                    continue
                connection.execute("UPDATE batch SET state='ready' WHERE id=? AND state='blocked'", (request['batch_id'],))
                audit(connection, 'wadoku_classification_retry', 'batch', request['batch_id'], {'previous_attempts': failures, 'limit': 3})
                connection.commit()
            runtime_prompt = retry_prompt(connection, request['batch_id'], prompt)
            input_bound += len(runtime_prompt.encode()) - len(prompt.encode())
            if input_bound + output_reserve > args.context_budget:
                raise ValueError('classification retry exceeds context reservation')
            item=claim(connection,'wadoku-structure-luna',args.work_dir/'outbox',run_id=run_id,
                kind='classification',model_id='gpt-5.6-luna',reasoning_effort='medium',transport='codex-agent',batch_id=request['batch_id'])
            if item is None:
                raise ValueError('task is not ready; inspect its current attempt before continuing')
            save_runtime_prompt(connection, item, runtime_prompt, prompt, schema=schema)
            connection.commit()
            started=time.monotonic()
            future=executor.submit(dispatch_one,item,runtime_prompt,'classification',request_timeout_seconds=240,output_schema=schema)
            futures[future]=(request,item,input_bound,output_reserve,started)
        for future in as_completed(futures):
            request,item,input_bound,output_reserve,started=futures[future]
            result=future.result()
            atomic_write(Path(item['response_path']).with_suffix('.events.jsonl'),result.stdout.encode())
            atomic_write(Path(item['response_path']).with_suffix('.stderr.txt'),result.stderr.encode())
            payload=None
            changes=[]
            errors=[]
            if result.returncode or result.usage is None:
                errors.append(f'CLI returncode={result.returncode}; usage_available={result.usage is not None}')
            else:
                try:
                    payload=parse_response(Path(item['response_path']).read_text())
                    payload,changes=normalize_decision(payload,request)
                    errors=validate_decision(request,payload)
                except (OSError,ValueError) as error:
                    errors=[str(error)]
            if result.usage is not None:
                usage=result.usage
                if usage['input_tokens']>input_bound or usage['output_tokens']>output_reserve:
                    errors.append('actual usage exceeded request reservation; recalibrate before next task')
                record_attempt_usage(connection,item['attempt_id'],effective_model_id=item['model_id'],
                    reasoning_effort=item['reasoning_effort'],transport='codex-agent',
                    input_tokens=usage['input_tokens'],cached_input_tokens=usage.get('cached_input_tokens',0),
                    output_tokens=usage['output_tokens'],total_tokens=usage['input_tokens']+usage['output_tokens'],
                    api_request_id=result.thread_id,finish_reason='turn.completed',status_reason='source classification',latency_ms=result.latency_ms)
            status='rejected' if errors else 'accepted'
            changed=connection.execute("UPDATE attempt SET outcome=?,error_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=? AND outcome='claimed' AND lease_token=?",
                (status,canonical_json(errors).decode(),item['attempt_id'],item['lease_token'])).rowcount
            changed_batch=connection.execute("UPDATE batch SET state=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND state='leased' AND lease_token=?",
                ('blocked' if errors else 'deterministic_validated',item['batch_id'],item['lease_token'])).rowcount
            if changed!=1 or changed_batch!=1:
                raise ValueError('classification lost its lease')
            if not errors:
                record={'method':'luna-proposal','attempt_id':item['attempt_id'],'prompt_sha256':prompt_hash,
                        'request_sha256':request['manifest_sha256'],'normalizations':changes,'result':payload}
                connection.execute('UPDATE wadoku_scope_entry SET decision_json=? WHERE scope_id=? AND entry_id=? AND decision_json IS NULL',
                    (canonical_json(record).decode(),args.scope_id,request['entry']['entry_id']))
                for candidate,decision in zip(request['examples'],payload['examples']):
                    connection.execute('''UPDATE wadoku_example_candidate SET decision_json=? WHERE scope_id=?
                        AND parent_id=? AND child_id=? AND relation_path=?''',
                        (canonical_json({**record,'result':decision}).decode(),args.scope_id,candidate['parent_id'],candidate['child_id'],candidate['relation_path']))
            audit(connection,'wadoku_classification_result','attempt',item['attempt_id'],{'errors':errors,'entry_id':request['entry']['entry_id'],
                'input_budget_estimate':input_bound,'output_reserve':output_reserve,'actual_usage':result.usage})
            connection.commit()
            print(json.dumps({'entry_id':request['entry']['entry_id'],'attempt_id':item['attempt_id'],'seconds':round(time.monotonic()-started,2),
                             'usage':result.usage,'errors':errors,'policy':payload.get('article_policy') if payload else None},ensure_ascii=False),flush=True)
            # Drain the whole bounded window; never abandon another live response.
    finally:
        executor.shutdown(wait=True)
        connection.close()
        database.close()


if __name__=='__main__':
    main()
