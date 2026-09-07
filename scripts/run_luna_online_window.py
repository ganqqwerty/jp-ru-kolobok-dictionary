#!/usr/bin/env python3
"""Run one productive, fixed-concurrency Luna window against PostgreSQL."""

from __future__ import annotations

import argparse
import fastjsonschema
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.run_integrity import workload_progress


ROOT = Path(__file__).resolve().parents[1]
WINDOW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
ACTIVE_DATABASE: Database | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runner_revision_sha256(config_path: Path | None = None, config: Config | None = None) -> str:
    selected_config = config_path.resolve() if config_path is not None else ROOT / "config.luna.toml"
    paths = [
        selected_config, ROOT / "pyproject.toml", ROOT / "uv.lock",
        ROOT / "scripts/run_codex_batches.py", ROOT / "scripts/run_luna_online_window.py",
    ]
    if config is None:
        paths.extend([ROOT / "prompts/translate_luna_v4.txt", ROOT / "terminology/ru-v1.json"])
    else:
        prompt_name = config.raw["versions"]["translation_prompt"].replace("-", "_")
        terminology_name = config.raw["versions"]["terminology"]
        paths.extend([
            config.root / "prompts" / f"{prompt_name}.txt",
            config.root / "terminology" / f"{terminology_name}.json",
        ])
    paths.extend(sorted((ROOT / "src/jitendex_ru").glob("*.py")))
    paths.extend(sorted((ROOT / "migrations").rglob("*")))
    digest = hashlib.sha256()
    for path in sorted(
        (path for path in paths if path.is_file()),
        key=lambda item: str(item.relative_to(ROOT)),
    ):
        relative = str(path.relative_to(ROOT)).encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def production_runner_active() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "scripts/run_codex_batches.py"], capture_output=True, text=True,
    )
    return any(line.strip() for line in result.stdout.splitlines())


def machine_snapshot() -> dict[str, Any]:
    def command(*parts: str) -> str:
        result = subprocess.run(parts, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"

    return {
        "captured_at": utc_now(), "platform": platform.platform(),
        "memory_pressure": command("memory_pressure", "-Q"),
        "vm_stat": command("vm_stat"), "swap_usage": command("sysctl", "vm.swapusage"),
        "power_state": command("pmset", "-g", "batt"),
    }


def postgres_snapshot(url: str, worker_prefix: str) -> dict[str, Any]:
    import psycopg
    with psycopg.connect(url) as connection:
        activity = connection.execute(
            """SELECT state,wait_event_type,wait_event,COUNT(*) FROM pg_stat_activity
            WHERE datname=current_database() GROUP BY state,wait_event_type,wait_event
            ORDER BY state,wait_event_type,wait_event"""
        ).fetchall()
        locks = connection.execute(
            """SELECT mode,granted,COUNT(*) FROM pg_locks l JOIN pg_database d ON d.oid=l.database
            WHERE d.datname=current_database() GROUP BY mode,granted ORDER BY mode,granted"""
        ).fetchall()
        database = connection.execute(
            """SELECT xact_commit,xact_rollback,blks_read,blks_hit,temp_files,temp_bytes,
            deadlocks,session_time,active_time,idle_in_transaction_time
            FROM pg_stat_database WHERE datname=current_database()"""
        ).fetchone()
        statements = connection.execute(
            """SELECT COUNT(*),COALESCE(SUM(calls),0),COALESCE(SUM(total_exec_time),0),
            COALESCE(SUM(rows),0),COALESCE(SUM(shared_blks_read),0),COALESCE(SUM(shared_blks_hit),0)
            FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())"""
        ).fetchone()
        duplicate_claims = connection.execute(
            """SELECT COUNT(*) FROM (SELECT batch_id FROM attempt WHERE outcome='claimed'
            AND worker_id LIKE %s GROUP BY batch_id HAVING COUNT(*) > 1) duplicate""",
            (worker_prefix + "-%",),
        ).fetchone()[0]
        return {
            "captured_at": utc_now(), "activity": [list(row) for row in activity],
            "locks": [list(row) for row in locks],
            "database": [float(value) for value in database],
            "statements": [float(value) for value in statements],
            "duplicate_claimed_batches": int(duplicate_claims),
        }


def monitoring_summary(samples: list[dict[str, Any]]) -> dict[str, float | int]:
    ungranted = [
        sum(int(row[2]) for row in sample["locks"] if not bool(row[1]))
        for sample in samples
    ]
    lock_waiters = [
        sum(int(row[3]) for row in sample["activity"] if row[1] == "Lock")
        for sample in samples
    ]
    return {
        "sample_count": len(samples),
        "lock_wait_sample_count": sum(value > 0 for value in ungranted),
        "max_ungranted_locks": max(ungranted, default=0),
        "max_lock_waiting_sessions": max(lock_waiters, default=0),
        "max_duplicate_claimed_batches": max(
            (int(sample["duplicate_claimed_batches"]) for sample in samples), default=0,
        ),
        "postgresql_active_time_delta_milliseconds": max(
            float(samples[-1]["database"][8]) - float(samples[0]["database"][8]), 0.0,
        ) if len(samples) >= 2 else 0.0,
        "postgresql_statement_exec_delta_milliseconds": max(
            float(samples[-1]["statements"][2]) - float(samples[0]["statements"][2]), 0.0,
        ) if len(samples) >= 2 else 0.0,
    }


def phase(events: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [event for event in events if event.get("event") == "phase" and event.get("phase") == name]
    if len(matches) != 1:
        raise RuntimeError(f"online window requires exactly one {name} phase")
    return matches[0]


def metric_delta(before: dict[str, Any], after: dict[str, Any], key: str) -> float:
    value = float(after.get(key, 0)) - float(before.get(key, 0))
    if value < -1e-6:
        raise RuntimeError(f"cumulative runner metric went backwards: {key}")
    return max(value, 0.0)


def postflight(database: Database, run_id: int, worker_prefix: str) -> dict[str, int]:
    connection = database.connect()
    try:
        scalar = lambda sql, values=(): int(connection.execute(sql, values).fetchone()[0] or 0)
        return {
            "window_claimed_attempts": scalar(
                "SELECT COUNT(*) FROM attempt WHERE worker_id LIKE ? AND outcome='claimed'",
                (worker_prefix + "-%",),
            ),
            "global_claimed_attempts": scalar("SELECT COUNT(*) FROM attempt WHERE outcome='claimed'"),
            "leased_batches": scalar("SELECT COUNT(*) FROM batch WHERE run_id=? AND state='leased'", (run_id,)),
            "missing_units": scalar(
                """SELECT COUNT(*) FROM translation_unit tu WHERE tu.run_id=?
                AND tu.status='ready'
                AND NOT EXISTS (SELECT 1 FROM batch_item bi
                JOIN batch b ON b.id=bi.batch_id
                WHERE bi.unit_id=tu.id AND b.run_id=tu.run_id)""", (run_id,),
            ),
            "duplicate_translations": scalar(
                """SELECT COUNT(*) FROM (SELECT unit_id,COUNT(*) FROM translation WHERE run_id=?
                GROUP BY unit_id HAVING COUNT(*) > 1) duplicate""", (run_id,),
            ),
            "unresolved_blocking_errors": scalar(
                """SELECT COUNT(*) FROM validation_issue WHERE run_id=?
                AND severity IN ('blocking','error') AND resolved_at IS NULL""", (run_id,),
            ),
            "source_hash_mismatches": scalar(
                """SELECT COUNT(*) FROM validation_issue WHERE run_id=?
                AND code='source_hash_mismatch' AND resolved_at IS NULL""", (run_id,),
            ),
        }
    finally:
        connection.close()


def window_batch_ids(events: list[dict[str, Any]], complete: bool) -> list[str]:
    if complete:
        start = float(phase(events, "measurement")["elapsed_seconds"])
        end = float(phase(events, "drain")["elapsed_seconds"])
        selected = [
            event for event in events if event.get("event") == "completed"
            and start <= float(event.get("elapsed_seconds", -1)) <= end
        ]
    else:
        selected = [event for event in events if event.get("event") == "completed"]
    return [str(event["batch_id"]) for event in selected]


def workload_profile(
    database: Database, batch_ids: list[str], worker_prefix: str,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "request_batch_count": len(batch_ids), "unique_batch_count": len(set(batch_ids)),
        "serialized_bytes": 0, "batch_articles": 0, "batch_units": 0,
        "source_characters": 0, "protected_token_units": 0,
        "role_counts": {}, "validation_issue_codes": {},
    }
    if not batch_ids:
        return profile
    unique_ids = list(dict.fromkeys(batch_ids))
    placeholders = ",".join("?" for _ in unique_ids)
    connection = database.connect()
    try:
        rows = connection.execute(
            f"""SELECT b.id,b.serialized_bytes,b.article_count,b.unit_count,
            COALESCE(SUM(tu.byte_count),0) source_characters,
            COALESCE(SUM(CASE WHEN tu.protected_tokens_json<>'[]' THEN 1 ELSE 0 END),0)
              protected_token_units
            FROM batch b LEFT JOIN batch_item bi ON bi.batch_id=b.id
            LEFT JOIN translation_unit tu ON tu.id=bi.unit_id
            WHERE b.id IN ({placeholders}) GROUP BY b.id,b.serialized_bytes,
            b.article_count,b.unit_count""", tuple(unique_ids),
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        for batch_id in batch_ids:
            row = by_id[batch_id]
            profile["serialized_bytes"] += int(row["serialized_bytes"])
            profile["batch_articles"] += int(row["article_count"])
            profile["batch_units"] += int(row["unit_count"])
            profile["source_characters"] += int(row["source_characters"])
            profile["protected_token_units"] += int(row["protected_token_units"])
        roles = connection.execute(
            f"""SELECT bi.batch_id,tu.role,COUNT(*) count FROM batch_item bi
            JOIN translation_unit tu ON tu.id=bi.unit_id
            WHERE bi.batch_id IN ({placeholders}) GROUP BY bi.batch_id,tu.role""",
            tuple(unique_ids),
        ).fetchall()
        role_by_batch: dict[str, dict[str, int]] = {}
        for row in roles:
            role_by_batch.setdefault(str(row["batch_id"]), {})[str(row["role"])] = int(row["count"])
        for batch_id in batch_ids:
            for role, count in role_by_batch.get(batch_id, {}).items():
                profile["role_counts"][role] = profile["role_counts"].get(role, 0) + count
        issues = connection.execute(
            f"""SELECT vi.code,COUNT(*) count FROM validation_issue vi
            JOIN attempt a ON a.id=vi.attempt_id
            WHERE a.worker_id LIKE ? AND a.batch_id IN ({placeholders})
            GROUP BY vi.code ORDER BY vi.code""",
            (worker_prefix + "-%", *unique_ids),
        ).fetchall()
        profile["validation_issue_codes"] = {
            str(row["code"]): int(row["count"]) for row in issues
        }
        return profile
    finally:
        connection.close()


def summarize(
    events: list[dict[str, Any]], steady_seconds: float,
    monitor: list[dict[str, Any]], postflight_checks: dict[str, int],
) -> dict[str, Any]:
    measurement = phase(events, "measurement")
    drain = phase(events, "drain")
    shutdown = phase(events, "shutdown")
    start = float(measurement["elapsed_seconds"])
    end = float(drain["elapsed_seconds"])
    measured = end - start
    if measured + 1 < steady_seconds:
        raise RuntimeError(f"incomplete steady window: {measured:.3f} seconds")
    before_workload = measurement["workload"]
    after_workload = drain["workload"]
    workload_delta = {
        key: int(after_workload[key]) - int(before_workload[key]) for key in before_workload
    }
    if any(value < 0 for value in workload_delta.values()):
        raise RuntimeError("productive workload counters went backwards")
    completed = [
        event for event in events if event.get("event") == "completed"
        and start <= float(event.get("elapsed_seconds", -1)) <= end
    ]
    submitted = [
        event for event in events if event.get("event") == "submitted"
        and start <= float(event.get("elapsed_seconds", -1)) <= end
    ]
    drained = [
        event for event in events if event.get("event") == "completed"
        and end < float(event.get("elapsed_seconds", -1))
        <= float(shutdown["elapsed_seconds"])
    ]
    latencies = [float(event["latency_ms"]) for event in completed if event.get("latency_ms") is not None]
    before_metrics = measurement["metrics"]
    after_metrics = drain["metrics"]
    recoveries = []
    for event in completed:
        try:
            detail = json.loads(event.get("detail", ""))
        except (json.JSONDecodeError, TypeError):
            detail = {}
        recoveries.append(detail.get("recovery", {}) if isinstance(detail, dict) else {})
    batch_ids = [str(event["batch_id"]) for event in completed]
    batch_digest = hashlib.sha256()
    for batch_id in batch_ids:
        encoded = batch_id.encode()
        batch_digest.update(len(encoded).to_bytes(4, "big"))
        batch_digest.update(encoded)
    minutes = measured / 60
    transaction_ms = metric_delta(before_metrics, after_metrics, "transaction_milliseconds")
    result = {
        "complete": True,
        "measured_seconds": measured,
        "before": before_workload, "after": after_workload, "deltas": workload_delta,
        "rates": {f"{key}_per_minute": value / minutes for key, value in workload_delta.items()},
        "submitted": len(submitted), "completed": len(completed),
        "drain_completed": len(drained),
        "failed_attempts": sum(not bool(event.get("ok")) for event in completed),
        "input_tokens": sum(int(event.get("input_tokens", 0)) for event in completed),
        "cached_input_tokens": sum(int(event.get("cached_input_tokens", 0)) for event in completed),
        "output_tokens": sum(int(event.get("output_tokens", 0)) for event in completed),
        "rate_limits": sum(bool(event.get("rate_limit")) for event in completed),
        "timeouts": sum(bool(event.get("timeout")) for event in completed),
        "transport_failures": sum(bool(event.get("transport_failure")) for event in completed),
        "validation_rejections": sum(bool(event.get("validation_rejection")) for event in completed),
        "retries": sum(bool(event.get("retry")) for event in completed),
        "splits": sum(bool(event.get("split")) for event in completed),
        "stale_lease_rejections": sum(bool(item.get("stale_lease")) for item in recoveries),
        "claim_collisions": int(monitoring_summary(monitor)["max_duplicate_claimed_batches"]),
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "latency_p99_ms": percentile(latencies, 0.99),
        "worker_peak_memory_bytes": max(
            (int(event.get("worker_peak_memory_bytes", 0)) for event in completed + drained), default=0,
        ),
        "progress_query_seconds": metric_delta(before_metrics, after_metrics, "progress_query_seconds"),
        "claim_milliseconds": metric_delta(before_metrics, after_metrics, "claim_milliseconds"),
        "ingestion_milliseconds": metric_delta(before_metrics, after_metrics, "ingestion_milliseconds"),
        "pool_wait_milliseconds": metric_delta(before_metrics, after_metrics, "pool_wait_milliseconds"),
        "database_checkouts": round(metric_delta(before_metrics, after_metrics, "database_checkouts")),
        "database_retries": round(metric_delta(before_metrics, after_metrics, "database_retries")),
        "transaction_milliseconds": transaction_ms,
        "database_duty_cycle": transaction_ms / (measured * 1000),
        "average_claim_milliseconds": metric_delta(before_metrics, after_metrics, "claim_milliseconds") / max(len(submitted), 1),
        "average_ingestion_milliseconds": metric_delta(before_metrics, after_metrics, "ingestion_milliseconds") / max(len(completed), 1),
        "first_batch_id": batch_ids[0] if batch_ids else None,
        "last_batch_id": batch_ids[-1] if batch_ids else None,
        "ordered_batch_sha256": batch_digest.hexdigest(),
        "postflight": postflight_checks,
    }
    denominator = max(len(completed), 1)
    result["error_rates"] = {
        key: result[key] / denominator for key in (
            "failed_attempts", "rate_limits", "timeouts", "transport_failures",
            "validation_rejections", "stale_lease_rejections", "claim_collisions",
        )
    }
    return result


def incomplete_summary(
    events: list[dict[str, Any]], initial_workload: dict[str, int],
    postflight_checks: dict[str, int],
) -> dict[str, Any]:
    measurements = [
        event for event in events
        if event.get("event") == "phase" and event.get("phase") == "measurement"
    ]
    shutdowns = [
        event for event in events
        if event.get("event") == "phase" and event.get("phase") == "shutdown"
    ]
    before = measurements[0].get("workload", initial_workload) if measurements else initial_workload
    after = shutdowns[-1].get("workload", before) if shutdowns else before
    deltas = {key: max(int(after[key]) - int(before[key]), 0) for key in before}
    completed = [event for event in events if event.get("event") == "completed"]
    quota_boundaries = sum(bool(event.get("quota_boundary")) for event in completed)
    return {
        "complete": False,
        "incomplete_reason": "quota_boundary" if quota_boundaries else "external_interrupt",
        "measured_seconds": 0.0, "before": before, "after": after, "deltas": deltas,
        "rates": {f"{key}_per_minute": 0.0 for key in deltas},
        "completed": len(completed),
        "failed_attempts": sum(not bool(event.get("ok")) for event in completed),
        "quota_boundaries": quota_boundaries,
        "rate_limits": sum(bool(event.get("rate_limit")) for event in completed),
        "timeouts": sum(bool(event.get("timeout")) for event in completed),
        "transport_failures": sum(bool(event.get("transport_failure")) for event in completed),
        "validation_rejections": sum(bool(event.get("validation_rejection")) for event in completed),
        "postflight": postflight_checks,
    }


def main() -> int:
    global ACTIVE_DATABASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--ramp-seconds", type=float, default=30.0)
    parser.add_argument("--steady-seconds", type=float, default=600.0)
    parser.add_argument("--minimum-completed", type=int, default=500)
    parser.add_argument("--monitor-seconds", type=float, default=30.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not WINDOW_ID.fullmatch(args.window_id):
        parser.error("--window-id must use 3-64 lowercase letters, numbers, or hyphens")
    if args.concurrency < 1 or args.ramp_seconds < 0 or args.steady_seconds <= 0:
        parser.error("invalid concurrency or timing")
    if args.minimum_completed < 1 or args.monitor_seconds <= 0 or args.request_timeout_seconds <= 0:
        parser.error("invalid completion or monitoring limit")
    if production_runner_active():
        parser.error("another Luna runner is active")
    for name in os.environ:
        if name.startswith("JITENDEX_BENCHMARK_"):
            parser.error(f"benchmark override is forbidden for production: {name}")

    config = Config.load(args.config)
    if config.db_backend != "postgresql":
        parser.error("online tuning requires PostgreSQL")
    url = config.database_url()
    from psycopg.conninfo import conninfo_to_dict
    database_name = conninfo_to_dict(url).get("dbname", "")
    if not database_name or database_name.startswith("jitendex_lcp_"):
        parser.error("online tuning refuses a disposable PostgreSQL database")
    result_dir = ROOT / "reports/luna_performance/online"
    log_dir = ROOT / "work/luna_performance/online"
    result_path = result_dir / f"{args.window_id}.json"
    log_path = log_dir / f"{args.window_id}.jsonl"
    if result_path.exists() or log_path.exists():
        parser.error("window ID already has evidence; choose a new window ID")
    result_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    database = Database(config)
    ACTIVE_DATABASE = database
    connection = database.connect()
    try:
        run = connection.execute(
            """SELECT id,prompt_sha256,limits_json,pipeline_version,state
            FROM run WHERE id=?""", (args.run_id,),
        ).fetchone()
        if run is None:
            raise RuntimeError("production run does not exist")
        latest_run = int(connection.execute("SELECT MAX(id) FROM run").fetchone()[0])
        claimed = int(connection.execute("SELECT COUNT(*) FROM attempt WHERE outcome='claimed'").fetchone()[0])
        ready = int(connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=? AND state IN ('ready','retryable')", (args.run_id,),
        ).fetchone()[0])
        initial_workload = workload_progress(connection, args.run_id)
        schema_version = int(connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0])
    finally:
        connection.close()
    if claimed:
        raise RuntimeError("online tuning requires zero claimed attempts before the session")
    if not args.dry_run and (args.run_id != latest_run or not ready):
        raise RuntimeError("online tuning requires the latest production run with ready work")

    import psycopg
    with psycopg.connect(url) as identity_connection:
        server_version = str(identity_connection.info.server_version)
    configuration = {
        "window_id": args.window_id, "run_id": args.run_id,
        "database_name": database_name, "database_backend": "postgresql",
        "database_server_version": server_version,
        "database_driver_version": psycopg.__version__, "database_schema_version": schema_version,
        "pool_max": config.db_pool_max, "database_workers": 1,
        "model": config.model("translation")["id"],
        "reasoning_effort": config.model("translation")["reasoning_effort"],
        "prompt_sha256": run[1],
        "limits_sha256": hashlib.sha256(str(run[2]).encode()).hexdigest(),
        "pipeline_version": run[3], "concurrency": args.concurrency,
        "ramp_seconds": args.ramp_seconds, "steady_seconds": args.steady_seconds,
        "minimum_completed": args.minimum_completed, "dry_run": args.dry_run,
        "request_timeout_seconds": args.request_timeout_seconds,
    }
    started_at = utc_now()
    events: list[dict[str, Any]] = []
    monitor: list[dict[str, Any]] = []
    machines: list[dict[str, Any]] = []
    monitor_errors: list[str] = []
    monitor_stop = threading.Event()

    def monitor_window() -> None:
        while not monitor_stop.is_set():
            try:
                machines.append(machine_snapshot())
                monitor.append(postgres_snapshot(url, args.window_id))
            except Exception as error:
                monitor_errors.append(f"{type(error).__name__}: {error}")
                monitor_stop.set()
                return
            monitor_stop.wait(args.monitor_seconds)

    thread = threading.Thread(target=monitor_window, daemon=True)
    thread.start()
    runner_exit = 0
    if not args.dry_run:
        command = [
            str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/run_codex_batches.py"),
            "--config", str(args.config.resolve()), "--run-id", str(args.run_id),
            "--kind", "translation", "--concurrency", str(args.concurrency),
            "--worker-prefix", args.window_id, "--progress-interval", "60",
            "--startup-seconds", str(args.ramp_seconds),
            "--drain-after-seconds", str(args.steady_seconds),
            "--measure-from", "measurement",
            "--request-timeout-seconds", str(args.request_timeout_seconds),
        ]
        process = subprocess.Popen(
            command, cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True,
        )
        assert process.stdout is not None
        with log_path.open("w", encoding="utf-8") as log:
            def record_line(line: str) -> None:
                log.write(line)
                log.flush()
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            try:
                for line in process.stdout:
                    record_line(line)
            except KeyboardInterrupt:
                os.killpg(process.pid, signal.SIGINT)
                for line in process.stdout:
                    record_line(line)
                process.wait(timeout=120)
                raise
        runner_exit = process.wait()
        if runner_exit not in {0, 130}:
            raise RuntimeError(f"productive runner failed with exit code {runner_exit}")
    else:
        log_path.write_text("", encoding="utf-8")
    monitor_stop.set()
    thread.join(timeout=30)
    machines.append(machine_snapshot())
    monitor.append(postgres_snapshot(url, args.window_id))
    if monitor_errors:
        raise RuntimeError(f"online monitoring failed: {monitor_errors[0]}")

    checks = postflight(database, args.run_id, args.window_id)
    if any(checks[key] for key in (
        "window_claimed_attempts", "global_claimed_attempts", "leased_batches",
        "missing_units", "duplicate_translations", "source_hash_mismatches",
    )):
        raise RuntimeError(f"online window postflight failed: {checks}")
    if args.dry_run:
        counters = {
            "complete": False,
            "incomplete_reason": "dry_run",
            "measured_seconds": 0.0, "before": initial_workload, "after": initial_workload,
            "deltas": {key: 0 for key in initial_workload},
            "rates": {f"{key}_per_minute": 0.0 for key in initial_workload},
            "completed": 0, "postflight": checks,
        }
    elif runner_exit == 130:
        counters = incomplete_summary(events, initial_workload, checks)
    else:
        counters = summarize(events, args.steady_seconds, monitor, checks)
        if counters["completed"] < args.minimum_completed:
            counters["complete"] = False
            counters["incomplete_reason"] = "minimum_completed"
    counters["workload_profile"] = workload_profile(
        database, window_batch_ids(events, bool(counters["complete"])), args.window_id,
    )
    result = {
        "schema_version": 1, "configuration": configuration,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runner_revision_sha256": runner_revision_sha256(args.config, config),
        "started_at": started_at, "ended_at": utc_now(),
        "runner_exit": runner_exit, "phases": [
            event for event in events if event.get("event") == "phase"
        ],
        "counters": counters, "postgresql_monitoring": monitor,
        "monitoring_summary": monitoring_summary(monitor),
        "machine_monitoring": machines, "runner_log_sha256": file_sha256(log_path),
    }
    schema = json.loads((ROOT / "reports/luna_performance/online-result.schema.json").read_text())
    fastjsonschema.compile(schema)(result)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    database.close()
    ACTIVE_DATABASE = None
    print(json.dumps({
        "event": "online_window_complete", "result": str(result_path),
        "completed": counters["completed"], "dry_run": args.dry_run,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        if ACTIVE_DATABASE is not None:
            ACTIVE_DATABASE.close()
            ACTIVE_DATABASE = None
        if not isinstance(error, SystemExit):
            print(json.dumps({"event": "online_window_failed", "error": str(error)}), file=sys.stderr)
        raise
