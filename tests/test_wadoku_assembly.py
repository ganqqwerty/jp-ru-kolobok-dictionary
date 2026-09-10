import pytest
import xml.etree.ElementTree as ET
from jitendex_ru import wadoku_assembly as assembly
from jitendex_ru.wadoku_xml import canonical_entry, label_catalog
from jitendex_ru.wadoku_xml import source_transcription_resolutions
from jitendex_ru.wadoku_quality import pronunciation_records
from pathlib import Path


def test_suffix_lookup_alignment_requires_exact_shared_reading():
    from jitendex_ru.wadoku_xml import literal_suffix_lookup
    assert literal_suffix_lookup('…おき', '…おき') == ('おき', 'おき')
    assert literal_suffix_lookup('…を…する', '…を…する') is None
    value = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>…伝</orth>
        <reading><hira>…づたい</hira></reading></form><sense><trans><tr>along</tr></trans></sense></entry>'''))
    decision = {'article_policy':'independent', 'lookup_needs_review':'reading alignment',
                'lookup_aliases':[{'kind':'suffix_only','source_form':'…伝','expression':'伝','reading':'づたい'}]}
    assert assembly.source_aligned_lookup(value, decision)['lookup_needs_review'] is None
    decision['lookup_aliases'][0]['reading'] = 'づた'
    assert assembly.source_aligned_lookup(value, decision)['lookup_needs_review']


def test_transcription_resolution_requires_unambiguous_source_pair():
    value = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>語</orth>
        <reading><hira>ご</hira></reading></form><sense><expl><transcr>morau</transcr></expl></sense>
        <ref><transcr>mora~u</transcr><jap>もらう</jap></ref></entry>'''))
    assert source_transcription_resolutions(value) == {'morau': 'もらう', 'mora~u': 'もらう'}
    value['tree']['children'].append({'tag':'ref','attributes':{},'text':'','tail':'', 'children':[
        {'tag':'transcr','attributes':{},'text':'morau','tail':'','children':[]},
        {'tag':'jap','attributes':{},'text':'別','tail':'','children':[]}]})
    assert source_transcription_resolutions(value) == {}


def test_inherited_content_and_variants_share_owner_without_inflection_row(monkeypatch):
    entries = [({'entry_id': i}, {}) for i in (1, 2, 3)]
    decisions = {
        1: {'article_policy': 'independent', 'lookup_policy': 'independent_direct', 'parent_id': None},
        2: {'article_policy': 'inherit_parent', 'lookup_policy': 'shared_direct', 'parent_id': 1},
        3: {'article_policy': 'inherit_parent', 'lookup_policy': 'deinflect_to_parent', 'parent_id': 1},
    }
    def render(value, *args):
        i = value['entry_id']
        return [[str(i), 'reading', '', 'v1', 0, [f'sense {i}'], i, '']], []
    monkeypatch.setattr(assembly, 'yomitan_rows', render)
    result = assembly.grouped_rows(entries, decisions, {})
    assert set(result) == {1}
    rows, _ = result[1]
    assert [r[0] for r in rows] == ['1', '2']
    assert all(r[6] == 1 and r[5] == ['sense 1', 'sense 2', 'sense 3'] for r in rows)
    assert entries == [({'entry_id': i}, {}) for i in (1, 2, 3)]


def test_independent_pitch_groups_stay_separate(monkeypatch):
    original = [['語', 'ご', '', '', 0, ['first'], 10, ''],
                ['語', 'ご', '', '', 0, ['second'], 11, '']]
    monkeypatch.setattr(assembly, 'yomitan_rows', lambda *args: (original, []))
    decision = {'article_policy': 'independent', 'lookup_policy': 'independent_direct', 'parent_id': None}
    assert assembly.grouped_rows([({'entry_id': 1}, {})], {1: decision}, {})[1][0] == original
    inherited = {'article_policy': 'inherit_parent', 'lookup_policy': 'shared_direct', 'parent_id': 1}
    with pytest.raises(ValueError, match='pitch/sense alignment'):
        assembly.grouped_rows([({'entry_id': 1}, {}), ({'entry_id': 2}, {})], {1: decision, 2: inherited}, {})


def test_reports_all_render_failures_without_returning_partial_dictionary(monkeypatch):
    def render(value, *args):
        raise ValueError(f"bad source {value['entry_id']}")
    monkeypatch.setattr(assembly, 'yomitan_rows', render)
    decision = {'article_policy': 'independent', 'lookup_policy': 'independent_direct', 'parent_id': None}
    with pytest.raises(assembly.AssemblyError) as caught:
        assembly.grouped_rows([({'entry_id': 1}, {}), ({'entry_id': 2}, {})],
                              {1: decision, 2: decision}, {})
    assert [i['entry_id'] for i in caught.value.issues] == [1, 2]


def test_real_template_uses_alias_without_inventing_pitch(tmp_path):
    value = canonical_entry(ET.fromstring('''<entry id="1"><form><orth>…づくり</orth>
        <reading><hira>…づくり</hira><accent>1</accent></reading></form>
        <sense><trans><tr>Herstellung</tr></trans></sense></entry>'''))
    parsed = pronunciation_records(value)
    assert not parsed['issues'] and not parsed['accents']
    assert parsed['limitations'][0]['source_value'] == '1'
    decision = {'article_policy': 'independent', 'lookup_policy': 'independent_direct',
                'parent_id': None, 'lookup_aliases': [
                    {'source_form': '…づくり', 'expression': 'づくり', 'reading': 'づくり'}]}
    labels = label_catalog(Path('terminology/wadoku-xml-labels-v2.json'))
    rows, metadata = assembly.grouped_rows([(value, {0: 'изготовление'})], {1: decision}, labels)[1]
    assert [(r[0], r[1]) for r in rows] == [('づくり', 'づくり')]
    assert metadata == []
    assert pronunciation_records(value) == parsed
    import json
    import zipfile
    output = tmp_path / 'dictionary.zip'
    report = assembly.build_grouped_archive([(value, {0: 'изготовление'})], {1: decision},
        output, labels=labels, license_text=b'fixture license', title='Fixture',
        revision='fixture', source_url='https://example.org/fixture', source_sha256='fixture',
        export_audit_id='fixture')
    assert report['source_entries'] == report['lexical_owners'] == 1
    with zipfile.ZipFile(output) as archive:
        terms = json.loads(archive.read('term_bank_1.json'))
        assert [(r[0], r[1]) for r in terms] == [('づくり', 'づくり')]
        assert archive.read('LICENSE') == b'fixture license'
        assert not any(n.startswith('term_meta_bank') for n in archive.namelist())
