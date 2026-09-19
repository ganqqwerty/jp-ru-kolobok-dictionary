"""Apply explicit lexical ownership to already localized Yomitan rows."""
from __future__ import annotations

import copy
from typing import Any

from .wadoku_pipeline import ownership_groups
from .wadoku_xml import yomitan_rows, build_rich_archive, canonical_identity, literal_suffix_lookup
from .wadoku_quality import TEMPLATE_RE
from .wadoku_classification import fixed_template_alias


class AssemblyError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(f'assembly has {len(issues)} unresolved source entries: {issues}')


def lookup_rows(rows, metadata, decision):
    """Use supplied aliases; never expand an open slot by guessing a word."""
    result = []
    template_terms = set()
    for row in rows:
        if not TEMPLATE_RE.search(row[0] + row[1]):
            result.append(row)
            continue
        template_terms.add(row[0])
        aliases = [a for a in decision.get('lookup_aliases', []) if a['source_form'] == row[0]]
        if not aliases:
            raise ValueError(f'template lacks classified lookup aliases: {row[0]}')
        for alias in aliases:
            if not alias.get('expression') or not alias.get('reading') or TEMPLATE_RE.search(alias['expression'] + alias['reading']):
                raise ValueError('lookup alias is not a concrete expression/reading')
            if alias.get('kind') in {'suffix_only', 'prefix_only', 'internal_prefix'} and fixed_template_alias(
                    row[0], row[1], alias['kind']) != (alias['expression'], alias['reading']):
                raise ValueError('lookup alias loses fixed lexical material or reading alignment')
            mapped = copy.deepcopy(row)
            mapped[0], mapped[1] = alias['expression'], alias['reading']
            if alias.get('kind') == 'internal_prefix':
                # Scope every definition/example to the FULL source phrase.
                # This marker also lets the common writer group across batches.
                mapped[5] = [{'type':'structured-content', 'content':{
                    'tag':'div', 'data':{'content':'internal-prefix-construction'},
                    'content':[
                        {'tag':'div', 'lang':'ja', 'style':{'fontWeight':'bold'},
                         'content':row[0] + '【' + row[1] + '】'},
                        *([{'tag':'div', 'data':{'content':'construction-tags'},
                            'content':' '.join(filter(None, [row[2], row[7]]))}]
                          if row[2] or row[7] else []),
                        *[g['content'] if isinstance(g,dict) and g.get('type')=='structured-content' else g
                          for g in mapped[5]],
                    ]}}]
                mapped[2] = mapped[3] = mapped[7] = ''
            result.append(mapped)
    # Template pitch cannot be transferred to an expanded word or a suffix.
    return result, [m for m in metadata if m[0] not in template_terms]


def source_aligned_lookup(value, decision):
    """Resolve only alias alignment explicitly shared by a single XML reading."""
    from .wadoku_quality import tree_paths, plain
    if not decision.get('lookup_needs_review') or decision.get('article_policy') != 'independent':
        return decision
    form = next(n for n in value['tree']['children'] if n['tag'] == 'form')
    readings = [plain(n) for n, _ in tree_paths(form) if n['tag'] == 'hira']
    forms = [plain(n) for n in form['children'] if n['tag'] == 'orth' and n['attributes'].get('midashigo') != 'true']
    if len(readings) != 1 or not forms:
        return decision
    expected = {(s, literal_suffix_lookup(s, readings[0])) for s in forms}
    if any(pair is None for _, pair in expected):
        return decision
    aliases = decision.get('lookup_aliases', [])
    actual = {(a['source_form'], (a['expression'], a['reading'])) for a in aliases if a['kind'] == 'suffix_only'}
    if len(aliases) != len(actual) or actual != expected:
        return decision
    return {**decision, 'lookup_needs_review': None,
            'source_lookup_alignment': {'reading': readings[0], 'forms': forms,
                                        'rule': 'single-form-reading-exact-suffix-v1'}}


def grouped_rows(entries, decisions, labels, *, language='ru'):
    """Return rows per lexical owner without discarding inherited content.

    Cross-entry pitch/sense alignment is not implied by lexical ownership.
    Ambiguous multi-group inheritance must be resolved before assembly.
    """
    values = {value['entry_id']: (value, targets) for value, targets in entries}
    if len(values) != len(entries):
        raise ValueError('duplicate source entry in assembly')
    groups = ownership_groups(list(values), decisions)
    rendered = {}
    issues = []
    for entry_id, (value, targets) in values.items():
        decision = source_aligned_lookup(value, decisions[entry_id])
        if decision.get('lookup_needs_review'):
            issues.append({'entry_id': entry_id, 'code': 'unresolved_lookup',
                           'reason': decision['lookup_needs_review']})
            continue
        try:
            rows, metadata = yomitan_rows(value, language, labels, targets)
            rendered[entry_id] = lookup_rows(rows, metadata, decision)
            if not rendered[entry_id][0]:
                issues.append({'entry_id': entry_id, 'code': 'no_lookup_rows'})
        except ValueError as error:
            issues.append({'entry_id': entry_id, 'code': 'render_error', 'reason': str(error)})
    if issues:
        raise AssemblyError(issues)
    return merge_grouped_rows(rendered, groups)


def merge_grouped_rows(rendered, groups):
    """Merge already rendered rows into explicit lexical ownership groups."""
    result = {}
    for owner, members in groups.items():
        rows, metadata = copy.deepcopy(rendered[owner])
        if not rows:
            raise ValueError(f'owner has no lookup rows: {owner}')
        if len(members) > 1 and any(
                len({row[6] for row in rendered[m['entry_id']][0]}) != 1 for m in members):
            raise ValueError(f'inherited pitch/sense alignment requires review: {owner}')
        for member in members:
            entry_id = member['entry_id']
            if entry_id == owner:
                continue
            child_rows, child_metadata = copy.deepcopy(rendered[entry_id])
            # Same-entry spelling rows repeat the same glossary. Append it once.
            child_content = child_rows[0][5]
            for row in rows:
                for gloss in child_content:
                    if gloss not in row[5]:
                        row[5].append(copy.deepcopy(gloss))
            if member['lookup_policy'] == 'shared_direct':
                for child in child_rows:
                    child[5] = copy.deepcopy(rows[0][5])
                    child[6] = rows[0][6]
                    rows.append(child)
                metadata.extend(child_metadata)
            # deinflect_to_parent deliberately contributes content, not a row.
        # Later siblings must also appear under earlier spelling variants.
        if len(members) > 1:
            for row in rows:
                row[5] = copy.deepcopy(rows[0][5])
        unique_rows = []
        for row in rows:
            if row not in unique_rows:
                unique_rows.append(row)
        unique_metadata = []
        for item in metadata:
            if item not in unique_metadata:
                unique_metadata.append(item)
        result[owner] = (unique_rows, unique_metadata)
    return result


def prepare_grouped_rows(entries, decisions, *, labels, reference_targets=None, language='ru'):
    """Common reference and ownership preparation for preflight and export."""
    entries = list(entries)
    values = {v['entry_id']: (v, t) for v, t in entries}
    groups = ownership_groups([v['entry_id'] for v, _ in entries], decisions)
    references = copy.deepcopy(reference_targets or {})
    for owner, members in groups.items():
        expression, reading, _ = canonical_identity(values[owner][0])
        if TEMPLATE_RE.search(expression + reading):
            aliases = decisions[owner].get('lookup_aliases', [])
            if not aliases:
                raise ValueError(f'owner has no concrete reference target: {owner}')
            expression, reading = aliases[0]['expression'], aliases[0]['reading']
        for member in members:
            references[str(member['entry_id'])] = {'expression': expression, 'reading': reading}
    localized = [({**value, 'reference_targets': references}, targets) for value, targets in entries]
    rendered = grouped_rows(localized, decisions, labels, language=language)
    owners = [(value, targets) for value, targets in localized if value['entry_id'] in rendered]
    return owners, rendered


def build_grouped_archive(entries, decisions, output, *, labels, reference_targets=None,
                          language='ru', **options):
    """Write the common archive from complete localized ownership groups."""
    entries = list(entries)
    owners, rendered = prepare_grouped_rows(entries, decisions, labels=labels,
        reference_targets=reference_targets, language=language)
    report = build_rich_archive(iter(owners), output, language=language, labels=labels,
        row_factory=lambda value, *_: rendered[value['entry_id']], **options)
    report.update(source_entries=len(entries), lexical_owners=len(owners))
    return report


def assembly_preflight(connection, config, run_id):
    """Read-only structural exercise of every projection, using source text."""
    import json
    from pathlib import Path
    from .wadoku_pipeline import localized_projection
    from .wadoku_xml import reference_target_index, label_catalog
    from .wadoku_quality import pronunciation_records
    from .util import sha256_file
    from .wadoku_transcriptions import apply_stored_resolutions
    snapshot = connection.execute('''SELECT s.metadata_json,r.pipeline_version FROM run r
        JOIN source_snapshot s ON s.id=r.dictionary_snapshot_id WHERE r.id=?''', (run_id,)).fetchone()
    if not snapshot or snapshot['pipeline_version'] != 'wadoku-xml-v3':
        raise ValueError('rich Wadoku run required')
    source = json.loads(snapshot['metadata_json'])
    xml = Path(source['xml_path'])
    if sha256_file(xml) != source['xml_sha256']:
        raise ValueError('source XML differs from frozen snapshot')
    entries, decisions, limitations = [], {}, []
    for row in connection.execute('''SELECT p.projection_json FROM run_article ra LEFT JOIN wadoku_projection p
        ON p.run_id=ra.run_id AND p.article_id=ra.article_id WHERE ra.run_id=? ORDER BY ra.article_id''', (run_id,)):
        if row[0] is None:
            raise ValueError('run article has no projection')
        projection = json.loads(row[0])
        targets = {u['unit_id']: json.loads(u['source_text']) if u['role']=='glossary_set' else u['source_text']
                   for u in projection['units']}
        value, blocks = localized_projection(projection, targets)
        value = apply_stored_resolutions(connection, run_id, value)
        entries.append((value, blocks))
        decision = projection['context']['article_group_decision']
        decisions[value['entry_id']] = decision
        limitations.extend({'entry_id': value['entry_id'], **item}
                           for item in pronunciation_records(value)['limitations'])
    if not entries:
        raise ValueError('empty preflight scope')
    report = {'run_id': run_id, 'source_only': True, 'source_entries': len(entries),
              'translation_quality_checked': False, 'limitations': limitations, 'issues': []}
    try:
        owners, rows = prepare_grouped_rows(entries, decisions,
            labels=label_catalog(config.root/'terminology/wadoku-xml-labels-v2.json'),
            reference_targets=reference_target_index(xml))
        report.update(lexical_owners=len(owners), term_rows=sum(len(v[0]) for v in rows.values()))
    except AssemblyError as error:
        report['issues'] = error.issues
    return report


def export_candidate(connection, config, run_id, output, license_path, *, diagnostic=False):
    """Export a complete run for inspection, not a semantic release approval."""
    import json
    import os
    import tempfile
    import uuid
    from pathlib import Path
    from .db import audit
    from .util import sha256_file
    from .wadoku_pipeline import localized_run
    from .wadoku_xml import reference_target_index, label_catalog
    from .schema_validation import validate_archive
    from .wadoku_transcriptions import apply_stored_resolutions

    output = Path(output).resolve()
    if output.exists():
        raise ValueError('candidate output already exists; choose a new path')
    # Review workers may finish while this export runs. Keep targets and the
    # accompanying unresolved-status report on one PostgreSQL snapshot.
    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    entries = [(apply_stored_resolutions(connection, run_id, value), blocks)
               for value, blocks in localized_run(connection, run_id, allow_unreviewed=diagnostic)]
    unreviewed = [dict(row) for row in connection.execute('''SELECT t.unit_id,t.id AS translation_id,
        t.review_reason,
        (SELECT r.decision FROM review r WHERE r.translation_id=t.id ORDER BY r.id DESC LIMIT 1) AS review_decision,
        (SELECT r.reason FROM review r WHERE r.translation_id=t.id ORDER BY r.id DESC LIMIT 1) AS review_detail
        FROM translation t WHERE t.run_id=? AND t.accepted=0
        AND t.id=(SELECT MAX(latest.id) FROM translation latest
                  WHERE latest.run_id=t.run_id AND latest.unit_id=t.unit_id) ORDER BY t.unit_id''', (run_id,))]
    snapshot = connection.execute('''SELECT s.* FROM run r JOIN source_snapshot s
        ON s.id=r.dictionary_snapshot_id WHERE r.id=?''', (run_id,)).fetchone()
    source = json.loads(snapshot['metadata_json'])
    xml = Path(source['xml_path'])
    if sha256_file(xml) != source['xml_sha256']:
        raise ValueError('source XML differs from frozen snapshot')
    if sha256_file(license_path) != config.raw['source']['license_sha256']:
        raise ValueError('license differs from configured source')
    decisions = {}
    for row in connection.execute('SELECT projection_json FROM wadoku_projection WHERE run_id=?', (run_id,)):
        projection = json.loads(row[0])
        decision = projection['context']['article_group_decision']
        decisions[decision['entry_id']] = decision
    export_id = uuid.uuid4().hex
    labels = label_catalog(config.root / 'terminology/wadoku-xml-labels-v2.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wadoku-export-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'candidate.zip'
        report = build_grouped_archive(entries, decisions, staged, labels=labels,
            reference_targets=reference_target_index(xml), license_text=Path(license_path).read_bytes(),
            title=(f'Wadoku RU · диагностика {run_id} · НЕ ПРОВЕРЕНО' if diagnostic
                   else f'Wadoku RU · кандидат {run_id}'), revision=f'rich-v3-run-{run_id}-{export_id[:8]}',
            source_url=snapshot['url'], source_sha256=snapshot['sha256'], export_audit_id=export_id,
            pipeline_version='wadoku-xml-v3',
            description_note=('Диагностическая сборка: языковая проверка не завершена. ' if diagnostic else '')
                + 'Кандидат для проверки. Классификация и примеры не считаются одобренными этим экспортом.')
        report.update(validate_archive(staged, config.root / 'schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162'))
        report.update(run_id=run_id, output=str(output), release_approved=False,
                      sha256=sha256_file(staged), diagnostic=diagnostic,
                      unaccepted_units=unreviewed)
        # Exclusive publication: never overwrite a previous manual-test artifact.
        os.link(staged, output)
    audit(connection, 'wadoku_candidate_export', 'run', str(run_id), report)
    connection.commit()
    return report
