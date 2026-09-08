from __future__ import annotations

from .database import ConnectionLike, RowLike

import json
import zipfile
from pathlib import Path
from typing import Any

from .db import audit
from .prep_metrics import PrepMetrics
from .util import nfc, sha256_file


SOURCE = "jpdb"


def select_top_terms(connection: ConnectionLike, archive_path: Path, limit: int = 5000) -> dict[str, Any]:
    """Persist and select the Jitendex articles reachable from JPDB's top rows.

    JPDB's Yomitan metadata contains spellings but no lexical sequence or sense.
    A term therefore covers every Jitendex article whose expression or reading is
    exactly that spelling. Duplicate spellings retain their earliest rank.
    """
    if limit < 1:
        raise ValueError("JPDB limit must be positive")
    archive_hash = sha256_file(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        try:
            rows = json.loads(archive.read("term_meta_bank_1.json"))
        except KeyError as error:
            raise ValueError("JPDB archive has no term_meta_bank_1.json") from error
        try:
            index = json.loads(archive.read("index.json"))
        except KeyError:
            index = {}
    if not isinstance(rows, list) or len(rows) < limit:
        raise ValueError(f"JPDB archive has fewer than {limit} frequency rows")

    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for rank, row in enumerate(rows[:limit], 1):
        if not isinstance(row, list) or len(row) < 3 or not isinstance(row[0], str):
            raise ValueError(f"invalid JPDB frequency row at rank {rank}")
        term = nfc(row[0])
        if term not in seen:
            ranked.append((rank, term))
            seen.add(term)

    connection.execute("UPDATE article SET selected=0")
    connection.execute("DELETE FROM frequency_article")
    connection.execute("DELETE FROM frequency_term")
    connection.execute("DELETE FROM frequency_source")
    connection.execute(
        """INSERT INTO frequency_source
        (source,source_sha256,rank_limit,local_path,title,revision,parser_version,metadata_json)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            SOURCE, archive_hash, limit, str(archive_path), index.get("title"), index.get("revision"),
            "jpdb-row-order-v1", json.dumps({"rank_mode": "row_order"}, ensure_ascii=False, sort_keys=True),
        ),
    )
    connection.executemany(
        "INSERT INTO frequency_term(source,source_sha256,rank,term) VALUES (?,?,?,?)",
        ((SOURCE, archive_hash, rank, term) for rank, term in ranked),
    )
    expression_index: dict[str, list[int]] = {}
    reading_index: dict[str, list[int]] = {}
    for article in connection.execute("SELECT id,expression,reading FROM article ORDER BY id"):
        expression_index.setdefault(nfc(article["expression"]), []).append(article["id"])
        reading_index.setdefault(nfc(article["reading"]), []).append(article["id"])

    matched_terms = 0
    mappings: list[tuple[str, str, int, str, int, str]] = []
    article_ids: set[int] = set()
    for rank, term in ranked:
        matches: dict[int, str] = {}
        for article_id in expression_index.get(term, []):
            matches[article_id] = "expression"
        for article_id in reading_index.get(term, []):
            matches.setdefault(article_id, "reading")
        if matches:
            matched_terms += 1
            connection.execute(
                "UPDATE frequency_term SET matched=1 WHERE source=? AND source_sha256=? AND rank=?",
                (SOURCE, archive_hash, rank),
            )
        for article_id, match_kind in matches.items():
            mappings.append((SOURCE, archive_hash, rank, term, article_id, match_kind))
            article_ids.add(article_id)
    connection.executemany(
        """INSERT INTO frequency_article
        (source,source_sha256,rank,term,article_id,match_kind) VALUES (?,?,?,?,?,?)""",
        mappings,
    )
    connection.executemany("UPDATE article SET selected=1 WHERE id=?", ((item,) for item in article_ids))
    result = {
        "source": SOURCE,
        "source_sha256": archive_hash,
        "requested_rows": limit,
        "unique_terms": len(ranked),
        "matched_terms": matched_terms,
        "skipped_terms": len(ranked) - matched_terms,
        "selected_articles": len(article_ids),
        "expression_matches": sum(item[-1] == "expression" for item in mappings),
        "reading_matches": sum(item[-1] == "reading" for item in mappings),
    }
    audit(connection, "select_frequency_scope", "frequency_source", archive_hash, result)
    return result


def coverage_report(connection: ConnectionLike, run_id: int) -> dict[str, Any]:
    source_row = connection.execute(
        "SELECT source_sha256 FROM frequency_term WHERE source=? ORDER BY rank LIMIT 1", (SOURCE,)
    ).fetchone()
    if source_row is None:
        raise ValueError("JPDB scope has not been imported")
    source_hash = source_row["source_sha256"]
    totals = connection.execute(
        "SELECT COUNT(*),SUM(matched) FROM frequency_term WHERE source=? AND source_sha256=?",
        (SOURCE, source_hash),
    ).fetchone()
    total_terms = int(totals[0])
    matched_terms = int(totals[1] or 0)
    covered = connection.execute(
        """SELECT COUNT(DISTINCT ft.term) FROM frequency_term ft
        JOIN frequency_article fa ON fa.source=ft.source AND fa.source_sha256=ft.source_sha256 AND fa.term=ft.term
        JOIN run_article ra ON ra.article_id=fa.article_id AND ra.run_id=?
        WHERE ft.source=? AND ft.source_sha256=?""",
        (run_id, SOURCE, source_hash),
    ).fetchone()[0]
    accepted_articles = connection.execute(
        """SELECT COUNT(*) FROM run_article ra WHERE ra.run_id=? AND NOT EXISTS (
        SELECT 1 FROM translation_unit tu WHERE tu.run_id=ra.run_id AND tu.article_id=ra.article_id
        AND NOT EXISTS (SELECT 1 FROM translation t WHERE t.run_id=tu.run_id AND t.unit_id=tu.id AND t.accepted=1))""",
        (run_id,),
    ).fetchone()[0]
    selected_articles = connection.execute("SELECT COUNT(*) FROM run_article WHERE run_id=?", (run_id,)).fetchone()[0]
    return {
        "run_id": run_id,
        "source_sha256": source_hash,
        "unique_terms": total_terms,
        "matched_terms": matched_terms,
        "skipped_terms": total_terms - matched_terms,
        "covered_terms": covered,
        "selected_articles": selected_articles,
        "fully_accepted_articles": accepted_articles,
        "complete": covered == matched_terms and accepted_articles == selected_articles,
    }


def reuse_accepted_translations(
    connection: ConnectionLike, source_run_id: int, target_run_id: int,
) -> dict[str, Any]:
    """Reuse byte-identical accepted Luna targets for the expanded run."""
    if source_run_id == target_run_id:
        raise ValueError("source and target runs must differ")
    for run_id in (source_run_id, target_run_id):
        if connection.execute("SELECT 1 FROM run WHERE id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run {run_id}")
    metrics = PrepMetrics("reuse_translations")
    with metrics.phase("translation_reuse") as phase:
        inserted = connection.execute(
            """INSERT INTO translation
            (run_id,unit_id,attempt_id,target_text,confidence,review_reason,target_sha256,accepted)
            SELECT ?,target.id,source.attempt_id,source.target_text,source.confidence,
            source.review_reason,source.target_sha256,1
            FROM translation_unit target
            JOIN translation_unit old ON old.run_id=? AND old.article_id=target.article_id
              AND old.json_pointer=target.json_pointer AND old.role=target.role
              AND old.source_sha256=target.source_sha256
            JOIN translation source ON source.unit_id=old.id AND source.accepted=1
            WHERE target.run_id=? AND target.status='ready'
              AND NOT EXISTS (SELECT 1 FROM translation existing
                              WHERE existing.unit_id=target.id AND existing.accepted=1)
            ON CONFLICT(unit_id,attempt_id) DO NOTHING""",
            (target_run_id, source_run_id, target_run_id),
        ).rowcount
        phase.update(input_rows=inserted, output_rows=inserted)
    with metrics.phase("status_update", input_rows=inserted) as phase:
        updated = connection.execute(
            """UPDATE translation_unit SET status='translated'
            WHERE run_id=? AND status='ready' AND EXISTS (
              SELECT 1 FROM translation accepted
              WHERE accepted.unit_id=translation_unit.id AND accepted.accepted=1)""",
            (target_run_id,),
        ).rowcount
        phase.update(output_rows=updated)
    audit_result = {
        "source_run_id": source_run_id, "target_run_id": target_run_id,
        "units_reused": inserted,
    }
    audit(connection, "reuse_accepted_translations", "run", target_run_id, audit_result)
    return {**audit_result, "phase_metrics": metrics.phases}


def accept_deterministic_translations(connection: ConnectionLike, run_id: int) -> dict[str, int]:
    """Promote deterministically valid Luna responses when no review pass is requested."""
    candidates = connection.execute(
        """SELECT t.id,t.unit_id FROM translation t JOIN attempt a ON a.id=t.attempt_id
        JOIN batch b ON b.id=a.batch_id
        WHERE t.run_id=? AND t.accepted=0 AND a.outcome='accepted'
          AND b.kind='translation' AND b.state='deterministic_validated'
          AND NOT EXISTS (SELECT 1 FROM translation accepted
                          WHERE accepted.unit_id=t.unit_id AND accepted.accepted=1)
          AND NOT EXISTS (SELECT 1 FROM validation_issue vi
                          WHERE vi.attempt_id=t.attempt_id AND vi.severity='error' AND vi.resolved_at IS NULL)
        ORDER BY t.unit_id,t.created_at DESC,t.id DESC""",
        (run_id,),
    ).fetchall()
    chosen: dict[str, int] = {}
    for row in candidates:
        chosen.setdefault(row["unit_id"], row["id"])
    connection.executemany(
        """UPDATE translation SET accepted=1,accepted_at=COALESCE(accepted_at,CURRENT_TIMESTAMP),
        acceptance_method=COALESCE(acceptance_method,'luna') WHERE id=?""",
        ((item,) for item in chosen.values()),
    )
    connection.executemany(
        "UPDATE translation_unit SET status='translated' WHERE id=?", ((item,) for item in chosen),
    )
    result = {"run_id": run_id, "translations_accepted": len(chosen)}
    audit(connection, "accept_deterministic_translations", "run", run_id, result)
    return result
