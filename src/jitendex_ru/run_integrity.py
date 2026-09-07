from __future__ import annotations

from .database import ConnectionLike, RowLike

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .util import canonical_json, sha256_bytes


EXPECTED_LEXICOGRAPHER_V2_ROLES = {
    "tooltip": 8992,
    "glossary_set": 5580,
    "pos": 4172,
    "example": 3176,
    "note": 1784,
    "xref_gloss": 1650,
    "register": 1108,
    "label": 1056,
}


def _database_json_value(value: Any) -> Any:
    """Normalize backend-native scalar types before deterministic hashing."""
    if isinstance(value, dict):
        return {key: _database_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_database_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, bytes):
        return value.hex()
    return value


def headword_progress(connection: ConnectionLike, run_id: int) -> tuple[int, int]:
    """Return fully translated headwords and the complete source-headword total."""
    row = connection.execute(
        """WITH selected_run AS (
          SELECT id AS run_id,jitendex_snapshot_id FROM run WHERE id=?
        ), source_articles AS (
          SELECT a.id,a.expression,a.reading,sr.run_id
          FROM selected_run sr JOIN article a
            ON a.snapshot_id=sr.jitendex_snapshot_id
        ), all_headwords AS (
          SELECT expression,reading FROM source_articles GROUP BY expression,reading
        ), incomplete_headwords AS (
          SELECT a.expression,a.reading FROM source_articles a
          LEFT JOIN run_article ra
            ON ra.article_id=a.id AND ra.run_id=a.run_id
          WHERE ra.article_id IS NULL
          UNION
          SELECT a.expression,a.reading FROM source_articles a
          JOIN run_article ra ON ra.article_id=a.id AND ra.run_id=a.run_id
          JOIN translation_unit tu
            ON tu.run_id=ra.run_id AND tu.article_id=ra.article_id
          WHERE NOT EXISTS (
            SELECT 1 FROM translation t
            WHERE t.run_id=tu.run_id AND t.unit_id=tu.id AND t.accepted=1
          ) AND NOT EXISTS (
            SELECT 1 FROM batch_item bi WHERE bi.unit_id=tu.id AND EXISTS (
              SELECT 1 FROM batch b WHERE b.id=bi.batch_id AND b.run_id=tu.run_id
                AND b.state='deterministic_validated')
          )
        )
        SELECT (SELECT COUNT(*) FROM all_headwords)
                 -(SELECT COUNT(*) FROM incomplete_headwords),
               (SELECT COUNT(*) FROM all_headwords)""",
        (run_id,),
    ).fetchone()
    return row[0], row[1]


def workload_progress(connection: ConnectionLike, run_id: int) -> dict[str, int]:
    """Return production-complete workload counts for one exact metric snapshot."""
    row = connection.execute(
        """WITH completed_unit AS MATERIALIZED (
          SELECT unit_id FROM translation WHERE run_id=? AND accepted=1
          UNION
          SELECT bi.unit_id FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
          WHERE b.run_id=? AND b.state='deterministic_validated'
        ), progress AS (
          SELECT COUNT(cu.unit_id) completed_units,
                 COALESCE(SUM(LENGTH(tu.source_text)) FILTER (WHERE cu.unit_id IS NOT NULL),0)
                   source_characters,
                 COUNT(DISTINCT tu.article_id) FILTER (WHERE cu.unit_id IS NULL)
                   incomplete_articles
          FROM translation_unit tu LEFT JOIN completed_unit cu ON cu.unit_id=tu.id
          WHERE tu.run_id=?
        )
        SELECT completed_units,source_characters,
               (SELECT COUNT(*) FROM run_article WHERE run_id=?)-incomplete_articles
        FROM progress""",
        (run_id, run_id, run_id, run_id),
    ).fetchone()
    units, source_characters, articles = row[0], row[1], row[2]
    headwords, _ = headword_progress(connection, run_id)
    return {
        "headwords": int(headwords), "articles": int(articles), "units": int(units),
        "source_characters": int(source_characters),
    }


def source_identity_report(
    connection: ConnectionLike, candidate_run_id: int, baseline_run_id: int = 2,
) -> dict[str, Any]:
    """Compare a new run with the immutable source-unit identity of a baseline run."""
    for run_id in (candidate_run_id, baseline_run_id):
        if connection.execute("SELECT 1 FROM run WHERE id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run {run_id}")
    articles, units = connection.execute(
        "SELECT COUNT(DISTINCT article_id),COUNT(*) FROM translation_unit WHERE run_id=?",
        (candidate_run_id,),
    ).fetchone()
    roles = {
        row["role"]: row["units"]
        for row in connection.execute(
            "SELECT role,COUNT(*) units FROM translation_unit WHERE run_id=? GROUP BY role ORDER BY role",
            (candidate_run_id,),
        )
    }
    key_sql = "SELECT article_id,json_pointer,role,source_sha256 FROM translation_unit WHERE run_id=?"
    missing = connection.execute(
        f"SELECT COUNT(*) FROM ({key_sql} EXCEPT {key_sql})",
        (baseline_run_id, candidate_run_id),
    ).fetchone()[0]
    extra = connection.execute(
        f"SELECT COUNT(*) FROM ({key_sql} EXCEPT {key_sql})",
        (candidate_run_id, baseline_run_id),
    ).fetchone()[0]
    passed = (
        articles == 1704
        and units == 27518
        and roles == EXPECTED_LEXICOGRAPHER_V2_ROLES
        and missing == 0
        and extra == 0
    )
    return {
        "candidate_run_id": candidate_run_id,
        "baseline_run_id": baseline_run_id,
        "articles": articles,
        "units": units,
        "roles": roles,
        "missing_from_candidate": missing,
        "extra_in_candidate": extra,
        "passed": passed,
    }


def run_history_fingerprint(connection: ConnectionLike, run_id: int) -> dict[str, Any]:
    """Hash every row owned by a run without including unrelated database state."""
    queries = {
        "run": ("SELECT * FROM run WHERE id=? ORDER BY id", (run_id,)),
        "translation_unit": ("SELECT * FROM translation_unit WHERE run_id=? ORDER BY id", (run_id,)),
        "run_article": ("SELECT * FROM run_article WHERE run_id=? ORDER BY article_id", (run_id,)),
        "batch": ("SELECT * FROM batch WHERE run_id=? ORDER BY id", (run_id,)),
        "batch_item": (
            "SELECT bi.* FROM batch_item bi JOIN batch b ON b.id=bi.batch_id WHERE b.run_id=? ORDER BY bi.batch_id,bi.ordinal",
            (run_id,),
        ),
        "attempt": (
            "SELECT a.* FROM attempt a JOIN batch b ON b.id=a.batch_id WHERE b.run_id=? ORDER BY a.id",
            (run_id,),
        ),
        "translation": ("SELECT * FROM translation WHERE run_id=? ORDER BY id", (run_id,)),
        "translation_canonicalization_history": (
            "SELECT * FROM translation_canonicalization_history WHERE run_id=? ORDER BY id",
            (run_id,),
        ),
        "review": (
            "SELECT r.* FROM review r JOIN translation t ON t.id=r.translation_id WHERE t.run_id=? ORDER BY r.id",
            (run_id,),
        ),
        "validation_issue": ("SELECT * FROM validation_issue WHERE run_id=? ORDER BY id", (run_id,)),
        "export": ("SELECT * FROM export WHERE run_id=? ORDER BY id", (run_id,)),
        "export_file": (
            "SELECT ef.* FROM export_file ef JOIN export e ON e.id=ef.export_id WHERE e.run_id=? ORDER BY ef.export_id,ef.path",
            (run_id,),
        ),
    }
    tables: dict[str, dict[str, Any]] = {}
    for table, (sql, parameters) in queries.items():
        rows = [_database_json_value(dict(row)) for row in connection.execute(sql, parameters)]
        tables[table] = {"rows": len(rows), "sha256": sha256_bytes(canonical_json(rows))}
    return {
        "run_id": run_id,
        "tables": tables,
        "sha256": sha256_bytes(canonical_json(tables)),
    }
