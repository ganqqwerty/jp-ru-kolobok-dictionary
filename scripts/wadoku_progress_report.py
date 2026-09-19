#!/usr/bin/env python3
"""Write a durable progress/error report for one frozen Wadoku scope."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random

from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_xml import canonical_identity
from wadoku_run_report import summarize


def error_class(value):
    text=json.dumps(value,ensure_ascii=False).lower()
    if 'transport' in text or 'returncode' in text or 'usage_available=false' in text:
        return 'transport'
    if ('incompatible article and lookup' in text or 'lookup alias' in text
            or 'full expansion' in text or 'attested evidence' in text
            or 'parent not present' in text
            or 'template has neither' in text
            or 'example template' in text
            or 'selected example' in text
            or 'article_policy' in text or 'lookup_policy' in text):
        return 'classification_contract'
    if 'context' in text or 'reservation' in text or 'token' in text:
        return 'context_or_usage'
    if 'schema' in text or 'json' in text or 'validation' in text:
        return 'response_validation'
    return 'other'


def attempt_summary(rows):
    """Summarize authoritative terminal attempt rows grouped by outcome."""
    outcomes={str(row['outcome']):int(row['attempts']) for row in rows}
    accepted=outcomes.get('accepted',0);rejected=outcomes.get('rejected',0)
    terminal=accepted+rejected
    token_fields=('input_tokens','cached_input_tokens','output_tokens','total_tokens')
    tokens={field:sum(int(row[field] or 0) for row in rows) for field in token_fields}
    return {'accepted':accepted,'rejected':rejected,'terminal':terminal,
            'error_rate':rejected/terminal if terminal else 0,'tokens':tokens,
            'terminal_missing_usage':sum(int(row['missing_usage'] or 0) for row in rows)}


def build(scope_id, log_dir, run_id=None):
    timeline=summarize(log_dir)
    config=load_profile(Path('config.wadoku.rich.luna.toml'))
    db=Database(config)
    try:
        with db.connect() as c:
            scope=c.execute('SELECT manifest_json FROM wadoku_scope WHERE id=?',(scope_id,)).fetchone()
            if not scope: raise ValueError('scope is absent')
            manifest=json.loads(scope[0]); total=manifest['entry_count']
            classified=c.execute('SELECT count(*) FROM wadoku_scope_entry WHERE scope_id=? AND decision_json IS NOT NULL',(scope_id,)).fetchone()[0]
            if run_id is None:
                candidates=c.execute("SELECT id,limits_json FROM run WHERE pipeline_version='wadoku-xml-v3' ORDER BY id DESC").fetchall()
                run_id=next((int(row['id']) for row in candidates if json.loads(row['limits_json']).get('scope_id')==scope_id),None)
            translated=units=translated_units=0; last_batch=None; random_article=None
            database_attempts={}
            if run_id is not None:
                translated=c.execute('''SELECT count(*) FROM run_article ra WHERE ra.run_id=? AND NOT EXISTS (
                    SELECT 1 FROM translation_unit u WHERE u.run_id=ra.run_id AND u.article_id=ra.article_id
                    AND NOT EXISTS (SELECT 1 FROM translation t WHERE t.run_id=u.run_id AND t.unit_id=u.id))''',(run_id,)).fetchone()[0]
                units=c.execute('SELECT count(*) FROM translation_unit WHERE run_id=?',(run_id,)).fetchone()[0]
                translated_units=c.execute('''SELECT count(*) FROM translation_unit u WHERE u.run_id=? AND EXISTS
                    (SELECT 1 FROM translation t WHERE t.run_id=u.run_id AND t.unit_id=u.id)''',(run_id,)).fetchone()[0]
                last_batch=c.execute('''SELECT b.id,b.manifest_path,a.completed_at FROM batch b JOIN attempt a ON a.batch_id=b.id
                    WHERE b.run_id=? AND b.kind='translation' AND a.outcome='accepted'
                    ORDER BY a.completed_at DESC,a.id DESC LIMIT 1''',(run_id,)).fetchone()
                if last_batch:
                    request=json.loads(Path(last_batch['manifest_path']).read_text())
                    article=random.Random(last_batch['id']).choice(request['articles'])
                    row=c.execute('SELECT id,expression,reading FROM article WHERE sequence=? AND id IN (SELECT article_id FROM run_article WHERE run_id=?)',
                                  (article['sequence'],run_id)).fetchone()
                    targets=[]
                    if row:
                        targets=[dict(item) for item in c.execute('''SELECT u.role,t.target_text,t.confidence,t.review_reason
                            FROM translation_unit u JOIN LATERAL (SELECT * FROM translation latest
                            WHERE latest.run_id=u.run_id AND latest.unit_id=u.id ORDER BY latest.id DESC LIMIT 1) t ON true
                            WHERE u.run_id=? AND u.article_id=? ORDER BY u.json_pointer''',(run_id,row[0])).fetchall()]
                    random_article={'batch_id':last_batch['id'],'finished_at':str(last_batch['completed_at']),
                        'stage':'translation',
                        'entry_id':article['sequence'],
                        'expression':row['expression'] if row else article.get('expression') or article.get('term'),
                        'reading':row['reading'] if row else article.get('reading'),
                        'translations':targets[:8]}
                attempt_query='''SELECT a.outcome,count(*) AS attempts,
                    coalesce(sum(a.input_tokens),0) AS input_tokens,
                    coalesce(sum(a.cached_input_tokens),0) AS cached_input_tokens,
                    coalesce(sum(a.output_tokens),0) AS output_tokens,
                    coalesce(sum(a.total_tokens),0) AS total_tokens,
                    sum(CASE WHEN a.total_tokens IS NULL THEN 1 ELSE 0 END) AS missing_usage
                    FROM attempt a JOIN batch b ON b.id=a.batch_id {join}
                    WHERE {predicate} AND b.kind=? AND a.outcome IN ('accepted','rejected')
                    GROUP BY a.outcome ORDER BY a.outcome'''
                classification_rows=c.execute(attempt_query.format(
                    join='JOIN run r ON r.id=b.run_id',predicate='r.selection_sha256=?'),
                    (scope_id,'classification')).fetchall()
                translation_rows=c.execute(attempt_query.format(
                    join='',predicate='b.run_id=?'),(run_id,'translation')).fetchall()
                database_attempts={'classification':attempt_summary(classification_rows),
                                   'translation':attempt_summary(translation_rows)}
            if random_article is None:
                classified_batch=c.execute('''SELECT b.id,b.manifest_path,a.completed_at FROM batch b
                    JOIN attempt a ON a.batch_id=b.id JOIN run r ON r.id=b.run_id
                    WHERE b.kind='classification' AND a.outcome='accepted' AND r.selection_sha256=?
                    ORDER BY a.completed_at DESC,a.id DESC LIMIT 1''',(scope_id,)).fetchone()
                if classified_batch:
                    request=json.loads(Path(classified_batch['manifest_path']).read_text())
                    entry_id=int(request['entry']['entry_id'])
                    source=c.execute('SELECT source_json,decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                                     (scope_id,entry_id)).fetchone()
                    expression,reading,_=canonical_identity(json.loads(source['source_json']))
                    decision=json.loads(source['decision_json'])['result'] if source['decision_json'] else None
                    random_article={'batch_id':classified_batch['id'],'finished_at':str(classified_batch['completed_at']),
                        'stage':'classification','entry_id':entry_id,'expression':expression,'reading':reading,
                        'article_policy':decision.get('article_policy') if decision else None,
                        'parent_id':decision.get('parent_id') if decision else None,
                        'lookup_policy':decision.get('lookup_policy') if decision else None,
                        'lookup_aliases':decision.get('lookup_aliases',[]) if decision else [],
                        'lookup_needs_review':decision.get('lookup_needs_review') if decision else None,
                        'reason':decision.get('reason') if decision else None,
                        'confidence':decision.get('confidence') if decision else None}
    finally:
        db.close()
    failed=timeline['failed_attempts']; classes=Counter(error_class(item.get('errors') or item) for item in failed)
    attempts=sum(item['terminal'] for item in database_attempts.values())
    rejected=sum(item['rejected'] for item in database_attempts.values())
    return {'schema_version':2,'generated_utc':datetime.now(timezone.utc).isoformat(),
        'scope_id':scope_id,'run_id':run_id,'scope_entries':total,'classified_articles':classified,
        'translated_articles':translated,'translation_units':units,'translated_units':translated_units,
        'elapsed_wall_s':timeline['wall_including_inter_iteration_review_s'],
        'attempt_count':attempts,'failed_attempt_count':rejected,
        'attempt_error_rate':(rejected/attempts if attempts else 0),
        'database_attempts':database_attempts,
        'error_class_counts':dict(sorted(classes.items())),'failed_attempt_samples':failed[:20],
        'random_article_from_last_finished_batch':random_article,
        'dangling_source_target_ids':manifest.get('dangling_source_target_ids',[]),
        'selection':{k:manifest.get(k) for k in ('version','selection_rule','prefix_size','requested_size','category_counts')},
        'timeline':timeline}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--scope-id',required=True);parser.add_argument('--log-dir',type=Path,required=True)
    parser.add_argument('--run-id',type=int);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=build(args.scope_id,args.log_dir,args.run_id);atomic_write(args.output,canonical_json(result));print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
