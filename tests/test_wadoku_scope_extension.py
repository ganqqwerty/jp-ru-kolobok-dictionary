import pytest

from jitendex_ru.wadoku_scope import link_closed_extension_ids


def test_extension_retains_old_entries_and_adds_exact_closed_set():
    order = [1, 2, 3, 4, 5, 6, 7, 8]
    refs = {1: {8}, 2: {5}, 3: {6}, 4: {7}}
    selected, seeds = link_closed_extension_ids(order, refs, retained={1, 8}, size=5)
    assert selected == {1, 2, 5, 6, 8}
    assert seeds == {2, 6}
    assert all((refs.get(entry, set()) & set(order)) <= selected for entry in selected)


def test_extension_rejects_unclosed_retained_scope():
    with pytest.raises(ValueError, match='not link closed'):
        link_closed_extension_ids([1, 2, 3], {1: {3}}, retained={1}, size=2)
