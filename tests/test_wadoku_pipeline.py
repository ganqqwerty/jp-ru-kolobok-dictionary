import copy
import json
import xml.etree.ElementTree as ET

import pytest

from jitendex_ru.batch import _manifest, _pack_envelopes
from jitendex_ru.wadoku_pipeline import prepare_projection, projection_envelope, localized_projection
from jitendex_ru.wadoku_xml import canonical_entry
from jitendex_ru.validate_response import wadoku_glossary_issues


VERSIONS = {k: 'test-v1' for k in ('labels', 'morphology', 'examples', 'corrections', 'prompt', 'schema')}


def test_explicit_scientific_example_preserves_source_term_only_on_selected_path():
    from jitendex_ru.validate_response import wadoku_scientific_source, wadoku_target_issues
    value = canonical_entry(ET.fromstring('<entry id="1"><sense><trans langdesc="scientific"><tr>actio libera in causa</tr></trans><trans><tr>Heim</tr></trans></sense></entry>'))
    unit = {'local_context': {'example_source_context': value['tree']},
            'source_path': '/entry[1]/sense[1]/trans[1]/tr[1]'}
    assert wadoku_scientific_source(unit)
    assert not wadoku_scientific_source({**unit, 'source_path': '/entry[1]/sense[1]/trans[2]/tr[1]'})
    target = 'actio libera in causa — пояснение термина'
    assert wadoku_target_issues('actio libera in causa', target, [])
    assert not wadoku_target_issues('actio libera in causa', target, [], scientific_source=True)


def test_scientific_main_sense_requires_exact_annotated_member_paths():
    from jitendex_ru.validate_response import wadoku_scientific_source, wadoku_target_issues
    value = canonical_entry(ET.fromstring('<entry id="1"><sense><trans><tr>Grube</tr></trans><trans langdesc="scientific"><tr>Fossa</tr></trans><trans langdesc="scientific"><tr>Fovea</tr></trans></sense></entry>'))
    base = '/entry[1]/sense[1]'
    unit = {'local_context': {'sense_context': value['tree']['children'][0], 'sense_path': base},
            'member_paths': [base + '/trans[2]/tr[1]']}
    assert wadoku_scientific_source(unit)
    assert not wadoku_target_issues('Fossa', 'Fossa', [], scientific_source=True)
    assert wadoku_target_issues('Fossa', 'arbitrary', [], scientific_source=True)
    for members in ([base + '/trans[1]/tr[1]'],
                    [base + '/trans[2]/tr[1]', base + '/trans[1]/tr[1]'],
                    [base + '/trans[2]/tr[1]/missing[1]']):
        assert not wadoku_scientific_source({**unit, 'member_paths': members})


def test_scope_extension_refuses_to_drop_existing_entries(tmp_path):
    from jitendex_ru.wadoku_scope import inventory
    from jitendex_ru.util import sha256_file
    source_file=tmp_path/'source.xml'
    source_file.write_text('fixture')
    core=tmp_path/'core.json'
    core.write_text(json.dumps({'entries':[{'entry_id':i,'categories':[]} for i in range(150)]}))
    retained=tmp_path/'retained.json'
    retained.write_text(json.dumps({'manifest':{'xml_sha256':sha256_file(source_file),
        'core_sha256':sha256_file(core),'scope_id':'fixture'},
        'entries':[{'entry_id':i,'categories':[]} for i in range(190)]}))
    with pytest.raises(ValueError,match='exceed requested size'):
        inventory(source_file,core,expected_sha256=sha256_file(source_file),size=190,
                  retained_scope=retained,required_ids=[999])


def test_collect_example_sources_verifies_identity(tmp_path, monkeypatch):
    import jitendex_ru.wadoku_scope as scope
    from jitendex_ru.wadoku_quality import source_identity
    from jitendex_ru.util import sha256_file
    value=source()
    path=tmp_path/'source.xml'
    path.write_text('fixture')
    monkeypatch.setattr(scope,'iter_canonical_entries',lambda _: iter([(1,value)]))
    example={'selection_state':'accepted','child_id':1,'source_sha256':source_identity(value)}
    assert scope.collect_example_sources(path,sha256_file(path),[example])=={1:value}
    with pytest.raises(ValueError,match='changed'):
        scope.collect_example_sources(path,sha256_file(path),[{**example,'source_sha256':'wrong'}])
    with pytest.raises(ValueError,match='absent'):
        scope.collect_example_sources(path,sha256_file(path),[{**example,'child_id':2}])


def test_ownership_groups_preserve_members_and_distinguish_phrase_from_inflection():
    from jitendex_ru.wadoku_pipeline import ownership_groups
    independent={'article_policy':'independent','parent_id':None,'lookup_policy':'independent_direct'}
    inherited={'article_policy':'inherit_parent','parent_id':1,'lookup_policy':'deinflect_to_parent'}
    decisions={1:independent,2:inherited,3:independent,4:{**inherited,'lookup_policy':'shared_direct'}}
    groups=ownership_groups([1,2,3,4],decisions)
    assert [x['entry_id'] for x in groups[1]]==[1,2,4]
    assert groups[3]==[{'entry_id':3,'lookup_policy':'independent_direct'}]
    with pytest.raises(ValueError,match='outside scope'):
        ownership_groups([2],{2:inherited})
    with pytest.raises(ValueError,match='cycle'):
        ownership_groups([1,2],{1:{**inherited,'parent_id':2},2:inherited})
    with pytest.raises(ValueError,match='exact distinct scope'):
        ownership_groups([1,2],{1:independent})


def source():
    return canonical_entry(ET.fromstring('''<entry id="1"><form><orth>語</orth>
    <reading><hira>ご</hira></reading></form><sense><trans><tr>Wort</tr></trans>
    <trans><tr>Sprache</tr></trans><expl>mit <jap>語</jap></expl></sense></entry>'''))


def test_envelope_uses_source_order_not_lexicographic_database_pointer():
    value = canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form>' +
        ''.join(f'<sense><trans><tr>meaning {i}</tr></trans></sense>' for i in range(12)) + '</entry>'))
    p = prepare_projection(value, versions=VERSIONS, run_identity='order-test')
    rows = [{'id': u['unit_id'], 'source_sha256': u['source_sha256'],
             'role': u['role'], 'source_text': u['source_text']} for u in p['units']]
    shuffled = [rows[i] for i in sorted(range(12), key=str)]
    article = {'id': 1, 'source_sha256': 'fixture'}
    envelope = projection_envelope(article, shuffled, p)
    assert [u['unit_id'] for u in envelope['units']] == [u['unit_id'] for u in p['units']]
    legacy = copy.deepcopy(p)
    legacy['envelope_version'] = 'selected-examples-v1'
    assert [u['unit_id'] for u in projection_envelope(article, shuffled, legacy)['units']] == [r['id'] for r in shuffled]
    assert shuffled[2]['id'] == rows[10]['id']


def test_projection_round_trip_and_identity():
    value = source()
    original = copy.deepcopy(value)
    projection = prepare_projection(value, versions=VERSIONS, run_identity='run-one')
    again = prepare_projection(value, versions=VERSIONS, run_identity='run-two')
    assert projection['context_sha256'] == again['context_sha256']
    assert projection['units'][0]['semantic_id'] == again['units'][0]['semantic_id']
    assert projection['units'][0]['unit_id'] != again['units'][0]['unit_id']
    units = projection['units']
    targets = {units[0]['unit_id']: ['слово', 'язык'], units[1]['unit_id']: 'с ⟦WDXP0001⟧'}
    derived, blocks = localized_projection(projection, targets)
    assert blocks == {0: ['слово', 'язык'], 1: 'с ⟦WDXP0001⟧'}
    assert derived['tree'] == original['tree'] and value == original
    with pytest.raises(ValueError, match='coverage'):
        localized_projection(projection, {units[0]['unit_id']: ['слово']})
    with pytest.raises(ValueError, match='array'):
        localized_projection(projection, {**targets, units[0]['unit_id']: 'слово'})


def test_envelope_retains_complete_context_and_new_contract():
    p = prepare_projection(source(), versions=VERSIONS, run_identity='one')
    rows = [{'id': u['unit_id'], **u} for u in p['units']]
    envelope = projection_envelope({'id': 8, 'source_sha256': 'hash'}, rows, p)
    assert envelope['units'][0]['packet_id'] == envelope['units'][1]['packet_id']
    assert envelope['units'][0]['local_context']['sense_context']['tag'] == 'sense'
    assert envelope['read_only_context']['source_tree'] == source()['tree']
    manifest, _ = _manifest('batch', [envelope], {})
    assert manifest['pipeline'] == 'wadoku-xml-v3'
    rows[0]['source_text'] = 'changed'
    with pytest.raises(ValueError, match='differs'):
        projection_envelope({'id': 8, 'source_sha256': 'hash'}, rows, p)


def test_oversize_sense_packet_is_never_split():
    envelope = {'article_id': 'a-1', 'units': [
        {'unit_id': str(i), 'packet_id': 'sense-one', 'source_text': 'word', 'role': 'translation'} for i in range(3)]}
    with pytest.raises(ValueError, match='hard article limit'):
        _pack_envelopes([envelope], {}, 10, 10000, 2, 10000, 10000, 2, True)
    envelope['units'][2]['packet_id'] = 'sense-two'
    packed = _pack_envelopes([envelope], {}, 10, 10000, 2, 10000, 10000, 2, True)
    assert [len(group[0]['units']) for group in packed] == [2, 1]


def test_rich_glossary_validation_is_typed_and_preserves_tokens():
    assert not wadoku_glossary_issues('["Wort", "Sprache"]', ['слово', 'язык'], [], 'u')
    for bad in ('слово', [], [7], ['слово', 'Слово']):
        assert wadoku_glossary_issues('["Wort"]', bad, [], 'u')
    assert wadoku_glossary_issues('mit ⟦WDXP0001⟧', ['со словом'], ['⟦WDXP0001⟧'], 'u')
    assert not wadoku_glossary_issues('mit ⟦WDXP0001⟧', ['с ⟦WDXP0001⟧'], ['⟦WDXP0001⟧'], 'u')


def test_examples_follow_sense_filter_and_use_native_selectors():
    from jitendex_ru.wadoku_xml import structured_entry
    value=canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form>'
        '<sense><trans><tr>Wort</tr></trans></sense><sense><trans><tr>Sprache</tr></trans></sense></entry>'))
    value['accepted_examples']=[{'unit_id':'e1','parent_id':1,'sense_path':'/entry[1]/sense[2]',
        'source_path':'/entry[1]/sense[1]/trans[1]/tr[1]','source_text':'ein Wort',
        'japanese':'一つの語','translation':'одно слово','protected_fragment_context':[]}]
    result=structured_entry(value,'ru',{}, {0:'слово',1:'язык'})
    senses=result['content']['content'][0]['content']
    assert 'example-sentence-a' not in json.dumps(senses[0])
    assert 'example-sentence-a' in json.dumps(senses[1])
    assert 'extra-box' in json.dumps(senses[1])
    filtered=structured_entry({**value,'sense_filter':[1]},'ru',{}, {0:'слово',1:'язык'})
    assert 'example-sentence' not in json.dumps(filtered)
    german=structured_entry(value,'de',{})
    assert 'ein Wort' in json.dumps(german)
    with pytest.raises(ValueError,match='parent sense'):
        structured_entry({**value,'entry_id':9},'de',{})


def test_reviewed_example_correction_reaches_projection_without_changing_proposal():
    from jitendex_ru.wadoku_classification import make_request
    from jitendex_ru.wadoku_pipeline import adopt_examples
    parent=source()
    from jitendex_ru.wadoku_quality import source_identity
    child=canonical_entry(ET.fromstring('<entry id="2"><form><orth>語の意味</orth><reading><hira>ごのいみ</hira></reading></form>'
        '<sense><usg type="dom">Ling.</usg><trans><tr>Bedeutung eines Wortes</tr></trans><expl>context</expl></sense></entry>'))
    candidate={'parent_id':1,'child_id':2,'relation_path':'/entry[1]/ref[1]',
        'source_sha256':source_identity(child),'japanese':'語の意味','reading':'ごのいみ','template':False,
        'sense_path':None,'source_blocks':[{'role':'translation','xml_path':'/entry[1]/sense[1]/trans[1]/tr[1]',
            'prompt_text':'Bedeutung eines Wortes','protected_fragments':[]}]}
    request=make_request(parent,[],[candidate],'scope')
    key=request['examples'][0]['decision_id']
    proposal={'batch_id':request['batch_id'],'manifest_sha256':request['manifest_sha256'],'entry_id':1,
        'article_policy':'independent','parent_id':None,'lookup_policy':'independent_direct','reason':'Слово.',
        'confidence':'high','lookup_aliases':[],'lookup_needs_review':None,
        'examples':[{'decision_id':key,'state':'accept','source_path':candidate['source_blocks'][0]['xml_path'],
            'sense_path':None,'reason':'Пример.'}]}
    before=copy.deepcopy(proposal)
    from jitendex_ru.wadoku_pipeline import candidate_examples
    staged=candidate_examples(request, proposal)
    assert staged[0]['decision']['stage']=='classifier_candidate'
    assert 'review' not in staged[0]['decision']
    uncertain=copy.deepcopy(proposal)
    uncertain['examples'][0].update(state='needs_review', source_path=None, sense_path=None)
    assert candidate_examples(request, uncertain)[0]['selection_state']=='unresolved'
    examples=adopt_examples(request,proposal,reviewer='fixture-review',review_note='Fixture sense checked.',
        corrections={key:{'sense_path':'/entry[1]/sense[1]','reason':'Matches the word sense.'}})
    with pytest.raises(ValueError,match='full source context'):
        prepare_projection(parent,versions=VERSIONS,run_identity='example-test',examples=examples)
    projection=prepare_projection(parent,versions=VERSIONS,run_identity='example-test',examples=examples,example_sources={2:child})
    unit=next(u for u in projection['units'] if u['role']=='example_translation')
    assert unit['sense_path']=='/entry[1]/sense[1]'
    assert unit['example_source_context']==child['tree']
    projection['context']['examples'].append({'decision_id':'excluded', 'selection_state':'rejected',
                                              'source_blocks':['irrelevant rejected candidate']})
    frozen=copy.deepcopy(projection)
    rows=[{'id': u['unit_id'], 'source_sha256':u['source_sha256'],
           'role':u['role'],'source_text':u['source_text']} for u in projection['units']]
    envelope=projection_envelope({'id':1,'source_sha256':'fixture'},rows,projection)
    assert len(envelope['read_only_context']['examples'])==1
    assert envelope['read_only_context']['example_selection_states'][-1]['state']=='rejected'
    example_unit=next(u for u in envelope['units'] if u['role']=='example_translation')
    assert example_unit['local_context']['example_source_context']==child['tree']
    assert projection==frozen
    legacy=copy.deepcopy(projection)
    legacy.pop('envelope_version')
    assert len(projection_envelope({'id':1,'source_sha256':'fixture'},rows,legacy)['read_only_context']['examples'])==2
    assert proposal==before
    assert examples[0]['decision']['review']['corrections'][key]['reason']
    with pytest.raises(ValueError,match='outside proposal'):
        adopt_examples(request,proposal,reviewer='test',review_note='test',corrections={'wrong':{'reason':'test'}})
    with pytest.raises(ValueError,match='invalid reviewed'):
        adopt_examples(request,proposal,reviewer='test',review_note='test',
            corrections={key:{'sense_path':'/entry[1]/sense[99]','reason':'bad'}})


def test_review_loader_rejects_stale_proposal_and_missing_candidates():
    from jitendex_ru.wadoku_pipeline import load_reviewed_examples
    from jitendex_ru.util import canonical_json, sha256_bytes
    proposal={'attempt_id':'attempt','result':{'fixture':True}}
    example={'child_id':2,'parent_id':1,'relation_path':'ref','source_sha256':'child',
             'selection_state':'accepted','decision':{'review':{'proposal_sha256':sha256_bytes(canonical_json(proposal['result']))}}}
    artifact={'scope_id':'scope','entry_id':1,'attempt_id':'attempt','source_sha256':'parent','examples':[example]}
    artifact['identity']=sha256_bytes(canonical_json(artifact))
    class Result:
        def __init__(self,value): self.value=value
        def fetchone(self): return self.value
        def fetchall(self): return self.value
    class Connection:
        def execute(self,sql,params):
            if 'FROM wadoku_scope_entry' in sql:
                return Result({'source_sha256':'parent','decision_json':json.dumps(proposal)})
            if 'FROM wadoku_example_candidate' in sql:
                return Result([{'child_id':2,'relation_path':'ref','candidate_json':json.dumps({'source_sha256':'child'})}])
            return Result([json.dumps(artifact)])
    assert load_reviewed_examples(Connection(),'scope',1)==[example]
    proposal['attempt_id']='new-attempt'
    with pytest.raises(ValueError,match='stale'):
        load_reviewed_examples(Connection(),'scope',1)
    proposal['attempt_id']='attempt'
    artifact['examples']=[]
    artifact.pop('identity')
    artifact['identity']=sha256_bytes(canonical_json(artifact))
    with pytest.raises(ValueError,match='coverage'):
        load_reviewed_examples(Connection(),'scope',1)
