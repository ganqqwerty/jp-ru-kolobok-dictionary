import json
import xml.etree.ElementTree as ET
import pytest
from pathlib import Path

from jitendex_ru.wadoku_xml import canonical_entry, sense_translation_entry, structured_entry, label_catalog, yomitan_rows
from jitendex_ru.wadoku_xml import reference_target_index


def test_structured_references_badges_and_source_only_fields(tmp_path):
    xml = '''<entry id="2"><form><orth>…餓鬼</orth><reading><hira>がき</hira></reading></form>
    <sense><etym><abbrev><ref id="3" type="other"><transcr>gaki·dō</transcr><jap>餓鬼道</jap></ref></abbrev></etym>
    <usg type="dom">Buddh.</usg><trans><tr>Welt</tr></trans></sense>
    <ref id="3" type="main"/><link type="picture" url="Gaki">Gaki</link><steinhaus>32</steinhaus></entry>'''
    path = tmp_path / 'source.xml'
    path.write_text('<entries>' + xml + '''<entry id="3"><form><orth>餓鬼道</orth>
    <reading><hira>がきどう</hira></reading></form><sense/></entry></entries>''')
    value = sense_translation_entry(canonical_entry(ET.fromstring(xml)))
    value['reference_targets'] = reference_target_index(path)
    labels = label_catalog(Path('terminology/wadoku-xml-labels-v1.json'))
    result = structured_entry(value, 'ru', labels, {0: '⟦WDXP0001⟧', 1: ['мир голодных духов']})
    text = json.dumps(result, ensure_ascii=False)
    assert 'Сокр. от: ' in text
    assert 'がきどう' in text
    assert '"href": "?query=' in text
    assert 'sense-tag' in text and 'буддизм' in text
    assert all(bad not in text for bad in ('gaki·dō', 'Gaki', '#3', '32'))
    assert result['content']['content'][0]['tag'] == 'div'


def test_unresolved_reference_fails_instead_of_exporting_raw_id():
    value = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>人</orth>
    <reading><hira>ひと</hira></reading></form><sense/><ref id="999" type="main"/></entry>'''))
    with pytest.raises(ValueError, match='Unresolved Wadoku reference'):
        structured_entry(value, 'ru', {})


def test_sense_projection_condenses_synonyms_without_changing_source_or_restrictions():
    source = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>素晴らしい</orth>
    <reading><hira>すばらしい</hira><accent>4</accent></reading></form>
    <gramGrp><keiyoushi/></gramGrp><sense><trans><tr>gut</tr></trans>
    <trans><tr>wunderbar</tr></trans><trans><tr>mit <jap>何</jap></tr></trans></sense>
    <sense><trans><tr>gewaltig</tr></trans></sense></entry>'''))
    projected = sense_translation_entry(source)
    assert len(source['blocks']) == 4
    assert projected['tree'] == source['tree']
    assert [b['role'] for b in projected['blocks']] == ['glossary_set', 'translation', 'glossary_set']
    labels = label_catalog(Path('terminology/wadoku-xml-labels-v1.json'))
    targets = {0: ['прекрасный', 'великолепный'], 1: 'с ⟦WDXP0001⟧', 2: ['необычайный']}
    rows, meta = yomitan_rows(projected, 'ru', labels, targets)
    rendered = json.dumps(rows[0][5], ensure_ascii=False)
    assert 'прекрасный; великолепный' in rendered
    assert '何' in rendered
    assert all(x not in rendered for x in ['Перевод:', 'Произношение:', 'accent=', 'keiyoushi'])
    assert rows[0][2] == 'い-прилагательное'
    assert meta[0][2]['pitches'] == [{'position': 4}]
