"""Source-bound Luna-high stress selection; never invent dictionary entries."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time

from wadoku_focus_scope import scan
from run_codex_batches import dispatch_one
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes, sha256_file
from jitendex_ru.wadoku_xml import canonical_entry
from jitendex_ru.wadoku_quality import example_candidates
from jitendex_ru.wadoku_scope import store_scope
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.database import Database

CATEGORIES = {
 'edge_templates':'Открытый слот в начале или конце; суффиксный и префиксный поиск',
 'internal_templates':'Внутренние слоты, отрицательные рамки, несколько разрывов',
 'phrases':'Устойчивые словосочетания, залог, управление и идиомы',
 'sentences':'Полные предложения, лицо, время, числа, примеры',
 'grammar':'Частицы и конструкции: уступка, модальность, желание, условие',
 'counters':'Счётные слова, единицы, числа и отношения количеств',
 'polysemy':'Несколько значений, пояснения и доменные метки',
 'register':'Грубая, разговорная, вежливая, устаревшая речь',
 'names_terms':'Имена собственные, научные и культурные термины',
 'forms_pitch':'Варианты написания и чтения, несколько питчей, не смешивать омонимы',
}
ROOT=Path('work/wadoku-xml/stress200')
XML=Path('work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml')
EXCLUDES=[Path('work/wadoku-xml/focus100/scope.json'),Path('work/wadoku-xml/pilot-v6/scope-193.json')]

def candidates():
    excluded={e['entry_id'] for path in EXCLUDES for e in json.loads(path.read_text())['entries']}
    pools={k:[] for k in CATEGORIES}
    for ordinal,elem in scan(XML):
        eid=int(elem.attrib['id'])
        if eid in excluded: continue
        orths=[''.join(n.itertext()) for n in elem.findall('{*}form/{*}orth')]
        if not orths: continue
        word=orths[0]
        tags=[n.tag.rsplit('}',1)[-1] for n in elem.iter()]
        uses=[' '.join([n.text or '',*n.attrib.values()]) for n in elem.findall('.//{*}usg')]
        refs=[dict(n.attrib) for n in elem.findall('.//{*}ref')]
        gloss=[''.join(n.itertext()) for n in elem.findall('.//{*}tr')]
        text=' '.join(gloss)
        mask=re.search('[…~〜～]',word)
        matches=[]
        if mask:
            matches.append('edge_templates' if mask.start()==0 or mask.end()==len(word) else 'internal_templates')
        if not mask and len(word)>=5 and re.search('[をにがのともでは]',word) and refs: matches.append('phrases')
        if word.endswith(('。','！','？')) or any(r.get('subentrytype')=='XSatz' for r in refs): matches.append('sentences')
        if any(t in tags for t in ('part','particle','conj','aux','auxV')) or (mask and any(x in text for x in ('wenn','obwohl','sollen','müssen','mögen'))): matches.append('grammar')
        if 'Zählwort' in text or re.search(r'\d+.*(?:Blatt|Meter|Stück|Liter)',text): matches.append('counters')
        if tags.count('sense')>=4 and (uses or 'expl' in tags): matches.append('polysemy')
        if any(re.search('vulg|coll|hon|pol|obs|slang|derog',u,re.I) for u in uses): matches.append('register')
        if any(re.search('Name|Eigen|Person|Bot|Zool|Buddh|Myth',u) for u in uses) or any('scientific' in n.attrib.values() for n in elem.iter()): matches.append('names_terms')
        if len(orths)>=3 or tags.count('accent')>=2: matches.append('forms_pitch')
        if not matches: continue
        rank=sha256_bytes(f'stress200-v1:{eid}'.encode())
        row={'entry_id':eid,'japanese':word,'reading':elem.findtext('{*}form/{*}reading/{*}hira',''),
             'german_hint':text[:170],'labels':uses[:4],'senses':tags.count('sense'),'spellings':orths[:3]}
        for cat in matches:
            pool=pools[cat]
            if len(pool)<60 or rank<pool[-1][0]:
                pool.append((rank,row));pool.sort(key=lambda x:x[0]);del pool[60:]
    if any(len(v)<25 for v in pools.values()): raise ValueError({k:len(v) for k,v in pools.items()})
    result={'categories':CATEGORIES,'quota_per_category':20,'pools':{k:[r for _,r in v] for k,v in pools.items()}}
    atomic_write(ROOT/'candidates.json',canonical_json(result))
    print({k:len(v) for k,v in pools.items()},flush=True)

def select():
    request=ROOT/'candidates.json'
    data=json.loads(request.read_text())
    schema={'type':'object','additionalProperties':False,'required':['selections'],'properties':{'selections':{
        'type':'array','minItems':200,'maxItems':200,'items':{'type':'object','additionalProperties':False,
        'required':['entry_id','category','reason'],'properties':{'entry_id':{'type':'integer'},
        'category':{'type':'string','enum':list(CATEGORIES)},'reason':{'type':'string'}}}}}}
    prompt=Path('prompts/select_luna_wadoku_stress200_v1.txt').read_text()
    for n in range(1,4):
        response=ROOT/f'selection-attempt-{n}.json'
        item={'attempt_id':f'select200-{n}','batch_id':'select200','request_path':str(request),'response_path':str(response),'model_id':'gpt-5.6-luna','reasoning_effort':'high'}
        started=time.monotonic()
        result=dispatch_one(item,prompt,'translation',output_schema=schema,request_timeout_seconds=600)
        atomic_write(response.with_suffix('.events.jsonl'),result.stdout.encode())
        atomic_write(response.with_suffix('.stderr.txt'),result.stderr.encode())
        log={'attempt':n,'model':'gpt-5.6-luna','reasoning_effort':'high','duration_s':time.monotonic()-started,'returncode':result.returncode,'usage':result.usage}
        atomic_write(response.with_suffix('.metrics.json'),canonical_json(log))
        if result.returncode:
            time.sleep(10);continue
        chosen=json.loads(response.read_text())['selections']
        valid=(len(chosen)==200 and len({r['entry_id'] for r in chosen})==200 and Counter(r['category'] for r in chosen)==Counter({k:20 for k in CATEGORIES})
               and all(r['entry_id'] in {v['entry_id'] for v in data['pools'][r['category']]} for r in chosen))
        if valid:
            atomic_write(ROOT/'selection.json',canonical_json({'selections':chosen,'attempt':n,'request_sha256':sha256_file(request),'prompt_sha256':sha256_file(Path('prompts/select_luna_wadoku_stress200_v1.txt'))}))
            print(log,flush=True);return
        raise ValueError('Luna selection violates membership, uniqueness or category quota; preserve response')
    raise RuntimeError('Luna selection unavailable after three attempts')

def freeze():
    config=load_profile(Path('config.wadoku.rich.luna.toml'))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get('dbname')!='wadoku_rich_pilot': raise ValueError('pilot DB only')
    selection=json.loads((ROOT/'selection.json').read_text())
    selected={r['entry_id']:r for r in selection['selections']}
    summaries={};parents=set()
    for _,elem in scan(XML):
        eid=int(elem.attrib['id'])
        summaries[eid]={'expression':elem.findtext('{*}form/{*}orth',''),'reading':elem.findtext('{*}form/{*}reading/{*}hira','')}
        if eid in selected: parents.update(int(n.get('id')) for n in elem.findall('.//{*}ref') if n.get('type')=='main' and n.get('id'))
    entries=[];examples=[];contexts={};targets=set()
    for ordinal,elem in scan(XML):
        eid=int(elem.attrib['id'])
        refs=[n for n in elem.findall('.//{*}ref') if n.get('id')]
        if eid not in selected and eid not in parents and not any(n.get('type')=='main' and int(n.get('id')) in selected for n in refs): continue
        value=canonical_entry(elem)
        if eid in selected:
            entries.append({'ordinal':ordinal,'entry_id':eid,'categories':[selected[eid]['category']],'selection_reason':selected[eid]['reason'],'source':value,'source_sha256':sha256_bytes(canonical_json(value))})
            targets.update(int(n.get('id')) for n in refs)
        if eid in parents: contexts[str(eid)]=value
        examples.extend(example_candidates([value],set(selected)))
    digest=sha256_file(XML)
    if len(entries)!=200 or digest!=config.raw['source']['xml_sha256']: raise ValueError('source coverage/hash mismatch')
    manifest={'version':'wadoku-stress200-scope-v1','xml_sha256':digest,'entry_count':200,'source_entry_count':len(summaries),
        'candidate_count':len(examples),'category_counts':dict(Counter(r['category'] for r in selected.values())),
        'parent_contexts':contexts,'reference_targets':{str(i):summaries[i] for i in targets if i in summaries},
        'selection_sha256':sha256_file(ROOT/'selection.json'),'selection_model':'gpt-5.6-luna','selection_reasoning':'high'}
    manifest['scope_id']=sha256_bytes(canonical_json([manifest,[(e['entry_id'],e['source_sha256']) for e in entries]]))
    data={'manifest':manifest,'entries':entries,'candidates':examples}
    db=Database(config)
    with db.connect() as c:
        baseline=json.loads(EXCLUDES[0].read_text())['manifest']['scope_id']
        snapshot=c.execute('SELECT snapshot_id FROM wadoku_scope WHERE id=?',(baseline,)).fetchone()[0]
        result=store_scope(c,snapshot,data);c.commit()
    db.close();atomic_write(ROOT/'scope.json',canonical_json(data));print(result,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['candidates','select','freeze']);args=parser.parse_args()
    globals()[args.command]()
