"""Build an explicitly diagnostic, exact-scope ZIP and its source-bound review page.

The production export gates remain unchanged. Unresolved entries retain literal
source lookup keys and a visible warning, never a guessed lemma or expansion.
"""
import argparse
import copy
import html
import json
from pathlib import Path
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


def literal_fallback(article):
    """Keep every translated unit for inspection if native assembly cannot render it."""
    senses={}
    for unit in article['units']:
        request=unit['request_unit'];text=unit['target_text']
        if unit['role']=='glossary_set': text='; '.join(json.loads(text))
        # Protected objects remain source objects, never guessed translations.
        for fragment in request.get('protected_fragment_context',[]):
            def plain(n): return n.get('text','')+''.join(plain(x)+x.get('tail','') for x in n.get('children',[]))
            replacement=plain(fragment.get('tree',{}))
            if fragment.get('placeholder'):
                text=text.replace(fragment['placeholder'],replacement or '[объект источника]')
        content={'tag':'div','lang':'ru','content':text}
        if unit['role']=='example_translation':
            content={'tag':'div','data':{'class':'extra-box','content':'example-sentence'},'content':[
                {'tag':'div','lang':'ja','data':{'content':'example-sentence-a'},'content':request['japanese']},
                {'tag':'div','lang':'ru','data':{'content':'example-sentence-b'},'content':text}]}
        senses.setdefault(request.get('sense_path'),[]).append(content)
    return [{'type':'structured-content','content':{'tag':'div','content':[
        {'tag':'ol','content':[{'tag':'li','content':v} for v in senses.values()]}]}}]


def export(run_id, root, scope, packet):
    config=load_profile(Path('config.wadoku.rich.luna.toml'))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname')!='wadoku_rich_pilot': raise ValueError('pilot DB only')
    expected={e['entry_id'] for e in scope['entries']}
    if len(expected)!=200 or {a['entry_id'] for a in packet['articles']}!=expected: raise ValueError('exact 200-entry scope required')
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
    issues=[]; rendered={};coverage={}
    for value,targets in entries:
        eid=value['entry_id']; decision=decisions[eid]; warnings=[]
        if decision.get('article_policy')!='independent':
            warnings.append('Владелец статьи не разрешён в этой выборке; показана исходная запись, не утверждённая самостоятельная лемма.')
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
        rendered[eid]=(rows,meta);coverage[str(eid)]={'sequences':sorted({r[6] for r in rows}),'keys':[[r[0],r[1]] for r in rows]}
    output=root/'site/dist/wadoku-stress200.zip'
    license_path=Path('work/wadoku-xml/source/wadoku-xml-20260705/LICENCE')
    candidates=list(license_path.parent.glob('*'))
    license_path=next((p for p in candidates if p.is_file() and p.stat().st_size<100000 and sha256_file(p)==config.raw['source']['license_sha256']),None)
    if license_path is None: raise ValueError('verified source license absent')
    report=build_rich_archive(iter(entries),output,language='ru',labels=labels,license_text=license_path.read_bytes(),
        title=f'Wadoku RU · стресс 200 · {run_id}',revision=f'stress200-run-{run_id}',source_url=config.raw['source']['url'],
        source_sha256=config.raw['source']['sha256'],export_audit_id=f'inspection-{run_id}',
        row_factory=lambda value,*args:rendered[value['entry_id']],description_note='Диагностический пилот. Только 200 выбранных исходных записей. Известные ошибки и неразрешённые шаблоны указаны на странице проверки. Не релиз.')
    report.update(validate_archive(output,Path('schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162')))
    report.update(run_id=run_id,sha256=sha256_file(output),source_entries=200,release_approved=False,issues=issues,coverage=coverage)
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


def site(root,scope,packet,report):
    from wadoku_select200 import CATEGORIES
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
    with zipfile.ZipFile(root/'site/dist/wadoku-stress200.zip') as z:
        rows=[r for n in z.namelist() if n.startswith('term_bank_') for r in json.loads(z.read(n))]
        css=z.read('styles.css')
    by_sequence={}
    for row in rows: by_sequence.setdefault(row[6],row)
    entries={e['entry_id']:e for e in scope['entries']}; cards=[]
    for article in packet['articles']:
        eid=article['entry_id'];entry=entries[eid];category=entry['categories'][0]
        errors=by_entry.get(eid,[])
        error_html=''.join(f'<p class="issue"><strong>{html.escape(i["id"])} · {html.escape(i["category"])}</strong> {issue_text(i)}</p>' for i in errors)
        bodies=''.join(render(by_sequence[s][5]) for s in report['coverage'][str(eid)]['sequences'])
        keys='　'.join(dict.fromkeys(k[0] for k in report['coverage'][str(eid)]['keys']))
        cards.append(f'<details class="entry" id="e{eid}" data-category="{category}" data-errors="{bool(errors)}"><summary><span lang="ja">{html.escape(article["expression"])}</span> <small>{html.escape(article["reading"])} · {eid} {"⚠" if errors else ""}</small></summary>{error_html}<p class="keys" lang="ja">{html.escape(keys)}</p><div class="dictionary">{bodies}</div></details>')
    issues_html=''.join(f'<p><a href="#e{i["entry_id"]}">{html.escape(i["id"])}</a> · {html.escape(i["category"])} — {issue_text(i)}</p>' for i in issues)
    options=''.join(f'<option value="{k}">{html.escape(v)}</option>' for k,v in CATEGORIES.items())
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Wadoku — стресс-пилот 200</title><link rel="stylesheet" href="styles.css"><link rel="stylesheet" href="review.css"><body><main>
<header><h1>Wadoku · 200 новых статей</h1><p>Перевод Luna, вычитка Astra. Диагностический словарь с известными ошибками, не релиз.</p><a class="download" href="wadoku-stress200.zip">Скачать Yomitan ZIP</a><p>Отключите прошлые пилоты, импортируйте ZIP и сканируйте японский текст. Здесь показан текст из этого же ZIP; настоящий popup проверяйте в Yomitan.</p></header>
<details class="errors"><summary>Найденные проблемы — COUNT</summary>ISSUES</details>
<nav><input id="search" aria-label="Найти статью" placeholder="Слово, чтение или ID"><select id="category" aria-label="Категория"><option value="">Все категории</option>OPTIONS</select><label><input type="checkbox" id="errorsOnly"> Только с замечаниями</label><output id="count"></output></nav>
CARDS</main><script src="review.js"></script></body></html>'''
    page=page.replace('COUNT',str(len(issues))).replace('ISSUES',issues_html).replace('OPTIONS',options).replace('CARDS',''.join(cards))
    atomic_write(root/'site/dist/index.html',page.encode());atomic_write(root/'site/dist/styles.css',css)
    for name in ('review.css','review.js'):
        atomic_write(root/'site/dist'/name,(Path('assets/wadoku-review')/name).read_bytes())
    atomic_write(root/'site/dist/issues.json',canonical_json(issues))
    print({'entries':len(cards),'issues':len(issues),'site':str(root/'site/dist/index.html')})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',type=int,required=True);parser.add_argument('--root',type=Path,default=Path('work/wadoku-xml/stress200'));args=parser.parse_args()
    scope=json.loads((args.root/'scope.json').read_text());packet=json.loads((args.root/'review-packet.json').read_text())
    report=export(args.run_id,args.root,scope,packet);site(args.root,scope,packet,report)
