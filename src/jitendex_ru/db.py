from __future__ import annotations

import json
import importlib.util
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Mapping

from .database import ConnectionLike, transaction as database_transaction


SCHEMA_VERSION = 10
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "sqlite"

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations/sqlite/0008_schema.sql"
SCHEMA = SCHEMA_PATH.read_text(encoding="utf-8")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(path: Path) -> None:
    with connect(path) as connection:
        # Journal mode is persistent. Set it once during initialization rather
        # than on every worker connection, where it can contend with writers.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        upgrade_path = MIGRATIONS / "0008_upgrade.py"
        spec = importlib.util.spec_from_file_location("jitendex_sqlite_upgrade_v8", upgrade_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load SQLite migration: {upgrade_path}")
        upgrade = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upgrade)
        upgrade.apply(connection)
        # The embedded compatibility upgrade establishes logical schema v8.
        # Later changes are applied only from numbered migration files.
        connection.execute("INSERT INTO schema_meta(version) VALUES (8) ON CONFLICT(version) DO NOTHING")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_meta")}
        for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_meta(version) VALUES (?) ON CONFLICT(version) DO NOTHING",
                (version,),
            )


@contextmanager
def transaction(connection: ConnectionLike, *, immediate: bool = False) -> Iterator[ConnectionLike]:
    with database_transaction(connection, immediate=immediate) as active:
        yield active


def audit(connection: ConnectionLike, event: str, entity: str, entity_id: object, details: object = None) -> None:
    connection.execute(
        "INSERT INTO audit_event(event_type,entity_type,entity_id,details_json) VALUES (?,?,?,?)",
        (event, entity, str(entity_id), json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
    )


def record_attempt_usage(
    connection: ConnectionLike,
    attempt_id: str,
    *,
    effective_model_id: str,
    reasoning_effort: str,
    transport: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    api_request_id: str | None = None,
    api_custom_id: str | None = None,
    api_job_id: str | None = None,
    finish_reason: str | None = None,
    status_reason: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Persist the effective request identity and raw API usage for an attempt."""
    if transport not in {"responses-sync", "batch-api", "codex-agent"}:
        raise ValueError(f"unsupported attempt transport: {transport}")
    counts = (input_tokens, cached_input_tokens, output_tokens, total_tokens)
    if any(value < 0 for value in counts):
        raise ValueError("token counts cannot be negative")
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input tokens cannot exceed input tokens")
    if total_tokens != input_tokens + output_tokens:
        raise ValueError("total tokens must equal input plus output tokens")
    if latency_ms is not None and latency_ms < 0:
        raise ValueError("latency cannot be negative")
    cursor = connection.execute(
        """UPDATE attempt SET effective_model_id=?,reasoning_effort=?,transport=?,
        api_request_id=?,api_custom_id=?,api_job_id=?,input_tokens=?,cached_input_tokens=?,
        output_tokens=?,total_tokens=?,finish_reason=?,status_reason=?,latency_ms=? WHERE id=?""",
        (
            effective_model_id, reasoning_effort, transport, api_request_id, api_custom_id,
            api_job_id, input_tokens, cached_input_tokens, output_tokens, total_tokens,
            finish_reason, status_reason, latency_ms, attempt_id,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"unknown attempt: {attempt_id}")
    audit(connection, "record_usage", "attempt", attempt_id, {
        "effective_model_id": effective_model_id,
        "reasoning_effort": reasoning_effort,
        "transport": transport,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    })


def record_attempt_cost(
    connection: ConnectionLike,
    attempt_id: str,
    *,
    price_snapshot_date: str,
    input_price_per_million: Decimal | str,
    cached_input_price_per_million: Decimal | str,
    output_price_per_million: Decimal | str,
    price_multiplier: Decimal | str = "1",
) -> Decimal:
    """Create the immutable price snapshot and exact computed cost for an attempt."""
    usage = connection.execute(
        """SELECT input_tokens,cached_input_tokens,output_tokens FROM attempt WHERE id=?""",
        (attempt_id,),
    ).fetchone()
    if usage is None:
        raise ValueError(f"unknown attempt: {attempt_id}")
    if any(usage[name] is None for name in ("input_tokens", "cached_input_tokens", "output_tokens")):
        raise ValueError("attempt usage must be recorded before cost")
    prices: Mapping[str, Decimal] = {
        "input": Decimal(input_price_per_million),
        "cached": Decimal(cached_input_price_per_million),
        "output": Decimal(output_price_per_million),
        "multiplier": Decimal(price_multiplier),
    }
    if any(value < 0 for value in prices.values()):
        raise ValueError("prices and price multiplier cannot be negative")
    uncached_tokens = usage["input_tokens"] - usage["cached_input_tokens"]
    cost = (
        Decimal(uncached_tokens) * prices["input"]
        + Decimal(usage["cached_input_tokens"]) * prices["cached"]
        + Decimal(usage["output_tokens"]) * prices["output"]
    ) * prices["multiplier"] / Decimal(1_000_000)
    connection.execute(
        """INSERT INTO attempt_cost_report(
        attempt_id,price_snapshot_date,input_price_per_million,cached_input_price_per_million,
        output_price_per_million,price_multiplier,input_tokens,cached_input_tokens,
        output_tokens,computed_cost) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            attempt_id, price_snapshot_date, str(prices["input"]), str(prices["cached"]),
            str(prices["output"]), str(prices["multiplier"]), usage["input_tokens"],
            usage["cached_input_tokens"], usage["output_tokens"], str(cost),
        ),
    )
    audit(connection, "record_cost", "attempt", attempt_id, {
        "price_snapshot_date": price_snapshot_date,
        "computed_cost": str(cost),
        "currency": "USD",
    })
    return cost
