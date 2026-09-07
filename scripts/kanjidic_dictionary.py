#!/usr/bin/env python3
"""Prepare, export, and verify the EN->RU KANJIDIC Yomitan run."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from jitendex_ru.apply_translations import apply_article
from jitendex_ru.batch import make_batches
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.extract_units import extract_selected
from jitendex_ru.jpdb_scope import accept_deterministic_translations
from jitendex_ru.util import canonical_json, sha256_bytes, sha256_file


KANJI_BANK_RE = re.compile(r"kanji_bank_(\d+)\.json")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
KANJI_SCHEMA_URL = (
    "https://raw.githubusercontent.com/yomidevs/yomitan/"
    "649cfb0bdfe7b156447202b151f049784e8468dc/ext/data/schemas/"
    "dictionary-kanji-bank-v3-schema.json"
)
KANJI_SCHEMA_SHA256 = "03c1691377314ae80d86982f1ae446d3ea786d9864f8fb225e127cd9701dc027"
TAG_DESCRIPTION_RU = {
    "included in list of regular-use characters": "входит в список кандзи общего употребления",
    "included in list of characters for use in personal names": "входит в список кандзи для личных имён",
    "Frequency": "Частотность",
    "Grade level": "Класс школы",
    "JLPT level": "Уровень JLPT",
    "Stroke count": "Количество черт",
    "JIS X 0208-1997 kuten code": "Код кутэн JIS X 0208-1997",
    "JIS X 0212-1990 kuten code": "Код кутэн JIS X 0212-1990",
    "JIS X 0213-2000 kuten code": "Код кутэн JIS X 0213-2000",
    "Unicode hex code": "Шестнадцатеричный код Unicode",
    "Four corner code": "Код по четырём углам",
    "Misclassification": "Ошибочная классификация",
}


def versioned_prompt(config: Config, key: str) -> bytes:
    version = config.raw["versions"][key]
    return (config.root / "prompts" / f"{version.replace('-', '_')}.txt").read_bytes()


def source_banks(archive: zipfile.ZipFile) -> list[tuple[int, str]]:
    return sorted(
        (int(match.group(1)), name)
        for name in archive.namelist()
        if (match := KANJI_BANK_RE.fullmatch(name))
    )


def validate_source(source: Path, expected_sha256: str) -> dict[str, int]:
    digest = sha256_file(source)
    if digest != expected_sha256:
        raise ValueError(f"KANJIDIC source hash differs: {digest}")
    entries = meanings = 0
    with zipfile.ZipFile(source) as archive:
        index = json.loads(archive.read("index.json"))
        if index.get("format") != 3 or index.get("title") != "KANJIDIC (English)":
            raise ValueError("unexpected KANJIDIC index metadata")
        banks = source_banks(archive)
        if not banks:
            raise ValueError("KANJIDIC archive has no kanji banks")
        for _bank_number, name in banks:
            rows = json.loads(archive.read(name))
            for row in rows:
                if (
                    not isinstance(row, list) or len(row) != 6
                    or not isinstance(row[0], str) or not isinstance(row[1], str)
                    or not isinstance(row[2], str) or not isinstance(row[3], str)
                    or not isinstance(row[4], list) or not row[4]
                    or not all(isinstance(item, str) and item for item in row[4])
                    or not isinstance(row[5], dict)
                ):
                    raise ValueError(f"invalid KANJIDIC row in {name}")
                entries += 1
                meanings += len(row[4])
    return {"banks": len(banks), "entries": entries, "meanings": meanings}


def ensure_snapshot(connection: Any, kind: str, source: Path, extractor: str) -> int:
    digest = sha256_file(source)
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES (?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        (kind, "kanjidic2", "https://www.edrdg.org/wiki/index.php/KANJIDIC_Project", digest,
         str(source), extractor),
    )
    return connection.execute(
        "SELECT id FROM source_snapshot WHERE kind=? AND sha256=?", (kind, digest),
    ).fetchone()["id"]


def import_kanjidic(connection: Any, snapshot_id: int, source: Path) -> int:
    added = 0
    with zipfile.ZipFile(source) as archive:
        for bank_number, name in source_banks(archive):
            for ordinal, row in enumerate(json.loads(archive.read(name))):
                raw = canonical_json(row)
                sequence = (bank_number - 1) * 10_000 + ordinal + 1
                reading = " ".join(item for item in (row[1], row[2]) if item)
                cursor = connection.execute(
                    """INSERT INTO article
                    (snapshot_id,bank_number,entry_ordinal,expression,reading,sequence,raw_json,source_sha256)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(snapshot_id,bank_number,entry_ordinal) DO NOTHING""",
                    (snapshot_id, bank_number, ordinal, row[0], reading, sequence,
                     raw.decode(), sha256_bytes(raw)),
                )
                added += cursor.rowcount
    audit(connection, "import_kanjidic", "source_snapshot", snapshot_id, {"articles_added": added})
    return added


def prepare(config: Config, source: Path) -> dict[str, Any]:
    source_report = validate_source(source, config.raw["source"]["sha256"])
    database = Database(config)
    database.migrate()
    connection = database.connect()
    try:
        extractor = config.raw["versions"]["extractor"]
        source_id = ensure_snapshot(connection, "jitendex", source, extractor)
        scope_id = ensure_snapshot(connection, "kaishi", source, extractor)
        articles_added = import_kanjidic(connection, source_id, source)
        connection.execute(
            "UPDATE article SET selected=CASE WHEN snapshot_id=? THEN 1 ELSE 0 END", (source_id,),
        )
        prompt_hash = sha256_bytes(versioned_prompt(config, "translation_prompt"))
        review_hash = sha256_bytes(versioned_prompt(config, "review_prompt"))
        terminology_path = config.root / "terminology/kanjidic-ru-v1.json"
        terminology_hash = sha256_bytes(terminology_path.read_bytes())
        limits = canonical_json(config.raw["batch"]).decode()
        selection_hash = sha256_bytes(f"kanjidic:{sha256_file(source)}".encode())
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
            connection, run_id, config.work_dir / "inbox",
            json.loads(terminology_path.read_text(encoding="utf-8")),
            batch_config["soft_max_articles"], batch_config["soft_max_bytes"],
            batch_config["soft_max_units"], batch_config["singleton_threshold_bytes"],
            batch_config["hard_max_article_bytes"], batch_config["hard_max_article_units"],
        )
        result = {
            "run_id": run_id, "articles_added": articles_added, **source_report,
            **extracted, **batches,
        }
        audit(connection, "prepare_kanjidic", "run", run_id, {"source": str(source), **result})
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


def localized_tags(rows: list[list[Any]]) -> list[list[Any]]:
    output = []
    for row in rows:
        localized = list(row)
        localized[3] = TAG_DESCRIPTION_RU.get(localized[3], localized[3])
        output.append(localized)
    return output


def export(config: Config, run_id: int, output: Path) -> dict[str, Any]:
    database = Database(config)
    connection = database.connect()
    try:
        counts = connection.execute(
            """SELECT (SELECT COUNT(*) FROM run_article WHERE run_id=?),
            (SELECT COUNT(*) FROM translation_unit WHERE run_id=?),
            (SELECT COUNT(*) FROM translation WHERE run_id=? AND accepted=1)""",
            (run_id, run_id, run_id),
        ).fetchone()
        count_values = (counts[0], counts[1], counts[2])
        if count_values != (10_350, 10_350, 10_350):
            raise ValueError(f"KANJIDIC run is incomplete: {count_values}")
        source = connection.execute(
            """SELECT ss.* FROM run r JOIN source_snapshot ss ON ss.id=r.jitendex_snapshot_id
            WHERE r.id=?""", (run_id,),
        ).fetchone()
        articles = connection.execute(
            """SELECT a.* FROM run_article ra JOIN article a ON a.id=ra.article_id
            WHERE ra.run_id=? ORDER BY a.bank_number,a.entry_ordinal""", (run_id,),
        ).fetchall()
        rows_by_bank: dict[int, list[list[Any]]] = {}
        for article in articles:
            rows_by_bank.setdefault(article["bank_number"], []).append(
                apply_article(connection, run_id, article)
            )
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(source["local_path"]) as archive:
            index = json.loads(archive.read("index.json"))
            index.update({
                "title": config.raw["product"]["title"],
                "revision": config.raw["product"]["revision"],
                "description": config.raw["product"]["description"],
                "attribution": "KANJIDIC2 / Electronic Dictionary Research and Development Group (EDRDG)",
                "sourceLanguage": "ja", "targetLanguage": "ru",
            })
            files["index.json"] = canonical_json(index)
            for name in archive.namelist():
                match = KANJI_BANK_RE.fullmatch(name)
                if match:
                    files[name] = canonical_json(rows_by_bank[int(match.group(1))])
                elif name == "tag_bank_1.json":
                    files[name] = canonical_json(localized_tags(json.loads(archive.read(name))))
                elif name != "index.json":
                    files[name] = archive.read(name)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as archive:
            for name in sorted(files, key=lambda item: (item != "index.json", item)):
                write_member(archive, name, files[name])
        result = {
            "run_id": run_id, "articles": len(articles), "units": counts[1],
            "output": str(output), "sha256": sha256_file(output),
        }
        audit(connection, "export_kanjidic", "run", run_id, result)
        connection.commit()
        return result
    finally:
        connection.close()
        database.close()


def cached_kanji_schema(config: Config) -> Path:
    path = config.work_dir / "schemas" / "dictionary-kanji-bank-v3-schema.json"
    if not path.is_file() or sha256_file(path) != KANJI_SCHEMA_SHA256:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(KANJI_SCHEMA_URL, timeout=30).read()
        if sha256_bytes(data) != KANJI_SCHEMA_SHA256:
            raise ValueError("downloaded Yomitan kanji schema hash differs")
        path.write_bytes(data)
    return path


def verify(config: Config, source: Path, output: Path) -> dict[str, Any]:
    import fastjsonschema

    validate_source(source, config.raw["source"]["sha256"])
    schema_validator = fastjsonschema.compile(
        json.loads(cached_kanji_schema(config).read_text(encoding="utf-8"))
    )
    entries = meanings = changed = 0
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(output) as output_zip:
        index = json.loads(output_zip.read("index.json"))
        if index.get("sourceLanguage") != "ja" or index.get("targetLanguage") != "ru":
            raise ValueError("output language metadata is not ja -> ru")
        if index.get("title") != config.raw["product"]["title"]:
            raise ValueError("output title differs from the configured product title")
        if set(source_zip.namelist()) != set(output_zip.namelist()):
            raise ValueError("output archive members differ from the source")
        for _bank_number, name in source_banks(source_zip):
            source_rows = json.loads(source_zip.read(name))
            output_rows = json.loads(output_zip.read(name))
            schema_validator(output_rows)
            if len(source_rows) != len(output_rows):
                raise ValueError(f"entry count changed in {name}")
            for source_row, output_row in zip(source_rows, output_rows, strict=True):
                if source_row[:4] != output_row[:4] or source_row[5] != output_row[5]:
                    raise ValueError(f"protected KANJIDIC fields changed in {name}")
                if not output_row[4] or len(set(output_row[4])) != len(output_row[4]):
                    raise ValueError(f"invalid Russian meanings in {name}")
                for item in output_row[4]:
                    exact_source_acronym = (
                        item in source_row[4]
                        and re.fullmatch(r"[A-Z][A-Z0-9.+/-]{1,11}", item) is not None
                    )
                    if not re.search(r"[А-Яа-яЁё]", item) and not exact_source_acronym:
                        raise ValueError(f"a Russian meaning lacks Cyrillic text in {name}")
                entries += 1
                meanings += len(output_row[4])
                changed += source_row[4] != output_row[4]
        source_tags = json.loads(source_zip.read("tag_bank_1.json"))
        output_tags = json.loads(output_zip.read("tag_bank_1.json"))
        for source_tag, output_tag in zip(source_tags, output_tags, strict=True):
            if source_tag[:3] != output_tag[:3] or source_tag[4:] != output_tag[4:]:
                raise ValueError("KANJIDIC tag identity changed")
    if entries != 10_350 or changed != entries:
        raise ValueError(f"translation coverage differs: {changed}/{entries}")
    return {
        "entries": entries, "translated_entries": changed, "russian_meanings": meanings,
        "sha256": sha256_file(output), "schema": "Yomitan kanji bank v3",
    }


def accept(config: Config, run_id: int) -> dict[str, int]:
    database = Database(config)
    connection = database.connect()
    try:
        result = accept_deterministic_translations(connection, run_id)
        resolved = connection.execute(
            """UPDATE validation_issue vi SET resolved_at=CURRENT_TIMESTAMP,
            waiver_reason='superseded by a later deterministic-valid KANJIDIC translation'
            WHERE vi.run_id=? AND vi.severity='error' AND vi.resolved_at IS NULL
              AND vi.unit_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM translation t
                WHERE t.run_id=vi.run_id AND t.unit_id=vi.unit_id AND t.accepted=1
              )""",
            (run_id,),
        ).rowcount
        result["validation_issues_resolved"] = resolved
        unresolved_units = connection.execute(
            """SELECT COUNT(*) FROM translation_unit tu WHERE tu.run_id=? AND NOT EXISTS (
            SELECT 1 FROM translation t WHERE t.run_id=tu.run_id AND t.unit_id=tu.id AND t.accepted=1)""",
            (run_id,),
        ).fetchone()[0]
        result["unresolved_units"] = unresolved_units
        audit(connection, "resolve_superseded_kanjidic_issues", "run", run_id, result)
        connection.commit()
        return result
    finally:
        connection.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.kanjidic.luna.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("source", type=Path)
    accept_parser = commands.add_parser("accept")
    accept_parser.add_argument("--run-id", type=int, required=True)
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
    elif args.command == "accept":
        result = accept(config, args.run_id)
    elif args.command == "export":
        result = export(config, args.run_id, args.output.resolve())
    else:
        result = verify(config, args.source.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
