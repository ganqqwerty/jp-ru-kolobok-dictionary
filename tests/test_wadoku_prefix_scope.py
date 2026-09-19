from pathlib import Path

from jitendex_ru.util import sha256_file
from jitendex_ru import wadoku_scope


def test_prefix_inventory_freezes_exact_source_order(tmp_path, monkeypatch):
    source = tmp_path / 'wadoku.xml'
    source.write_text('''<entries>
      <entry id="11"><form><orth>一</orth><reading><hira>いち</hira></reading></form><sense><trans><tr>eins</tr></trans></sense></entry>
      <entry id="12"><form><orth>二</orth><reading><hira>に</hira></reading></form><sense><trans><tr>zwei</tr></trans></sense></entry>
      <entry id="13"><form><orth>三</orth><reading><hira>さん</hira></reading></form><sense><trans><tr>drei</tr></trans></sense></entry>
    </entries>''', encoding='utf-8')
    monkeypatch.setitem(wadoku_scope.EXPECTED_COUNTS, 'entries', 3)
    data = wadoku_scope.prefix_inventory(source, expected_sha256=sha256_file(source), size=2)
    assert [entry['entry_id'] for entry in data['entries']] == [11, 12]
    assert [entry['ordinal'] for entry in data['entries']] == [1, 2]
    assert data['manifest']['entry_count'] == 2
    assert data['manifest']['source_entry_count'] == 3
    assert data['manifest']['prefix_size'] == 2


def test_prefix_inventory_rejects_oversized_scope(tmp_path, monkeypatch):
    source = tmp_path / 'wadoku.xml'
    source.write_text('<entries/>', encoding='utf-8')
    monkeypatch.setitem(wadoku_scope.EXPECTED_COUNTS, 'entries', 1)
    try:
        wadoku_scope.prefix_inventory(source, expected_sha256=sha256_file(source), size=2)
    except ValueError as error:
        assert 'outside the source inventory' in str(error)
    else:
        raise AssertionError('oversized prefix was accepted')
