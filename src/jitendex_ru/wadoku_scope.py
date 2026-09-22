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


def prefix_inventory(source: Path, *, expected_sha256: str, size: int) -> dict[str, Any]:
    """Freeze the first N source entries and all context needed to process them."""
    if not 1 <= size <= EXPECTED_COUNTS['entries']:
        raise ValueError('prefix scope size is outside the source inventory')
    if sha256_file(source) != expected_sha256:
        raise ValueError('source XML hash mismatch')
    entries = []
    selected: set[int] = set()
    parent_ids: set[int] = set()
    for ordinal, value in iter_canonical_entries(source):
        if ordinal > size:
            break
        entry_id = int(value['entry_id'])
        selected.add(entry_id)
        entries.append({'ordinal': ordinal, 'entry_id': entry_id, 'categories': ['source-prefix'],
                        'source': value, 'source_sha256': sha256_bytes(canonical_json(value))})
        parent_ids.update(int(node['attributes']['id']) for node, _ in tree_paths(value['tree'])
                          if node['tag'] in {'ref', 'sref'} and node['attributes'].get('type') == 'main'
                          and node['attributes'].get('id'))
    if len(entries) != size or len(selected) != size:
        raise ValueError('source prefix is incomplete or contains duplicate entry IDs')
    candidates: list[dict[str, Any]] = []
    parent_contexts: dict[str, dict[str, Any]] = {}
    reference_targets: dict[str, dict[str, str]] = {}
    source_count = 0
    for _ordinal, value in iter_canonical_entries(source):
        source_count += 1
        entry_id = int(value['entry_id'])
        if entry_id in parent_ids:
            parent_contexts[str(entry_id)] = value
        if entry_id in parent_ids:
            expression, reading, _sequence = canonical_identity(value)
            reference_targets[str(entry_id)] = {'expression': expression, 'reading': reading}
        candidates.extend(example_candidates([value], selected))
    if source_count != EXPECTED_COUNTS['entries']:
        raise ValueError('full XML entry count differs')
    missing_parents = parent_ids - {int(key) for key in parent_contexts}
    if missing_parents:
        raise ValueError(f'main-reference contexts absent: {sorted(missing_parents)}')
    manifest = {'version': 'wadoku-prefix-scope-v1', 'xml_sha256': expected_sha256,
                'entry_count': len(entries), 'source_entry_count': source_count,
                'candidate_count': len(candidates), 'category_counts': {'source-prefix': len(entries)},
                'parent_contexts': parent_contexts, 'reference_targets': reference_targets,
                'prefix_size': size, 'selection_rule': 'first-source-entries-v1'}
    manifest['scope_id'] = sha256_bytes(canonical_json(
        [manifest, [(entry['entry_id'], entry['source_sha256']) for entry in entries]]))
    if sha256_file(source) != expected_sha256:
        raise ValueError('source changed while inventorying')
    return {'manifest': manifest, 'entries': entries, 'candidates': candidates}


def link_closed_prefix_ids(
    order: list[int], references: dict[int, set[int]], *, size: int,
) -> tuple[set[int], set[int], int]:
    """Choose exactly ``size`` entries while closing every resolvable reference.

    The largest possible source prefix is preferred. Remaining capacity is
    filled in source order, but a filler is accepted only together with its
    complete transitive reference closure.
    """
    if not 1 <= size <= len(order) or len(set(order)) != len(order):
        raise ValueError('link-closed scope size or source order is invalid')
    known = set(order)

    def closed(seed: set[int]) -> set[int]:
        result = set(seed)
        pending = list(seed)
        while pending:
            for target in references.get(pending.pop(), set()):
                if target in known and target not in result:
                    result.add(target)
                    pending.append(target)
        return result

    low, high = 0, size
    while low < high:
        middle = (low + high + 1) // 2
        if len(closed(set(order[:middle]))) <= size:
            low = middle
        else:
            high = middle - 1
    seed_size = low
    selected = closed(set(order[:seed_size]))
    filler_seeds: set[int] = set()
    while len(selected) < size:
        for entry_id in order:
            if entry_id in selected:
                continue
            expanded = selected | closed({entry_id})
            if len(expanded) <= size:
                selected = expanded
                filler_seeds.add(entry_id)
                break
        else:
            raise ValueError(f'cannot fill link-closed scope exactly: {len(selected)}/{size}')
    return selected, filler_seeds, seed_size


def link_closed_extension_ids(
    order: list[int], references: dict[int, set[int]], *, retained: set[int], size: int,
) -> tuple[set[int], set[int]]:
    """Extend an existing closed scope without dropping or reselecting its entries."""
    if not retained or not retained <= set(order) or not len(retained) < size <= len(order):
        raise ValueError('invalid retained scope or extension size')
    known = set(order)
    if any((references.get(entry_id, set()) & known) - retained for entry_id in retained):
        raise ValueError('retained scope is not link closed')
    selected = set(retained)
    seeds: set[int] = set()
    for entry_id in order:
        if len(selected) == size:
            break
        if entry_id in selected:
            continue
        closure = {entry_id}
        pending = [entry_id]
        while pending:
            for target in references.get(pending.pop(), set()) & known:
                if target not in selected and target not in closure:
                    closure.add(target)
                    pending.append(target)
            if len(selected) + len(closure) > size:
                break
        if len(selected) + len(closure) <= size:
            selected.update(closure)
            seeds.add(entry_id)
    if len(selected) != size:
        raise ValueError(f'cannot extend link-closed scope exactly: {len(selected)}/{size}')
    return selected, seeds


def linked_prefix_inventory(source: Path, *, expected_sha256: str, size: int,
                            retained_scope: Path | None = None) -> dict[str, Any]:
    """Freeze an exact-size prefix-oriented scope with transitive link closure."""
    if not 1 <= size <= EXPECTED_COUNTS['entries']:
        raise ValueError('linked scope size is outside the source inventory')
    if sha256_file(source) != expected_sha256:
        raise ValueError('source XML hash mismatch')
    summaries: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    references: dict[int, set[int]] = {}
    raw_references: dict[int, list[dict[str, str]]] = {}
    for ordinal, value in iter_canonical_entries(source):
        expression, reading, entry_id = canonical_identity(value)
        refs = [dict(node['attributes']) | {'tag': node['tag']} for node, _ in tree_paths(value['tree'])
                if node['tag'] in {'ref', 'sref'} and node['attributes'].get('id')]
        order.append(entry_id)
        summaries[entry_id] = {'ordinal': ordinal, 'expression': expression, 'reading': reading}
        references[entry_id] = {int(ref['id']) for ref in refs}
        raw_references[entry_id] = refs
    if len(order) != EXPECTED_COUNTS['entries']:
        raise ValueError('full XML entry count differs')
    retained = json.loads(retained_scope.read_text()) if retained_scope else None
    retained_ids = {entry['entry_id'] for entry in retained['entries']} if retained else set()
    retained_entries = {entry['entry_id']: entry for entry in retained['entries']} if retained else {}
    if retained:
        old_manifest = retained['manifest']
        if (old_manifest.get('version') not in {'wadoku-linked-prefix-scope-v1', 'wadoku-linked-prefix-scope-v2'}
                or old_manifest['xml_sha256'] != expected_sha256
                or len(retained_ids) != old_manifest['entry_count']):
            raise ValueError('retained scope is incompatible or incomplete')
        selected, filler_seeds = link_closed_extension_ids(
            order, references, retained=retained_ids, size=size)
        seed_size = old_manifest['prefix_size']
        seed_ids = set(order[:seed_size])
    else:
        selected, filler_seeds, seed_size = link_closed_prefix_ids(order, references, size=size)
        seed_ids = set(order[:seed_size])
    known = set(order)
    dangling = sorted({target for entry_id in selected for target in references[entry_id] if target not in known})
    dependencies = [
        {'from_id': entry_id, 'target_id': int(ref['id']), 'relation': ref,
         'state': 'included' if int(ref['id']) in selected else 'missing-from-source'}
        for entry_id in order if entry_id in selected for ref in raw_references[entry_id]
    ]
    external = [row for row in dependencies if row['state'] != 'included']
    if any(row['target_id'] in known for row in external):
        raise ValueError('link-closed selection left a resolvable target outside the scope')
    entries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for ordinal, value in iter_canonical_entries(source):
        entry_id = int(value['entry_id'])
        if entry_id in selected:
            if entry_id in retained_entries:
                old = retained_entries[entry_id]
                if old['ordinal'] != ordinal or old['source']['entry_id'] != entry_id:
                    raise ValueError('retained source identity or ordinal changed')
                value = old['source']
            if entry_id in retained_ids:
                categories = ['retained-source-entry']
            elif entry_id in seed_ids:
                categories = ['source-prefix']
            elif entry_id in filler_seeds:
                categories = ['closure-filler']
            else:
                categories = ['reference-dependency']
            entries.append({'ordinal': ordinal, 'entry_id': entry_id, 'categories': categories,
                            'source': value, 'source_sha256': sha256_bytes(canonical_json(value))})
        candidates.extend(example_candidates([value], selected))
    targets = {str(target): {'expression': summaries[target]['expression'],
                             'reading': summaries[target]['reading']}
               for entry_id in selected for target in references[entry_id] if target in selected}
    manifest = {
        'version': 'wadoku-linked-prefix-scope-v2' if retained else 'wadoku-linked-prefix-scope-v1',
        'xml_sha256': expected_sha256,
        'entry_count': len(entries), 'source_entry_count': len(order),
        'candidate_count': len(candidates),
        'category_counts': dict(Counter(c for row in entries for c in row['categories'])),
        'parent_contexts': {}, 'reference_targets': targets, 'dependencies': dependencies,
        'dangling_source_target_ids': dangling, 'prefix_size': seed_size,
        'requested_size': size, 'selection_rule': 'largest-prefix-transitive-reference-closure-v1',
    }
    if retained:
        manifest.update(retained_scope_id=retained['manifest']['scope_id'],
                        retained_entry_count=len(retained_ids),
                        added_entry_count=size-len(retained_ids),
                        selection_rule='retained-link-closed-source-extension-v1')
        old_hashes = {entry['entry_id']: entry['source_sha256'] for entry in retained['entries']}
        if any(entry['source_sha256'] != old_hashes[entry['entry_id']]
               for entry in entries if entry['entry_id'] in retained_ids):
            raise ValueError('retained source entry changed')
    manifest['scope_id'] = sha256_bytes(canonical_json(
        [manifest, [(entry['entry_id'], entry['source_sha256']) for entry in entries]]))
    if len(entries) != size or len(selected) != size:
        raise ValueError('link-closed scope did not reach its exact requested size')
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
