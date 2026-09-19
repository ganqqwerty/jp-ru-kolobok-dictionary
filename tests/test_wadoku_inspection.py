import json
from wadoku_inspection_site import literal_fallback, render
from jitendex_ru.wadoku_scope import link_closed_prefix_ids


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
