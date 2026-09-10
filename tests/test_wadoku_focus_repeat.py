import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jitendex_ru.wadoku_classification import fixed_template_alias, make_request, response_schema, validate_decision, normalize_decision
from jitendex_ru.wadoku_assembly import lookup_rows
from jitendex_ru.wadoku_quality import translation_projection
from jitendex_ru.wadoku_xml import canonical_entry, structured_entry, label_catalog
from jitendex_ru.validate_response import wadoku_redundant_source_echo
from jitendex_ru.util import canonical_json, sha256_bytes
from wadoku_focus_scope import repeat_scope


def test_boundary_alias_never_drops_internal_material():
    assert fixed_template_alias('…にする', '…にする', 'suffix_only') == ('にする', 'にする')
    assert fixed_template_alias('新…', 'しん…', 'prefix_only') == ('新', 'しん')
    assert fixed_template_alias('何も…ない', 'なにも…ない', 'suffix_only') is None
    assert fixed_template_alias('新…', '…しん', 'prefix_only') is None
    assert fixed_template_alias('…限り', 'かぎり', 'suffix_only') == ('限り', 'かぎり')
    assert fixed_template_alias('何も…ない', 'なにもない', 'suffix_only') is None
    with pytest.raises(ValueError, match='fixed lexical'):
        lookup_rows([['何も…ない', 'なにも…ない']], [], {'lookup_aliases': [
            {'source_form': '何も…ない', 'expression': 'ない', 'reading': 'ない', 'kind': 'suffix_only'}]})


def test_repeat_keeps_sources_and_does_not_mutate_baseline():
    source = {'entry_id': 7}
    baseline = {'manifest': {'scope_id': 'old'}, 'entries': [
        {'entry_id': 7, 'source': source, 'source_sha256': sha256_bytes(canonical_json(source))}], 'candidates': []}
    original = copy.deepcopy(baseline)
    result = repeat_scope(baseline, 'v2')
    assert baseline == original
    assert result['entries'] == baseline['entries']
    assert result['manifest']['scope_id'] != 'old'
    result['entries'][0]['source']['entry_id'] = 8
    with pytest.raises(ValueError, match='hash mismatch'):
        repeat_scope(result, 'v3')


def test_prefix_schema_and_exact_evidence_are_required():
    value = canonical_entry(ET.fromstring('<entry id="1"><form><orth>新…</orth><reading><hira>しん…</hira></reading></form><sense/></entry>'))
    request = make_request(value, [], [], 'scope', 'prompt')
    result = {'batch_id': request['batch_id'], 'manifest_sha256': request['manifest_sha256'], 'entry_id': 1,
        'article_policy': 'independent', 'parent_id': None, 'lookup_policy': 'independent_direct',
        'reason': 'Префикс.', 'confidence': 'high', 'lookup_needs_review': None, 'examples': [],
        'lookup_aliases': [{'source_form': '新…', 'expression': '新', 'reading': 'しん',
            'kind': 'prefix_only', 'evidence_path': '/entry[1]/form[1]/orth[1]', 'reason': 'Начальная часть.'}]}
    assert validate_decision(request, result) == []
    wire = {**copy.deepcopy(result), 'examples': {}, 'selected_example_ids': []}
    wire['lookup_aliases'][0]['evidence_path'] = '/entry/form/orth'
    normalized, changes = normalize_decision(wire, request)
    assert validate_decision(request, normalized) == []
    assert changes[-1]['rule'] == 'source-prefix-evidence-path-v1'
    assert wire['lookup_aliases'][0]['evidence_path'] == '/entry/form/orth'
    result['lookup_aliases'][0]['reading'] = 'あたら'
    assert validate_decision(request, result)
    request['response_schema_version'] = 'wadoku-structure-schema-v5'
    assert 'prefix_only' not in response_schema(request)['properties']['lookup_aliases']['items']['properties']['kind']['enum']


def test_controlled_grammar_keeps_nested_scope_and_renders_without_translation():
    value = canonical_entry(ET.fromstring('<entry id="1"><form><orth>語</orth><reading><hira>ご</hira></reading></form>'
        '<sense><expl>als N.</expl><sense><trans><tr>Öffnen</tr></trans></sense></sense></entry>'))
    original = copy.deepcopy(value)
    versions = {k: 'test' for k in ('labels', 'morphology', 'examples', 'corrections', 'prompt')}
    new = translation_projection(value, versions={**versions, 'schema': 'rich-v4'})
    old = translation_projection(value, versions={**versions, 'schema': 'rich-v3'})
    assert len(new['units']) == len(old['units']) - 1
    assert new['units'][0]['grammatical_scope'][0]['label'] == 'существительное'
    target = {new['units'][0]['projection_index']: ['открытие']}
    rendered = structured_entry(new['render_value'], 'ru', label_catalog(Path('terminology/wadoku-xml-labels-v2.json')), target)
    assert 'существительное' in json.dumps(rendered, ensure_ascii=False)
    assert value == original


def test_echo_guard_is_narrow_and_preserves_short_notation_and_etymons():
    unit = {'role': 'glossary_set', 'source_text': '["System", "Tor", "fp", "Korrolar", "Satz"]'}
    assert wadoku_redundant_source_echo(unit, ['система (System)']) == ['System']
    assert wadoku_redundant_source_echo(unit, ['Tor — гол']) == ['Tor']
    assert wadoku_redundant_source_echo(unit, ['следствие (Korrolar, Satz)']) == ['Korrolar', 'Satz']
    assert not wadoku_redundant_source_echo(unit, ['тихо (fp)'])
    assert not wadoku_redundant_source_echo({**unit, 'role': 'etymology'}, ['из санскр. preta'])
    assert not wadoku_redundant_source_echo({**unit, 'protected_tokens': ['token']}, ['система (System)'])
