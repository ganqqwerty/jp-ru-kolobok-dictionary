#!/usr/bin/env python3
"""Choose 100 new extension entries for an early translation quality check."""
import argparse
from collections import Counter
import json
from pathlib import Path

from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes
from jitendex_ru.wadoku_quality import plain, tree_paths


QUOTAS = (
    ('internal_slot', 9), ('suffix', 15), ('prefix', 10),
    ('multiple_senses', 20), ('linked_examples', 20),
    ('cross_reference', 16), ('ordinary', 10),
)


def choose(old, new):
    previous = {entry['entry_id'] for entry in old['entries']}
    if (new['manifest'].get('retained_scope_id') != old['manifest']['scope_id']
            or len(previous) != 20_000 or len(new['entries']) != 30_000):
        raise ValueError('pilot needs the exact retained 20,000 and extended 30,000 scopes')
    candidates = Counter(item['parent_id'] for item in new['candidates'])
    eligible = {label: [] for label, _ in QUOTAS}
    for entry in new['entries']:
        entry_id = entry['entry_id']
        if entry_id in previous:
            continue
        nodes = [node for node, _ in tree_paths(entry['source']['tree'])]
        forms = [plain(node) for node in nodes if node['tag'] == 'orth']
        slot_forms = [form for form in forms if '…' in form]
        tags = {node['tag'] for node in nodes}
        flags = {
            'internal_slot': any(not f.startswith('…') and not f.endswith('…') for f in slot_forms),
            'suffix': any(f.startswith('…') for f in slot_forms),
            'prefix': any(f.endswith('…') for f in slot_forms),
            'multiple_senses': sum(node['tag'] == 'sense' for node in nodes) > 1,
            'linked_examples': candidates[entry_id] > 0,
            'cross_reference': bool(tags & {'ref', 'sref'}),
            'ordinary': True,
        }
        for label, matches in flags.items():
            if matches:
                eligible[label].append(entry_id)
    selected = []
    used = set()
    for label, target in QUOTAS:
        ordered = sorted(eligible[label], key=lambda value: sha256_bytes(f'wadoku-extension-pilot:{value}'.encode()))
        available = [entry_id for entry_id in ordered if entry_id not in used]
        if len(available) < target:
            raise ValueError(f'insufficient distinct pilot entries for {label}')
        for entry_id in available[:target]:
            used.add(entry_id)
            selected.append({'entry_id': entry_id, 'category': label})
    if len(selected) != 100 or len(used) != 100:
        raise ValueError('pilot size differs from 100')
    return {'version': 'wadoku-extension-pilot-v1',
            'scope_id': new['manifest']['scope_id'],
            'retained_scope_id': old['manifest']['scope_id'],
            'entries': selected, 'entry_ids': [row['entry_id'] for row in selected],
            'category_counts': dict(Counter(row['category'] for row in selected))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-scope', type=Path, required=True)
    parser.add_argument('--new-scope', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    selection = choose(json.loads(args.old_scope.read_text()), json.loads(args.new_scope.read_text()))
    atomic_write(args.output, canonical_json(selection))
    print(json.dumps({'scope_id': selection['scope_id'],
                      'entry_count': len(selection['entry_ids']),
                      'category_counts': selection['category_counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
