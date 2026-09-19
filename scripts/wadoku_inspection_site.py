"""Build an explicitly diagnostic, exact-scope ZIP and its source-bound review page.

The production export gates remain unchanged. Unresolved entries retain literal
source lookup keys and a visible warning, never a guessed lemma or expansion.
"""
import argparse
import copy
import html
import json
from pathlib import Path
import random
import re
import zipfile

from jitendex_ru.database import Database
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_pipeline import localized_run
from jitendex_ru.wadoku_assembly import lookup_rows
from jitendex_ru.wadoku_xml import canonical_identity, yomitan_rows, build_rich_archive, label_catalog, reference_target_index
from jitendex_ru.wadoku_transcriptions import apply_stored_resolutions
from jitendex_ru.wadoku_quality import TEMPLATE_RE
from jitendex_ru.util import atomic_write, canonical_json, sha256_file
from jitendex_ru.schema_validation import validate_archive


def literal_fallback(article, language='ru'):
    """Keep every translated unit for inspection if native assembly cannot render it."""
    senses={}
    for unit in article['units']:
        request=unit['request_unit']
        text=unit['target_text'] if language == 'ru' else request['source_text']
        if unit['role']=='glossary_set': text='; '.join(json.loads(text))
        # Protected objects remain source objects, never guessed translations.
        for fragment in request.get('protected_fragment_context',[]):
            def plain(n): return n.get('text','')+''.join(plain(x)+x.get('tail','') for x in n.get('children',[]))
            replacement=plain(fragment.get('tree',{}))
            if fragment.get('placeholder'):
                text=text.replace(fragment['placeholder'],replacement or '[объект источника]')
        content={'tag':'div','lang':language,'content':text}
        if unit['role']=='example_translation':
            content={'tag':'div','data':{'class':'extra-box','content':'example-sentence'},'content':[
                {'tag':'div','lang':'ja','data':{'content':'example-sentence-a'},'content':request['japanese']},
                {'tag':'div','lang':language,'data':{'content':'example-sentence-b'},'content':text}]}
        senses.setdefault(request.get('sense_path'),[]).append(content)
    return [{'type':'structured-content','content':{'tag':'div','content':[
        {'tag':'ol','content':[{'tag':'li','content':v} for v in senses.values()]}]}}]


def export(run_id, root, scope, packet):
    config=load_profile(Path('config.wadoku.rich.luna.toml'))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname')!='wadoku_rich_pilot': raise ValueError('pilot DB only')
    expected={e['entry_id'] for e in scope['entries']}
    source_count=scope['manifest']['entry_count']
    if not 1 <= source_count <= 20_000 or len(expected)!=source_count:
        raise ValueError('inspection scope count differs from its manifest')
    if {a['entry_id'] for a in packet['articles']}!=expected:
        raise ValueError('review packet differs from the exact frozen scope')
    db=Database(config)
    with db.connect() as c:
        entries=[(apply_stored_resolutions(c,run_id,v),t) for v,t in localized_run(c,run_id,allow_unreviewed=True)]
        projections=[json.loads(r[0]) for r in c.execute('SELECT projection_json FROM wadoku_projection WHERE run_id=?',(run_id,))]
    db.close()
    if {v['entry_id'] for v,t in entries}!=expected: raise ValueError('export scope differs')
    decisions={p['context']['entry_id']:p['context']['article_group_decision'] for p in projections}
    articles={a['entry_id']:a for a in packet['articles']}
    xml=Path('work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml')
    if sha256_file(xml)!=config.raw['source']['xml_sha256']: raise ValueError('source hash differs')
    refs=reference_target_index(xml)
    labels=label_catalog(Path('terminology/wadoku-xml-labels-v2.json'))
    issues=[]; rendered={'ru':{},'de':{}};coverage={}
    for value,targets in entries:
        eid=value['entry_id']; decision=decisions[eid]; warnings=[]
        if decision.get('article_policy')!='independent':
            parent=decision.get('parent_id')
            if parent in expected:
                warnings.append(f'Запись классифицирована как подстатья #{parent}; в диагностическом экспорте временно показана отдельно.')
            else:
                warnings.append(f'Запись классифицирована как подстатья #{parent}, но родитель не входит в выборку; здесь она временно показана отдельно.')
        if decision.get('lookup_needs_review'):
            warnings.append('Шаблон/поиск не разрешён: '+decision['lookup_needs_review'])
        try:
            rows,meta=yomitan_rows({**value,'reference_targets':refs},'ru',labels,targets)
            try:
                if not decision.get('lookup_needs_review'): rows,meta=lookup_rows(rows,meta,decision)
            except ValueError as error:
                warnings.append(str(error))
            if any(TEMPLATE_RE.search(r[0]+r[1]) for r in rows):
                warnings.append('Сохранён буквальный шаблон: естественный поиск не гарантирован; заполнение слотов не придумано.')
        except ValueError as error:
            expression,reading,_=canonical_identity(value)
            rows=[[expression,reading,'','',0,literal_fallback(articles[eid]),eid,'']];meta=[]
            warnings.append('Упрощённый диагностический вид; исходные переводы сохранены. '+str(error))
        if warnings:
            warning={'type':'structured-content','content':{'tag':'div','style':{'color':'#cf5572'},
                     'content':'Диагностика: '+ ' '.join(warnings)}}
            rows=copy.deepcopy(rows)
            for row in rows: row[5]=[warning,*row[5]]
            issues.append({'id':f'STR-{eid}','entry_id':eid,'category':'structure','severity':'warning','message':' '.join(warnings)})
        rendered['ru'][eid]=(rows,meta)
        try:
            de_rows,de_meta=yomitan_rows({**value,'reference_targets':refs},'de',labels,targets)
            if not decision.get('lookup_needs_review'):
                de_rows,de_meta=lookup_rows(de_rows,de_meta,decision)
        except ValueError:
            expression,reading,_=canonical_identity(value)
            de_rows=[[expression,reading,'','',0,literal_fallback(articles[eid],'de'),eid,'']];de_meta=[]
        rendered['de'][eid]=(de_rows,de_meta)
        coverage[str(eid)]={'sequences':sorted({r[6] for r in rows}),'keys':[[r[0],r[1]] for r in rows]}
    stem='wadoku-stress200' if source_count==200 else f'wadoku-linked{source_count}-run{run_id}'
    archive_names={'ru':f'{stem}-ru.zip','de':f'{stem}-de.zip'}
    license_path=Path('work/wadoku-xml/source/wadoku-xml-20260705/LICENCE')
    candidates=list(license_path.parent.glob('*'))
    license_path=next((p for p in candidates if p.is_file() and p.stat().st_size<100000 and sha256_file(p)==config.raw['source']['license_sha256']),None)
    if license_path is None: raise ValueError('verified source license absent')
    reports={}
    for language in ('ru','de'):
        output=root/'site/dist'/archive_names[language]
        reports[language]=build_rich_archive(iter(entries),output,language=language,labels=labels,
            license_text=license_path.read_bytes(),
            title=(f'Wadoku RU · диагностика {source_count} · {run_id}' if language=='ru'
                   else f'Wadoku DE · diagnostic {source_count} · {run_id}'),
            revision=f'diagnostic-{language}-{source_count}-run-{run_id}',source_url=config.raw['source']['url'],
            source_sha256=config.raw['source']['sha256'],export_audit_id=f'inspection-{language}-{run_id}',
            row_factory=lambda value,*args,lang=language:rendered[lang][value['entry_id']],
            description_note=f'Diagnostic dictionary with {source_count} frozen source records. Not a release.')
        reports[language].update(validate_archive(output,Path('schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162')))
        reports[language].update(archive_name=archive_names[language],sha256=sha256_file(output))
    report=copy.deepcopy(reports['ru'])
    prefix_sequences = {(g['expression'],g['reading'],s):g['sequence']
                        for g in report['prefix_lookup_groups'] for s in g['source_sequences']}
    for eid, item in coverage.items():
        item['sequences'] = sorted({prefix_sequences.get((r[0],r[1],r[6]),r[6])
                                    for r in rendered['ru'][int(eid)][0]})
    report.update(run_id=run_id,source_entries=source_count,archive_name=archive_names['ru'],
                  archives=copy.deepcopy(reports),release_approved=False,issues=issues,coverage=coverage)
    atomic_write(root/'export-report.json',canonical_json(report));return report


def render(node):
    if isinstance(node,str): return html.escape(node)
    if isinstance(node,list): return ''.join(map(render,node))
    if not isinstance(node,dict): return ''
    if node.get('type')=='structured-content': return render(node['content'])
    tag=node.get('tag','div')
    if tag not in {'div','span','ol','ul','li','ruby','rt','a','br'}: tag='span'
    attrs=''.join(f' data-sc-{html.escape(str(k),quote=True)}="{html.escape(str(v),quote=True)}"' for k,v in node.get('data',{}).items())
    if node.get('lang'): attrs+=f' lang="{html.escape(node["lang"],quote=True)}"'
    if tag=='a' and node.get('href','').startswith(('https://','http://','?','#')): attrs+=f' href="{html.escape(node["href"],quote=True)}"'
    style=';'.join(re.sub('[A-Z]',lambda m:'-'+m[0].lower(),k)+':'+str(v) for k,v in node.get('style',{}).items())
    if style: attrs+=f' style="{html.escape(style,quote=True)}"'
    return f'<{tag}{attrs}>'+render(node.get('content',''))+f'</{tag}>'


def review_sample(scope, articles, sample_per_batch, batch_size=5000):
    """Choose a deterministic random sample inside each consecutive scope block."""
    if not 1 <= sample_per_batch <= batch_size:
        raise ValueError('sample size must fit inside one review block')
    entries={entry['entry_id']:entry for entry in scope['entries']}
    if len(entries)!=len(articles) or {a['entry_id'] for a in articles}!=set(entries):
        raise ValueError('review articles differ from the frozen scope')
    ordered=sorted(articles,key=lambda article:entries[article['entry_id']]['ordinal'])
    sampled=[];batch_count=(len(ordered)+batch_size-1)//batch_size
    for batch_index in range(batch_count):
        members=ordered[batch_index*batch_size:(batch_index+1)*batch_size]
        if len(ordered)>batch_size and len(members)>sample_per_batch:
            rng=random.Random(f"{scope['manifest']['scope_id']}:{batch_index+1}")
            members=sorted(rng.sample(members,sample_per_batch),key=lambda a:entries[a['entry_id']]['ordinal'])
        sampled.extend((batch_index+1,article) for article in members)
    return sampled,batch_count


def site(root,scope,packet,report,sample_per_batch=100):
    from wadoku_select200 import CATEGORIES
    category_labels={**CATEGORIES,'source-prefix':'Первые записи источника',
                     'reference-dependency':'Ссылочная зависимость','closure-filler':'Заполнитель замкнутой выборки'}
    issue_file=root/'manual-issues.json'
    manual=json.loads(issue_file.read_text()) if issue_file.exists() else []
    issues=manual+report['issues']; by_entry={}
    levels={'review':'нужна проверка','warning':'проблема','minor':'стиль'}
    def issue_text(i):
        text=html.escape(i['message'])
        if i.get('source_url','').startswith('https://'):
            text+=f' <a href="{html.escape(i["source_url"],quote=True)}">Источник проверки</a>'
        return f'[{levels.get(i["severity"],i["severity"])}] '+text
    for issue in issues: by_entry.setdefault(issue['entry_id'],[]).append(issue)
    with zipfile.ZipFile(root/'site/dist'/report['archive_name']) as z:
        rows=[r for n in z.namelist() if n.startswith('term_bank_') for r in json.loads(z.read(n))]
        css=z.read('styles.css')
    by_sequence={}
    for row in rows: by_sequence.setdefault(row[6],row)
    entries={e['entry_id']:e for e in scope['entries']};cards=[];ordered=packet['articles']
    sampled,batch_count=review_sample(scope,ordered,sample_per_batch)
    for batch_index,article in sampled:
        eid=article['entry_id'];entry=entries[eid];category=entry['categories'][0]
        errors=by_entry.get(eid,[])
        error_html=''.join(f'<p class="issue"><strong>{html.escape(i["id"])} · {html.escape(i["category"])}</strong> {issue_text(i)}</p>' for i in errors)
        bodies=''.join(render(by_sequence[s][5]) for s in report['coverage'][str(eid)]['sequences'])
        keys='　'.join(dict.fromkeys(k[0] for k in report['coverage'][str(eid)]['keys']))
        cards.append(f'<details class="entry" id="e{eid}" data-batch="{batch_index}" data-category="{category}" data-errors="{bool(errors)}"><summary><span lang="ja">{html.escape(article["expression"])}</span> <small>{html.escape(article["reading"])} · {eid} {"⚠" if errors else ""}</small></summary>{error_html}<p class="keys" lang="ja">{html.escape(keys)}</p><div class="dictionary">{bodies}</div></details>')
    sampled_ids={article['entry_id'] for _,article in sampled}
    sampled_issues=[i for i in issues if i['entry_id'] in sampled_ids]
    issues_html=''.join(f'<p><a href="#e{i["entry_id"]}">{html.escape(i["id"])}</a> · {html.escape(i["category"])} — {issue_text(i)}</p>' for i in sampled_issues)
    options=''.join(f'<option value="{k}">{html.escape(category_labels.get(k,k))}</option>'
                    for k in scope['manifest']['category_counts'])
    tabs=''.join(f'<button type="button" class="batch-tab" data-batch="{i}">{(i-1)*5000+1}–{min(i*5000,len(ordered))}</button>' for i in range(1,batch_count+1))
    de_archive=report['archives']['de']['archive_name']
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Wadoku — диагностика {len(ordered)} статей</title><link rel="stylesheet" href="styles.css"><link rel="stylesheet" href="review.css"><body><main>
<header><h1>Wadoku · {len(ordered)} статей</h1><p>Русский перевод: Luna. Немецкий словарь: исходный Wadoku XML. Показана детерминированная случайная выборка до {sample_per_batch} статей из каждого блока 5 000. Полная ручная вычитка не проводилась; это не релиз.</p><a class="download" href="{html.escape(report['archive_name'],quote=True)}">Скачать JP→RU ZIP</a> <a class="download secondary" href="{html.escape(de_archive,quote=True)}">Скачать JP→DE ZIP</a></header>
<div class="batch-tabs" role="tablist">{tabs}</div>
<details class="errors"><summary>Проблемы в показанной выборке — {len(sampled_issues)}; во всём экспорте — {len(issues)}</summary>{issues_html}</details>
<nav><input id="search" aria-label="Найти статью" placeholder="Слово, чтение или ID"><select id="category" aria-label="Категория"><option value="">Все категории</option>{options}</select><label><input type="checkbox" id="errorsOnly"> Только с замечаниями</label><output id="count"></output></nav>
{''.join(cards)}</main><script src="review.js"></script></body></html>'''
    atomic_write(root/'site/dist/index.html',page.encode());atomic_write(root/'site/dist/styles.css',css)
    for name in ('review.css','review.js'):
        atomic_write(root/'site/dist'/name,(Path('assets/wadoku-review')/name).read_bytes())
    atomic_write(root/'site/dist/issues.json',canonical_json(issues))
    print({'source_entries':len(ordered),'sampled_entries':len(cards),'issues':len(issues),'site':str(root/'site/dist/index.html')})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',type=int,required=True);parser.add_argument('--root',type=Path,default=Path('work/wadoku-xml/stress200'));parser.add_argument('--sample-per-batch',type=int,default=100);args=parser.parse_args()
    if not 1 <= args.sample_per_batch <= 5000: raise ValueError('sample size must be 1–5000')
    scope=json.loads((args.root/'scope.json').read_text());packet=json.loads((args.root/'review-packet.json').read_text())
    report=export(args.run_id,args.root,scope,packet);site(args.root,scope,packet,report,args.sample_per_batch)
