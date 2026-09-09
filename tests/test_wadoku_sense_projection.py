import json
import xml.etree.ElementTree as ET
from pathlib import Path

from jitendex_ru.wadoku_xml import canonical_entry, sense_translation_entry, structured_entry, label_catalog, yomitan_rows


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
