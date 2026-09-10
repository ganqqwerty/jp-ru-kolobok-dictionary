"""Run one frozen 100-entry comparison, with bounded retries and a shared timeline."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from jitendex_ru.database import Database
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_telemetry import event, run_logged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scope-id', required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=100)
    parser.add_argument('--articles-per-batch', type=int, default=6)
    parser.add_argument('--event-log', type=Path)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 100:
        raise ValueError('concurrency is 1–100')
    config = load_profile(Path('config.wadoku.rich.luna.toml'))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname') != 'wadoku_rich_pilot':
        raise ValueError('isolated pilot PostgreSQL required')
    db = Database(config)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    def stage(name, command):
        started = time.monotonic()
        log = args.work_dir / (name + '.console.log')
        event('stage_started', name=name, command=command, console_log=str(log))
        with log.open('a', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, *command], stdout=stream, stderr=subprocess.STDOUT)
        event('stage_finished', name=name, returncode=result.returncode, duration_s=round(time.monotonic()-started, 3))
        if result.returncode:
            raise RuntimeError(f'{name} failed with {result.returncode}; see {log}')
    try:
        with db.connect() as c:
            count = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=?',(args.scope_id,)).fetchone()[0]
        if count != 100:
            raise ValueError('repeat requires exactly 100 frozen source entries')
        for attempt in range(1, 4):
            with db.connect() as c:
                missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',(args.scope_id,)).fetchone()[0]
            if not missing:
                break
            stage(f'classification-pass{attempt}', ['scripts/wadoku_classify_window.py', '--scope-id', args.scope_id,
                '--limit', '100', '--concurrency', str(args.concurrency), '--context-budget', '128000',
                '--work-dir', str(args.work_dir / 'classification'), '--event-log', str(args.work_dir / 'classification.jsonl')])
        with db.connect() as c:
            missing = c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NULL',(args.scope_id,)).fetchone()[0]
        event('classification_coverage', total=100, unresolved=missing)
        stage('translation', ['scripts/wadoku_translate_window.py', '--scope-id', args.scope_id,
            '--candidate-scope', '--unclassified-source-only', '--max-batches', '100',
            '--concurrency', str(args.concurrency), '--articles-per-batch', str(args.articles_per_batch), '--context-budget', '128000',
            '--event-log', str(args.work_dir / 'translation.jsonl')])
        event('pipeline_finished', scope_id=args.scope_id, classification_unresolved=missing,
              note='Translation completion is not semantic approval; main-thread review follows.')
    finally:
        db.close()


if __name__ == '__main__':
    run_logged('repeat-pipeline', main)
