#!/usr/bin/env python3
"""Merge validated Wadoku recovery results into the authoritative PostgreSQL run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from jitendex_ru.config import Config
from jitendex_ru.database import ConnectionLike, Database, transaction
from jitendex_ru.db import audit
from jitendex_ru.validate_response import target_storage, validate_worker_payload


UNIT_COLUMNS = (
    "id", "article_id", "json_pointer", "role", "source_text", "source_sha256",
    "protected_tokens_json", "byte_count",
)
BATCH_COLUMNS = (
    "id", "run_id", "kind", "manifest_sha256", "serialized_bytes", "article_count",
    "unit_count", "state", "lease_token", "lease_expires_at", "attempt_count",
    "manifest_path", "created_at",
)
BATCH_IDENTITY_COLUMNS = (
    "run_id", "kind", "manifest_sha256", "serialized_bytes", "article_count", "unit_count",
)
BATCH_ITEM_COLUMNS = ("batch_id", "unit_id", "ordinal")
ATTEMPT_COLUMNS = (
    "id", "batch_id", "worker_id", "model", "prompt_sha256", "lease_token",
    "request_path", "response_path", "outcome", "error_json", "created_at", "completed_at",
    "effective_model_id", "reasoning_effort", "transport", "api_request_id", "api_custom_id",
    "api_job_id", "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens",
    "finish_reason", "status_reason", "latency_ms",
)
TRANSLATION_COLUMNS = (
    "run_id", "unit_id", "attempt_id", "target_text", "confidence", "review_reason",
    "target_sha256", "accepted", "created_at",
)
TRANSLATION_COMPARE_COLUMNS = (
    "target_text", "confidence", "review_reason", "target_sha256",
)
CHUNK_SIZE = 900


def chunks(values: Sequence[str], size: int = CHUNK_SIZE) -> Iterable[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def row_values(row: Any, columns: Sequence[str]) -> tuple[Any, ...]:
    return tuple(row[column] for column in columns)


def source_rows_by_ids(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    ids: Sequence[str],
) -> dict[str, sqlite3.Row]:
    result: dict[str, sqlite3.Row] = {}
    selected = ",".join(columns)
    for part in chunks(ids):
        placeholders = ",".join("?" for _ in part)
        for row in connection.execute(
            f"SELECT {selected} FROM {table} WHERE id IN ({placeholders})", part,
        ):
            result[row["id"]] = row
    return result


def target_rows_by_ids(
    connection: ConnectionLike,
    table: str,
    columns: Sequence[str],
    ids: Sequence[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    selected = ",".join(columns)
    for part in chunks(ids):
        placeholders = ",".join("?" for _ in part)
        for row in connection.execute(
            f"SELECT {selected} FROM {table} WHERE id IN ({placeholders})", part,
        ):
            result[row["id"]] = row
    return result


def unit_digest(connection: Any, run_id: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    last_article_id = -1
    while True:
        selected = ",".join(UNIT_COLUMNS)
        rows = connection.execute(
            f"SELECT {selected} FROM translation_unit "
            "WHERE run_id=? AND article_id>? ORDER BY article_id LIMIT 10000",
            (run_id, last_article_id),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            digest.update(json.dumps(
                row_values(row, UNIT_COLUMNS), ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8"))
            digest.update(b"\n")
        count += len(rows)
        last_article_id = rows[-1]["article_id"]
    return count, digest.hexdigest()


def compare_existing_translations(
    source: sqlite3.Connection, target: ConnectionLike, run_id: int,
) -> int:
    compared = 0
    last_article_id = -1
    while True:
        rows = target.execute(
            """SELECT tu.article_id,t.unit_id,t.target_text,t.confidence,t.review_reason,
            t.target_sha256 FROM translation t JOIN translation_unit tu ON tu.id=t.unit_id
            WHERE t.run_id=? AND tu.article_id>? ORDER BY tu.article_id LIMIT 10000""",
            (run_id, last_article_id),
        ).fetchall()
        if not rows:
            break
        unit_ids = [row["unit_id"] for row in rows]
        source_rows: dict[str, sqlite3.Row] = {}
        for part in chunks(unit_ids):
            placeholders = ",".join("?" for _ in part)
            for source_row in source.execute(
                "SELECT unit_id,target_text,confidence,review_reason,target_sha256 "
                f"FROM translation WHERE run_id=? AND unit_id IN ({placeholders})",
                (run_id, *part),
            ):
                source_rows[source_row["unit_id"]] = source_row
        if len(source_rows) != len(rows):
            raise RuntimeError("SQLite lacks a translation that already exists in PostgreSQL")
        for row in rows:
            source_row = source_rows[row["unit_id"]]
            if row_values(row, TRANSLATION_COMPARE_COLUMNS) != row_values(
                source_row, TRANSLATION_COMPARE_COLUMNS,
            ):
                raise RuntimeError(f"translation mismatch for unit {row['unit_id']}")
        compared += len(rows)
        last_article_id = rows[-1]["article_id"]
    return compared


def source_translations(
    source: sqlite3.Connection, run_id: int, unit_ids: Sequence[str],
) -> list[sqlite3.Row]:
    result: list[sqlite3.Row] = []
    selected = ",".join(TRANSLATION_COLUMNS)
    for part in chunks(unit_ids):
        placeholders = ",".join("?" for _ in part)
        result.extend(source.execute(
            f"SELECT {selected} FROM translation WHERE run_id=? "
            f"AND unit_id IN ({placeholders}) ORDER BY unit_id",
            (run_id, *part),
        ).fetchall())
    if len(result) != len(unit_ids):
        raise RuntimeError(f"SQLite contains {len(result)} of {len(unit_ids)} missing translations")
    return result


def source_attempts(
    source: sqlite3.Connection, attempt_ids: Sequence[str],
) -> list[sqlite3.Row]:
    rows = source_rows_by_ids(source, "attempt", ATTEMPT_COLUMNS, attempt_ids)
    if len(rows) != len(attempt_ids):
        raise RuntimeError("SQLite lacks an attempt referenced by a final translation")
    return [rows[attempt_id] for attempt_id in attempt_ids]


def source_batch_items(
    source: sqlite3.Connection, batch_ids: Sequence[str],
) -> list[sqlite3.Row]:
    result: list[sqlite3.Row] = []
    for part in chunks(batch_ids):
        placeholders = ",".join("?" for _ in part)
        result.extend(source.execute(
            "SELECT batch_id,unit_id,ordinal FROM batch_item "
            f"WHERE batch_id IN ({placeholders}) ORDER BY batch_id,ordinal", part,
        ).fetchall())
    return result


def insert_rows(
    connection: ConnectionLike,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Any],
    conflict: str,
) -> None:
    if not rows:
        return
    placeholders = ",".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) {conflict}",
        (row_values(row, columns) for row in rows),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-config", type=Path, default=Path("config.wadoku.recovery.sqlite.toml"),
    )
    parser.add_argument(
        "--target-config", type=Path, default=Path("config.wadoku.luna.toml"),
    )
    parser.add_argument("--run-id", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_config = Config.load(args.source_config)
    target_config = Config.load(args.target_config)
    if source_config.db_backend != "sqlite":
        raise ValueError("source config must use SQLite")
    if target_config.db_backend != "postgresql":
        raise ValueError("target config must use PostgreSQL")

    source_uri = f"file:{source_config.db_path.resolve()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    source.row_factory = sqlite3.Row
    database = Database(target_config)
    target = database.connect()
    try:
        source_unit_count, source_unit_sha256 = unit_digest(source, args.run_id)
        target_unit_count, target_unit_sha256 = unit_digest(target, args.run_id)
        if (source_unit_count, source_unit_sha256) != (target_unit_count, target_unit_sha256):
            raise RuntimeError("PostgreSQL and SQLite translation units do not match")

        source_counts = source.execute(
            """SELECT COUNT(*) total,COALESCE(SUM(accepted),0) accepted,
            COUNT(DISTINCT unit_id) unique_units FROM translation WHERE run_id=?""",
            (args.run_id,),
        ).fetchone()
        source_unresolved = source.execute(
            "SELECT COUNT(*) FROM validation_issue WHERE run_id=? AND severity='error' "
            "AND resolved_at IS NULL", (args.run_id,),
        ).fetchone()[0]
        if tuple(source_counts) != (source_unit_count, source_unit_count, source_unit_count):
            raise RuntimeError(f"SQLite recovery is incomplete: {tuple(source_counts)}")
        if source_unresolved:
            raise RuntimeError(f"SQLite recovery has {source_unresolved} unresolved errors")

        active_leases = target.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased' "
            "AND lease_expires_at>CURRENT_TIMESTAMP", (args.run_id,),
        ).fetchone()[0]
        if active_leases:
            raise RuntimeError(f"PostgreSQL has {active_leases} active leases")

        compared = compare_existing_translations(source, target, args.run_id)
        missing_rows = target.execute(
            """SELECT tu.id FROM translation_unit tu LEFT JOIN translation t
            ON t.run_id=tu.run_id AND t.unit_id=tu.id
            WHERE tu.run_id=? AND t.id IS NULL ORDER BY tu.article_id""",
            (args.run_id,),
        ).fetchall()
        missing_unit_ids = [row["id"] for row in missing_rows]
        translations = source_translations(source, args.run_id, missing_unit_ids)
        if any(row["accepted"] != 1 for row in translations):
            raise RuntimeError("a missing SQLite translation is not accepted")

        attempt_ids = sorted({row["attempt_id"] for row in translations})
        attempts = source_attempts(source, attempt_ids)
        if any(row["outcome"] != "accepted" for row in attempts):
            raise RuntimeError("a final SQLite attempt is not accepted")
        existing_attempts = target_rows_by_ids(target, "attempt", ("id",), attempt_ids)
        if existing_attempts:
            raise RuntimeError("a recovery attempt ID already exists in PostgreSQL")

        batch_ids = sorted({row["batch_id"] for row in attempts})
        source_batches = source_rows_by_ids(source, "batch", BATCH_COLUMNS, batch_ids)
        if len(source_batches) != len(batch_ids):
            raise RuntimeError("SQLite lacks a batch referenced by a final attempt")
        if any(source_batches[batch_id]["state"] != "deterministic_validated" for batch_id in batch_ids):
            raise RuntimeError("a final SQLite batch is not deterministically validated")
        target_batches = target_rows_by_ids(target, "batch", BATCH_COLUMNS, batch_ids)
        for batch_id, target_batch in target_batches.items():
            source_batch = source_batches[batch_id]
            if row_values(source_batch, BATCH_IDENTITY_COLUMNS) != row_values(
                target_batch, BATCH_IDENTITY_COLUMNS,
            ):
                raise RuntimeError(f"batch identity mismatch for {batch_id}")
        new_batch_ids = sorted(set(batch_ids) - set(target_batches))
        new_batches = [source_batches[batch_id] for batch_id in new_batch_ids]
        batch_items = source_batch_items(source, new_batch_ids)

        responses: dict[str, dict[str, Any]] = {}
        source_targets = {row["unit_id"]: row for row in translations}
        for attempt_row in attempts:
            response_path = Path(attempt_row["response_path"])
            if not response_path.is_file():
                raise RuntimeError(f"missing accepted response file: {response_path}")
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            responses[attempt_row["id"]] = payload
            for item in payload.get("translations", []):
                unit_id = item.get("unit_id") if isinstance(item, dict) else None
                source_translation = source_targets.get(unit_id)
                if source_translation is not None and (
                    target_storage("glossary_set", item.get("target_text"))
                    != source_translation["target_text"]
                ):
                    raise RuntimeError(f"response target differs from SQLite for {unit_id}")

        result: dict[str, Any] = {
            "run_id": args.run_id,
            "apply": args.apply,
            "unit_count": source_unit_count,
            "unit_sha256": source_unit_sha256,
            "existing_translations_compared": compared,
            "translations_to_insert": len(translations),
            "attempts_to_insert": len(attempts),
            "batches_to_insert": len(new_batches),
            "batch_items_to_insert": len(batch_items),
        }
        if not args.apply:
            target.rollback()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0

        target.commit()
        with transaction(target):
            target.execute("SELECT pg_advisory_xact_lock(?)", (937264,))
            changed_count = target.execute(
                "SELECT COUNT(*) FROM translation WHERE run_id=?", (args.run_id,),
            ).fetchone()[0]
            if changed_count != compared:
                raise RuntimeError("PostgreSQL changed after preflight; refusing to merge")

            insert_rows(
                target, "batch", BATCH_COLUMNS, new_batches, "ON CONFLICT(id) DO NOTHING",
            )
            insert_rows(
                target, "batch_item", BATCH_ITEM_COLUMNS, batch_items,
                "ON CONFLICT(batch_id,unit_id) DO NOTHING",
            )
            insert_rows(
                target, "attempt", ATTEMPT_COLUMNS, attempts, "ON CONFLICT(id) DO NOTHING",
            )

            validation_issue_count = 0
            for attempt_row in attempts:
                issues = validate_worker_payload(
                    target, {"batch_id": attempt_row["batch_id"]}, responses[attempt_row["id"]],
                )
                if issues:
                    validation_issue_count += len(issues)
                    raise RuntimeError(
                        f"response validation failed for {attempt_row['id']}: "
                        f"{json.dumps(issues[:3], ensure_ascii=False)}"
                    )
            result["response_validation_issues"] = validation_issue_count

            pending_translations = []
            for row in translations:
                values = dict(row)
                values["accepted"] = 0
                pending_translations.append(values)
            insert_rows(
                target, "translation", TRANSLATION_COLUMNS, pending_translations,
                "ON CONFLICT(unit_id,attempt_id) DO NOTHING",
            )

            for part in chunks(batch_ids):
                placeholders = ",".join("?" for _ in part)
                target.execute(
                    "UPDATE batch SET state='deterministic_validated',lease_token=NULL,"
                    f"lease_expires_at=NULL WHERE id IN ({placeholders})", part,
                )
                target.execute(
                    "UPDATE batch SET attempt_count=(SELECT COUNT(*) FROM attempt a "
                    "WHERE a.batch_id=batch.id) "
                    f"WHERE id IN ({placeholders})", part,
                )

            interrupted = target.execute(
                """UPDATE attempt SET outcome='interrupted',completed_at=CURRENT_TIMESTAMP,
                error_json=? WHERE outcome='claimed' AND batch_id IN
                (SELECT id FROM batch WHERE run_id=?)""",
                (json.dumps({"reason": "superseded by authoritative recovery sync"}), args.run_id),
            ).rowcount
            superseded_batches = target.execute(
                """UPDATE batch SET state='blocked',lease_token=NULL,lease_expires_at=NULL
                WHERE run_id=? AND state IN ('ready','leased')""", (args.run_id,),
            ).rowcount
            translated_units = target.execute(
                """UPDATE translation_unit tu SET status='translated' WHERE tu.run_id=?
                AND EXISTS (SELECT 1 FROM translation t
                WHERE t.run_id=tu.run_id AND t.unit_id=tu.id)""", (args.run_id,),
            ).rowcount
            resolved_issues = target.execute(
                """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,
                waiver_reason='superseded by validated authoritative recovery sync'
                WHERE run_id=? AND severity='error' AND resolved_at IS NULL""",
                (args.run_id,),
            ).rowcount
            final_count = target.execute(
                "SELECT COUNT(*) FROM translation WHERE run_id=?", (args.run_id,),
            ).fetchone()[0]
            if final_count != source_unit_count:
                raise RuntimeError(f"PostgreSQL translation count is {final_count}, expected {source_unit_count}")
            result.update({
                "interrupted_stale_attempts": interrupted,
                "superseded_batches_blocked": superseded_batches,
                "translation_units_marked": translated_units,
                "validation_issues_resolved": resolved_issues,
                "final_translation_count": final_count,
            })
            audit(target, "sync_wadoku_recovery", "run", args.run_id, result)

        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        source.close()
        target.close()
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
