import copy
from pathlib import Path
import xml.etree.ElementTree as ET

from jitendex_ru.util import canonical_json
from jitendex_ru.wadoku_wire import FORMAT, MARKER, REF, expand_wire, translation_wire
from jitendex_ru.wadoku_xml import lossless_node
import json


def fixture_manifest():
    tree = lossless_node(ET.fromstring(
        '<entry id="1"><orth>どうにか…する</orth><sense>before '
        '<trans lang="de">A &amp; B</trans> after <ref id="2"/> tail</sense></entry>'))
    return {'batch_id': 'b', 'manifest_sha256': 'hash', 'articles': [{
        'article_id': 'a-1', 'read_only_context': {'source_tree': tree},
        'units': [{'unit_id': f'u-{i}', 'source_sha256': f'h-{i}',
                   'source_text': 'German aid', 'required_literals': ['…'],
                   'local_context': {'sense_context': tree['children'][1]}}
                  for i in range(6)]}]}


def test_lossless_smaller_transport_without_mutation():
    manifest = fixture_manifest()
    saved = copy.deepcopy(manifest)
    data, mode = translation_wire(manifest, MARKER)
    wire = json.loads(data)
    assert mode == FORMAT
    assert len(data) < len(canonical_json(manifest))
    assert expand_wire(wire) == saved == manifest
    assert len(wire['contexts']) == 2
    refs = [u['local_context']['sense_context'][REF] for u in wire['manifest']['articles'][0]['units']]
    assert len(set(refs)) == 1


def test_frozen_old_prompts_remain_canonical():
    manifest = fixture_manifest()
    assert translation_wire(manifest, 'old prompt') == (canonical_json(manifest), 'canonical-json')


def test_small_requests_and_reserved_keys_fall_back():
    for manifest in ({'articles': []}, {REF: 'source data', **fixture_manifest()}):
        assert translation_wire(manifest, MARKER) == (canonical_json(manifest), 'canonical-json')


def test_xml_normalization_cannot_silently_change_source():
    manifest = fixture_manifest()
    manifest['articles'][0]['read_only_context']['source_tree']['text'] = 'a\rb'
    assert translation_wire(manifest, MARKER) == (canonical_json(manifest), 'canonical-json')


def test_source_attribute_whitespace_and_root_tail_roundtrip():
    manifest = fixture_manifest()
    tree = manifest['articles'][0]['read_only_context']['source_tree']
    tree['tail'] = ' after root & <'
    tree['attributes']['note'] = 'tab\tnewline\nquote"'
    data, mode = translation_wire(manifest, MARKER)
    assert mode == FORMAT
    assert expand_wire(json.loads(data)) == manifest


def test_new_prompt_preserves_all_previous_quality_instructions():
    root = Path(__file__).resolve().parents[1] / 'prompts'
    old = (root / 'translate_luna_wadoku_xml_ru_v18.txt').read_text().strip()
    new = (root / 'translate_luna_wadoku_xml_ru_v19.txt').read_text()
    assert new.startswith(old)
    assert MARKER in new
