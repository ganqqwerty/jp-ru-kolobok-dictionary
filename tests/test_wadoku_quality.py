import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jitendex_ru.wadoku_quality import (
    ISSUE_IDS, example_candidates, parse_devoicing, pronunciation_records,
    quality_gate, select_examples, pronunciation_groups,
    translation_projection, example_translation_unit, apply_source_corrections, source_identity,
)
from jitendex_ru.wadoku_xml import canonical_entry, label_catalog, structured_entry, yomitan_rows


def source(body, *, form=None, entry_id=1):
    return canonical_entry(ET.fromstring(f'<entry id="{entry_id}">'
        + (form or '<form><orth>語</orth><reading><hira>ご</hira></reading></form>')
        + body + '</entry>'))


LABELS = label_catalog(Path('terminology/wadoku-xml-labels-v2.json'))


def test_exact_note_duplicate_is_hidden_only_within_same_sense():
    from jitendex_ru.wadoku_xml import sense_translation_entry
    value=sense_translation_entry(source('<sense><trans><tr>Stück</tr></trans><def>Zählwort</def></sense>'
                                        '<sense><trans><tr>anderes</tr></trans><def>Zählwort</def></sense>'))
    targets={i: (['счётное слово'] if b['role']=='glossary_set' and i==0 else
                 ['другое'] if b['role']=='glossary_set' else 'Счётное слово.') for i,b in enumerate(value['blocks'])}
    result=json.dumps(structured_entry(value,'ru',LABELS,targets),ensure_ascii=False)
    assert result.count('Счётное слово.') == 1


def test_v6_sentence_task_is_explicit_without_changing_output_role():
    value = source('<sense><trans><tr>Es war mein Wunsch.</tr></trans></sense>'
                   '<ref type="main" subentrytype="XSatz" id="2"/>',
                   form='<form><orth>私の願いであった。</orth></form>')
    versions = dict(labels='test', morphology='test', examples='test', corrections='test', prompt='test', schema='rich-v6')
    unit = translation_projection(value, versions=versions)['units'][0]
    assert unit['role'] == 'glossary_set'
    assert unit['task_type'] == 'sentence_translation'
    assert unit['japanese'] == '私の願いであった。'
    versions['schema'] = 'rich-v5'
    assert 'task_type' not in translation_projection(value, versions=versions)['units'][0]


def test_v7_supplies_lookup_spelling_and_separate_note_context():
    value=source('<sense><trans><tr>Stück</tr></trans><def>Zählwort</def></sense>',
        form='<form><orth midashigo="true">×帖</orth><orth>帖</orth><reading><hira>ちょう</hira></reading></form>')
    versions=dict(labels='test',morphology='test',examples='test',corrections='test',prompt='test',schema='rich-v7')
    unit=translation_projection(value,versions=versions)['units'][0]
    assert unit['japanese']=='帖' and unit['reading']=='ちょう'
    assert unit['separate_notes']==[{'role':'definition','text':'Zählwort'}]


def test_pitch_scope_and_duplicates_are_preserved_without_glossary_digits():
    value = source('<sense><accent>0</accent><trans><tr>gut</tr></trans></sense>'
                   '<sense><accent>0</accent><accent>0</accent><accent>3</accent><trans><tr>gut</tr></trans></sense>',
                   form='<form><orth>きつけ</orth><reading><hira>きつけ</hira>'
                   '<hatsuon>き･[Dev]つけ</hatsuon><accent>0</accent><accent>3</accent></reading></form>')
    original = copy.deepcopy(value)
    parsed = pronunciation_records(value)
    assert not parsed['issues']
    assert [p['scope'] for p in parsed['accents']] == ['entry', 'entry', 'sense:1', 'sense:2', 'sense:2']
    rows, metadata = yomitan_rows(value, 'ru', LABELS, {0: 'хороший', 1: 'добрый'})
    assert metadata[0][2]['pitches'] == [{'position': 0, 'devoice': [2]}, {'position': 3, 'devoice': [2]}]
    assert len(rows) == 2 and rows[0][6] != rows[1][6]
    assert 'хороший' in json.dumps(rows[0], ensure_ascii=False)
    assert 'хороший' not in json.dumps(rows[1], ensure_ascii=False)
    assert value == original


def test_identical_meanings_with_two_accents_stay_one_article():
    value = source('<sense><accent>0</accent><accent>1</accent><trans><tr>gut</tr></trans></sense>',
                   form='<form><orth>語</orth><reading><hira>ご</hira><accent>0</accent><accent>1</accent></reading></form>')
    groups = pronunciation_groups(value)
    assert len(groups['groups']) == 1
    rows, meta = yomitan_rows(value, 'ru', LABELS, {0: 'слово'})
    assert len(rows) == 1 and rows[0][6] == 1
    assert len(meta[0][2]['pitches']) == 2


def test_unmarked_sense_without_entry_pitch_is_not_guessed():
    value = source('<sense><accent>0</accent></sense><sense/>')
    assert pronunciation_groups(value)['issues'][0]['code'] == 'unresolved_sense_pitch'


def test_bad_devoicing_fails_closed():
    assert parse_devoicing('[Dev]ひと･かい', 'ひとかい') == [1]
    assert parse_devoicing('[Dev]しゅっぱつ', 'しゅっぱつ') == [1]
    assert parse_devoicing('[Dev]す[Dev]き', 'すき') == [1, 2]
    assert parse_devoicing('[Gr]ま[Dev]くしみりあん', 'まくしみりあん') == [2]
    for notation in ('き[Unknown]つけ', 'き[Dev]つけ[Dev]', 'き[Dev]け', 'し[Dev]ゅつ'):
        if '[Dev]' in notation:
            with pytest.raises(ValueError):
                parse_devoicing(notation, 'きつけ')


def test_attribute_usage_and_entry_label_placement():
    value = source('<usg reg="coll"/><sense><trans><tr>gut</tr></trans></sense>'
                   '<sense related="true"><usg reg="lit"/><trans><tr>gut</tr></trans></sense>')
    result = structured_entry(value, 'ru', LABELS, {0: 'хороший', 1: 'добрый'})
    contents = result['content']['content']
    assert contents[0]['data']['content'] == 'entry-tags'
    assert 'разговорное' in json.dumps(contents[0], ensure_ascii=False)
    assert 'литературное' in json.dumps(contents[1], ensure_ascii=False)
    assert 'Связанное значение' in json.dumps(contents[1], ensure_ascii=False)
    assert '"content": []' not in json.dumps(result)


def test_unknown_register_blocks_instead_of_empty_badge():
    with pytest.raises(ValueError, match='missing Wadoku label'):
        structured_entry(source('<sense><usg reg="new-code"/></sense>'), 'ru', LABELS)


def test_reference_inside_explanation_remains_inline():
    value = source('<sense><expl>weniger als <ref id="2" type="other"><jap>私</jap></ref></expl></sense>')
    rendered = structured_entry(value, 'ru', LABELS, {0: 'менее формальное, чем ⟦WDXP0001⟧'})
    block = rendered['content']['content'][0]
    assert block['content'][1]['tag'] == 'span'
    assert block['content'][1]['content'][0]['tag'] == 'a'
    assert 'другая ссылка' not in json.dumps(rendered, ensure_ascii=False)


def test_reference_template_and_unresolved_transcription_block():
    with pytest.raises(ValueError, match='reference template'):
        structured_entry(source('<sense><ref id="2" type="main"><jap>…たち</jap></ref></sense>'), 'ru', LABELS)
    value = source('<sense><expl>für <transcr>morau</transcr></expl></sense>')
    with pytest.raises(ValueError, match='Unresolved Japanese transcription'):
        structured_entry(value, 'ru', LABELS, {0: 'для ⟦WDXP0001⟧'})
    value['transcription_resolutions'] = {'morau': 'もらう'}
    assert 'もらう' in json.dumps(structured_entry(value, 'ru', LABELS, {0: 'для ⟦WDXP0001⟧'}), ensure_ascii=False)


def test_example_selection_never_treats_vwbsp_as_a_decision():
    child = source('<sense><trans><tr>fremde Person</tr></trans></sense>'
                   '<ref id="9" type="main" subentrytype="VwBsp"/>', entry_id=2)
    candidates = example_candidates([child], {9})
    assert select_examples(candidates, [])['unresolved'] == candidates
    c = candidates[0]
    decision = {k: c[k] for k in ('parent_id', 'child_id', 'relation_path', 'source_sha256')}
    decision.update(state='accept', reviewer='human', reason='useful phrase')
    selected = select_examples(candidates, [decision])
    assert len(selected['selected']) == 1
    assert selected['selected'][0]['sense_path'] is None
    assert candidates[0]['selection_state'] == 'unresolved'
    with pytest.raises(ValueError, match='sense attachment'):
        select_examples(candidates, [{**decision, 'sense_path': '/entry[1]/sense[1]'}])
    with pytest.raises(ValueError, match='stale'):
        select_examples(candidates, [{**decision, 'source_sha256': 'changed'}])


def test_quality_gate_requires_all_issues_and_semantic_evidence():
    assert not quality_gate({}, 'identity')['release_ready']
    ledger = {'artifact_identity': 'identity', 'issues': [
        {'id': issue, 'status': 'fixed-with-evidence', 'reviewer': 'human',
         'evidence': 'test report', 'regression_case': 'case'} for issue in ISSUE_IDS]}
    assert not quality_gate(ledger, 'identity')['release_ready']

    for gate in ('implementation_contract', 'semantic_review', 'lookup_smoke', 'user_pilot_approval'):
        ledger[gate] = {'passed': True, 'reviewer': 'human', 'evidence': 'report'}
    assert quality_gate(ledger, 'identity')['release_ready']
    assert not quality_gate(ledger, 'changed')['release_ready']
    ledger['issues'][0]['status'] = 'reviewed-limitation'
    assert not quality_gate(ledger, 'identity')['release_ready']


def test_projection_is_versioned_complete_and_context_sensitive():
    value = source('<sense><trans><tr>gut</tr></trans><trans><tr>fein</tr></trans>'
                   '<expl>mit <jap>語</jap></expl></sense>')
    versions = {k: 'test-v1' for k in ('labels', 'morphology', 'examples', 'corrections', 'prompt', 'schema')}
    original = copy.deepcopy(value)
    projected = translation_projection(value, versions=versions)
    assert [u['role'] for u in projected['units']] == ['glossary_set', 'explanation']
    assert len(projected['units'][0]['member_paths']) == 2
    assert projected['units'][1]['protected_tokens'] == ['⟦WDXP0001⟧']
    assert value == original
    changed = translation_projection(value, versions={**versions, 'prompt': 'test-v2'})
    assert changed['units'][0]['unit_id'] != projected['units'][0]['unit_id']
    value['lookup_aliases'] = [{'expression': '言葉'}]
    assert translation_projection(value, versions=versions)['context_sha256'] != projected['context_sha256']


def test_source_correction_keeps_raw_evidence_and_is_path_bound():
    value = source('<sense/><ref id="2" type="syn"><jap>拾う</jap></ref>')
    original = copy.deepcopy(value)
    correction = {'entry_id': 1, 'path': '/entry[1]/ref[1]', 'attribute': 'type',
                  'source_sha256': source_identity(value), 'original': 'syn', 'replacement': 'anto',
                  'reviewer': 'human', 'reason': 'source error', 'evidence': 'dictionary', 'version': 'v1'}
    derived = apply_source_corrections(value, [correction])
    assert value == original
    assert derived['tree']['children'][-1]['attributes']['type'] == 'anto'
    assert derived['source_correction_audit']['source_sha256'] == source_identity(value)
    with pytest.raises(ValueError, match='stale'):
        apply_source_corrections(value, [{**correction, 'source_sha256': 'bad'}])


def test_example_unit_requires_reviewed_meaning_path():
    child = source('<sense><trans><tr>fremde Person</tr></trans></sense>'
                   '<ref id="9" type="main" subentrytype="VwBsp"/>', entry_id=2)
    candidates = example_candidates([child], {9})
    c = candidates[0]
    path = c['source_blocks'][0]['xml_path']
    decision = {k: c[k] for k in ('parent_id', 'child_id', 'relation_path', 'source_sha256')}
    decision.update(state='accept', reviewer='human', reason='useful phrase', source_path=path)
    accepted = select_examples(candidates, [decision])['selected'][0]
    unit = example_translation_unit(accepted, path)
    assert unit['role'] == 'example_translation'
    assert unit['source_text'] == 'fremde Person'
    assert unit['japanese'] == '語'
    with pytest.raises(ValueError):
        example_translation_unit(c, path)
