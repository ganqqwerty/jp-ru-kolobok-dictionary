#!/usr/bin/env python3
"""Re-ingest terminal blocked responses that pass the current validator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jitendex_ru.batch import claim, close_superseded_batches
from jitendex_ru.config import Config
from jitendex_ru.database import Database, transaction
from jitendex_ru.db import audit
from jitendex_ru.util import atomic_write, canonical_json
from jitendex_ru.validate_response import ingest_response, validate_worker_payload


BLOCKED_SQL = """
SELECT b.id AS batch_id,a.* FROM batch b
JOIN LATERAL (
  SELECT * FROM attempt WHERE batch_id=b.id AND response_path IS NOT NULL
  ORDER BY created_at DESC LIMIT 1
) a ON TRUE
WHERE b.run_id=? AND b.kind='translation' AND b.state='blocked'
ORDER BY b.id
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--worker-id", default="saved-response-revalidation")
    parser.add_argument(
        "--targets", type=Path,
        help="optional JSON array of batch_id, unit_id, and replacement target_text values",
    )
    args = parser.parse_args()

    config = Config.load(args.config)
    database = Database(config)
    connection = database.connect()
    model = config.model("translation")
    target_entries = json.loads(args.targets.read_text(encoding="utf-8")) if args.targets else []
    overrides: dict[str, dict[str, object]] = {}
    for entry in target_entries:
        if set(entry) != {"batch_id", "unit_id", "target_text"}:
            raise ValueError("each target entry must contain batch_id, unit_id, and target_text")
        batch_overrides = overrides.setdefault(entry["batch_id"], {})
        if entry["unit_id"] in batch_overrides:
            raise ValueError(f"duplicate target for {entry['unit_id']}")
        batch_overrides[entry["unit_id"]] = entry["target_text"]
    repaired: list[str] = []
    superseded: list[str] = []
    still_invalid: dict[str, list[str]] = {}
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased'", (args.run_id,),
        ).fetchone()[0]
        if active:
            raise RuntimeError(f"run {args.run_id} still has {active} leased batches")

        candidates = connection.execute(BLOCKED_SQL, (args.run_id,)).fetchall()
        unknown_batches = set(overrides) - {row["batch_id"] for row in candidates}
        if unknown_batches:
            raise ValueError(f"target batches are not blocked candidates: {sorted(unknown_batches)}")
        for old_attempt in candidates:
            response_path = Path(old_attempt["response_path"])
            if not response_path.is_file():
                still_invalid[old_attempt["batch_id"]] = ["response_file_missing"]
                continue
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            batch_overrides = overrides.get(old_attempt["batch_id"], {})
            found: set[str] = set()
            for translation in payload.get("translations", []):
                unit_id = translation.get("unit_id") if isinstance(translation, dict) else None
                if unit_id in batch_overrides:
                    translation["target_text"] = batch_overrides[unit_id]
                    translation["confidence"] = "high"
                    translation["review_reason"] = None
                    found.add(unit_id)
            missing = set(batch_overrides) - found
            if missing:
                raise ValueError(
                    f"target units are absent from {old_attempt['batch_id']}: {sorted(missing)}"
                )
            issues = validate_worker_payload(connection, old_attempt, payload)
            if issues:
                still_invalid[old_attempt["batch_id"]] = sorted({issue["code"] for issue in issues})
                continue

            with transaction(connection, immediate=True):
                updated = connection.execute(
                    "UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL "
                    "WHERE id=? AND run_id=? AND state='blocked'",
                    (old_attempt["batch_id"], args.run_id),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(f"batch state changed for {old_attempt['batch_id']}")
            item = claim(
                connection, args.worker_id, config.work_dir / "outbox",
                run_id=args.run_id, kind="translation", batch_id=old_attempt["batch_id"],
                model_id=model["id"], reasoning_effort=model["reasoning_effort"],
                transport="codex-agent",
            )
            if item is None:
                raise RuntimeError(f"could not claim {old_attempt['batch_id']}")
            new_response = Path(item["response_path"])
            atomic_write(new_response, canonical_json(payload))
            outcome = ingest_response(connection, new_response)
            with transaction(connection, immediate=True):
                connection.execute(
                    """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,
                    waiver_reason='superseded by saved response accepted under current validator'
                    WHERE resolved_at IS NULL AND attempt_id IN (
                      SELECT id FROM attempt WHERE batch_id=? AND id<>?
                    )""",
                    (old_attempt["batch_id"], item["attempt_id"]),
                )
                event = "targeted_batch_repair" if batch_overrides else "saved_response_revalidated"
                audit(connection, event, "attempt", item["attempt_id"], {
                    "batch_id": old_attempt["batch_id"],
                    "source_attempt_id": old_attempt["id"],
                    "replaced_unit_ids": sorted(batch_overrides),
                    "outcome": outcome,
                })
            repaired.append(old_attempt["batch_id"])
        superseded = close_superseded_batches(connection, args.run_id)
    finally:
        connection.close()
        database.close()

    print(json.dumps({
        "run_id": args.run_id,
        "repaired_batches": repaired,
        "superseded_batches": superseded,
        "still_invalid": still_invalid,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
