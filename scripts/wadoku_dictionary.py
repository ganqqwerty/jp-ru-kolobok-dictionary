#!/usr/bin/env python3
"""Prepare and export the Wadoku JP->RU Yomitan translation run."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from jitendex_ru.batch import make_batches
from jitendex_ru.build_dictionary import materialize_run
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.extract_units import extract_selected
from jitendex_ru.import_jitendex import import_jitendex
from jitendex_ru.schema_validation import validate_archive
from jitendex_ru.util import canonical_json, sha256_bytes, sha256_file
from jitendex_ru.validate_response import WADOKU_NEUTRAL_TOKEN_RE, source_scientific_names


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def versioned_prompt(config: Config, key: str) -> bytes:
    version = config.raw["versions"][key]
    return (config.root / "prompts" / f"{version.replace('-', '_')}.txt").read_bytes()


def ensure_snapshot(connection, kind: str, source: Path, extractor: str) -> int:
    digest = sha256_file(source)
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES (?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        (kind, "wadoku-2022-08-26", "https://www.wadoku.de/", digest, str(source), extractor),
    )
    return connection.execute(
        "SELECT id FROM source_snapshot WHERE kind=? AND sha256=?", (kind, digest),
    ).fetchone()["id"]


def source_article_count(source: Path) -> int:
    with zipfile.ZipFile(source) as archive:
        return sum(
            len(json.loads(archive.read(name)))
            for name in archive.namelist()
            if name.startswith("term_bank_") and name.endswith(".json")
        )


def prepare(config: Config, source: Path) -> dict[str, object]:
    database = Database(config)
    connection = database.connect()
    try:
        extractor = config.raw["versions"]["extractor"]
        source_id = ensure_snapshot(connection, "jitendex", source, extractor)
        scope_id = ensure_snapshot(connection, "kaishi", source, extractor)
        expected_articles = source_article_count(source)
        existing_articles = connection.execute(
            "SELECT COUNT(*) FROM article WHERE snapshot_id=?", (source_id,),
        ).fetchone()[0]
        if existing_articles not in {0, expected_articles}:
            raise ValueError(
                f"Wadoku snapshot is incomplete: {existing_articles}/{expected_articles} articles"
            )
        articles_added = import_jitendex(connection, source_id, source) if not existing_articles else 0
        connection.execute("UPDATE article SET selected=CASE WHEN snapshot_id=? THEN 1 ELSE 0 END", (source_id,))
        prompt_hash = sha256_bytes(versioned_prompt(config, "translation_prompt"))
        review_hash = sha256_bytes(versioned_prompt(config, "review_prompt"))
        terminology_hash = sha256_bytes((config.root / "terminology/ru-v1.json").read_bytes())
        limits = canonical_json(config.raw["batch"]).decode()
        selection_hash = sha256_bytes(f"wadoku:{sha256_file(source)}".encode())
        pipeline = config.raw["versions"]["pipeline"]
        connection.execute(
            """INSERT INTO run
            (jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
             prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,pipeline_version)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
            prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json) DO NOTHING""",
            (source_id, scope_id, selection_hash, extractor, prompt_hash, review_hash,
             terminology_hash, limits, pipeline),
        )
        run_id = connection.execute(
            """SELECT id FROM run WHERE jitendex_snapshot_id=? AND selection_sha256=?
            AND extractor_version=? AND prompt_sha256=? ORDER BY id DESC LIMIT 1""",
            (source_id, selection_hash, extractor, prompt_hash),
        ).fetchone()["id"]
        extracted = extract_selected(connection, run_id)
        batch_config = config.raw["batch"]
        batches = make_batches(
            connection, run_id, config.work_dir / "inbox", {},
            batch_config["soft_max_articles"], batch_config["soft_max_bytes"],
            batch_config["soft_max_units"], batch_config["singleton_threshold_bytes"],
            batch_config["hard_max_article_bytes"], batch_config["hard_max_article_units"],
        )
        result = {"run_id": run_id, "articles_added": articles_added, **extracted, **batches}
        audit(connection, "prepare_wadoku", "run", run_id, {"source": str(source), **result})
        connection.commit()
        return result
    finally:
        connection.close()
        database.close()


def write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compresslevel=9)


def export(config: Config, run_id: int, output: Path) -> dict[str, object]:
    database = Database(config)
    connection = database.connect()
    try:
        _run, source, rows = materialize_run(connection, run_id)
        with zipfile.ZipFile(source["local_path"]) as archive:
            index = json.loads(archive.read("index.json"))
        index.update({
            "title": "Вадоку — японско-русский словарь",
            "revision": "wadoku-jp-ru-2026.08.30",
            "description": "Японско-русский словарь. Производный перевод немецкого словаря Wadoku.",
            "sourceLanguage": "ja",
            "targetLanguage": "ru",
        })
        files = {"index.json": canonical_json(index), "term_bank_1.json": canonical_json(rows)}
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as archive:
            for name, data in files.items():
                write_member(archive, name, data)
        schema = validate_archive(output, config.root / "work/schemas/pinned-yomitan")
        result = {"run_id": run_id, "articles": len(rows), "output": str(output),
                  "sha256": sha256_file(output), **schema}
        audit(connection, "export_wadoku", "run", run_id, result)
        connection.commit()
        return result
    finally:
        connection.close()
        database.close()


def verify(config: Config, source: Path, output: Path) -> dict[str, Any]:
    schema = validate_archive(output, config.root / "work/schemas/pinned-yomitan")
    articles = translated = neutral = definitions = cyrillic_rows = 0
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(output) as output_zip:
        source_names = set(source_zip.namelist())
        output_names = set(output_zip.namelist())
        if source_names != output_names:
            raise ValueError("output archive members differ from the source")
        index = json.loads(output_zip.read("index.json"))
        if index.get("sourceLanguage") != "ja" or index.get("targetLanguage") != "ru":
            raise ValueError("output language metadata is not ja -> ru")
        if index.get("title") != "Вадоку — японско-русский словарь":
            raise ValueError("unexpected output dictionary title")
        bank_names = sorted(
            name for name in source_names if re.fullmatch(r"term_bank_\d+\.json", name)
        )
        if not bank_names:
            raise ValueError("source has no term banks")
        for name in bank_names:
            source_rows = json.loads(source_zip.read(name))
            output_rows = json.loads(output_zip.read(name))
            if len(source_rows) != len(output_rows):
                raise ValueError(f"entry count changed in {name}")
            for source_row, output_row in zip(source_rows, output_rows, strict=True):
                if source_row[:5] != output_row[:5] or source_row[6:] != output_row[6:]:
                    raise ValueError(f"protected term fields changed in {name}")
                source_glossary = source_row[5]
                target_glossary = output_row[5]
                if (
                    not isinstance(target_glossary, list)
                    or not target_glossary
                    or not all(isinstance(item, str) and item.strip() for item in target_glossary)
                    or len(set(target_glossary)) != len(target_glossary)
                ):
                    raise ValueError(f"invalid translated glossary in {name}")
                combined = " ".join(target_glossary)
                has_cyrillic = re.search(r"[А-Яа-яЁё]", combined) is not None
                source_text = json.dumps(source_glossary, ensure_ascii=False)
                source_taxa = set(source_scientific_names(source_text))
                is_scientific_name = bool(source_taxa) and all(
                    item in source_taxa for item in target_glossary
                )
                is_neutral_token = source_glossary == target_glossary and all(
                    WADOKU_NEUTRAL_TOKEN_RE.fullmatch(item) is not None
                    for item in target_glossary
                )
                is_neutral = is_scientific_name or is_neutral_token
                if not has_cyrillic and not is_neutral:
                    raise ValueError(f"translated glossary lacks Russian text in {name}")
                if source_glossary == target_glossary and not is_neutral:
                    raise ValueError(f"source glossary was not translated in {name}")
                articles += 1
                definitions += len(target_glossary)
                translated += source_glossary != target_glossary
                neutral += is_neutral
                cyrillic_rows += has_cyrillic
    return {
        "articles": articles,
        "translated_articles": translated,
        "language_neutral_articles": neutral,
        "cyrillic_articles": cyrillic_rows,
        "definitions": definitions,
        "sha256": sha256_file(output),
        **schema,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.wadoku.luna.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("source", type=Path)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--run-id", type=int, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "prepare":
        result = prepare(config, args.source.resolve())
    elif args.command == "export":
        result = export(config, args.run_id, args.output.resolve())
    else:
        result = verify(config, args.source.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
