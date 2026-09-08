import importlib.util
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jitendex_ru.batch import claim, make_batches, retry_or_split
from jitendex_ru.config import Config
from jitendex_ru.database import Database
from jitendex_ru.extract_units import extract_selected
from jitendex_ru.jpdb_scope import reuse_accepted_translations
from jitendex_ru.run_integrity import workload_progress
from jitendex_ru.util import canonical_json, sha256_bytes
from jitendex_ru.validate_response import ingest_response


URL_ENV = "JITENDEX_TEST_POSTGRES_URL"
pytestmark = pytest.mark.skipif(not os.environ.get(URL_ENV), reason="disposable PostgreSQL URL not configured")
ROOT = Path(__file__).resolve().parents[1]
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "run_codex_batches_postgresql_test", ROOT / "scripts/run_codex_batches.py",
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = RUNNER
RUNNER_SPEC.loader.exec_module(RUNNER)


def configured(tmp_path):
    return Config(tmp_path, {
        "project": {"work_dir": str(tmp_path / "work"), "dist_dir": str(tmp_path / "dist")},
        "database": {"backend": "postgresql", "url_env": URL_ENV, "pool_max": 8},
        "models": {
            "translation": {"id": "gpt-5.6-luna", "reasoning_effort": "medium"},
            "review": {"id": "gpt-5.6-terra", "reasoning_effort": "medium"},
        },
    })


@pytest.fixture
def database(tmp_path):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    url = os.environ[URL_ENV]
    database_name = conninfo_to_dict(url).get("dbname", "")
    if not re.fullmatch(r"jitendex_lcp_[a-z0-9_]+", database_name):
        raise RuntimeError("PostgreSQL recovery tests require a jitendex_lcp_* database")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    instance = Database(configured(tmp_path))
    instance.migrate()
    try:
        yield instance
    finally:
        instance.close()


def seed(database, tmp_path, batch_count=4):
    connection = database.connect()
    try:
        connection.execute(
            """INSERT INTO source_snapshot(id,kind,version,url,sha256,local_path,extractor_version)
            VALUES (1,'jitendex','v','u','j','p','e'),(2,'kaishi','v','u','k','p','e')"""
        )
        connection.execute(
            """INSERT INTO run(id,jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,
            extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json)
            VALUES (1,1,2,'selection','e','p','r','t','{}')"""
        )
        for number in range(batch_count):
            path = tmp_path / f"empty-{number}.json"
            path.write_text("{}", encoding="utf-8")
            connection.execute(
                """INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,
                unit_count,manifest_path) VALUES (?,?,?,1,0,0,?)""",
                (f"empty-{number}", 1, f"hash-empty-{number}", str(path)),
            )
        connection.commit()
    finally:
        connection.close()


def test_postgresql_incremental_extraction_and_set_based_reuse(tmp_path, database):
    connection = database.connect()
    try:
        connection.execute(
            """INSERT INTO source_snapshot(id,kind,version,url,sha256,local_path,extractor_version)
            VALUES (1,'jitendex','v','u','j','p','e'),(2,'kaishi','v','u','k','p','e')"""
        )
        articles = (
            ["食べる", "たべる", "", "v1", 0,
             {"type": "structured-content", "content": {"tag": "span", "lang": "en",
              "data": {"content": "glossary"}, "content": "to eat"}},
             1, ""],
            ["飲む", "のむ", "", "v1", 0,
             {"type": "structured-content", "content": {"tag": "span", "lang": "en",
              "data": {"content": "glossary"}, "content": "to drink"}},
             2, ""],
        )
        for article_id, article in enumerate(articles, 1):
            raw = canonical_json(article).decode()
            connection.execute(
                """INSERT INTO article(id,snapshot_id,bank_number,entry_ordinal,expression,reading,
                sequence,raw_json,source_sha256,selected) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (article_id, 1, 1, article_id, article[0], article[1], article[6], raw,
                 sha256_bytes(raw.encode()), 1 if article_id == 1 else 0),
            )
        for run_id, selection in ((1, "source"), (2, "target")):
            connection.execute(
                """INSERT INTO run(id,jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,
                extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,
                pipeline_version) VALUES (?,1,2,?,'e','p','r','t','{}','lexicographer-v2')""",
                (run_id, selection),
            )
        source_result = extract_selected(connection, 1)
        assert source_result["units_added"] > 0
        source_unit = connection.execute(
            "SELECT * FROM translation_unit WHERE run_id=1 ORDER BY id LIMIT 1"
        ).fetchone()
        connection.execute(
            """INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,
            unit_count,manifest_path) VALUES ('source-batch',1,'source-manifest',1,1,1,'source')"""
        )
        connection.execute(
            "INSERT INTO batch_item(batch_id,unit_id,ordinal) VALUES ('source-batch',?,0)",
            (source_unit["id"],),
        )
        connection.execute(
            """INSERT INTO attempt(id,batch_id,worker_id,model,prompt_sha256,request_path,outcome)
            VALUES ('source-attempt','source-batch','worker','luna','p','request','accepted')"""
        )
        target = "есть"
        connection.execute(
            """INSERT INTO translation
            (run_id,unit_id,attempt_id,target_text,confidence,target_sha256,accepted)
            VALUES (1,?,'source-attempt',?,'high',?,1)""",
            (source_unit["id"], target, sha256_bytes(target.encode())),
        )
        connection.commit()

        connection.execute("UPDATE article SET selected=1")
        target_result = extract_selected(connection, 2, source_run_id=1)
        source_count = connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=1"
        ).fetchone()[0]
        target_count = connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=2"
        ).fetchone()[0]
        assert target_count > source_count
        assert target_result["units_added"] == target_count
        details = json.loads(connection.execute(
            """SELECT details_json FROM audit_event
            WHERE event_type='extract_units' AND entity_id='2' ORDER BY id DESC LIMIT 1"""
        ).fetchone()[0])
        assert details["articles_parsed"] == 1
        copied_id = source_unit["id"].replace("u-r1-", "u-r2-", 1)
        assert connection.execute(
            "SELECT source_sha256 FROM translation_unit WHERE id=?", (copied_id,),
        ).fetchone()[0] == source_unit["source_sha256"]

        reused = reuse_accepted_translations(connection, 1, 2)
        assert reused["units_reused"] == 1
        assert connection.execute(
            "SELECT status FROM translation_unit WHERE id=?", (copied_id,),
        ).fetchone()[0] == "translated"
        assert connection.execute(
            "SELECT COUNT(*) FROM translation_unit WHERE run_id=2 AND status='ready'"
        ).fetchone()[0] == target_count - 1
        assert reuse_accepted_translations(connection, 1, 2)["units_reused"] == 0
        made = make_batches(
            connection, 2, tmp_path / "inbox", {}, 12, 49_152, 200, 16_384,
        )
        assert made["units"] == target_count - 1
        assert made["phase_metrics"]["database_batch_loading"]["output_rows"] > made["units"]
        assert connection.execute(
            "SELECT COUNT(*) FROM batch WHERE run_id=2 AND kind='translation'"
        ).fetchone()[0] == made["batches_created"]
        assert connection.execute(
            """SELECT COUNT(*) FROM audit_event WHERE event_type='create'
            AND entity_type='batch' AND entity_id IN (SELECT id FROM batch WHERE run_id=2)"""
        ).fetchone()[0] == made["batches_created"]
        connection.commit()
    finally:
        connection.close()


def test_postgresql_claim_recovery_replay_split_and_connection_loss(tmp_path, database):
    config = configured(tmp_path)
    seed(database, tmp_path)
    metric_connection = database.connect()
    try:
        assert workload_progress(metric_connection, 1) == {
            "headwords": 0, "articles": 0, "units": 0, "source_characters": 0,
        }
    finally:
        metric_connection.close()

    def take(number):
        connection = database.connect()
        try:
            return claim(
                connection, f"parallel-{number}", tmp_path / "outbox", run_id=1,
                kind="translation", model_id="gpt-5.6-luna", reasoning_effort="medium",
                transport="codex-agent",
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as workers:
        claims = list(workers.map(take, range(4)))
    assert len({item["batch_id"] for item in claims if item}) == 4

    killed = claims[0]
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )
    RUNNER.CHILDREN[killed["attempt_id"]] = process
    RUNNER.STOP_REQUESTED.clear()
    try:
        RUNNER.request_stop()
        assert process.wait(timeout=10) < 0
    finally:
        RUNNER.CHILDREN.pop(killed["attempt_id"], None)
        RUNNER.STOP_REQUESTED.clear()
    assert RUNNER.interrupt_claim(config, killed, database)

    for item in claims[1:]:
        assert RUNNER.interrupt_claim(config, item, database)

    connection = database.connect()
    assert connection.execute(
        "SELECT COUNT(*) FROM attempt WHERE outcome='claimed'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM audit_event WHERE event_type='interrupt'"
    ).fetchone()[0] == 4

    expired = claim(
        connection, "expired", tmp_path / "outbox", run_id=1, kind="translation",
        batch_id="empty-0", model_id="gpt-5.6-luna", reasoning_effort="medium",
        transport="codex-agent",
    )
    connection.execute(
        "UPDATE batch SET lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 minute' WHERE id='empty-0'"
    )
    connection.commit()
    replacement = claim(
        connection, "replacement", tmp_path / "outbox", run_id=1, kind="translation",
        batch_id="empty-0", model_id="gpt-5.6-luna", reasoning_effort="medium",
        transport="codex-agent",
    )
    assert replacement["attempt_id"] != expired["attempt_id"]
    assert connection.execute(
        "SELECT outcome FROM attempt WHERE id=?", (expired["attempt_id"],)
    ).fetchone()[0] == "interrupted"
    connection.execute(
        "UPDATE attempt SET outcome='interrupted',completed_at=CURRENT_TIMESTAMP WHERE id=?",
        (replacement["attempt_id"],),
    )
    connection.execute(
        "UPDATE batch SET state='ready',lease_token=NULL,lease_expires_at=NULL WHERE id='empty-0'"
    )

    raw = json.dumps(["語", "ご", "", "", 0, [], 1, ""])
    connection.execute(
        """INSERT INTO article(id,snapshot_id,bank_number,entry_ordinal,expression,reading,
        sequence,raw_json,source_sha256,selected) VALUES (1,1,1,1,'語','ご',1,?,'article',1)""",
        (raw,),
    )
    connection.execute(
        "INSERT INTO run_article(run_id,article_id,structural_fingerprint) VALUES (1,1,'fingerprint')"
    )
    connection.execute(
        """INSERT INTO translation_unit(id,run_id,article_id,json_pointer,role,source_text,
        source_sha256,protected_tokens_json,byte_count) VALUES
        ('unit-1',1,1,'/5/0','glossary','word','source','[]',4),
        ('unit-2',1,1,'/5/1','glossary','term','source-2','[]',4)"""
    )
    manifest = {
        "schema_version": 1, "batch_id": "ingest", "manifest_sha256": "ingest-hash",
        "articles": [{"units": [{
            "unit_id": "unit-1", "source_sha256": "source", "role": "glossary",
            "source_text": "word", "protected_tokens": [],
        }]}],
    }
    manifest_path = tmp_path / "ingest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    connection.execute(
        """INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,
        unit_count,manifest_path) VALUES ('ingest',1,'ingest-hash',1,1,1,?)""",
        (str(manifest_path),),
    )
    connection.execute("INSERT INTO batch_item VALUES ('ingest','unit-1',0)")

    split_manifest = {
        "schema_version": 1, "batch_id": "split", "manifest_sha256": "split-hash",
        "terminology": {}, "articles": [{"article_id": "a-1", "units": [
            {"unit_id": "unit-1", "source_sha256": "source", "role": "glossary"},
            {"unit_id": "unit-2", "source_sha256": "source-2", "role": "glossary"},
        ]}],
    }
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(split_manifest), encoding="utf-8")
    connection.execute(
        """INSERT INTO batch(id,run_id,manifest_sha256,serialized_bytes,article_count,
        unit_count,state,attempt_count,manifest_path)
        VALUES ('split',1,'split-hash',1,1,2,'retryable',3,?)""", (str(split_path),)
    )
    connection.execute("INSERT INTO batch_item VALUES ('split','unit-1',0),('split','unit-2',1)")
    connection.commit()

    item = claim(
        connection, "ingester", tmp_path / "outbox", run_id=1, kind="translation",
        batch_id="ingest", model_id="gpt-5.6-luna", reasoning_effort="medium",
        transport="codex-agent",
    )
    response = Path(item["response_path"])
    response.write_text(json.dumps({
        "schema_version": 1, "batch_id": "ingest", "manifest_sha256": "ingest-hash",
        "translations": [{"unit_id": "unit-1", "source_sha256": "source",
                          "target_text": "слово", "confidence": "high", "review_reason": None}],
    }), encoding="utf-8")
    connection.close()
    accepted, detail = RUNNER.ingest(
        config, "translation",
        RUNNER.DispatchResult(
            claim=item, returncode=0, stdout="", stderr="", thread_id="thread-test",
            usage={"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
            latency_ms=25,
        ),
        database,
    )
    assert accepted
    assert json.loads(detail)["translations_ingested"] == 1
    assert database.metrics.transaction_ms > 0
    connection = database.connect()
    with pytest.raises(ValueError, match="stale attempt"):
        ingest_response(connection, response)
    connection.rollback()

    translation_id = connection.execute(
        "SELECT id FROM translation WHERE attempt_id=?", (item["attempt_id"],),
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO attempt_cost_report(
        attempt_id,price_snapshot_date,input_price_per_million,
        cached_input_price_per_million,output_price_per_million,input_tokens,
        cached_input_tokens,output_tokens,computed_cost)
        VALUES (?,'2026-08-13','1','1','1',1,0,1,'0.000002')""",
        (item["attempt_id"],),
    )
    connection.execute(
        """INSERT INTO translation_canonicalization_history(
        run_id,unit_id,translation_id,previous_target_text,previous_target_sha256,
        canonical_target_text,canonical_target_sha256,mapping_source,
        mapping_identity_json,canonicalizer_version)
        VALUES (1,'unit-1',?,'слово','old','слово','new','test','{}','test-v1')""",
        (translation_id,),
    )
    connection.commit()
    for statement in (
        "UPDATE attempt_cost_report SET computed_cost='0' WHERE attempt_id='" + item["attempt_id"] + "'",
        "DELETE FROM attempt_cost_report WHERE attempt_id='" + item["attempt_id"] + "'",
        "UPDATE translation_canonicalization_history SET mapping_source='changed' WHERE translation_id=" + str(translation_id),
        "DELETE FROM translation_canonicalization_history WHERE translation_id=" + str(translation_id),
    ):
        with pytest.raises(Exception, match="immutable"):
            connection.execute(statement)
        connection.rollback()

    split = retry_or_split(connection, "split")
    assert split["split"] and len(split["children"]) == 2
    assert retry_or_split(connection, "split")["split"] is False

    connection.execute("UPDATE run SET state='should-rollback' WHERE id=1")
    connection.close()
    connection = database.connect()
    assert connection.execute("SELECT state FROM run WHERE id=1").fetchone()[0] == "active"
    assert connection.execute(
        "SELECT COUNT(*) FROM attempt WHERE outcome='claimed'"
    ).fetchone()[0] == 0
    connection.close()
