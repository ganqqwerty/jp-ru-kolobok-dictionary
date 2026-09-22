"""Run one frozen comparison or source prefix with bounded worker loads."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import math

from jitendex_ru.database import Database
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_telemetry import event, run_logged


def translation_run_id(log: Path) -> int:
    for line in reversed(log.read_text().splitlines()):
        if not line.startswith('{'):
            continue
        payload = json.loads(line)
        if 'run_id' in payload:
            return int(payload['run_id'])
        if 'prepared' in payload and 'run_id' in payload['prepared']:
            return int(payload['prepared']['run_id'])
    raise RuntimeError('translation completed without a parseable run ID')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scope-id', required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=100)
    parser.add_argument('--articles-per-batch', type=int, default=6)
    parser.add_argument('--classification-window-size', type=int, default=1000)
    parser.add_argument('--classification-context-budget', type=int, default=250000)
    parser.add_argument('--event-log', type=Path)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 100:
        raise ValueError('concurrency is 1–100')
    if not 1 <= args.classification_window_size <= 5000:
        raise ValueError('classification window size is 1–5000')
    config = load_profile(Path('config.wadoku.rich.luna.toml'))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname') != 'wadoku_rich_pilot':
        raise ValueError('isolated pilot PostgreSQL required')
    db = Database(config)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    pipeline_started = time.monotonic()
    def stage(name, command):
        started = time.monotonic()
        log = args.work_dir / (name + '.console.log')
        event('stage_started', name=name, command=command, console_log=str(log))
        with log.open('a', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, *command], stdout=stream, stderr=subprocess.STDOUT)
        event('stage_finished', name=name, returncode=result.returncode, duration_s=round(time.monotonic()-started, 3))
        if result.returncode:
            raise RuntimeError(f'{name} failed with {result.returncode}; see {log}')
        return log
    def refresh_progress():
        command=['scripts/wadoku_progress_report.py','--scope-id',args.scope_id,
            '--log-dir',str(args.work_dir),'--output',str(args.work_dir/'progress-report.json')]
        result=subprocess.run([sys.executable,*command],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        event('progress_report_refreshed',scope_id=args.scope_id,returncode=result.returncode,
              output=str(args.work_dir/'progress-report.json'))
        if result.returncode:
            raise RuntimeError(f'progress report failed: {result.stderr[-2000:]}')
    try:
        with db.connect() as c:
            scope = c.execute('SELECT manifest_json FROM wadoku_scope WHERE id=?',(args.scope_id,)).fetchone()
            count = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=?',(args.scope_id,)).fetchone()[0]
        if not scope:
            raise ValueError('scope is absent')
        manifest = json.loads(scope[0])
        prefix_scope = manifest.get('version') == 'wadoku-prefix-scope-v1' and manifest.get('prefix_size') == count
        linked_scope = (manifest.get('version') == 'wadoku-linked-prefix-scope-v1'
                        and manifest.get('requested_size') == count and 1 <= count <= 20_000)
        linked_scope = linked_scope or (manifest.get('version') == 'wadoku-linked-prefix-scope-v2'
                        and manifest.get('requested_size') == count
                        and manifest.get('retained_entry_count') == 20_000
                        and manifest.get('added_entry_count') == 10_000 and count == 30_000)
        if count != manifest.get('entry_count') or (count not in {100,200} and not prefix_scope and not linked_scope):
            raise ValueError('runner requires a supported pilot, prefix scope, or link-closed scope')
        with db.connect() as c:
            initial_missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',
                                        (args.scope_id,)).fetchone()[0]
        event('pipeline_progress', phase='classification', total=count, completed=count-initial_missing,
              remaining=initial_missing,
              elapsed_s=round(time.monotonic()-pipeline_started, 3))
        max_windows = math.ceil(count / args.classification_window_size) * 3
        for attempt in range(1, max_windows + 1):
            with db.connect() as c:
                missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',(args.scope_id,)).fetchone()[0]
            if not missing:
                break
            log = stage(f'classification-window{attempt:03d}', ['scripts/wadoku_classify_window.py', '--scope-id', args.scope_id,
                '--limit', str(min(args.classification_window_size, missing)), '--concurrency', str(args.concurrency), '--context-budget', str(args.classification_context_budget),
                '--work-dir', str(args.work_dir / 'classification'), '--event-log', str(args.work_dir / 'classification.jsonl')])
            summary = next((json.loads(line)['classification_window'] for line in reversed(log.read_text().splitlines())
                            if line.startswith('{"classification_window"')), None)
            with db.connect() as c:
                missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',(args.scope_id,)).fetchone()[0]
            event('pipeline_progress', phase='classification', total=count, completed=count-missing, remaining=missing,
                  window=attempt, window_summary=summary, elapsed_s=round(time.monotonic()-pipeline_started, 3))
            refresh_progress()
            if summary and summary['tasks'] == 0:
                break
        with db.connect() as c:
            missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',(args.scope_id,)).fetchone()[0]
        event('classification_coverage', total=count, unresolved=missing)
        if missing:
            raise RuntimeError(f'{missing} entries still need classification; see classification logs')
        translation_log=stage('translation', ['scripts/wadoku_translate_window.py', '--scope-id', args.scope_id,
            '--candidate-scope', '--max-batches', str(min(5000, count)),
            '--concurrency', str(args.concurrency), '--articles-per-batch', str(args.articles_per_batch), '--context-budget', '192000',
            '--event-log', str(args.work_dir / 'translation.jsonl')])
        run_id = translation_run_id(translation_log)
        translation_window = 1
        while True:
            with db.connect() as c:
                ready = c.execute("SELECT count(*) FROM batch WHERE run_id=? AND kind='translation' AND state='ready'",
                                  (run_id,)).fetchone()[0]
                translated_articles = c.execute('''SELECT count(DISTINCT tu.article_id) FROM translation t
                    JOIN translation_unit tu ON tu.id=t.unit_id WHERE t.run_id=?''', (run_id,)).fetchone()[0]
            event('pipeline_progress', phase='translation', run_id=run_id, total=count,
                  completed=translated_articles, remaining=max(0, count-translated_articles),
                  ready_batches=ready, window=translation_window,
                  elapsed_s=round(time.monotonic()-pipeline_started, 3))
            if not ready:
                break
            translation_window += 1
            translation_log = stage(f'translation-window{translation_window:03d}',
                ['scripts/wadoku_translate_window.py', '--run-id', str(run_id),
                 '--max-batches', '5000', '--concurrency', str(args.concurrency),
                 '--context-budget', '192000', '--event-log', str(args.work_dir / 'translation.jsonl')])
            if translation_run_id(translation_log) != run_id:
                raise RuntimeError('translation resume changed the frozen run identity')
        stage('progress-report', ['scripts/wadoku_progress_report.py','--scope-id',args.scope_id,
            '--run-id',str(run_id),'--log-dir',str(args.work_dir),
            '--output',str(args.work_dir/'progress-report.json')])
        event('pipeline_finished', scope_id=args.scope_id, classification_unresolved=missing,
              total_articles=count, elapsed_s=round(time.monotonic()-pipeline_started, 3),
              note='Translation completion is not semantic approval; main-thread review follows.')
    finally:
        db.close()


if __name__ == '__main__':
    run_logged('repeat-pipeline', main)
