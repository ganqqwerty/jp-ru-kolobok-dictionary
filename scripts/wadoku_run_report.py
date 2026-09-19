"""Summarize saved command timelines and rejected attempts, without reading translations."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from jitendex_ru.util import atomic_write, canonical_json


def summarize(directory):
    invocations = {}
    attempts = {}
    for path in sorted(Path(directory).glob('*.jsonl')):
        if path.name.endswith('.events.jsonl'):
            continue  # Raw CLI streams, e.g. the selector, are not command journals.
        for number, line in enumerate(path.read_text().splitlines(), 1):
            row = json.loads(line)
            if 'invocation_id' not in row:
                raise ValueError(f'not a command journal: {path}:{number}')
            invocation = invocations.setdefault(row['invocation_id'], {'path': str(path), 'stage': row['stage'], 'rows': []})
            invocation['rows'].append(row)
            if row.get('attempt_id'):
                attempt = attempts.setdefault(row['attempt_id'], {})
                attempt.update({k:row[k] for k in ('attempt_id','batch_id','entry_id','entry_ids','request_path','response_path','errors','status','returncode','duration_s','latency_ms','usage','recovery') if k in row})
                attempt['stage'] = row['stage']
    commands = []
    for key, value in invocations.items():
        rows = value.pop('rows')
        rows.sort(key=lambda r:r['elapsed_s'])
        starts = [r for r in rows if r['event']=='command_started']
        ends = [r for r in rows if r['event']=='command_finished']
        commands.append({**value, 'invocation_id':key, 'started_utc':starts[0]['utc'] if starts else None,
            'finished_utc':ends[-1]['utc'] if ends else None,
            'elapsed_s':ends[-1]['elapsed_s'] if ends else None, 'status':ends[-1]['status'] if ends else 'running',
            'peak_workers':max(r['active_workers'] for r in rows),
            'events':dict(Counter(r['event'] for r in rows)),
            'exceptions':[{'utc':r['utc'],'error':r['error'],'error_type':r.get('error_type')} for r in rows if r['event']=='command_exception']})
    commands.sort(key=lambda c:c['started_utc'] or '')
    failed = [a for a in attempts.values() if a.get('status')=='rejected' or a.get('returncode') not in (None,0)]
    unfinished = [a['attempt_id'] for a in attempts.values() if a.get('status') not in {'accepted','rejected'}]
    stage_attempts = Counter(a.get('stage','unknown') for a in attempts.values())
    stage_failures = Counter(a.get('stage','unknown') for a in failed)
    stage_terminal = Counter(a.get('stage','unknown') for a in attempts.values()
                             if a.get('status') in {'accepted','rejected'})
    usage_fields=('input_tokens','cached_input_tokens','cache_write_input_tokens',
                  'output_tokens','reasoning_output_tokens')
    stage_tokens={}
    missing_usage=Counter()
    for attempt in attempts.values():
        if attempt.get('status') not in {'accepted','rejected'}:
            continue
        stage=attempt.get('stage','unknown');usage=attempt.get('usage')
        if not isinstance(usage,dict):
            missing_usage[stage]+=1;continue
        totals=stage_tokens.setdefault(stage,{field:0 for field in usage_fields})
        for field in usage_fields:
            value=usage.get(field)
            if isinstance(value,int): totals[field]+=value
    stage_error_rates={stage:(stage_failures[stage]/total if total else 0)
                       for stage,total in stage_terminal.items()}
    first = min((c['started_utc'] for c in commands if c['started_utc']),default=None)
    last = max((c['finished_utc'] for c in commands if c['finished_utc']),default=None)
    return {'commands':commands, 'attempt_count':len(attempts), 'failed_attempts':failed, 'unfinished_attempts':unfinished,
        'stage_attempt_counts':dict(sorted(stage_attempts.items())),
        'stage_terminal_counts':dict(sorted(stage_terminal.items())),
        'stage_failed_attempt_counts':dict(sorted(stage_failures.items())),
        'stage_error_rates':dict(sorted(stage_error_rates.items())),
        'stage_token_totals':dict(sorted(stage_tokens.items())),
        'stage_terminal_missing_usage':dict(sorted(missing_usage.items())),
        'wall_including_inter_iteration_review_s':round((datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds(),3) if first and last else None,
        'note':'Do not sum nested pipeline and child stage durations. Wall time across iterations includes manual review and prompt edits.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--log-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=summarize(args.log_dir)
    atomic_write(args.output,canonical_json(result))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
