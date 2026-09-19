import copy
import xml.etree.ElementTree as ET

from jitendex_ru.wadoku_classification import make_request, validate_decision, normalize_decision, source_context, entry_tree
from jitendex_ru.wadoku_quality import example_candidates
from jitendex_ru.wadoku_xml import canonical_entry
from wadoku_classify_window import output_reserve


def test_classification_output_reserve_fits_observed_large_example_sets():
    assert output_reserve(0) == 4096
    assert output_reserve(56) == 9888
    assert 117897 + output_reserve(56) <= 128000


def test_classification_is_source_bound_and_ambiguity_is_allowed():
    parent=canonical_entry(ET.fromstring('<entry id="1"><form><orth>知る</orth><reading><hira>しる</hira></reading></form><sense><trans><tr>wissen</tr></trans></sense></entry>'))
    child=canonical_entry(ET.fromstring('<entry id="2"><form><orth>知らない人</orth><reading><hira>しらないひと</hira></reading></form><sense><trans><tr>unbekannte Person</tr></trans></sense><ref type="main" subentrytype="VwBsp" id="1"/></entry>'))
    candidate=example_candidates([child],{1})
    request=make_request(parent,[],candidate,'scope','prompt')
    result={'batch_id':request['batch_id'],'manifest_sha256':request['manifest_sha256'],'entry_id':1,
        'article_policy':'independent','parent_id':None,'lookup_policy':'independent_direct','reason':'Самостоятельный глагол.',
        'confidence':'high','lookup_aliases':[],'lookup_needs_review':None,'examples':[{
            'decision_id':request['examples'][0]['decision_id'],'state':'accept',
            'source_path':candidate[0]['source_blocks'][0]['xml_path'],'sense_path':None,'reason':'Фраза с существительным.'}]}
    assert validate_decision(request,result)==[]
    bad=copy.deepcopy(result)
    bad['lookup_aliases']=[{'source_form':'知らない人','expression':'知らない人','reading':'しらないひと',
                          'kind':'full_expansion','evidence_path':None,'reason':'Not a parent alias.'}]
    assert validate_decision(request,bad)
    bad=copy.deepcopy(result)
    bad['examples'][0]['source_path']='/invented'
    assert validate_decision(request,bad)
    bad=copy.deepcopy(result)
    bad['article_policy']='inherit_parent'
    bad['lookup_policy']='shared_direct'
    bad['parent_id']=999
    assert validate_decision(request,bad)
    result['article_policy']='needs_review'
    result['lookup_policy']='needs_review'
    result['confidence']='low'
    assert validate_decision(request,result)==[]
    assert make_request(parent,[],candidate,'scope','new-prompt')['batch_id']!=request['batch_id']


def test_xml_context_keeps_internal_text_attributes_and_order():
    source=canonical_entry(ET.fromstring('<entry id="9"><form><orth>語</orth><reading><hira>ご</hira></reading></form>'
        '<sense related="true"><expl>before <jap>語</jap> after &amp; tail</expl></sense></entry>'))
    assert entry_tree(source_context(source))==source['tree']
    assert len(str(source_context(source)))<len(str(source))


def test_reading_spacing_normalization_is_audited_and_does_not_edit_raw():
    raw={'lookup_aliases':[{'reading':'せず　に　は　いられない','expression':'せずにはいられない'}]}
    normalized,changes=normalize_decision(raw)
    assert raw['lookup_aliases'][0]['reading']=='せず　に　は　いられない'
    assert normalized['lookup_aliases'][0]['reading']=='せずにはいられない'
    assert changes[0]['rule']=='kana-lookup-spacing-v1'


def test_source_suffix_keeps_particles_and_excludes_display_heading():
    source=canonical_entry(ET.fromstring('<entry id="9"><form><orth midashigo="true">…を打(ち)切りにする</orth>'
        '<orth>…を打ち切りにする</orth><reading><hira>…をうちきりにする</hira></reading></form><sense/></entry>'))
    request=make_request(source,[],[],'scope')
    request['response_schema_version']='wadoku-structure-schema-v3'
    raw={'lookup_aliases':[
        {'source_form':'…を打(ち)切りにする','kind':'suffix_only','expression':'打ち切りにする','reading':'うちきりにする'},
        {'source_form':'…を打ち切りにする','kind':'suffix_only','expression':'打ち切りにする','reading':'うちきりにする'}]}
    normalized,changes=normalize_decision(raw,request)
    assert len(normalized['lookup_aliases'])==1
    assert normalized['lookup_aliases'][0]['expression']=='を打ち切りにする'
    assert normalized['lookup_aliases'][0]['reading']=='をうちきりにする'
    assert len(changes)==4
    assert normalized['lookup_aliases'][0]['evidence_path']=='/entry[1]/form[1]/orth[2]'
    assert any(c['rule']=='source-suffix-evidence-path-v1' for c in changes)
    assert normalize_decision({})==({},[])


def test_required_candidate_map_separates_eligibility_from_selection():
    import pytest
    source=canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form><sense/></entry>'))
    candidate={'parent_id':1,'child_id':2,'relation_path':'/entry[1]/ref[1]',
               'source_sha256':'hash','japanese':'語の意味','reading':'ごのいみ','source_blocks':[], 'template':False}
    request=make_request(source,[],[candidate],'scope')
    key=request['examples'][0]['decision_id']
    result={'batch_id':request['batch_id'],'manifest_sha256':request['manifest_sha256'],'entry_id':1,
            'article_policy':'independent','parent_id':None,'lookup_policy':'independent_direct',
            'reason':'Отдельное слово.','confidence':'high','lookup_aliases':[],'lookup_needs_review':None,
            'examples':{key:{'eligible':True,'source_path':None,'sense_path':None,'reason':'Подходит.'}},
            'selected_example_ids':[]}
    normalized,changes=normalize_decision(result,request)
    assert normalized['examples'][0]['state']=='reject'
    assert normalized['examples'][0]['reason'].startswith('Не выбран по лимиту')
    assert not validate_decision(request,normalized)
    missing=copy.deepcopy(result)
    missing['examples']={}
    with pytest.raises(ValueError):
        normalize_decision(missing,request)
    result['examples'][key]['eligible']=False
    result['selected_example_ids']=[key]
    with pytest.raises(ValueError,match='not eligible'):
        normalize_decision(result,request)


def test_example_schema_rejects_reference_as_translation_path():
    import fastjsonschema
    import pytest
    from jitendex_ru.wadoku_classification import response_schema
    source=canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form><sense/></entry>'))
    candidate={'parent_id':1,'child_id':2,'relation_path':'/entry[1]/ref[1]',
               'source_sha256':'hash','source_blocks':[{'role':'translation','xml_path':'/entry[1]/sense[1]/trans[1]/tr[1]'}]}
    request=make_request(source,[],[candidate],'scope')
    item=response_schema(request)['properties']['examples']['properties'][request['examples'][0]['decision_id']]
    validate=fastjsonschema.compile(item)
    value={'eligible':True,'source_path':'/entry[1]/sense[1]/trans[1]/tr[1]',
           'sense_path':'/entry[1]/sense[1]','reason':'Соответствует значению.'}
    validate(value)
    with pytest.raises(ValueError):
        validate({**value,'source_path':'/entry[1]/ref[1]'})
    with pytest.raises(ValueError):
        validate({**value,'sense_path':value['source_path']})


def test_rebase_preserves_answer_and_rejects_changed_context():
    import pytest
    from jitendex_ru.wadoku_classification import rebase_proposal
    source=canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form><sense/></entry>'))
    old=make_request(source,[],[],'old','prompt')
    new=make_request(source,[],[],'new','prompt')
    result={'batch_id':old['batch_id'],'manifest_sha256':old['manifest_sha256'],'entry_id':1,
        'article_policy':'independent','parent_id':None,'lookup_policy':'independent_direct',
        'reason':'Слово.','confidence':'high','lookup_aliases':[],'lookup_needs_review':None,'examples':[]}
    rebased,_=rebase_proposal(old,result,new)
    assert rebased['manifest_sha256']==new['manifest_sha256']
    assert result['manifest_sha256']==old['manifest_sha256']
    legacy=copy.deepcopy(old)
    legacy['entry']=copy.deepcopy(source)
    legacy['entry']['tree']['tail']='\n'
    assert rebase_proposal(legacy,result,new)[0]==rebased
    legacy['entry']['tree']['children'][0]['text']='changed'
    with pytest.raises(ValueError,match='context changed'):
        rebase_proposal(legacy,result,new)
    with pytest.raises(ValueError,match='context changed'):
        rebase_proposal(old,result,{**new,'prompt_sha256':'changed'})
