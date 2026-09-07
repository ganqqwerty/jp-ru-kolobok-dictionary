#!/usr/bin/env python3
"""Prepare, translate, export, and verify the isolated DOJG Russian dictionary."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jitendex_ru.apply_translations import apply_article
from jitendex_ru.batch import make_batches
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.dojg import extract_dojg_segments, japanese_text_signature
from jitendex_ru.extract_units import extract_selected
from jitendex_ru.import_jitendex import import_jitendex
from jitendex_ru.jpdb_scope import (
    accept_deterministic_translations, reuse_accepted_translations,
)
from jitendex_ru.schema_validation import validate_archive
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes, sha256_file
from jitendex_ru.validate_response import dojg_untranslated_english


TERM_BANK_RE = re.compile(r"^term_bank_(\d+)\.json$")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EXPECTED_ENTRIES = 535
EXPECTED_UNIQUE_HEADWORDS = 504
EXPECTED_GLOSSARY_CHARACTERS = 908_201
EXPECTED_VOLUMES = {"DOJG基本": 86, "DOJG中級編": 191, "DOJG上級編": 258}


def versioned_prompt(config: Config, key: str) -> bytes:
    version = config.raw["versions"][key]
    return (config.root / "prompts" / f"{version.replace('-', '_')}.txt").read_bytes()


def terminology_path(config: Config) -> Path:
    version = config.raw["versions"]["terminology"]
    return config.root / "terminology" / f"{version}.json"


def read_source(source: Path) -> tuple[dict[str, Any], list[tuple[int, int, list[Any]]], dict[str, Any]]:
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if names.count("index.json") != 1:
            raise ValueError("DOJG source must contain exactly one index.json")
        index = json.loads(archive.read("index.json"))
        if index.get("format") != 3:
            raise ValueError("DOJG source must use Yomitan format 3")
        banks = sorted(
            (int(match.group(1)), name)
            for name in names if (match := TERM_BANK_RE.fullmatch(name))
        )
        if not banks:
            raise ValueError("DOJG source has no term bank")
        bank_rows: list[tuple[int, int, list[Any]]] = []
        for bank_number, name in banks:
            rows = json.loads(archive.read(name))
            if not isinstance(rows, list):
                raise ValueError(f"DOJG term bank is not an array: {name}")
            for ordinal, row in enumerate(rows):
                if (
                    not isinstance(row, list) or len(row) < 8
                    or not isinstance(row[5], list) or len(row[5]) != 1
                    or not isinstance(row[5][0], str) or not isinstance(row[6], int)
                ):
                    raise ValueError(f"unsupported DOJG entry at {name}:{ordinal}")
                bank_rows.append((bank_number, ordinal, row))
    volume_counts = Counter(str(row[7]) for _bank, _ordinal, row in bank_rows)
    stats = {
        "sha256": sha256_file(source),
        "bytes": source.stat().st_size,
        "entries": len(bank_rows),
        "unique_headwords": len({str(row[0]) for _bank, _ordinal, row in bank_rows}),
        "glossary_characters": sum(len(row[5][0]) for _bank, _ordinal, row in bank_rows),
        "volumes": dict(sorted(volume_counts.items())),
        "term_banks": len(banks),
        "index": index,
    }
    return index, bank_rows, stats


def inspect_source(config: Config, source: Path) -> dict[str, Any]:
    _index, bank_rows, stats = read_source(source)
    expected_hash = config.raw["source"]["sha256"]
    if stats["sha256"] != expected_hash:
        raise ValueError(f"DOJG source SHA-256 mismatch: expected {expected_hash}, got {stats['sha256']}")
    expected = {
        "entries": EXPECTED_ENTRIES,
        "unique_headwords": EXPECTED_UNIQUE_HEADWORDS,
        "glossary_characters": EXPECTED_GLOSSARY_CHARACTERS,
        "volumes": EXPECTED_VOLUMES,
    }
    actual = {key: stats[key] for key in expected}
    if actual != expected:
        raise ValueError(f"DOJG source inventory mismatch: expected {expected}, got {actual}")
    units = sum(len(extract_dojg_segments(row)) for _bank, _ordinal, row in bank_rows)
    articles_without_units = sum(
        not extract_dojg_segments(row) for _bank, _ordinal, row in bank_rows
    )
    return {**stats, "translation_units": units, "articles_without_units": articles_without_units}


def ensure_schemas(config: Config) -> Path:
    schema_dir = config.work_dir / "schemas" / "pinned-yomitan"
    schema_dir.mkdir(parents=True, exist_ok=True)
    for spec in config.raw["schemas"].values():
        target = schema_dir / spec["filename"]
        if not target.is_file():
            request = urllib.request.Request(spec["url"], headers={"User-Agent": "dojg-ru-pipeline/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                atomic_write(target, response.read())
        actual = sha256_file(target)
        if actual != spec["sha256"]:
            raise ValueError(f"schema SHA-256 mismatch for {target.name}: {actual}")
    return schema_dir


def ensure_snapshot(
    connection: Any, kind: str, source: Path, extractor: str,
    source_stats: dict[str, Any],
) -> int:
    metadata = canonical_json({
        "dictionary": "dojg",
        "source_index": source_stats["index"],
        "inventory": {key: source_stats[key] for key in (
            "entries", "unique_headwords", "glossary_characters", "volumes", "term_banks",
        )},
    }).decode()
    connection.execute(
        """INSERT INTO source_snapshot
        (kind,version,url,sha256,local_path,extractor_version,metadata_json)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        (
            kind, "DOJG_v1.01;2022-04-30;better formatting", "", source_stats["sha256"],
            str(source), extractor, metadata,
        ),
    )
    return connection.execute(
        "SELECT id FROM source_snapshot WHERE kind=? AND sha256=?", (kind, source_stats["sha256"]),
    ).fetchone()["id"]


def pilot_selection(bank_rows: list[tuple[int, int, list[Any]]]) -> list[tuple[int, int]]:
    by_volume: dict[str, list[tuple[int, int, list[Any]]]] = defaultdict(list)
    headword_count = Counter(str(row[0]) for _bank, _ordinal, row in bank_rows)
    for item in bank_rows:
        by_volume[str(item[2][7])].append(item)
    selected: set[tuple[int, int]] = set()
    for volume in EXPECTED_VOLUMES:
        rows = by_volume[volume]
        ranked = [
            min(rows, key=lambda item: (len(item[2][5][0]), item[0], item[1])),
            max(rows, key=lambda item: (len(item[2][5][0]), -item[0], -item[1])),
            max(rows, key=lambda item: (item[2][5][0].count("|"), -item[0], -item[1])),
            max(rows, key=lambda item: (item[2][5][0].count("[例文"), -item[0], -item[1])),
            sorted(rows, key=lambda item: (len(item[2][5][0]), item[0], item[1]))[len(rows) // 2],
        ]
        repeated = next((item for item in rows if headword_count[str(item[2][0])] > 1), rows[0])
        ranked.append(repeated)
        for bank, ordinal, _row in ranked:
            selected.add((bank, ordinal))
        if len([item for item in selected if item in {(row[0], row[1]) for row in rows}]) < 6:
            for bank, ordinal, _row in sorted(rows, key=lambda item: (item[0], item[1])):
                selected.add((bank, ordinal))
                if len([item for item in selected if item in {(row[0], row[1]) for row in rows}]) >= 6:
                    break
    return sorted(selected)


def source_round_trip(connection: Any, snapshot_id: int, bank_rows: list[tuple[int, int, list[Any]]]) -> dict[str, int]:
    stored = connection.execute(
        "SELECT bank_number,entry_ordinal,raw_json FROM article WHERE snapshot_id=? ORDER BY bank_number,entry_ordinal",
        (snapshot_id,),
    ).fetchall()
    expected = [(bank, ordinal, canonical_json(row).decode()) for bank, ordinal, row in bank_rows]
    actual = [(row["bank_number"], row["entry_ordinal"], row["raw_json"]) for row in stored]
    if actual != expected:
        raise ValueError("DOJG database import is not a lossless source round trip")
    return {"round_trip_entries": len(actual)}


def _run_id(
    connection: Any, config: Config, source_id: int, scope_id: int,
    selection_hash: str,
) -> int:
    extractor = config.raw["versions"]["extractor"]
    prompt_hash = sha256_bytes(versioned_prompt(config, "translation_prompt"))
    review_hash = sha256_bytes(versioned_prompt(config, "review_prompt"))
    terminology_hash = sha256_bytes(terminology_path(config).read_bytes())
    limits = canonical_json(config.raw["batch"]).decode()
    pipeline = config.raw["versions"]["pipeline"]
    connection.execute(
        """INSERT INTO run
        (jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
         prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,pipeline_version)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
        prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json) DO NOTHING""",
        (
            source_id, scope_id, selection_hash, extractor, prompt_hash, review_hash,
            terminology_hash, limits, pipeline,
        ),
    )
    return connection.execute(
        """SELECT id FROM run WHERE jitendex_snapshot_id=? AND selection_sha256=?
        AND extractor_version=? AND prompt_sha256=? ORDER BY id DESC LIMIT 1""",
        (source_id, selection_hash, extractor, prompt_hash),
    ).fetchone()["id"]


def prepare(
    config: Config, source: Path, scope: str, source_run_id: int | None = None,
    create_batches: bool = True,
) -> dict[str, Any]:
    source_stats = inspect_source(config, source)
    ensure_schemas(config)
    _index, bank_rows, _stats = read_source(source)
    selected = pilot_selection(bank_rows) if scope == "pilot" else [
        (bank, ordinal) for bank, ordinal, _row in bank_rows
    ]
    database = Database(config)
    database.migrate()
    connection = database.connect()
    try:
        extractor = config.raw["versions"]["extractor"]
        source_id = ensure_snapshot(connection, "jitendex", source, extractor, source_stats)
        scope_id = ensure_snapshot(connection, "kaishi", source, extractor, source_stats)
        articles_added = import_jitendex(connection, source_id, source)
        round_trip = source_round_trip(connection, source_id, bank_rows)
        connection.execute("UPDATE article SET selected=0 WHERE snapshot_id=?", (source_id,))
        connection.executemany(
            "UPDATE article SET selected=1 WHERE snapshot_id=? AND bank_number=? AND entry_ordinal=?",
            ((source_id, bank, ordinal) for bank, ordinal in selected),
        )
        selection_hash = sha256_bytes(canonical_json({
            "dictionary": "dojg", "scope": scope, "source_sha256": source_stats["sha256"],
            "entries": selected,
        }))
        run_id = _run_id(connection, config, source_id, scope_id, selection_hash)
        extracted = extract_selected(connection, run_id)
        reused: dict[str, Any] = {"units_reused": 0}
        if source_run_id is not None:
            reused = reuse_accepted_translations(connection, source_run_id, run_id)
        batches: dict[str, Any] = {"batches_created": 0}
        if create_batches:
            limits = config.raw["batch"]
            terminology = json.loads(terminology_path(config).read_text(encoding="utf-8"))
            batches = make_batches(
                connection, run_id, config.work_dir / f"{scope}-inbox", terminology,
                limits["soft_max_articles"], limits["soft_max_bytes"], limits["soft_max_units"],
                limits["singleton_threshold_bytes"], limits["hard_max_article_bytes"],
                limits["hard_max_article_units"],
            )
        audit(connection, "prepare_dojg", "run", run_id, {
            "scope": scope, "source": str(source), "selected_entries": len(selected),
            "source_run_id": source_run_id,
        })
        connection.commit()
        if scope == "pilot":
            selection_rows = [
                {
                    "bank_number": bank, "entry_ordinal": ordinal,
                    "headword": next(
                        row[0] for item_bank, item_ordinal, row in bank_rows
                        if item_bank == bank and item_ordinal == ordinal
                    ),
                }
                for bank, ordinal in selected
            ]
            atomic_write(
                config.work_dir / "pilot-selection.json",
                canonical_json({"source_sha256": source_stats["sha256"], "entries": selection_rows}) + b"\n",
            )
        return {
            "run_id": run_id, "scope": scope, "selected_entries": len(selected),
            "articles_added": articles_added, **round_trip,
            "translation_units": extracted["units_added"],
            "articles_without_units": extracted["articles_without_units"],
            "parsed_articles": extracted["parsed_articles"],
            "units_reused": reused["units_reused"],
            "batches_created": batches["batches_created"],
            "batched_articles": batches.get("articles", 0),
            "batched_units": batches.get("units", 0),
            "phase_metrics": {
                "extraction": extracted["phase_metrics"],
                "batching": batches.get("phase_metrics", {}),
            },
        }
    finally:
        connection.close()
        database.close()


def run_status(config: Config, run_id: int) -> dict[str, Any]:
    database = Database(config)
    connection = database.connect()
    try:
        scalar = lambda sql, values=(): int(connection.execute(sql, values).fetchone()[0] or 0)
        return {
            "run_id": run_id,
            "run_state": connection.execute("SELECT state FROM run WHERE id=?", (run_id,)).fetchone()[0],
            "entries": scalar("SELECT COUNT(*) FROM run_article WHERE run_id=?", (run_id,)),
            "units": scalar("SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,)),
            "accepted_units": scalar(
                "SELECT COUNT(*) FROM translation WHERE run_id=? AND accepted=1", (run_id,),
            ),
            "unit_states": {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) count FROM translation_unit WHERE run_id=? GROUP BY status ORDER BY status",
                    (run_id,),
                )
            },
            "batch_states": {
                row["state"]: int(row["count"])
                for row in connection.execute(
                    "SELECT state,COUNT(*) count FROM batch WHERE run_id=? GROUP BY state ORDER BY state",
                    (run_id,),
                )
            },
            "attempt_outcomes": {
                row["outcome"]: int(row["count"])
                for row in connection.execute(
                    """SELECT a.outcome,COUNT(*) count FROM attempt a JOIN batch b ON b.id=a.batch_id
                    WHERE b.run_id=? GROUP BY a.outcome ORDER BY a.outcome""", (run_id,),
                )
            },
            "unresolved_errors": scalar(
                """SELECT COUNT(*) FROM validation_issue WHERE run_id=?
                AND severity IN ('error','blocking') AND resolved_at IS NULL""", (run_id,),
            ),
            "terminal_blocked_batches": scalar(
                """SELECT COUNT(*) FROM batch b WHERE b.run_id=? AND b.state='blocked'
                AND NOT EXISTS (SELECT 1 FROM audit_event ae WHERE ae.event_type='split'
                                AND ae.entity_type='batch' AND ae.entity_id=b.id)""", (run_id,),
            ),
        }
    finally:
        connection.close()
        database.close()


def accept_run(config: Config, run_id: int) -> dict[str, Any]:
    database = Database(config)
    connection = database.connect()
    try:
        accepted = accept_deterministic_translations(connection, run_id)
        unit_count = connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,),
        ).fetchone()[0]
        accepted_count = connection.execute(
            "SELECT COUNT(*) FROM translation WHERE run_id=? AND accepted=1", (run_id,),
        ).fetchone()[0]
        unfinished = connection.execute(
            """SELECT COUNT(*) FROM batch b WHERE b.run_id=? AND (
            b.state IN ('ready','leased','retryable') OR (
              b.state='blocked' AND NOT EXISTS (
                SELECT 1 FROM audit_event ae WHERE ae.event_type='split'
                AND ae.entity_type='batch' AND ae.entity_id=b.id)))""",
            (run_id,),
        ).fetchone()[0]
        unresolved = connection.execute(
            """SELECT COUNT(*) FROM validation_issue WHERE run_id=?
            AND severity IN ('error','blocking') AND resolved_at IS NULL""", (run_id,),
        ).fetchone()[0]
        duplicate = connection.execute(
            """SELECT COUNT(*) FROM (SELECT unit_id FROM translation WHERE run_id=? AND accepted=1
            GROUP BY unit_id HAVING COUNT(*)<>1) duplicate""", (run_id,),
        ).fetchone()[0]
        if unfinished or unresolved or duplicate or accepted_count != unit_count:
            raise ValueError({
                "unfinished_batches": unfinished, "unresolved_errors": unresolved,
                "duplicate_accepted_units": duplicate, "accepted_units": accepted_count,
                "units": unit_count,
            })
        connection.execute("UPDATE run SET state='complete' WHERE id=?", (run_id,))
        audit(connection, "accept_dojg", "run", run_id, {
            "units": unit_count, "accepted_units": accepted_count,
        })
        connection.commit()
        return {**accepted, "units": unit_count, "accepted_units": accepted_count, "passed": True}
    finally:
        connection.close()
        database.close()


def write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compresslevel=9)


def verify_output(
    config: Config, connection: Any, run_id: int, output: Path,
) -> dict[str, Any]:
    schema_result = validate_archive(output, ensure_schemas(config))
    articles = connection.execute(
        """SELECT a.* FROM run_article ra JOIN article a ON a.id=ra.article_id
        WHERE ra.run_id=? ORDER BY a.bank_number,a.entry_ordinal""", (run_id,),
    ).fetchall()
    source_rows = [json.loads(row["raw_json"]) for row in articles]
    expected_rows = [apply_article(connection, run_id, article) for article in articles]
    with zipfile.ZipFile(output) as archive:
        index = json.loads(archive.read("index.json"))
        output_rows: list[list[Any]] = []
        for _number, name in sorted(
            (int(match.group(1)), name)
            for name in archive.namelist() if (match := TERM_BANK_RE.fullmatch(name))
        ):
            output_rows.extend(json.loads(archive.read(name)))
    if not source_rows or len(output_rows) != len(source_rows):
        raise ValueError("DOJG output entry count changed")
    for ordinal, (source, translated) in enumerate(zip(source_rows, output_rows, strict=True)):
        if translated != expected_rows[ordinal]:
            raise ValueError(f"DOJG output differs from accepted database data at entry {ordinal}")
        if source[:5] != translated[:5] or source[6:] != translated[6:]:
            raise ValueError(f"DOJG non-glossary fields changed at entry {ordinal}")
        if japanese_text_signature(source[5][0]) != japanese_text_signature(translated[5][0]):
            raise ValueError(f"DOJG Japanese text changed at entry {ordinal}")
        for delimiter in ("\n", "|"):
            if source[5][0].count(delimiter) != translated[5][0].count(delimiter):
                raise ValueError(f"DOJG layout delimiter changed at entry {ordinal}")
        if re.search(r"⟦J\d{4}⟧", translated[5][0]):
            raise ValueError(f"DOJG placeholder leaked into output at entry {ordinal}")
    remaining_english = sum(
        bool(dojg_untranslated_english(row["source_text"], row["target_text"]))
        for row in connection.execute(
            """SELECT tu.source_text,t.target_text FROM translation t
            JOIN translation_unit tu ON tu.id=t.unit_id
            WHERE t.run_id=? AND t.accepted=1""",
            (run_id,),
        )
    )
    accepted = connection.execute(
        "SELECT COUNT(*) FROM translation WHERE run_id=? AND accepted=1", (run_id,),
    ).fetchone()[0]
    units = connection.execute(
        "SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,),
    ).fetchone()[0]
    if accepted != units:
        raise ValueError(f"DOJG accepted coverage is incomplete: {accepted}/{units}")
    if remaining_english:
        raise ValueError(f"DOJG output still has {remaining_english} translatable English cells")
    product = dict(config.raw["product"])
    if len(source_rows) != EXPECTED_ENTRIES:
        product.update({
            "title": f"{product['title']} — пилот",
            "revision": f"{product['revision']}-pilot-r{run_id}",
            "description": f"{product['description']} Пилот: {len(source_rows)} статей.",
        })
    for key in ("title", "revision", "description"):
        if index.get(key) != product[key]:
            raise ValueError(f"DOJG output metadata mismatch: {key}")
    return {
        **schema_result,
        "entries": len(output_rows),
        "accepted_units": accepted,
        "remaining_english_cells": remaining_english,
        "japanese_preserved": True,
        "sha256": sha256_file(output),
        "verified": True,
    }


def export(
    config: Config, run_id: int, output: Path, *, allow_partial: bool = False,
) -> dict[str, Any]:
    database = Database(config)
    connection = database.connect()
    try:
        source = connection.execute(
            """SELECT ss.* FROM run r JOIN source_snapshot ss ON ss.id=r.jitendex_snapshot_id
            WHERE r.id=?""", (run_id,),
        ).fetchone()
        articles = connection.execute(
            """SELECT a.* FROM run_article ra JOIN article a ON a.id=ra.article_id
            WHERE ra.run_id=? ORDER BY a.bank_number,a.entry_ordinal""", (run_id,),
        ).fetchall()
        if not allow_partial and len(articles) != EXPECTED_ENTRIES:
            raise ValueError(f"full DOJG export requires {EXPECTED_ENTRIES} entries, got {len(articles)}")
        rows = [apply_article(connection, run_id, article) for article in articles]
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(source["local_path"]) as archive:
            index = json.loads(archive.read("index.json"))
            product = dict(config.raw["product"])
            if len(articles) != EXPECTED_ENTRIES:
                product.update({
                    "title": f"{product['title']} — пилот",
                    "revision": f"{product['revision']}-pilot-r{run_id}",
                    "description": f"{product['description']} Пилот: {len(articles)} статей.",
                })
            index.update({key: product[key] for key in ("title", "revision", "description")})
            files["index.json"] = canonical_json(index)
            for name in archive.namelist():
                if name != "index.json" and not TERM_BANK_RE.fullmatch(name):
                    files[name] = archive.read(name)
        files["term_bank_1.json"] = canonical_json(rows)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as archive:
            for name in sorted(files, key=lambda item: (item != "index.json", item)):
                write_member(archive, name, files[name])
        verified = verify_output(config, connection, run_id, output)
        file_manifest = [
            {"path": name, "sha256": sha256_bytes(data), "bytes": len(data)}
            for name, data in sorted(files.items())
        ]
        manifest_hash = sha256_bytes(canonical_json(file_manifest))
        export_id = connection.execute(
            """INSERT INTO export(run_id,output_path,manifest_sha256,zip_sha256,verified)
            VALUES (?,?,?,?,1) RETURNING id""",
            (run_id, str(output), manifest_hash, verified["sha256"]),
        ).fetchone()[0]
        connection.executemany(
            "INSERT INTO export_file(export_id,path,sha256,byte_count) VALUES (?,?,?,?)",
            ((export_id, item["path"], item["sha256"], item["bytes"]) for item in file_manifest),
        )
        audit(connection, "export_dojg", "export", export_id, verified)
        connection.commit()
        return {"run_id": run_id, "export_id": export_id, "output": str(output), **verified}
    finally:
        connection.close()
        database.close()


def verify(config: Config, run_id: int, output: Path) -> dict[str, Any]:
    database = Database(config)
    connection = database.connect()
    try:
        return verify_output(config, connection, run_id, output)
    finally:
        connection.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.dojg.luna.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("source", type=Path)
    commands.add_parser("init-db")
    commands.add_parser("acquire-schemas")
    prep = commands.add_parser("prepare")
    prep.add_argument("source", type=Path)
    prep.add_argument("--scope", choices=("pilot", "full"), required=True)
    prep.add_argument("--source-run-id", type=int)
    prep.add_argument("--no-batches", action="store_true")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--run-id", type=int, required=True)
    accept_parser = commands.add_parser("accept")
    accept_parser.add_argument("--run-id", type=int, required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--run-id", type=int, required=True)
    export_parser.add_argument("--output", type=Path)
    export_parser.add_argument("--allow-partial", action="store_true")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--run-id", type=int, required=True)
    verify_parser.add_argument("path", type=Path)
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "inspect":
        result = inspect_source(config, args.source.resolve())
    elif args.command == "init-db":
        database = Database(config)
        database.migrate()
        database.close()
        result = {"database_initialized": True, "backend": config.db_backend}
    elif args.command == "acquire-schemas":
        result = {"schema_dir": str(ensure_schemas(config))}
    elif args.command == "prepare":
        result = prepare(
            config, args.source.resolve(), args.scope, args.source_run_id,
            create_batches=not args.no_batches,
        )
    elif args.command == "status":
        result = run_status(config, args.run_id)
    elif args.command == "accept":
        result = accept_run(config, args.run_id)
    elif args.command == "export":
        output = args.output or config.dist_dir / config.raw["product"]["output"]
        result = export(config, args.run_id, output.resolve(), allow_partial=args.allow_partial)
    else:
        result = verify(config, args.run_id, args.path.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
