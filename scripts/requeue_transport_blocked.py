#!/usr/bin/env python3
"""Requeue terminal Wadoku batches that ended without a model response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jitendex_ru.config import Config
from jitendex_ru.database import Database, transaction
from jitendex_ru.db import audit


BLOCKED_SQL = """
SELECT b.id AS batch_id,b.attempt_count,a.id AS attempt_id,a.error_json,a.response_path
FROM batch b
LEFT JOIN LATERAL (
  SELECT * FROM attempt WHERE batch_id=b.id ORDER BY created_at DESC LIMIT 1
) a ON TRUE
WHERE b.run_id=? AND b.kind='translation' AND b.state='blocked'
ORDER BY b.id
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config)
    database = Database(config)
    connection = database.connect()
    candidates: list[dict[str, object]] = []
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased'", (args.run_id,),
        ).fetchone()[0]
        if active and args.apply:
            raise RuntimeError(f"run {args.run_id} still has {active} leased batches")
        for row in connection.execute(BLOCKED_SQL, (args.run_id,)).fetchall():
            try:
                error = json.loads(row["error_json"] or "null")
            except json.JSONDecodeError:
                continue
            response = Path(row["response_path"]) if row["response_path"] else None
            if not isinstance(error, dict) or "transport_error" not in error:
                continue
            if response is not None and response.is_file():
                continue
            candidates.append({
                "batch_id": row["batch_id"],
                "attempt_id": row["attempt_id"],
                "attempt_count": row["attempt_count"],
            })
        if args.apply:
            with transaction(connection, immediate=True):
                for item in candidates:
                    changed = connection.execute(
                        """UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL
                        WHERE id=? AND run_id=? AND state='blocked'""",
                        (item["batch_id"], args.run_id),
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError(f"batch state changed for {item['batch_id']}")
                    audit(connection, "requeue_transport_block", "batch", item["batch_id"], {
                        "source_attempt_id": item["attempt_id"],
                        "attempt_count": item["attempt_count"],
                        "reason": "terminal attempt has a transport error and no response file",
                    })
    finally:
        connection.close()
        database.close()

    print(json.dumps({
        "run_id": args.run_id,
        "active_leases": active,
        "apply": args.apply,
        "transport_blocked_batches": candidates,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
