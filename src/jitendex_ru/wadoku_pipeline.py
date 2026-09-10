"""Shared rich Wadoku contract for selected and full PostgreSQL runs.

This module does not classify lexical relations. Those decisions must be supplied
with their source evidence before projection; source articles remain immutable.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .util import canonical_json, sha256_bytes
from .wadoku_quality import translation_projection, example_translation_unit
from .wadoku_xml import canonical_identity

PIPELINE = "wadoku-xml-v3"
PREPARATION_VERSION = "wadoku-rich-prepare-v4-source-unit-order"


def ownership_groups(entry_ids: list[int], decisions: dict[int, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Group reviewed ownership only; never infer it from spelling or glosses.

    Members keep source IDs and lookup policies so downstream rendering can
    preserve every source sense and suppress only pure-inflection lookup rows.
    Pitch grouping remains a separate, sense-aware export step.
    """
    selected = set(entry_ids)
    if len(selected) != len(entry_ids) or set(decisions) != selected:
        raise ValueError('ownership decisions must cover the exact distinct scope')
    for entry_id, decision in decisions.items():
        policy, lookup, parent = (decision.get(k) for k in ('article_policy','lookup_policy','parent_id'))
        if policy == 'independent':
            if lookup != 'independent_direct' or parent is not None:
                raise ValueError('invalid independent ownership')
        elif policy == 'inherit_parent':
            if lookup not in {'shared_direct','deinflect_to_parent'}:
                raise ValueError('invalid inherited lookup policy')
            if parent not in selected:
                raise ValueError(f'ownership parent outside scope: {entry_id} -> {parent}')
        else:
            raise ValueError(f'unresolved ownership: {entry_id}')
    roots: dict[int, int] = {}
    for entry_id in entry_ids:
        path = []
        seen = set()
        current = entry_id
        while current not in roots:
            if current in seen:
                raise ValueError(f'ownership cycle: {path + [current]}')
            seen.add(current)
            path.append(current)
            if decisions[current]['article_policy'] == 'independent':
                roots[current] = current
                break
            current = decisions[current]['parent_id']
        root = roots[current]
        for member in path:
            roots[member] = root
    groups: dict[int, list[dict[str, Any]]] = {}
    for entry_id in entry_ids:
        groups.setdefault(roots[entry_id], []).append({'entry_id':entry_id,
            'lookup_policy':decisions[entry_id]['lookup_policy']})
    return groups


def record_ownership_adoption(connection: Any, review: dict[str, Any]) -> dict[str, Any]:
    """Store a source-bound lexical review separately from the Luna proposal."""
    from pathlib import Path
    from .db import audit
    from .wadoku_classification import source_context, validate_decision
    if not review.get('reviewer', '').strip() or not review.get('note', '').strip():
        raise ValueError('ownership adoption requires an actual review')
    scope_id, entry_id = review['scope_id'], review['entry_id']
    row = connection.execute('SELECT * FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=? FOR UPDATE',
                             (scope_id, entry_id)).fetchone()
    if not row or not row['decision_json']:
        raise ValueError('classification proposal absent')
    proposal = json.loads(row['decision_json'])
    if proposal['attempt_id'] != review['attempt_id']:
        raise ValueError('stale ownership review')
    attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?', (review['attempt_id'],)).fetchone()
    request = proposal.get('request') or json.loads(Path(attempt[0]).read_text())
    if request['scope_id'] != scope_id or request['entry'] != source_context(json.loads(row['source_json'])):
        raise ValueError('ownership source differs')
    corrections = review.get('corrections', {})
    if set(corrections) - {'article_policy', 'lookup_policy', 'parent_id', 'reason'}:
        raise ValueError('unsupported ownership correction')
    if corrections and not corrections.get('reason', '').strip():
        raise ValueError('ownership correction requires a reason')
    result = {**copy.deepcopy(proposal['result']), **copy.deepcopy(corrections)}
    errors = validate_decision(request, result)
    if errors or result['article_policy'] == 'needs_review':
        raise ValueError(f'unresolved or invalid ownership review: {errors}')
    ownership = {k: result[k] for k in ('article_policy','lookup_policy','parent_id','reason')}
    artifact = {'scope_id': scope_id, 'entry_id': entry_id, 'attempt_id': review['attempt_id'],
        'source_sha256': row['source_sha256'], 'proposal_sha256': sha256_bytes(canonical_json(proposal['result'])),
        'ownership': ownership, 'reviewer': review['reviewer'], 'note': review['note'],
        'corrections': copy.deepcopy(corrections), 'version': 'ownership-adoption-v1'}
    artifact['identity'] = sha256_bytes(canonical_json(artifact))
    entity = f'{scope_id}:{entry_id}'
    previous = connection.execute('''SELECT details_json FROM audit_event WHERE event_type='wadoku_ownership_adoption'
        AND entity_type='scope_entry' AND entity_id=? ORDER BY id DESC LIMIT 1''', (entity,)).fetchone()
    created = not previous or json.loads(previous[0]) != artifact
    if created:
        audit(connection, 'wadoku_ownership_adoption', 'scope_entry', entity, artifact)
    return {'entry_id': entry_id, 'identity': artifact['identity'], 'created': created, 'ownership': ownership}


def load_reviewed_examples(connection: Any, scope_id: str, entry_id: int) -> list[dict[str, Any]]:
    """Load only a current, complete reviewed example artifact for projection."""
    row = connection.execute('SELECT source_sha256,decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                             (scope_id, entry_id)).fetchone()
    if not row or not row['decision_json']:
        raise ValueError('entry classification missing')
    candidates = connection.execute('''SELECT child_id,relation_path,candidate_json FROM wadoku_example_candidate
        WHERE scope_id=? AND parent_id=?''', (scope_id, entry_id)).fetchall()
    if not candidates:
        return []
    event = connection.execute('''SELECT details_json FROM audit_event WHERE event_type='wadoku_example_adoption'
        AND entity_type='scope_entry' AND entity_id=? ORDER BY id DESC LIMIT 1''',
        (f'{scope_id}:{entry_id}',)).fetchone()
    if not event:
        raise ValueError('example semantic review missing')
    artifact = json.loads(event[0])
    identity = artifact.pop('identity')
    proposal = json.loads(row['decision_json'])
    if (sha256_bytes(canonical_json(artifact)) != identity or artifact['source_sha256'] != row['source_sha256']
            or artifact['attempt_id'] != proposal['attempt_id']
            or artifact['scope_id'] != scope_id or artifact['entry_id'] != entry_id):
        raise ValueError('stale example review')
    examples = artifact['examples']
    expected = {(c['child_id'],c['relation_path']): json.loads(c['candidate_json'])['source_sha256'] for c in candidates}
    observed = {(c['child_id'],c['relation_path']): c['source_sha256'] for c in examples}
    if len(observed) != len(examples) or observed != expected:
        raise ValueError('reviewed example coverage differs from source')
    for example in examples:
        review = example['decision']['review']
        if (example['parent_id'] != entry_id or example['selection_state'] not in {'accepted','rejected'}
                or review['proposal_sha256'] != sha256_bytes(canonical_json(proposal['result']))):
            raise ValueError('stale or unresolved reviewed example')
    return examples


def record_example_adoption(connection: Any, review: dict[str, Any]) -> dict[str, Any]:
    """Persist reviewed examples as a derived audit artifact, never as source edits."""
    from pathlib import Path
    from .db import audit
    from .wadoku_classification import source_context
    scope_id, entry_id = review['scope_id'], review['entry_id']
    row = connection.execute('SELECT * FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=? FOR UPDATE',
                             (scope_id, entry_id)).fetchone()
    if not row or not row['decision_json']:
        raise ValueError('source entry has no classification proposal')
    proposal = json.loads(row['decision_json'])
    if proposal['attempt_id'] != review['attempt_id']:
        raise ValueError('review refers to a different proposal attempt')
    attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?', (review['attempt_id'],)).fetchone()
    if not attempt:
        raise ValueError('proposal attempt absent')
    request = proposal.get('request') or json.loads(Path(attempt['request_path']).read_text())
    if request['scope_id'] != scope_id or request['entry'] != source_context(json.loads(row['source_json'])):
        raise ValueError('review request differs from frozen source')
    for candidate in request['examples']:
        stored = connection.execute('''SELECT candidate_json FROM wadoku_example_candidate
            WHERE scope_id=? AND parent_id=? AND child_id=? AND relation_path=?''',
            (scope_id, entry_id, candidate['child_id'], candidate['relation_path'])).fetchone()
        if not stored or json.loads(stored[0])['source_sha256'] != candidate['source_sha256']:
            raise ValueError('example source differs from frozen scope')
    examples = adopt_examples(request, proposal['result'], reviewer=review['reviewer'],
                              review_note=review['note'], corrections=review.get('corrections'))
    artifact = {'scope_id': scope_id, 'entry_id': entry_id, 'attempt_id': review['attempt_id'],
                'source_sha256': row['source_sha256'], 'examples': examples}
    artifact['identity'] = sha256_bytes(canonical_json(artifact))
    entity = f'{scope_id}:{entry_id}'
    previous = connection.execute('''SELECT details_json FROM audit_event
        WHERE event_type='wadoku_example_adoption' AND entity_type='scope_entry' AND entity_id=?
        ORDER BY id DESC LIMIT 1''', (entity,)).fetchone()
    created = not previous or json.loads(previous[0]) != artifact
    if created:
        audit(connection, 'wadoku_example_adoption', 'scope_entry', entity, artifact)
    return {'identity': artifact['identity'], 'entry_id': entry_id, 'created': created,
            'accepted_examples': sum(e['selection_state'] == 'accepted' for e in examples)}


def candidate_examples(request: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """Stage classifier selections for translation, without claiming final review."""
    from .wadoku_classification import validate_decision
    errors = validate_decision(request, proposal)
    if errors:
        raise ValueError(f'invalid candidate classification: {errors}')
    states = {'accept': 'accepted', 'reject': 'rejected', 'needs_review': 'unresolved'}
    return [{**copy.deepcopy(candidate), 'selection_state': states[decision['state']],
             'sense_path': decision['sense_path'],
             'decision': {**copy.deepcopy(decision), 'stage': 'classifier_candidate',
                          'request_sha256': request['manifest_sha256']}}
            for candidate, decision in zip(request['examples'], proposal['examples'])]


def load_candidate_examples(connection: Any, scope_id: str, entry_id: int) -> list[dict[str, Any]]:
    """Load a source-bound proposal; do not create an adoption audit event."""
    from pathlib import Path
    from .wadoku_classification import source_context
    row = connection.execute('SELECT source_json,decision_json FROM wadoku_scope_entry WHERE scope_id=? AND entry_id=?',
                             (scope_id, entry_id)).fetchone()
    if not row or not row['decision_json']:
        raise ValueError('candidate classification missing')
    proposal = json.loads(row['decision_json'])
    attempt = connection.execute('SELECT request_path FROM attempt WHERE id=?', (proposal['attempt_id'],)).fetchone()
    if not attempt:
        raise ValueError('candidate attempt missing')
    request = proposal.get('request') or json.loads(Path(attempt['request_path']).read_text())
    if request['scope_id'] != scope_id or request['entry'] != source_context(json.loads(row['source_json'])):
        raise ValueError('candidate request differs from frozen source')
    stored = connection.execute('SELECT child_id,relation_path,candidate_json FROM wadoku_example_candidate WHERE scope_id=? AND parent_id=?',
                                (scope_id, entry_id)).fetchall()
    expected = {(c['child_id'], c['relation_path']): c['source_sha256'] for c in request['examples']}
    actual = {(c['child_id'], c['relation_path']): json.loads(c['candidate_json'])['source_sha256'] for c in stored}
    if actual != expected:
        raise ValueError('candidate example inventory differs from frozen scope')
    return candidate_examples(request, proposal['result'])


def adopt_examples(request: dict[str, Any], proposal: dict[str, Any], *, reviewer: str,
                   review_note: str, corrections: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Apply an explicit semantic review without rewriting the original proposal.

    The caller stores this result in the versioned projection. Calling this does
    not assert that lexical ownership or lookup decisions have been reviewed.
    """
    from .wadoku_classification import validate_decision
    if not reviewer.strip() or not review_note.strip():
        raise ValueError('example adoption requires an actual review record')
    result = copy.deepcopy(proposal)
    corrections = corrections or {}
    indexed = {row['decision_id']: row for row in result['examples']}
    if set(corrections) - set(indexed):
        raise ValueError('example correction outside proposal')
    for key, change in corrections.items():
        if (set(change) - {'state', 'source_path', 'sense_path', 'reason'}
                or not isinstance(change.get('reason'), str) or not change['reason'].strip()):
            raise ValueError('example correction needs a reason and supported fields')
        indexed[key].update(copy.deepcopy(change))
    errors = validate_decision(request, result)
    if errors:
        raise ValueError(f'invalid reviewed classification: {errors}')
    if any(row['state'] == 'needs_review' for row in result['examples']):
        raise ValueError('unresolved example decisions remain')
    review = {'reviewer': reviewer, 'note': review_note,
              'request_sha256': request['manifest_sha256'],
              'proposal_sha256': sha256_bytes(canonical_json(proposal)),
              'corrections': copy.deepcopy(corrections), 'version': 'example-adoption-v1'}
    adopted = []
    for candidate, decision in zip(request['examples'], result['examples']):
        item = copy.deepcopy(candidate)
        item['selection_state'] = 'accepted' if decision['state'] == 'accept' else 'rejected'
        item['sense_path'] = decision['sense_path']
        item['decision'] = {**copy.deepcopy(decision), 'review': copy.deepcopy(review)}
        if item['selection_state'] == 'accepted':
            example_translation_unit(item, decision['source_path'])
        adopted.append(item)
    return adopted


def prepare_run(connection: Any, *, snapshot_id: int, entries: list[tuple[int, dict[str, Any]]],
                versions: dict[str, str], limits: dict[str, Any],
                decisions: dict[int, dict[str, Any]] | None = None,
                example_sources: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Prepare any frozen scope through one contract, atomically and idempotently.

    Entries carry original XML ordinals. Decisions are derived overlays, never
    changes to article.raw_json. The caller verifies the source archive first.
    """
    from .db import audit
    if getattr(connection, "backend", None) != "postgresql":
        raise ValueError("rich Wadoku runs require PostgreSQL")
    if not entries or len({v['entry_id'] for _, v in entries}) != len(entries):
        raise ValueError("scope must contain distinct source entries")
    if len({ordinal for ordinal, _ in entries}) != len(entries):
        raise ValueError("duplicate source ordinal")
    decisions = decisions or {}
    if set(decisions) - {v['entry_id'] for _, v in entries}:
        raise ValueError("decision outside selected scope")
    scope = [(ordinal, v['entry_id'], sha256_bytes(canonical_json(v))) for ordinal, v in entries]
    selection_hash = sha256_bytes(canonical_json(scope))
    identity = sha256_bytes(canonical_json({"snapshot_id": snapshot_id, "scope": scope,
        "versions": versions, "limits": limits, "decisions": decisions, "pipeline": PIPELINE,
        "preparation_version": PREPARATION_VERSION}))
    # Concurrent prepare calls for the same identity serialize before lookup.
    connection.execute("SELECT pg_advisory_xact_lock(?)", (int(identity[:15], 16),))
    existing = connection.execute("SELECT id FROM run WHERE run_identity_sha256=?", (identity,)).fetchone()
    if existing:
        run_id = int(existing[0])
        count = connection.execute("SELECT COUNT(*) FROM wadoku_projection WHERE run_id=?", (run_id,)).fetchone()[0]
        if count != len(entries):
            raise ValueError("incomplete saved preparation")
        return {"run_id": run_id, "created": False, "articles": count, "identity": identity}
    run_id = int(connection.execute(
        """INSERT INTO run(dictionary_snapshot_id,selection_sha256,extractor_version,prompt_sha256,
        review_prompt_sha256,terminology_sha256,limits_json,pipeline_version,run_identity_sha256)
        VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
        (snapshot_id, selection_hash, PIPELINE, versions['prompt'], sha256_bytes(b''),
         sha256_bytes(canonical_json(versions)), canonical_json(limits).decode(), PIPELINE, identity),
    ).fetchone()[0])
    unit_count = 0
    for ordinal, original in entries:
        expression, reading, sequence = canonical_identity(original)
        raw = canonical_json(original).decode()
        source_hash = sha256_bytes(raw.encode())
        structural_hash = sha256_bytes(canonical_json(original['tree']))
        connection.execute(
            """INSERT INTO article(snapshot_id,bank_number,entry_ordinal,expression,reading,sequence,
            raw_json,source_sha256,structural_fingerprint,selected) VALUES (?,1,?,?,?,?,?,?,?,1)
            ON CONFLICT(snapshot_id,bank_number,entry_ordinal) DO NOTHING""",
            (snapshot_id, ordinal, expression, reading, sequence, raw, source_hash, structural_hash),
        )
        article = connection.execute(
            "SELECT id,source_sha256 FROM article WHERE snapshot_id=? AND bank_number=1 AND entry_ordinal=?",
            (snapshot_id, ordinal),
        ).fetchone()
        if article['source_sha256'] != source_hash:
            raise ValueError("immutable source article differs")
        article_id = int(article['id'])
        connection.execute(
            "INSERT INTO run_article(run_id,article_id,structural_fingerprint,prepared_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (run_id, article_id, structural_hash),
        )
        derived = copy.deepcopy(original)
        overlay = decisions.get(sequence, {})
        allowed = {'article_group_decision', 'lookup_aliases', 'source_correction_audit', 'examples'}
        if set(overlay) - allowed:
            raise ValueError("unsupported decision overlay")
        derived.update({key: copy.deepcopy(val) for key, val in overlay.items() if key != 'examples'})
        projection = prepare_projection(derived, versions=versions, run_identity=identity,
                                        examples=overlay.get('examples', []), example_sources=example_sources)
        unit_count += store_projection(connection, run_id, article_id, projection)
    report = {"run_id": run_id, "created": True, "articles": len(entries), "units": unit_count, "identity": identity}
    audit(connection, "prepare_wadoku_projection", "run", run_id, report)
    return report


def prepare_projection(value: dict[str, Any], *, versions: dict[str, str],
                       run_identity: str, examples: list[dict[str, Any]] | None = None,
                       example_sources: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Freeze meaning context and scope globally unique DB IDs to this run."""
    from .wadoku_quality import source_identity
    examples = copy.deepcopy(examples or [])
    for example in examples:
        if example.get('selection_state') != 'accepted':
            continue
        child = (example_sources or {}).get(example['child_id'])
        if child is None or source_identity(child) != example['source_sha256']:
            raise ValueError('accepted example requires its unchanged full source context')
        example['child_source_context'] = copy.deepcopy(child['tree'])
    result = translation_projection(value, versions=versions, examples=examples)
    for example in examples or []:
        if example.get("selection_state") == "accepted":
            unit = example_translation_unit(example, example["decision"]["source_path"])
            unit['example_source_context'] = copy.deepcopy(example['child_source_context'])
            if versions['schema'] in {'rich-v5', 'rich-v6'}:
                from .wadoku_quality import lexical_contract, tree_paths
                nodes = {path: node for node, path in tree_paths(example['child_source_context'])}
                source_path = example['decision']['source_path']
                source_scope = max((p for p,n in nodes.items() if n['tag']=='sense' and source_path.startswith(p+'/')),
                                   key=len, default=None)
                unit.update(lexical_contract(nodes,source_scope,[source_path],unit['source_text']))
            if versions['schema'] == 'rich-v6':
                unit['task_type'] = 'example_translation'
            unit["projection_index"] = None
            result["units"].append(unit)
    for unit in result["units"]:
        unit["semantic_id"] = unit["unit_id"]
        unit["unit_id"] = "wd3-" + sha256_bytes(canonical_json([run_identity, unit["semantic_id"]]))[:40]
    result["pipeline"] = PIPELINE
    result['envelope_version'] = 'selected-examples-v2-source-order'
    return result


def projection_envelope(article: Any, units: list[Any], projection: dict[str, Any]) -> dict[str, Any]:
    """Use the frozen complete sense context, not an arbitrary +/- two blocks."""
    value = projection["render_value"]
    expression, reading, sequence = canonical_identity(value)
    indexed = {unit["unit_id"]: unit for unit in projection["units"]}
    if projection.get('envelope_version') == 'selected-examples-v2-source-order':
        source_order = {u['unit_id']: i for i, u in enumerate(projection['units'])}
        units = sorted(units, key=lambda row: source_order[row['id']])
    prepared = []
    for row in units:
        unit = copy.deepcopy(indexed[row["id"]])
        if (unit["source_sha256"], unit["role"], unit["source_text"]) != (
                row["source_sha256"], row["role"], row["source_text"]):
            raise ValueError("frozen projection differs from translation unit")
        unit["local_context"] = {"sense_path": unit.get("sense_path"),
                                 "sense_context": unit.pop("sense_context", None),
                                 "example_source_context": unit.pop("example_source_context", None),
                                 "member_paths": unit.get("member_paths", [])}
        if 'grammatical_scope' in unit:
            unit['local_context']['grammatical_scope'] = unit.pop('grammatical_scope')
        # The batcher may split only between complete meaning packets.
        unit["packet_id"] = unit.get("sense_path") or unit["semantic_id"]
        prepared.append(unit)
    context = copy.deepcopy(projection['context'])
    if context.get('versions', {}).get('schema') in {'rich-v5', 'rich-v6'}:
        # Translation confidence must not mirror unrelated lookup/owner uncertainty.
        context.pop('article_group_decision', None)
        context.pop('lookup_aliases', None)
    if projection.get('envelope_version') in {'selected-examples-v1', 'selected-examples-v2-source-order'}:
        examples = context.get('examples', [])
        context['example_selection_states'] = [
            {'decision_id': e.get('decision_id'), 'state': e.get('selection_state')}
            for e in examples]
        context['examples'] = [e for e in examples if e.get('selection_state') == 'accepted']
        for example in context['examples']:
            # Each translated example still has its complete child tree in local_context.
            example.pop('child_source_context', None)
        if isinstance(context.get('article_group_decision'), dict):
            context['article_group_decision'].pop('examples', None)
    return {"article_id": f"a-{article['id']}", "source_sha256": article["source_sha256"],
            "term": expression, "reading": reading, "sequence": sequence,
            "read_only_context": {"dictionary": "Wadoku", "pipeline": PIPELINE,
                                  **context},
            "units": prepared}


def store_projection(connection: Any, run_id: int, article_id: int,
                     projection: dict[str, Any]) -> int:
    """Save the projection and its units in the caller's preparation transaction."""
    connection.execute(
        """INSERT INTO wadoku_projection(run_id,article_id,context_sha256,projection_json)
        VALUES (?,?,?,?)""",
        (run_id, article_id, projection["context_sha256"], canonical_json(projection).decode()),
    )
    for ordinal, unit in enumerate(projection["units"]):
        connection.execute(
            """INSERT INTO translation_unit(id,run_id,article_id,json_pointer,role,source_text,
            source_sha256,protected_tokens_json,byte_count,status) VALUES (?,?,?,?,?,?,?,?,?,'ready')""",
            (unit["unit_id"], run_id, article_id, f"/projection/units/{ordinal}", unit["role"],
             unit["source_text"], unit["source_sha256"], canonical_json(unit["protected_tokens"]).decode(),
             len(unit["source_text"].encode())),
        )
    return len(projection["units"])


def load_projection(connection: Any, run_id: int, article_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT projection_json FROM wadoku_projection WHERE run_id=? AND article_id=?",
        (run_id, article_id),
    ).fetchone()
    if row is None:
        raise ValueError("missing frozen Wadoku projection")
    return json.loads(row["projection_json"])


def localized_projection(projection: dict[str, Any], targets: dict[str, Any]) -> tuple[dict[str, Any], dict[int, Any]]:
    """Require exact coverage and keep example translations out of definitions."""
    units = projection["units"]
    if set(targets) != {unit["unit_id"] for unit in units}:
        raise ValueError("projection target coverage differs")
    value = copy.deepcopy(projection["render_value"])
    blocks = {}
    examples = []
    for unit in units:
        target = targets[unit["unit_id"]]
        if unit["role"] == "glossary_set":
            if not isinstance(target, list) or not target or not all(isinstance(s, str) and s for s in target):
                raise ValueError("glossary_set target must be a nonempty string array")
        elif not isinstance(target, str) or not target:
            raise ValueError("scalar target must be a nonempty string")
        if unit["projection_index"] is None:
            examples.append({**copy.deepcopy(unit), "translation": target})
        else:
            blocks[unit["projection_index"]] = target
    value["accepted_examples"] = examples
    return value, blocks


def localized_run(connection: Any, run_id: int, *, allow_unreviewed: bool = False) -> list[tuple[dict[str, Any], dict[int, Any]]]:
    """Load complete coverage; require acceptance except for explicit diagnostics.

    This validates translation coverage only. Ownership and example decisions
    still need their separate semantic checks before a release export.
    """
    run = connection.execute('SELECT pipeline_version FROM run WHERE id=?', (run_id,)).fetchone()
    if not run or run[0] != PIPELINE:
        raise ValueError('rich Wadoku run required')
    rows = connection.execute('''SELECT ra.article_id,p.projection_json,u.id AS unit_id,
        u.role,t.target_text,t.accepted
        FROM run_article ra
        LEFT JOIN wadoku_projection p ON p.run_id=ra.run_id AND p.article_id=ra.article_id
        LEFT JOIN translation_unit u ON u.run_id=ra.run_id AND u.article_id=ra.article_id
        LEFT JOIN translation t ON t.id=(SELECT MAX(latest.id) FROM translation latest
            WHERE latest.run_id=ra.run_id AND latest.unit_id=u.id)
        WHERE ra.run_id=? ORDER BY ra.article_id,u.id''', (run_id,)).fetchall()
    if not rows:
        raise ValueError('cannot assemble an empty run')
    grouped = {}
    for row in rows:
        if not row['projection_json']:
            raise ValueError(f"missing projection for article {row['article_id']}")
        if row['article_id'] not in grouped:
            grouped[row['article_id']] = (json.loads(row['projection_json']), {})
        if row['unit_id'] is None:
            continue
        if row['target_text'] is None or (not allow_unreviewed and not row['accepted']):
            raise ValueError(f"latest translation missing or unaccepted: {row['unit_id']}")
        grouped[row['article_id']][1][row['unit_id']] = (
            json.loads(row['target_text']) if row['role'] == 'glossary_set' else row['target_text'])
    return [localized_projection(projection, targets) for projection, targets in grouped.values()]
