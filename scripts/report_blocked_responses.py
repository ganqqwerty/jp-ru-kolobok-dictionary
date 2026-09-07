#!/usr/bin/env python3
"""Report terminal blocked batches under the current response validator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json
from jitendex_ru.validate_response import validate_worker_payload


BLOCKED_SQL = """
SELECT b.id AS batch_id,a.* FROM batch b
LEFT JOIN LATERAL (
  SELECT * FROM attempt WHERE batch_id=b.id
  ORDER BY created_at DESC LIMIT 1
) a ON TRUE
WHERE b.run_id=? AND b.kind='translation' AND b.state='blocked'
ORDER BY b.id
"""


def error_value(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--targets", type=Path)
    args = parser.parse_args()

    config = Config.load(args.config)
    target_entries = json.loads(args.targets.read_text(encoding="utf-8")) if args.targets else []
    overrides: dict[str, dict[str, Any]] = {}
    for entry in target_entries:
        if set(entry) != {"batch_id", "unit_id", "target_text"}:
            raise ValueError("each target entry must contain batch_id, unit_id, and target_text")
        batch_overrides = overrides.setdefault(entry["batch_id"], {})
        if entry["unit_id"] in batch_overrides:
            raise ValueError(f"duplicate target for {entry['unit_id']}")
        batch_overrides[entry["unit_id"]] = entry["target_text"]
    database = Database(config)
    connection = database.connect()
    valid_saved: list[str] = []
    invalid: list[dict[str, Any]] = []
    try:
        candidates = connection.execute(BLOCKED_SQL, (args.run_id,)).fetchall()
        for attempt in candidates:
            response_path = Path(attempt["response_path"]) if attempt["response_path"] else None
            if response_path is None or not response_path.is_file():
                invalid.append({
                    "batch_id": attempt["batch_id"],
                    "attempt_id": attempt["id"],
                    "issues": [{"code": "response_file_missing"}],
                    "last_error": error_value(attempt["error_json"]),
                    "units": [],
                })
                continue
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            batch_overrides = overrides.get(attempt["batch_id"], {})
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
                raise ValueError(f"target units are absent from {attempt['batch_id']}: {sorted(missing)}")
            issues = validate_worker_payload(connection, attempt, payload)
            if not issues:
                valid_saved.append(attempt["batch_id"])
                continue
            translations = {
                item.get("unit_id"): item.get("target_text")
                for item in payload.get("translations", [])
                if isinstance(item, dict) and isinstance(item.get("unit_id"), str)
            }
            unit_ids = sorted({
                issue["unit_id"] for issue in issues if isinstance(issue.get("unit_id"), str)
            })
            units = []
            for unit_id in unit_ids:
                unit = connection.execute(
                    "SELECT article_id,role,source_text FROM translation_unit WHERE id=?",
                    (unit_id,),
                ).fetchone()
                units.append({
                    "unit_id": unit_id,
                    "article_id": unit["article_id"] if unit else None,
                    "role": unit["role"] if unit else None,
                    "source_text": unit["source_text"] if unit else None,
                    "target_text": translations.get(unit_id),
                })
            invalid.append({
                "batch_id": attempt["batch_id"],
                "attempt_id": attempt["id"],
                "issues": issues,
                "last_error": error_value(attempt["error_json"]),
                "units": units,
            })
        unknown_batches = set(overrides) - {row["batch_id"] for row in candidates}
        if unknown_batches:
            raise ValueError(f"target batches are not blocked candidates: {sorted(unknown_batches)}")
    finally:
        connection.close()
        database.close()

    report = {
        "run_id": args.run_id,
        "blocked_batches": len(valid_saved) + len(invalid),
        "valid_saved_response_batches": valid_saved,
        "invalid_batches": invalid,
        "target_replacements": len(target_entries),
    }
    data = canonical_json(report) + b"\n"
    if args.output:
        atomic_write(args.output.resolve(), data)
        print(json.dumps({
            "output": str(args.output.resolve()),
            "blocked_batches": report["blocked_batches"],
            "valid_saved_responses": len(valid_saved),
            "invalid_batches": len(invalid),
            "target_replacements": len(target_entries),
        }, sort_keys=True))
    else:
        print(data.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
