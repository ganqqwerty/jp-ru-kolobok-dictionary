"""Bounded source-rendering resolution through shared Luna CLI and PG attempts."""
import argparse
import json
from pathlib import Path

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.batch import claim
from jitendex_ru.db import audit, record_attempt_usage
from jitendex_ru.util import atomic_write, canonical_json, sha256_file
from jitendex_ru.wadoku_transcriptions import request_for, response_schema, validate_resolution, apply_stored_resolutions
from run_codex_batches import dispatch_one
from jitendex_ru.wadoku_profile import DEFAULT_PROFILE, load_profile, resolve_prompt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--config', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--max-batches', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.max_batches <= 10:
        raise ValueError('choose 1–10 tasks')
    config = load_profile(args.config)
    from psycopg.conninfo import conninfo_to_dict
    if config.db_backend != 'postgresql' or conninfo_to_dict(config.database_url()).get('dbname') != 'wadoku_rich_pilot':
        raise ValueError('isolated pilot PostgreSQL required')
    prompt_path = resolve_prompt(config, 'transcription')
    prompt, digest = prompt_path.read_text(), sha256_file(prompt_path)
    work = Path('work/wadoku-xml/pilot-v6/transcriptions') / str(args.run_id)
    db = Database(config); c = db.connect()
    try:
        run = c.execute('SELECT pipeline_version FROM run WHERE id=?', (args.run_id,)).fetchone()
        if not run or run[0] != 'wadoku-xml-v3':
            raise ValueError('rich Wadoku run required')
        requests = [request_for(apply_stored_resolutions(c, args.run_id, json.loads(r[0])['render_value']), digest, args.run_id) for r in c.execute(
            'SELECT projection_json FROM wadoku_projection WHERE run_id=? ORDER BY article_id', (args.run_id,))]
        completed = 0
        for request in requests:
            if request is None or completed >= args.max_batches:
                continue
            existing = c.execute('SELECT state FROM batch WHERE id=?', (request['batch_id'],)).fetchone()
            if existing:
                continue  # Never replay a running, completed or rejected attempt implicitly.
            schema = response_schema()
            if len(canonical_json(request)) + len(prompt.encode()) + len(canonical_json(schema)) + 32768 + 12000 > 96000:
                raise ValueError('transcription request exceeds context budget')
            path = (work / 'inbox' / (request['batch_id'] + '.json')).resolve()
            atomic_write(path, canonical_json(request))
            c.execute("""INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,article_count,unit_count,manifest_path)
                VALUES (?,?,'classification',?,?,1,0,?)""", (request['batch_id'], args.run_id,
                request['manifest_sha256'], len(canonical_json(request)), str(path)))
            c.commit()
            item = claim(c, 'wadoku-transcription-luna', work/'outbox', run_id=args.run_id,
                kind='classification', model_id='gpt-5.6-luna', reasoning_effort='medium',
                transport='codex-agent', batch_id=request['batch_id'])
            if item is None:
                raise ValueError('transcription task could not be claimed')
            c.execute('UPDATE attempt SET prompt_sha256=? WHERE id=?', (digest, item['attempt_id'])); c.commit()
            result = dispatch_one(item, prompt, 'classification', output_schema=schema, request_timeout_seconds=240)
            atomic_write(Path(item['response_path']).with_suffix('.events.jsonl'), result.stdout.encode())
            atomic_write(Path(item['response_path']).with_suffix('.stderr.txt'), result.stderr.encode())
            if result.usage:
                u = result.usage
                record_attempt_usage(c, item['attempt_id'], effective_model_id='gpt-5.6-luna',
                    reasoning_effort='medium', transport='codex-agent', input_tokens=u['input_tokens'],
                    cached_input_tokens=u.get('cached_input_tokens', 0), output_tokens=u['output_tokens'],
                    total_tokens=u['input_tokens']+u['output_tokens'], api_request_id=result.thread_id,
                    finish_reason='turn.completed', status_reason='transcription resolution', latency_ms=result.latency_ms)
                c.commit()
            try:
                if result.returncode or not result.usage:
                    raise ValueError('transcription CLI failed or usage absent')
                response = json.loads(Path(item['response_path']).read_text())
                validate_resolution(request, response)
            except ValueError as error:
                c.execute("UPDATE attempt SET outcome='rejected',error_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                          (json.dumps({'error': str(error)}), item['attempt_id']))
                c.execute("UPDATE batch SET state='blocked' WHERE id=?", (item['batch_id'],)); c.commit()
                raise
            c.execute("UPDATE attempt SET outcome='accepted',completed_at=CURRENT_TIMESTAMP WHERE id=?", (item['attempt_id'],))
            c.execute("UPDATE batch SET state='complete' WHERE id=?", (item['batch_id'],))
            audit(c, 'wadoku_transcription_resolution', 'run', args.run_id,
                  {'entry_id': request['entry_id'], 'source_sha256': request['source_sha256'],
                   'prompt_sha256': digest, 'attempt_id': item['attempt_id'], 'response': response})
            c.commit(); completed += 1
            print(json.dumps({'entry_id': request['entry_id'], 'response': response}, ensure_ascii=False), flush=True)
    finally:
        c.close(); db.close()


if __name__ == '__main__':
    main()
