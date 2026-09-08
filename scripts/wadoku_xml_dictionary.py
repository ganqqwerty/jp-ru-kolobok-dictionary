#!/usr/bin/env python3
"""Build the rich Wadoku XML German and Russian Yomitan editions."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from jitendex_ru.config import Config
from jitendex_ru.batch import make_batches as shared_make_batches
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.schema_validation import validate_archive
from jitendex_ru.validate_response import wadoku_target_issues
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes, sha256_file
from jitendex_ru.wadoku_xml import (
    EXPECTED_COUNTS, WADOKU_ARCHIVE_SHA256, WADOKU_PIPELINE, build_rich_archive,
    canonical_identity, iter_canonical_entries, label_catalog, source_report,
    translation_unit_id, decode_v1_single_block, localized_archive_shape,
)


SCHEMA_DIR = Path("schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162")
EMPTY_SHA256 = sha256_bytes(b"")
FIXED_SAMPLE = ("インスリン", "ころっと", "人買い", "バーン･ジョーンズ", "暴食", "三ケ日人骨")


def _require_postgresql(config: Config) -> None:
    if config.db_backend != "postgresql":
        raise ValueError("Wadoku XML production commands require PostgreSQL")


def db_check(config: Config, require_schema_version: int | None) -> dict[str, Any]:
    _require_postgresql(config)
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get("dbname") != "wadoku":
        raise ValueError("WADOKU_POSTGRES_URL must name database wadoku")
    database = Database(config)
    connection = database.connect()
    try:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if database_name != "wadoku":
            raise ValueError("connected PostgreSQL database is not wadoku")
        version = int(connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0])
        allowed = {require_schema_version} if require_schema_version is not None else {9, 10}
        if version not in allowed:
            raise ValueError(f"unexpected Wadoku schema version: {version}; expected {sorted(allowed)}")
        active = int(connection.execute(
            """SELECT COUNT(*) FROM batch b JOIN run r ON r.id=b.run_id
            WHERE r.pipeline_version=? AND b.state='leased'""", (WADOKU_PIPELINE,),
        ).fetchone()[0])
        if active:
            raise ValueError(f"{active} Wadoku XML batch leases are active")
        return {"database": database_name, "schema_version": version, "active_leases": active}
    finally:
        connection.close()
        database.close()


def _verify_source_hashes(config: Config, archive: Path, source: Path, xsd: Path, license_path: Path) -> None:
    expected = config.raw["source"]
    actual = {"sha256": sha256_file(archive), "xml_sha256": sha256_file(source),
              "xsd_sha256": sha256_file(xsd), "license_sha256": sha256_file(license_path)}
    mismatches = {key: {"expected": expected[key], "actual": value}
                  for key, value in actual.items() if expected[key] != value}
    if archive.stat().st_size != 25_697_088:
        mismatches["archive_bytes"] = {"expected": 25_697_088, "actual": archive.stat().st_size}
    if mismatches:
        raise ValueError(f"Wadoku source provenance differs: {mismatches}")


def _versioned_prompt(config: Config) -> bytes:
    version = config.raw["versions"]["translation_prompt"]
    return (config.root / "prompts" / f"{version.replace('-', '_')}.txt").read_bytes()


def _terminology_hash(config: Config) -> str:
    return sha256_bytes(canonical_json({
        "terminology": sha256_file(config.path("paths", "terminology")),
        "controlled_labels": sha256_file(config.path("paths", "controlled_labels")),
        "subentry_groups": sha256_file(config.path("paths", "subentry_groups")),
    }))


def _run_identity(snapshot_hash: str, selection_hash: str, extractor: str,
                  prompt_hash: str, terminology_hash: str, limits_json: str,
                  pipeline: str) -> str:
    return sha256_bytes(canonical_json({
        "dictionary_snapshot_sha256": snapshot_hash, "selection_sha256": selection_hash,
        "extractor_version": extractor, "prompt_sha256": prompt_hash,
        "terminology_sha256": terminology_hash, "limits_json": limits_json,
        "pipeline_version": pipeline,
    }))


def _import_articles(connection: Any, snapshot_id: int, source: Path) -> int:
    existing = int(connection.execute(
        "SELECT COUNT(*) FROM article WHERE snapshot_id=?", (snapshot_id,),
    ).fetchone()[0])
    if existing:
        if existing != EXPECTED_COUNTS["entries"]:
            raise ValueError(f"partial Wadoku XML snapshot has {existing} articles")
        return 0

    def rows() -> Iterator[tuple[Any, ...]]:
        for ordinal, value in iter_canonical_entries(source):
            expression, reading, sequence = canonical_identity(value)
            raw = canonical_json(value)
            yield (snapshot_id, 1, ordinal, expression, reading, sequence, raw.decode(),
                   sha256_bytes(raw), sha256_bytes(canonical_json(value["tree"])), 1)

    return connection.copy_rows(
        "article", ("snapshot_id", "bank_number", "entry_ordinal", "expression", "reading",
                    "sequence", "raw_json", "source_sha256", "structural_fingerprint", "selected"),
        rows(),
    )


def _ensure_snapshot(connection: Any, config: Config, archive: Path, source: Path,
                     xsd: Path, license_path: Path, report: dict[str, Any]) -> int:
    metadata = canonical_json({
        "archive_bytes": archive.stat().st_size, "archive_path": str(archive.resolve()),
        "xml_path": str(source.resolve()), "xsd_path": str(xsd.resolve()),
        "license_path": str(license_path.resolve()),
        "xml_sha256": config.raw["source"]["xml_sha256"],
        "xsd_sha256": config.raw["source"]["xsd_sha256"],
        "license_sha256": config.raw["source"]["license_sha256"],
        "source_counts": report["source_counts"], "normalized_counts": report["normalized_counts"],
    }).decode()
    connection.execute(
        """INSERT INTO source_snapshot
        (kind,version,url,sha256,local_path,extractor_version,metadata_json)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        ("wadoku", config.raw["source"]["version"], config.raw["source"]["url"],
         config.raw["source"]["sha256"], str(archive.resolve()), WADOKU_PIPELINE, metadata),
    )
    return int(connection.execute(
        "SELECT id FROM source_snapshot WHERE kind='wadoku' AND sha256=?",
        (config.raw["source"]["sha256"],),
    ).fetchone()[0])


def _ensure_run(connection: Any, config: Config, snapshot_id: int) -> tuple[int, bool]:
    selection_hash = sha256_bytes(f"wadoku-all:{WADOKU_ARCHIVE_SHA256}".encode())
    prompt_hash = sha256_bytes(_versioned_prompt(config))
    terminology_hash = _terminology_hash(config)
    limits_json = canonical_json(config.raw["batch"]).decode()
    identity = _run_identity(WADOKU_ARCHIVE_SHA256, selection_hash, WADOKU_PIPELINE,
                             prompt_hash, terminology_hash, limits_json, WADOKU_PIPELINE)
    existing = connection.execute("SELECT id FROM run WHERE run_identity_sha256=?", (identity,)).fetchone()
    if existing is not None:
        return int(existing[0]), False
    row = connection.execute(
        """INSERT INTO run
        (dictionary_snapshot_id,selection_sha256,extractor_version,prompt_sha256,
         review_prompt_sha256,terminology_sha256,limits_json,pipeline_version,run_identity_sha256)
        VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
        (snapshot_id, selection_hash, WADOKU_PIPELINE, prompt_hash, EMPTY_SHA256,
         terminology_hash, limits_json, WADOKU_PIPELINE, identity),
    ).fetchone()
    return int(row[0]), True


def _prepare_run_contents(connection: Any, run_id: int, snapshot_id: int,
                          source: Path) -> dict[str, int]:
    existing_articles = int(connection.execute(
        "SELECT COUNT(*) FROM run_article WHERE run_id=?", (run_id,),
    ).fetchone()[0])
    existing_units = int(connection.execute(
        "SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,),
    ).fetchone()[0])
    if existing_articles or existing_units:
        if existing_articles != EXPECTED_COUNTS["entries"]:
            raise ValueError(f"partial Wadoku run has {existing_articles} articles")
        return {"run_articles_added": 0, "translation_units_added": 0,
                "run_articles": existing_articles, "translation_units": existing_units}
    prepared_at = datetime.now(timezone.utc)
    article_rows = connection.execute(
        """SELECT id,entry_ordinal,structural_fingerprint FROM article
        WHERE snapshot_id=? ORDER BY entry_ordinal""", (snapshot_id,),
    ).fetchall()
    if len(article_rows) != EXPECTED_COUNTS["entries"]:
        raise ValueError("Wadoku snapshot article count differs before run preparation")
    article_by_ordinal = {int(row["entry_ordinal"]): int(row["id"]) for row in article_rows}
    run_articles = connection.copy_rows(
        "run_article", ("run_id", "article_id", "structural_fingerprint", "prepared_at"),
        ((run_id, int(row["id"]), row["structural_fingerprint"], prepared_at)
         for row in article_rows),
    )

    def units() -> Iterator[tuple[Any, ...]]:
        for ordinal, value in iter_canonical_entries(source):
            article_id = article_by_ordinal[ordinal]
            for index, block in enumerate(value["blocks"]):
                if not block["has_translatable_text"]:
                    continue
                source_text = block["prompt_text"]
                yield (translation_unit_id(WADOKU_ARCHIVE_SHA256, value["entry_id"], block),
                       run_id, article_id, f"/blocks/{index}", block["role"], source_text,
                       sha256_bytes(source_text.encode()), canonical_json([
                           item["placeholder"] for item in block["protected_fragments"]
                       ]).decode(), len(source_text.encode()), "ready")

    translation_units = connection.copy_rows(
        "translation_unit", ("id", "run_id", "article_id", "json_pointer", "role",
                             "source_text", "source_sha256", "protected_tokens_json",
                             "byte_count", "status"), units(),
    )
    return {"run_articles_added": run_articles, "translation_units_added": translation_units,
            "run_articles": run_articles, "translation_units": translation_units}


def prepare(config: Config, archive: Path, source: Path, xsd: Path,
            license_path: Path) -> dict[str, Any]:
    _require_postgresql(config)
    _verify_source_hashes(config, archive, source, xsd, license_path)
    report = source_report(
        source, xsd, config.path("paths", "controlled_labels"),
        config.path("paths", "subentry_groups"),
    )
    if (
        report["expected_count_mismatches"] or report["missing_controlled_label_count"]
        or report["missing_subentry_policies"] or report["unused_subentry_policies"]
    ):
        raise ValueError("Wadoku source or controlled-label gate failed")
    database = Database(config)
    connection = database.connect()
    try:
        snapshot_id = _ensure_snapshot(connection, config, archive, source, xsd, license_path, report)
        articles_added = _import_articles(connection, snapshot_id, source)
        audit(connection, "import_wadoku_xml", "source_snapshot", snapshot_id,
              {"articles_added": articles_added, "source_counts": report["source_counts"]})
        connection.commit()
        run_id, run_created = _ensure_run(connection, config, snapshot_id)
        prepared = _prepare_run_contents(connection, run_id, snapshot_id, source)
        audit(connection, "prepare_wadoku_xml", "run", run_id,
              {"snapshot_id": snapshot_id, "run_created": run_created, **prepared})
        connection.commit()
        return {"run_id": run_id, "snapshot_id": snapshot_id, "run_created": run_created,
                "articles_added": articles_added, **prepared,
                "source_counts": report["source_counts"],
                "normalized_counts": report["normalized_counts"]}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def _reserve_export_id(connection: Any, run_id: int, output: Path) -> tuple[int, bool]:
    existing = connection.execute(
        "SELECT id FROM export WHERE run_id=? AND output_path=? ORDER BY id LIMIT 1",
        (run_id, str(output.resolve())),
    ).fetchone()
    if existing is not None:
        return int(existing[0]), False
    return int(connection.execute(
        "SELECT nextval(pg_get_serial_sequence('export','id'))"
    ).fetchone()[0]), True


def _run_source(connection: Any, run_id: int) -> tuple[Any, dict[str, Any]]:
    row = connection.execute(
        """SELECT r.*,ss.sha256 snapshot_sha256,ss.url source_url,ss.metadata_json
        FROM run r JOIN source_snapshot ss ON ss.id=r.dictionary_snapshot_id WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    if row is None or row["pipeline_version"] != WADOKU_PIPELINE:
        raise ValueError(f"run {run_id} is not a Wadoku XML run")
    return row, json.loads(row["metadata_json"])


def export_de(config: Config, run_id: int, output: Path) -> dict[str, Any]:
    _require_postgresql(config)
    labels = label_catalog(config.path("paths", "controlled_labels"))
    database = Database(config)
    connection = database.connect()
    try:
        run, metadata = _run_source(connection, run_id)
        article_count = int(connection.execute(
            "SELECT COUNT(*) FROM run_article WHERE run_id=?", (run_id,),
        ).fetchone()[0])
        if article_count != EXPECTED_COUNTS["entries"]:
            raise ValueError(f"Wadoku run has {article_count} articles")
        export_id, is_new = _reserve_export_id(connection, run_id, output)

        def entries() -> Iterator[tuple[dict[str, Any], None]]:
            for row in connection.execute(
                """SELECT a.raw_json FROM run_article ra JOIN article a ON a.id=ra.article_id
                WHERE ra.run_id=? ORDER BY a.entry_ordinal""", (run_id,),
            ):
                yield json.loads(row[0]), None

        report = build_rich_archive(
            entries(), output.resolve(), language="de", labels=labels,
            license_text=Path(metadata["license_path"]).read_bytes(),
            title=config.raw["product"]["de"]["title"],
            revision=config.raw["product"]["de"]["revision"],
            source_url=run["source_url"], source_sha256=run["snapshot_sha256"],
            export_audit_id=export_id,
        )
        report.update(validate_archive(output.resolve(), config.root / SCHEMA_DIR))
        manifest_hash = sha256_bytes(canonical_json(report["files"]))
        if is_new:
            connection.execute(
                """INSERT INTO export(id,run_id,output_path,manifest_sha256,zip_sha256,verified)
                VALUES (?,?,?,?,?,1)""",
                (export_id, run_id, str(output.resolve()), manifest_hash, report["zip_sha256"]),
            )
            connection.copy_rows(
                "export_file", ("export_id", "path", "sha256", "byte_count"),
                ((export_id, name, digest, report["file_bytes"][name])
                 for name, digest in report["files"].items()),
            )
        else:
            recorded = connection.execute(
                "SELECT zip_sha256 FROM export WHERE id=?", (export_id,),
            ).fetchone()[0]
            if recorded != report["zip_sha256"]:
                raise ValueError("repeat German export is not byte-identical")
        audit(connection, "export_wadoku_de", "export", export_id, report)
        connection.commit()
        return {"run_id": run_id, "export_id": export_id,
                "output": str(output.resolve()), **report}
    finally:
        connection.close()
        database.close()


def verify_de(config: Config, run_id: int, archive: Path) -> dict[str, Any]:
    _require_postgresql(config)
    schema = validate_archive(archive.resolve(), config.root / SCHEMA_DIR)
    database = Database(config)
    connection = database.connect()
    try:
        _run, metadata = _run_source(connection, run_id)
        with zipfile.ZipFile(archive) as source:
            bad = source.testzip()
            if bad is not None:
                raise ValueError(f"corrupt Wadoku archive member: {bad}")
            index = json.loads(source.read("index.json"))
            expected_product = config.raw["product"]["de"]
            if (index.get("sourceLanguage"), index.get("targetLanguage")) != ("ja", "de"):
                raise ValueError("German Wadoku archive language metadata differs")
            if (index.get("title"), index.get("revision")) != (
                expected_product["title"], expected_product["revision"],
            ):
                raise ValueError("German Wadoku archive product metadata differs")
            term_rows = sum(len(json.loads(source.read(name))) for name in source.namelist()
                            if name.startswith("term_bank_"))
            if source.read("LICENSE") != Path(metadata["license_path"]).read_bytes():
                raise ValueError("German Wadoku archive LICENSE differs")
        export_row = connection.execute(
            "SELECT id,zip_sha256 FROM export WHERE run_id=? AND output_path=?",
            (run_id, str(archive.resolve())),
        ).fetchone()
        if export_row is None or export_row["zip_sha256"] != sha256_file(archive):
            raise ValueError("German Wadoku archive has no matching PostgreSQL export audit")
        connection.execute("UPDATE export SET verified=1 WHERE id=?", (export_row["id"],))
        audit(connection, "verify_wadoku_de", "export", export_row["id"],
              {"term_rows": term_rows, "zip_sha256": export_row["zip_sha256"], **schema})
        connection.commit()
        return {"run_id": run_id, "term_rows": term_rows,
                "zip_sha256": export_row["zip_sha256"], **schema}
    finally:
        connection.close()
        database.close()


def _localized_entries(
    connection: Any, run_id: int, *, complete_only: bool,
) -> Iterator[tuple[dict[str, Any], dict[int, str]]]:
    where_complete = """AND NOT EXISTS (
      SELECT 1 FROM translation_unit pending
      WHERE pending.run_id=ra.run_id AND pending.article_id=ra.article_id
      AND NOT EXISTS (
        SELECT 1 FROM translation accepted
        WHERE accepted.run_id=pending.run_id AND accepted.unit_id=pending.id
          AND accepted.accepted=1
      )
    )""" if complete_only else ""
    rows = connection.execute(
        f"""SELECT a.raw_json,COALESCE(targets.targets,'{{}}'::jsonb) targets
        FROM run_article ra JOIN article a ON a.id=ra.article_id
        LEFT JOIN LATERAL (
          SELECT jsonb_object_agg(
            substring(tu.json_pointer from '/blocks/([0-9]+)$'),accepted.target_text
          ) targets
          FROM translation_unit tu
          JOIN LATERAL (
            SELECT t.target_text FROM translation t
            WHERE t.run_id=tu.run_id AND t.unit_id=tu.id AND t.accepted=1
            ORDER BY t.id DESC LIMIT 1
          ) accepted ON TRUE
          WHERE tu.run_id=ra.run_id AND tu.article_id=ra.article_id
        ) targets ON TRUE
        WHERE ra.run_id=? {where_complete}
        ORDER BY a.entry_ordinal""",
        (run_id,),
    )
    for row in rows:
        raw_targets = row["targets"]
        if isinstance(raw_targets, str):
            raw_targets = json.loads(raw_targets)
        yield json.loads(row["raw_json"]), {
            int(index): target for index, target in raw_targets.items()
        }


def _accepted_coverage(connection: Any, run_id: int) -> dict[str, int]:
    row = connection.execute(
        """SELECT COUNT(*) units,
        COUNT(*) FILTER (WHERE accepted_count=1) accepted_units,
        COUNT(*) FILTER (WHERE accepted_count=0) missing_units,
        COUNT(*) FILTER (WHERE accepted_count>1) duplicate_units
        FROM (
          SELECT tu.id,COUNT(t.id) accepted_count
          FROM translation_unit tu LEFT JOIN translation t
            ON t.run_id=tu.run_id AND t.unit_id=tu.id AND t.accepted=1
          WHERE tu.run_id=? GROUP BY tu.id
        ) coverage""",
        (run_id,),
    ).fetchone()
    assert row is not None
    return {key: int(row[key]) for key in (
        "units", "accepted_units", "missing_units", "duplicate_units",
    )}


def _record_archive_export(
    connection: Any, run_id: int, output: Path, export_id: int, is_new: bool,
    report: dict[str, Any], event_type: str,
) -> None:
    manifest_hash = sha256_bytes(canonical_json(report["files"]))
    if is_new:
        connection.execute(
            """INSERT INTO export(id,run_id,output_path,manifest_sha256,zip_sha256,verified)
            VALUES (?,?,?,?,?,1)""",
            (export_id, run_id, str(output.resolve()), manifest_hash, report["zip_sha256"]),
        )
        connection.copy_rows(
            "export_file", ("export_id", "path", "sha256", "byte_count"),
            ((export_id, name, digest, report["file_bytes"][name])
             for name, digest in report["files"].items()),
        )
    else:
        recorded = connection.execute(
            "SELECT zip_sha256 FROM export WHERE id=?", (export_id,),
        ).fetchone()[0]
        if recorded != report["zip_sha256"]:
            raise ValueError("repeat Wadoku export is not byte-identical")
    audit(connection, event_type, "export", export_id, report)


def export_ru(config: Config, run_id: int, output: Path) -> dict[str, Any]:
    _require_postgresql(config)
    labels = label_catalog(config.path("paths", "controlled_labels"))
    database = Database(config)
    connection = database.connect()
    try:
        run, metadata = _run_source(connection, run_id)
        coverage = _accepted_coverage(connection, run_id)
        if coverage["missing_units"] or coverage["duplicate_units"]:
            raise ValueError(f"Russian export requires exact accepted coverage: {coverage}")
        export_id, is_new = _reserve_export_id(connection, run_id, output)
        report = build_rich_archive(
            _localized_entries(connection, run_id, complete_only=False),
            output.resolve(), language="ru", labels=labels,
            license_text=Path(metadata["license_path"]).read_bytes(),
            title=config.raw["product"]["ru"]["title"],
            revision=config.raw["product"]["ru"]["revision"],
            source_url=run["source_url"], source_sha256=run["snapshot_sha256"],
            export_audit_id=export_id,
        )
        report.update(validate_archive(output.resolve(), config.root / SCHEMA_DIR))
        _record_archive_export(
            connection, run_id, output, export_id, is_new, report, "export_wadoku_ru",
        )
        connection.commit()
        return {"run_id": run_id, "export_id": export_id,
                "output": str(output.resolve()), **coverage, **report}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def export_checkpoint(config: Config, run_id: int, output_dir: Path) -> dict[str, Any]:
    _require_postgresql(config)
    labels = label_catalog(config.path("paths", "controlled_labels"))
    database = Database(config)
    connection = database.connect()
    try:
        run, metadata = _run_source(connection, run_id)
        complete_entries = int(connection.execute(
            """SELECT COUNT(*) FROM run_article ra WHERE ra.run_id=? AND NOT EXISTS (
              SELECT 1 FROM translation_unit tu WHERE tu.run_id=ra.run_id
              AND tu.article_id=ra.article_id AND NOT EXISTS (
                SELECT 1 FROM translation t WHERE t.run_id=tu.run_id
                AND t.unit_id=tu.id AND t.accepted=1
              )
            )""", (run_id,),
        ).fetchone()[0])
        if complete_entries == 0:
            raise ValueError("checkpoint has no fully accepted entries")
        output = output_dir.resolve() / f"wadoku-jp-ru-rich-run-{run_id}-{complete_entries}-accepted.zip"
        export_id, is_new = _reserve_export_id(connection, run_id, output)
        product = config.raw["product"]["ru"]
        report = build_rich_archive(
            _localized_entries(connection, run_id, complete_only=True), output,
            language="ru", labels=labels,
            license_text=Path(metadata["license_path"]).read_bytes(),
            title=f"{product['title']} (неполный контрольный экспорт)",
            revision=f"{product['revision']}-checkpoint-run-{run_id}-{complete_entries}",
            source_url=run["source_url"], source_sha256=run["snapshot_sha256"],
            export_audit_id=export_id,
            description_note=(
                f"Incomplete accepted-only checkpoint: {complete_entries} complete entries; "
                "not a release archive."
            ),
        )
        report.update(validate_archive(output, config.root / SCHEMA_DIR))
        _record_archive_export(
            connection, run_id, output, export_id, is_new, report,
            "export_wadoku_checkpoint",
        )
        connection.commit()
        return {"run_id": run_id, "export_id": export_id,
                "complete_entries": complete_entries, "output": str(output), **report}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def verify_ru(config: Config, run_id: int, archive: Path) -> dict[str, Any]:
    _require_postgresql(config)
    schema = validate_archive(archive.resolve(), config.root / SCHEMA_DIR)
    database = Database(config)
    connection = database.connect()
    try:
        _run, metadata = _run_source(connection, run_id)
        coverage = _accepted_coverage(connection, run_id)
        if coverage["missing_units"] or coverage["duplicate_units"]:
            raise ValueError(f"Russian archive lacks exact accepted coverage: {coverage}")
        blocking_issues = int(connection.execute(
            """SELECT COUNT(*) FROM validation_issue WHERE run_id=?
            AND severity='error' AND resolved_at IS NULL AND waiver_reason IS NULL""",
            (run_id,),
        ).fetchone()[0])
        if blocking_issues:
            raise ValueError(f"Russian archive run has {blocking_issues} blocking issues")
        with zipfile.ZipFile(archive) as source:
            bad = source.testzip()
            if bad is not None:
                raise ValueError(f"corrupt Wadoku archive member: {bad}")
            index = json.loads(source.read("index.json"))
            product = config.raw["product"]["ru"]
            if (index.get("sourceLanguage"), index.get("targetLanguage")) != ("ja", "ru"):
                raise ValueError("Russian Wadoku archive language metadata differs")
            if (index.get("title"), index.get("revision")) != (
                product["title"], product["revision"],
            ):
                raise ValueError("Russian Wadoku archive product metadata differs")
            if source.read("LICENSE") != Path(metadata["license_path"]).read_bytes():
                raise ValueError("Russian Wadoku archive LICENSE differs")
            term_names = sorted(
                (name for name in source.namelist() if name.startswith("term_bank_")),
                key=lambda value: int(value.removeprefix("term_bank_").removesuffix(".json")),
            )
            term_rows = russian_spans = german_spans = 0
            for name in term_names:
                raw = source.read(name)
                term_rows += len(json.loads(raw))
                russian_spans += raw.count(b'"lang":"ru"')
                german_spans += raw.count(b'"lang":"de"')
            if german_spans:
                raise ValueError(f"Russian Wadoku archive contains {german_spans} German spans")
            if russian_spans == 0:
                raise ValueError("Russian Wadoku archive contains no Russian spans")
        export_row = connection.execute(
            "SELECT id,zip_sha256 FROM export WHERE run_id=? AND output_path=?",
            (run_id, str(archive.resolve())),
        ).fetchone()
        if export_row is None or export_row["zip_sha256"] != sha256_file(archive):
            raise ValueError("Russian Wadoku archive has no matching PostgreSQL export audit")
        connection.execute("UPDATE export SET verified=1 WHERE id=?", (export_row["id"],))
        result = {
            "run_id": run_id, "term_rows": term_rows, "russian_spans": russian_spans,
            "german_spans": german_spans, "blocking_issues": blocking_issues,
            "zip_sha256": export_row["zip_sha256"], **coverage, **schema,
        }
        audit(connection, "verify_wadoku_ru", "export", export_row["id"], result)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def compare_archives(de_archive: Path, ru_archive: Path) -> dict[str, Any]:
    def numbered(source: zipfile.ZipFile, prefix: str) -> list[str]:
        return sorted(
            (name for name in source.namelist() if name.startswith(prefix)),
            key=lambda value: int(value.removeprefix(prefix).removesuffix(".json")),
        )

    with zipfile.ZipFile(de_archive) as de, zipfile.ZipFile(ru_archive) as ru:
        if de.testzip() is not None or ru.testzip() is not None:
            raise ValueError("cannot compare a corrupt Wadoku archive")
        if de.read("LICENSE") != ru.read("LICENSE"):
            raise ValueError("German and Russian license files differ")
        de_terms, ru_terms = numbered(de, "term_bank_"), numbered(ru, "term_bank_")
        de_meta, ru_meta = numbered(de, "term_meta_bank_"), numbered(ru, "term_meta_bank_")
        de_tags, ru_tags = numbered(de, "tag_bank_"), numbered(ru, "tag_bank_")
        if list(map(len, (de_terms, de_meta, de_tags))) != list(map(len, (ru_terms, ru_meta, ru_tags))):
            raise ValueError("German and Russian bank counts differ")
        term_rows = 0
        for de_name, ru_name in zip(de_terms, ru_terms, strict=True):
            de_rows, ru_rows = json.loads(de.read(de_name)), json.loads(ru.read(ru_name))
            if len(de_rows) != len(ru_rows):
                raise ValueError(f"term row count differs in {de_name} and {ru_name}")
            for offset, (de_row, ru_row) in enumerate(zip(de_rows, ru_rows, strict=True)):
                invariant = (0, 1, 2, 3, 4, 6, 7)
                if any(de_row[index] != ru_row[index] for index in invariant):
                    raise ValueError(f"term identity differs at {de_name}:{offset}")
                if localized_archive_shape(de_row[5]) != localized_archive_shape(ru_row[5]):
                    raise ValueError(f"term structure differs at {de_name}:{offset}")
            term_rows += len(de_rows)
        meta_rows = 0
        for de_name, ru_name in zip(de_meta, ru_meta, strict=True):
            de_rows, ru_rows = json.loads(de.read(de_name)), json.loads(ru.read(ru_name))
            if de_rows != ru_rows:
                raise ValueError(f"pronunciation metadata differs in {de_name} and {ru_name}")
            meta_rows += len(de_rows)
        tag_rows = 0
        for de_name, ru_name in zip(de_tags, ru_tags, strict=True):
            de_rows, ru_rows = json.loads(de.read(de_name)), json.loads(ru.read(ru_name))
            if len(de_rows) != len(ru_rows):
                raise ValueError(f"tag row count differs in {de_name} and {ru_name}")
            for offset, (de_row, ru_row) in enumerate(zip(de_rows, ru_rows, strict=True)):
                if de_row[:3] != ru_row[:3] or de_row[4:] != ru_row[4:]:
                    raise ValueError(f"tag identity differs at {de_name}:{offset}")
            tag_rows += len(de_rows)
    return {
        "passed": True, "term_rows": term_rows, "term_meta_rows": meta_rows,
        "tag_rows": tag_rows, "de_sha256": sha256_file(de_archive),
        "ru_sha256": sha256_file(ru_archive),
    }


def reuse_v1(config: Config, source_run_id: int, target_run_id: int,
             apply: bool) -> dict[str, Any]:
    _require_postgresql(config)
    database = Database(config)
    connection = database.connect()
    try:
        _run_source(connection, target_run_id)
        old_rows = connection.execute(
            """SELECT a.expression,a.reading,tu.source_text,t.target_text,
            t.id source_translation_id,t.attempt_id,tu.source_sha256
            FROM translation t JOIN translation_unit tu ON tu.id=t.unit_id
            JOIN article a ON a.id=tu.article_id
            WHERE t.run_id=? AND t.accepted=1 ORDER BY t.id""",
            (source_run_id,),
        )
        candidate_rows: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
        source_rows = cardinality_rejected = 0
        for row in old_rows:
            source_rows += 1
            pair = decode_v1_single_block(row["source_text"], row["target_text"])
            if pair is None:
                cardinality_rejected += 1
                continue
            german, russian = pair
            key = (unicodedata.normalize("NFC", row["expression"]),
                   unicodedata.normalize("NFC", row["reading"]), german)
            candidate_rows.append((key, {
                "russian": russian, "source_translation_id": int(row["source_translation_id"]),
                "attempt_id": row["attempt_id"], "source_sha256": row["source_sha256"],
            }))
        frequencies = Counter(key for key, _item in candidate_rows)
        duplicate_keys = {key for key, count in frequencies.items() if count > 1}
        unique = {key: item for key, item in candidate_rows if key not in duplicate_keys}
        target_run = connection.execute(
            "SELECT dictionary_snapshot_id FROM run WHERE id=?", (target_run_id,),
        ).fetchone()
        new_counts: Counter[tuple[str, str, str]] = Counter()
        matched: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        for row in connection.execute(
            """SELECT a.raw_json,a.expression,a.reading FROM run_article ra
            JOIN article a ON a.id=ra.article_id WHERE ra.run_id=? ORDER BY a.entry_ordinal""",
            (target_run_id,),
        ):
            value = json.loads(row["raw_json"])
            expression = unicodedata.normalize("NFC", row["expression"])
            reading = unicodedata.normalize("NFC", row["reading"])
            for block in value["blocks"]:
                if not block["has_translatable_text"] or block["protected_fragments"]:
                    continue
                key = (expression, reading, block["source_text"])
                if key not in unique:
                    continue
                new_counts[key] += 1
                matched[key] = (translation_unit_id(WADOKU_ARCHIVE_SHA256, value["entry_id"], block), unique[key])
        reusable = [(key, *matched[key]) for key, count in new_counts.items() if count == 1]
        existing_reuse = int(connection.execute(
            """SELECT COUNT(*) FROM translation_reuse tr JOIN translation t
            ON t.id=tr.target_translation_id WHERE t.run_id=? AND tr.mapping_rule='exact_single_block_v1'""",
            (target_run_id,),
        ).fetchone()[0])
        applied = 0
        if apply and reusable:
            connection.execute(
                """CREATE TEMP TABLE wadoku_reuse_candidate(
                unit_id TEXT PRIMARY KEY,source_translation_id BIGINT NOT NULL,
                attempt_id TEXT NOT NULL,target_text TEXT NOT NULL,target_sha256 TEXT NOT NULL)
                ON COMMIT DROP"""
            )
            connection.copy_rows(
                "wadoku_reuse_candidate",
                ("unit_id", "source_translation_id", "attempt_id", "target_text", "target_sha256"),
                ((unit_id, item["source_translation_id"], item["attempt_id"], item["russian"],
                  sha256_bytes(item["russian"].encode())) for _key, unit_id, item in reusable),
            )
            applied = connection.execute(
                """INSERT INTO translation
                (run_id,unit_id,attempt_id,target_text,confidence,review_reason,target_sha256,
                 accepted,accepted_at,acceptance_method)
                SELECT ?,c.unit_id,c.attempt_id,c.target_text,'high',NULL,c.target_sha256,
                       1,CURRENT_TIMESTAMP,'v1_exact_reuse'
                FROM wadoku_reuse_candidate c JOIN translation_unit tu ON tu.id=c.unit_id
                WHERE tu.run_id=? AND NOT EXISTS(
                  SELECT 1 FROM translation t WHERE t.run_id=? AND t.unit_id=c.unit_id AND t.accepted=1)
                ON CONFLICT(unit_id,attempt_id) DO NOTHING""",
                (target_run_id, target_run_id, target_run_id),
            ).rowcount
            connection.execute(
                """INSERT INTO translation_reuse
                (target_translation_id,source_translation_id,source_segment_index,mapping_rule)
                SELECT t.id,c.source_translation_id,0,'exact_single_block_v1'
                FROM wadoku_reuse_candidate c JOIN translation t
                  ON t.run_id=? AND t.unit_id=c.unit_id AND t.accepted=1
                ON CONFLICT(target_translation_id) DO NOTHING""", (target_run_id,),
            )
            connection.execute(
                """UPDATE translation_unit SET status='translated' WHERE run_id=? AND EXISTS(
                SELECT 1 FROM translation t WHERE t.run_id=? AND t.unit_id=translation_unit.id
                AND t.accepted=1)""", (target_run_id, target_run_id),
            )
        total_units = int(connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (target_run_id,),
        ).fetchone()[0])
        result = {
            "source_run_id": source_run_id, "target_run_id": target_run_id,
            "source_rows": source_rows, "unique_candidate_keys": len(unique),
            "duplicate_key_groups_excluded": len(duplicate_keys),
            "duplicate_rows_excluded": sum(frequencies[key] for key in duplicate_keys),
            "source_cardinality_mismatches": cardinality_rejected,
            "new_xml_mismatches": len(unique) - len(new_counts),
            "repeated_new_blocks_excluded": sum(count > 1 for count in new_counts.values()),
            "reusable_blocks": len(reusable), "previously_applied_blocks": existing_reuse,
            "applied_blocks": applied, "remaining_blocks": total_units - existing_reuse - applied,
        }
        output = config.work_dir / "reuse-v1-report.json"
        atomic_write(output, canonical_json(result) + b"\n")
        result["output"] = str(output)
        if apply:
            audit(connection, "reuse_wadoku_v1", "run", target_run_id, result)
            connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def _ready_filter() -> str:
    return """tu.run_id=? AND tu.status='ready' AND NOT EXISTS(
      SELECT 1 FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
      WHERE bi.unit_id=tu.id AND b.kind='translation')"""


def _pilot_eligible_filter() -> str:
    return """OCTET_LENGTH(a.raw_json)<=32000 AND (
      SELECT COUNT(*) FROM translation_unit pending
      WHERE pending.run_id=tu.run_id AND pending.article_id=a.id AND pending.status='ready'
      AND NOT EXISTS(
        SELECT 1 FROM batch_item pending_bi JOIN batch pending_b ON pending_b.id=pending_bi.batch_id
        WHERE pending_bi.unit_id=pending.id AND pending_b.kind='translation'
      )
    )<=100"""


def _pilot_article_ids(connection: Any, run_id: int, count: int) -> tuple[set[int], dict[str, Any]]:
    selected: list[int] = []

    def add(rows: Any) -> None:
        for row in rows:
            article_id = int(row[0])
            if article_id not in selected and len(selected) < count:
                selected.append(article_id)

    placeholders = ",".join("?" for _ in FIXED_SAMPLE)
    add(connection.execute(
        f"""SELECT DISTINCT a.id FROM translation_unit tu JOIN article a ON a.id=tu.article_id
        WHERE {_ready_filter()} AND {_pilot_eligible_filter()}
        AND a.expression IN ({placeholders}) ORDER BY a.id""",
        (run_id, *FIXED_SAMPLE),
    ))
    add(connection.execute(
        f"""SELECT DISTINCT ON (tu.role) a.id FROM translation_unit tu
        JOIN article a ON a.id=tu.article_id WHERE {_ready_filter()}
        AND {_pilot_eligible_filter()}
        ORDER BY tu.role,a.entry_ordinal""", (run_id,),
    ))
    add(connection.execute(
        f"""SELECT DISTINCT ON (placeholder_class) article_id FROM (
          SELECT a.id article_id,a.entry_ordinal,CASE
            WHEN jsonb_array_length(tu.protected_tokens_json::jsonb)=0 THEN 0
            WHEN jsonb_array_length(tu.protected_tokens_json::jsonb)=1 THEN 1 ELSE 2 END placeholder_class
          FROM translation_unit tu JOIN article a ON a.id=tu.article_id
          WHERE {_ready_filter()} AND {_pilot_eligible_filter()}
        ) classes ORDER BY placeholder_class,entry_ordinal""", (run_id,),
    ))
    add(connection.execute(
        f"""SELECT a.id FROM translation_unit tu JOIN article a ON a.id=tu.article_id
        WHERE {_ready_filter()} AND {_pilot_eligible_filter()} GROUP BY a.id,a.entry_ordinal
        HAVING SUM(tu.byte_count)<=20000 ORDER BY SUM(tu.byte_count) DESC,a.entry_ordinal LIMIT 20""",
        (run_id,),
    ))
    add(connection.execute(
        f"""SELECT a.id FROM translation_unit tu JOIN article a ON a.id=tu.article_id
        WHERE {_ready_filter()} AND {_pilot_eligible_filter()} GROUP BY a.id,a.entry_ordinal
        ORDER BY SUM(tu.byte_count),a.entry_ordinal LIMIT 20""", (run_id,),
    ))
    add(connection.execute(
        f"""SELECT DISTINCT a.id FROM translation_unit tu JOIN article a ON a.id=tu.article_id
        WHERE {_ready_filter()} AND {_pilot_eligible_filter()} ORDER BY a.id LIMIT ?""",
        (run_id, count * 2),
    ))
    if len(selected) != count:
        raise ValueError(f"pilot needs {count} ready articles; selected {len(selected)}")
    selected_set = set(selected)
    coverage_rows = connection.execute(
        """SELECT tu.role,tu.protected_tokens_json,a.expression FROM translation_unit tu
        JOIN article a ON a.id=tu.article_id WHERE tu.run_id=? AND tu.article_id=ANY(?)
        ORDER BY a.entry_ordinal,tu.json_pointer""", (run_id, selected),
    ).fetchall()
    role_counts = Counter(row["role"] for row in coverage_rows)
    placeholder_counts = Counter(
        min(len(json.loads(row["protected_tokens_json"])), 2) for row in coverage_rows
    )
    fixed_pending = sorted({row["expression"] for row in coverage_rows if row["expression"] in FIXED_SAMPLE})
    return selected_set, {
        "article_ids": selected, "roles": dict(sorted(role_counts.items())),
        "placeholder_classes": {str(key): value for key, value in sorted(placeholder_counts.items())},
        "fixed_sample_pending_and_selected": fixed_pending,
    }


def make_wadoku_batches(
    config: Config, run_id: int, max_articles: int, max_units: int, max_bytes: int,
    pilot_batches: int | None, pilot_only: bool, pilot_output: Path | None,
) -> dict[str, Any]:
    _require_postgresql(config)
    database = Database(config)
    connection = database.connect()
    try:
        _run_source(connection, run_id)
        terminology = json.loads(config.path("paths", "terminology").read_text(encoding="utf-8"))
        article_ids = None
        selection: dict[str, Any] | None = None
        singleton = config.raw["batch"]["singleton_threshold_bytes"]
        if pilot_only:
            if pilot_batches is None or pilot_output is None:
                raise ValueError("pilot-only batching requires --pilot-batches and --pilot-output")
            article_ids, selection = _pilot_article_ids(connection, run_id, pilot_batches)
            singleton = 0
        result = shared_make_batches(
            connection, run_id, config.work_dir / "inbox", terminology,
            max_articles, max_bytes, max_units, singleton,
            config.raw["batch"]["hard_max_article_bytes"],
            config.raw["batch"]["hard_max_article_units"], article_ids=article_ids,
        )
        if pilot_only:
            batch_rows = connection.execute(
                """SELECT b.id,b.unit_count,b.article_count FROM batch b WHERE b.run_id=?
                AND EXISTS(SELECT 1 FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
                           WHERE bi.batch_id=b.id AND tu.article_id=ANY(?)) ORDER BY b.id""",
                (run_id, list(article_ids or ())),
            ).fetchall()
            if len(batch_rows) != pilot_batches:
                raise ValueError(f"pilot created {len(batch_rows)} batches, expected {pilot_batches}")
            assert selection is not None and pilot_output is not None
            selection.update({
                "schema_version": 1, "run_id": run_id,
                "batch_ids": [row["id"] for row in batch_rows],
                "batch_units": {row["id"]: int(row["unit_count"]) for row in batch_rows},
            })
            atomic_write(pilot_output.resolve(), canonical_json(selection) + b"\n")
            result["pilot_output"] = str(pilot_output.resolve())
            result["pilot_coverage"] = selection
        audit(connection, "make_wadoku_batches", "run", run_id,
              {"pilot_only": pilot_only, **result})
        connection.commit()
        return {"run_id": run_id, **result}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def pilot_check(
    config: Config, run_id: int, selection_path: Path, output: Path,
) -> dict[str, Any]:
    _require_postgresql(config)
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("run_id") != run_id:
        raise ValueError("pilot selection run ID differs")
    batch_ids = selection.get("batch_ids")
    if not isinstance(batch_ids, list) or len(batch_ids) != 100 or len(set(batch_ids)) != 100:
        raise ValueError("pilot selection must contain 100 unique batch IDs")
    database = Database(config)
    connection = database.connect()
    try:
        _run_source(connection, run_id)
        states = {row["id"]: row["state"] for row in connection.execute(
            "SELECT id,state FROM batch WHERE run_id=? AND id=ANY(?) ORDER BY id",
            (run_id, batch_ids),
        )}
        missing_batches = sorted(set(batch_ids) - set(states))
        state_counts = Counter(states.values())
        attempt_rows = connection.execute(
            """SELECT a.outcome,COUNT(*) attempts,
            COALESCE(SUM(a.input_tokens),0) input_tokens,
            COALESCE(SUM(a.cached_input_tokens),0) cached_input_tokens,
            COALESCE(SUM(a.output_tokens),0) output_tokens,
            COALESCE(SUM(a.total_tokens),0) total_tokens,
            MIN(a.latency_ms) min_latency_ms,MAX(a.latency_ms) max_latency_ms,
            AVG(a.latency_ms) average_latency_ms
            FROM attempt a JOIN batch b ON b.id=a.batch_id
            WHERE b.run_id=? AND b.id=ANY(?) GROUP BY a.outcome ORDER BY a.outcome""",
            (run_id, batch_ids),
        ).fetchall()
        attempts = {
            row["outcome"]: {
                "attempts": int(row["attempts"]),
                "input_tokens": int(row["input_tokens"]),
                "cached_input_tokens": int(row["cached_input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "min_latency_ms": row["min_latency_ms"],
                "max_latency_ms": row["max_latency_ms"],
                "average_latency_ms": (
                    round(float(row["average_latency_ms"]), 3)
                    if row["average_latency_ms"] is not None else None
                ),
            }
            for row in attempt_rows
        }
        selected_units = int(connection.execute(
            """SELECT COUNT(DISTINCT bi.unit_id) FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
            WHERE b.run_id=? AND b.id=ANY(?)""", (run_id, batch_ids),
        ).fetchone()[0])
        validated_units = int(connection.execute(
            """SELECT COUNT(DISTINCT bi.unit_id) FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
            WHERE b.run_id=? AND b.id=ANY(?) AND b.state='deterministic_validated'
            AND EXISTS (
              SELECT 1 FROM translation t JOIN attempt a ON a.id=t.attempt_id
              WHERE t.run_id=b.run_id AND t.unit_id=bi.unit_id
                AND a.batch_id=b.id AND a.outcome='accepted'
            )""", (run_id, batch_ids),
        ).fetchone()[0])
        issue_rows = connection.execute(
            """SELECT vi.code,COUNT(*) issues FROM validation_issue vi
            JOIN attempt a ON a.id=vi.attempt_id JOIN batch b ON b.id=a.batch_id
            WHERE b.run_id=? AND b.id=ANY(?) GROUP BY vi.code ORDER BY vi.code""",
            (run_id, batch_ids),
        ).fetchall()
        issue_counts = {row["code"]: int(row["issues"]) for row in issue_rows}
        sample_rows = connection.execute(
            """SELECT a.expression,a.reading,tu.id unit_id,tu.role,tu.source_text,
            t.target_text,t.confidence,at.id attempt_id
            FROM batch b JOIN batch_item bi ON bi.batch_id=b.id
            JOIN translation_unit tu ON tu.id=bi.unit_id JOIN article a ON a.id=tu.article_id
            LEFT JOIN LATERAL (
              SELECT candidate.* FROM translation candidate JOIN attempt selected_attempt
                ON selected_attempt.id=candidate.attempt_id
              WHERE candidate.run_id=tu.run_id AND candidate.unit_id=tu.id
                AND selected_attempt.outcome='accepted'
              ORDER BY candidate.id DESC LIMIT 1
            ) t ON TRUE
            LEFT JOIN attempt at ON at.id=t.attempt_id
            WHERE b.run_id=? AND b.id=ANY(?) AND a.expression=ANY(?)
            ORDER BY a.entry_ordinal,tu.json_pointer""",
            (run_id, batch_ids, list(FIXED_SAMPLE)),
        ).fetchall()
        fixed_sample = [dict(row) for row in sample_rows]
        fixed_expected = set(selection.get("fixed_sample_pending_and_selected", []))
        fixed_seen = {row["expression"] for row in sample_rows}
        passed = (
            not missing_batches
            and state_counts == Counter({"deterministic_validated": 100})
            and selected_units > 0
            and validated_units == selected_units
            and fixed_expected <= fixed_seen
        )
        result = {
            "schema_version": 1, "run_id": run_id, "passed": passed,
            "human_review_required": True, "batch_count": len(states),
            "missing_batches": missing_batches, "batch_states": dict(sorted(state_counts.items())),
            "selected_units": selected_units, "validated_units": validated_units,
            "attempts": attempts, "validation_issues": issue_counts,
            "protected_token_failures": issue_counts.get("protected_token_missing", 0),
            "fixed_sample": fixed_sample,
        }
        atomic_write(output.resolve(), canonical_json(result) + b"\n")
        audit(connection, "pilot_check_wadoku", "run", run_id,
              {key: value for key, value in result.items() if key != "fixed_sample"})
        connection.commit()
        if not passed:
            raise ValueError(f"Wadoku pilot gate failed; see {output.resolve()}")
        return {"output": str(output.resolve()), **result}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def progress(config: Config, run_id: int, step_id: str, event: str) -> dict[str, Any]:
    _require_postgresql(config)
    if event not in {"start", "snapshot", "finish"}:
        raise ValueError("progress event must be start, snapshot, or finish")
    database = Database(config)
    connection = database.connect()
    try:
        run, _metadata = _run_source(connection, run_id)
        row = connection.execute(
            """WITH completed_unit AS MATERIALIZED (
              SELECT unit_id FROM translation WHERE run_id=? AND accepted=1
              UNION
              SELECT bi.unit_id FROM batch_item bi JOIN batch b ON b.id=bi.batch_id
              WHERE b.run_id=? AND b.state='deterministic_validated'
            ), unit_metrics AS (
              SELECT COUNT(*) total_units,
                     COUNT(cu.unit_id) live_complete_units,
                     COUNT(DISTINCT tu.article_id) FILTER (WHERE cu.unit_id IS NULL) incomplete_articles
              FROM translation_unit tu LEFT JOIN completed_unit cu ON cu.unit_id=tu.id
              WHERE tu.run_id=?
            ), attempt_metrics AS (
              SELECT COUNT(*) FILTER (WHERE a.outcome='accepted') accepted_attempts,
                     COUNT(*) FILTER (WHERE a.outcome='rejected') rejected_attempts,
                     COUNT(*) FILTER (WHERE a.outcome='interrupted') interrupted_attempts,
                     COUNT(*) FILTER (WHERE a.outcome='claimed') active_requests,
                     COALESCE(SUM(a.input_tokens) FILTER (WHERE a.completed_at IS NOT NULL),0) input_tokens,
                     COALESCE(SUM(a.cached_input_tokens) FILTER (WHERE a.completed_at IS NOT NULL),0) cached_input_tokens,
                     COALESCE(SUM(a.output_tokens) FILTER (WHERE a.completed_at IS NOT NULL),0) output_tokens,
                     COALESCE(SUM(a.total_tokens) FILTER (WHERE a.completed_at IS NOT NULL),0) total_tokens,
                     COUNT(*) FILTER (WHERE a.completed_at IS NOT NULL AND a.total_tokens IS NULL)
                       terminal_missing_usage
              FROM attempt a JOIN batch b ON b.id=a.batch_id WHERE b.run_id=?
            )
            SELECT um.*,
              (SELECT COUNT(*) FROM run_article WHERE run_id=?) total_articles,
              (SELECT COUNT(*) FROM run_article WHERE run_id=?)-um.incomplete_articles live_complete_articles,
              (SELECT COUNT(*) FROM translation WHERE run_id=? AND accepted=1
                 AND acceptance_method='v1_exact_reuse') reused_units,
              (SELECT COUNT(DISTINCT t.unit_id) FROM translation t
                 JOIN attempt a ON a.id=t.attempt_id JOIN batch own_batch ON own_batch.id=a.batch_id
                 WHERE t.run_id=? AND own_batch.run_id=t.run_id AND a.outcome='accepted')
                 luna_complete_units,
              am.*,
              (SELECT COUNT(DISTINCT vi.attempt_id) FROM validation_issue vi
                 WHERE vi.run_id=? AND vi.attempt_id IS NOT NULL) validation_rejected_attempts
            FROM unit_metrics um CROSS JOIN attempt_metrics am""",
            (run_id, run_id, run_id, run_id, run_id, run_id, run_id, run_id, run_id),
        ).fetchone()
        assert row is not None
        now = datetime.now(timezone.utc)
        metrics = {key: int(row[key]) for key in (
            "total_units", "live_complete_units", "total_articles", "live_complete_articles",
            "reused_units", "luna_complete_units", "accepted_attempts", "rejected_attempts",
            "interrupted_attempts", "active_requests", "input_tokens", "cached_input_tokens",
            "output_tokens", "total_tokens", "terminal_missing_usage",
            "validation_rejected_attempts",
        )}
        terminal = metrics["accepted_attempts"] + metrics["rejected_attempts"]
        metrics.update({
            "remaining_units": metrics["total_units"] - metrics["live_complete_units"],
            "remaining_articles": metrics["total_articles"] - metrics["live_complete_articles"],
            "article_percentage": round(
                100 * metrics["live_complete_articles"] / metrics["total_articles"], 6,
            ) if metrics["total_articles"] else 100.0,
            "failed_query_percentage": round(
                100 * metrics["rejected_attempts"] / terminal, 6,
            ) if terminal else 0.0,
            "validation_rejection_percentage": round(
                100 * metrics["validation_rejected_attempts"] / terminal, 6,
            ) if terminal else 0.0,
            "snapshot_at": now.isoformat().replace("+00:00", "Z"),
            "step_id": step_id, "event": event,
            "run_created_at": run["created_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        start_row = connection.execute(
            """SELECT created_at FROM audit_event WHERE event_type='orchestrator_progress_start'
            AND entity_type='run' AND entity_id=? AND details_json::jsonb->>'step_id'=?
            ORDER BY id DESC LIMIT 1""", (str(run_id), step_id),
        ).fetchone()
        started = now if event == "start" or start_row is None else start_row["created_at"]
        metrics["step_started_at"] = started.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        metrics["step_elapsed_seconds"] = round((now - started).total_seconds(), 3)
        metrics["run_elapsed_seconds"] = round((now - run["created_at"]).total_seconds(), 3)
        prior_row = connection.execute(
            """SELECT created_at,details_json FROM audit_event
            WHERE event_type IN ('orchestrator_progress_snapshot','orchestrator_progress_start')
              AND entity_type='run' AND entity_id=? AND created_at<=?-INTERVAL '5 minutes'
            ORDER BY created_at DESC LIMIT 1""", (str(run_id), now),
        ).fetchone()
        rolling_rate = None
        if prior_row is not None:
            prior = json.loads(prior_row["details_json"])
            seconds = (now - prior_row["created_at"]).total_seconds()
            articles = metrics["live_complete_articles"] - int(prior.get("live_complete_articles", 0))
            if seconds >= 300 and articles >= 0:
                rolling_rate = round(articles * 60 / seconds, 3)
        metrics["rolling_articles_per_minute"] = rolling_rate
        metrics["eta_seconds"] = (
            round(metrics["remaining_articles"] * 60 / rolling_rate)
            if rolling_rate and metrics["step_elapsed_seconds"] >= 600 and terminal >= 500
            else None
        )
        audit(connection, f"orchestrator_progress_{event}", "run", run_id, metrics)
        connection.commit()
        return metrics
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def failure_report(config: Config, run_id: int, output: Path) -> dict[str, Any]:
    _require_postgresql(config)
    database = Database(config)
    connection = database.connect()
    try:
        _run_source(connection, run_id)
        rows = connection.execute(
            """SELECT b.id batch_id,b.state,b.attempt_count,b.article_count,b.unit_count,
            a.id attempt_id,a.outcome,a.error_json,a.completed_at,
            COALESCE((SELECT jsonb_agg(jsonb_build_object(
              'code',vi.code,'severity',vi.severity,'unit_id',vi.unit_id,
              'created_at',vi.created_at,'resolved_at',vi.resolved_at,
              'waiver_reason',vi.waiver_reason
            ) ORDER BY vi.id) FROM validation_issue vi WHERE vi.attempt_id=a.id),'[]'::jsonb) issues
            FROM batch b LEFT JOIN LATERAL (
              SELECT latest.* FROM attempt latest WHERE latest.batch_id=b.id
              ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1
            ) a ON TRUE
            WHERE b.run_id=? AND b.state NOT IN ('deterministic_validated','superseded')
            ORDER BY b.id""", (run_id,),
        ).fetchall()
        failures = []
        for row in rows:
            issues = row["issues"]
            if isinstance(issues, str):
                issues = json.loads(issues)
            failures.append({
                "batch_id": row["batch_id"], "state": row["state"],
                "attempt_count": int(row["attempt_count"]),
                "article_count": int(row["article_count"]),
                "unit_count": int(row["unit_count"]),
                "latest_attempt_id": row["attempt_id"], "latest_outcome": row["outcome"],
                "completed_at": (
                    row["completed_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if row["completed_at"] is not None else None
                ),
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
                "issues": issues,
                "retryable": row["state"] in {"ready", "retryable"},
                "terminal": row["state"] == "blocked",
            })
        state_counts = Counter(item["state"] for item in failures)
        result = {
            "schema_version": 1, "run_id": run_id,
            "unresolved_batches": len(failures),
            "retryable_batches": sum(item["retryable"] for item in failures),
            "terminal_batches": sum(item["terminal"] for item in failures),
            "states": dict(sorted(state_counts.items())), "batches": failures,
        }
        atomic_write(output.resolve(), canonical_json(result) + b"\n")
        audit(connection, "wadoku_failure_report", "run", run_id,
              {key: value for key, value in result.items() if key != "batches"})
        connection.commit()
        return {"output": str(output.resolve()), **result}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def replace_target(config: Config, run_id: int, input_path: Path) -> dict[str, Any]:
    _require_postgresql(config)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    expected = {"unit_id", "target_text", "actor", "reason"}
    if set(payload) != expected:
        raise ValueError(f"manual replacement fields must be exactly {sorted(expected)}")
    if not all(isinstance(payload[key], str) and payload[key].strip()
               for key in expected):
        raise ValueError("manual replacement fields must be non-empty strings")
    database = Database(config)
    connection = database.connect()
    try:
        _run_source(connection, run_id)
        unit = connection.execute(
            "SELECT * FROM translation_unit WHERE run_id=? AND id=?",
            (run_id, payload["unit_id"]),
        ).fetchone()
        if unit is None:
            raise ValueError("manual replacement unit does not belong to this run")
        issues = wadoku_target_issues(
            unit["source_text"], payload["target_text"],
            list(json.loads(unit["protected_tokens_json"])), unit["id"],
        )
        if issues:
            raise ValueError(f"manual replacement failed validation: {issues}")
        translation = connection.execute(
            """SELECT * FROM translation WHERE run_id=? AND unit_id=? AND accepted=1
            ORDER BY id DESC LIMIT 1""", (run_id, unit["id"]),
        ).fetchone()
        if translation is None:
            raise ValueError("manual replacement requires an accepted target")
        new_hash = sha256_bytes(payload["target_text"].encode())
        if (translation["target_sha256"] == new_hash
                and translation["acceptance_method"] == "manual"):
            return {"run_id": run_id, "unit_id": unit["id"], "applied": False,
                    "target_sha256": new_hash}
        mapping = canonical_json({
            "actor": payload["actor"], "reason": payload["reason"],
            "input_path": str(input_path.resolve().relative_to(config.root)),
        }).decode()
        connection.execute(
            """INSERT INTO translation_canonicalization_history
            (run_id,unit_id,translation_id,previous_target_text,previous_target_sha256,
             canonical_target_text,canonical_target_sha256,mapping_source,
             mapping_identity_json,canonicalizer_version)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, unit["id"], translation["id"], translation["target_text"],
             translation["target_sha256"], payload["target_text"], new_hash,
             "manual-wadoku-qa", mapping, "manual-wadoku-qa-v1"),
        )
        connection.execute(
            """UPDATE translation SET target_text=?,target_sha256=?,acceptance_method='manual'
            WHERE id=?""", (payload["target_text"], new_hash, translation["id"]),
        )
        result = {
            "run_id": run_id, "unit_id": unit["id"], "translation_id": translation["id"],
            "applied": True, "previous_target_sha256": translation["target_sha256"],
            "target_sha256": new_hash, "actor": payload["actor"], "reason": payload["reason"],
        }
        audit(connection, "replace_wadoku_target", "translation", translation["id"], result)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        database.close()


def _utc(value: Any) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"message": "unparseable error details"}

    secret_parts = ("password", "secret", "token", "authorization", "database_url", "dsn")

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: clean(child) for key, child in sorted(item.items())
                if not any(part in key.casefold() for part in secret_parts)
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    return clean(parsed)


def _relative_path(root: Path, value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return None


def article_log(
    config: Config, run_id: int, output: Path, summary_path: Path,
    require_status: str | None,
) -> dict[str, Any]:
    """Reconstruct the immutable per-article ledger from PostgreSQL."""
    _require_postgresql(config)
    database = Database(config)
    connection = database.connect()
    output = output.resolve()
    summary_path = summary_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        run, _metadata = _run_source(connection, run_id)
        source_hash = run["snapshot_sha256"]
        expected_articles = int(connection.execute(
            "SELECT COUNT(*) FROM run_article WHERE run_id=?", (run_id,),
        ).fetchone()[0])
        method_totals: Counter[str] = Counter()
        block_count = 0
        incomplete = record_count = 0
        entry_ids: set[int] = set()
        article_ids: set[int] = set()
        uncompressed_hash = hashlib.sha256()
        first_event: datetime | None = None
        last_event: datetime | None = None

        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as raw_file:
            temporary = Path(raw_file.name)
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as stream:
                article_rows = connection.execute(
                    """SELECT a.id article_id,a.entry_ordinal,a.expression,a.reading,a.raw_json,
                    ra.prepared_at FROM run_article ra JOIN article a ON a.id=ra.article_id
                    WHERE ra.run_id=? ORDER BY a.entry_ordinal""", (run_id,),
                )
                while page := article_rows.fetchmany(5000):
                    page_ids = [int(row["article_id"]) for row in page]
                    units_by_article: dict[int, list[Any]] = {value: [] for value in page_ids}
                    for row in connection.execute(
                        """SELECT tu.*,t.id translation_id,t.attempt_id accepted_attempt_id,
                        t.target_text,t.target_sha256,t.confidence,t.accepted_at,t.created_at translation_created_at,
                        t.acceptance_method,tr.source_translation_id,tr.source_segment_index,
                        tr.mapping_rule,tr.created_at reuse_created_at,st.run_id source_run_id,
                        st.attempt_id source_attempt_id,st.target_sha256 source_target_sha256,
                        su.source_sha256 reused_source_sha256
                        FROM translation_unit tu LEFT JOIN LATERAL (
                          SELECT chosen.* FROM translation chosen
                          WHERE chosen.run_id=tu.run_id AND chosen.unit_id=tu.id AND chosen.accepted=1
                          ORDER BY chosen.id DESC LIMIT 1
                        ) t ON TRUE LEFT JOIN translation_reuse tr ON tr.target_translation_id=t.id
                        LEFT JOIN translation st ON st.id=tr.source_translation_id
                        LEFT JOIN translation_unit su ON su.id=st.unit_id
                        WHERE tu.run_id=? AND tu.article_id=ANY(?)
                        ORDER BY tu.article_id,tu.json_pointer""", (run_id, page_ids),
                    ):
                        units_by_article[int(row["article_id"])].append(row)

                    history_by_unit: dict[str, list[dict[str, Any]]] = {}
                    for row in connection.execute(
                        """SELECT h.* FROM translation_canonicalization_history h
                        JOIN translation_unit tu ON tu.id=h.unit_id
                        WHERE h.run_id=? AND tu.article_id=ANY(?) ORDER BY h.id""",
                        (run_id, page_ids),
                    ):
                        identity = json.loads(row["mapping_identity_json"])
                        history_by_unit.setdefault(row["unit_id"], []).append({
                            "actor": identity.get("actor"), "reason": identity.get("reason"),
                            "previous_target_sha256": row["previous_target_sha256"],
                            "replacement_target_sha256": row["canonical_target_sha256"],
                            "created_at": _utc(row["created_at"]),
                        })

                    attempts_by_article: dict[int, list[dict[str, Any]]] = {value: [] for value in page_ids}
                    for row in connection.execute(
                        """SELECT DISTINCT tu.article_id,a.* FROM batch_item bi
                        JOIN translation_unit tu ON tu.id=bi.unit_id
                        JOIN attempt a ON a.batch_id=bi.batch_id
                        JOIN batch b ON b.id=a.batch_id
                        WHERE b.run_id=? AND tu.article_id=ANY(?)
                        ORDER BY tu.article_id,a.created_at,a.id""", (run_id, page_ids),
                    ):
                        article_id = int(row["article_id"])
                        attempts_by_article[article_id].append({
                            "batch_id": row["batch_id"], "attempt_id": row["id"],
                            "worker_id": row["worker_id"], "configured_model": row["model"],
                            "effective_model": row["effective_model_id"],
                            "reasoning_effort": row["reasoning_effort"], "transport": row["transport"],
                            "prompt_sha256": row["prompt_sha256"],
                            "api_request_id": row["api_request_id"], "api_custom_id": row["api_custom_id"],
                            "api_job_id": row["api_job_id"], "claim_time": _utc(row["created_at"]),
                            "dispatch_time": _utc(row["dispatched_at"]),
                            "completion_time": _utc(row["completed_at"]), "outcome": row["outcome"],
                            "finish_reason": row["finish_reason"], "status_reason": row["status_reason"],
                            "latency_ms": row["latency_ms"], "input_tokens": row["input_tokens"],
                            "cached_input_tokens": row["cached_input_tokens"],
                            "output_tokens": row["output_tokens"], "total_tokens": row["total_tokens"],
                            "usage_scope": "batch_attempt", "error": _safe_json(row["error_json"]),
                            "request_path": _relative_path(config.root, row["request_path"]),
                            "response_path": _relative_path(config.root, row["response_path"]),
                        })

                    issues_by_article: dict[int, list[dict[str, Any]]] = {value: [] for value in page_ids}
                    for row in connection.execute(
                        """SELECT DISTINCT related.article_id,vi.* FROM validation_issue vi
                        JOIN LATERAL (
                          SELECT tu.article_id FROM translation_unit tu
                          WHERE tu.run_id=? AND tu.id=vi.unit_id
                          UNION
                          SELECT tu.article_id FROM attempt at
                          JOIN batch_item bi ON bi.batch_id=at.batch_id
                          JOIN translation_unit tu ON tu.id=bi.unit_id
                          WHERE tu.run_id=? AND at.id=vi.attempt_id
                        ) related ON TRUE
                        WHERE vi.run_id=? AND related.article_id=ANY(?)
                        ORDER BY vi.id,related.article_id""", (run_id, run_id, run_id, page_ids),
                    ):
                        issue = {
                            "issue_id": int(row["id"]), "unit_id": row["unit_id"],
                            "attempt_id": row["attempt_id"], "validator": row["validator"],
                            "severity": row["severity"], "code": row["code"],
                            "details": json.loads(row["details_json"]),
                            "created_at": _utc(row["created_at"]),
                            "resolved_at": _utc(row["resolved_at"]),
                            "waiver_reason": row["waiver_reason"],
                        }
                        issues_by_article[int(row["article_id"])].append(issue)

                    for article in page:
                        article_id = int(article["article_id"])
                        raw = json.loads(article["raw_json"])
                        units = units_by_article[article_id]
                        unit_by_index = {int(row["json_pointer"].rsplit("/", 1)[1]): row for row in units}
                        expected_unit_indices = {
                            index for index, block in enumerate(raw["blocks"])
                            if block["has_translatable_text"]
                        }
                        if set(unit_by_index) != expected_unit_indices or len(unit_by_index) != len(units):
                            raise ValueError(f"translation-unit block coverage differs for article {article_id}")
                        accepted_times = [row["accepted_at"] for row in units if row["accepted_at"]]
                        complete = all(row["translation_id"] is not None for row in units)
                        completion = (max(accepted_times) if units and complete else
                                      article["prepared_at"] if not units else None)
                        blocks: list[dict[str, Any]] = []
                        for index, source_block in enumerate(raw["blocks"]):
                            block_count += 1
                            unit = unit_by_index.get(index)
                            if unit is None:
                                method = "source_preserved"
                                method_totals[method] += 1
                                prompt_hash = sha256_bytes(source_block["prompt_text"].encode())
                                blocks.append({
                                    "block_index": index, "xml_path": source_block["xml_path"],
                                    "role": source_block["role"],
                                    "source_text": source_block["source_text"],
                                    "prompt_text": source_block["prompt_text"],
                                    "target_text": source_block["prompt_text"],
                                    "source_sha256": sha256_bytes(source_block["source_text"].encode()),
                                    "prompt_sha256": prompt_hash, "target_sha256": prompt_hash,
                                    "translation_id": None,
                                    "acceptance_time": _utc(article["prepared_at"]), "confidence": None,
                                    "final_method": method,
                                })
                                continue
                            raw_method = unit["acceptance_method"]
                            method = {"manual": "manual_replacement"}.get(raw_method, raw_method or "luna")
                            if method not in {"v1_exact_reuse", "luna", "manual_replacement"}:
                                raise ValueError(f"invalid final method {method!r} for {unit['id']}")
                            if unit["translation_id"] is not None:
                                method_totals[method] += 1
                            block = {
                                "block_index": index, "xml_path": source_block["xml_path"],
                                "role": source_block["role"], "unit_id": unit["id"],
                                "source_text": source_block["source_text"],
                                "prompt_text": unit["source_text"], "target_text": unit["target_text"],
                                "source_sha256": sha256_bytes(source_block["source_text"].encode()),
                                "prompt_sha256": unit["source_sha256"],
                                "target_sha256": unit["target_sha256"],
                                "translation_id": unit["translation_id"],
                                "acceptance_time": _utc(unit["accepted_at"]),
                                "confidence": unit["confidence"], "final_method": method,
                            }
                            if method == "v1_exact_reuse":
                                if unit["translation_id"] is not None and unit["source_translation_id"] is None:
                                    raise ValueError(f"missing reuse provenance for {unit['id']}")
                                block["reuse"] = {
                                    "source_run_id": unit["source_run_id"],
                                    "source_translation_id": unit["source_translation_id"],
                                    "source_attempt_id": unit["source_attempt_id"],
                                    "mapping_rule": unit["mapping_rule"],
                                    "reuse_time": _utc(unit["reuse_created_at"]),
                                    "reused_source_sha256": unit["reused_source_sha256"],
                                    "source_target_sha256": unit["source_target_sha256"],
                                    "current_source_sha256": unit["source_sha256"],
                                }
                            elif method == "luna":
                                if unit["translation_id"] is not None and not unit["accepted_attempt_id"]:
                                    raise ValueError(f"missing Luna attempt provenance for {unit['id']}")
                                block["accepted_attempt_id"] = unit["accepted_attempt_id"]
                            elif method == "manual_replacement":
                                block["canonicalization_history"] = history_by_unit.get(unit["id"], [])
                                if unit["translation_id"] is not None and not block["canonicalization_history"]:
                                    raise ValueError(f"missing manual replacement history for {unit['id']}")
                            blocks.append(block)

                        if not complete:
                            incomplete += 1
                        if raw["entry_id"] in entry_ids or article_id in article_ids:
                            raise ValueError("duplicate Wadoku entry or article ID in article log")
                        entry_ids.add(raw["entry_id"])
                        article_ids.add(article_id)
                        attempts = attempts_by_article[article_id]
                        for attempt in attempts:
                            times = [attempt["claim_time"], attempt["dispatch_time"], attempt["completion_time"]]
                            present = [value for value in times if value is not None]
                            if present != sorted(present):
                                raise ValueError(f"illegal attempt timestamp order: {attempt['attempt_id']}")
                        event_times = [article["prepared_at"], completion]
                        for attempt in attempts:
                            event_times.extend([
                                datetime.fromisoformat(value.replace("Z", "+00:00"))
                                for value in (attempt["claim_time"], attempt["dispatch_time"], attempt["completion_time"])
                                if value
                            ])
                        for value in (time for time in event_times if time is not None):
                            first_event = value if first_event is None else min(first_event, value)
                            last_event = value if last_event is None else max(last_event, value)
                        record = {
                            "schema_version": 1, "run_id": run_id, "source_snapshot_sha256": source_hash,
                            "wadoku_entry_id": raw["entry_id"], "article_id": article_id,
                            "expression": article["expression"], "reading": article["reading"],
                            "source_ordinal": int(article["entry_ordinal"]),
                            "run_created_at": _utc(run["created_at"]),
                            "prepared_at": _utc(article["prepared_at"]),
                            "status": "complete" if complete else "in_progress",
                            "completion_time": _utc(completion), "unit_count": len(units),
                            "accepted_unit_count": sum(row["translation_id"] is not None for row in units),
                            "blocks": blocks, "attempts": attempts,
                            "validation_issues": issues_by_article[article_id],
                        }
                        encoded = canonical_json(record) + b"\n"
                        stream.write(encoded)
                        uncompressed_hash.update(encoded)
                        record_count += 1

        if record_count != expected_articles or len(entry_ids) != expected_articles:
            raise ValueError(f"article log coverage differs: {record_count}/{expected_articles}")
        status = "complete" if incomplete == 0 else "in_progress"
        if require_status is not None and status != require_status:
            raise ValueError(f"article log status is {status}; required {require_status}")
        os.replace(temporary, output)
        temporary = None

        attempt_summary = connection.execute(
            """SELECT a.outcome,COUNT(*) attempts,COALESCE(SUM(a.input_tokens),0) input_tokens,
            COALESCE(SUM(a.cached_input_tokens),0) cached_input_tokens,
            COALESCE(SUM(a.output_tokens),0) output_tokens,COALESCE(SUM(a.total_tokens),0) total_tokens,
            COUNT(*) FILTER (WHERE a.completed_at IS NOT NULL AND a.total_tokens IS NULL) missing_usage
            FROM attempt a JOIN batch b ON b.id=a.batch_id WHERE b.run_id=?
            GROUP BY a.outcome ORDER BY a.outcome""", (run_id,),
        ).fetchall()
        attempts_by_outcome = {row["outcome"]: int(row["attempts"]) for row in attempt_summary}
        terminal = sum(count for outcome, count in attempts_by_outcome.items() if outcome != "claimed")
        rejected = attempts_by_outcome.get("rejected", 0)
        validation_rejected = int(connection.execute(
            """SELECT COUNT(DISTINCT vi.attempt_id) FROM validation_issue vi
            JOIN attempt a ON a.id=vi.attempt_id JOIN batch b ON b.id=a.batch_id
            WHERE b.run_id=?""", (run_id,),
        ).fetchone()[0])
        summary = {
            "schema_version": 1, "run_id": run_id, "source_snapshot_sha256": source_hash,
            "status": status, "article_count": record_count, "incomplete_record_count": incomplete,
            "block_count": block_count, "blocks_by_final_method": dict(sorted(method_totals.items())),
            "attempts_by_outcome": attempts_by_outcome,
            "failed_query_percentage": round(100 * rejected / terminal, 6) if terminal else 0.0,
            "validation_rejection_percentage": round(100 * validation_rejected / terminal, 6) if terminal else 0.0,
            "tokens": {key: sum(int(row[key]) for row in attempt_summary) for key in
                       ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")},
            "terminal_attempts_missing_usage": sum(int(row["missing_usage"]) for row in attempt_summary),
            "first_event_time": _utc(first_event), "last_event_time": _utc(last_event),
            "article_log_path": str(output), "article_log_gzip_sha256": sha256_file(output),
            "article_log_uncompressed_sha256": uncompressed_hash.hexdigest(),
        }
        summary_bytes = canonical_json(summary) + b"\n"
        atomic_write(summary_path, summary_bytes)
        summary_hash = sha256_bytes(summary_bytes)
        result = {**summary, "summary_path": str(summary_path), "summary_sha256": summary_hash}
        audit(connection, "wadoku_article_log", "run", run_id, result)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
        database.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    report = commands.add_parser("source-report")
    report.add_argument("--source", type=Path, required=True)
    report.add_argument("--xsd", type=Path, required=True)
    report.add_argument("--labels", type=Path, required=True)
    report.add_argument("--subentry-groups", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("db-check")
    check.add_argument("--config", type=Path, required=True)
    check.add_argument("--require-schema-version", type=int)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--config", type=Path, required=True)
    prepare_parser.add_argument("--archive", type=Path, required=True)
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--xsd", type=Path, required=True)
    prepare_parser.add_argument("--license", type=Path, required=True)
    export_parser = commands.add_parser("export-de")
    export_parser.add_argument("--config", type=Path, required=True)
    export_parser.add_argument("--run-id", type=int, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify-de")
    verify_parser.add_argument("--config", type=Path, required=True)
    verify_parser.add_argument("--run-id", type=int, required=True)
    verify_parser.add_argument("--archive", type=Path, required=True)
    reuse_parser = commands.add_parser("reuse-v1")
    reuse_parser.add_argument("--config", type=Path, required=True)
    reuse_parser.add_argument("--source-run-id", type=int, required=True)
    reuse_parser.add_argument("--target-run-id", type=int, required=True)
    reuse_parser.add_argument("--apply", action="store_true")
    batches_parser = commands.add_parser("make-batches")
    batches_parser.add_argument("--config", type=Path, required=True)
    batches_parser.add_argument("--run-id", type=int, required=True)
    batches_parser.add_argument("--max-articles", type=int, default=100)
    batches_parser.add_argument("--max-units", type=int, default=100)
    batches_parser.add_argument("--max-bytes", type=int, default=24576)
    batches_parser.add_argument("--pilot-batches", type=int)
    batches_parser.add_argument("--pilot-only", action="store_true")
    batches_parser.add_argument("--pilot-output", type=Path)
    pilot_parser = commands.add_parser("pilot-check")
    pilot_parser.add_argument("--config", type=Path, required=True)
    pilot_parser.add_argument("--run-id", type=int, required=True)
    pilot_parser.add_argument("--selection", type=Path, required=True)
    pilot_parser.add_argument("--output", type=Path, required=True)
    checkpoint_parser = commands.add_parser("export-checkpoint")
    checkpoint_parser.add_argument("--config", type=Path, required=True)
    checkpoint_parser.add_argument("--run-id", type=int, required=True)
    checkpoint_parser.add_argument("--output-dir", type=Path, required=True)
    progress_parser = commands.add_parser("progress")
    progress_parser.add_argument("--config", type=Path, required=True)
    progress_parser.add_argument("--run-id", type=int, required=True)
    progress_parser.add_argument("--step-id", required=True)
    progress_parser.add_argument("--event", choices=("start", "snapshot", "finish"), required=True)
    failures_parser = commands.add_parser("failures")
    failures_parser.add_argument("--config", type=Path, required=True)
    failures_parser.add_argument("--run-id", type=int, required=True)
    failures_parser.add_argument("--output", type=Path, required=True)
    replacement_parser = commands.add_parser("replace-target")
    replacement_parser.add_argument("--config", type=Path, required=True)
    replacement_parser.add_argument("--run-id", type=int, required=True)
    replacement_parser.add_argument("--input", type=Path, required=True)
    export_ru_parser = commands.add_parser("export-ru")
    export_ru_parser.add_argument("--config", type=Path, required=True)
    export_ru_parser.add_argument("--run-id", type=int, required=True)
    export_ru_parser.add_argument("--output", type=Path, required=True)
    verify_ru_parser = commands.add_parser("verify-ru")
    verify_ru_parser.add_argument("--config", type=Path, required=True)
    verify_ru_parser.add_argument("--run-id", type=int, required=True)
    verify_ru_parser.add_argument("--archive", type=Path, required=True)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--de", type=Path, required=True)
    compare_parser.add_argument("--ru", type=Path, required=True)
    log_parser = commands.add_parser("article-log")
    log_parser.add_argument("--config", type=Path, required=True)
    log_parser.add_argument("--run-id", type=int, required=True)
    log_parser.add_argument("--require-status", choices=("in_progress", "complete"))
    log_parser.add_argument("--output", type=Path, required=True)
    log_parser.add_argument("--summary", type=Path, required=True)
    return root


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "source-report":
        result = source_report(
            args.source.resolve(), args.xsd.resolve(), args.labels.resolve(),
            args.subentry_groups.resolve(),
        )
        atomic_write(args.output.resolve(), canonical_json(result) + b"\n")
        if result["expected_count_mismatches"]:
            raise ValueError("Wadoku source counts differ: " + json.dumps(
                result["expected_count_mismatches"], ensure_ascii=False,
            ))
        if result["missing_subentry_policies"] or result["unused_subentry_policies"]:
            raise ValueError("Wadoku subentry policy inventory differs from source")
        return {"output": str(args.output.resolve()), **result["normalized_counts"],
                "missing_controlled_label_count": result["missing_controlled_label_count"]}
    if args.command == "compare":
        return compare_archives(args.de.resolve(), args.ru.resolve())
    config = Config.load(args.config)
    if args.command == "db-check":
        return db_check(config, args.require_schema_version)
    if args.command == "prepare":
        return prepare(config, args.archive.resolve(), args.source.resolve(), args.xsd.resolve(),
                       args.license.resolve())
    if args.command == "export-de":
        return export_de(config, args.run_id, args.output)
    if args.command == "verify-de":
        return verify_de(config, args.run_id, args.archive)
    if args.command == "reuse-v1":
        return reuse_v1(config, args.source_run_id, args.target_run_id, args.apply)
    if args.command == "make-batches":
        return make_wadoku_batches(
            config, args.run_id, args.max_articles, args.max_units, args.max_bytes,
            args.pilot_batches, args.pilot_only, args.pilot_output,
        )
    if args.command == "pilot-check":
        return pilot_check(config, args.run_id, args.selection.resolve(), args.output.resolve())
    if args.command == "export-checkpoint":
        return export_checkpoint(config, args.run_id, args.output_dir)
    if args.command == "progress":
        return progress(config, args.run_id, args.step_id, args.event)
    if args.command == "failures":
        return failure_report(config, args.run_id, args.output)
    if args.command == "replace-target":
        return replace_target(config, args.run_id, args.input.resolve())
    if args.command == "export-ru":
        return export_ru(config, args.run_id, args.output)
    if args.command == "verify-ru":
        return verify_ru(config, args.run_id, args.archive)
    if args.command == "article-log":
        return article_log(
            config, args.run_id, args.output, args.summary, args.require_status,
        )
    raise ValueError(f"unsupported command: {args.command}")


def main() -> None:
    print(json.dumps(execute(parser().parse_args()), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
