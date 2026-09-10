from __future__ import annotations

from .database import ConnectionLike, RowLike

import json
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .batch import _article_envelope
from .db import audit
from .util import CYRILLIC_RE, TAG_RE, atomic_write, canonical_json, sha256_bytes
from .validate_response import (_plain_text_issues, allows_japanese_grammar_label, target_storage,
                                wadoku_glossary_issues, wadoku_target_issues)


def _review_manifest(batch_id: str, articles: list[dict[str, Any]]) -> tuple[dict[str, Any], bytes]:
    wadoku = any(article.get('read_only_context', {}).get('pipeline') == 'wadoku-xml-v3'
                 for article in articles)
    lexicographer = any(
        "preservation_inventory" in article.get("read_only_context", {})
        or any(unit["role"] == "glossary_set" for unit in article["units"])
        for article in articles
    )
    payload = {"schema_version": 2 if lexicographer or wadoku else 1, "batch_id": batch_id, "manifest_sha256": "", "target_language": "ru", "articles": articles}
    if wadoku:
        payload['pipeline'] = 'wadoku-xml-v3'
    elif lexicographer:
        payload["pipeline"] = "lexicographer-v2"
    digest = sha256_bytes(canonical_json(payload))
    payload["manifest_sha256"] = digest
    return payload, canonical_json(payload)


def _split_review_envelope(
    envelope: dict[str, Any], max_bytes: int, max_units: int,
) -> list[dict[str, Any]]:
    """Split one article across review batches without dropping its context."""
    segments: list[dict[str, Any]] = []
    current_units: list[dict[str, Any]] = []
    for unit in envelope["units"]:
        candidate_units = current_units + [unit]
        candidate = {**envelope, "units": candidate_units}
        _, data = _review_manifest("rb-" + "0" * 24, [candidate])
        if current_units and (len(data) > max_bytes or len(candidate_units) > max_units):
            segments.append({**envelope, "units": current_units})
            current_units = [unit]
            candidate = {**envelope, "units": current_units}
            _, data = _review_manifest("rb-" + "0" * 24, [candidate])
        else:
            current_units = candidate_units
        if len(data) > max_bytes or len(current_units) > max_units:
            raise ValueError(f"review unit {unit['unit_id']} exceeds review limits")
    if current_units:
        segments.append({**envelope, "units": current_units})
    return segments


def make_review_batches(
    connection: ConnectionLike, run_id: int, inbox: Path,
    max_articles: int = 6, max_bytes: int = 49152, max_units: int = 120,
    review_prompt_sha256: str | None = None,
    recheck: bool = False,
    unresolved_only: bool = False,
    article_ids: list[int] | None = None,
) -> dict[str, int]:
    if unresolved_only:
        recheck = True
    if recheck:
        run = connection.execute('SELECT pipeline_version FROM run WHERE id=?', (run_id,)).fetchone()
        if not run or run[0] != 'wadoku-xml-v3' or not review_prompt_sha256:
            raise ValueError('recheck requires rich Wadoku and an explicit review prompt')
    selection = ('t.id=(SELECT MAX(t2.id) FROM translation t2 WHERE t2.run_id=t.run_id AND t2.unit_id=t.unit_id)'
                 if recheck else 't.accepted=0 AND NOT EXISTS (SELECT 1 FROM review r WHERE r.translation_id=t.id)')
    if unresolved_only:
        selection += " AND t.accepted=0 AND EXISTS (SELECT 1 FROM review r WHERE r.translation_id=t.id AND r.decision='needs_adjudication')"
        selection += " AND NOT EXISTS (SELECT 1 FROM review seen JOIN attempt done ON done.id=seen.attempt_id WHERE seen.translation_id=t.id AND done.prompt_sha256=?)"
    parameters = [run_id, review_prompt_sha256] if unresolved_only else [run_id]
    if article_ids is not None:
        if (not recheck or not 1 <= len(article_ids) <= 10
                or len(set(article_ids)) != len(article_ids)
                or any(type(i) is not int or i <= 0 for i in article_ids)):
            raise ValueError('article subset requires explicit recheck and 1–10 distinct article IDs')
        marks = ','.join('?' for _ in article_ids)
        present = {r[0] for r in connection.execute(
            f'SELECT article_id FROM run_article WHERE run_id=? AND article_id IN ({marks})',
            (run_id, *article_ids))}
        if present != set(article_ids):
            raise ValueError('review article subset is outside the run')
        selection += f' AND tu.article_id IN ({marks})'
        parameters.extend(article_ids)
    grouped: dict[int, list[RowLike]] = defaultdict(list)
    for row in connection.execute(
        f"""SELECT tu.*,t.id translation_id,t.target_text,t.confidence,t.review_reason
        FROM translation t JOIN translation_unit tu ON tu.id=t.unit_id
        WHERE t.run_id=? AND {selection}
        ORDER BY tu.article_id,tu.json_pointer""", tuple(parameters)
    ):
        grouped[row["article_id"]].append(row)
    article_rows = {row["id"]: row for row in connection.execute("SELECT * FROM article WHERE selected=1")}
    envelopes = []
    for article_id, units in sorted(grouped.items()):
        base = _article_envelope(connection, article_rows[article_id], units)
        if base.get('read_only_context', {}).get('pipeline') == 'wadoku-xml-v3':
            # Review requests are new artifacts even when the translation run
            # froze an older envelope order (0, 1, 10, 11, 2, ...).
            from .wadoku_pipeline import load_projection
            projection = load_projection(connection, run_id, article_id)
            order = {u['unit_id']: i for i, u in enumerate(projection['units'])}
            base['units'].sort(key=lambda u: order[u['unit_id']])
        translations = {row["id"]: row for row in units}
        for unit in base["units"]:
            candidate = translations[unit["unit_id"]]
            unit["candidate_target"] = json.loads(candidate["target_text"]) if unit["role"] == "glossary_set" else candidate["target_text"]
            unit["candidate_confidence"] = candidate["confidence"]
            unit["candidate_review_reason"] = candidate["review_reason"]
            if unresolved_only:
                unit['prior_review_reasons'] = [r[0] for r in connection.execute(
                    "SELECT reason FROM review WHERE translation_id=? AND decision='needs_adjudication' ORDER BY id",
                    (candidate['translation_id'],))]
        envelopes.extend(_split_review_envelope(base, max_bytes, max_units))

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for envelope in envelopes:
        candidate = current + [envelope]
        _, data = _review_manifest("rb-" + "0" * 24, candidate)
        units = sum(len(item["units"]) for item in candidate)
        if current and (len(candidate) > max_articles or len(data) > max_bytes or units > max_units):
            groups.append(current)
            current = [envelope]
        else:
            current = candidate
    if current:
        groups.append(current)

    created = 0
    for group in groups:
        identity = {"run_id": run_id, "candidates": [
            [unit["unit_id"], sha256_bytes(
                canonical_json(unit["candidate_target"])
                if isinstance(unit["candidate_target"], list)
                else unit["candidate_target"].encode()
            )]
            for article in group for unit in article["units"]
        ]}
        if review_prompt_sha256 is not None:
            identity['review_prompt_sha256'] = review_prompt_sha256
        if recheck:
            identity['recheck'] = True
        if article_ids is not None:
            identity['article_subset'] = sorted(article_ids)
        if unresolved_only:
            identity['unresolved_only'] = True
            identity['prior_review_reasons'] = [u['prior_review_reasons'] for a in group for u in a['units']]
        batch_id = f"rb-{sha256_bytes(canonical_json(identity))[:24]}"
        manifest, data = _review_manifest(batch_id, group)
        if review_prompt_sha256 is not None:
            manifest['review_prompt_sha256'] = review_prompt_sha256
            if recheck:
                manifest['recheck'] = True
            manifest['manifest_sha256'] = ''
            manifest['manifest_sha256'] = sha256_bytes(canonical_json(manifest))
            data = canonical_json(manifest)
        existing = connection.execute('SELECT manifest_sha256 FROM batch WHERE id=?', (batch_id,)).fetchone()
        if existing:
            if existing[0] != manifest['manifest_sha256']:
                raise ValueError('review batch identity conflicts with frozen manifest')
            continue
        path = inbox / f"{batch_id}.json"
        atomic_write(path, data + b"\n")
        units = [unit for article in group for unit in article["units"]]
        connection.execute(
            """INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,article_count,unit_count,manifest_path)
            VALUES (?,?,?,?,?,?,?,?)""",
            (batch_id, run_id, "review", manifest["manifest_sha256"], len(data), len(group), len(units), str(path)),
        )
        connection.executemany(
            "INSERT INTO batch_item(batch_id,unit_id,ordinal) VALUES (?,?,?)",
            ((batch_id, unit["unit_id"], index) for index, unit in enumerate(units)),
        )
        audit(connection, "create", "review_batch", batch_id, {"units": len(units)})
        created += 1
    return {"review_batches_created": created, "units": sum(len(group) for group in grouped.values())}


def ingest_review(connection: ConnectionLike, path: Path) -> dict[str, int]:
    attempt = connection.execute(
        """SELECT a.*,b.run_id,b.manifest_sha256,b.kind FROM attempt a JOIN batch b ON b.id=a.batch_id
        WHERE a.response_path=?""", (str(path),)
    ).fetchone()
    if attempt is None or attempt["kind"] != "review":
        raise ValueError(f"no review attempt expects {path}")
    lock = " FOR UPDATE" if getattr(connection, "backend", "sqlite") == "postgresql" else ""
    owned_batch = connection.execute(
        "SELECT state,lease_token FROM batch WHERE id=?" + lock, (attempt["batch_id"],),
    ).fetchone()
    if (
        attempt["outcome"] != "claimed" or owned_batch is None
        or owned_batch["state"] != "leased"
        or not attempt["lease_token"] or owned_batch["lease_token"] != attempt["lease_token"]
    ):
        raise ValueError(f"stale review attempt no longer owns batch lease: {attempt['id']}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "batch_id", "manifest_sha256", "reviews"}:
        raise ValueError("unexpected review response fields")
    run = connection.execute("SELECT pipeline_version FROM run WHERE id=?", (attempt["run_id"],)).fetchone()
    wadoku = run['pipeline_version'] == 'wadoku-xml-v3'
    expected_schema = 2 if run["pipeline_version"] in {"lexicographer-v2", "wadoku-xml-v3"} else 1
    if payload.get("schema_version") != expected_schema or payload.get("batch_id") != attempt["batch_id"] or payload.get("manifest_sha256") != attempt["manifest_sha256"]:
        raise ValueError("review envelope mismatch")
    expected = connection.execute(
        """SELECT tu.*,t.id translation_id,source_attempt.worker_id source_worker_id
        FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
        JOIN translation t ON t.id=(
          SELECT t2.id FROM translation t2
          WHERE t2.unit_id=tu.id AND t2.run_id=?
          ORDER BY t2.accepted DESC,t2.id DESC LIMIT 1
        )
        JOIN attempt source_attempt ON source_attempt.id=t.attempt_id
        WHERE bi.batch_id=? ORDER BY bi.ordinal""",
        (attempt["run_id"], attempt["batch_id"]),
    ).fetchall()
    reviews = payload.get("reviews")
    if not isinstance(reviews, list) or [item.get("unit_id") for item in reviews] != [row["id"] for row in expected]:
        raise ValueError("review unit order or set mismatch")
    frozen = json.loads(Path(attempt['request_path']).read_text(encoding='utf-8'))
    if wadoku and frozen.get('review_prompt_sha256') is not None and frozen['review_prompt_sha256'] != attempt['prompt_sha256']:
        raise ValueError('review prompt provenance mismatch')
    candidates = {unit['unit_id']: unit for article in frozen['articles'] for unit in article['units']}
    accepted = adjudication = already_reviewed = 0
    for source, item in zip(expected, reviews):
        if set(item) != {"unit_id", "source_sha256", "decision", "replacement_target", "reason"}:
            raise ValueError(f"unexpected review fields for {source['id']}")
        if source["source_worker_id"] == attempt["worker_id"]:
            raise ValueError(f"reviewer also produced translation for {source['id']}")
        if item.get("source_sha256") != source["source_sha256"]:
            raise ValueError(f"review source hash mismatch for {source['id']}")
        current = connection.execute('SELECT target_text FROM translation WHERE id=?', (source['translation_id'],)).fetchone()
        if current[0] != target_storage(source['role'], candidates[source['id']]['candidate_target']):
            raise ValueError(f"review candidate changed for {source['id']}")
        decision = item.get("decision")
        replacement = item.get("replacement_target")
        if decision not in {"accept", "replace", "needs_adjudication"}:
            raise ValueError(f"invalid review decision for {source['id']}")
        if wadoku and (not isinstance(item.get('reason'), str) or not item['reason'].strip()):
            raise ValueError(f"nonempty Russian reason string required for {source['id']}; null is invalid even for accept")
        if wadoku and decision != 'replace' and replacement is not None:
            raise ValueError(f"replacement_target must be null for decision={decision}, unit={source['id']}")
        stored_replacement = None
        if decision == "replace":
            try:
                stored_replacement = target_storage(source["role"], replacement)
            except ValueError as error:
                raise ValueError(f"invalid review replacement for {source['id']}: {error}") from error
            values = replacement if source["role"] == "glossary_set" else [replacement]
            if wadoku:
                from .validate_response import wadoku_scientific_source
                validator = wadoku_glossary_issues if source['role'] == 'glossary_set' else wadoku_target_issues
                options = {} if source['role'] == 'glossary_set' else {
                    'scientific_source': wadoku_scientific_source(candidates[source['id']])}
                replacement_issues = validator(source['source_text'], replacement,
                             json.loads(source['protected_tokens_json']), source['id'], **options)
                if replacement_issues:
                    raise ValueError(f"invalid review replacement for {source['id']}: "
                                     + json.dumps(replacement_issues, ensure_ascii=False))
            elif not 1 <= len(values) <= 12 or any(
                _plain_text_issues(
                    value, [],
                    allow_no_cyrillic=allows_japanese_grammar_label(source["role"], source["source_text"]),
                )
                for value in values
            ):
                raise ValueError(f"invalid review replacement for {source['id']}")
        if decision == "replace":
            for token in json.loads(source["protected_tokens_json"]):
                if token not in stored_replacement:
                    raise ValueError(f"review replacement lost protected token for {source['id']}")
        # Review batches can overlap when a previously prepared pilot batch is
        # completed after the full review pass is materialized.  Validate the
        # stale response item, but never let it overwrite an already accepted
        # editorial decision.
        if source["status"] == "reviewed" and not (wadoku and frozen.get('recheck')):
            already_reviewed += 1
            continue
        connection.execute(
            "INSERT INTO review(translation_id,attempt_id,decision,replacement_target,reason) VALUES (?,?,?,?,?)",
            (source["translation_id"], attempt["id"], decision, stored_replacement, item.get("reason")),
        )
        if decision in {"accept", "replace"}:
            if decision == "replace":
                if wadoku and frozen.get('recheck'):
                    connection.execute('UPDATE translation SET accepted=0 WHERE run_id=? AND unit_id=?',
                                       (attempt['run_id'], source['id']))
                connection.execute(
                    """INSERT INTO translation(run_id,unit_id,attempt_id,target_text,confidence,review_reason,target_sha256,accepted)
                    VALUES (?,?,?,?,?,?,?,1)""",
                    (attempt["run_id"], source["id"], attempt["id"], stored_replacement, "high", item.get("reason"), sha256_bytes(stored_replacement.encode())),
                )
            else:
                connection.execute("UPDATE translation SET accepted=1 WHERE id=?", (source["translation_id"],))
            connection.execute("UPDATE translation_unit SET status='reviewed' WHERE id=?", (source["id"],))
            if wadoku and frozen.get('recheck'):
                connection.execute("""UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,
                    waiver_reason=? WHERE run_id=? AND unit_id=? AND code='needs_adjudication'
                    AND resolved_at IS NULL""",
                    ('resolved by contextual review attempt ' + attempt['id'], attempt['run_id'], source['id']))
            accepted += 1
        else:
            if wadoku and frozen.get('recheck'):
                connection.execute('UPDATE translation SET accepted=0 WHERE run_id=? AND unit_id=?',
                                   (attempt['run_id'], source['id']))
                connection.execute("UPDATE translation_unit SET status='translated' WHERE id=?", (source['id'],))
            connection.execute(
                """INSERT INTO validation_issue(run_id,unit_id,attempt_id,validator,severity,code,details_json)
                VALUES (?,?,?,'review-v1','error','needs_adjudication',?)""",
                (attempt["run_id"], source["id"], attempt["id"], json.dumps({"reason": item.get("reason")}, ensure_ascii=False)),
            )
            adjudication += 1
    accepted_attempt = connection.execute(
        """UPDATE attempt SET outcome='accepted',completed_at=CURRENT_TIMESTAMP
        WHERE id=? AND outcome='claimed' AND lease_token=?""",
        (attempt["id"], attempt["lease_token"]),
    ).rowcount
    completed_batch = connection.execute(
        """UPDATE batch SET state=? WHERE id=? AND state='leased' AND lease_token=?""",
        ("complete" if not adjudication else "blocked", attempt["batch_id"], attempt["lease_token"]),
    ).rowcount
    if accepted_attempt != 1 or completed_batch != 1:
        raise ValueError(f"review lease ownership changed during ingestion: {attempt['id']}")
    connection.execute(
        """UPDATE batch SET state='complete' WHERE run_id=? AND kind='translation'
        AND state='deterministic_validated' AND NOT EXISTS (
          SELECT 1 FROM batch_item bi WHERE bi.batch_id=batch.id AND NOT EXISTS (
            SELECT 1 FROM translation t WHERE t.unit_id=bi.unit_id AND t.run_id=batch.run_id AND t.accepted=1
          )
        )""", (attempt["run_id"],)
    )
    audit(connection, "ingest", "review_attempt", attempt["id"], {
        "accepted": accepted, "already_reviewed": already_reviewed, "adjudication": adjudication,
    })
    return {"accepted": accepted, "already_reviewed": already_reviewed, "needs_adjudication": adjudication}


def apply_adjudication(connection: ConnectionLike, path: Path, actor: str) -> dict[str, Any]:
    """Resolve one review conflict while retaining the original review record."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"batch_id", "unit_id", "decision", "target_text", "reason"}
    if set(payload) != required:
        raise ValueError("unexpected adjudication fields")
    if payload["decision"] not in {"accept_candidate", "replace"}:
        raise ValueError("invalid adjudication decision")
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        raise ValueError("adjudication reason is required")
    source = connection.execute(
        """SELECT r.id review_id,r.translation_id,r.attempt_id review_attempt_id,
        t.run_id,t.unit_id,t.target_text,tu.role,tu.source_sha256,tu.protected_tokens_json,
        b.manifest_path,run.review_prompt_sha256
        FROM review r JOIN attempt a ON a.id=r.attempt_id
        JOIN batch b ON b.id=a.batch_id JOIN translation t ON t.id=r.translation_id
        JOIN translation_unit tu ON tu.id=t.unit_id JOIN run ON run.id=t.run_id
        WHERE b.id=? AND t.unit_id=? AND r.decision='needs_adjudication'""",
        (payload["batch_id"], payload["unit_id"]),
    ).fetchone()
    if source is None:
        raise ValueError("no matching unresolved review conflict")
    target = payload["target_text"]
    if payload["decision"] == "accept_candidate" and source["role"] == "glossary_set" and isinstance(target, list):
        target = target_storage(source["role"], target)
    elif payload["decision"] == "replace":
        target = target_storage(source["role"], target)
    if not isinstance(target, str) or (source["role"] != "glossary_set" and (not CYRILLIC_RE.search(target) or TAG_RE.search(target))):
        raise ValueError("invalid adjudication target")
    if payload["decision"] == "accept_candidate" and target != source["target_text"]:
        raise ValueError("accepted target differs from candidate")
    for token in json.loads(source["protected_tokens_json"]):
        if token not in target:
            raise ValueError(f"adjudication lost protected token {token}")
    existing = connection.execute("SELECT id FROM attempt WHERE response_path=?", (str(path),)).fetchone()
    if existing:
        return {"adjudicated": 0, "attempt_id": existing["id"], "already_applied": True}
    attempt_id = f"adj-{uuid.uuid4().hex}"
    connection.execute(
        """INSERT INTO attempt(id,batch_id,worker_id,model,prompt_sha256,request_path,response_path,outcome,completed_at)
        VALUES (?,?,?,?,?,?,?,'accepted',CURRENT_TIMESTAMP)""",
        (attempt_id, payload["batch_id"], actor, "gpt-5.6-terra", source["review_prompt_sha256"],
         source["manifest_path"], str(path)),
    )
    decision = "accept" if payload["decision"] == "accept_candidate" else "replace"
    connection.execute(
        "INSERT INTO review(translation_id,attempt_id,decision,replacement_target,reason) VALUES (?,?,?,?,?)",
        (source["translation_id"], attempt_id, decision, None if decision == "accept" else target, payload["reason"]),
    )
    if decision == "accept":
        connection.execute("UPDATE translation SET accepted=1 WHERE id=?", (source["translation_id"],))
    else:
        connection.execute(
            """INSERT INTO translation(run_id,unit_id,attempt_id,target_text,confidence,review_reason,target_sha256,accepted)
            VALUES (?,?,?,?,?,?,?,1)""",
            (source["run_id"], source["unit_id"], attempt_id, target, "low", payload["reason"], sha256_bytes(target.encode())),
        )
    connection.execute("UPDATE translation_unit SET status='reviewed' WHERE id=?", (source["unit_id"],))
    connection.execute(
        """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,waiver_reason=?
        WHERE attempt_id=? AND unit_id=? AND code='needs_adjudication' AND resolved_at IS NULL""",
        (f"adjudicated by {actor}: {payload['reason']}", source["review_attempt_id"], source["unit_id"]),
    )
    remaining = connection.execute(
        """SELECT COUNT(*) FROM validation_issue vi JOIN attempt a ON a.id=vi.attempt_id
        WHERE a.batch_id=? AND vi.code='needs_adjudication' AND vi.resolved_at IS NULL""",
        (payload["batch_id"],),
    ).fetchone()[0]
    if not remaining:
        connection.execute("UPDATE batch SET state='complete' WHERE id=?", (payload["batch_id"],))
    connection.execute(
        """UPDATE batch SET state='complete' WHERE run_id=? AND kind='translation'
        AND state='deterministic_validated' AND NOT EXISTS (
          SELECT 1 FROM batch_item bi WHERE bi.batch_id=batch.id AND NOT EXISTS (
            SELECT 1 FROM translation t WHERE t.unit_id=bi.unit_id AND t.run_id=batch.run_id AND t.accepted=1
          )
        )""",
        (source["run_id"],),
    )
    audit(connection, "adjudicate", "review", source["review_id"], {
        "actor": actor, "attempt_id": attempt_id, "decision": decision, "reason": payload["reason"],
    })
    return {"adjudicated": 1, "attempt_id": attempt_id, "decision": decision}
