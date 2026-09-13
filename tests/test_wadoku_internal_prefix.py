import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from jitendex_ru.wadoku_classification import fixed_template_alias, make_request, validate_decision, response_schema
from jitendex_ru.wadoku_assembly import lookup_rows, build_grouped_archive
from jitendex_ru.wadoku_xml import canonical_entry, label_catalog, build_rich_archive
from jitendex_ru.schema_validation import validate_archive


@pytest.mark.parametrize('form,reading,key,kana', [
    ('どうにか…する', 'どうにか…する', 'どうにか', 'どうにか'),
    ('費用が…だけかかる', 'ひようが…だけかかる', '費用が', 'ひようが'),
    ('誰も…ない', 'だれも…ない', '誰も', 'だれも'),
    ('費用が…より…かかる', 'ひようが…より…かかる', '費用が', 'ひようが'),
])
def test_internal_prefix_is_exact_and_schema_versioned(form, reading, key, kana):
    assert fixed_template_alias(form, reading, 'internal_prefix') == (key, kana)
    value = canonical_entry(ET.fromstring(f'<entry id="1"><form><orth>{form}</orth><reading><hira>{reading}</hira></reading></form><sense/></entry>'))
    request = make_request(value, [], [], 'scope')
    result = {'batch_id':request['batch_id'], 'manifest_sha256':request['manifest_sha256'], 'entry_id':1,
        'article_policy':'independent', 'parent_id':None, 'lookup_policy':'independent_direct',
        'reason':'Конструкция.', 'confidence':'high', 'lookup_needs_review':None, 'examples':[],
        'lookup_aliases':[{'source_form':form, 'expression':key, 'reading':kana, 'kind':'internal_prefix',
                           'evidence_path':'/entry[1]/form[1]/orth[1]', 'reason':'Начало конструкции.'}]}
    assert validate_decision(request, result) == []
    bad = copy.deepcopy(result)
    bad['lookup_aliases'][0]['expression'] = 'ない'
    assert validate_decision(request, bad)
    legacy = {**request, 'response_schema_version':'wadoku-structure-schema-v6'}
    assert 'internal_prefix' not in response_schema(legacy)['properties']['lookup_aliases']['items']['properties']['kind']['enum']
    assert validate_decision(legacy, result)


@pytest.mark.parametrize('form,reading', [
    ('…を…する', '…を…する'), ('費用が…かかる', 'ひようがかかる'),
    ('費用が…', 'ひようが…'), ('費用が…かかる', '…ひようがかかる'),
])
def test_unsafe_internal_prefix_is_not_guessed(form, reading):
    assert fixed_template_alias(form, reading, 'internal_prefix') is None


def test_prefix_rows_keep_full_phrase_and_remove_phrase_rules_and_pitch():
    row = ['費用が…かかる', 'ひようが…かかる', 'глагол', 'v5r', 0, ['стоить'], 7, '']
    original = copy.deepcopy(row)
    rows, metadata = lookup_rows([row], [[row[0], 'pitch', {}]], {'lookup_aliases':[
        {'source_form':row[0], 'expression':'費用が', 'reading':'ひようが', 'kind':'internal_prefix'}]})
    assert rows[0][:4] == ['費用が', 'ひようが', '', '']
    assert metadata == [] and row == original
    body = json.dumps(rows[0][5], ensure_ascii=False)
    assert all(text in body for text in [row[0], row[1], 'глагол', 'стоить'])


def test_archive_merges_constructions_but_not_ordinary_word_or_other_reading(tmp_path):
    entries, decisions = [], {}
    for eid, form, reading in [(1,'どうにか…する','どうにか…する'),
                                (2,'どうにか…なる','どうにか…なる'),
                                (3,'どうにか','どうにか'),
                                (4,'どうにか…別','べつ…べつ')]:
        value = canonical_entry(ET.fromstring(f'<entry id="{eid}"><form><orth>{form}</orth><reading><hira>{reading}</hira></reading></form><sense><trans><tr>source</tr></trans></sense></entry>'))
        entries.append((value, {0:f'перевод {eid}'}))
        alias = fixed_template_alias(form, reading, 'internal_prefix')
        decisions[eid] = {'article_policy':'independent', 'lookup_policy':'independent_direct', 'parent_id':None,
            'lookup_aliases':[] if alias is None else [{'source_form':form, 'expression':alias[0], 'reading':alias[1], 'kind':'internal_prefix'}]}
    output = tmp_path/'test.zip'
    report = build_grouped_archive(entries, decisions, output,
        labels=label_catalog(Path('terminology/wadoku-xml-labels-v2.json')),
        license_text=b'test', title='test', revision='test', source_url='https://example.org',
        source_sha256='test', export_audit_id='test')
    assert report['source_entries'] == 4 and report['term_rows'] == 3
    assert len(report['prefix_lookup_groups']) == 2
    with zipfile.ZipFile(output) as archive:
        rows = json.loads(archive.read('term_bank_1.json'))
    combined = next(r for r in rows if r[0:2] == ['どうにか','どうにか'] and r[6] != 3)
    body = json.dumps(combined[5], ensure_ascii=False)
    assert all(t in body for t in ['どうにか…する','どうにか…なる','перевод 1','перевод 2'])
    assert 'перевод 3' not in body and 'перевод 4' not in body
    assert len({r[6] for r in rows}) == 3
    validate_archive(output, Path('schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162'))


def test_prefix_merge_crosses_bank_boundary(tmp_path):
    def factory(value, *_):
        eid = value['entry_id']
        form = f'始め…{eid}'
        rows, _ = lookup_rows([[form,form,'','',0,[str(eid)],eid,'']], [], {'lookup_aliases':[
            {'source_form':form,'expression':'始め','reading':'始め','kind':'internal_prefix'}]})
        if eid == 1:
            rows.extend([[str(i),'','','',0,['normal'],1,''] for i in range(10001)])
        return rows, []
    output = tmp_path/'banks.zip'
    report = build_rich_archive(iter([({'entry_id':1},None),({'entry_id':2},None)]),output,
        language='ru',labels={},license_text=b'test',title='test',revision='test',
        source_url='https://example.org',source_sha256='test',export_audit_id='test',row_factory=factory)
    assert report['term_rows'] == 10002 and report['term_banks'] == 2
    with zipfile.ZipFile(output) as archive:
        rows = [r for n in archive.namelist() if n.startswith('term_bank_') for r in json.loads(archive.read(n))]
    merged = [r for r in rows if r[0] == '始め']
    assert len(merged) == 1 and len(merged[0][5]) == 2
