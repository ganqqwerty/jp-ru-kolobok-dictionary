#!/usr/bin/env python3
"""Prepare and export the JP->RU Surasura Yomitan translation run."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

from jitendex_ru.apply_translations import apply_article
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.extract_units import extract_selected
from jitendex_ru.import_jitendex import import_jitendex
from jitendex_ru.util import canonical_json, sha256_bytes, sha256_file


TERM_BANK_RE = re.compile(r"term_bank_\d+\.json")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def versioned_prompt(config: Config, key: str) -> bytes:
    version = config.raw["versions"][key]
    return (config.root / "prompts" / f"{version.replace('-', '_')}.txt").read_bytes()


def ensure_snapshot(connection, kind: str, source: Path, extractor: str) -> int:
    digest = sha256_file(source)
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES (?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        (kind, "surasura-2023-03-22", "http://sura-sura.com/", digest, str(source), extractor),
    )
    return connection.execute(
        "SELECT id FROM source_snapshot WHERE kind=? AND sha256=?", (kind, digest),
    ).fetchone()["id"]


def prepare(config: Config, source: Path) -> dict[str, object]:
    database = Database(config)
    connection = database.connect()
    try:
        extractor = config.raw["versions"]["extractor"]
        source_id = ensure_snapshot(connection, "jitendex", source, extractor)
        scope_id = ensure_snapshot(connection, "kaishi", source, extractor)
        articles_added = import_jitendex(connection, source_id, source)
        connection.execute("UPDATE article SET selected=CASE WHEN snapshot_id=? THEN 1 ELSE 0 END", (source_id,))
        prompt_hash = sha256_bytes(versioned_prompt(config, "translation_prompt"))
        review_hash = sha256_bytes(versioned_prompt(config, "review_prompt"))
        terminology_hash = sha256_bytes((config.root / "terminology/ru-v1.json").read_bytes())
        limits = canonical_json(config.raw["batch"]).decode()
        selection_hash = sha256_bytes(f"surasura:{sha256_file(source)}".encode())
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
        result = extract_selected(connection, run_id)
        audit(connection, "prepare_surasura", "run", run_id, {"source": str(source)})
        connection.commit()
        return {"run_id": run_id, "articles_added": articles_added, **result}
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
        source = connection.execute(
            """SELECT ss.* FROM run r JOIN source_snapshot ss ON ss.id=r.jitendex_snapshot_id
            WHERE r.id=?""", (run_id,),
        ).fetchone()
        articles = connection.execute(
            """SELECT a.* FROM run_article ra JOIN article a ON a.id=ra.article_id
            WHERE ra.run_id=? ORDER BY a.bank_number,a.entry_ordinal""", (run_id,),
        ).fetchall()
        unit_count = connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,),
        ).fetchone()[0]
        rows = [apply_article(connection, run_id, article) for article in articles]
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(source["local_path"]) as archive:
            index = json.loads(archive.read("index.json"))
            index.update({
                "title": "surasura 擬声語 — русский",
                "revision": "surasura-ru-2026.08.30",
                "description": "Японско-русский словарь звукоподражаний и образных слов. Производный перевод словаря surasura.",
            })
            files["index.json"] = canonical_json(index)
            for name in archive.namelist():
                if name != "index.json" and not TERM_BANK_RE.fullmatch(name):
                    files[name] = archive.read(name)
        files["term_bank_1.json"] = canonical_json(rows)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as archive:
            for name in sorted(files, key=lambda item: (item != "index.json", item)):
                write_member(archive, name, files[name])
        result = {"run_id": run_id, "articles": len(rows), "units": unit_count,
                  "output": str(output), "sha256": sha256_file(output)}
        audit(connection, "export_surasura", "run", run_id, result)
        connection.commit()
        return result
    finally:
        connection.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.surasura.luna.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("source", type=Path)
    build = commands.add_parser("export")
    build.add_argument("--run-id", type=int, required=True)
    build.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = Config.load(args.config)
    result = prepare(config, args.source.resolve()) if args.command == "prepare" else export(
        config, args.run_id, args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
