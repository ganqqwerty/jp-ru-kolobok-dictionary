from __future__ import annotations

from .database import ConnectionLike, RowLike

import datetime as dt
import json
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .db import audit, transaction
from .dojg import DOJG_ROLE, dojg_segment_context
from .extract_units import (
    glossary_evidence, kanjidic_context, lexicographic_context, protected_tokens, semantic_context,
)
from .prep_metrics import PrepMetrics
from .util import atomic_write, canonical_json, json_pointer_get, sha256_bytes


TagCatalog = Mapping[tuple[str, str], Mapping[str, str]]


def _approved_tag_catalog(connection: ConnectionLike, snapshot_id: int) -> dict[tuple[str, str], dict[str, str]]:
    rows = connection.execute(
        """SELECT category,code,label_ru,description_ru FROM jitendex_tag
        WHERE snapshot_id=? AND source_kind='embedded_tooltip'
          AND translation_source='approved_workbook'
        ORDER BY id""",
        (snapshot_id,),
    ).fetchall()
    catalog: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["category"], row["code"])
        if key in catalog:
            raise ValueError(f"duplicate approved Jitendex tag terminology for {key}")
        if not row["label_ru"] or not row["description_ru"]:
            raise ValueError(f"incomplete approved Jitendex tag terminology for {key}")
        catalog[key] = {"label_ru": row["label_ru"], "description_ru": row["description_ru"]}
    return catalog


def _required_tag_terminology(source: Any, pointer: str, catalog: TagCatalog) -> dict[str, str] | None:
    parent_pointer, separator, field = pointer.rpartition("/")
    if not separator or field not in {"content", "title"}:
        return None
    parent = json_pointer_get(source, parent_pointer)
    if not isinstance(parent, dict):
        return None
    data = parent.get("data")
    if not isinstance(data, dict) or data.get("class") != "tag":
        return None
    category = data.get("content")
    code = data.get("code", "")
    if not isinstance(category, str) or not isinstance(code, str):
        raise ValueError(f"invalid Jitendex tag metadata at {parent_pointer}")
    approved = catalog.get((category, code))
    if approved is None:
        # Unknown tags must not stop an intermediate JPDB batch. The final
        # cumulative run receives a deterministic catalog-driven cleanup
        # before export, after the approved catalog has been completed.
        return None
    return {
        "source": "approved_jitendex_tag_catalog",
        "category": category,
        "code": code,
        "target_text": approved["label_ru" if field == "content" else "description_ru"],
    }


def _evidence(connection: ConnectionLike, article_id: int) -> list[dict[str, str]]:
    rows = connection.execute(
        """SELECT DISTINCT kn.id,kn.word,kn.reading,kn.meaning_en,kn.sentence_ja,kn.sentence_en
        FROM selection_candidate sc JOIN kaishi_note kn ON kn.id=sc.note_id
        JOIN selection_decision sd ON sd.note_id=sc.note_id AND sd.sequence=sc.sequence
        WHERE sc.article_id=? AND sd.decision='included' AND sd.review_status='accepted' ORDER BY kn.id""",
        (article_id,),
    ).fetchall()
    return [
        {key: value for key, value in dict(row).items() if key != "id"}
        for row in rows
    ]


def _article_envelope(
    connection: ConnectionLike, article: RowLike, units: list[RowLike],
    tag_catalog: TagCatalog | None = None,
    evidence: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    source = json.loads(article["raw_json"])
    if units and str(units[0]["json_pointer"]).startswith("/projection/units/"):
        from .wadoku_pipeline import load_projection, projection_envelope
        return projection_envelope(article, units, load_projection(connection, units[0]["run_id"], article["id"]))
    if (
        isinstance(source, dict) and source.get("schema_version") == 1
        and source.get("namespace") == "http://www.wadoku.de/xml/entry"
    ):
        blocks = source.get("blocks", [])
        grammar: list[str] = []

        def collect_grammar(node: dict[str, Any], inside: bool = False) -> None:
            active = inside or node.get("tag") == "gramGrp"
            if inside and node.get("tag") != "gramGrp":
                grammar.append(str(node.get("tag")))
            for child in node.get("children", []):
                collect_grammar(child, active)

        collect_grammar(source["tree"])
        prepared_units = []
        for unit in units:
            index = int(str(unit["json_pointer"]).removeprefix("/blocks/"))
            block = blocks[index]
            nearby = [item["source_text"] for item in blocks[max(0, index - 2):index + 3]]
            prepared_units.append({
                "unit_id": unit["id"], "source_sha256": unit["source_sha256"],
                "role": unit["role"], "source_text": unit["source_text"],
                "protected_tokens": json.loads(unit["protected_tokens_json"]),
                "local_context": {
                    "unit_role": block["role"], "xml_path": block["xml_path"],
                    "sense_path": block["sense_path"], "nearby_source_blocks": nearby,
                },
            })
        return {
            "article_id": f"a-{article['id']}", "source_sha256": article["source_sha256"],
            "term": article["expression"], "reading": article["reading"],
            "sequence": article["sequence"],
            "read_only_context": {
                "dictionary": "Wadoku", "entry_id": source["entry_id"],
                "expression": article["expression"], "reading": article["reading"],
                "entry_grammar": list(dict.fromkeys(grammar)),
                "preservation_rule": "Protected tokens and XML structure are immutable.",
            },
            "units": prepared_units,
        }
    kanjidic = any(unit["role"] == "glossary_set" and unit["json_pointer"] == "/4" for unit in units)
    glossary_index = 4 if kanjidic else 5
    plain_glossary = (
        isinstance(source[glossary_index], list)
        and all(isinstance(item, str) for item in source[glossary_index])
    )
    lexicographer = any(unit["role"] == "glossary_set" for unit in units)
    dojg = any(unit["role"] == DOJG_ROLE for unit in units)
    prepared_units = []
    for unit in units:
        preserved = list(dict.fromkeys(
            json.loads(unit["protected_tokens_json"])
            if unit["role"] == DOJG_ROLE else [
                *json.loads(unit["protected_tokens_json"]),
                *protected_tokens(unit["role"], unit["source_text"]),
            ]
        ))
        prepared = {
            "unit_id": unit["id"], "source_sha256": unit["source_sha256"], "role": unit["role"],
            "protected_tokens": preserved,
            "local_context": (
                dojg_segment_context(source, unit["json_pointer"])
                if unit["role"] == DOJG_ROLE else unit["role"]
            ),
        }
        if unit["role"] == "glossary_set":
            evidence_key = "source_gloss_evidence" if plain_glossary else "english_gloss_evidence"
            prepared[evidence_key] = glossary_evidence(unit["source_text"])
            prepared["instruction"] = "author one variable-length list of Russian dictionary definitions"
        else:
            prepared["source_text"] = unit["source_text"]
        if tag_catalog is not None and unit["role"] != DOJG_ROLE:
            required = _required_tag_terminology(source, unit["json_pointer"], tag_catalog)
            if required is not None:
                prepared["required_terminology"] = required
        prepared_units.append(prepared)
    return {
        "article_id": f"a-{article['id']}", "source_sha256": article["source_sha256"],
        "term": article["expression"], "reading": article["reading"], "sequence": article["sequence"],
        "kaishi_evidence": _evidence(connection, article["id"]) if evidence is None else evidence,
        "read_only_context": (
            {
                "dictionary": "Dictionary of Japanese Grammar",
                "volume": source[7],
                "entry_heading": source[0],
                "preservation_rule": "Japanese placeholders and source layout are immutable.",
            }
            if dojg else (
                kanjidic_context(source) if kanjidic else (
                    lexicographic_context(source) if lexicographer else semantic_context(source)
                )
            )
        ),
        "units": prepared_units,
    }


def _uses_lexicographer(articles: list[dict[str, Any]]) -> bool:
    return any(
        "preservation_inventory" in article.get("read_only_context", {})
        or any(unit["role"] == "glossary_set" for unit in article["units"])
        for article in articles
    )


def _manifest_pipeline(articles: list[dict[str, Any]]) -> str:
    if any(article.get("read_only_context", {}).get("pipeline") == "wadoku-xml-v3" for article in articles):
        return "wadoku-xml-v3"
    if any(article.get("read_only_context", {}).get("dictionary") == "Wadoku" for article in articles):
        return "wadoku-xml-v2"
    if any(unit["role"] == DOJG_ROLE for article in articles for unit in article["units"]):
        return "dojg-v1"
    if any(article.get("read_only_context", {}).get("dictionary") == "KANJIDIC2" for article in articles):
        return "kanjidic-v1"
    return "lexicographer-v2" if _uses_lexicographer(articles) else "scalar-v1"


def _manifest_payload(
    batch_id: str, articles: list[dict[str, Any]], terminology: dict[str, str],
    manifest_sha256: str = "",
) -> dict[str, Any]:
    pipeline = _manifest_pipeline(articles)
    payload = {
        "schema_version": 2 if pipeline != "scalar-v1" else 1, "pipeline": pipeline,
        "batch_id": batch_id, "manifest_sha256": manifest_sha256,
        "target_language": "ru", "terminology": terminology, "articles": articles,
    }
    if pipeline == "scalar-v1":
        payload.pop("pipeline")
    return payload


def _manifest(batch_id: str, articles: list[dict[str, Any]], terminology: dict[str, str]) -> tuple[dict[str, Any], bytes]:
    payload = _manifest_payload(batch_id, articles, terminology)
    digest = sha256_bytes(canonical_json(payload))
    payload["manifest_sha256"] = digest
    return payload, canonical_json(payload)


def _manifest_size(
    articles: list[dict[str, Any]], terminology: dict[str, str], article_sizes: dict[int, int],
) -> int:
    empty = _manifest_payload(
        "b-" + "0" * 24, [], terminology, "0" * 64,
    )
    pipeline = _manifest_pipeline(articles)
    if pipeline != "scalar-v1":
        empty["schema_version"] = 2
        empty["pipeline"] = pipeline
    serialized_empty = len(canonical_json(empty))
    article_bytes = sum(article_sizes[id(article)] for article in articles)
    return serialized_empty + article_bytes + max(0, len(articles) - 1)


def _pack_envelopes(
    envelopes: list[dict[str, Any]], terminology: dict[str, str],
    soft_max_articles: int, soft_max_bytes: int, soft_max_units: int,
    singleton_threshold_bytes: int, hard_max_article_bytes: int, hard_max_article_units: int,
    split_oversized_articles: bool = False,
) -> list[list[dict[str, Any]]]:
    """Pack whole articles using soft group caps and hard article ceilings."""
    article_sizes = {id(envelope): len(canonical_json(envelope)) for envelope in envelopes}
    if split_oversized_articles:
        expanded: list[dict[str, Any]] = []
        for envelope in envelopes:
            original_bytes = _manifest_size([envelope], terminology, article_sizes)
            if (original_bytes <= hard_max_article_bytes
                    and len(envelope["units"]) <= hard_max_article_units):
                expanded.append(envelope)
                continue
            base = {**envelope, "units": []}
            current_units: list[dict[str, Any]] = []
            packets: dict[str, list[dict[str, Any]]] = {}
            for unit in envelope["units"]:
                packets.setdefault(unit.get("packet_id", unit["unit_id"]), []).append(unit)
            for packet in packets.values():
                unit = packet[0]
                candidate_units = [*current_units, *packet]
                candidate = {**base, "units": candidate_units}
                _, exact = _manifest("b-" + "0" * 24, [candidate], terminology)
                if (len(exact) > hard_max_article_bytes
                        or len(candidate_units) > hard_max_article_units):
                    if not current_units:
                        raise ValueError(
                            f"unit {unit['unit_id']} in article {envelope['article_id']} "
                            "exceeds a hard article limit"
                        )
                    expanded.append({**base, "units": current_units})
                    current_units = packet
                    _, exact = _manifest(
                        "b-" + "0" * 24, [{**base, "units": current_units}], terminology,
                    )
                    if len(exact) > hard_max_article_bytes or len(current_units) > hard_max_article_units:
                        raise ValueError(
                            f"unit {unit['unit_id']} in article {envelope['article_id']} "
                            "exceeds a hard article limit"
                        )
                else:
                    current_units = candidate_units
            if current_units:
                expanded.append({**base, "units": current_units})
        envelopes = expanded
        article_sizes = {
            id(envelope): (
                article_sizes[id(envelope)]
                if id(envelope) in article_sizes else len(canonical_json(envelope))
            )
            for envelope in envelopes
        }
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def measured(candidate: list[dict[str, Any]]) -> tuple[int, int]:
        return _manifest_size(candidate, terminology, article_sizes), sum(
            len(article["units"]) for article in candidate
        )

    for envelope in envelopes:
        article_bytes, article_units = measured([envelope])
        if article_bytes > hard_max_article_bytes or article_units > hard_max_article_units:
            raise ValueError(f"article {envelope['article_id']} exceeds a hard article limit")

        force_singleton = (
            article_bytes > singleton_threshold_bytes
            or article_bytes > soft_max_bytes
            or article_units > soft_max_units
        )
        candidate = current + [envelope]
        byte_count, unit_count = measured(candidate)
        if current and (
            force_singleton
            or len(candidate) > soft_max_articles
            or byte_count > soft_max_bytes
            or unit_count > soft_max_units
        ):
            batches.append(current)
            current = []
        if force_singleton:
            batches.append([envelope])
        else:
            current.append(envelope)
    if current:
        batches.append(current)
    for batch in batches:
        _, exact = _manifest("b-" + "0" * 24, batch, terminology)
        predicted = _manifest_size(batch, terminology, article_sizes)
        if len(exact) != predicted:
            raise RuntimeError("cached manifest size does not match exact serialization")
        if len(batch) > 1 and len(exact) > soft_max_bytes:
            raise RuntimeError("packed multi-article manifest exceeds the exact byte limit")
    return batches


def _ready_evidence(connection: ConnectionLike, run_id: int) -> dict[int, list[dict[str, str]]]:
    rows = connection.execute(
        """SELECT DISTINCT sc.article_id,kn.id,kn.word,kn.reading,kn.meaning_en,
        kn.sentence_ja,kn.sentence_en
        FROM translation_unit tu
        JOIN selection_candidate sc ON sc.article_id=tu.article_id
        JOIN kaishi_note kn ON kn.id=sc.note_id
        JOIN selection_decision sd ON sd.note_id=sc.note_id AND sd.sequence=sc.sequence
        WHERE tu.run_id=? AND tu.status='ready'
          AND sd.decision='included' AND sd.review_status='accepted'
          AND NOT EXISTS (SELECT 1 FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
                          WHERE bi.unit_id=tu.id AND b.kind='translation')
        ORDER BY sc.article_id,kn.id""",
        (run_id,),
    ).fetchall()
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["article_id"]].append({
            key: value for key, value in dict(row).items() if key not in {"article_id", "id"}
        })
    return grouped


def make_batches(
    connection: ConnectionLike, run_id: int, inbox: Path, terminology: dict[str, str],
    soft_max_articles: int, soft_max_bytes: int, soft_max_units: int, singleton_threshold_bytes: int,
    hard_max_article_bytes: int | None = None, hard_max_article_units: int | None = None,
    article_ids: set[int] | None = None,
) -> dict[str, Any]:
    metrics = PrepMetrics("make_batches")
    run = connection.execute(
        "SELECT jitendex_snapshot_id,dictionary_snapshot_id,pipeline_version FROM run WHERE id=?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"unknown run: {run_id}")
    wadoku = run["pipeline_version"] in {"wadoku-xml-v2", "wadoku-xml-v3"}
    snapshot_id = run["dictionary_snapshot_id"] or run["jitendex_snapshot_id"]
    tag_catalog = None if wadoku else _approved_tag_catalog(connection, snapshot_id)
    grouped: dict[int, list[RowLike]] = defaultdict(list)
    with metrics.phase("ready_unit_loading") as phase:
        for unit in connection.execute(
            """SELECT tu.* FROM translation_unit tu WHERE tu.run_id=? AND tu.status='ready'
            AND NOT EXISTS (SELECT 1 FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
                            WHERE bi.unit_id=tu.id AND b.kind='translation')
            ORDER BY tu.article_id,tu.json_pointer""", (run_id,)
        ):
            if article_ids is None or unit["article_id"] in article_ids:
                grouped[unit["article_id"]].append(unit)
        phase.update(input_rows=sum(map(len, grouped.values())), output_rows=len(grouped))
    with metrics.phase("article_loading", input_rows=len(grouped)) as phase:
        articles = {row["id"]: row for row in connection.execute(
            """SELECT a.* FROM article a WHERE a.selected=1 AND EXISTS (
            SELECT 1 FROM translation_unit tu WHERE tu.run_id=? AND tu.article_id=a.id
              AND tu.status='ready' AND NOT EXISTS (
                SELECT 1 FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
                WHERE bi.unit_id=tu.id AND b.kind='translation'))""", (run_id,),
        )}
        missing = set(grouped) - set(articles)
        if missing:
            raise RuntimeError(f"missing {len(missing)} ready-unit articles")
        evidence = {} if wadoku else _ready_evidence(connection, run_id)
        phase.update(output_rows=len(articles), evidence_rows=sum(map(len, evidence.values())))
    with metrics.phase("envelope_creation", input_rows=len(grouped)) as phase:
        envelopes = [
            _article_envelope(
                connection, articles[article_id], units, tag_catalog,
                evidence=evidence.get(article_id, []),
            )
            for article_id, units in sorted(grouped.items())
        ]
        phase.update(output_rows=len(envelopes))
    with metrics.phase("batch_packing", input_rows=len(envelopes)) as phase:
        batches = _pack_envelopes(
            envelopes, terminology,
            soft_max_articles, soft_max_bytes, soft_max_units, singleton_threshold_bytes,
            hard_max_article_bytes if hard_max_article_bytes is not None else soft_max_bytes,
            hard_max_article_units if hard_max_article_units is not None else soft_max_units,
            split_oversized_articles=wadoku,
        )
        phase.update(output_rows=len(batches))

    batch_rows: list[tuple[Any, ...]] = []
    item_rows: list[tuple[Any, ...]] = []
    audit_rows: list[tuple[Any, ...]] = []
    files_written = bytes_written = 0
    with metrics.phase("manifest_writing", input_rows=len(batches)) as phase:
        for article_group in batches:
            identity = {"run_id": run_id, "article_ids": [item["article_id"] for item in article_group],
                        "unit_ids": [unit["unit_id"] for item in article_group for unit in item["units"]]}
            batch_id = f"b-{sha256_bytes(canonical_json(identity))[:24]}"
            manifest, data = _manifest(batch_id, article_group, terminology)
            path = inbox / f"{batch_id}.json"
            atomic_write(path, data + b"\n")
            files_written += 1
            bytes_written += len(data) + 1
            units = [unit for article in article_group for unit in article["units"]]
            batch_rows.append((
                batch_id, run_id, "translation", manifest["manifest_sha256"], len(data),
                len(article_group), len(units), str(path),
            ))
            item_rows.extend(
                (batch_id, unit["unit_id"], ordinal) for ordinal, unit in enumerate(units)
            )
            audit_rows.append((
                "create", "batch", batch_id,
                json.dumps({"bytes": len(data), "units": len(units)}, ensure_ascii=False, sort_keys=True),
            ))
        phase.update(output_rows=len(batches), files_written=files_written, bytes_written=bytes_written)
    with metrics.phase("database_batch_loading", input_rows=len(batch_rows) + len(item_rows)) as phase:
        if getattr(connection, "backend", "sqlite") == "postgresql":
            connection.copy_rows(
                "batch", ("id", "run_id", "kind", "manifest_sha256", "serialized_bytes",
                          "article_count", "unit_count", "manifest_path"), batch_rows,
            )
            connection.copy_rows("batch_item", ("batch_id", "unit_id", "ordinal"), item_rows)
            connection.copy_rows(
                "audit_event", ("event_type", "entity_type", "entity_id", "details_json"), audit_rows,
            )
        else:
            items_by_batch: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
            for item in item_rows:
                items_by_batch[item[0]].append(item)
            for row, audit_row in zip(batch_rows, audit_rows, strict=True):
                connection.execute(
                    """INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,
                    article_count,unit_count,manifest_path) VALUES (?,?,?,?,?,?,?,?)""", row,
                )
                batch_id = row[0]
                connection.executemany(
                    "INSERT INTO batch_item(batch_id,unit_id,ordinal) VALUES (?,?,?)",
                    items_by_batch[batch_id],
                )
                audit(connection, audit_row[0], audit_row[1], audit_row[2], json.loads(audit_row[3]))
        phase.update(output_rows=len(batch_rows) + len(item_rows) + len(audit_rows))
    return {
        "batches_created": len(batches), "articles": len(envelopes),
        "units": sum(len(a["units"]) for a in envelopes), "phase_metrics": metrics.phases,
    }


def claim(
    connection: ConnectionLike, worker_id: str, outbox: Path,
    *, run_id: int, kind: str, model_id: str, reasoning_effort: str,
    transport: str, lease_minutes: int | None = None, batch_id: str | None = None,
) -> dict[str, str] | None:
    import uuid

    if kind not in {"translation", "review", "classification"}:
        raise ValueError(f"unsupported batch kind: {kind}")
    if transport not in {"responses-sync", "batch-api", "codex-agent"}:
        raise ValueError(f"unsupported transport: {transport}")
    if not model_id:
        raise ValueError("model_id is required")
    if not reasoning_effort:
        raise ValueError("reasoning_effort is required")
    effective_lease_minutes = lease_minutes if lease_minutes is not None else (26 * 60 if transport == "batch-api" else 30)
    outbox.mkdir(parents=True, exist_ok=True)
    backend = getattr(connection, "backend", "sqlite")
    now = dt.datetime.now(dt.timezone.utc)
    with transaction(connection, immediate=True):
        if backend == "postgresql":
            expired = connection.execute(
                """SELECT id,lease_token FROM batch
                WHERE run_id=? AND kind=? AND state='leased'
                AND lease_expires_at < CURRENT_TIMESTAMP
                AND NOT EXISTS (SELECT 1 FROM translation t JOIN batch_item bi ON bi.unit_id=t.unit_id
                                WHERE bi.batch_id=batch.id)
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED""", (run_id, kind),
            ).fetchall()
        else:
            expired = connection.execute(
                """SELECT id,lease_token FROM batch WHERE run_id=? AND kind=? AND state='leased'
                AND lease_expires_at < ? AND NOT EXISTS (SELECT 1 FROM translation t
                JOIN batch_item bi ON bi.unit_id=t.unit_id WHERE bi.batch_id=batch.id)
                ORDER BY created_at,id""",
                (run_id, kind, now.isoformat()),
            ).fetchall()
        for stale in expired:
            connection.execute(
                """UPDATE attempt SET outcome='interrupted',
                error_json='{"reason":"expired lease recovered before next claim"}',
                completed_at=CURRENT_TIMESTAMP WHERE batch_id=? AND lease_token=? AND outcome='claimed'""",
                (stale["id"], stale["lease_token"]),
            )
            recovered = connection.execute(
                """UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL
                WHERE id=? AND state='leased' AND lease_token=?""",
                (stale["id"], stale["lease_token"]),
            ).rowcount
            if recovered:
                audit(connection, "recover_expired_lease", "batch", stale["id"], {
                    "lease_token": stale["lease_token"], "reason": "database lease expired",
                })
        if batch_id is None:
            lock = " FOR UPDATE SKIP LOCKED" if backend == "postgresql" else ""
            batch = connection.execute(
                "SELECT * FROM batch WHERE run_id=? AND kind=? AND state='ready' "
                f"ORDER BY created_at,id LIMIT 1{lock}",
                (run_id, kind),
            ).fetchone()
        else:
            lock = " FOR UPDATE SKIP LOCKED" if backend == "postgresql" else ""
            batch = connection.execute(
                "SELECT * FROM batch WHERE id=? AND run_id=? AND kind=? AND state='ready'" + lock,
                (batch_id, run_id, kind),
            ).fetchone()
        if batch is None:
            return None
        token = secrets.token_urlsafe(24)
        attempt_id = f"att-{uuid.uuid4().hex}"
        expires = now + dt.timedelta(minutes=effective_lease_minutes)
        response_path = outbox / f"{attempt_id}.json"
        if backend == "postgresql":
            expires = connection.execute(
                """UPDATE batch SET state='leased',lease_token=?,
                lease_expires_at=CURRENT_TIMESTAMP + (? * INTERVAL '1 minute'),
                attempt_count=attempt_count+1 WHERE id=? RETURNING lease_expires_at""",
                (token, effective_lease_minutes, batch["id"]),
            ).fetchone()[0]
        else:
            connection.execute(
                "UPDATE batch SET state='leased',lease_token=?,lease_expires_at=?,attempt_count=attempt_count+1 WHERE id=?",
                (token, expires.isoformat(), batch["id"]),
            )
        connection.execute(
            """INSERT INTO attempt(
            id,batch_id,worker_id,model,prompt_sha256,lease_token,request_path,response_path,
            reasoning_effort,transport,api_custom_id)
            SELECT ?,?,?,?,CASE WHEN ?='review' THEN r.review_prompt_sha256 ELSE r.prompt_sha256 END,
            ?,?,?,?,?,? FROM run r WHERE r.id=?""",
            (
                attempt_id, batch["id"], worker_id, model_id, kind, token,
                batch["manifest_path"], str(response_path), reasoning_effort, transport,
                attempt_id if transport == "batch-api" else None, run_id,
            ),
        )
        audit(connection, "claim", "batch", batch["id"], {
            "attempt_id": attempt_id, "worker_id": worker_id, "run_id": run_id,
            "kind": kind, "model_id": model_id, "reasoning_effort": reasoning_effort,
            "transport": transport,
        })
    return {
        "batch_id": batch["id"], "attempt_id": attempt_id, "lease_token": token,
        "request_path": batch["manifest_path"], "response_path": str(response_path),
        "lease_expires_at": expires.isoformat() if hasattr(expires, "isoformat") else str(expires),
        "model_id": model_id, "reasoning_effort": reasoning_effort, "transport": transport,
    }


def retry_or_split(connection: ConnectionLike, batch_id: str, *, max_attempts: int = 3) -> dict[str, Any]:
    with transaction(connection, immediate=True):
        return _retry_or_split_locked(connection, batch_id, max_attempts=max_attempts)


def split_ready_batch(connection: ConnectionLike, batch_id: str, *, reason: str) -> dict[str, Any]:
    """Split an oversized ready batch before claiming a worker attempt."""
    with transaction(connection, immediate=True):
        lock = " FOR UPDATE" if getattr(connection, "backend", "sqlite") == "postgresql" else ""
        batch = connection.execute("SELECT * FROM batch WHERE id=?" + lock, (batch_id,)).fetchone()
        if batch is None or batch["state"] != "ready":
            return {"batch_id": batch_id, "requeued": False, "split": False}
        return _split_batch_locked(connection, batch, reason=reason)


def close_superseded_batches(connection: ConnectionLike, run_id: int) -> list[str]:
    """Close queued split batches whose units were satisfied by another batch."""
    rows = connection.execute(
        """SELECT b.id FROM batch b WHERE b.run_id=?
        AND b.state IN ('ready','retryable')
        AND EXISTS (SELECT 1 FROM batch_item bi WHERE bi.batch_id=b.id)
        AND NOT EXISTS (
          SELECT 1 FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
          WHERE bi.batch_id=b.id AND tu.status<>'translated'
        ) ORDER BY b.id""",
        (run_id,),
    ).fetchall()
    closed: list[str] = []
    with transaction(connection, immediate=True):
        for row in rows:
            batch_id = row["id"]
            updated = connection.execute(
                """UPDATE batch SET state='deterministic_validated',
                lease_token=NULL,lease_expires_at=NULL
                WHERE id=? AND run_id=? AND state IN ('ready','retryable')""",
                (batch_id, run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError(f"batch state changed for {batch_id}")
            connection.execute(
                """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,
                waiver_reason='all units satisfied by another deterministically valid split batch'
                WHERE resolved_at IS NULL AND attempt_id IN (
                  SELECT id FROM attempt WHERE batch_id=?
                )""",
                (batch_id,),
            )
            audit(connection, "close_superseded_batch", "batch", batch_id, {
                "reason": "all units translated by another deterministically valid split batch",
            })
            closed.append(batch_id)
    return closed


def _retry_or_split_locked(
    connection: ConnectionLike, batch_id: str, *, max_attempts: int,
) -> dict[str, Any]:
    lock = " FOR UPDATE" if getattr(connection, "backend", "sqlite") == "postgresql" else ""
    batch = connection.execute("SELECT * FROM batch WHERE id=?" + lock, (batch_id,)).fetchone()
    if batch is None or batch["state"] != "retryable":
        return {"batch_id": batch_id, "requeued": False, "split": False}
    if batch["attempt_count"] < max_attempts:
        connection.execute(
            "UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL WHERE id=?", (batch_id,)
        )
        audit(connection, "retry", "batch", batch_id, {"attempt_count": batch["attempt_count"]})
        return {"batch_id": batch_id, "requeued": True, "split": False}

    return _split_batch_locked(connection, batch, reason="validation retries exhausted")


def _split_batch_locked(connection: ConnectionLike, batch: Any, *, reason: str) -> dict[str, Any]:
    batch_id = batch["id"]
    manifest = json.loads(Path(batch["manifest_path"]).read_text(encoding="utf-8"))
    articles = manifest["articles"]
    if len(articles) > 1:
        midpoint = len(articles) // 2
        groups = [articles[:midpoint], articles[midpoint:]]
    else:
        units = articles[0]["units"]
        packets: dict[str, list[dict[str, Any]]] = {}
        for unit in units:
            packets.setdefault(unit.get("packet_id", unit["unit_id"]), []).append(unit)
        if len(packets) < 2:
            connection.execute("UPDATE batch SET state='blocked' WHERE id=?", (batch_id,))
            audit(connection, "block", "batch", batch_id,
                  {"reason": f"indivisible meaning packet: {reason}"})
            return {"batch_id": batch_id, "requeued": False, "split": False, "blocked": True}
        packet_list = list(packets.values())
        midpoint = len(packet_list) // 2
        groups = []
        for half in (packet_list[:midpoint], packet_list[midpoint:]):
            subset = [unit for packet in half for unit in packet]
            article = dict(articles[0])
            article["units"] = subset
            groups.append([article])

    children: list[str] = []
    inbox = Path(batch["manifest_path"]).parent
    for group in groups:
        identity = {"parent": batch_id, "unit_ids": [unit["unit_id"] for article in group for unit in article["units"]]}
        child_id = f"b-{sha256_bytes(canonical_json(identity))[:24]}"
        child_manifest, data = _manifest(child_id, group, manifest.get("terminology", {}))
        path = inbox / f"{child_id}.json"
        atomic_write(path, data + b"\n")
        units = [unit for article in group for unit in article["units"]]
        connection.execute(
            """INSERT INTO batch(id,run_id,kind,manifest_sha256,serialized_bytes,article_count,unit_count,manifest_path)
            VALUES (?,?,?,?,?,?,?,?)""",
            (child_id, batch["run_id"], batch["kind"], child_manifest["manifest_sha256"], len(data), len(group), len(units), str(path)),
        )
        connection.executemany(
            "INSERT INTO batch_item(batch_id,unit_id,ordinal) VALUES (?,?,?)",
            ((child_id, unit["unit_id"], index) for index, unit in enumerate(units)),
        )
        children.append(child_id)
    connection.execute("UPDATE batch SET state='blocked' WHERE id=?", (batch_id,))
    connection.execute(
        """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,waiver_reason='superseded by deterministic split'
        WHERE attempt_id IN (SELECT id FROM attempt WHERE batch_id=?) AND resolved_at IS NULL""", (batch_id,)
    )
    audit(connection, "split", "batch", batch_id, {"children": children, "reason": reason})
    return {"batch_id": batch_id, "requeued": False, "split": True, "children": children}
