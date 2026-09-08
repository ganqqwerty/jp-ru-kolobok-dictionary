#!/usr/bin/env python3
"""Replay valid saved Luna responses into a deterministic recovery run."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

from jitendex_ru.batch import claim
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.db import audit
from jitendex_ru.util import atomic_write, canonical_json
from jitendex_ru.validate_response import ingest_response, validate_worker_payload


def load_targets(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None:
        return {}
    targets: dict[str, dict[str, object]] = defaultdict(dict)
    for entry in json.loads(path.read_text(encoding="utf-8")):
        if set(entry) != {"batch_id", "unit_id", "target_text"}:
            raise ValueError("each target entry must contain batch_id, unit_id, and target_text")
        batch_targets = targets[entry["batch_id"]]
        if entry["unit_id"] in batch_targets:
            raise ValueError(f"duplicate target for {entry['unit_id']}")
        batch_targets[entry["unit_id"]] = entry["target_text"]
    return dict(targets)


def apply_targets(payload: dict, replacements: dict[str, object]) -> set[str]:
    found: set[str] = set()
    for item in payload.get("translations", []):
        unit_id = item.get("unit_id") if isinstance(item, dict) else None
        if unit_id in replacements:
            item["target_text"] = replacements[unit_id]
            item["confidence"] = "high"
            item["review_reason"] = None
            found.add(unit_id)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-blocked", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")

    config = Config.load(args.config)
    database = Database(config)
    connection = database.connect()
    model = config.model("translation")
    targets = load_targets(args.targets)
    files_by_batch: dict[str, list[tuple[int, Path, dict]]] = defaultdict(list)
    unreadable = 0
    for path in args.responses.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            batch_id = payload["batch_id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            unreadable += 1
            continue
        files_by_batch[batch_id].append((path.stat().st_mtime_ns, path, payload))

    result: dict[str, object] = {
        "run_id": args.run_id,
        "response_files": sum(len(items) for items in files_by_batch.values()),
        "response_batches": len(files_by_batch),
        "unreadable_files": unreadable,
        "dry_run": args.dry_run,
    }
    issue_codes: Counter[str] = Counter()
    recovered = already_valid = unknown_batch = no_valid_response = 0
    used_targets: set[tuple[str, str]] = set()
    try:
        leased = connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased'", (args.run_id,),
        ).fetchone()[0]
        if leased:
            raise RuntimeError(f"run {args.run_id} has {leased} leased batches")

        for batch_id in sorted(files_by_batch):
            batch = connection.execute(
                "SELECT state,manifest_sha256 FROM batch WHERE id=? AND run_id=?",
                (batch_id, args.run_id),
            ).fetchone()
            if batch is None:
                unknown_batch += 1
                continue
            if batch["state"] == "deterministic_validated":
                already_valid += 1
                continue
            if batch["state"] not in ({"ready", "blocked"} if args.allow_blocked else {"ready"}):
                raise RuntimeError(f"unexpected state for {batch_id}: {batch['state']}")

            selected: tuple[Path, dict, set[str]] | None = None
            candidate_issues: Counter[str] = Counter()
            replacements = targets.get(batch_id, {})
            for _mtime, source_path, original in sorted(files_by_batch[batch_id], reverse=True):
                payload = copy.deepcopy(original)
                found = apply_targets(payload, replacements)
                missing = set(replacements) - found
                if missing:
                    candidate_issues["target_unit_missing"] += 1
                    continue
                issues = validate_worker_payload(connection, {"batch_id": batch_id}, payload)
                if not issues:
                    selected = source_path, payload, found
                    break
                candidate_issues.update(issue["code"] for issue in issues)
            if selected is None:
                no_valid_response += 1
                issue_codes.update(candidate_issues)
                continue

            source_path, payload, found = selected
            used_targets.update((batch_id, unit_id) for unit_id in found)
            if not args.dry_run:
                if batch["state"] == "blocked":
                    connection.execute(
                        """UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL
                        WHERE id=? AND state='blocked'""", (batch_id,),
                    )
                    audit(connection, "reopen_after_validator_fix", "batch", batch_id, {
                        "source_response": str(source_path),
                        "reason": "saved Luna response passes the revised deterministic validator",
                    })
                    connection.commit()
                item = claim(
                    connection, "saved-response-recovery", config.work_dir / "outbox",
                    run_id=args.run_id, kind="translation", batch_id=batch_id,
                    model_id=model["id"], reasoning_effort=model["reasoning_effort"],
                    transport="codex-agent",
                )
                if item is None:
                    raise RuntimeError(f"could not claim {batch_id}")
                response_path = Path(item["response_path"])
                atomic_write(response_path, canonical_json(payload))
                ingest_response(connection, response_path)
                audit(connection, "recover_saved_response", "attempt", item["attempt_id"], {
                    "batch_id": batch_id,
                    "source_response": str(source_path),
                    "replaced_unit_ids": sorted(found),
                })
                connection.commit()
            recovered += 1
            if args.limit is not None and recovered >= args.limit:
                break
    finally:
        connection.close()
        database.close()

    unused_targets = sorted(
        { (batch_id, unit_id) for batch_id, entries in targets.items() for unit_id in entries }
        - used_targets
    )
    result.update({
        "recovered_batches": recovered,
        "already_valid_batches": already_valid,
        "unknown_batches": unknown_batch,
        "batches_without_valid_response": no_valid_response,
        "invalid_issue_codes": dict(sorted(issue_codes.items())),
        "used_targets": len(used_targets),
        "unused_targets": [
            {"batch_id": batch_id, "unit_id": unit_id}
            for batch_id, unit_id in unused_targets
        ],
    })
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
