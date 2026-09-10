"""Freeze a representative scope and its complete reverse-example inventory."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .util import canonical_json, sha256_bytes, sha256_file
from .wadoku_xml import iter_canonical_entries, canonical_identity, EXPECTED_COUNTS
from .wadoku_quality import tree_paths, plain, example_candidates

SUPPLEMENT_FORMS = {'知らない', '知らない人', 'ほだし', 'ほだす', 'たまらない', 'か', 'の'}


def collect_example_sources(source: Path, expected_sha256: str, examples: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Resolve all accepted child contexts in one verified XML pass."""
    from .wadoku_quality import source_identity
    required: dict[int, str] = {}
    for candidate in examples:
        if candidate['selection_state'] != 'accepted':
            continue
        child_id, digest = candidate['child_id'], candidate['source_sha256']
        if child_id in required and required[child_id] != digest:
            raise ValueError('conflicting example source identities')
        required[child_id] = digest
    if sha256_file(source) != expected_sha256:
        raise ValueError('example source XML hash differs')
    found = {}
    if required:
        for _, value in iter_canonical_entries(source):
            child_id = value['entry_id']
            if child_id not in required:
                continue
            if source_identity(value) != required[child_id]:
                raise ValueError(f'example child source changed: {child_id}')
            found[child_id] = value
            if len(found) == len(required):
                break
    if set(found) != set(required):
        raise ValueError(f'example child source absent: {sorted(set(required)-set(found))}')
    if sha256_file(source) != expected_sha256:
        raise ValueError('source changed during example-context collection')
    return found


def inventory(source: Path, core_path: Path, *, expected_sha256: str, size: int = 190,
              retained_scope: Path | None = None, required_ids: list[int] | None = None) -> dict[str, Any]:
    if not 150 <= size <= 200:
        raise ValueError('pilot scope must contain 150–200 source entries')
    if sha256_file(source) != expected_sha256:
        raise ValueError('source XML hash mismatch')
    core = json.loads(core_path.read_text())
    selected = {int(row['entry_id']): list(row['categories']) + ['regression-core'] for row in core['entries']}
    if len(selected) != 150:
        raise ValueError('expected the unchanged 150-entry regression core')
    retained = json.loads(retained_scope.read_text()) if retained_scope else None
    if retained:
        if (retained['manifest']['xml_sha256'] != expected_sha256
                or retained['manifest']['core_sha256'] != sha256_file(core_path)):
            raise ValueError('retained scope uses different source or regression core')
        retained_entries = {e['entry_id']: e for e in retained['entries']}
        if len(retained_entries) != len(retained['entries']) or not set(selected) <= set(retained_entries):
            raise ValueError('retained scope has duplicates or lacks core entries')
        selected = {key: list(entry['categories']) for key, entry in retained_entries.items()}
    required_ids = sorted(set(required_ids or []))
    for entry_id in required_ids:
        selected.setdefault(entry_id, ['required-owner-dependency'])
    if len(selected) > size:
        raise ValueError('retained scope and required owners exceed requested size')
    summaries = {}
    supplement = []
    observed = Counter()
    for ordinal, value in iter_canonical_entries(source):
        expression, reading, entry_id = canonical_identity(value)
        forms = [plain(node) for node, _ in tree_paths(value['tree']) if node['tag'] == 'orth']
        refs = [dict(node['attributes']) for node, _ in tree_paths(value['tree'])
                if node['tag'] in {'ref', 'sref'} and node['attributes'].get('id')]
        summaries[entry_id] = {'expression': expression, 'reading': reading, 'refs': refs, 'ordinal': ordinal}
        if set(forms) & SUPPLEMENT_FORMS and entry_id not in selected:
            supplement.append(entry_id)
        for node, _ in tree_paths(value['tree']):
            observed[f"node:{node['tag']}"] += 1
            for key in node['attributes']:
                observed[f"attribute:{node['tag']}@{key}"] += 1
    if len(summaries) != EXPECTED_COUNTS['entries']:
        raise ValueError('full XML entry count differs')
    for entry_id in sorted(supplement):
        if entry_id in selected:
            continue
        if len(selected) >= size:
            raise ValueError('required supplement exceeds scope ceiling')
        selected[entry_id] = ['grammar-or-lexical-supplement']
    # Select reference dependencies by frequency, not arbitrary truncation of article content.
    incoming = Counter(int(ref['id']) for entry_id in selected for ref in summaries[entry_id]['refs'])
    for target, _count in sorted(incoming.items(), key=lambda item: (-item[1], item[0])):
        if len(selected) >= size:
            break
        if target not in selected and target in summaries:
            selected[target] = ['reference-dependency']
    # Deterministic unseen controls fill any remaining room, without altering the core.
    for entry_id in sorted(summaries, key=lambda key: sha256_bytes(f'wadoku-pilot-v6:{key}'.encode())):
        if len(selected) >= size:
            break
        if entry_id not in selected:
            selected[entry_id] = ['unseen-control']
    missing = set(selected) - set(summaries)
    if missing:
        raise ValueError(f'selected IDs absent from source: {sorted(missing)}')
    entries, candidates, parent_contexts = [], [], {}
    parent_ids = {int(ref['id']) for entry_id in selected for ref in summaries[entry_id]['refs']
                  if ref.get('type') == 'main'}
    for ordinal, value in iter_canonical_entries(source):
        entry_id = int(value['entry_id'])
        if entry_id in selected:
            entries.append({'ordinal': ordinal, 'entry_id': entry_id, 'categories': selected[entry_id],
                            'source': value, 'source_sha256': sha256_bytes(canonical_json(value))})
        if entry_id in parent_ids:
            parent_contexts[str(entry_id)] = value
        candidates.extend(example_candidates([value], set(selected)))
    dependencies = []
    for entry_id in selected:
        for ref in summaries[entry_id]['refs']:
            target = int(ref['id'])
            dependencies.append({'from_id': entry_id, 'target_id': target, 'relation': ref,
                'state': 'included' if target in selected else 'external-to-pilot' if target in summaries else 'unresolved'})
    targets = {str(row['target_id']): {'expression': summaries[row['target_id']]['expression'],
                                    'reading': summaries[row['target_id']]['reading']}
               for row in dependencies if row['target_id'] in summaries}
    manifest = {'version': 'wadoku-scope-v1', 'xml_sha256': expected_sha256,
                'core_sha256': sha256_file(core_path), 'entry_count': len(entries),
                'source_entry_count': len(summaries), 'candidate_count': len(candidates),
                'category_counts': dict(Counter(c for row in entries for c in row['categories'])),
                'dependencies': dependencies, 'reference_targets': targets,
                'parent_contexts': parent_contexts, 'source_inventory': dict(observed)}
    if retained or required_ids:
        manifest['version'] = 'wadoku-scope-v2'
        manifest['retained_scope_id'] = retained['manifest']['scope_id'] if retained else None
        manifest['required_owner_ids'] = required_ids
    if retained:
        actual = {e['entry_id']: e['source_sha256'] for e in entries}
        if any(actual.get(e['entry_id']) != e['source_sha256'] for e in retained['entries']):
            raise ValueError('retained source entry changed')
    manifest['scope_id'] = sha256_bytes(canonical_json([manifest, [(e['entry_id'], e['source_sha256']) for e in entries]]))
    if sha256_file(source) != expected_sha256:
        raise ValueError('source changed while inventorying')
    return {'manifest': manifest, 'entries': entries, 'candidates': candidates}


def store_scope(connection: Any, snapshot_id: int, data: dict[str, Any]) -> dict[str, Any]:
    manifest = data['manifest']
    scope_id = manifest['scope_id']
    connection.execute('SELECT pg_advisory_xact_lock(?)', (int(scope_id[:15], 16),))
    existing = connection.execute('SELECT manifest_json FROM wadoku_scope WHERE id=?', (scope_id,)).fetchone()
    if existing:
        if json.loads(existing[0]) != manifest:
            raise ValueError('scope manifest changed')
    else:
        connection.execute('INSERT INTO wadoku_scope(id,snapshot_id,manifest_json) VALUES (?,?,?)',
                           (scope_id, snapshot_id, canonical_json(manifest).decode()))
        for entry in data['entries']:
            connection.execute('''INSERT INTO wadoku_scope_entry(scope_id,entry_id,ordinal,categories_json,
                source_json,source_sha256) VALUES (?,?,?,?,?,?)''',
                (scope_id, entry['entry_id'], entry['ordinal'], canonical_json(entry['categories']).decode(),
                 canonical_json(entry['source']).decode(), entry['source_sha256']))
        for candidate in data['candidates']:
            connection.execute('''INSERT INTO wadoku_example_candidate(scope_id,parent_id,child_id,
                relation_path,candidate_json) VALUES (?,?,?,?,?)''',
                (scope_id, candidate['parent_id'], candidate['child_id'], candidate['relation_path'], canonical_json(candidate).decode()))
    counts = connection.execute('''SELECT
        (SELECT COUNT(*) FROM wadoku_scope_entry WHERE scope_id=?) entries,
        (SELECT COUNT(*) FROM wadoku_example_candidate WHERE scope_id=?) candidates''', (scope_id,scope_id)).fetchone()
    if counts['entries'] != manifest['entry_count'] or counts['candidates'] != manifest['candidate_count']:
        raise ValueError('incomplete PostgreSQL scope inventory')
    return {'scope_id': scope_id, **dict(counts), 'category_counts': manifest['category_counts']}
