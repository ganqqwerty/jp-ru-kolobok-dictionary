import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import fastjsonschema
import pytest

from jitendex_ru.db import connect, initialize


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_luna_benchmark", ROOT / "scripts/run_luna_benchmark.py",
)
assert SPEC and SPEC.loader
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)
ONLINE_SPEC = importlib.util.spec_from_file_location(
    "run_luna_online_window", ROOT / "scripts/run_luna_online_window.py",
)
assert ONLINE_SPEC and ONLINE_SPEC.loader
ONLINE = importlib.util.module_from_spec(ONLINE_SPEC)
ONLINE_SPEC.loader.exec_module(ONLINE)


def benchmark_database(tmp_path):
    path = tmp_path / "stage.sqlite3"
    initialize(path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "articles": [{"units": [{"unit_id": "u", "source_sha256": "source"}]}],
    }), encoding="utf-8")
    connection = connect(path)
    connection.execute(
        """INSERT INTO source_snapshot(id,kind,version,url,sha256,local_path,extractor_version)
        VALUES (1,'jitendex','v','u','j','p','e'),(2,'kaishi','v','u','k','p','e')"""
    )
    connection.execute(
        """INSERT INTO run(id,jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,
        extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json)
        VALUES (1,1,2,'source','e','p','r','t','{}'),(2,1,2,'benchmark','e','p','r','t','{}')"""
    )
    raw = json.dumps(["語", "ご", "", "", 0, [], 1, ""])
    connection.execute(
        """INSERT INTO article(id,snapshot_id,bank_number,entry_ordinal,expression,reading,
        sequence,raw_json,source_sha256,selected) VALUES (1,1,1,1,'語','ご',1,?,'a',1)""",
        (raw,),
    )
    connection.execute(
        "INSERT INTO run_article(run_id,article_id,structural_fingerprint) VALUES (2,1,'fingerprint')"
    )
    connection.execute(
        """INSERT INTO translation_unit(id,run_id,article_id,json_pointer,role,source_text,
        source_sha256,protected_tokens_json,byte_count) VALUES
        ('u',2,1,'/5/0','glossary','word','source','[]',4)"""
    )
    connection.execute(
        """INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,
        unit_count,manifest_path) VALUES ('b',2,'manifest',1,1,1,?)""",
        (str(manifest),),
    )
    connection.execute("INSERT INTO batch_item VALUES ('b','u',0)")
    connection.execute(
        "INSERT INTO benchmark_marker(stage_id,corpus_sha256,run_id) VALUES ('template',?,2)",
        ("f" * 64,),
    )
    connection.commit()
    return connection


def test_workload_metrics_and_postflight_cover_stage_invariants(tmp_path):
    connection = benchmark_database(tmp_path)
    assert BENCHMARK.workload_counts(connection, 2) == {
        "headwords": 0, "articles": 0, "units": 0, "source_characters": 0,
    }
    assert not any(BENCHMARK.postflight(connection, 2).values())
    connection.execute(
        """INSERT INTO attempt(id,batch_id,worker_id,model,prompt_sha256,request_path,
        outcome) VALUES ('a','b','w','m','p','r','accepted')"""
    )
    connection.execute(
        """INSERT INTO translation(run_id,unit_id,attempt_id,target_text,confidence,
        target_sha256,accepted) VALUES (2,'u','a','слово','high','target',0)"""
    )
    connection.commit()
    assert BENCHMARK.workload_counts(connection, 2) == {
        "headwords": 0, "articles": 0, "units": 0, "source_characters": 0,
    }
    connection.execute("UPDATE translation SET accepted=1 WHERE attempt_id='a'")
    connection.commit()
    assert BENCHMARK.workload_counts(connection, 2) == {
        "headwords": 1, "articles": 1, "units": 1, "source_characters": 4,
    }
    connection.execute("DELETE FROM translation WHERE attempt_id='a'")
    connection.execute("UPDATE batch SET state='deterministic_validated' WHERE id='b'")
    connection.commit()
    assert BENCHMARK.workload_counts(connection, 2) == {
        "headwords": 1, "articles": 1, "units": 1, "source_characters": 4,
    }
    connection.close()


def test_sqlite_missing_unit_postflight_uses_unit_index(tmp_path):
    connection = benchmark_database(tmp_path)
    plan = connection.execute(
        """EXPLAIN QUERY PLAN SELECT COUNT(*) FROM translation_unit tu
        WHERE tu.run_id=2 AND NOT EXISTS (
        SELECT 1 FROM batch_item bi WHERE bi.unit_id=tu.id AND EXISTS (
        SELECT 1 FROM batch b WHERE b.id=bi.batch_id AND b.run_id=tu.run_id))"""
    ).fetchall()
    assert any("batch_item_unit" in row[3] for row in plan)
    connection.close()


def test_online_postflight_finds_only_ready_units_without_a_batch(tmp_path):
    connection = benchmark_database(tmp_path)
    path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    connection.execute("DELETE FROM batch_item")
    connection.commit()
    connection.close()
    database = SimpleNamespace(connect=lambda: connect(path))

    assert ONLINE.postflight(database, 2, "window")["missing_units"] == 1
    connection = connect(path)
    connection.execute("UPDATE translation_unit SET status='translated' WHERE id='u'")
    connection.commit()
    connection.close()
    assert ONLINE.postflight(database, 2, "window")["missing_units"] == 0


def test_benchmark_json_contracts_compile():
    for name in ("corpus.schema.json", "stage.schema.json", "result.schema.json"):
        schema = json.loads((ROOT / "reports/luna_performance" / name).read_text())
        fastjsonschema.compile(schema)


def test_checked_in_online_dry_results_match_the_current_contract():
    schema = json.loads((ROOT / "reports/luna_performance/online-result.schema.json").read_text())
    validate = fastjsonschema.compile(schema)
    paths = sorted((ROOT / "reports/luna_performance/online").glob("*dry*.json"))
    assert paths
    for path in paths:
        validate(json.loads(path.read_text()))


def test_stage_schema_accepts_corpus_builder_contract():
    schema = json.loads((ROOT / "reports/luna_performance/stage.schema.json").read_text())
    validate = fastjsonschema.compile(schema)
    validate({
        "schema_version": 1,
        "stage_id": "template",
        "corpus_sha256": "f" * 64,
        "database_backend": "sqlite",
        "database_path": "/disposable/template.sqlite3",
        "run_id": 1,
        "source_run_id": 44,
        "headword_count": 60_000,
        "article_count": 60_695,
        "unit_count": 456_651,
        "source_characters": 14_770_918,
        "batch_count": 9_994,
        "extracted_units": 456_651,
        "method": "difficulty-fifth-plus-hash-sample-v1",
        "template": True,
    })


def test_harness_validates_stage_schema_and_postgresql_name_boundary(tmp_path):
    stage = {
        "schema_version": 1, "stage_id": "template", "corpus_sha256": "f" * 64,
        "database_backend": "sqlite", "database_path": "/disposable/template.sqlite3",
        "run_id": 1, "source_run_id": 44, "headword_count": 1, "article_count": 1,
        "unit_count": 1, "source_characters": 1, "batch_count": 1,
        "extracted_units": 1, "method": "fixed-v1", "template": True,
        "unexpected": "rejected",
    }
    path = tmp_path / "stage.json"
    path.write_text(json.dumps(stage), encoding="utf-8")
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        BENCHMARK.validated_stage(path)
    with pytest.raises(RuntimeError, match="jitendex_lcp_ prefix"):
        BENCHMARK.clone_postgresql("unused", "jitendex", "jitendex_lcp_trial")


def test_measured_window_requires_one_complete_phase_pair():
    phases = [
        {"event": "phase", "phase": "startup", "elapsed_seconds": 0.0},
        {"event": "phase", "phase": "measurement", "elapsed_seconds": 60.0},
        {"event": "phase", "phase": "shutdown", "elapsed_seconds": 1260.0},
    ]
    assert BENCHMARK.measured_window_seconds(phases) == 1200.0
    with pytest.raises(RuntimeError, match="exactly one"):
        BENCHMARK.measured_window_seconds(phases[:2])


def test_measurement_summary_excludes_startup_metrics():
    baseline = {
        "submitted": 2, "completed": 1, "failed_attempts": 0,
        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3,
        "rate_limits": 0, "timeouts": 0, "transport_failures": 0,
        "validation_rejections": 0, "retries": 0, "splits": 0,
        "progress_query_seconds": 0.5, "claim_milliseconds": 20.0,
        "ingestion_milliseconds": 5.0, "pool_wait_milliseconds": 1.0,
        "database_checkouts": 2, "database_retries": 0,
        "transaction_milliseconds": 8.0,
    }
    final_metrics = {
        **baseline, "submitted": 4, "completed": 3, "failed_attempts": 1,
        "input_tokens": 50, "output_tokens": 10, "progress_query_seconds": 0.7,
        "claim_milliseconds": 30.0, "ingestion_milliseconds": 15.0,
        "pool_wait_milliseconds": 2.5, "database_checkouts": 5,
        "database_retries": 1, "transaction_milliseconds": 18.0,
    }
    phases = [
        {"event": "phase", "phase": "measurement", "elapsed_seconds": 60.0,
         "metrics": baseline},
        {"event": "phase", "phase": "shutdown", "elapsed_seconds": 1260.0,
         "metrics": final_metrics},
    ]
    events = [
        {"event": "completed", "elapsed_seconds": 59.0, "ok": True,
         "latency_ms": 999, "input_tokens": 999, "output_tokens": 999},
        {"event": "submitted", "elapsed_seconds": 61.0},
        {"event": "completed", "elapsed_seconds": 100.0, "ok": True,
         "latency_ms": 100, "input_tokens": 20, "cached_input_tokens": 5,
         "output_tokens": 4, "worker_peak_memory_bytes": 1000},
        {"event": "completed", "elapsed_seconds": 200.0, "ok": False,
         "latency_ms": 300, "input_tokens": 20, "cached_input_tokens": 0,
         "output_tokens": 3, "rate_limit": True, "retry": True,
         "worker_peak_memory_bytes": 2000},
        {"event": "progress", "elapsed_seconds": 120.0,
         "connections_in_use": 2, "workers_active": 80},
    ]
    summary = BENCHMARK.measurement_summary(events, phases, {"database_backend": "postgresql"})
    assert summary["submitted"] == 1
    assert summary["completed"] == 2
    assert summary["failed_attempts"] == 1
    assert summary["input_tokens"] == 40
    assert summary["output_tokens"] == 7
    assert summary["rate_limits"] == 1
    assert summary["retries"] == 1
    assert summary["latency_p50_ms"] == 100
    assert summary["latency_p95_ms"] == 300
    assert summary["transaction_milliseconds"] == 10.0
    assert summary["database_checkouts"] == 3
    assert summary["connections_in_use"] == 2
    assert summary["workers_active"] == 80


def test_monitoring_summary_reports_postgresql_lock_waits_and_deltas():
    samples = [
        {"locks": [["RowExclusiveLock", True, 2]],
         "activity": [["active", None, None, 1]],
         "database": [0, 0, 0, 0, 0, 0, 0, 0, 100.0, 0],
         "statements": [0, 0, 40.0, 0, 0, 0]},
        {"locks": [["RowExclusiveLock", False, 3]],
         "activity": [["active", "Lock", "transactionid", 2]],
         "database": [0, 0, 0, 0, 0, 0, 0, 0, 150.0, 0],
         "statements": [0, 0, 90.0, 0, 0, 0]},
    ]
    assert BENCHMARK.monitoring_summary(samples) == {
        "sample_count": 2, "lock_wait_sample_count": 1,
        "max_ungranted_locks": 3, "max_lock_waiting_sessions": 2,
        "postgresql_active_time_delta_milliseconds": 50.0,
        "postgresql_statement_exec_delta_milliseconds": 50.0,
    }


def test_online_summary_uses_steady_window_and_keeps_drain_separate():
    baseline_metrics = {
        "progress_query_seconds": 1.0, "claim_milliseconds": 20.0,
        "ingestion_milliseconds": 30.0, "pool_wait_milliseconds": 2.0,
        "database_checkouts": 4, "database_retries": 0,
        "transaction_milliseconds": 10.0,
    }
    final_metrics = {
        "progress_query_seconds": 1.2, "claim_milliseconds": 30.0,
        "ingestion_milliseconds": 50.0, "pool_wait_milliseconds": 3.0,
        "database_checkouts": 7, "database_retries": 1,
        "transaction_milliseconds": 20.0,
    }
    events = [
        {"event": "phase", "phase": "measurement", "elapsed_seconds": 60.0,
         "metrics": baseline_metrics,
         "workload": {"headwords": 10, "articles": 20, "units": 30, "source_characters": 40}},
        {"event": "submitted", "elapsed_seconds": 61.0, "batch_id": "b1"},
        {"event": "completed", "elapsed_seconds": 100.0, "batch_id": "b1",
         "ok": True, "detail": "done", "latency_ms": 100,
         "input_tokens": 20, "cached_input_tokens": 5, "output_tokens": 4,
         "worker_peak_memory_bytes": 1000},
        {"event": "phase", "phase": "drain", "elapsed_seconds": 660.0,
         "metrics": final_metrics,
         "workload": {"headwords": 12, "articles": 23, "units": 35, "source_characters": 47}},
        {"event": "completed", "elapsed_seconds": 680.0, "batch_id": "b2",
         "ok": True, "detail": "done", "latency_ms": 200,
         "worker_peak_memory_bytes": 2000},
        {"event": "phase", "phase": "shutdown", "elapsed_seconds": 700.0,
         "metrics": final_metrics,
         "workload": {"headwords": 13, "articles": 24, "units": 36, "source_characters": 48}},
    ]
    monitor = [
        {"locks": [["AccessShareLock", True, 1]], "activity": [],
         "database": [0] * 10, "statements": [0] * 6, "duplicate_claimed_batches": 0},
        {"locks": [["AccessShareLock", True, 1]], "activity": [],
         "database": [0] * 10, "statements": [0] * 6, "duplicate_claimed_batches": 0},
    ]
    postflight = {
        "window_claimed_attempts": 0, "global_claimed_attempts": 0,
        "leased_batches": 0, "missing_units": 0, "duplicate_translations": 0,
        "unresolved_blocking_errors": 0,
    }
    summary = ONLINE.summarize(events, 600, monitor, postflight)
    assert summary["complete"] is True
    assert summary["completed"] == 1
    assert summary["drain_completed"] == 1
    assert summary["deltas"] == {
        "headwords": 2, "articles": 3, "units": 5, "source_characters": 7,
    }
    assert summary["rates"]["headwords_per_minute"] == 0.2
    assert summary["worker_peak_memory_bytes"] == 2000
    assert summary["database_duty_cycle"] == pytest.approx(10 / 600_000)


def test_online_incomplete_summary_records_quota_without_rates():
    workload = {"headwords": 1, "articles": 2, "units": 3, "source_characters": 4}
    postflight = {
        "window_claimed_attempts": 0, "global_claimed_attempts": 0,
        "leased_batches": 0, "missing_units": 0, "duplicate_translations": 0,
        "unresolved_blocking_errors": 0,
    }
    summary = ONLINE.incomplete_summary([
        {"event": "completed", "ok": False, "quota_boundary": True,
         "rate_limit": True, "transport_failure": False},
    ], workload, postflight)
    assert summary["complete"] is False
    assert summary["incomplete_reason"] == "quota_boundary"
    assert summary["quota_boundaries"] == 1
    assert summary["rates"]["headwords_per_minute"] == 0.0


def test_online_workload_profile_records_request_shape_and_validation_difficulty(tmp_path):
    connection = benchmark_database(tmp_path)
    path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    connection.execute(
        """INSERT INTO attempt(id,batch_id,worker_id,model,prompt_sha256,request_path,
        outcome) VALUES ('a','b','window-0001','m','p','r','rejected')"""
    )
    connection.execute(
        """INSERT INTO validation_issue(run_id,unit_id,attempt_id,validator,severity,code,details_json)
        VALUES (2,'u','a','deterministic-v1','error','protected_token_missing','{}')"""
    )
    connection.commit()
    connection.close()
    database = SimpleNamespace(connect=lambda: connect(path))

    profile = ONLINE.workload_profile(database, ["b", "b"], "window")
    assert profile == {
        "request_batch_count": 2, "unique_batch_count": 1,
        "serialized_bytes": 2, "batch_articles": 2, "batch_units": 2,
        "source_characters": 8, "protected_token_units": 0,
        "role_counts": {"glossary": 2},
        "validation_issue_codes": {"protected_token_missing": 1},
    }


def test_online_window_batch_ids_excludes_ramp_and_drain_for_complete_results():
    events = [
        {"event": "completed", "elapsed_seconds": 9, "batch_id": "ramp"},
        {"event": "phase", "phase": "measurement", "elapsed_seconds": 10},
        {"event": "completed", "elapsed_seconds": 11, "batch_id": "steady"},
        {"event": "phase", "phase": "drain", "elapsed_seconds": 20},
        {"event": "completed", "elapsed_seconds": 21, "batch_id": "drain"},
    ]
    assert ONLINE.window_batch_ids(events, True) == ["steady"]
    assert ONLINE.window_batch_ids(events, False) == ["ramp", "steady", "drain"]
