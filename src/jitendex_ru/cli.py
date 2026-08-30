from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .acquire import acquire
from .all_article_scope import select_all_article_scope
from .apple_dictionary import build_apple_dictionary, verify_apple_dictionary
from .batch import claim, make_batches, retry_or_split
from .build_dictionary import build, record_yomitan_smoke, verify
from .canonicalize import canonicalize_final_run
from .config import Config
from .database import Database
from .combined_frequency_scope import combined_coverage_report, select_combined_scope
from .db import audit
from .extract_units import extract_selected
from .goldendict import build_goldendict, verify_goldendict
from .import_jitendex import import_jitendex
from .jitendex_tags import import_jitendex_tags, ingest_approved_tag_rows, read_approved_tag_csv
from .import_kaishi import import_kaishi
from .jpdb_scope import (
    accept_deterministic_translations, coverage_report, reuse_accepted_translations, select_top_terms,
)
from .mdict import build_mdict, verify_mdict
from .pilot import build_pilot_selection, load_pilot_selection, verify_pilot_batches, write_pilot_selection
from .pocketbook import build_pocketbook, verify_pocketbook
from .prep_metrics import PrepMetrics
from .openai_requests import audit_run_input_tokens, write_token_audit
from .resolve_selection import apply_resolutions, generate_candidates, make_resolution_batches, selection_manifest_hash, unresolved_report
from .review import apply_adjudication, ingest_review, make_review_batches
from .run_integrity import run_history_fingerprint, source_identity_report
from .schema_validation import validate_archive
from .util import canonical_json, sha256_bytes, sha256_file
from .validate_response import ValidationFailure, ingest_response
from .yomitan_audit import (
    write_yomitan_archive_audit, write_yomitan_database_audit,
    write_yomitan_visible_latin_approval,
)
from .yomitan_remediation import write_yomitan_update_index
from .yomitan_plain import convert_yomitan_to_plain, verify_plain_yomitan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="translationctl")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser("acquire")
    commands.add_parser("import-sources")
    approved_tags = commands.add_parser("import-approved-tags")
    approved_tags.add_argument("--snapshot-id", type=int, required=True)
    approved_tags.add_argument("--csv", type=Path, required=True)
    commands.add_parser("resolve-scope")
    jpdb_scope = commands.add_parser("select-jpdb-scope")
    jpdb_scope.add_argument("path", type=Path)
    jpdb_scope.add_argument("--limit", type=int, default=5000)
    coverage = commands.add_parser("report-jpdb-coverage")
    coverage.add_argument("--run-id", type=int, required=True)
    all_scope = commands.add_parser("select-all-article-scope")
    all_scope.add_argument("--source-run-id", type=int, required=True)
    all_scope.add_argument("--add-articles", type=int, default=10_000)
    combined_scope = commands.add_parser("select-combined-frequency-scope")
    combined_scope.add_argument("--jpdb", type=Path, required=True)
    combined_scope.add_argument("--jpdb-limit", type=int, required=True)
    combined_scope.add_argument("--frequency-limit", type=int, required=True)
    combined_scope.add_argument("--aozora", type=Path, required=True)
    combined_scope.add_argument("--bccwj", type=Path, required=True)
    combined_scope.add_argument("--cc100", type=Path, required=True)
    combined_scope.add_argument("--monodicts", type=Path, required=True)
    combined_scope.add_argument("--wikipedia", type=Path, required=True)
    combined_scope.add_argument("--kokugo", type=Path, required=True)
    combined_coverage = commands.add_parser("report-combined-frequency-coverage")
    combined_coverage.add_argument("--run-id", type=int, required=True)
    reuse = commands.add_parser("reuse-translations")
    reuse.add_argument("--source-run-id", type=int, required=True)
    reuse.add_argument("--target-run-id", type=int, required=True)
    accept = commands.add_parser("accept-translations")
    accept.add_argument("--run-id", type=int, required=True)
    canonicalize = commands.add_parser("canonicalize-final-run")
    canonicalize.add_argument("--run-id", type=int, required=True)
    canonicalize.add_argument("--remediation-manifest", type=Path)
    resolution_batches = commands.add_parser("make-resolution-batches")
    resolution_batches.add_argument("--max-notes", type=int, default=10)
    resolution = commands.add_parser("apply-resolutions")
    resolution.add_argument("path", type=Path)
    resolution.add_argument("--actor", required=True)
    report = commands.add_parser("report")
    report.add_argument("topic", choices=("scope", "progress"))
    extract = commands.add_parser("extract-units")
    extract.add_argument("--run-id", type=int)
    extract.add_argument("--source-run-id", type=int)
    batches = commands.add_parser("make-batches")
    batches.add_argument("--run-id", type=int)
    batches.add_argument("--max-articles", type=int)
    batches.add_argument("--max-bytes", type=int)
    batches.add_argument("--max-units", type=int)
    claim_parser = commands.add_parser("claim")
    claim_parser.add_argument("--worker-id", required=True)
    claim_parser.add_argument("--run-id", type=int, required=True)
    claim_parser.add_argument("--kind", choices=("translation", "review"), required=True)
    claim_parser.add_argument("--batch-id")
    claim_parser.add_argument(
        "--transport", choices=("responses-sync", "batch-api", "codex-agent"),
        default="codex-agent",
    )
    ingest = commands.add_parser("ingest-response")
    ingest.add_argument("path", type=Path)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--run-id", type=int)
    identity_parser = commands.add_parser("verify-run-identity")
    identity_parser.add_argument("--run-id", type=int, required=True)
    identity_parser.add_argument("--baseline-run-id", type=int, default=2)
    history_parser = commands.add_parser("history-fingerprint")
    history_parser.add_argument("--run-id", type=int, required=True)
    pilot_parser = commands.add_parser("select-luna-pilot")
    pilot_parser.add_argument("--run-id", type=int, required=True)
    pilot_parser.add_argument("--output", type=Path, required=True)
    pilot_batches_parser = commands.add_parser("make-pilot-batches")
    pilot_batches_parser.add_argument("--run-id", type=int, required=True)
    pilot_batches_parser.add_argument("--selection", type=Path, required=True)
    verify_pilot_parser = commands.add_parser("verify-pilot-batches")
    verify_pilot_parser.add_argument("--run-id", type=int, required=True)
    verify_pilot_parser.add_argument("--selection", type=Path, required=True)
    token_parser = commands.add_parser("audit-input-tokens")
    token_parser.add_argument("--run-id", type=int, required=True)
    token_parser.add_argument("--output", type=Path, required=True)
    retry = commands.add_parser("retry")
    retry.add_argument("batch_id")
    review_batches = commands.add_parser("make-review-batches")
    review_batches.add_argument("--run-id", type=int)
    review_ingest = commands.add_parser("ingest-review")
    review_ingest.add_argument("path", type=Path)
    adjudicate = commands.add_parser("adjudicate-review")
    adjudicate.add_argument("path", type=Path)
    adjudicate.add_argument("--actor", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--run-id", type=int)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--enable-yomitan-updates", action="store_true")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("path", type=Path)
    verify_parser.add_argument("--require-yomitan-updates", action="store_true")
    verify_parser.add_argument("--lexical-approval", type=Path)
    goldendict_parser = commands.add_parser("export-goldendict")
    goldendict_parser.add_argument("--run-id", type=int)
    goldendict_parser.add_argument("--output", type=Path, required=True)
    verify_goldendict_parser = commands.add_parser("verify-goldendict")
    verify_goldendict_parser.add_argument("path", type=Path)
    pocketbook_parser = commands.add_parser("export-pocketbook")
    pocketbook_parser.add_argument("--run-id", type=int)
    pocketbook_parser.add_argument("--output", type=Path, required=True)
    pocketbook_parser.add_argument("--compiler", type=Path, required=True)
    pocketbook_parser.add_argument("--compiler-sha256", required=True)
    pocketbook_parser.add_argument("--language-dir", type=Path, required=True)
    verify_pocketbook_parser = commands.add_parser("verify-pocketbook")
    verify_pocketbook_parser.add_argument("path", type=Path)
    apple_parser = commands.add_parser("export-apple-dictionary")
    apple_parser.add_argument("--run-id", type=int)
    apple_parser.add_argument("--output", type=Path, required=True)
    apple_parser.add_argument("--build-tool", type=Path, required=True)
    apple_parser.add_argument("--build-tool-sha256", required=True)
    verify_apple_parser = commands.add_parser("verify-apple-dictionary")
    verify_apple_parser.add_argument("path", type=Path)
    mdict_parser = commands.add_parser("export-mdict")
    mdict_parser.add_argument("--run-id", type=int)
    mdict_parser.add_argument("--output", type=Path, required=True)
    verify_mdict_parser = commands.add_parser("verify-mdict")
    verify_mdict_parser.add_argument("path", type=Path)
    smoke_parser = commands.add_parser("record-yomitan-smoke")
    smoke_parser.add_argument("path", type=Path)
    smoke_parser.add_argument("--actor", required=True)
    localization_audit = commands.add_parser("audit-yomitan-archive")
    localization_audit.add_argument("path", type=Path)
    localization_audit.add_argument("--output", type=Path, required=True)
    localization_audit.add_argument("--run-id", type=int)
    database_localization_audit = commands.add_parser("audit-yomitan-localization")
    database_localization_audit.add_argument("--run-id", type=int, required=True)
    database_localization_audit.add_argument("--output", type=Path, required=True)
    database_localization_audit.add_argument("--archive", type=Path)
    update_index = commands.add_parser("generate-yomitan-update-index")
    update_index.add_argument("path", type=Path)
    update_index.add_argument("--output", type=Path, required=True)
    latin_approval = commands.add_parser("approve-yomitan-visible-latin")
    latin_approval.add_argument("path", type=Path)
    latin_approval.add_argument("--output", type=Path, required=True)
    plain_export = commands.add_parser("export-yomitan-plain")
    plain_export.add_argument("path", type=Path)
    plain_export.add_argument("--output", type=Path, required=True)
    plain_verify = commands.add_parser("verify-yomitan-plain")
    plain_verify.add_argument("path", type=Path)
    plain_verify.add_argument("--source", type=Path)
    return parser


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _snapshot(connection, config: Config, kind: str, local_path: Path) -> int:
    spec = config.raw[kind]
    connection.execute(
        """INSERT INTO source_snapshot(kind,version,url,sha256,local_path,extractor_version)
        VALUES (?,?,?,?,?,?) ON CONFLICT(kind,sha256) DO NOTHING""",
        (kind, spec["version"], spec["url"], spec["sha256"], str(local_path), config.raw["versions"]["extractor"]),
    )
    row = connection.execute("SELECT id FROM source_snapshot WHERE kind=? AND sha256=?", (kind, spec["sha256"])).fetchone()
    return row["id"]


def _active_run(connection, explicit: int | None = None) -> int:
    if explicit is not None:
        exists = connection.execute("SELECT 1 FROM run WHERE id=?", (explicit,)).fetchone()
        if not exists:
            raise ValueError(f"unknown run {explicit}")
        return explicit
    row = connection.execute("SELECT id FROM run WHERE state='active' ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("no active run; run extract-units first")
    return row["id"]


def _analyze_run_prep(connection, *tables: str) -> None:
    if connection.backend != "postgresql":
        return
    allowed = {"article", "run_article", "translation_unit", "translation", "batch", "batch_item"}
    if not tables or any(table not in allowed for table in tables):
        raise ValueError("invalid RUN-PREP table for ANALYZE")
    connection.execute(f"ANALYZE {','.join(tables)}")


def _versioned_prompt(config: Config, key: str) -> Path:
    version = config.raw["versions"][key]
    path = config.root / "prompts" / f"{version.replace('-', '_')}.txt"
    if not path.is_file():
        raise ValueError(f"configured {key} prompt does not exist: {path}")
    return path


def _create_run(connection, config: Config) -> int:
    total = connection.execute("SELECT COUNT(*) FROM kaishi_note").fetchone()[0]
    resolution_counts = connection.execute(
        """SELECT kn.id,COUNT(DISTINCT sd.sequence) count,
        COUNT(DISTINCT CASE WHEN sd.actor LIKE 'terra-adjudicator-%' THEN sd.sequence END) adjudicated_count
        FROM kaishi_note kn
        LEFT JOIN selection_decision sd ON sd.note_id=kn.id AND sd.decision='included' AND sd.review_status='accepted'
        GROUP BY kn.id"""
    ).fetchall()
    resolved = sum(row["count"] == 1 or (row["count"] > 1 and row["adjudicated_count"] == row["count"]) for row in resolution_counts)
    ambiguous = sum(row["count"] > 1 and row["adjudicated_count"] != row["count"] for row in resolution_counts)
    if not total or resolved != total or ambiguous:
        raise ValueError(f"selection gate failed: {resolved}/{total} notes have accepted single or explicitly adjudicated multi-sequence scope; {ambiguous} are ambiguous")
    jitendex_snapshots = connection.execute("SELECT DISTINCT snapshot_id FROM article WHERE selected=1").fetchall()
    kaishi_snapshots = connection.execute("SELECT DISTINCT snapshot_id FROM kaishi_note").fetchall()
    if len(jitendex_snapshots) != 1 or len(kaishi_snapshots) != 1:
        raise ValueError("a run must bind to exactly one Jitendex and one Kaishi snapshot")
    jitendex_snapshot_id = jitendex_snapshots[0]["snapshot_id"]
    kaishi_snapshot_id = kaishi_snapshots[0]["snapshot_id"]
    prompt = _versioned_prompt(config, "translation_prompt").read_bytes()
    review_prompt = _versioned_prompt(config, "review_prompt").read_bytes()
    terminology = (config.root / "terminology/ru-v1.json").read_bytes()
    limits = config.raw["batch"]
    pipeline_version = config.raw["versions"].get("pipeline", "scalar-v1")
    values = (
        jitendex_snapshot_id, kaishi_snapshot_id, selection_manifest_hash(connection),
        config.raw["versions"]["extractor"], sha256_bytes(prompt), sha256_bytes(review_prompt),
        sha256_bytes(terminology), canonical_json(limits).decode(), pipeline_version,
    )
    connection.execute(
        """INSERT INTO run(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,pipeline_version)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json) DO NOTHING""", values,
    )
    row = connection.execute(
        """SELECT id FROM run WHERE jitendex_snapshot_id=? AND kaishi_snapshot_id=?
        AND selection_sha256=? AND extractor_version=? AND prompt_sha256=?
        AND review_prompt_sha256=? AND terminology_sha256=? AND limits_json=? AND pipeline_version=?""", values,
    ).fetchone()
    audit(connection, "create", "run", row["id"], {})
    return row["id"]


def _scope_report(connection) -> dict[str, Any]:
    total = connection.execute("SELECT COUNT(*) FROM kaishi_note").fetchone()[0]
    resolved = connection.execute(
        "SELECT COUNT(DISTINCT note_id) FROM selection_decision WHERE decision='included' AND review_status='accepted'"
    ).fetchone()[0]
    candidates = connection.execute("SELECT COUNT(*) FROM selection_candidate").fetchone()[0]
    selected = connection.execute("SELECT COUNT(*) FROM article WHERE selected=1").fetchone()[0]
    return {"kaishi_notes": total, "resolved_notes": resolved, "unresolved_notes": total - resolved, "candidates": candidates, "selected_articles": selected, "unresolved": unresolved_report(connection)}


def _progress_report(connection) -> dict[str, Any]:
    runs = []
    for run in connection.execute("SELECT * FROM run ORDER BY id"):
        units = {row["status"]: row["count"] for row in connection.execute(
            "SELECT status,COUNT(*) count FROM translation_unit WHERE run_id=? GROUP BY status", (run["id"],)
        )}
        batches = {row["state"]: row["count"] for row in connection.execute(
            "SELECT state,COUNT(*) count FROM batch WHERE run_id=? GROUP BY state", (run["id"],)
        )}
        blocking = connection.execute(
            "SELECT COUNT(*) FROM validation_issue WHERE run_id=? AND severity='error' AND resolved_at IS NULL", (run["id"],)
        ).fetchone()[0]
        runs.append({"run_id": run["id"], "state": run["state"], "units": units, "batches": batches, "blocking_issues": blocking})
    return {"runs": runs}


def _validation_report(connection, run_id: int) -> dict[str, Any]:
    total = connection.execute("SELECT COUNT(*) FROM translation_unit WHERE run_id=?", (run_id,)).fetchone()[0]
    accepted = connection.execute(
        """SELECT COUNT(*) FROM translation_unit tu WHERE tu.run_id=? AND EXISTS (
        SELECT 1 FROM translation t WHERE t.unit_id=tu.id AND t.accepted=1)""", (run_id,)
    ).fetchone()[0]
    reviewed = connection.execute(
        """SELECT COUNT(*) FROM translation_unit tu WHERE tu.run_id=? AND EXISTS (
        SELECT 1 FROM translation t JOIN review r ON r.translation_id=t.id
        WHERE t.unit_id=tu.id AND r.decision IN ('accept','replace'))""", (run_id,)
    ).fetchone()[0]
    blocking = connection.execute(
        "SELECT COUNT(*) FROM validation_issue WHERE run_id=? AND severity='error' AND resolved_at IS NULL", (run_id,)
    ).fetchone()[0]
    batch_mismatches = connection.execute(
        """SELECT COUNT(*) FROM batch b WHERE b.run_id=? AND b.unit_count !=
        (SELECT COUNT(*) FROM batch_item bi WHERE bi.batch_id=b.id)""", (run_id,)
    ).fetchone()[0]
    return {
        "run_id": run_id, "units": total, "accepted_units": accepted, "reviewed_units": reviewed,
        "blocking_issues": blocking, "batch_membership_mismatches": batch_mismatches,
        "release_ready": total > 0 and accepted == total and reviewed == total and blocking == 0 and batch_mismatches == 0,
    }


def execute(args: argparse.Namespace) -> Any:
    if args.command == "audit-yomitan-archive":
        report = write_yomitan_archive_audit(
            args.path.resolve(), args.output.resolve(), run_id=args.run_id,
        )
        return {
            "output": str(args.output.resolve()),
            "archive_sha256": report["archive_sha256"],
            "article_count": report["article_count"],
            "issue_counts": report["issue_counts"],
        }
    if args.command == "generate-yomitan-update-index":
        index = write_yomitan_update_index(args.path.resolve(), args.output.resolve())
        return {"output": str(args.output.resolve()), "revision": index["revision"]}
    if args.command == "approve-yomitan-visible-latin":
        approval = write_yomitan_visible_latin_approval(
            args.path.resolve(), args.output.resolve(),
        )
        return {
            "output": str(args.output.resolve()),
            "classification_counts": approval["classification_counts"],
            "review_records_sha256": approval["review_records_sha256"],
        }
    if args.command == "export-yomitan-plain":
        result = convert_yomitan_to_plain(args.path.resolve(), args.output.resolve())
        result.update(verify_plain_yomitan(args.output.resolve(), source=args.path.resolve()))
        config = Config.load(args.config)
        result.update(validate_archive(
            args.output.resolve(), config.work_dir / "schemas" / "pinned-yomitan",
        ))
        return result
    if args.command == "verify-yomitan-plain":
        result = verify_plain_yomitan(
            args.path.resolve(), source=args.source.resolve() if args.source else None,
        )
        config = Config.load(args.config)
        result.update(validate_archive(
            args.path.resolve(), config.work_dir / "schemas" / "pinned-yomitan",
        ))
        return result
    config = Config.load(args.config)
    database = Database(config)
    if args.command == "init-db":
        database.migrate()
        database.close()
        return {"database_backend": config.db_backend, "initialized": True}
    # Keep the historical convenience for ephemeral SQLite databases.  A
    # production PostgreSQL database must only be migrated by the explicit
    # init-db command so an ordinary report/accept/build invocation cannot
    # change its schema mid-run.
    if config.db_backend == "sqlite":
        database.migrate()
    connection = database.connect()
    try:
        if args.command == "acquire":
            return {"files": [str(path) for path in acquire(config)]}
        if args.command == "import-sources":
            downloads = config.work_dir / "downloads"
            paths = {kind: downloads / config.raw[kind]["filename"] for kind in ("jitendex", "kaishi")}
            for kind, path in paths.items():
                if not path.exists() or sha256_file(path) != config.raw[kind]["sha256"]:
                    raise ValueError(f"missing or invalid pinned {kind} artifact; run acquire")
            jitendex_id = _snapshot(connection, config, "jitendex", paths["jitendex"])
            kaishi_id = _snapshot(connection, config, "kaishi", paths["kaishi"])
            result = {"jitendex_articles_added": import_jitendex(connection, jitendex_id, paths["jitendex"]),
                      "kaishi_notes_added": import_kaishi(connection, kaishi_id, paths["kaishi"]),
                      **import_jitendex_tags(connection, jitendex_id, paths["jitendex"])}
            connection.commit()
            return result
        if args.command == "import-approved-tags":
            source = args.csv.resolve()
            result = ingest_approved_tag_rows(
                connection, args.snapshot_id, read_approved_tag_csv(source),
                source_path=str(source), source_sha256=sha256_file(source),
            )
            connection.commit()
            return result
        if args.command == "resolve-scope":
            result = generate_candidates(connection)
            connection.commit()
            return result
        if args.command == "select-jpdb-scope":
            result = select_top_terms(connection, args.path.resolve(), args.limit)
            connection.commit()
            return result
        if args.command == "report-jpdb-coverage":
            return coverage_report(connection, args.run_id)
        if args.command == "select-all-article-scope":
            metrics = PrepMetrics("select_scope")
            with metrics.phase("statistics_update"):
                _analyze_run_prep(connection, "article", "run_article")
            with metrics.phase("scope_selection"):
                result = select_all_article_scope(connection, args.source_run_id, args.add_articles)
            connection.commit()
            return {**result, "phase_metrics": metrics.phases}
        if args.command == "select-combined-frequency-scope":
            result = select_combined_scope(
                connection,
                args.jpdb.resolve(),
                args.jpdb_limit,
                {
                    "aozora_bunko": args.aozora.resolve(),
                    "bccwj": args.bccwj.resolve(),
                    "cc100": args.cc100.resolve(),
                    "monodicts_206k": args.monodicts.resolve(),
                    "wikipedia_v2": args.wikipedia.resolve(),
                    "kokugo_jiten": args.kokugo.resolve(),
                },
                args.frequency_limit,
            )
            connection.commit()
            return result
        if args.command == "report-combined-frequency-coverage":
            return combined_coverage_report(connection, args.run_id)
        if args.command == "reuse-translations":
            result = reuse_accepted_translations(connection, args.source_run_id, args.target_run_id)
            connection.commit()
            metrics = PrepMetrics("reuse_translations")
            with metrics.phase("statistics_update"):
                _analyze_run_prep(connection, "translation_unit", "translation")
            connection.commit()
            return {**result, "phase_metrics": {**result["phase_metrics"], **metrics.phases}}
        if args.command == "accept-translations":
            result = accept_deterministic_translations(connection, args.run_id)
            connection.commit()
            return result
        if args.command == "canonicalize-final-run":
            result = canonicalize_final_run(
                connection, args.run_id, remediation_manifest=args.remediation_manifest,
            )
            connection.commit()
            return result
        if args.command == "audit-yomitan-localization":
            report = write_yomitan_database_audit(
                connection,
                args.run_id,
                args.output.resolve(),
                archive_path=args.archive.resolve() if args.archive else None,
            )
            return {
                "output": str(args.output.resolve()),
                "accepted_target_count": report["accepted_target_count"],
                "issue_counts": report["issue_counts"],
            }
        if args.command == "make-resolution-batches":
            result = make_resolution_batches(connection, config.work_dir / "selection-inbox", args.max_notes)
            connection.commit()
            return result
        if args.command == "apply-resolutions":
            result = {"resolutions_applied": apply_resolutions(connection, args.path, args.actor)}
            connection.commit()
            return result
        if args.command == "report":
            return _scope_report(connection) if args.topic == "scope" else _progress_report(connection)
        if args.command == "extract-units":
            metrics = PrepMetrics("extract_units")
            if args.run_id:
                run_id = args.run_id
            else:
                with metrics.phase("run_creation"):
                    run_id = _create_run(connection, config)
            result = {"run_id": run_id, **extract_selected(connection, run_id, args.source_run_id)}
            connection.commit()
            with metrics.phase("statistics_update"):
                _analyze_run_prep(connection, "run_article", "translation_unit")
            connection.commit()
            return {**result, "phase_metrics": {**metrics.phases, **result["phase_metrics"]}}
        if args.command == "make-batches":
            run_id = _active_run(connection, args.run_id)
            limits = config.raw["batch"]
            terminology = json.loads((config.root / "terminology/ru-v1.json").read_text(encoding="utf-8"))
            result = make_batches(
                connection, run_id, config.work_dir / "inbox", terminology,
                args.max_articles or limits["soft_max_articles"],
                args.max_bytes or limits["soft_max_bytes"],
                args.max_units or limits["soft_max_units"],
                limits["singleton_threshold_bytes"],
                limits["hard_max_article_bytes"], limits["hard_max_article_units"],
            )
            connection.commit()
            metrics = PrepMetrics("make_batches")
            with metrics.phase("statistics_update"):
                _analyze_run_prep(connection, "batch", "batch_item")
            connection.commit()
            return {**result, "phase_metrics": {**result["phase_metrics"], **metrics.phases}}
        if args.command == "claim":
            model = config.model(args.kind)
            return claim(
                connection, args.worker_id, config.work_dir / "outbox",
                run_id=args.run_id, kind=args.kind, batch_id=args.batch_id,
                model_id=model["id"], reasoning_effort=model["reasoning_effort"],
                transport=args.transport,
            ) or {"claimed": False}
        if args.command == "ingest-response":
            try:
                result = ingest_response(connection, args.path.resolve())
            except ValidationFailure:
                connection.commit()
                raise
            connection.commit()
            return result
        if args.command == "validate":
            return _validation_report(connection, _active_run(connection, args.run_id))
        if args.command == "verify-run-identity":
            result = source_identity_report(connection, args.run_id, args.baseline_run_id)
            if not result["passed"]:
                raise ValueError(f"run source identity gate failed: {result}")
            return result
        if args.command == "history-fingerprint":
            return run_history_fingerprint(connection, args.run_id)
        if args.command == "select-luna-pilot":
            terminology = json.loads((config.root / "terminology/ru-v1.json").read_text(encoding="utf-8"))
            protocol_path = config.root / "protocols/luna_pilot_v1.toml"
            payload = build_pilot_selection(
                connection, args.run_id, terminology,
                protocol_sha256=sha256_bytes(protocol_path.read_bytes()),
            )
            write_pilot_selection(args.output.resolve(), payload)
            return {key: payload[key] for key in (
                "run_id", "article_count", "unit_count", "role_counts",
                "feature_article_counts", "selection_sha256",
            )}
        if args.command == "make-pilot-batches":
            selection = load_pilot_selection(args.selection.resolve())
            article_ids = {article["article_id"] for article in selection["articles"]}
            limits = config.raw["batch"]
            terminology = json.loads((config.root / "terminology/ru-v1.json").read_text(encoding="utf-8"))
            result = make_batches(
                connection, args.run_id, config.work_dir / f"pilot-{limits['profile']}-inbox", terminology,
                limits["soft_max_articles"], limits["soft_max_bytes"], limits["soft_max_units"],
                limits["singleton_threshold_bytes"], limits["hard_max_article_bytes"],
                limits["hard_max_article_units"], article_ids,
            )
            result.update({
                "run_id": args.run_id,
                "profile": limits["profile"],
                "selection_sha256": selection["selection_sha256"],
            })
            connection.commit()
            return result
        if args.command == "verify-pilot-batches":
            selection = load_pilot_selection(args.selection.resolve())
            result = verify_pilot_batches(connection, args.run_id, selection, config.raw["batch"])
            if not result["passed"]:
                raise ValueError(f"pilot batch gate failed: {result}")
            return result
        if args.command == "audit-input-tokens":
            model = config.model("translation")
            prompt = _versioned_prompt(config, "translation_prompt").read_text(encoding="utf-8")
            report = audit_run_input_tokens(
                connection, args.run_id, prompt=prompt, model=model["id"],
                reasoning_effort=model["reasoning_effort"],
                api_key=os.environ.get("OPENAI_API_KEY", ""),
            )
            if not report["passed"]:
                raise ValueError(f"input-token headroom gate failed: {report}")
            write_token_audit(args.output.resolve(), report)
            return {key: report[key] for key in report if key != "requests"}
        if args.command == "retry":
            result = retry_or_split(connection, args.batch_id)
            connection.commit()
            return result
        if args.command == "make-review-batches":
            result = make_review_batches(connection, _active_run(connection, args.run_id), config.work_dir / "review-inbox")
            connection.commit()
            return result
        if args.command == "ingest-review":
            result = ingest_review(connection, args.path.resolve())
            connection.commit()
            return result
        if args.command == "adjudicate-review":
            result = apply_adjudication(connection, args.path.resolve(), args.actor)
            connection.commit()
            return result
        if args.command == "build":
            result = build(
                connection, _active_run(connection, args.run_id), args.output.resolve(),
                updatable=args.enable_yomitan_updates,
            )
            connection.commit()
            return result
        if args.command == "verify":
            result = verify(
                connection, args.path.resolve(),
                require_updatable=args.require_yomitan_updates,
                lexical_approval=(
                    args.lexical_approval.resolve() if args.lexical_approval else None
                ),
            )
            result.update(validate_archive(args.path.resolve(), config.work_dir / "schemas" / "pinned-yomitan"))
            connection.commit()
            return result
        if args.command == "export-goldendict":
            result = build_goldendict(
                connection, _active_run(connection, args.run_id), args.output.resolve(),
            )
            connection.commit()
            return result
        if args.command == "verify-goldendict":
            result = verify_goldendict(connection, args.path.resolve())
            connection.commit()
            return result
        if args.command == "export-pocketbook":
            result = build_pocketbook(
                connection, _active_run(connection, args.run_id), args.output.resolve(),
                compiler=args.compiler.resolve(),
                compiler_sha256=args.compiler_sha256,
                language_dir=args.language_dir.resolve(),
            )
            connection.commit()
            return result
        if args.command == "verify-pocketbook":
            result = verify_pocketbook(connection, args.path.resolve())
            connection.commit()
            return result
        if args.command == "export-apple-dictionary":
            result = build_apple_dictionary(
                connection, _active_run(connection, args.run_id), args.output.resolve(),
                build_tool=args.build_tool.resolve(),
                build_tool_sha256=args.build_tool_sha256,
            )
            connection.commit()
            return result
        if args.command == "verify-apple-dictionary":
            result = verify_apple_dictionary(connection, args.path.resolve())
            connection.commit()
            return result
        if args.command == "export-mdict":
            result = build_mdict(
                connection, _active_run(connection, args.run_id), args.output.resolve(),
            )
            connection.commit()
            return result
        if args.command == "verify-mdict":
            result = verify_mdict(connection, args.path.resolve())
            connection.commit()
            return result
        if args.command == "record-yomitan-smoke":
            result = record_yomitan_smoke(connection, args.path.resolve(), args.actor)
            connection.commit()
            return result
        raise AssertionError(args.command)
    finally:
        connection.close()
        database.close()


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(_parser().parse_args(argv))
        _print(result)
        return 0
    except ValidationFailure as error:
        _print({"error": str(error), "issues": error.issues})
        return 2
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
