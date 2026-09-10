#!/usr/bin/env python3
"""Dispatch isolated translation or review manifests through the bundled Codex CLI.

The model process runs in an ephemeral, read-only temporary workspace and receives
only the configured prompt plus one manifest on stdin. The coordinator owns all
database access, response ingestion, retry state, and usage auditing.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jitendex_ru.batch import claim, retry_or_split
from jitendex_ru.config import Config
from jitendex_ru.database import Database, ErrorCategory, classify_database_error
from jitendex_ru.db import audit, connect, record_attempt_usage, transaction
from jitendex_ru.review import ingest_review
from jitendex_ru.run_integrity import headword_progress, workload_progress
from jitendex_ru.validate_response import ValidationFailure, ingest_response


CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
STOP_REQUESTED = threading.Event()
CHILDREN_LOCK = threading.Lock()
CHILDREN: dict[str, subprocess.Popen[str]] = {}


def worker_peak_memory_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def measurement_starts_on_launch(measure_from: str, submission_number: int) -> bool:
    return measure_from == "first-launch" and submission_number == 1


def window_deadline_action(
    now: float, stop_at: float | None, drain_at: float | None,
    stop_requested: bool, productive_drain: bool,
) -> str | None:
    if stop_at is not None and now >= stop_at and not stop_requested:
        return "interrupt"
    if drain_at is not None and now >= drain_at and not productive_drain:
        return "drain"
    return None


def usage_boundary(result: "DispatchResult") -> bool:
    detail = (result.stdout + "\n" + result.stderr).lower()
    return "usage limit" in detail or "purchase more credits" in detail


def codex_executable(config: Config) -> Path:
    """Allow a fake executable only for isolated SQLite runner tests."""
    override = os.environ.get("JITENDEX_TEST_CODEX_EXECUTABLE")
    if not override:
        return CODEX
    if os.environ.get("JITENDEX_RUNNER_TEST_MODE") != "1" or config.db_backend != "sqlite":
        raise RuntimeError("test Codex override requires test mode and SQLite")
    return Path(override)


@dataclass(frozen=True)
class DispatchResult:
    claim: dict[str, str]
    returncode: int
    stdout: str
    stderr: str
    thread_id: str | None
    usage: dict[str, int] | None
    latency_ms: int
    interrupted: bool = False


def sqlite_retry(operation, *, attempts: int = 6, base_delay: float = 0.2, metrics=None):
    """Retry a complete idempotent operation only after a transient database error."""
    for number in range(attempts):
        try:
            return operation()
        except Exception as error:
            if classify_database_error(error) is not ErrorCategory.TRANSIENT or number + 1 == attempts:
                raise
            if metrics is not None:
                metrics.retries += 1
            delay = min(base_delay * (2 ** number), 2.0)
            time.sleep(delay + random.uniform(0, delay / 4))
    raise AssertionError("unreachable")


def request_stop(*_args: object) -> None:
    """Stop new claims and terminate every bundled Codex child process."""
    STOP_REQUESTED.set()
    with CHILDREN_LOCK:
        children = list(CHILDREN.values())
    for process in children:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def _connection(config: Config, database: Database | None = None):
    return database.connect() if database is not None else connect(config.db_path)


def interrupt_claim(config: Config, item: dict[str, str], database: Database | None = None) -> bool:
    """Requeue one exact claim if it is still owned by this interrupted worker."""
    connection = _connection(config, database)
    try:
        with transaction(connection, immediate=True):
            updated = connection.execute(
                """UPDATE attempt SET outcome='interrupted',
                error_json='{"reason":"runner interrupted"}',completed_at=CURRENT_TIMESTAMP
                WHERE id=? AND batch_id=? AND lease_token=? AND outcome='claimed'""",
                (item["attempt_id"], item["batch_id"], item["lease_token"]),
            ).rowcount
            if not updated:
                return False
            connection.execute(
                """UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL
                WHERE id=? AND state='leased' AND lease_token=?""",
                (item["batch_id"], item["lease_token"]),
            )
            audit(connection, "interrupt", "attempt", item["attempt_id"], {
                "batch_id": item["batch_id"], "reason": "runner interrupted",
            })
        return True
    finally:
        connection.close()


def mark_attempt_dispatched(
    config: Config, item: dict[str, str], database: Database | None = None,
) -> None:
    """Record the real process-launch boundary once before starting Codex."""
    connection = _connection(config, database)
    try:
        with transaction(connection, immediate=True):
            updated = connection.execute(
                """UPDATE attempt SET dispatched_at=COALESCE(dispatched_at,CURRENT_TIMESTAMP)
                WHERE id=? AND batch_id=? AND lease_token=? AND outcome='claimed'""",
                (item["attempt_id"], item["batch_id"], item["lease_token"]),
            ).rowcount
            if updated != 1:
                raise ValueError(f"attempt no longer owns launch lease: {item['attempt_id']}")
            audit(connection, "dispatch", "attempt", item["attempt_id"], {
                "batch_id": item["batch_id"], "transport": "codex-agent",
            })
    finally:
        connection.close()


def live_headword_progress(
    config: Config, run_id: int, database: Database | None = None,
) -> tuple[int, int]:
    """Return fully translated headwords and the complete source-headword total."""
    connection = _connection(config, database)
    try:
        return headword_progress(connection, run_id)
    finally:
        connection.close()


def live_workload_progress(
    config: Config, run_id: int, database: Database | None = None,
) -> dict[str, int]:
    connection = _connection(config, database)
    try:
        return workload_progress(connection, run_id)
    finally:
        connection.close()


@dataclass
class ProgressTracker:
    done: int
    sampled_at: float

    def sample(self, current_done: int, total: int, sampled_at: float) -> dict[str, Any]:
        elapsed = sampled_at - self.sampled_at
        speed = (current_done - self.done) * 60 / elapsed if elapsed > 0 else 0.0
        self.done = current_done
        self.sampled_at = sampled_at
        return {
            "event": "progress", "headwords_done": current_done,
            "headwords_remaining": total - current_done,
            "headwords_per_minute": round(speed, 1),
        }


def parse_events(stdout: str) -> tuple[str | None, dict[str, int] | None]:
    thread_id = None
    usage = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return thread_id, usage


def _target_schema(role: str) -> dict[str, Any]:
    if role == "glossary_set":
        return {
            "type": "array", "items": {"type": "string"},
        }
    return {"type": "string"}


def build_output_schema(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    units = [unit for article in manifest["articles"] for unit in article["units"]]
    items = []
    for unit in units:
        if kind == "translation":
            properties = {
                "unit_id": {"type": "string", "const": unit["unit_id"]},
                "source_sha256": {"type": "string", "const": unit["source_sha256"]},
                "target_text": _target_schema(unit["role"]),
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "review_reason": {"type": ["string", "null"]},
            }
        else:
            properties = {
                "unit_id": {"type": "string", "const": unit["unit_id"]},
                "source_sha256": {"type": "string", "const": unit["source_sha256"]},
                "decision": {"type": "string", "enum": ["accept", "replace", "needs_adjudication"]},
                "replacement_target": {"anyOf": [_target_schema(unit["role"]), {"type": "null"}]},
                "reason": {"type": ["string", "null"]},
            }
            if manifest.get('pipeline') == 'wadoku-xml-v3':
                properties['reason'] = {'type': 'string', 'minLength': 1}
        items.append({
            "type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties,
        })
    collection = "translations" if kind == "translation" else "reviews"
    properties = {
        "schema_version": {"type": "integer", "const": 2},
        "batch_id": {"type": "string", "const": manifest["batch_id"]},
        "manifest_sha256": {"type": "string", "const": manifest["manifest_sha256"]},
        collection: {
            "type": "array", "minItems": len(items), "maxItems": len(items),
            "items": {"anyOf": items},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": list(properties), "properties": properties,
    }


def dispatch_one(
    item: dict[str, str], prompt: str, kind: str,
    on_launch: Callable[[float], None] | None = None,
    executable: Path = CODEX,
    request_timeout_seconds: float | None = None,
    output_schema: dict[str, Any] | None = None,
) -> DispatchResult:
    manifest_text = Path(item["request_path"]).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    model_input = f"{prompt.rstrip()}\n\nSUPPLIED BATCH\n{manifest_text}"
    schema_file = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", prefix="jitendex-schema-",
        dir="/private/tmp", delete=False,
    )
    try:
        json.dump(output_schema if output_schema is not None else build_output_schema(manifest, kind), schema_file, ensure_ascii=False)
        schema_file.close()
        schema_path = Path(schema_file.name)
    except Exception:
        schema_file.close()
        Path(schema_file.name).unlink(missing_ok=True)
        raise
    command = [
        str(executable), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "-s", "read-only", "-C", "/private/tmp",
        "-m", item["model_id"], "-c", f'model_reasoning_effort="{item["reasoning_effort"]}"',
        "--output-schema", str(schema_path), "--json", "-o", item["response_path"], "-",
    ]
    started = time.monotonic()
    if STOP_REQUESTED.is_set():
        schema_path.unlink(missing_ok=True)
        return DispatchResult(
            claim=item, returncode=130, stdout="", stderr="runner interrupted",
            thread_id=None, usage=None, latency_ms=0, interrupted=True,
        )
    if on_launch is not None:
        on_launch(started)
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        with CHILDREN_LOCK:
            CHILDREN[item["attempt_id"]] = process
        if STOP_REQUESTED.is_set() and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            if request_timeout_seconds is None:
                stdout, stderr = process.communicate(model_input)
            else:
                stdout, stderr = process.communicate(
                    model_input, timeout=request_timeout_seconds,
                )
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                remaining_stdout, remaining_stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining_stdout, remaining_stderr = process.communicate()
            as_text = lambda value: (
                value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            )
            stdout = as_text(error.stdout) + as_text(remaining_stdout)
            stderr = as_text(error.stderr) + as_text(remaining_stderr)
            stderr += f"\nrequest timed out after {request_timeout_seconds:g} seconds"
    finally:
        with CHILDREN_LOCK:
            CHILDREN.pop(item["attempt_id"], None)
        schema_path.unlink(missing_ok=True)
    latency_ms = round((time.monotonic() - started) * 1000)
    thread_id, usage = parse_events(stdout)
    return DispatchResult(
        claim=item, returncode=process.returncode, stdout=stdout,
        stderr=stderr, thread_id=thread_id, usage=usage, latency_ms=latency_ms,
        interrupted=STOP_REQUESTED.is_set() and process.returncode != 0,
    )


def reject_transport(
    config: Config, kind: str, result: DispatchResult, reason: str,
    database: Database | None = None,
) -> dict[str, Any]:
    connection = _connection(config, database)
    try:
        with transaction(connection, immediate=True):
            lock = " FOR UPDATE" if getattr(connection, "backend", "sqlite") == "postgresql" else ""
            owned = connection.execute(
                "SELECT state,lease_token FROM batch WHERE id=?" + lock,
                (result.claim["batch_id"],),
            ).fetchone()
            if (
                owned is None or owned["state"] != "leased"
                or owned["lease_token"] != result.claim["lease_token"]
            ):
                return {"requeued": False, "split": False, "stale_lease": True}
            rejected = connection.execute(
                """UPDATE attempt SET outcome='rejected',error_json=?,completed_at=CURRENT_TIMESTAMP
                WHERE id=? AND batch_id=? AND lease_token=? AND outcome='claimed'""",
                (
                    json.dumps({"transport_error": reason}), result.claim["attempt_id"],
                    result.claim["batch_id"], result.claim["lease_token"],
                ),
            ).rowcount
            released = connection.execute(
                """UPDATE batch SET state='retryable' WHERE id=? AND state='leased'
                AND lease_token=?""",
                (result.claim["batch_id"], result.claim["lease_token"]),
            ).rowcount
            if rejected != 1 or released != 1:
                raise RuntimeError("lease ownership changed while rejecting transport failure")
            audit(connection, "reject", "attempt", result.claim["attempt_id"], {"transport_error": reason})
        if kind == "translation":
            recovery = retry_or_split(connection, result.claim["batch_id"])
        else:
            batch = connection.execute(
                "SELECT attempt_count FROM batch WHERE id=?", (result.claim["batch_id"],),
            ).fetchone()
            state = "ready" if batch["attempt_count"] < 3 else "blocked"
            connection.execute(
                "UPDATE batch SET state=?,lease_token=NULL,lease_expires_at=NULL WHERE id=?",
                (state, result.claim["batch_id"]),
            )
            audit(connection, "retry" if state == "ready" else "block", "review_batch", result.claim["batch_id"], {
                "attempt_count": batch["attempt_count"], "reason": reason,
            })
            connection.commit()
            recovery = {"requeued": state == "ready", "split": False, "blocked": state == "blocked"}
        return recovery
    finally:
        connection.close()


def ingest(
    config: Config, kind: str, result: DispatchResult, database: Database | None = None,
) -> tuple[bool, str]:
    response_path = Path(result.claim["response_path"])
    if result.returncode or result.usage is None or not response_path.is_file():
        reason = (result.stdout + "\n" + result.stderr).strip()[-5000:] or f"Codex exited {result.returncode} without usage/response"
        recovery = reject_transport(config, kind, result, reason, database)
        return False, json.dumps({"transport_error": reason, "recovery": recovery}, ensure_ascii=False)

    input_tokens = int(result.usage.get("input_tokens", 0))
    cached_tokens = int(result.usage.get("cached_input_tokens", 0))
    output_tokens = int(result.usage.get("output_tokens", 0))
    connection = _connection(config, database)
    try:
        usage_transaction_started = time.monotonic()
        record_attempt_usage(
            connection, result.claim["attempt_id"],
            effective_model_id=result.claim["model_id"],
            reasoning_effort=result.claim["reasoning_effort"], transport="codex-agent",
            input_tokens=input_tokens, cached_input_tokens=cached_tokens,
            output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
            api_request_id=result.thread_id, finish_reason="turn.completed",
            status_reason="isolated bundled Codex route", latency_ms=result.latency_ms,
        )
        # Usage belongs to the completed model request even if deterministic
        # ingestion rejects its payload, so persist it independently first.
        connection.commit()
        database_metrics = getattr(connection, "metrics", None)
        if database_metrics is not None:
            database_metrics.transaction((time.monotonic() - usage_transaction_started) * 1000)
        ingestion_transaction_started = time.monotonic()
        if kind == "translation":
            outcome = ingest_response(connection, response_path)
        else:
            outcome = ingest_review(connection, response_path)
        connection.commit()
        if database_metrics is not None:
            database_metrics.transaction((time.monotonic() - ingestion_transaction_started) * 1000)
        return True, json.dumps(outcome, ensure_ascii=False, sort_keys=True)
    except ValidationFailure as error:
        connection.commit()
        if database_metrics is not None:
            database_metrics.transaction((time.monotonic() - ingestion_transaction_started) * 1000)
        recovery = retry_or_split(connection, result.claim["batch_id"])
        connection.commit()
        return False, json.dumps(
            {"validation_issues": error.issues, "recovery": recovery}, ensure_ascii=False,
        )
    except Exception as error:
        connection.rollback()
        if "ingestion_transaction_started" in locals() and database_metrics is not None:
            database_metrics.transaction((time.monotonic() - ingestion_transaction_started) * 1000)
        if classify_database_error(error) is ErrorCategory.TRANSIENT:
            raise
        recovery = reject_transport(config, kind, result, str(error), database)
        return False, json.dumps({"transport_error": str(error), "recovery": recovery}, ensure_ascii=False)
    finally:
        connection.close()


def next_claim(
    config: Config, run_id: int, kind: str, worker_id: str,
    database: Database | None = None,
) -> dict[str, str] | None:
    connection = _connection(config, database)
    try:
        model = config.model(kind)
        return claim(
            connection, worker_id, config.work_dir / "outbox", run_id=run_id, kind=kind,
            model_id=model["id"], reasoning_effort=model["reasoning_effort"],
            transport="codex-agent",
        )
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--kind", choices=("translation", "review"), required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--worker-prefix", required=True)
    parser.add_argument("--max-submissions", type=int)
    parser.add_argument("--progress-interval", type=float, default=60.0)
    parser.add_argument("--startup-seconds", type=float, default=0.0)
    parser.add_argument("--warmup-seconds", type=float, default=0.0)
    parser.add_argument("--stop-after-seconds", type=float)
    parser.add_argument("--drain-after-seconds", type=float)
    parser.add_argument("--measure-from", choices=("measurement", "first-launch"), default="measurement")
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be positive")
    if args.startup_seconds < 0:
        parser.error("--startup-seconds must not be negative")
    if args.warmup_seconds < 0:
        parser.error("--warmup-seconds must not be negative")
    if args.stop_after_seconds is not None and args.stop_after_seconds <= 0:
        parser.error("--stop-after-seconds must be positive")
    if args.drain_after_seconds is not None and args.drain_after_seconds <= 0:
        parser.error("--drain-after-seconds must be positive")
    if args.stop_after_seconds is not None and args.drain_after_seconds is not None:
        parser.error("--stop-after-seconds and --drain-after-seconds are mutually exclusive")
    if args.measure_from == "first-launch" and args.warmup_seconds:
        parser.error("--warmup-seconds requires --measure-from measurement")
    if args.request_timeout_seconds <= 0:
        parser.error("--request-timeout-seconds must be positive")
    config = Config.load(args.config)
    try:
        executable = codex_executable(config)
    except RuntimeError as error:
        parser.error(str(error))
    if not executable.is_file():
        parser.error(f"Codex CLI not found: {executable}")
    database = Database(config)
    STOP_REQUESTED.clear()
    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    prompt_key = "translation_prompt" if args.kind == "translation" else "review_prompt"
    prompt_name = config.raw["versions"][prompt_key].replace("-", "_")
    prompt = (config.root / "prompts" / f"{prompt_name}.txt").read_text(encoding="utf-8")
    submitted = completed = failed = 0
    input_tokens = output_tokens = cached_input_tokens = 0
    request_latencies: list[int] = []
    rate_limits = timeouts = transport_failures = validation_rejections = 0
    retries = splits = 0
    active: dict[Future[DispatchResult], dict[str, Any]] = {}
    launch_events: queue.SimpleQueue[tuple[dict[str, Any], int, float, bool, float]] = queue.SimpleQueue()
    launch_signal = threading.Event()
    progress_query_started = time.monotonic()
    initial_done, total_headwords = sqlite_retry(
        lambda: live_headword_progress(config, args.run_id, database), metrics=database.metrics,
    )
    progress_query_seconds = time.monotonic() - progress_query_started
    progress_query_seconds_total = progress_query_seconds
    progress = ProgressTracker(initial_done, time.monotonic())
    stage_started = time.monotonic()
    next_progress = stage_started + args.progress_interval
    stop_at = None
    drain_at = None
    measurement_at = None
    bounded_stop = False
    productive_drain = False
    launch_interval = args.startup_seconds / max(args.concurrency - 1, 1)
    next_initial_launch = stage_started
    slots_started = 0
    initial_slots_launched = 0
    latest_initial_launch = stage_started
    claim_milliseconds = 0.0
    ingestion_milliseconds = 0.0
    print(json.dumps({
        "event": "progress", "headwords_done": initial_done,
        "headwords_remaining": total_headwords - initial_done,
        "headwords_per_minute": 0.0, "database_backend": database.backend,
        "progress_query_seconds": round(progress_query_seconds, 6),
        "claim_milliseconds": 0.0, "ingestion_milliseconds": 0.0,
        **database.metrics.snapshot(), "workers_active": 0, "elapsed_seconds": 0.0,
    }), flush=True)
    print(json.dumps({"event": "phase", "phase": "startup", "elapsed_seconds": 0.0}), flush=True)

    executor = ThreadPoolExecutor(max_workers=args.concurrency)

    def metric_snapshot() -> dict[str, Any]:
        return {
            "submitted": submitted, "completed": completed, "failed_attempts": failed,
            "input_tokens": input_tokens, "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens, "rate_limits": rate_limits,
            "timeouts": timeouts, "transport_failures": transport_failures,
            "validation_rejections": validation_rejections, "retries": retries,
            "splits": splits, "progress_query_seconds": round(progress_query_seconds_total, 6),
            "claim_milliseconds": round(claim_milliseconds, 3),
            "ingestion_milliseconds": round(ingestion_milliseconds, 3),
            **database.metrics.snapshot(),
        }

    def emit_phase(phase: str, at: float, reason: str | None = None) -> None:
        workload = sqlite_retry(
            lambda: live_workload_progress(config, args.run_id, database),
            metrics=database.metrics,
        )
        payload: dict[str, Any] = {
            "event": "phase", "phase": phase,
            "elapsed_seconds": round(at - stage_started, 6),
            "metrics": metric_snapshot(),
            "workload": workload,
        }
        if reason is not None:
            payload["reason"] = reason
        print(json.dumps(payload), flush=True)

    def record_launch(
        item: dict[str, Any], number: int, requested: float, opening_slot: bool,
        launched: float,
    ) -> None:
        launch_events.put((item, number, requested, opening_slot, launched))
        launch_signal.set()

    def drain_launches() -> None:
        nonlocal initial_slots_launched, latest_initial_launch, measurement_at, stop_at, drain_at
        launch_signal.clear()
        while True:
            try:
                item, number, requested, opening_slot, launched = launch_events.get_nowait()
            except queue.Empty:
                break
            if opening_slot:
                initial_slots_launched += 1
                latest_initial_launch = max(latest_initial_launch, launched)
            print(json.dumps({
                "event": "launched", "number": number, "batch_id": item["batch_id"],
                "requested_launch_seconds": round(requested - stage_started, 6),
                "actual_launch_seconds": round(launched - stage_started, 6),
            }), flush=True)
            if measurement_starts_on_launch(args.measure_from, number):
                emit_phase("measurement", launched)
                if args.stop_after_seconds:
                    stop_at = launched + args.stop_after_seconds
                elif args.drain_after_seconds:
                    drain_at = launched + args.drain_after_seconds
            if opening_slot and initial_slots_launched == args.concurrency:
                if args.warmup_seconds:
                    measurement_at = latest_initial_launch + args.warmup_seconds
                    emit_phase("warmup", latest_initial_launch)
                elif args.measure_from == "measurement":
                    emit_phase("measurement", latest_initial_launch)
                    if args.stop_after_seconds:
                        stop_at = latest_initial_launch + args.stop_after_seconds
                    elif args.drain_after_seconds:
                        drain_at = latest_initial_launch + args.drain_after_seconds

    try:
        while True:
            drain_launches()
            claim_unavailable = False
            if measurement_at is not None and time.monotonic() >= measurement_at:
                measurement_at = None
                emit_phase("measurement", time.monotonic())
                if args.stop_after_seconds and args.measure_from == "measurement":
                    stop_at = time.monotonic() + args.stop_after_seconds
                elif args.drain_after_seconds and args.measure_from == "measurement":
                    drain_at = time.monotonic() + args.drain_after_seconds
            deadline_action = window_deadline_action(
                time.monotonic(), stop_at, drain_at,
                STOP_REQUESTED.is_set(), productive_drain,
            )
            if deadline_action == "interrupt":
                bounded_stop = True
                request_stop()
            elif deadline_action == "drain":
                productive_drain = True
                emit_phase("drain", time.monotonic(), "productive_window_complete")
            while not STOP_REQUESTED.is_set() and not productive_drain and len(active) < args.concurrency:
                if args.max_submissions is not None and submitted >= args.max_submissions:
                    break
                now = time.monotonic()
                opening_slot = slots_started < args.concurrency and now >= next_initial_launch
                if opening_slot:
                    requested_launch = next_initial_launch
                    slots_started += 1
                    next_initial_launch = stage_started + slots_started * launch_interval
                elif len(active) < slots_started:
                    requested_launch = now
                else:
                    break
                submitted += 1
                claim_started = time.monotonic()
                item = sqlite_retry(lambda: next_claim(
                    config, args.run_id, args.kind, f"{args.worker_prefix}-{submitted:04d}", database,
                ), metrics=database.metrics)
                claim_milliseconds += (time.monotonic() - claim_started) * 1000
                if item is None:
                    submitted -= 1
                    if opening_slot:
                        slots_started -= 1
                    claim_unavailable = True
                    break
                number = submitted
                def callback(
                    launched, item=item, number=number,
                    requested_launch=requested_launch, opening_slot=opening_slot,
                ):
                    mark_attempt_dispatched(config, item, database)
                    record_launch(item, number, requested_launch, opening_slot, launched)
                active[executor.submit(
                    dispatch_one, item, prompt, args.kind, callback, executable,
                    args.request_timeout_seconds,
                )] = item
                print(json.dumps({
                    "event": "submitted", "number": submitted, "batch_id": item["batch_id"],
                    "requested_launch_seconds": round(requested_launch - stage_started, 6),
                    "elapsed_seconds": round(time.monotonic() - stage_started, 6),
                }), flush=True)
            if initial_slots_launched < slots_started and active and not STOP_REQUESTED.is_set():
                launch_signal.wait(1.0)
            drain_launches()
            if not active and claim_unavailable:
                break
            if not active and slots_started < args.concurrency and not STOP_REQUESTED.is_set():
                STOP_REQUESTED.wait(max(0.0, min(next_initial_launch - time.monotonic(), 1.0)))
                continue
            if not active:
                break
            deadlines = [next_progress]
            if slots_started < args.concurrency:
                deadlines.append(next_initial_launch)
            if stop_at is not None:
                deadlines.append(stop_at)
            if drain_at is not None and not productive_drain:
                deadlines.append(drain_at)
            if measurement_at is not None:
                deadlines.append(measurement_at)
            if launch_signal.is_set():
                deadlines.append(time.monotonic())
            elif initial_slots_launched < slots_started:
                deadlines.append(time.monotonic() + 0.1)
            timeout = max(0.0, min(deadlines) - time.monotonic())
            done, _ = wait(active, timeout=timeout, return_when=FIRST_COMPLETED)
            drain_launches()
            if time.monotonic() >= next_progress:
                progress_query_started = time.monotonic()
                current_done, total_headwords = sqlite_retry(
                    lambda: live_headword_progress(config, args.run_id, database), metrics=database.metrics,
                )
                progress_query_seconds = time.monotonic() - progress_query_started
                progress_query_seconds_total += progress_query_seconds
                now = time.monotonic()
                report = progress.sample(current_done, total_headwords, now)
                report["elapsed_seconds"] = round(now - stage_started, 6)
                report["workers_active"] = len(active)
                report.update({
                    "database_backend": database.backend,
                    "progress_query_seconds": round(progress_query_seconds_total, 6),
                    "claim_milliseconds": round(claim_milliseconds, 3),
                    "ingestion_milliseconds": round(ingestion_milliseconds, 3),
                    **database.metrics.snapshot(),
                })
                print(json.dumps(report), flush=True)
                next_progress = now + args.progress_interval
            for future in done:
                item = active[future]
                batch_id = item["batch_id"]
                result = None
                try:
                    result = future.result()
                    if result.interrupted:
                        requeued = sqlite_retry(
                            lambda: interrupt_claim(config, result.claim, database), metrics=database.metrics,
                        )
                        ok, detail = False, "interrupted and requeued" if requeued else "interrupted after completion"
                    elif usage_boundary(result):
                        requeued = sqlite_retry(
                            lambda: interrupt_claim(config, result.claim, database),
                            metrics=database.metrics,
                        )
                        ok = False
                        detail = json.dumps({
                            "quota_boundary": True, "requeued": requeued,
                            "message": "Codex usage boundary",
                        })
                    else:
                        ingestion_started = time.monotonic()
                        ok, detail = sqlite_retry(
                            lambda: ingest(config, args.kind, result, database), metrics=database.metrics,
                        )
                        ingestion_milliseconds += (time.monotonic() - ingestion_started) * 1000
                except Exception as error:
                    if STOP_REQUESTED.is_set():
                        requeued = sqlite_retry(lambda: interrupt_claim(config, item, database), metrics=database.metrics)
                        detail = "interrupted and requeued" if requeued else "interrupted after completion"
                    else:
                        requeued = sqlite_retry(lambda: interrupt_claim(config, item, database), metrics=database.metrics)
                        detail = f"dispatch failed and claim requeued: {error}" if requeued else str(error)
                    ok = False
                active.pop(future)
                completed += 1
                failed += int(not ok)
                if result is not None:
                    request_latencies.append(result.latency_ms)
                    usage = result.usage or {}
                    input_tokens += int(usage.get("input_tokens", 0))
                    cached_input_tokens += int(usage.get("cached_input_tokens", 0))
                    output_tokens += int(usage.get("output_tokens", 0))
                lowered_detail = detail.lower()
                was_quota_boundary = "quota_boundary" in lowered_detail
                was_rate_limit = (
                    "429" in lowered_detail or "rate limit" in lowered_detail
                    or was_quota_boundary
                )
                was_timeout = "timeout" in lowered_detail or "timed out" in lowered_detail
                was_validation = not ok and ("validator" in lowered_detail or "issue" in lowered_detail)
                rate_limits += int(was_rate_limit)
                timeouts += int(was_timeout)
                validation_rejections += int(was_validation)
                transport_failures += int(not ok and not was_validation and not was_rate_limit and not was_timeout)
                try:
                    detail_payload = json.loads(detail)
                except (json.JSONDecodeError, TypeError):
                    detail_payload = {}
                recovery = detail_payload.get("recovery", {}) if isinstance(detail_payload, dict) else {}
                retries += int(bool(recovery.get("requeued")))
                splits += int(bool(recovery.get("split")))
                print(json.dumps({
                    "event": "completed", "batch_id": batch_id, "ok": ok,
                    "detail": detail, "completed": completed, "failed": failed,
                    "elapsed_seconds": round(time.monotonic() - stage_started, 6),
                    "latency_ms": result.latency_ms if result is not None else None,
                    "input_tokens": int((result.usage or {}).get("input_tokens", 0)) if result else 0,
                    "cached_input_tokens": int((result.usage or {}).get("cached_input_tokens", 0)) if result else 0,
                    "output_tokens": int((result.usage or {}).get("output_tokens", 0)) if result else 0,
                    "rate_limit": was_rate_limit, "timeout": was_timeout,
                    "quota_boundary": was_quota_boundary,
                    "transport_failure": not ok and not was_validation and not was_rate_limit and not was_timeout,
                    "validation_rejection": was_validation,
                    "retry": bool(recovery.get("requeued")),
                    "split": bool(recovery.get("split")),
                    "worker_peak_memory_bytes": worker_peak_memory_bytes(),
                }, ensure_ascii=False), flush=True)
                if was_quota_boundary and not STOP_REQUESTED.is_set():
                    request_stop()
    finally:
        emit_phase(
            "shutdown", time.monotonic(),
            "productive_drain" if productive_drain else (
                "bounded_stage_stop" if bounded_stop else (
                    "external_interrupt" if STOP_REQUESTED.is_set() else "work_exhausted"
                )
            ),
        )
        if STOP_REQUESTED.is_set() or active:
            request_stop()
        executor.shutdown(wait=True, cancel_futures=True)
        for item in active.values():
            sqlite_retry(lambda item=item: interrupt_claim(config, item, database), metrics=database.metrics)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    progress_query_started = time.monotonic()
    final_done, total_headwords = sqlite_retry(
        lambda: live_headword_progress(config, args.run_id, database), metrics=database.metrics,
    )
    progress_query_seconds = time.monotonic() - progress_query_started
    progress_query_seconds_total += progress_query_seconds
    report = progress.sample(final_done, total_headwords, time.monotonic())
    report["elapsed_seconds"] = round(time.monotonic() - stage_started, 6)
    report["workers_active"] = 0
    report.update({
        "database_backend": database.backend,
        "progress_query_seconds": round(progress_query_seconds_total, 6),
        "claim_milliseconds": round(claim_milliseconds, 3),
        "ingestion_milliseconds": round(ingestion_milliseconds, 3),
        **database.metrics.snapshot(),
    })
    print(json.dumps(report), flush=True)
    ordered_latencies = sorted(request_latencies)
    percentile = lambda value: ordered_latencies[
        min(len(ordered_latencies) - 1, round((len(ordered_latencies) - 1) * value))
    ] if ordered_latencies else None
    print(json.dumps({
        "event": "summary", "submitted": submitted, "completed": completed,
        "failed_attempts": failed, "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "rate_limits": rate_limits, "timeouts": timeouts,
        "transport_failures": transport_failures,
        "validation_rejections": validation_rejections, "retries": retries, "splits": splits,
        "latency_p50_ms": percentile(0.50), "latency_p95_ms": percentile(0.95),
        "latency_p99_ms": percentile(0.99),
        "worker_peak_memory_bytes": worker_peak_memory_bytes(),
        "database_backend": database.backend,
        "progress_query_seconds": round(progress_query_seconds_total, 6),
        "claim_milliseconds": round(claim_milliseconds, 3),
        "ingestion_milliseconds": round(ingestion_milliseconds, 3),
        "workers_active": 0,
        "headwords_done": report["headwords_done"],
        "headwords_remaining": report["headwords_remaining"],
        "headwords_per_minute": report["headwords_per_minute"],
        **database.metrics.snapshot(),
    }), flush=True)
    database.close()
    return 130 if STOP_REQUESTED.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
