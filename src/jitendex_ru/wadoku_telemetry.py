"""Append-only, thread-safe command and worker timing without translation text."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid

from .util import atomic_write, canonical_json


class Journal:
    def __init__(self, path, stage):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stage = stage
        self.invocation_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.counts = Counter()
        self.active = 0
        self.peak = 0

    def emit(self, event, **fields):
        with self.lock:
            if event == 'worker_started':
                self.active += 1
                self.peak = max(self.peak, self.active)
            elif event in {'worker_finished', 'worker_exception'}:
                self.active -= 1
            self.counts[event] += 1
            row = {'utc': datetime.now(timezone.utc).isoformat(), 'stage': self.stage,
                   'invocation_id': self.invocation_id, 'elapsed_s': round(time.monotonic() - self.started, 3),
                   'event': event, 'active_workers': self.active, **fields}
            text = json.dumps(row, ensure_ascii=False, default=str)
            text = re.sub(r'password=[^\s"\\]+', 'password=REDACTED', text)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(text + '\n')
                stream.flush()

    def finish(self, status):
        self.emit('command_finished', status=status)
        summary = {'stage': self.stage, 'invocation_id': self.invocation_id, 'status': status,
                   'elapsed_s': round(time.monotonic() - self.started, 3),
                   'peak_workers': self.peak, 'active_workers': self.active,
                   'events': dict(self.counts), 'journal': str(self.path)}
        atomic_write(self.path.with_name(self.path.stem + '.' + self.invocation_id + '.summary.json'), canonical_json(summary))


_journal = None


def event(event_type, **fields):
    if _journal is not None:
        _journal.emit(event_type, **fields)


def logged_dispatch(dispatch, item, prompt, kind, **kwargs):
    context = {'attempt_id': item['attempt_id'], 'batch_id': item['batch_id'],
               'request_path': item['request_path'], 'response_path': item['response_path']}
    started = time.monotonic()
    event('worker_started', **context)
    try:
        result = dispatch(item, prompt, kind, **kwargs)
    except Exception as error:
        event('worker_exception', **context, duration_s=round(time.monotonic()-started, 3),
              error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    event('worker_finished', **context, duration_s=round(time.monotonic()-started, 3),
          returncode=result.returncode, latency_ms=result.latency_ms, usage=result.usage)
    return result


def run_logged(stage, main):
    global _journal
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--event-log', type=Path)
    args, _ = parser.parse_known_args()
    path = args.event_log or Path('work/wadoku-xml/logs') / (stage + '-' + uuid.uuid4().hex + '.jsonl')
    _journal = Journal(path, stage)
    _journal.emit('command_started', argv=sys.argv)
    try:
        main()
    except BaseException as error:
        _journal.emit('command_exception', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        _journal.finish('failed')
        raise
    else:
        _journal.finish('completed')
    finally:
        _journal = None
