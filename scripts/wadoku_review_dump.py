"""Source-bound, ordered review packets for the main-thread human/LLM reviewer."""
import argparse
import json
from pathlib import Path

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    db=Database(Config.load(Path('config.wadoku.rich.luna.toml')))
    c=db.connect()
    try:
        contexts={}
        for row in c.execute("SELECT manifest_path FROM batch WHERE run_id=? AND kind='translation' ORDER BY created_at,id",(args.run_id,)):
            request=json.loads(Path(row[0]).read_text())
            for article in request['articles']:
                for unit in article['units']:
                    contexts[unit['unit_id']]=unit
        rows=c.execute('''SELECT a.id article_id,a.sequence entry_id,a.expression,a.reading,tu.id unit_id,
            tu.source_text,tu.role,t.id translation_id,t.target_text,t.confidence,t.review_reason,t.attempt_id
            FROM translation_unit tu JOIN article a ON a.id=tu.article_id
            LEFT JOIN LATERAL (SELECT * FROM translation WHERE run_id=tu.run_id AND unit_id=tu.id ORDER BY id DESC LIMIT 1) t ON true
            WHERE tu.run_id=? ORDER BY a.entry_ordinal,tu.json_pointer''',(args.run_id,)).fetchall()
        articles={}
        for row in rows:
            item=dict(row)
            article=articles.setdefault(item['article_id'],{k:item[k] for k in ('article_id','entry_id','expression','reading')})
            context=contexts[item['unit_id']]
            item['request_unit']=context
            article.setdefault('units',[]).append(item)
        for article in articles.values():
            article['units'].sort(key=lambda u:(u['request_unit']['projection_index'] is None,
                                               u['request_unit']['projection_index'] or 0, u['unit_id']))
        result={'run_id':args.run_id,'article_count':len(articles),'unit_count':len(rows),'articles':list(articles.values())}
        atomic_write(args.output,canonical_json(result))
        print(json.dumps({'article_count':len(articles),'unit_count':len(rows),'missing':sum(r['translation_id'] is None for r in rows),'output':str(args.output)}))
    finally:
        c.close()
        db.close()


if __name__=='__main__':
    main()
