#!/usr/bin/env python3
"""Prepare the full rich pilot and translate it through bounded saved windows."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import traceback
import time

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.batch import make_batches, claim, retry_or_split, split_ready_batch
from jitendex_ru.db import record_attempt_usage, audit
from jitendex_ru.util import canonical_json, sha256_file, sha256_bytes, atomic_write
from jitendex_ru.validate_response import ingest_response, ValidationFailure
from jitendex_ru.wadoku_pipeline import prepare_run, load_reviewed_examples, load_candidate_examples
from jitendex_ru.wadoku_scope import collect_example_sources
from jitendex_ru.wadoku_windows import begin_window, window_batches, finish_window
from jitendex_ru.wadoku_retry import retry_prompt, save_runtime_prompt
from jitendex_ru.wadoku_telemetry import event, logged_dispatch, run_logged
from jitendex_ru.wadoku_profile import DEFAULT_PROFILE, load_profile, resolve_prompt
from jitendex_ru.wadoku_wire import translation_wire
from jitendex_ru.jpdb_scope import reuse_accepted_translations
from run_codex_batches import dispatch_one, build_output_schema


def resume_prepared(connection, run_id, prompt_sha256):
    """Use frozen PostgreSQL inputs; never reparse XML to resume a window."""
    run=connection.execute('SELECT * FROM run WHERE id=?',(run_id,)).fetchone()
    if not run or run['pipeline_version']!='wadoku-xml-v3':
        raise ValueError('resume requires a prepared rich Wadoku run')
    if run['prompt_sha256']!=prompt_sha256:
        raise ValueError('resume prompt differs from frozen run')
    return {'run_id':run_id,'created':False,'resumed':True}


def require_complete(report):
    if report['missing_units']:
        raise RuntimeError(f"translation incomplete: {len(report['missing_units'])} units missing; see coverage event")


def recover_transport(c, batch_id):
    """A busy service is not evidence that the dictionary batch needs splitting."""
    row = c.execute('SELECT attempt_count,state FROM batch WHERE id=?', (batch_id,)).fetchone()
    if row['state'] != 'retryable':
        raise ValueError('transport recovery requires retryable state')
    if row['attempt_count'] >= 3:
        c.execute("UPDATE batch SET state='blocked' WHERE id=? AND state='retryable'", (batch_id,))
        c.commit()
        return {'batch_id':batch_id,'requeued':False,'split':False,'blocked':True}
    delay = min(20, 5 * 2 ** (row['attempt_count'] - 1))
    c.commit()
    return {**retry_or_split(c, batch_id, max_attempts=3), 'delay_s':delay}


def dispatch_batch(config,run_id,batch,work,prompt,context_budget,database=None):
    db=database or Database(config)
    c=db.connect()
    try:
        base_prompt = prompt
        prompt = retry_prompt(c, batch['id'], base_prompt)
        request_path=Path(batch['manifest_path'])
        request=json.loads(request_path.read_text())
        schema=build_output_schema(request,'translation')
        wire, wire_format = translation_wire(request, base_prompt)
        supplied=len(wire)+len(prompt.encode())+len(canonical_json(schema))+32768
        if supplied+12000>context_budget:
            recovery = split_ready_batch(
                c, batch['id'],
                reason=f'complete request reservation {supplied + 12000} exceeds {context_budget}',
            )
            c.commit()
            event('preflight_split', run_id=run_id, batch_id=batch['id'],
                  request_reservation=supplied+12000, context_budget=context_budget, recovery=recovery)
            if not recovery.get('split'):
                raise ValueError('complete request exceeds context reservation and is indivisible')
            return recovery
        item=claim(c,'wadoku-rich-luna',work/'outbox',run_id=run_id,kind='translation',
                   model_id='gpt-5.6-luna',reasoning_effort='medium',transport='codex-agent',batch_id=batch['id'])
        if item is None:
            return
        save_runtime_prompt(c, item, prompt, base_prompt, schema=schema)
        wire_path = Path(item['response_path']).with_suffix('.wire.json')
        atomic_write(wire_path, wire)
        wire_details = {'wire_format': wire_format, 'path': str(wire_path),
                        'sha256': sha256_bytes(wire), 'wire_bytes': len(wire),
                        'canonical_bytes': len(canonical_json(request))}
        audit(c, 'wadoku_worker_transport', 'attempt', item['attempt_id'], wire_details)
        c.commit()
        event('attempt_queued', run_id=run_id, batch_id=batch['id'], attempt_id=item['attempt_id'],
              entry_ids=[a['sequence'] for a in request['articles']], input_reservation=supplied,
              worker_transport=wire_details)
        c.close()
        c=None  # Never occupy a database connection while waiting for Luna.
        result=logged_dispatch(dispatch_one,item,prompt,'translation',request_timeout_seconds=240,
                               output_schema=schema,model_request_text=wire.decode())
        atomic_write(Path(item['response_path']).with_suffix('.events.jsonl'),result.stdout.encode())
        atomic_write(Path(item['response_path']).with_suffix('.stderr.txt'),result.stderr.encode())
        c=db.connect()
        if result.usage:
            usage=result.usage
            record_attempt_usage(c,item['attempt_id'],effective_model_id=item['model_id'],
                reasoning_effort='medium',transport='codex-agent',input_tokens=usage['input_tokens'],
                cached_input_tokens=usage.get('cached_input_tokens',0),output_tokens=usage['output_tokens'],
                total_tokens=usage['input_tokens']+usage['output_tokens'],api_request_id=result.thread_id,
                finish_reason='turn.completed',status_reason='rich pilot translation',latency_ms=result.latency_ms)
            c.commit()
        if result.returncode or not result.usage:
            errors=[{'code':'transport_failure','returncode':result.returncode,'usage_available':bool(result.usage)}]
            changed=c.execute("UPDATE attempt SET outcome='rejected',error_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=? AND outcome='claimed' AND lease_token=?",
                              (canonical_json(errors).decode(),item['attempt_id'],item['lease_token'])).rowcount
            leased=c.execute("UPDATE batch SET state='retryable',lease_token=NULL,lease_expires_at=NULL WHERE id=? AND state='leased' AND lease_token=?",
                             (item['batch_id'],item['lease_token'])).rowcount
            if changed!=1 or leased!=1:
                c.rollback()
                raise ValueError('transport recovery lost its exact lease')
            c.commit()
        else:
            try:
                accepted=ingest_response(c,Path(item['response_path']))
            except ValidationFailure:
                c.commit()
                errors=json.loads(c.execute('SELECT error_json FROM attempt WHERE id=?',(item['attempt_id'],)).fetchone()[0])
            else:
                c.commit()
                event('attempt_result',run_id=run_id,batch_id=batch['id'],attempt_id=item['attempt_id'],status='accepted',errors=[])
                return
        c.commit()
        recovery=(recover_transport(c,item['batch_id']) if result.returncode or not result.usage
                  else retry_or_split(c,item['batch_id'],max_attempts=3))
        c.commit()
        event('attempt_result',run_id=run_id,batch_id=batch['id'],attempt_id=item['attempt_id'],
              status='rejected',errors=errors,recovery=recovery)
        if recovery.get('delay_s'):
            c.close()
            c=None
            event('transport_backoff', batch_id=item['batch_id'], delay_s=recovery['delay_s'])
            time.sleep(recovery['delay_s'])
    finally:
        if c is not None:
            c.close()
        if database is None:
            db.close()


def dispatch_window(config,c,run_id,ordinal,work,prompt,context_budget,max_batches,concurrency):
    remaining=max_batches*3
    started=time.monotonic()
    database=Database(config)
    failures={}
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures={}
            while remaining or futures:
                running=set(futures.values())
                ready=[dict(row) for row in window_batches(c,run_id,ordinal)
                       if row['state']=='ready' and row['id'] not in running and row['id'] not in failures]
                c.commit()
                for batch in ready[:min(concurrency-len(futures),remaining)]:
                    future=pool.submit(dispatch_batch,config,run_id,batch,work,prompt,context_budget,database)
                    futures[future]=batch['id']
                    remaining-=1
                if not futures:
                    break
                done,_=wait(futures,return_when=FIRST_COMPLETED)
                for future in done:
                    batch_id=futures.pop(future)
                    try:
                        future.result()
                    except Exception as error:
                        failures[batch_id]=str(error)
                        event('batch_exception',run_id=run_id,batch_id=batch_id,error_type=type(error).__name__,
                              error=str(error),traceback=traceback.format_exc())
                    if hasattr(c, 'execute'):
                        total_articles=c.execute('SELECT count(*) FROM run_article WHERE run_id=?',(run_id,)).fetchone()[0]
                        completed_articles=c.execute('''WITH expected AS (
                                SELECT article_id,count(*) AS units FROM translation_unit
                                WHERE run_id=? GROUP BY article_id
                            ), done AS (
                                SELECT u.article_id,count(DISTINCT t.unit_id) AS units
                                FROM translation_unit u JOIN translation t
                                  ON t.run_id=u.run_id AND t.unit_id=u.id
                                WHERE u.run_id=? GROUP BY u.article_id
                            ) SELECT count(*) FROM expected e JOIN done d USING(article_id)
                              WHERE e.units=d.units''',(run_id,run_id)).fetchone()[0]
                        event('translation_progress',run_id=run_id,total=total_articles,completed=completed_articles,
                              remaining=total_articles-completed_articles,
                              elapsed_s=round(time.monotonic()-started,3))
            if failures:
                raise RuntimeError(f'{len(failures)} batches failed unexpectedly; see event log: {sorted(failures)}')
    finally:
        event('database_metrics',run_id=run_id,**database.metrics.snapshot())
        database.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scope-id')
    parser.add_argument('--run-id',type=int)
    parser.add_argument('--candidate-scope',action='store_true')
    parser.add_argument('--unclassified-source-only', action='store_true',
                        help='Focused diagnostic only: translate unresolved entries without adopting failed structure')
    parser.add_argument('--candidate-subset',type=int,nargs='+',
                        help='Diagnostic source entry IDs, preserving candidate example context')
    parser.add_argument('--candidate-subset-file',type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--reuse-from-run',type=int,action='append',default=[])
    parser.add_argument('--entry-ids',type=int,nargs='+')
    parser.add_argument('--max-batches',type=int,default=4)
    parser.add_argument('--concurrency',type=int,default=3)
    parser.add_argument('--articles-per-batch',type=int)
    parser.add_argument('--event-log',type=Path)
    parser.add_argument('--context-budget',type=int,default=96000)
    parser.add_argument('--config', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--prompt',type=Path)
    args=parser.parse_args()
    if args.candidate_subset_file:
        if args.candidate_subset:
            raise ValueError('choose subset IDs or a selection file')
        selection=json.loads(args.candidate_subset_file.read_text())
        if selection.get('scope_id')!=args.scope_id:
            raise ValueError('pilot selection belongs to a different scope')
        args.candidate_subset=selection['entry_ids']
    candidate_subset = args.candidate_subset
    if args.unclassified_source_only and (not args.candidate_scope or args.run_id is not None):
        raise ValueError('unclassified source fallback requires a new focused candidate scope')
    if candidate_subset:
        if args.run_id is not None or args.entry_ids or args.candidate_scope or not args.scope_id:
            raise ValueError('candidate subset requires only scope ID and diagnostic entry IDs')
        if not 1 <= len(candidate_subset) <= 100 or len(set(candidate_subset)) != len(candidate_subset):
            raise ValueError('candidate subset requires 1–100 distinct source entries')
        args.candidate_scope = True
    if args.run_id is not None and (args.entry_ids or args.scope_id or args.candidate_scope):
        raise ValueError('--run-id resumes frozen input; do not supply entries or scope')
    if args.reuse_from_run and (args.run_id is not None or not args.candidate_scope):
        raise ValueError('translation reuse requires a new complete candidate scope')
    if args.candidate_scope and args.entry_ids:
        raise ValueError('candidate scope always includes the entire frozen pilot')
    if args.run_id is None and (not args.scope_id or (not args.entry_ids and not args.candidate_scope)):
        raise ValueError('new preparation requires --scope-id and --entry-ids')
    if not 1 <= args.max_batches <= 5000 or (args.entry_ids and not 1 <= len(args.entry_ids) <= 10):
        raise ValueError('choose 1–5000 batches, or 1–10 diagnostic entries')
    if not 1 <= args.concurrency <= 100:
        raise ValueError('concurrency must be 1–100')
    if args.articles_per_batch is not None and (not 1 <= args.articles_per_batch <= 100 or args.run_id is not None):
        raise ValueError('articles-per-batch is 1–100 and only for a new run')
    if args.context_budget < 1:
        raise ValueError('context reservation must be positive')
    cache=json.loads((Path.home()/'.codex/models_cache.json').read_text())
    model=next(m for m in cache['models'] if m['slug']=='gpt-5.6-luna')
    effective=model['context_window']*model['effective_context_window_percent']//100
    if args.context_budget > effective:
        raise ValueError('reservation exceeds the locally configured effective model context')
    config=load_profile(args.config)
    from psycopg.conninfo import conninfo_to_dict
    if config.db_backend!='postgresql' or conninfo_to_dict(config.database_url()).get('dbname')!='wadoku_rich_pilot':
        raise ValueError('isolated pilot PostgreSQL required')
    db=Database(config)
    c=db.connect()
    try:
        frozen = None
        if args.run_id is not None:
            row = c.execute('SELECT prompt_sha256 FROM run WHERE id=?', (args.run_id,)).fetchone()
            if not row:
                raise ValueError('run absent')
            frozen = row[0]
        prompt_path = resolve_prompt(config, 'translation', args.prompt, frozen_sha256=frozen)
        prompt = prompt_path.read_text()
        if args.run_id is not None:
            prepared=resume_prepared(c,args.run_id,sha256_file(prompt_path))
        else:
            scope=c.execute('SELECT * FROM wadoku_scope WHERE id=?',(args.scope_id,)).fetchone()
            if not scope:
                raise ValueError('scope absent')
            if args.candidate_scope:
                scoped=c.execute('SELECT entry_id,decision_json FROM wadoku_scope_entry WHERE scope_id=? ORDER BY ordinal', (args.scope_id,)).fetchall()
                scope_manifest = json.loads(scope['manifest_json'])
                focused = scope_manifest.get('version') == 'wadoku-focused-scope-v1'
                prefix_scope = (scope_manifest.get('version') == 'wadoku-prefix-scope-v1'
                                and scope_manifest.get('prefix_size') == len(scoped))
                linked_scope = (scope_manifest.get('version') == 'wadoku-linked-prefix-scope-v1'
                                and scope_manifest.get('requested_size') == len(scoped)
                                and 1 <= len(scoped) <= 20_000)
                linked_scope = linked_scope or (scope_manifest.get('version') == 'wadoku-linked-prefix-scope-v2'
                                and scope_manifest.get('requested_size') == len(scoped)
                                and scope_manifest.get('retained_entry_count') == 20_000
                                and scope_manifest.get('added_entry_count') == 10_000
                                and len(scoped) == 30_000)
                if not prefix_scope and not linked_scope and not (focused and len(scoped) == 100) and not 150 <= len(scoped) <= 200:
                    raise ValueError('candidate scope size or manifest is unsupported')
                if candidate_subset:
                    scoped = [r for r in scoped if r['entry_id'] in candidate_subset]
                    if len(scoped) != len(candidate_subset):
                        raise ValueError('diagnostic entry is outside the frozen pilot')
                missing=[r['entry_id'] for r in scoped if not r['decision_json']]
                if args.unclassified_source_only and not focused:
                    raise ValueError('source-only fallback is restricted to focused diagnostic scopes')
                if missing and not args.unclassified_source_only:
                    raise ValueError(f'candidate scope has missing classification: {missing}')
                args.entry_ids=[r['entry_id'] for r in scoped]
            entries,decisions=[],{}
            for entry_id in args.entry_ids:
                row=c.execute('SELECT * FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',(args.scope_id,entry_id)).fetchone()
                if not row:
                    raise ValueError('source/classification absent')
                if not row['decision_json']:
                    if not args.unclassified_source_only:
                        raise ValueError('source/classification absent')
                    entries.append((row['ordinal'], json.loads(row['source_json'])))
                    decisions[entry_id] = {'article_group_decision': {
                        'article_policy': 'needs_review', 'lookup_policy': 'needs_review',
                        'parent_id': None, 'lookup_aliases': [],
                        'lookup_needs_review': 'Classification failed; source-only diagnostic translation.',
                        'reason': 'No failed Luna structural proposal was adopted.'}, 'examples': []}
                    continue
                proposal=json.loads(row['decision_json'])['result']
                if not args.candidate_scope and (proposal['article_policy']!='independent' or proposal['lookup_needs_review']):
                    raise ValueError('diagnostic subset requires resolved independent entries')
                entries.append((row['ordinal'],json.loads(row['source_json'])))
                loader=load_candidate_examples if args.candidate_scope else load_reviewed_examples
                decisions[entry_id]={'article_group_decision':proposal,
                                    'examples':loader(c,args.scope_id,entry_id)}
            source_path=Path('work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml')
            children=collect_example_sources(source_path,config.raw['source']['xml_sha256'],
                                            [e for d in decisions.values() for e in d['examples']])
            versions={'labels':sha256_file(Path('terminology/wadoku-xml-labels-v2.json')),
                'morphology':'source-classification-v6','examples':'reviewed-full-context-v1',
                'corrections':'source-preserved','prompt':sha256_file(prompt_path),'schema':'rich-v7'}
            if args.candidate_scope:
                versions['examples']='classifier-candidate-full-context-v1'
                versions['morphology']='classification-sha256:'+sha256_bytes(canonical_json(
                    [[r['entry_id'],json.loads(r['decision_json']) if r['decision_json'] else None] for r in scoped]))
            limits={'diagnostic_subset':True,'scope_id':args.scope_id,'max_new_batches':args.max_batches,
                    'context_budget':96000,'output_reserve':12000,
                    'articles_per_batch':args.articles_per_batch or min(100, config.raw['batch']['soft_max_articles'])}
            if args.candidate_scope:
                limits.update(diagnostic_subset=False,candidate_scope=True,release_approved=False,
                              context_budget=args.context_budget)
                if candidate_subset:
                    limits.update(diagnostic_subset=True,candidate_scope=False,
                                  candidate_subset=candidate_subset)
                if args.unclassified_source_only:
                    limits.update(unclassified_source_only=missing, release_approved=False)
            prepared=prepare_run(c,snapshot_id=scope['snapshot_id'],entries=entries,versions=versions,
                limits=limits,decisions=decisions,example_sources=children)
        run_id=prepared['run_id']
        if args.reuse_from_run:
            scope_manifest=json.loads(scope['manifest_json'])
            if scope_manifest.get('version')!='wadoku-linked-prefix-scope-v2':
                raise ValueError('translation reuse requires the extended scope')
            for source_run_id in dict.fromkeys(args.reuse_from_run):
                previous=c.execute('SELECT limits_json FROM run WHERE id=?',(source_run_id,)).fetchone()
                if not previous or source_run_id==run_id:
                    raise ValueError('invalid translation reuse source run')
                previous_limits=json.loads(previous['limits_json'])
                if previous_limits.get('scope_id') not in {scope_manifest['retained_scope_id'],args.scope_id}:
                    raise ValueError('source run does not match retained or pilot scope')
                if previous_limits.get('scope_id')==args.scope_id and not previous_limits.get('candidate_subset'):
                    raise ValueError('same-scope reuse source is not a pilot subset')
                reused=reuse_accepted_translations(c,source_run_id,run_id)
                event('translation_reuse',run_id=run_id,**reused)
        work=Path('work/wadoku-xml/pilot-v6/translation')/str(run_id)
        run_limits=json.loads(c.execute('SELECT limits_json FROM run WHERE id=?',(run_id,)).fetchone()[0])
        make_batches(c,run_id,work/'inbox',{},run_limits.get('articles_per_batch',1),24000,100,16000,96000,150)
        c.commit()
        print(json.dumps({'prepared':prepared}),flush=True)
        event('stage_prepared',run_id=run_id,scope_id=run_limits.get('scope_id'),concurrency=args.concurrency,
              articles_per_batch=run_limits.get('articles_per_batch',1))
        if args.prepare_only:
            return
        window=begin_window(c,run_id,args.max_batches)
        c.commit()
        if window['state']=='no_ready_batches':
            print(json.dumps({'window':window}),flush=True)
            return
        audit(c,'wadoku_translation_dispatch_budget','run',run_id,
              {'context_budget':args.context_budget,'output_reserve':12000,'runtime_margin':32768,
               'max_new_batches':args.max_batches,'schema_included':True,'concurrency':args.concurrency})
        c.commit()
        dispatch_window(config,c,run_id,window['ordinal'],work,prompt,args.context_budget,args.max_batches,args.concurrency)
        report=finish_window(c,run_id,window['ordinal'])
        c.commit()
        event('coverage',run_id=run_id,ordinal=window['ordinal'],articles=report['articles'],units=report['units'],
              translated_units=report['translated_units'],missing_units=report['missing_units'],tokens=report['known_tokens'])
        print(json.dumps({'run_id':run_id,'articles':report['articles'],'units':report['units'],
                          'missing_units':report['missing_units'],'known_tokens':report['known_tokens']}),flush=True)
        require_complete(report)
    finally:
        c.close()
        db.close()


if __name__=='__main__':
    run_logged('translation', main)
