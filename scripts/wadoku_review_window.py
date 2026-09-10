#!/usr/bin/env python3
"""Bounded contextual Luna review using shared PostgreSQL review ingestion."""
import argparse
import json
from pathlib import Path

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.batch import claim
from jitendex_ru.db import audit, record_attempt_usage
from jitendex_ru.review import make_review_batches, ingest_review
from jitendex_ru.util import atomic_write, canonical_json, sha256_file, sha256_bytes
from run_codex_batches import dispatch_one, build_output_schema
from jitendex_ru.wadoku_retry import retry_prompt, save_runtime_prompt
from jitendex_ru.wadoku_profile import DEFAULT_PROFILE, load_profile, resolve_prompt


def ingest_or_retry(c, item, max_attempts=3):
    """Roll back partial review writes; retry only the lease we still own."""
    try:
        outcome = ingest_review(c, Path(item['response_path']))
    except ValueError as error:
        c.rollback()
        lock = ' FOR UPDATE' if getattr(c, 'backend', 'sqlite') == 'postgresql' else ''
        owned = c.execute(
            "SELECT b.state,b.lease_token,b.attempt_count,a.outcome,a.lease_token AS attempt_lease "
            "FROM batch b JOIN attempt a ON a.batch_id=b.id WHERE a.id=?" + lock,
            (item['attempt_id'],)).fetchone()
        if (not owned or owned['state'] != 'leased' or owned['outcome'] != 'claimed'
                or not owned['attempt_lease'] or owned['lease_token'] != owned['attempt_lease']):
            c.rollback()
            raise
        state = 'ready' if owned['attempt_count'] < max_attempts else 'blocked'
        c.execute("UPDATE attempt SET outcome='rejected',error_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                  (json.dumps({'validation_error': str(error)}, ensure_ascii=False), item['attempt_id']))
        c.execute("UPDATE batch SET state=?,lease_token=NULL,lease_expires_at=NULL WHERE id=?",
                  (state, item['batch_id']))
        audit(c, 'wadoku_review_rejected', 'attempt', item['attempt_id'],
              {'reason': str(error), 'next_state': state})
        c.commit()
        return {'validation_rejected': True, 'next_state': state, 'reason': str(error)}
    c.commit()
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--max-batches', type=int, default=4)
    parser.add_argument('--config', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--prompt', type=Path)
    parser.add_argument('--recheck', action='store_true')
    parser.add_argument('--resume', action='store_true',
                        help='Only dispatch existing requests; do not prepare new candidates')
    parser.add_argument('--unresolved-only', action='store_true')
    parser.add_argument('--article-ids', type=int, nargs='+',
                        help='Run-local article IDs for an explicit bounded recheck')
    args = parser.parse_args()
    if not 1 <= args.max_batches <= 10:
        raise ValueError('review window must contain 1–10 batches')
    config = load_profile(args.config)
    from psycopg.conninfo import conninfo_to_dict
    if config.db_backend != 'postgresql' or conninfo_to_dict(config.database_url()).get('dbname') != 'wadoku_rich_pilot':
        raise ValueError('isolated pilot PostgreSQL required')
    model = next(m for m in json.loads((Path.home()/'.codex/models_cache.json').read_text())['models']
                 if m['slug'] == 'gpt-5.6-luna')
    if 128000 > model['context_window'] * model['effective_context_window_percent'] // 200:
        raise ValueError('review reservation exceeds half the effective model context')
    prompt_path = resolve_prompt(config, 'review', args.prompt)
    prompt, digest = prompt_path.read_text(), sha256_file(prompt_path)
    db = Database(config)
    c = db.connect()
    try:
        run = c.execute('SELECT pipeline_version FROM run WHERE id=?', (args.run_id,)).fetchone()
        if not run or run[0] != 'wadoku-xml-v3':
            raise ValueError('rich Wadoku run required')
        work = Path('work/wadoku-xml/pilot-v6/review') / str(args.run_id) / digest[:12]
        if args.unresolved_only:
            work = work / 'unresolved'
        if args.article_ids:
            work = work / ('subset-' + sha256_bytes(canonical_json(sorted(args.article_ids)))[:12])
        prepared = {'resumed': True} if args.resume else make_review_batches(c, args.run_id, work/'inbox', max_articles=1,
            max_bytes=65000, max_units=150, review_prompt_sha256=digest, recheck=args.recheck,
            unresolved_only=args.unresolved_only, article_ids=args.article_ids)
        c.commit()
        print(json.dumps({'prepared': prepared}), flush=True)
        completed = []
        for _ in range(args.max_batches):
            batch = c.execute("SELECT * FROM batch WHERE run_id=? AND kind='review' AND state='ready' AND manifest_path LIKE ? ORDER BY id LIMIT 1",
                              (args.run_id, str(work/'inbox')+'/%')).fetchone()
            if batch is None:
                break
            manifest = json.loads(Path(batch['manifest_path']).read_text())
            if manifest.get('review_prompt_sha256') != digest:
                raise ValueError('review prompt differs from manifest')
            schema = build_output_schema(manifest, 'review')
            runtime_prompt = retry_prompt(c, batch['id'], prompt)
            reserved = len(canonical_json(manifest)) + len(canonical_json(schema)) + len(runtime_prompt.encode()) + 32768 + 12000
            if reserved > 128000:
                raise ValueError('complete review request exceeds context reservation before claim')
            item = claim(c, 'wadoku-luna-review-'+digest[:12], work/'outbox', run_id=args.run_id,
                kind='review', model_id='gpt-5.6-luna', reasoning_effort='medium',
                transport='codex-agent', batch_id=batch['id'])
            if item is None:
                break
            c.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?', (digest, item['attempt_id']))
            save_runtime_prompt(c, item, runtime_prompt, prompt, schema=schema)
            audit(c, 'wadoku_review_dispatch', 'attempt', item['attempt_id'],
                  {'prompt_sha256': digest, 'reserved_tokens_upper_bound': reserved})
            c.commit()
            result = dispatch_one(item, runtime_prompt, 'review', request_timeout_seconds=240, output_schema=schema)
            atomic_write(Path(item['response_path']).with_suffix('.events.jsonl'), result.stdout.encode())
            atomic_write(Path(item['response_path']).with_suffix('.stderr.txt'), result.stderr.encode())
            if result.usage:
                usage = result.usage
                record_attempt_usage(c, item['attempt_id'], effective_model_id='gpt-5.6-luna',
                    reasoning_effort='medium', transport='codex-agent', input_tokens=usage['input_tokens'],
                    cached_input_tokens=usage.get('cached_input_tokens', 0), output_tokens=usage['output_tokens'],
                    total_tokens=usage['input_tokens']+usage['output_tokens'], api_request_id=result.thread_id,
                    finish_reason='turn.completed', status_reason='contextual review', latency_ms=result.latency_ms)
                c.commit()
            if result.returncode or not result.usage:
                raise ValueError(f'check saved review attempt before recovery: {item["attempt_id"]}')
            outcome = ingest_or_retry(c, item)
            completed.append({'attempt_id': item['attempt_id'], **outcome})
            print(json.dumps(completed[-1]), flush=True)
        print(json.dumps({'run_id': args.run_id, 'review_window': completed}), flush=True)
    finally:
        c.close()
        db.close()


if __name__ == '__main__':
    main()
