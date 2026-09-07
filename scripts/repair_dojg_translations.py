#!/usr/bin/env python3
"""Apply audited deterministic corrections to accepted DOJG translations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jitendex_ru.config import Config
from jitendex_ru.database import Database, transaction
from jitendex_ru.db import audit
from jitendex_ru.dojg import DOJG_JAPANESE_TEXT_RE, DOJG_PLACEHOLDER_RE, DOJG_ROLE
from jitendex_ru.util import sha256_bytes, sha256_file
from jitendex_ru.validate_response import (
    _plain_text_issues, allows_dojg_notation_only, dojg_allowed_english,
    dojg_untranslated_english,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("repairs", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.repairs.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "run_id", "repairs"}:
        raise ValueError("repair file has unexpected top-level fields")
    if payload["schema_version"] != 1 or payload["run_id"] != args.run_id:
        raise ValueError("repair file version or run ID mismatch")
    entries = payload["repairs"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("repair file must contain a non-empty repairs array")
    unit_ids = [entry.get("unit_id") for entry in entries if isinstance(entry, dict)]
    if len(unit_ids) != len(entries) or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("repair unit IDs must be present and unique")
    artifact_sha256 = sha256_file(args.repairs)

    config = Config.load(args.config)
    database = Database(config)
    connection = database.connect()
    changed: list[str] = []
    try:
        leased = connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased'", (args.run_id,),
        ).fetchone()[0]
        if leased:
            raise RuntimeError(f"run {args.run_id} still has {leased} leased batches")
        with transaction(connection, immediate=True):
            for entry in entries:
                if set(entry) != {"unit_id", "target_text", "reason"}:
                    raise ValueError(f"repair entry has unexpected fields: {entry.get('unit_id')}")
                target = entry["target_text"]
                row = connection.execute(
                    """SELECT t.id AS translation_id,t.target_text,t.target_sha256,
                    tu.source_text,tu.protected_tokens_json,tu.role
                    FROM translation t JOIN translation_unit tu ON tu.id=t.unit_id
                    WHERE t.run_id=? AND t.unit_id=? AND t.accepted=1""",
                    (args.run_id, entry["unit_id"]),
                ).fetchone()
                if row is None or row["role"] != DOJG_ROLE:
                    raise ValueError(f"accepted DOJG translation not found: {entry['unit_id']}")
                protected = json.loads(row["protected_tokens_json"])
                issues = _plain_text_issues(
                    target, protected,
                    allow_no_cyrillic=allows_dojg_notation_only(row["source_text"], target),
                    allowed_english=dojg_allowed_english(row["source_text"]),
                )
                if "\n" in target or "|" in target:
                    issues.append("dojg_structural_delimiter_added")
                if DOJG_PLACEHOLDER_RE.findall(target) != DOJG_PLACEHOLDER_RE.findall(row["source_text"]):
                    issues.append("dojg_placeholder_order_or_set_mismatch")
                if DOJG_JAPANESE_TEXT_RE.search(target):
                    issues.append("dojg_japanese_added")
                residual = dojg_untranslated_english(row["source_text"], target)
                if residual:
                    issues.append(f"dojg_untranslated_english:{','.join(residual)}")
                if issues:
                    raise ValueError({"unit_id": entry["unit_id"], "issues": issues})
                target_sha256 = sha256_bytes(target.encode())
                connection.execute(
                    """UPDATE translation SET target_text=?,target_sha256=?,review_reason=?
                    WHERE id=? AND run_id=? AND accepted=1""",
                    (target, target_sha256, entry["reason"], row["translation_id"], args.run_id),
                )
                audit(connection, "repair_accepted_dojg", "translation", row["translation_id"], {
                    "unit_id": entry["unit_id"],
                    "reason": entry["reason"],
                    "old_target_sha256": row["target_sha256"],
                    "new_target_sha256": target_sha256,
                    "repair_artifact_sha256": artifact_sha256,
                })
                changed.append(entry["unit_id"])
    finally:
        connection.close()
        database.close()

    print(json.dumps({
        "run_id": args.run_id,
        "repairs_applied": len(changed),
        "unit_ids": changed,
        "repair_artifact_sha256": artifact_sha256,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
