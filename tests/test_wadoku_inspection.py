import json
from wadoku_inspection_site import literal_fallback, render, review_sample
from jitendex_ru.wadoku_scope import link_closed_prefix_ids
from wadoku_progress_report import error_class


def test_inspection_fallback_preserves_every_translation_and_examples():
    article={'units':[
        {'role':'glossary_set','target_text':'["значение"]','request_unit':{'sense_path':'one'}},
        {'role':'example_translation','target_text':'перевод примера','request_unit':{'sense_path':'one','japanese':'例'}},
        {'role':'explanation','target_text':'пояснение','request_unit':{'sense_path':'two'}},
    ]}
    data=json.dumps(literal_fallback(article),ensure_ascii=False)
    assert all(text in data for text in ['значение','перевод примера','пояснение','例'])
    assert 'example-sentence-a' in data and 'example-sentence-b' in data


def test_preview_escapes_source_content_and_unsafe_links():
    assert '&lt;script&gt;' in render('<script>')
    assert 'javascript:' not in render({'tag':'a','href':'javascript:alert(1)','content':'test'})
    assert 'https://example.org' in render({'tag':'a','href':'https://example.org','content':'test'})


def test_link_closed_selection_keeps_exact_size_and_every_known_target():
    order = [1, 2, 3, 4, 5, 6]
    references = {1: {5}, 2: {3}, 3: set(), 4: {6}, 5: set(), 6: set()}
    selected, fillers, prefix_size = link_closed_prefix_ids(order, references, size=5)
    assert selected == {1, 2, 3, 5, 6}
    assert fillers == {6}
    assert prefix_size == 3
    assert all(references[entry] <= selected for entry in selected)


def test_progress_report_separates_transport_and_classification_contract_errors():
    assert error_class(['CLI returncode=-15; usage_available=False']) == 'transport'
    assert error_class(['incompatible article and lookup decisions']) == 'classification_contract'
    assert error_class(['full expansion has no exact attested evidence']) == 'classification_contract'
    assert error_class(['parent not present in source context']) == 'classification_contract'
    assert error_class(['template has neither an alias nor an unresolved reason']) == 'classification_contract'
    assert error_class(['selected example is not eligible']) == 'classification_contract'
    assert error_class(['duplicate selected example ID']) == 'classification_contract'
    assert error_class(['unexpanded example template selected']) == 'classification_contract'


def test_review_sample_is_deterministic_and_stays_inside_each_5000_block():
    entries=[{'entry_id':i,'ordinal':i,'categories':['test']} for i in range(1,20_001)]
    articles=[{'entry_id':i} for i in range(20_000,0,-1)]
    scope={'manifest':{'scope_id':'fixture'},'entries':entries}
    first,count=review_sample(scope,articles,100)
    second,_=review_sample(scope,articles,100)
    assert count == 4 and first == second and len(first) == 400
    for batch in range(1,5):
        ids=[article['entry_id'] for current,article in first if current==batch]
        assert len(ids)==100 and min(ids)>(batch-1)*5000 and max(ids)<=batch*5000
