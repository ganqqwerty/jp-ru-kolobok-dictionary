# HIST — JPDB Luna Translation Run History

HIST-1 — This file preserves the commands, observed outputs, repairs, audits, and export results from completed JPDB Luna runs. It is historical evidence, not the operational source of truth for future batches.

HIST-2 — For the current procedure, pinned inputs, provenance rules, and stopping point, use [JPDB_LUNA_ORCHESTRATION_RUNBOOK.md](JPDB_LUNA_ORCHESTRATION_RUNBOOK.md).

HIST-3 — The first part below is the legacy top-10k procedure with its recorded results. Later sections record cumulative continuations.

## HIST-10K-ENV — Top-10k historical inputs and environment

HIST-10K-ENV-1 — Run from:

~~~text
/Users/iuriikatkov/Documents/ChatGPT/jitendex-translations
~~~

HIST-10K-ENV-2 — Environment:

~~~bash
export PYTHONPATH=src
export UV_CACHE_DIR=/private/tmp/jitendex-uv-cache
~~~

HIST-10K-ENV-3 — Pinned inputs:

~~~text
Configuration: config.luna.clean-v1.toml
Prompt:        prompts/translate_luna_clean_v1.txt
Model:         gpt-5.6-luna
Effort:        medium
Database:      work/progress.sqlite3
JPDB ZIP:      work/downloads/Freq.JPDB_2022-05-10T03_27_02.930Z.zip
JPDB SHA-256:  5bda39a9e3b443b02199435ea723aa0555c891d1ce2c92ea7680163b72b07a0e
~~~

HIST-10K-ENV-4 — Verify the source:

~~~bash
shasum -a 256 work/downloads/Freq.JPDB_2022-05-10T03_27_02.930Z.zip
~~~

HIST-10K-ENV-5 — This workflow does not run the optional Terra review pass. translationctl validate therefore reports release_ready false and reviewed_units 0 even when Luna coverage and Yomitan validation are complete.

## HIST-10K-STATE — Top-10k state behavior observed

HIST-10K-STATE-1 — select-jpdb-scope replaces the live JPDB scope: it clears JPDB frequency mappings, clears article.selected, and selects articles for the new limit. Finish the current coverage report and export before selecting another limit.

HIST-10K-STATE-2 — Created runs remain frozen through run_article. Export reads run_article rather than mutable article.selected.

HIST-10K-STATE-3 — An identical selection and identical configuration can resolve to the existing run. Change batch.clean_run_nonce for a genuinely new clean rerun of the same scope.

## HIST-10K — Top-10k detailed execution record

HIST-10K-1 — The completed expansion used source run 5 and created target run 6.

### HIST-10K-S1 — 1. Select the first 10,000 JPDB rows

~~~bash
translationctl --config config.luna.clean-v1.toml \
  select-jpdb-scope \
  work/downloads/Freq.JPDB_2022-05-10T03_27_02.930Z.zip \
  --limit 10000
~~~

HIST-10K-S1-1 — Observed:

~~~text
requested_rows:       10,000
unique_terms:          9,395
matched_terms:         9,350
skipped_terms:            45
selected_articles:    12,901
expression_matches:   11,271
reading_matches:       3,542
~~~

HIST-10K-S1-2 — Duplicate spellings keep their earliest rank. Each spelling matches exact normalized Jitendex expressions and readings and can map to multiple articles.

HIST-10K-S1-3 — Do not run resolve-scope for this step. That command is for Kaishi candidate generation; select-jpdb-scope directly establishes the JPDB selection.

### HIST-10K-S2 — 2. Create the run and extract units

~~~bash
translationctl --config config.luna.clean-v1.toml extract-units
~~~

HIST-10K-S2-1 — Observed:

~~~json
{
  "articles_without_units": 557,
  "run_id": 6,
  "units_added": 148017
}
~~~

HIST-10K-S2-2 — This freezes every selected article and its structural fingerprint in run_article.

### HIST-10K-S3 — 3. Reuse accepted top-5k translations

~~~bash
translationctl --config config.luna.clean-v1.toml \
  reuse-translations \
  --source-run-id 5 \
  --target-run-id 6
~~~

HIST-10K-S3-1 — Reuse requires an exact article ID, JSON pointer, role, source SHA-256, and an accepted source-run translation.

HIST-10K-S3-2 — Observed: 92,228 reused units.

HIST-10K-S3-3 — Run 5 contained 92,238 accepted units. Ten old units were image rendering controls named appearance and sizeUnits. The extractor was corrected to classify them as structure, so they intentionally do not exist in run 6.

### HIST-10K-S4 — 4. Batch only remaining ready units

~~~bash
translationctl --config config.luna.clean-v1.toml \
  make-batches \
  --run-id 6
~~~

HIST-10K-S4-1 — Observed:

~~~json
{
  "articles": 5430,
  "batches_created": 988,
  "units": 55789
}
~~~

HIST-10K-S4-2 — Limits from the configuration:

~~~text
soft_max_articles = 6
soft_max_bytes = 24576
soft_max_units = 100
singleton_threshold_bytes = 16384
hard_max_article_bytes = 49152
hard_max_article_units = 200
~~~

HIST-10K-S4-3 — Manifests are stored in work/inbox. Batch identity is deterministic over run ID, article IDs, and ordered unit IDs.

### HIST-10K-S5 — 5. Start four parallel Luna pools

HIST-10K-S5-1 — The orchestrator used four persistent PTY sessions with concurrency 20 each, for target concurrency 80.

HIST-10K-S5-2 — Pool 1:

~~~bash
python scripts/run_codex_batches.py \
  --config config.luna.clean-v1.toml \
  --run-id 6 \
  --kind translation \
  --concurrency 20 \
  --worker-prefix jpdb10k-pool1
~~~

HIST-10K-S5-3 — Run the same command in three more sessions with these prefixes:

~~~text
jpdb10k-pool2
jpdb10k-pool3
jpdb10k-pool4
~~~

HIST-10K-S5-4 — For each batch the runner:

HIST-10K-S5-5 — claims and leases the batch;

HIST-10K-S5-6 — creates an attempt row;

HIST-10K-S5-7 — generates a strict schema for the exact ordered unit set;

HIST-10K-S5-8 — supplies the configured prompt and one manifest;

HIST-10K-S5-9 — invokes the bundled Codex CLI ephemerally in a read-only temporary workspace;

HIST-10K-S5-10 — writes the model response to work/outbox;

HIST-10K-S5-11 — records model identity, tokens, request/thread ID, and latency;

HIST-10K-S5-12 — runs deterministic validation;

HIST-10K-S5-13 — ingests valid output;

HIST-10K-S5-14 — retries or recursively splits rejected output;

HIST-10K-S5-15 — claims more work until no ready batch remains.

HIST-10K-S5-16 — The subprocess runner currently has no explicit timeout.

### HIST-10K-S6 — 6. Monitor authoritative SQLite state

~~~bash
sqlite3 -cmd '.timeout 5000' -header -column work/progress.sqlite3 "
SELECT state,COUNT(*) batches,SUM(unit_count) units
FROM batch
WHERE run_id=6
GROUP BY state
ORDER BY state;

SELECT COUNT(*) unresolved_errors
FROM validation_issue
WHERE run_id=6
  AND severity='error'
  AND resolved_at IS NULL;

SELECT COUNT(*) orphaned_unresolved
FROM validation_issue vi
WHERE vi.run_id=6
  AND vi.severity='error'
  AND vi.resolved_at IS NULL
  AND NOT EXISTS (
    SELECT 1
    FROM attempt a
    JOIN batch b ON b.id=a.batch_id
    WHERE a.id=vi.attempt_id
      AND b.state IN ('ready','leased','retryable')
  );

SELECT COUNT(*) leaf_blocked
FROM batch b
WHERE b.run_id=6
  AND b.state='blocked'
  AND NOT EXISTS (
    SELECT 1
    FROM audit_event ae
    WHERE ae.event_type='split'
      AND ae.entity_id=b.id
  );
"
~~~

HIST-10K-S6-1 — Interpretation:

HIST-10K-S6-2 — ready, leased, and retryable are unfinished;

HIST-10K-S6-3 — deterministic_validated is completed work awaiting acceptance;

HIST-10K-S6-4 — blocked with a split audit event is a superseded parent, not a gap;

HIST-10K-S6-5 — leaf_blocked requires targeted repair;

HIST-10K-S6-6 — orphaned_unresolved should stay zero while retries remain active.

HIST-10K-S6-7 — Normal automatic failures included no_cyrillic, too_much_english, unit_order_or_set_mismatch, and protected_token_missing. Let retry/split isolation finish before manual repair. Do not repair or requeue a batch solely for a canonical terminology or tag wording difference; defer that difference to the final top-300k normalization pass described above.

### HIST-10K-S7 — 7. Recover a genuinely stalled worker

HIST-10K-S7-1 — One 66-unit batch stayed silent for repeated bounded waits because the runner has no subprocess timeout.

HIST-10K-S7-2 — The orchestrator interrupted the owning PTY with Ctrl-C, confirmed the leased attempt, marked it interrupted, requeued it, recorded an audit event, and ran one replacement worker.

HIST-10K-S7-3 — Find active leases:

~~~bash
sqlite3 -header -column work/progress.sqlite3 "
SELECT b.id batch_id,b.unit_count,b.lease_expires_at,
       a.id attempt_id,a.created_at,a.outcome,
       a.request_path,a.response_path
FROM batch b
JOIN attempt a ON a.batch_id=b.id
WHERE b.run_id=6
  AND b.state='leased'
ORDER BY a.created_at DESC;
"
~~~

HIST-10K-S7-4 — Only after confirming the process is stopped:

~~~sql
BEGIN IMMEDIATE;

UPDATE attempt
SET outcome='interrupted',
    error_json='{"reason":"stalled codex subprocess interrupted after repeated bounded waits"}',
    completed_at=CURRENT_TIMESTAMP
WHERE id='<ATTEMPT_ID>'
  AND outcome='claimed';

UPDATE batch
SET state='ready',
    lease_token=NULL,
    lease_expires_at=NULL
WHERE id='<BATCH_ID>'
  AND state='leased';

INSERT INTO audit_event(event_type,entity_type,entity_id,details_json)
VALUES (
  'recover_interrupted_worker',
  'batch',
  '<BATCH_ID>',
  '{"attempt_id":"<ATTEMPT_ID>"}'
);

COMMIT;
~~~

HIST-10K-S7-5 — Replacement:

~~~bash
python scripts/run_codex_batches.py \
  --config config.luna.clean-v1.toml \
  --run-id 6 \
  --kind translation \
  --concurrency 1 \
  --worker-prefix jpdb10k-recovered
~~~

HIST-10K-S7-6 — The replacement failed one strict order/set validation, retried automatically, then ingested all 66 units.

### HIST-10K-S8 — 8. Repair exhausted singleton leaves through normal ingestion

HIST-10K-S8-1 — Wait for all automatic workers to exit, then inspect only terminal leaves:

HIST-10K-S8-2 — Repair only deterministic structural/content failures at this stage. Do not create a targeted repair merely to force a known tag or whole-leaf term into its canonical wording; that cleanup is intentionally deferred until the final cumulative run.

~~~bash
sqlite3 -header -column work/progress.sqlite3 "
SELECT b.id batch_id,
       tu.id unit_id,
       tu.article_id,
       tu.role,
       tu.json_pointer,
       tu.source_text,
       tu.source_sha256,
       tu.protected_tokens_json
FROM batch b
JOIN batch_item bi ON bi.batch_id=b.id
JOIN translation_unit tu ON tu.id=bi.unit_id
WHERE b.run_id=6
  AND b.state='blocked'
  AND NOT EXISTS (
    SELECT 1 FROM audit_event ae
    WHERE ae.event_type='split'
      AND ae.entity_id=b.id
  )
ORDER BY b.id,bi.ordinal;
"
~~~

HIST-10K-S8-3 — Run 6 had five leaves representing four translations:

| Source | Accepted Russian target |
|---|---|
| Boston Dynamic's robot, RHex, is an amazing piece of work that can run over various terrains. | Робот «Рэкс» компании «Бостон Дайнэмикс» — удивительная разработка, способная передвигаться по самой разной местности. |
| kozo (Broussonetia kazinoki x papyrifera); Japanese paper mulberry tree | кодзо (гибрид бруссонетии Кадзиноки и бруссонетии бумажной); японское бумажное дерево |
| from initials of Linux, Apache, MySQL, PHP | от начальных букв названий «Линукс», «Апачи», «Май-эс-кью-эл» и «Пи-эйч-пи» |
| glossary set LAMP | ЛАМП |

HIST-10K-S8-4 — The Boston example occurred in two articles.

HIST-10K-S8-5 — Requeue only terminal leaves:

~~~sql
BEGIN IMMEDIATE;

UPDATE batch
SET state='ready',
    lease_token=NULL,
    lease_expires_at=NULL
WHERE run_id=6
  AND state='blocked'
  AND NOT EXISTS (
    SELECT 1 FROM audit_event ae
    WHERE ae.event_type='split'
      AND ae.entity_id=batch.id
  );

COMMIT;
~~~

HIST-10K-S8-6 — Explicitly claim each batch:

~~~bash
translationctl --config config.luna.clean-v1.toml \
  claim \
  --worker-id targeted-top10k-repair \
  --run-id 6 \
  --kind translation \
  --batch-id '<BATCH_ID>' \
  --transport codex-agent
~~~

HIST-10K-S8-7 — Read the returned request_path and write strict JSON to the returned response_path:

~~~json
{
  "schema_version": 2,
  "batch_id": "<BATCH_ID>",
  "manifest_sha256": "<MANIFEST_SHA256>",
  "translations": [
    {
      "unit_id": "<UNIT_ID>",
      "source_sha256": "<SOURCE_SHA256>",
      "target_text": "русский перевод",
      "confidence": "high",
      "review_reason": null
    }
  ]
}
~~~

HIST-10K-S8-8 — For glossary_set, target_text must be an array:

~~~json
"target_text": ["ЛАМП"]
~~~

HIST-10K-S8-9 — Requirements:

HIST-10K-S8-10 — exact unit order and set;

HIST-10K-S8-11 — exact source hashes;

HIST-10K-S8-12 — no extra fields;

HIST-10K-S8-13 — preserve protected tokens;

HIST-10K-S8-14 — Cyrillic unless explicitly allowed;

HIST-10K-S8-15 — no markup/control characters;

HIST-10K-S8-16 — at most two unprotected ASCII words after allowed scientific-taxon handling.

HIST-10K-S8-17 — Ingest each file through the standard validator:

~~~bash
translationctl --config config.luna.clean-v1.toml \
  ingest-response \
  '<RESPONSE_PATH>'
~~~

HIST-10K-S8-18 — A valid later response resolves earlier deterministic issues for the same batch.

### HIST-10K-S9 — 9. Prove all translation work is drained

HIST-10K-S9-1 — Required run-6 state before final acceptance:

~~~text
deterministic_validated units: 55,789
active batches:                    0
unresolved errors:                 0
leaf-blocked batches:              0
translated units:            148,017
~~~

HIST-10K-S9-2 — Check with:

~~~bash
sqlite3 -header -column work/progress.sqlite3 "
SELECT state,COUNT(*) batches,SUM(unit_count) units
FROM batch WHERE run_id=6
GROUP BY state ORDER BY state;

SELECT COUNT(*) unresolved_errors
FROM validation_issue
WHERE run_id=6
  AND severity='error'
  AND resolved_at IS NULL;

SELECT COUNT(*) active_batches
FROM batch
WHERE run_id=6
  AND state IN ('ready','leased','retryable');

SELECT COUNT(*) leaf_blocked
FROM batch b
WHERE b.run_id=6
  AND b.state='blocked'
  AND NOT EXISTS (
    SELECT 1 FROM audit_event ae
    WHERE ae.event_type='split'
      AND ae.entity_id=b.id
  );

SELECT COUNT(*) translated_units
FROM translation_unit
WHERE run_id=6
  AND status='translated';
"
~~~

### HIST-10K-S10 — 10. Accept deterministic translations

~~~bash
translationctl --config config.luna.clean-v1.toml \
  accept-translations \
  --run-id 6
~~~

HIST-10K-S10-1 — Observed:

~~~json
{
  "run_id": 6,
  "translations_accepted": 55789
}
~~~

### HIST-10K-S11 — 11. Run the coverage gate

~~~bash
translationctl --config config.luna.clean-v1.toml \
  report-jpdb-coverage \
  --run-id 6
~~~

HIST-10K-S11-1 — Observed:

~~~json
{
  "complete": true,
  "covered_terms": 9350,
  "fully_accepted_articles": 12901,
  "matched_terms": 9350,
  "run_id": 6,
  "selected_articles": 12901,
  "skipped_terms": 45,
  "source_sha256": "5bda39a9e3b443b02199435ea723aa0555c891d1ce2c92ea7680163b72b07a0e",
  "unique_terms": 9395
}
~~~

HIST-10K-S11-2 — complete true means every matched JPDB term reaches a frozen run article and every selected article has all units accepted.

### HIST-10K-S12 — 12. Build the dictionary

~~~bash
translationctl --config config.luna.clean-v1.toml \
  build \
  --run-id 6 \
  --output dist/jitendex-jpdb-10k-ru-luna-clean-v1.zip
~~~

HIST-10K-S12-1 — Observed:

~~~json
{
  "articles": 12901,
  "export_id": 12,
  "files": 24,
  "zip_sha256": "a13a670937df9c1ffecaab815e6fc51773bf1b276e4cc6de05c5c38fbc2aa113"
}
~~~

### HIST-10K-S13 — 13. Verify ZIP and pinned Yomitan schemas

~~~bash
translationctl --config config.luna.clean-v1.toml \
  verify \
  dist/jitendex-jpdb-10k-ru-luna-clean-v1.zip
~~~

HIST-10K-S13-1 — Observed:

~~~json
{
  "articles": 12901,
  "files": 24,
  "schema_validated_banks": 10,
  "verified": true,
  "zip_sha256": "a13a670937df9c1ffecaab815e6fc51773bf1b276e4cc6de05c5c38fbc2aa113"
}
~~~

HIST-10K-S13-2 — This checks index placement, duplicate members, target language, media, frozen article count, recorded export hash, and every pinned term-bank schema.

### HIST-10K-S14 — 14. Run tests and run validation

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest -q
~~~

HIST-10K-S14-1 — Observed: 49 passed.

HIST-10K-S14-3 — The original historical shell used bare `pytest -q` and inherited repository import settings. The code block now shows the equivalent self-contained command required for new runs. Without `PYTHONPATH=src`, collection can stop before any test executes.

~~~bash
translationctl --config config.luna.clean-v1.toml \
  validate \
  --run-id 6
~~~

HIST-10K-S14-2 — Observed:

~~~json
{
  "accepted_units": 148017,
  "batch_membership_mismatches": 0,
  "blocking_issues": 0,
  "release_ready": false,
  "reviewed_units": 0,
  "run_id": 6,
  "units": 148017
}
~~~

HIST-10K-S14-3 — release_ready false is caused by the intentionally omitted review stage.

### HIST-10K-S15 — 15. Final database and archive audit

~~~bash
sqlite3 -header -column work/progress.sqlite3 "
SELECT COUNT(*) unique_terms,
       SUM(matched) matched_terms,
       SUM(CASE WHEN matched=0 THEN 1 ELSE 0 END) skipped_terms,
       MIN(rank) min_rank,
       MAX(rank) max_rank
FROM frequency_term
WHERE source='jpdb';

SELECT COUNT(DISTINCT fa.article_id) mapped_articles,
       COUNT(*) mappings
FROM frequency_article fa
WHERE fa.source='jpdb';

SELECT COUNT(*) missing_mapped_run_articles
FROM frequency_article fa
WHERE fa.source='jpdb'
  AND NOT EXISTS (
    SELECT 1 FROM run_article ra
    WHERE ra.run_id=6
      AND ra.article_id=fa.article_id
  );

SELECT COUNT(*) run_articles
FROM run_article WHERE run_id=6;

SELECT COUNT(*) units
FROM translation_unit WHERE run_id=6;

SELECT COUNT(*) accepted_translations,
       COUNT(DISTINCT unit_id) accepted_units
FROM translation
WHERE run_id=6 AND accepted=1;

SELECT COUNT(*) units_without_exactly_one_accept
FROM translation_unit tu
WHERE tu.run_id=6
  AND (
    SELECT COUNT(*)
    FROM translation t
    WHERE t.run_id=tu.run_id
      AND t.unit_id=tu.id
      AND t.accepted=1
  )<>1;

SELECT COUNT(*) unresolved_errors
FROM validation_issue
WHERE run_id=6
  AND severity='error'
  AND resolved_at IS NULL;

SELECT COUNT(*) active_batches
FROM batch
WHERE run_id=6
  AND state IN ('ready','leased','retryable');

SELECT COUNT(*) leaf_blocked
FROM batch b
WHERE b.run_id=6
  AND b.state='blocked'
  AND NOT EXISTS (
    SELECT 1 FROM audit_event ae
    WHERE ae.event_type='split'
      AND ae.entity_id=b.id
  );

SELECT id,run_id,output_path,zip_sha256,verified
FROM export
WHERE run_id=6
ORDER BY id DESC
LIMIT 1;
"
~~~

HIST-10K-S15-1 — Required:

~~~text
unique terms:                       9,395
matched terms:                      9,350
skipped terms:                         45
rank range:                         1–10,000
mapped/frozen articles:             12,901
frequency-to-article mappings:      14,813
missing mapped run articles:             0
translation units:                148,017
accepted units:                   148,017
units without exactly one accept:       0
unresolved errors:                      0
active batches:                         0
leaf blocked:                           0
latest export verified:                 1
~~~

HIST-10K-S15-2 — Archive:

~~~bash
shasum -a 256 dist/jitendex-jpdb-10k-ru-luna-clean-v1.zip
unzip -Z1 dist/jitendex-jpdb-10k-ru-luna-clean-v1.zip | wc -l

for n in 1 2 3 4 5 6 7 8 9 10; do
  unzip -p dist/jitendex-jpdb-10k-ru-luna-clean-v1.zip "term_bank_$n.json" | jq length
done | awk '{s+=$1} END {print s}'
~~~

HIST-10K-S15-3 — Required:

~~~text
SHA-256:        a13a670937df9c1ffecaab815e6fc51773bf1b276e4cc6de05c5c38fbc2aa113
ZIP members:    24
term-bank rows: 12,901
~~~

## HIST-10K-SUM — Completed run summary

| Scope | Run | Unique | Matched | Skipped | Articles | Accepted units | Reused | New | Schema banks | ZIP SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Top 5,000 rows | 5 | 4,749 | 4,726 | 23 | 7,194 | 92,238 | 26,638 | 65,600 | 6 | 752ad0919317b75764e5117a9e24ba62e9f9ee448b0336d8692da75885cd06fa |
| Top 10,000 rows | 6 | 9,395 | 9,350 | 45 | 12,901 | 148,017 | 92,228 | 55,789 | 10 | a13a670937df9c1ffecaab815e6fc51773bf1b276e4cc6de05c5c38fbc2aa113 |
| Top 20,000 rows | 7 | 18,595 | 18,481 | 114 | 23,432 | 241,479 | 148,017 | 93,462 | 16 | cfe26fc8dd4177d4dffea667af8e27eef6df4fb5869eca4f79f4d237141a861a |
| Top 30,000 rows | 8 | 27,717 | 27,472 | 245 | 33,603 | 323,186 | 241,479 | 81,707 | 21 | f6334307b20eb69f80ecfa7932f3a8d76655c1acf389d573c91946ac741a4824 |
| Top 40,000 rows | 9 | 36,700 | 36,233 | 467 | 43,347 | 397,771 | 323,186 | 74,585 | 25 | 97b0414caf834589cf2750c05a83a9ada70b152671e1261d35ed8801ad228489 |
| Top 50,000 rows | 10 | 45,581 | 44,791 | 790 | 52,631 | 465,525 | 397,771 | 67,754 | 29 | 562b3f628c7fcbab03cb7701bfe8f127ef0a64629b26c1c15983ec7bfa3ee673 |
| Top 60,000 rows | 11 | 54,442 | 53,264 | 1,178 | 61,909 | 530,142 | 465,525 | 64,617 | 33 | 566507ed9ead96b130fc7785b82c58348d5dbc1df6e769dc64747fade04d9bd4 |
| Top 70,000 rows | 12 | 63,255 | 61,606 | 1,649 | 70,864 | 589,571 | 530,142 | 59,429 | 36 | dde40d8265239f69fc394c2a8b110fe3b52877ee29ee2618bd7c6b619674c07f |
| Top 80,000 rows | 13 | 71,923 | 69,705 | 2,218 | 79,397 | 644,744 | 589,571 | 55,173 | 39 | 1ff1147705bcb5f39bc2906c1b7f7669d33204cad344b5fe193d67c1370b5daa |
| Top 90,000 rows | 14 | 80,556 | 77,688 | 2,868 | 87,872 | 698,173 | 644,744 | 53,429 | 42 | e660db82617968ebc6e4ef63361609c9b34e9fab26c8c441cc5ce3834d7001c2 |
| Top 100,000 rows | 15 | 89,142 | 85,514 | 3,628 | 96,081 | 748,745 | 698,173 | 50,572 | 45 | fe6024a2fa0ef1a1a589ec7ce94700f1810da40fb0c0049d9919c1a036cda622 |

## HIST-20K — Top-20k continuation results

HIST-20K-1 — The top-20k expansion followed the top-10k procedure with source run 6 and target run 7.

~~~bash
translationctl --config config.luna.clean-v1.toml \
  select-jpdb-scope \
  work/downloads/Freq.JPDB_2022-05-10T03_27_02.930Z.zip \
  --limit 20000

translationctl --config config.luna.clean-v1.toml extract-units

translationctl --config config.luna.clean-v1.toml \
  reuse-translations \
  --source-run-id 6 \
  --target-run-id 7

translationctl --config config.luna.clean-v1.toml \
  make-batches \
  --run-id 7
~~~

HIST-20K-2 — Observed scope and extraction:

~~~text
requested rows:          20,000
unique terms:            18,595
matched terms:           18,481
skipped terms:              114
selected articles:       23,432
frequency mappings:      26,967
rank-10,001–20,000 unique terms:   9,200
rank-10,001–20,000 matched terms:  9,131
rank-10,001–20,000 skipped terms:     69
new articles beyond run 6:         10,531
translation units:      241,479
articles without units:   1,154
reused run-6 units:      148,017
new Luna units:           93,462
initial batches:           1,749
~~~

HIST-20K-3 — Four pools ran at concurrency 20 with prefixes `jpdb20k-pool1` through `jpdb20k-pool4`. Automatic retries and recursive splits validated 93,460 new units. Two exhausted singleton example units were requeued, explicitly claimed, translated from their supplied Japanese article context, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| Why don't you try out getting a new Windows Media Player skin and changing the player's look? | Попробуйте установить новую тему оформления для «Виндоус Медиа Плеер» и изменить внешний вид проигрывателя. |
| What Microsoft is launching is a beta version of its "NetShow streaming server"; it supplies video and audio on demand. | Компания «Майкрософт» запускает бета-версию сервера потоковой передачи «НетШоу», который предоставляет видео и аудио по запросу. |

HIST-20K-4 — Final translation state before acceptance:

~~~text
deterministic_validated units: 93,462
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             241,479
~~~

HIST-20K-5 — Acceptance and coverage:

~~~bash
translationctl --config config.luna.clean-v1.toml \
  accept-translations \
  --run-id 7

translationctl --config config.luna.clean-v1.toml \
  report-jpdb-coverage \
  --run-id 7
~~~

HIST-20K-6 — Observed: 93,462 newly accepted translations and `complete: true`, covering all 18,481 matched terms and all 23,432 frozen articles.

HIST-20K-7 — Build and verification:

~~~bash
translationctl --config config.luna.clean-v1.toml \
  build \
  --run-id 7 \
  --output dist/jitendex-jpdb-20k-ru-luna-clean-v1.zip

translationctl --config config.luna.clean-v1.toml \
  verify \
  dist/jitendex-jpdb-20k-ru-luna-clean-v1.zip
~~~

HIST-20K-8 — Verified output:

~~~text
articles:               23,432
ZIP members:                36
schema-validated banks:     16
SHA-256: cfe26fc8dd4177d4dffea667af8e27eef6df4fb5869eca4f79f4d237141a861a
~~~

HIST-20K-9 — Run validation reported 241,479 accepted units, zero batch-membership mismatches, and zero blocking issues. As with earlier Luna-only runs, `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-30K — Top-30k continuation results

HIST-30K-1 — The top-30k expansion followed the same cumulative procedure with source run 7 and target run 8.

~~~bash
translationctl --config config.luna.clean-v1.toml \
  select-jpdb-scope \
  work/downloads/Freq.JPDB_2022-05-10T03_27_02.930Z.zip \
  --limit 30000

translationctl --config config.luna.clean-v1.toml extract-units

translationctl --config config.luna.clean-v1.toml \
  reuse-translations \
  --source-run-id 7 \
  --target-run-id 8

translationctl --config config.luna.clean-v1.toml \
  make-batches \
  --run-id 8
~~~

HIST-30K-2 — Observed scope and extraction:

~~~text
requested rows:          30,000
unique terms:            27,717
matched terms:           27,472
skipped terms:              245
selected articles:       33,603
frequency mappings:      38,792
rank-20,001–30,000 unique terms:   9,122
rank-20,001–30,000 matched terms:  8,991
rank-20,001–30,000 skipped terms:    131
new articles beyond run 7:         10,171
translation units:      323,186
articles without units:   1,749
reused run-7 units:      241,479
new Luna units:           81,707
initial batches:           1,635
~~~

HIST-30K-3 — Four pools ran at concurrency 20 with prefixes `jpdb30k-pool1` through `jpdb30k-pool4`. Automatic retries and recursive splits validated 81,705 new units. Two exhausted singleton units were requeued, explicitly claimed, translated from their supplied article context, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| kozo (Broussonetia kazinoki x papyrifera); Japanese paper mulberry tree | кодзо (гибрид бруссонетии Кадзиноки и бруссонетии бумажной); японское бумажное дерево |
| edible seaweed, usu. Porphyra yezoensis or P. tenera, usu. dried and pressed into sheets | съедобная морская водоросль, обычно порфира йезоэнсис или порфира тенера; как правило, высушивается и прессуется в листы |

HIST-30K-4 — Final translation state before acceptance:

~~~text
deterministic_validated units: 81,707
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             323,186
~~~

HIST-30K-5 — Acceptance and coverage reported 81,707 newly accepted translations and `complete: true`, covering all 27,472 matched terms and all 33,603 frozen articles.

HIST-30K-6 — Verified output:

~~~text
articles:               33,603
ZIP members:                48
schema-validated banks:     21
SHA-256: f6334307b20eb69f80ecfa7932f3a8d76655c1acf389d573c91946ac741a4824
~~~

HIST-30K-7 — Run validation reported 323,186 accepted units, zero batch-membership mismatches, and zero blocking issues. All 52 tests passed. As with earlier Luna-only runs, `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-40K — Top-40k continuation results

HIST-40K-1 — The top-40k expansion used source run 8 and target run 9.

~~~text
requested rows:          40,000
unique terms:            36,700
matched terms:           36,233
skipped terms:              467
selected articles:       43,347
frequency mappings:      50,254
rank-30,001–40,000 unique terms:   8,983
rank-30,001–40,000 matched terms:  8,761
rank-30,001–40,000 skipped terms:    222
new articles beyond run 8:          9,744
translation units:      397,771
articles without units:   2,380
reused run-8 units:      323,186
new Luna units:           74,585
initial batches:           1,555
~~~

HIST-40K-2 — Four pools ran at concurrency 20 with prefixes `jpdb40k-pool1` through `jpdb40k-pool4`. One 43-unit batch remained silent for repeated bounded waits in a live Codex subprocess. The owning pool was interrupted, the exact claimed attempt was marked `interrupted`, the batch was requeued with a `recover_interrupted_worker` audit event, and a one-worker replacement ingested all 43 units.

HIST-40K-3 — Automatic retries and recursive splits validated 74,573 new units. Twelve exhausted singleton units were requeued, explicitly claimed, translated from their supplied article context, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| lowest seat; foot of the table; seat furthest from the seat of honour | последнее место; нижний конец стола; место, наиболее удалённое от почётного места |
| spelling and reading variants | варианты написания и чтения |
| forms | формы |
| large pink (Dianthus superbus var. longicalycinus) | гвоздика пышная длинночашечковая |
| Lagenaria siceraria var. depressa (variety of bottle gourd) | лагенария обыкновенная, разновидность приплюснутая (сорт бутылочной тыквы) |
| wasei | японское слово, образованное из английских элементов |
| English: "concent(ric plug)" | англ.: «концентрическая вилка» |
| noun | сущ. |
| ① pink (any flower of genus Dianthus, esp. the fringed pink, Dianthus superbus) | ① гвоздика (любое растение рода Гвоздика, особенно гвоздика пышная) |
| English: "towel (blan)ket" | англ.: от «полотенце» и «одеяло» |
| ① Macintosh (brand of personal computer manufactured by Apple); Mac | ① «Макинтош» (марка персональных компьютеров компании «Эппл»); «Мак» |
| ① bottle gourd (Lagenaria siceraria var. hispida); calabash | ① бутылочная тыква (лагенария обыкновенная, разновидность щетинистая); калебаса |

HIST-40K-4 — Final translation state before acceptance:

~~~text
deterministic_validated units: 74,585
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             397,771
~~~

HIST-40K-5 — Acceptance and coverage reported 74,585 newly accepted translations and `complete: true`, covering all 36,233 matched terms and all 43,347 frozen articles.

HIST-40K-6 — Verified output:

~~~text
articles:               43,347
ZIP members:                56
schema-validated banks:     25
SHA-256: 97b0414caf834589cf2750c05a83a9ada70b152671e1261d35ed8801ad228489
~~~

HIST-40K-7 — Run validation reported 397,771 accepted units, zero batch-membership mismatches, and zero blocking issues. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-50K — Top-50k continuation results

HIST-50K-1 — The top-50k expansion used source run 9 and target run 10.

~~~text
requested rows:          50,000
unique terms:            45,581
matched terms:           44,791
skipped terms:              790
selected articles:       52,631
frequency mappings:      61,157
rank-40,001–50,000 unique terms:   8,881
rank-40,001–50,000 matched terms:  8,558
rank-40,001–50,000 skipped terms:    323
new articles beyond run 9:          9,284
translation units:      465,525
articles without units:   3,065
reused run-9 units:      397,771
new Luna units:           67,754
initial batches:           1,459
~~~

HIST-50K-2 — Four pools ran at concurrency 20 with prefixes `jpdb50k-pool1` through `jpdb50k-pool4`. Automatic retries and recursive splits validated 67,751 new units. Three exhausted singleton units were requeued, explicitly claimed, translated from their supplied article context, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| marron (Cherax tenuimanus and Cherax cainii species of freshwater crayfish) | маррон (пресноводный рак видов херакс тенуиманус и херакс Каина) |
| gentian (Gentiana scabra var. buergeri); autumn bellflower | горечавка шероховатая, разновидность Бюргера; осенний колокольчик |
| English: "televi(sion) game" | англ.: «телевизионная игра» |

HIST-50K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 67,754
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             465,525
~~~

HIST-50K-4 — Acceptance and coverage reported 67,754 newly accepted translations and `complete: true`, covering all 44,791 matched terms and all 52,631 frozen articles.

HIST-50K-5 — Verified output:

~~~text
articles:               52,631
ZIP members:                75
schema-validated banks:     29
SHA-256: 562b3f628c7fcbab03cb7701bfe8f127ef0a64629b26c1c15983ec7bfa3ee673
~~~

HIST-50K-6 — Run validation reported 465,525 accepted units, zero batch-membership mismatches, and zero blocking issues. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-60K — Top-60k continuation results

HIST-60K-1 — The top-60k expansion used source run 10 and target run 11.

~~~text
requested rows:          60,000
unique terms:            54,442
matched terms:           53,264
skipped terms:            1,178
selected articles:       61,909
frequency mappings:      72,004
rank-50,001–60,000 unique terms:   8,861
rank-50,001–60,000 matched terms:  8,473
rank-50,001–60,000 skipped terms:    388
new articles beyond run 10:         9,278
translation units:      530,142
articles without units:   3,749
reused run-10 units:     465,525
new Luna units:           64,617
initial batches:           1,447
~~~

HIST-60K-2 — Four pools ran at concurrency 20 with prefixes `jpdb60k-pool1` through `jpdb60k-pool4`. Automatic retries and recursive splits validated 64,610 new units. Seven exhausted singleton units were requeued, explicitly claimed, translated from their supplied article context, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| ramie (Boehmeria nivea var. candicans) | рами (бёмерия снежная, разновидность беловатая) |
| English: "game soft(ware)" | англ.: «игровое программное обеспечение» |
| Manchurian ash (Fraxinus mandshurica var. japonica), in two articles | ясень маньчжурский, разновидность японская |
| English: "hit and away" | англ.: «ударить и отойти» |
| Alpine leek (Allium victorialis var. platyphyllum) | лук победный, разновидность широколистная |
| Asian hazel (Corylus heterophylla var. thunbergii); Siberian hazel | лещина разнолистная, разновидность Тунберга; лещина сибирская |

HIST-60K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 64,617
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             530,142
~~~

HIST-60K-4 — Acceptance and coverage reported 64,617 newly accepted translations and `complete: true`, covering all 53,264 matched terms and all 61,909 frozen articles.

HIST-60K-5 — Verified output:

~~~text
articles:               61,909
ZIP members:                95
schema-validated banks:     33
SHA-256: 566507ed9ead96b130fc7785b82c58348d5dbc1df6e769dc64747fade04d9bd4
~~~

HIST-60K-6 — Run validation reported 530,142 accepted units, zero batch-membership mismatches, and zero blocking issues. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-70K — Top-70k continuation results

HIST-70K-1 — The top-70k expansion used source run 11 and target run 12.

~~~text
requested rows:          70,000
unique terms:            63,255
matched terms:           61,606
skipped terms:            1,649
selected articles:       70,864
frequency mappings:      82,516
rank-60,001–70,000 unique terms:   8,813
rank-60,001–70,000 matched terms:  8,342
rank-60,001–70,000 skipped terms:    471
new articles beyond run 11:         8,955
translation units:      589,571
articles without units:   4,462
reused run-11 units:     530,142
new Luna units:           59,429
initial batches:           1,384
~~~

HIST-70K-2 — Four pools ran at concurrency 20 with prefixes `jpdb70k-pool1` through `jpdb70k-pool4`. Automatic retries and recursive splits validated 59,426 new units. Three exhausted singleton units from one bottle-gourd article were requeued, explicitly claimed, translated with the same validated Cyrillic taxonomy used in earlier runs, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| Lagenaria siceraria var. gourda; gourd container | лагенария обыкновенная, разновидность гурда; сосуд из плода бутылочной тыквы |
| Lagenaria siceraria var. depressa | лагенария обыкновенная, разновидность приплюснутая |
| bottle gourd (Lagenaria siceraria var. hispida); calabash | бутылочная тыква (лагенария обыкновенная, разновидность щетинистая); калебаса |

HIST-70K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 59,429
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             589,571
~~~

HIST-70K-4 — Acceptance and coverage reported 59,429 newly accepted translations and `complete: true`, covering all 61,606 matched terms and all 70,864 frozen articles.

HIST-70K-5 — Verified output:

~~~text
articles:               70,864
ZIP members:               108
schema-validated banks:     36
SHA-256: dde40d8265239f69fc394c2a8b110fe3b52877ee29ee2618bd7c6b619674c07f
~~~

HIST-70K-6 — Run validation reported 589,571 accepted units, zero batch-membership mismatches, and zero blocking issues. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-80K — Top-80k continuation results

HIST-80K-1 — The top-80k expansion used source run 12 and target run 13.

~~~text
requested rows:          80,000
unique terms:            71,923
matched terms:           69,705
skipped terms:            2,218
selected articles:       79,397
frequency mappings:      92,686
rank-70,001–80,000 unique terms:   8,668
rank-70,001–80,000 matched terms:  8,099
rank-70,001–80,000 skipped terms:    569
new articles beyond run 12:         8,533
translation units:      644,744
articles without units:   5,131
reused run-12 units:     589,571
new Luna units:           55,173
initial batches:           1,317
~~~

HIST-80K-2 — Four pools ran at concurrency 20 with prefixes `jpdb80k-pool1` through `jpdb80k-pool4`. Automatic retries and recursive splits validated 55,167 new units. One expired 58-unit attempt left a Luna subprocess orphaned after a replacement attempt had already split its parent; the orphan was interrupted and audited using the stalled-worker recovery procedure, with no lost units. Six exhausted singleton units were then requeued, explicitly claimed, translated, and ingested through the standard validator. The botanical and product-name entries needed one additional fully Cyrillic retry:

| Source | Accepted Russian target |
|---|---|
| English: "one box car" | из англ. «однокорпусный автомобиль» |
| Spanish: "cha cha cha" | из исп. «ча-ча-ча» |
| English: "royal milk tea" | из англ. «королевский чай с молоком» |
| shiso (Perilla frutescens var. crispa); perilla; beefsteak plant | сисо (перилла нанкинская); перилла; перилла кустарниковая |
| Why don't you try out getting a new Windows Media Player skin and changing the player's look? | Попробуйте установить новую тему оформления для проигрывателя «Виндоус Медиа Плеер» и изменить его внешний вид. |
| English: "one-room mansion" | из англ. «однокомнатная квартира» |

HIST-80K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 55,173
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             644,744
~~~

HIST-80K-4 — Acceptance and coverage reported 55,173 newly accepted translations and `complete: true`, covering all 69,705 matched terms and all 79,397 frozen articles.

HIST-80K-5 — Verified output:

~~~text
articles:               79,397
ZIP members:               123
schema-validated banks:     39
SHA-256: 1ff1147705bcb5f39bc2906c1b7f7669d33204cad344b5fe193d67c1370b5daa
~~~

HIST-80K-6 — Run validation reported 644,744 accepted units, zero batch-membership mismatches, and zero blocking issues. The acceptance audit found exactly one accepted translation per unit and no active, unresolved, or leaf-blocked work. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-90K — Top-90k continuation results

HIST-90K-1 — The top-90k expansion used source run 13 and target run 14.

~~~text
requested rows:          90,000
unique terms:            80,556
matched terms:           77,688
skipped terms:            2,868
selected articles:       87,872
frequency mappings:     102,919
rank-80,001–90,000 unique terms:   8,633
rank-80,001–90,000 matched terms:  7,983
rank-80,001–90,000 skipped terms:    650
new articles beyond run 13:         8,475
translation units:      698,173
articles without units:   5,867
reused run-13 units:     644,744
new Luna units:           53,429
initial batches:           1,297
~~~

HIST-90K-2 — Four pools ran at concurrency 20 with prefixes `jpdb90k-pool1` through `jpdb90k-pool4`. Automatic retries and recursive splits validated 53,425 new units. Four exhausted singleton units were requeued, explicitly claimed, translated with fully Cyrillic botanical naming where necessary, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| See also | См. также |
| ramie (Boehmeria nivea var. candicans) | рами (бемерия снежная, разновидность беловатая) |
| ramie (Boehmeria nivea var. nipononivea) | рами (бемерия снежная, разновидность японская) |
| English: "plus minus zero" | из англ. «плюс-минус ноль» |

HIST-90K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 53,429
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             698,173
~~~

HIST-90K-4 — Acceptance and coverage reported 53,429 newly accepted translations and `complete: true`, covering all 77,688 matched terms and all 87,872 frozen articles.

HIST-90K-5 — Verified output:

~~~text
articles:               87,872
ZIP members:               140
schema-validated banks:     42
SHA-256: e660db82617968ebc6e4ef63361609c9b34e9fab26c8c441cc5ce3834d7001c2
~~~

HIST-90K-6 — Run validation reported 698,173 accepted units, zero batch-membership mismatches, and zero blocking issues. The acceptance audit found exactly one accepted translation per unit and no active, unresolved, or leaf-blocked work. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-100K — Top-100k continuation results

HIST-100K-1 — The top-100k expansion used source run 14 and target run 15.

~~~text
requested rows:         100,000
unique terms:            89,142
matched terms:           85,514
skipped terms:            3,628
selected articles:       96,081
frequency mappings:     112,833
rank-90,001–100,000 unique terms:   8,586
rank-90,001–100,000 matched terms:  7,826
rank-90,001–100,000 skipped terms:    760
new articles beyond run 14:          8,209
translation units:      748,745
articles without units:   6,504
reused run-14 units:     698,173
new Luna units:           50,572
initial batches:           1,269
~~~

HIST-100K-2 — Four pools ran at concurrency 20 with prefixes `jpdb100k-pool1` through `jpdb100k-pool4`. Automatic retries and recursive splits validated 50,566 new units. Six exhausted singleton units were requeued, explicitly claimed, translated with fully Cyrillic taxonomy and etymology where necessary, and ingested through the standard validator:

| Source | Accepted Russian target |
|---|---|
| word usually written using kana alone | слово обычно записывается только каной |
| See also | См. также |
| Elatostema umbellatum var. majus (variety of plant related to the nettles) | элатостема зонтичная, разновидность крупная (растение, родственное крапиве) |
| English: "doctor heli(copter)" | из англ. «врачебный вертолёт» |
| English: "body buil(ding)" | из англ. «построение тела» |
| Elatostema umbellatum var. majus (glossary set) | элатостема зонтичная, разновидность крупная (растение, родственное крапиве) |

HIST-100K-3 — Final translation state before acceptance:

~~~text
deterministic_validated units: 50,572
active batches:                     0
unresolved errors:                  0
leaf-blocked batches:               0
translated units:             748,745
~~~

HIST-100K-4 — Acceptance and coverage reported 50,572 newly accepted translations and `complete: true`, covering all 85,514 matched terms and all 96,081 frozen articles. The remaining 3,628 unique JPDB terms were absent from Jitendex and therefore correctly skipped.

HIST-100K-5 — Verified output:

~~~text
articles:               96,081
ZIP members:               168
schema-validated banks:     45
SHA-256: fe6024a2fa0ef1a1a589ec7ce94700f1810da40fb0c0049d9919c1a036cda622
~~~

HIST-100K-6 — Run validation reported 748,745 accepted units, zero batch-membership mismatches, and zero blocking issues. The acceptance audit found exactly one accepted translation per unit and no active, unresolved, or leaf-blocked work. All 52 tests passed. `release_ready` remains false only because the optional review stage was intentionally omitted.

## HIST-110K — Top-110k continuation results

HIST-110K-1 — The top-110k expansion used source run 15 and target run 16.

~~~text
requested rows:          110,000
unique JPDB terms:        97,649
matched terms:            93,207
skipped terms:             4,442
selected articles:       104,178
translation units:       797,418
reused run-15 units:     748,745
new Luna units:           48,673
~~~

HIST-110K-2 — All 48,673 new units were accepted. Coverage was complete, with zero blocking issues or batch-membership mismatches.

HIST-110K-3 — Verified output:

~~~text
articles:               104,178
ZIP members:                182
schema-validated banks:      48
SHA-256: 1543ee99de2a78739988c178a1d212605caf61ab1e01827ed1a6205f02b6609c
~~~

## HIST-120K — Top-120k continuation results

HIST-120K-1 — The top-120k expansion used source run 16 and target run 17.

~~~text
requested rows:          120,000
unique JPDB terms:       106,281
matched terms:           100,659
skipped terms:             5,622
selected articles:       111,995
translation units:       844,446
reused run-16 units:     797,418
new Luna units:           47,028
~~~

HIST-120K-2 — All 47,028 new units were accepted. Coverage was complete, with zero blocking issues or batch-membership mismatches.

HIST-120K-3 — Verified output:

~~~text
articles:               111,995
ZIP members:                190
schema-validated banks:      50
SHA-256: 1a2395c97876949f30a3d6771d0147645dca191af0860d3cd85684c3ec4b273d
~~~

## HIST-130K — Top-130k continuation results

HIST-130K-1 — The top-130k expansion used source run 17 and target run 18.

~~~text
requested rows:          130,000
unique JPDB terms:       114,817
matched terms:           108,134
skipped terms:             6,683
selected articles:       119,811
translation units:       890,684
reused run-17 units:     844,446
new Luna units:           46,238
~~~

HIST-130K-2 — All 46,238 new units were accepted. Coverage was complete, with zero blocking issues or batch-membership mismatches.

HIST-130K-3 — Verified output:

~~~text
articles:               119,811
ZIP members:                201
schema-validated banks:      53
SHA-256: 42a2b0da404cac0d881f85f446ea429cb4c8f4beafcc18d0dc363028ddf6f806
~~~

## HIST-140K — Top-140k continuation results

HIST-140K-1 — The top-140k expansion used source run 18 and target run 19.

~~~text
requested rows:          140,000
unique JPDB terms:       123,386
matched terms:           115,077
skipped terms:             8,309
selected articles:       126,943
frequency mappings:      151,297
translation units:       932,516
reused run-18 units:     890,684
new Luna units:           41,832
~~~

HIST-140K-2 — All 41,832 new units were accepted. The final database audit found exactly one accepted translation per unit, zero missing mapped articles, zero active or terminal-blocked batches, and zero unresolved validation errors. Coverage was complete. All 57 tests passed.

HIST-140K-3 — Verified output:

~~~text
articles:               126,943
ZIP members:                209
schema-validated banks:      55
SHA-256: 73c13b9df09a24dd2af35763ff40aa29d515003131723208030e1ac972d91007
~~~

HIST-140K-4 — At completion, run 19 became the recorded stopping point and top 150k became the next target.

## HIST-150K — Top-150k v4 continuation results

HIST-150K-1 — The top-150k expansion used source run 19 and target run 20. It started the Luna v4 sequence with `config.luna.toml`, `gpt-5.6-luna`, medium effort, the bundled CLI transport, and prompt SHA-256 `fbd0e0c92914b4654b8ae8aaa7063b893d7d02eb8caf6b10cb015c72beb0c9b5`.

~~~text
requested rows:          150,000
unique JPDB terms:       131,626
matched terms:           122,365
skipped terms:             9,261
selected articles:       134,250
translation units:       974,564
reused run-19 units:     932,516
new Luna units:           42,048
~~~

HIST-150K-2 — All 42,048 new units were accepted. Coverage was complete and the archive passed schema and member verification.

~~~text
ZIP members:                217
schema-validated banks:      57
SHA-256: beda96a529678131ff4159313670d90be8ff4986d35742d7c363ea02427e5bfa
~~~

## HIST-160K — Top-160k v4 continuation results

HIST-160K-1 — The top-160k expansion used source run 20 and target run 21.

~~~text
requested rows:          160,000
unique JPDB terms:       139,734
matched terms:           129,629
skipped terms:            10,105
selected articles:       141,450
translation units:     1,015,516
reused run-20 units:     974,564
new Luna units:           40,952
~~~

HIST-160K-2 — All 40,952 new units were accepted. Coverage was complete and the archive passed schema and member verification.

~~~text
ZIP members:                230
schema-validated banks:      60
SHA-256: 498ece41deafb92ff329e344d4d85b1061d7ac8ef92ae72dc46ec5b6fc34b20e
~~~

HIST-160K-3 — `build_dictionary.py` was changed from repeated whole-chunk serialization to exact incremental byte accounting. The resulting chunk boundaries remained exact, while cumulative build time dropped from tens of minutes to about one minute.

## HIST-170K — Top-170k v4 continuation results

HIST-170K-1 — The top-170k expansion used source run 21 and target run 22.

~~~text
requested rows:          170,000
unique JPDB terms:       147,740
matched terms:           136,755
skipped terms:            10,985
selected articles:       148,145
translation units:     1,054,088
reused run-21 units:   1,015,516
new Luna units:           38,572
~~~

HIST-170K-2 — All 38,572 new units were accepted. Coverage was complete and the archive passed schema and member verification.

~~~text
ZIP members:                234
schema-validated banks:      62
SHA-256: 9c348da6579f28fa6dcef8b39852e5e827e5095a0d561ce0622ac221cc8b9c24
~~~

## HIST-180K — Top-180k v4 continuation results

HIST-180K-1 — The top-180k expansion used source run 22 and target run 23.

~~~text
requested rows:          180,000
unique JPDB terms:       155,696
matched terms:           143,796
skipped terms:            11,900
selected articles:       154,675
translation units:     1,091,168
reused run-22 units:   1,054,088
new Luna units:           37,080
~~~

HIST-180K-2 — A scientific-name detector incorrectly protected ordinary phrases such as `Japanese paper`, `Morse code`, and `Kamakura and`. The detector was narrowed to conservative parenthesized/comma taxa and repeated genera. Thirty-six already valid Luna singleton responses were revalidated under the corrected deterministic rule with explicit audit records.

HIST-180K-3 — One language-origin note needed the exact citation `Vienna waltz`. A one-unit child manifest exposed that citation as a protected token to Luna. Extraction now places keyboard chords, language-origin citations, and conservative cross-reference taxa in model-visible protected tokens as well as validator checks.

HIST-180K-4 — All 37,080 new units were accepted, all 143,796 matched headwords were covered, and the archive passed schema and member verification.

~~~text
ZIP members:                243
schema-validated banks:      64
SHA-256: 696af0c8d5e8a7086d8e70f9e3605da31e7eba2cbb4ac4556e22a3c1bdb19940
~~~

## HIST-190K — Top-190k v4 continuation results

HIST-190K-1 — The top-190k expansion used source run 23 and target run 24. The subset gate proved that all 1,091,168 source units had exact target identities before reuse.

~~~text
requested rows:          190,000
unique JPDB terms:       163,558
matched terms:           150,697
skipped terms:            12,861
selected articles:       160,870
translation units:     1,126,413
reused run-23 units:   1,091,168
new Luna units:           35,245
initial batches:              959
~~~

HIST-190K-2 — Four CLI pools ran at concurrency 20. One runner exited after a transient SQLite lock while claiming more work. Process absence was verified, its 18 claims were marked interrupted, and 699 units were requeued with audit events. A fresh recovery pool accepted all 18 batches.

HIST-190K-3 — Retry-tail regressions now allow an exact source acronym such as `ETD` when the same glossary set also contains Russian wording. They also allow a narrow set of English grammar tokens such as `this`, `that`, and `which` inside an otherwise Russian translated example. Tests reject an English prose fragment that merely adds a Cyrillic word.

HIST-190K-4 — All 35,245 new units were accepted. Coverage was complete for all 150,697 matched headwords and 160,870 articles. The 12,861 absent JPDB terms were correctly skipped. The final audit found 1,126,413 accepted units, zero membership mismatches, zero blocking issues, zero unresolved errors, and only `codex-agent` Luna-medium attempts. All 61 tests passed.

~~~text
ZIP members:                247
schema-validated banks:      65
SHA-256: 8d632ea386915467eb6813b9c3aeba6bd665dbb6af2142ff247a2c607f6ae2e0
~~~

HIST-190K-5 — Run 24 is the stopping point recorded in the operational [JPDB_LUNA_ORCHESTRATION_RUNBOOK.md](JPDB_LUNA_ORCHESTRATION_RUNBOOK.md). The next cumulative target is top 200k.

## HIST-EXPORT-LIMIT — Historical exporter metadata limitation

HIST-EXPORT-LIMIT-1 — Through run 24, `src/jitendex_ru/build_dictionary.py` hardcoded these values for every frequency-scoped run:

~~~text
title:       Jitendex JPDB 5k — русский
revision:    ...-jpdb-5k-ru
description: ...верхним 5000 строкам JPDB...
~~~

HIST-EXPORT-LIMIT-2 — The top-10k through top-190k archives contain the correct cumulative articles and pass coverage/schema verification, but their internal titles and descriptions still say 5k.

HIST-EXPORT-LIMIT-3 — Schema version 7 added explicit `frequency_source` metadata and term-keyed frequency provenance. The exporter now derives labels from active frequency sources that map into the frozen run. This resolves the limitation for new exports without changing historical ZIP files or hashes.

HIST-EXPORT-LIMIT-4 — Historical top-10k through top-190k archives keep their original internal metadata. Their recorded hashes remain authoritative and must not be rewritten.

## HIST-FREQ40K — One-off six-list top-40k supplement

HIST-FREQ40K-1 — Run 25 kept the verified JPDB top-190k scope from run 24 and added every Jitendex article matched by the top-40k scope of Aozora Bunko, BCCWJ, CC100, Monodicts 206k, Wikipedia v2, or 国語辞典. This was a one-off combined export and did not advance the normal JPDB stopping point.

~~~text
six-list union terms:             103,536
combined union terms:             200,059
combined matched terms:           162,815
combined skipped terms:            37,244
selected articles:                173,253
translation units:              1,199,359
reused run-24 units:            1,126,413
new Luna units:                    72,946
initial batches:                    1,766
articles without units:            14,287
~~~

HIST-FREQ40K-2 — The pinned source SHA-256 values were:

~~~text
JPDB:           5bda39a9e3b443b02199435ea723aa0555c891d1ce2c92ea7680163b72b07a0e
Aozora Bunko:   116009c3034d97a16b257fda10f2138067815986c954bffbb5c93aad60faa867
BCCWJ:          4a0f79b88b3934d2cfca6ec1018c0658c7af6e27bf5eaa371db554e0cb3c1693
CC100:          64f2a7d79e42dc842a30697e36f8b4f77dbcb3c6ff7fd1feec756b1fe65396e0
Monodicts 206k: 932a65a0661c1c040b471a03aa9e11eb20a2ab6f42b62ffbf1dab568db7d8b39
Wikipedia v2:   d85bb5bb4cdb3277dd862d662ec5e8e87971457ac53a87c2f25b41446c57d6c8
国語辞典:        ac267dd5756363fd2b9d0bfd64f89d86d2b9c90f833f3273375173791080c32c
~~~

HIST-FREQ40K-3 — Per-source unique, matched, and skipped term counts were Aozora Bunko 40,156 / 34,536 / 5,620; BCCWJ 38,898 / 28,720 / 10,178; CC100 39,319 / 36,999 / 2,320; Monodicts 206k 40,000 / 37,045 / 2,955; Wikipedia v2 40,000 / 29,068 / 10,932; 国語辞典 40,000 / 36,833 / 3,167; and JPDB 163,558 / 150,697 / 12,861.

HIST-FREQ40K-4 — Four Luna-medium CLI pools ran at concurrency 20. One pool exited after a transient SQLite lock with five active claims covering 138 tail units. Process absence was verified, those exact attempts were marked interrupted, the five batches were requeued with `recover_interrupted_worker` audit events, and a replacement pool completed them through normal validation and recursive splitting.

HIST-FREQ40K-5 — All 72,946 new units were accepted. The final audit found 1,199,359 units with exactly one accepted translation, zero missing run-24 articles or units, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. Combined coverage was complete for all 162,815 matched terms and all 173,253 selected articles. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                263
schema-validated banks:      70
archive articles:       173,253
SHA-256: e895ac97b81a46a1350b7e5dc346538b6305b33e301065d4bd19dfcf218cc83b
~~~

HIST-FREQ40K-6 — Export 31 was verified with the title `Jitendex JPDB 190k + frequency-six top40k — русский` and revision `2026.07.09.0-jpdb-190k-freq6-40k-ru`. The archive is `dist/jitendex-jpdb-190k-plus-freq6-top40k-ru-luna-v4.zip`.

HIST-FREQ40K-7 — Normal JPDB progression resumes from run 24 at top 200k. Run 24 remains the primary reuse and containment source. Run 25 may be used only as a second reuse source for overlay translations that enter JPDB top 200k; overlay-only articles must not enter that export.

## HIST-200K — Top-200k v4 continuation results

HIST-200K-1 — The top-200k expansion used source run 24 and target run 26. The subset gate found zero source units missing from the target. Run 25 then supplied 5,365 additional exact reusable translations only for articles that independently entered JPDB top 200k.

~~~text
requested rows:          200,000
unique JPDB terms:       171,362
matched terms:           157,505
skipped terms:            13,857
selected articles:       166,665
translation units:     1,159,255
reused run-24 units:   1,126,413
reused run-25 units:       5,365
new Luna units:           27,477
initial batches:              760
~~~

HIST-200K-2 — Four Luna-medium CLI pools ran at concurrency 20. Automatic retries and recursive splits drained all but one singleton. That singleton translated `hepatica (Hepatica nobilis var. japonica f. variegata); liverleaf` correctly but copied too many unprotected Latin taxon qualifiers. An audited targeted repair preserved the protected `Hepatica nobilis` binomial and rendered the qualifiers in Russian through normal claim and ingestion provenance.

HIST-200K-3 — All 27,477 new units were accepted. Coverage was complete for all 157,505 matched headwords and 166,665 articles. The final audit found 1,159,255 units with exactly one accepted translation, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                255
schema-validated banks:      67
archive articles:       166,665
SHA-256: 1fe24f33db2b27d6c7b5f1125018763a1db297d78620963d8ea218bbf2a87ba0
~~~

HIST-200K-4 — Export 32 was verified with the title `Jitendex JPDB 200k — русский` and revision `2026.07.09.0-jpdb-200k-ru`. Run 26 is the new stopping point and top 210k is the next cumulative target.

## HIST-210K — Top-210k v4 continuation results

HIST-210K-1 — The top-210k expansion used source run 26 and target run 27. The subset gate found zero source units missing from the target.

~~~text
requested rows:          210,000
unique JPDB terms:       179,076
matched terms:           164,189
skipped terms:            14,887
selected articles:       172,303
translation units:     1,191,493
reused run-26 units:   1,159,255
new Luna units:           32,238
initial batches:              881
~~~

HIST-210K-2 — Automatic retries and recursive splits drained all but two singleton leaves. One moth glossary retained unprotected Latin taxonomy, and one hepatica cross-reference copied unprotected Latin qualifiers. Audited targeted repairs rendered those unprotected parts in Russian, preserved the required protected binomial, and passed normal claim and ingestion provenance.

HIST-210K-3 — All 32,238 new units were accepted. Coverage was complete for all 164,189 matched headwords and 172,303 articles. The final audit found 1,191,493 units with exactly one accepted translation, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

HIST-210K-4 — The selector requested rows 1–210,000. The maximum retained deduplicated term rank was 209,999 because row 210,000 repeated an earlier spelling.

~~~text
ZIP members:                261
schema-validated banks:      69
archive articles:       172,303
SHA-256: 7f51c79783c7c2fc5a2038eef3c74c75bf80e45448003f3318da9cf28b1d1c7a
~~~

HIST-210K-5 — Export 33 was verified with the title `Jitendex JPDB 210k — русский` and revision `2026.07.09.0-jpdb-210k-ru`. Run 27 is the new stopping point and top 220k is the next cumulative target.

## HIST-220K — Top-220k v4 continuation results

HIST-220K-1 — The top-220k expansion used source run 27 and target run 28. The subset gate found zero source units missing from the target.

~~~text
requested rows:          220,000
unique JPDB terms:       186,762
matched terms:           170,865
skipped terms:            15,897
selected articles:       178,001
translation units:     1,223,419
reused run-27 units:   1,191,493
new Luna units:           31,926
initial batches:              881
~~~

HIST-220K-2 — One pool exited after a transient failure. Process absence was verified, its exact 15 claims were marked interrupted, requeued, and audited, and a replacement pool completed them. Automatic retries and recursive splits drained all but one singleton. That Java-man cross-reference retained an unprotected Latin taxon; an audited targeted repair rendered the taxon in Russian through normal claim and ingestion provenance.

HIST-220K-3 — All 31,926 new units were accepted. Coverage was complete for all 170,865 matched headwords and 178,001 articles. The final audit found 1,223,419 units with exactly one accepted translation, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

HIST-220K-4 — The selector requested rows 1–220,000. The maximum retained deduplicated term rank was 219,999 because row 220,000 repeated an earlier spelling.

~~~text
ZIP members:                265
schema-validated banks:      71
archive articles:       178,001
SHA-256: 8245e88c24cba5e7eb0e8671b3be1089cd4ff654fa9f76f2d5168640affbd54e
~~~

HIST-220K-5 — Export 34 was verified with the title `Jitendex JPDB 220k — русский` and revision `2026.07.09.0-jpdb-220k-ru`. Run 28 is the new stopping point and top 230k is the next cumulative target.

## HIST-230K — Top-230k v4 continuation results

HIST-230K-1 — The top-230k expansion used source run 28 and target run 29. The subset gate found zero source units missing from the target.

~~~text
requested rows:          230,000
unique JPDB terms:       196,418
matched terms:           180,195
skipped terms:            16,223
selected articles:       186,233
translation units:     1,267,553
reused run-28 units:   1,223,419
new Luna units:           44,134
initial batches:            1,309
~~~

HIST-230K-2 — Automatic retries and recursive splits drained all but two singleton leaves. A language-origin note retained multiple unprotected ASCII forms, and a Shift-JIS glossary retained multiple unprotected encoding-name words. Audited targeted repairs transliterated those unprotected forms in Russian, preserved the required `0208` token, and passed normal claim and ingestion provenance.

HIST-230K-3 — All 44,134 new units were accepted. Coverage was complete for all 180,195 matched headwords and 186,233 articles. The final audit found 1,267,553 units with exactly one accepted translation, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                268
schema-validated banks:      73
archive articles:       186,233
SHA-256: 889d11740c7e2c985a644d59f4ea72420a8c618bdb65e736e7f45813b8f36f33
~~~

HIST-230K-4 — Export 35 was verified with the title `Jitendex JPDB 230k — русский` and revision `2026.07.09.0-jpdb-230k-ru`. Run 29 is the new stopping point and top 240k is the next cumulative target.

## HIST-240K — Top-240k v4 continuation results

HIST-240K-1 — The top-240k expansion used source run 29 and target run 30. The subset gate found zero source units missing from the target.

~~~text
requested rows:          240,000
unique JPDB terms:       205,884
matched terms:           189,546
skipped terms:            16,338
selected articles:       190,184
translation units:     1,286,891
reused run-29 units:   1,267,553
new Luna units:           19,338
initial batches:              605
~~~

HIST-240K-2 — One pool exited after a SQLite lock. Process absence was verified, its exact 19 claims covering 651 units were marked interrupted, requeued, and audited, and a replacement pool completed them. Automatic retries and recursive splits drained every unit without targeted repair.

HIST-240K-3 — All 19,338 new units were accepted. Coverage was complete for all 189,546 matched headwords and 190,184 articles. The final audit found 1,286,891 units with exactly one accepted translation, zero missing mapped articles, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                271
schema-validated banks:      74
archive articles:       190,184
SHA-256: 92f7703efd152c7ae156bef96fdf61b9ecf8c21e8ce54ab0fc10ebf2ec09f4d3
~~~

HIST-240K-4 — Export 36 was verified with the title `Jitendex JPDB 240k — русский` and revision `2026.07.09.0-jpdb-240k-ru`. Run 30 is the new stopping point and top 250k is the next cumulative target.

## HIST-250K — Top-250k v4 continuation results

HIST-250K-1 — The top-250k expansion used source run 30 and target run 31. The subset gate found zero source units missing from the target.

~~~text
requested rows:          250,000
unique JPDB terms:       215,398
matched terms:           198,900
skipped terms:            16,498
selected articles:       193,874
translation units:     1,305,309
reused run-30 units:   1,286,891
new Luna units:           18,418
initial batches:              568
~~~

HIST-250K-2 — Automatic retries and recursive splits drained all but one singleton leaf. Its glossary described ASCII transfer with XON-XOFF flow control, and repeated Luna responses retained excessive unprotected English abbreviations. An audited targeted repair transliterated the abbreviations into Russian and passed normal claim and ingestion provenance.

HIST-250K-3 — All 18,418 new units were accepted. Coverage was complete for all 198,900 matched headwords and 193,874 articles. The final audit found 1,305,309 units with exactly one accepted translation, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                273
schema-validated banks:      75
archive articles:       193,874
SHA-256: 42de9a154ba6bd5b2fb85d40d6e0d77499d723411d5ed3de8eed08b75c45edba
~~~

HIST-250K-4 — Export 37 was verified with the title `Jitendex JPDB 250k — русский` and revision `2026.07.09.0-jpdb-250k-ru`. Run 31 is the new stopping point and top 260k is the next cumulative target.

## HIST-260K — Top-260k v4 continuation results

HIST-260K-1 — The top-260k expansion used source run 31 and target run 32. The subset gate found zero source units missing from the target.

~~~text
requested rows:          260,000
unique JPDB terms:       224,572
matched terms:           207,928
skipped terms:            16,644
selected articles:       197,965
translation units:     1,329,154
reused run-31 units:   1,305,309
new Luna units:           23,845
initial batches:              626
~~~

HIST-260K-2 — One pool exited after a SQLite lock. Process absence was verified, its exact 19 claims covering 731 units were marked interrupted, requeued, and audited, and a replacement pool completed them. Automatic retries and recursive splits then drained all but two singleton language-origin notes. Audited targeted repairs transliterated the Spanish, Portuguese, and English source forms into Russian and passed normal claim and ingestion provenance.

HIST-260K-3 — All 23,845 new units were accepted. Coverage was complete for all 207,928 matched headwords and 197,965 articles. The final audit found 1,329,154 units with exactly one accepted translation, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                274
schema-validated banks:      76
archive articles:       197,965
SHA-256: f394eea111e952eb1d7d05131672249618633e9080f8efaa14badd990cbf70ad
~~~

HIST-260K-4 — Export 38 was verified with the title `Jitendex JPDB 260k — русский` and revision `2026.07.09.0-jpdb-260k-ru`. Run 32 is the new stopping point and top 270k is the next cumulative target.

## HIST-270K — Top-270k v4 continuation results

HIST-270K-1 — The top-270k expansion used source run 32 and target run 33. The subset gate found zero source units missing from the target.

~~~text
requested rows:          270,000
unique JPDB terms:       234,290
matched terms:           217,572
skipped terms:            16,718
selected articles:       202,324
translation units:     1,350,884
reused run-32 units:   1,329,154
new Luna units:           21,730
initial batches:              660
~~~

HIST-270K-2 — Automatic retries and recursive splits drained every unit without targeted repair. The retry tail isolated Cyrillic and English-density failures into smaller children, which Luna then translated validly.

HIST-270K-3 — All 21,730 new units were accepted. Coverage was complete for all 217,572 matched headwords and 202,324 articles. The final audit found 1,350,884 units with exactly one accepted translation, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                278
schema-validated banks:      77
archive articles:       202,324
SHA-256: 5206d936ef4aa01f36e3841f0174888a81e755bdecd135aade0c424af2332769
~~~

HIST-270K-4 — Export 39 was verified with the title `Jitendex JPDB 270k — русский` and revision `2026.07.09.0-jpdb-270k-ru`. Run 33 is the new stopping point and top 280k is the next cumulative target.

## HIST-280K — Top-280k v4 continuation results

HIST-280K-1 — The top-280k expansion used source run 33 and target run 34. The subset gate found zero source units missing from the target.

~~~text
requested rows:          280,000
unique JPDB terms:       244,046
matched terms:           227,114
skipped terms:            16,932
selected articles:       206,485
translation units:     1,371,763
reused run-33 units:   1,350,884
new Luna units:           20,879
initial batches:              643
~~~

HIST-280K-2 — One 56-unit Luna subprocess remained silent after repeated bounded waits. Its owning pool was stopped, the exact claim was marked interrupted and audited, and a replacement worker resumed it. Recursive splitting validated all but two identical language-origin notes. Audited targeted repairs transliterated the French and English source forms into Russian and passed normal claim and ingestion provenance.

HIST-280K-3 — All 20,879 new units were accepted. Coverage was complete for all 227,114 matched headwords and 206,485 articles. The final audit found 1,371,763 units with exactly one accepted translation, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                279
schema-validated banks:      78
archive articles:       206,485
SHA-256: 5666b39dc51a9b5593a7373062033255521e60fa93a1a3b586fd7a8ee89a6b96
~~~

HIST-280K-4 — Export 40 was verified with the title `Jitendex JPDB 280k — русский` and revision `2026.07.09.0-jpdb-280k-ru`. Run 34 is the new stopping point and top 290k is the next cumulative target.

## HIST-290K — Top-290k v4 continuation results

HIST-290K-1 — The top-290k expansion used source run 34 and target run 35. The subset gate found zero source units missing from the target.

~~~text
requested rows:          290,000
unique JPDB terms:       253,819
matched terms:           236,719
skipped terms:            17,100
selected articles:       211,292
translation units:     1,393,585
reused run-34 units:   1,371,763
new Luna units:           21,822
initial batches:              728
~~~

HIST-290K-2 — One pool exited after a SQLite lock. Process absence was verified, its exact 19 claims covering 524 units were marked interrupted, requeued, and audited, and a replacement pool completed them. Automatic retries and recursive splits drained every unit without targeted repair.

HIST-290K-3 — All 21,822 new units were accepted. Coverage was complete for all 236,719 matched headwords and 211,292 articles. The final audit found 1,393,585 units with exactly one accepted translation, zero active batches, zero terminal blocked leaves, zero unresolved errors, zero blocking issues, and zero batch-membership mismatches. All 64 tests passed and SQLite `quick_check` returned `ok`.

~~~text
ZIP members:                281
schema-validated banks:      80
archive articles:       211,292
SHA-256: e9bf1a8f25e34cf6724b83152463b8457523f4a115f87e46815bb8568733326a
~~~

HIST-290K-4 — Export 41 was verified with the title `Jitendex JPDB 290k — русский` and revision `2026.07.09.0-jpdb-290k-ru`. Run 35 is the new stopping point and top 300k is the next cumulative target.

## HIST-300K — Top-300k v4 final planned JPDB results

HIST-300K-1 — The top-300k expansion used source run 35 and target run 36. The subset gate found zero source units missing from the target.

~~~text
requested rows:          300,000
unique JPDB terms:       263,571
matched terms:           246,264
skipped terms:            17,307
selected articles:       216,368
translation units:     1,421,789
reused run-35 units:   1,393,585
new Luna units:           28,204
initial batches:              774
~~~

HIST-300K-2 — Automatic retries and recursive splits drained all but one singleton example. An audited targeted repair transliterated the product and company names into Russian and passed normal claim and ingestion provenance.

HIST-300K-3 — The final deterministic canonicalizer was implemented and tested before export. It resolved 880,676 structured tag leaves by stable `(category, code, field)` identity, changed 333,092 final-run values, recorded immutable per-unit provenance, and found 547,584 values already exact. A second run changed zero values. Missing mappings, conflicting requirements, non-scalar targets, and acceptance gaps fail closed. All 67 tests passed.

HIST-300K-4 — All 28,204 new units were accepted. Coverage was complete for all 246,264 matched JPDB headwords and 216,368 selected articles. The final validation found 1,421,789 accepted units, zero blocking issues, zero membership mismatches, and zero bad target hashes.

~~~text
ZIP members:                282
schema-validated banks:      81
archive articles:       216,368
SHA-256: 8d8944228ff3c2f64b4630a3bf85474151755b5c84e9eaa8688182de54e977b3
~~~

HIST-300K-5 — Export 42 was verified with the title `Jitendex JPDB 300k — русский` and revision `2026.07.09.0-jpdb-300k-ru`.

HIST-300K-6 — The full-snapshot audit proved top 300k is not full Jitendex coverage. The snapshot has 433,885 articles and 431,545 distinct expression-reading headwords. Run 36 covers 216,368 articles and 214,990 distinct headwords, leaving 217,517 articles and 216,555 headwords. Work therefore continues beyond JPDB ranks in cumulative 10,000-article increments.

## HIST-ALL-226368 — 226,368-article continuation results

HIST-ALL-226368-1 — The first all-article expansion used source run 36 and target run 37. It retained every source article and added the next 10,000 articles in stable source-bank order. The subset gate found zero source units missing from the target.

~~~text
selected articles:         226,368
distinct headwords done:   224,971
headwords remaining:       206,574
translation units:       1,443,831
reused run-36 units:     1,421,789
new Luna units:             22,042
initial batches:               479
~~~

HIST-ALL-226368-2 — Four pools of 20 workers completed all initial batches. Capacity, protected-token, unit-order, and Cyrillic validation failures succeeded through normal automatic retries. No manual repair or interrupted-lease recovery was required.

HIST-ALL-226368-3 — All 22,042 new units were accepted. Coverage was complete for all 226,368 selected articles. The final audit found 1,443,831 units with exactly one accepted translation, zero unresolved errors, zero blocking issues, and zero acceptance gaps. Canonicalization changed 96 new structured values; its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                284
schema-validated banks:      83
archive articles:       226,368
SHA-256: 75fe600adc4790363ac28da54a4547a42196a15e7ea4a274af4b8b62d5e6cfaa
~~~

HIST-ALL-226368-4 — Export 43 was verified with the title `Jitendex 226 368 статей — русский` and revision `2026.07.09.0-articles-226368-ru`. Run 37 is the new stopping point; 236,368 articles is the next cumulative target.

## HIST-ALL-236368 — 236,368-article continuation results

HIST-ALL-236368-1 — The expansion used source run 37 and target run 38. It retained every source article, added the next 10,000 articles in stable source-bank order, and had zero source-unit containment gaps.

~~~text
selected articles:         236,368
distinct headwords done:   234,928
headwords remaining:       196,617
translation units:       1,448,980
reused run-37 units:     1,443,831
new Luna units:              5,149
initial batches:               105
~~~

HIST-ALL-236368-2 — Two launchers exited on SQLite claim contention after taking five claims. A sixth Luna subprocess remained silent after repeated bounded waits. Process absence was proved, the exact six claims covering 330 units were marked interrupted, requeued, and audited, and a six-worker recovery pool validated all six. Automatic retries resolved three Cyrillic-validation failures without manual repair.

HIST-ALL-236368-3 — All 5,149 new units were accepted. Coverage was complete for all 236,368 selected articles. The final audit found 1,448,980 units with exactly one accepted translation, zero unresolved errors, zero blocking issues, and zero acceptance gaps. Canonicalization changed 42 new structured values; its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                286
schema-validated banks:      85
archive articles:       236,368
SHA-256: 2a4e194ca6074a65ba3ac24cbfe89ff156f49900e7054d10421f135952fce796
~~~

HIST-ALL-236368-4 — Export 44 was verified with the title `Jitendex 236 368 статей — русский` and revision `2026.07.09.0-articles-236368-ru`. Run 38 is the new stopping point; 246,368 articles is the next cumulative target.

## HIST-ALL-246368 — 246,368-article continuation results

HIST-ALL-246368-1 — The expansion used source run 38 and target run 39. It retained every source article, added the next 10,000 articles in stable source-bank order, and had zero source-unit containment gaps.

~~~text
selected articles:         246,368
distinct headwords done:   244,854
headwords remaining:       186,691
translation units:       1,456,150
reused run-38 units:     1,448,980
new Luna units:              7,170
initial batches:               141
~~~

HIST-ALL-246368-2 — Automatic retries and recursive splits isolated repeated English and Cyrillic validation failures. All child leaves validated without manual repair or interrupted-lease recovery.

HIST-ALL-246368-3 — All 7,170 new units were accepted. Coverage was complete for all 246,368 selected articles. The final audit found 1,456,150 units with exactly one accepted translation, zero unresolved errors, zero blocking issues, and zero acceptance gaps. Canonicalization changed 62 new structured values; its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                287
schema-validated banks:      86
archive articles:       246,368
SHA-256: df78d515eaa31922e1b2bf9768ecbdf1e2d2905824c88903869a1a89c3b78e4c
~~~

HIST-ALL-246368-4 — Export 45 was verified with the title `Jitendex 246 368 статей — русский` and revision `2026.07.09.0-articles-246368-ru`. Run 39 is the new stopping point; 256,368 articles is the next cumulative target.

## HIST-ALL-256368 — 256,368-article continuation results

HIST-ALL-256368-1 — Run 40 retained run 39, added 10,000 source-ordered articles, reused 1,456,150 units, and translated 7,630 new units in 156 initial batches. It finished with 254,799 distinct headwords done and 176,746 remaining.

HIST-ALL-256368-2 — A temporary Luna capacity and WebSocket 403 disruption affected all pools. HTTPS fallback and normal retries recovered without stale claims or manual repairs. Recursive splits resolved the remaining Cyrillic failures.

HIST-ALL-256368-3 — All 1,463,780 units were accepted with zero gaps, unresolved errors, or blocking issues. Canonicalization changed 52 values and its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                289
schema-validated banks:      88
archive articles:       256,368
SHA-256: 1d03ffd1f2a3aec8ec6d7e12bae75f4e3b2470586e8f0d9e7a4a203384e0b584
~~~

HIST-ALL-256368-4 — Export 46 was verified. Run 40 is the stopping point; 266,368 articles is next.

## HIST-ALL-266368 — 266,368-article continuation results

HIST-ALL-266368-1 — Run 41 retained run 40, added 10,000 source-ordered articles, reused 1,463,780 units, and translated 6,430 new units in 142 batches. It finished with 264,763 distinct headwords done and 166,782 remaining.

HIST-ALL-266368-2 — Intermittent capacity and WebSocket failures recovered through normal retries. All 1,470,210 units were accepted with zero gaps, unresolved errors, or blocking issues. Canonicalization changed 57 values and its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                291
schema-validated banks:      90
archive articles:       266,368
SHA-256: fa3d69575df08863d15922c56426edeb892b13a526a7291441072f1ad32a5874
~~~

HIST-ALL-266368-3 — Export 47 was verified. Run 41 is the stopping point; 276,368 articles is next.

## HIST-ALL-276368 — 276,368-article continuation results

HIST-ALL-276368-1 — Run 42 retained run 41, added 10,000 source-ordered articles, reused 1,470,210 units, and translated 10,316 new units in 212 initial batches. It finished with 274,732 distinct headwords done and 156,813 remaining.

HIST-ALL-276368-2 — Two launchers stopped after SQLite claim contention and one Luna subprocess remained silent. Process absence was proved. The exact 20 stale claims covering 904 units were marked interrupted, requeued, and audited. A recovery pool validated every requeued unit. Two response-order mismatches passed normal retries.

HIST-ALL-276368-3 — All 1,480,526 units were accepted with zero gaps, unresolved errors, or blocking leaf issues. Canonicalization changed 107 values and its idempotence pass changed zero. All 69 tests passed.

~~~text
ZIP members:                293
schema-validated banks:      92
archive articles:       276,368
SHA-256: 9e00391edb33b54b3611ccb18054f3a3c619e0c4be9a5340afb7274950637046
~~~

HIST-ALL-276368-4 — Export 49 was verified after canonicalization. Run 42 is the stopping point; 286,368 articles is next.

## HIST-ALL-286368 — 286,368-article continuation results

HIST-ALL-286368-1 — Run 43 retained run 42, added 10,000 source-ordered articles, reused 1,480,526 units, and translated 24,829 new units in 573 initial batches. It finished with 284,680 distinct headwords done and 146,865 remaining.

HIST-ALL-286368-2 — Reuse and initial batch creation accidentally overlapped, producing unclaimed manifests for reused units. Before any model attempt, those Run 43-only batch records were removed, the repair was audited, and 573 correct batches were regenerated from exactly the 24,829 ready units. Normal retries and recursive splits resolved English, Cyrillic, and protected-token validation failures.

HIST-ALL-286368-3 — All 1,505,355 units were accepted with zero gaps, unresolved errors, or blocking leaf issues. Canonicalization changed 145 values and its idempotence pass changed zero. The build gate exposed one scalar whose source contained leading whitespace stripped during extraction. Application now verifies stripped scalar provenance while preserving exact source whitespace around the Russian replacement. All 70 tests passed.

~~~text
ZIP members:                297
schema-validated banks:      94
archive articles:       286,368
SHA-256: cabdb40310e6599b7c39638865cf2afbb5201bd1c17f6c549cd4496b32fb1fee
~~~

HIST-ALL-286368-4 — Export 50 was verified. Run 43 is the stopping point; 296,368 articles is next.

## HIST-ALL-296368 — 296,368-article continuation results

HIST-ALL-296368-1 — Run 44 retained run 43, added 10,000 source-ordered articles, reused 1,505,355 units, and translated 35,712 new units in 880 initial batches. It finished with 294,630 distinct headwords done and 136,915 remaining.

HIST-ALL-296368-2 — Several pool launchers exited on SQLite contention. After all live workers drained, exact stale claims were proved process-absent, marked interrupted, audited, and requeued. A recovery pool validated 847 requeued units. Eight final singleton leaves from four related articles repeatedly failed because required source acronyms `SV`, `SVC`, `SVO`, `SVOO`, and `SVOC` were counted as untranslated English. The validator now requires source acronyms but excludes them from its English quota. All eight leaves then validated without changing their Russian translations.

HIST-ALL-296368-3 — All 1,541,067 units were accepted with zero gaps, unresolved errors, or blocking leaf issues. Canonicalization changed 102 values and its idempotence pass changed zero. All 71 tests passed.

~~~text
ZIP members:                301
schema-validated banks:      96
archive articles:       296,368
SHA-256: fa30ecf36ca420c90168b8cd7028364405cbb153564dfacfabdb19f4e81c7861
~~~

HIST-ALL-296368-4 — Export 51 was verified. Run 44 is the stopping point; 306,368 articles is next.

## HIST-ALL-306368 — 306,368-article continuation results

HIST-ALL-306368-1 — Run 45 retained run 44, added 10,000 source-ordered articles, reused 1,541,067 units, and translated 47,579 new units. It finished with 304,239 distinct headwords done and 127,306 remaining. PostgreSQL was the authoritative database for the resumed work.

HIST-ALL-306368-2 — The paused run had 110 exact expired leases. Process absence and lease-token identity were proved before one atomic audited recovery returned them to the queue. The PostgreSQL runner then drained all work at concurrency 80 without database retries, transport failures, timeouts, or rate-limit failures.

HIST-ALL-306368-3 — Recursive isolation left 185 terminal singleton leaves. Their validator-required source acronyms were missing from the model manifests. Audited corrected-manifest children exposed those protected tokens to Luna and all 185 leaves validated through normal claim and ingestion provenance. The repair pass submitted 188 attempts, including three automatic validation retries, and completed at 148.8 headwords per minute.

HIST-ALL-306368-4 — All 1,588,646 units were accepted with zero gaps, unresolved errors, active batches, terminal leaves, or membership mismatches. Coverage was complete for all 246,264 matched JPDB terms and all 306,368 selected articles. Canonicalization changed 14 values. All 86 tests passed.

~~~text
ZIP members:                304
schema-validated banks:      99
archive articles:       306,368
SHA-256: 9dd8e4c0569a6ab419fe89fcddcc20a831f12d7aad386c159ad19990d31bf072
~~~

HIST-ALL-306368-5 — Export 52 was verified. Run 45 is the stopping point; 316,368 articles is next.

## HIST-ALL-316368 — 316,368-article continuation results

HIST-ALL-316368-1 — Run 46 retained run 45, added 10,000 source-ordered articles, reused 1,588,646 units, and translated 25,305 new units. All work used authoritative PostgreSQL.

HIST-ALL-316368-2 — Productive fixed windows measured 275.9 headwords per minute at concurrency 70, 300.0 at 80, and 311.9 at 90. Concurrency 70 was 8.0% slower than 80. Concurrency 90 was only 4.0% faster than 80 and did not pass the 5% increase gate.

HIST-ALL-316368-3 — Acceptance initially selected a bad PostgreSQL nested-loop plan and exceeded 15 minutes. Removing a redundant run correlation let the existing globally unique unit index complete acceptance in 3.3 seconds. Returning article JSON once per article reduced canonicalization from over 13 minutes to 21.1 seconds. Bulk export replaced about 948,000 small reads and completed in 75.6 seconds. The changes preserve hashes, structural checks, and SQLite behavior.

HIST-ALL-316368-4 — All 1,613,951 units were accepted with zero gaps, active batches, terminal leaves, unresolved errors, or membership mismatches. Canonicalization changed 119 values and its idempotence pass changed zero.

~~~text
ZIP members:                306
schema-validated banks:      101
archive articles:       316,368
SHA-256: e024fa042d3cc516e22935e882e47a2359dddd77a4dafe47f731a43e520f98f5
~~~

HIST-ALL-316368-5 — Export 53 was verified. A PostgreSQL custom-format backup before the following scope expansion is `work/backups/jitendex-postgresql-before-326368.dump`, SHA-256 `7f6d924381006932f969b84d8b08ce8141840b9684d23e91619282c18dc66a39`.

## HIST-ALL-326368 — 326,368-article continuation results

HIST-ALL-326368-1 — Run 47 retained run 46, added 10,000 source-ordered articles, reused 1,613,951 units, and translated 41,539 new units from 1,268 initial batches.

HIST-ALL-326368-2 — Productive window `run47-c100-1` measured 346.0 headwords and 1,855.9 units per minute for 90.006 seconds. It completed 88 measured requests with one validation retry, p50 69.0 seconds, p95 95.6 seconds, p99 103.5 seconds, 6.7% database duty, and 199 MB peak runner memory. It had zero rate limits, timeouts, transport failures, database retries, collisions, stale leases, missing units, or duplicate translations. Concurrency 100 beat 80 by 15.3% and 90 by 10.9%, so it passed the 5% gate.

HIST-ALL-326368-3 — The final concurrency-100 drain took 936.2 seconds and submitted 1,118 attempts. It recorded 19 validation rejections, 16 request timeouts, 33 retries, and two splits. Exact leases were requeued automatically. Two transient rate-limit classifications came from timed-out service output. The drain ended with zero active work, terminal leaves, unresolved errors, transport failures, or database retries.

HIST-ALL-326368-4 — Stale PostgreSQL statistics first made scope selection exceed three minutes. `ANALYZE` reduced the unchanged deterministic query to 13.7 seconds. Long row-wise unit extraction remains a preparation optimization opportunity; it did not affect translation correctness.

HIST-ALL-326368-5 — All 1,655,490 units were accepted with zero gaps, active batches, terminal leaves, unresolved errors, or membership mismatches. Canonicalization changed 100 values and its idempotence pass changed zero. All 113 tests passed normally and with the disposable PostgreSQL recovery database enabled.

~~~text
ZIP members:                314
schema-validated banks:      103
archive articles:       326,368
SHA-256: 8fc3962d23025ac807d84a4bc8e2d21e8ab8a6d1335201aa0c374a0c542acd0d
~~~

HIST-ALL-326368-6 — Export 54 was verified. Run 47 is the stopping point. Concurrency 100 is the proven setting for a future authorized run; do not create Run 48 or test 110 without new authorization.

## HIST-ALL-336368 — 336,368-article continuation results

HIST-ALL-336368-1 — Run 49 retained Run 47, added 10,000 source-ordered articles, reused 1,655,490 units, and translated 36,846 new units from 995 initial batches. Run 48 does not exist: a canceled preparation transaction consumed that PostgreSQL sequence value before rollback. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-336368.dump`, SHA-256 `3da7e5fc73b1ef94df58b19bf575a6ba8f17b9cfbd0132446cfc9e80db3567a9`.

HIST-ALL-336368-2 — The first preparation attempt exceeded nine minutes in a correlated missing-article count and was canceled before commit. Replacing it with the equivalent run-article count minus distinct unit-article count reduced extraction to 29.0 seconds. The successful preparation took 158.6 seconds: source preflight 5.3, scope selection 13.6, extraction 29.0, reuse 70.1, batching 14.7, and verification 25.9 seconds.

HIST-ALL-336368-3 — Productive window `run49-c110-1` completed 85 measured requests in 90.001 seconds at concurrency 110. It measured 332.0 headwords and 1,909.3 units per minute, p50 74.8 seconds, p95 99.1 seconds, p99 102.1 seconds, 7.3% database duty, and 196 MB peak runner memory. It had one validation retry and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, missing units, duplicates, or source-hash mismatches. Headword throughput was 4.0% below concurrency 100, so 100 remains the recommended setting.

HIST-ALL-336368-4 — The main concurrency-110 drain and recovery produced 1,012 accepted attempts and 92 rejected validation attempts, with 74 retries and 17 splits. Across 1,085 timed attempts, p50 latency was 75.4 seconds and p95 was 116.8 seconds. The runner peaked near 200 MB. PostgreSQL recorded zero database retries.

HIST-ALL-336368-5 — A laptop reboot interrupted three exact leases during the last 34 units. Host and PostgreSQL clocks remained correct; an initial claim that the clock moved backward was disproved by the boot time and both clocks. With zero surviving worker processes, the three matching lease tokens were marked interrupted and audited, then requeued through the normal runner recovery path. No attempts, translations, or evidence were deleted.

HIST-ALL-336368-6 — Recursive recovery isolated one glossary leaf whose correct Latin taxon text repeatedly triggered the English-word limit. The saved Russian translation was ingested through a targeted claimed attempt with both taxa in standard parentheses, which passed the unchanged validator. The run ended with zero unfinished batches, terminal leaves, unresolved errors, claimed attempts, untranslated units, or membership mismatches.

HIST-ALL-336368-7 — All 1,692,336 units were accepted. Canonicalization changed 213 values and its idempotence pass changed zero. All 113 tests passed normally and with the disposable PostgreSQL recovery database enabled. Preparation took 2 minutes 39 seconds, the fixed 110 window about 2 minutes including ramp and drain, the main translation work about 30 minutes before the reboot, resumed recovery 3 minutes 40 seconds, and the full batch about 57 minutes of wall time including reboot recovery, acceptance, export, and tests.

~~~text
ZIP members:                319
schema-validated banks:      106
archive articles:       336,368
SHA-256: 84dcf1ecf6c5d4fe9532f2b1f415abc2f1f4edfe53422477ccfff116858001b2
~~~

HIST-ALL-336368-8 — Export 55 was verified. Run 49 is the stopping point. Concurrency 100 remains the proven setting for a future authorized run; do not create another run without new authorization.

## HIST-ALL-346368 — 346,368-article continuation results

HIST-ALL-346368-1 — Run 50 retained Run 49, added 10,000 source-ordered articles, reused 1,692,336 units, and translated 29,904 new units from 824 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-346368.dump`, SHA-256 `508692ec3c140d2f3d7023e0fd9e1eff65232017e30bfb113aecdf3826fcbf9e`.

HIST-ALL-346368-2 — Optimized preparation took 171.40 seconds: source preflight 8.96, scope selection 16.35, extraction 34.17, reuse 77.33, batching 9.04, and verification 25.56 seconds. It passed all identity, acceptance, batching, and error gates with no Luna requests.

HIST-ALL-346368-3 — Five full concurrency-100 windows completed 404 measured requests and 453 drain requests. Their mean measured throughput was 308.5 headwords per minute, with individual results from 288.0 to 340.6. Peak runner memory was 202 MB. They recorded 13 measured validation rejections, 10 retries, three splits, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, missing units, or duplicate translations.

HIST-ALL-346368-4 — The final four retry batches recursively isolated three singleton spelling variants of one slipper-lobster gloss. Luna repeatedly returned correct Russian text with Latin taxonomic names that triggered `too_much_english`. The audited targeted-leaf path ingested the concise Russian definition `лангуст-цикада (один из видов)` for all three without another Luna request. The final short-window telemetry wrapper exited after productive work ended during ramp because it had no measurement phase; no request or translation was lost.

HIST-ALL-346368-5 — The first export attempt exhausted Docker's remaining 6.6 GB while PostgreSQL spilled a large parallel sort at the default 4 MB `work_mem`. A session-only 512 MB `work_mem` with parallel gather disabled avoided the spill without moving data into iCloud or changing persistent database settings. Build and independent verification then took 288 seconds.

HIST-ALL-346368-6 — All 1,722,240 units were accepted with zero unfinished leaves, unresolved errors, missing units, duplicates, source-hash mismatches, or membership mismatches. The 22 blocked batch rows are preserved split-parent provenance. All 113 tests passed with two expected PostgreSQL integration skips. Run 50 took about 48 minutes from creation through verified export and tests.

~~~text
ZIP members:                323
schema-validated banks:     108
archive articles:       346,368
SHA-256: 55bac7397ed07067c05fc7b95aff6a4f7f413e994fa4bf4de3d75796e39ac846
~~~

HIST-ALL-346368-7 — Export 56 was verified. Run 50 is the stopping point. Concurrency 100 remains the proven setting for a future authorized run; do not create another run without new authorization.

## HIST-ALL-356368 — 356,368-article continuation results

HIST-ALL-356368-1 — Run 51 retained Run 50, added 10,000 source-ordered articles, reused 1,722,240 units, and translated 36,470 new units from 963 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-356368.dump`, SHA-256 `5b7f1902a4e97fa0047bec4e586e86d7960de6d637181ca21879e30c41091ceb`.

HIST-ALL-356368-2 — Preparation took 179.47 seconds: source preflight 9.20, scope selection 14.14, extraction 36.69, reuse 82.61, batching 9.97, and verification 26.86 seconds. It passed all pre-Luna gates.

HIST-ALL-356368-3 — Six full concurrency-100 windows completed 467 measured requests and 540 drain requests. Mean measured throughput was 295.8 headwords per minute. Peak runner memory was 202 MB. The windows recorded 19 measured validation rejections, 16 retries, three splits, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, missing units, or duplicate translations.

HIST-ALL-356368-4 — The final eight retry batches recursively isolated four singleton glosses: two spelling variants for Saccocirrus worms and two for the New Guinea singing dog. Repeated Latin taxonomic text triggered `too_much_english`. The audited targeted-leaf path ingested concise Russian definitions without more Luna requests. The short-window telemetry wrapper again ended without a measurement phase after productive work finished during ramp; no request or translation was lost.

HIST-ALL-356368-5 — All 1,758,710 units were accepted with zero unfinished leaves, unresolved errors, missing units, duplicates, source-hash mismatches, or membership mismatches. Export used session-only 512 MB `work_mem` and disabled parallel gather to avoid Docker temporary-file spill. Build and independent verification took 298 seconds. All 113 tests passed with two expected PostgreSQL integration skips.

~~~text
ZIP members:                332
schema-validated banks:     111
archive articles:       356,368
SHA-256: a2d431226f3d64628ae5142406a6f4487fe22678a35934ea8a19ef75f3cbefdd
~~~

HIST-ALL-356368-6 — Export 57 was verified. Run 51 is the latest completed checkpoint. The next 10,000-article continuation targets 366,368 articles.

## HIST-ALL-366368 — 366,368-article continuation results

HIST-ALL-366368-1 — Run 52 retained Run 51, added 10,000 source-ordered articles, reused 1,758,710 units, and translated 34,268 new units from 862 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-366368.dump`, SHA-256 `1cf2a714132af34478bf37a6332c44cc409b846919d479d4f65893917cc749e0`.

HIST-ALL-366368-2 — Preparation recorded source preflight 9.78 seconds, scope selection 18.76, extraction 40.39, reuse 89.42, and batching 10.61 seconds. The final acceptance-gap query exhausted Docker temporary space under default 4 MB `work_mem`; the identical read-only gate passed with session-only 512 MB `work_mem` and parallel gather disabled. Run 52 had 366,368 articles, 1,792,978 units, 34,268 ready units, 862 batches, and zero gaps or unresolved errors before Luna started.

HIST-ALL-366368-3 — Five full concurrency-100 windows completed 373 measured requests and 500 drain requests. Mean measured throughput was 284.7 headwords per minute. Peak runner memory was 200 MB. They recorded nine measured validation rejections, seven retries, two splits, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, missing units, or duplicate translations.

HIST-ALL-366368-4 — The final retry window isolated two untranslated xref leaves: `daffodil; narcissus` and `YouTube`. The audited targeted-leaf path ingested `нарцисс` and `Ютуб` without more Luna requests. The short-window telemetry wrapper ended without a measurement phase after productive work finished during ramp; no request or translation was lost.

HIST-ALL-366368-5 — All 1,792,978 units were accepted with zero unfinished leaves, unresolved errors, missing units, duplicates, source-hash mismatches, or membership mismatches. Build and independent verification took 305 seconds using session-only sort memory. All 113 tests passed with two expected PostgreSQL integration skips.

~~~text
ZIP members:                334
schema-validated banks:     113
archive articles:       366,368
SHA-256: 2e8390e3f3d71032d042d1c4662ccd19e03e51483845a3b81ba0219f8450b287
~~~

HIST-ALL-366368-6 — Export 58 was verified. Run 52 is the latest completed checkpoint. Docker has about 2 GB free, so PostgreSQL storage must be expanded or safely migrated outside Documents before the next 10,000-article continuation.

## HIST-ALL-376368 — 376,368-article continuation results

HIST-ALL-376368-1 — Before Run 53, Docker's internal disk limit was raised from 128 GB to 264 GB. The disk image stayed under `~/Library/Containers`, outside Documents and iCloud. PostgreSQL had 130 GB free after restart and 128 GB free after the completed run. The pre-run backup is `work/backups/jitendex-postgresql-before-376368.dump`, SHA-256 `acc9930e34655562b0bd63c2f947ea3ea3a716bcf1818d268fff450ef6e18328`.

HIST-ALL-376368-2 — Run 53 retained Run 52, added 10,000 source-ordered articles, reused 1,792,978 units, and translated 38,615 new units from 977 initial batches. Preparation took 177.96 seconds: source preflight 17.60, scope selection 17.13, extraction 31.97, reuse 75.26, batching 6.98, and verification 29.01 seconds. It passed every pre-Luna gate.

HIST-ALL-376368-3 — Six full concurrency-100 windows completed 430 measured requests and 599 drain requests. Mean measured throughput was 268.86 headwords per minute, ranging from 233.32 as the scope emptied to 281.32. Peak runner memory was 203 MB and maximum database duty cycle was 6.29%. The measured windows recorded 24 validation rejections, 19 retries, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-376368-4 — Two small retry windows recursively isolated validation failures. All terminal children validated through normal Luna responses, so no manual target repair was needed. The last seven requests finished during ramp; the telemetry wrapper reported that no measurement phase existed, but PostgreSQL proved zero ready, leased, active, terminal blocked leaves, or unresolved errors. The 45 blocked rows are preserved split-parent provenance.

HIST-ALL-376368-5 — Acceptance took 5.30 seconds and validation took 17.85 seconds. All 1,831,593 units were accepted with zero membership mismatches or blocking issues. Build took 104.89 seconds and independent verification took 206.53 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 53 took about 54 minutes from preparation start through verified export and tests.

~~~text
ZIP members:                339
schema-validated banks:     116
archive articles:       376,368
translated headwords:   374,108
headwords remaining:     57,437
SHA-256: c62358ab73758f42bd5fbc93d01fb84ef854a395fe58b061116fc8130f415e2a
~~~

HIST-ALL-376368-6 — Export 59 was verified. Run 53 is the latest completed checkpoint. The next 10,000-article continuation targets 386,368 articles at concurrency 100.

## HIST-ALL-386368 — 386,368-article continuation results

HIST-ALL-386368-1 — Run 54 retained Run 53, added 10,000 source-ordered articles, reused 1,831,593 units, and translated 35,761 new units from 903 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-386368.dump`, SHA-256 `9d2de1fcbc1bdad9b51cbbe2aaa5f67507b36747ffb2fb79f87b98d66354a096`.

HIST-ALL-386368-2 — Preparation took 183.84 seconds: source preflight 22.67, scope selection 15.93, extraction 33.61, reuse 78.02, batching 5.79, and verification 27.82 seconds. It passed all pre-Luna identity, acceptance, batching, and error gates.

HIST-ALL-386368-3 — Five full concurrency-100 windows completed 367 measured requests and 500 drain requests. Mean measured throughput was 285.72 headwords per minute, ranging from 265.99 to 309.99. Peak runner memory was 201 MB and maximum database duty cycle was 6.72%. They recorded eight measured validation rejections, six retries, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-386368-4 — The final 58 requests and their recursive children all validated through normal Luna responses. They finished during ramp, so the telemetry wrapper reported that no measurement phase existed. PostgreSQL proved zero ready, leased, active, terminal blocked leaves, or unresolved errors. The nine blocked rows are preserved split-parent provenance; no manual target repair was needed.

HIST-ALL-386368-5 — Acceptance took 5.07 seconds and validation took 22.59 seconds. All 1,867,354 units were accepted with zero membership mismatches or blocking issues. Build took 110.95 seconds and independent verification took 209.07 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 54 took about 46 minutes from preparation start through verified export and tests.

~~~text
ZIP members:                342
schema-validated banks:     118
archive articles:       386,368
translated headwords:   384,087
headwords remaining:     47,458
SHA-256: 59562ccf6914c5b0559b8ecfbced91ed1fa639b8314baabc362ce7f18fa78a73
~~~

HIST-ALL-386368-6 — Export 60 was verified. Run 54 is the latest completed checkpoint. The next 10,000-article continuation targets 396,368 articles at concurrency 100.

## HIST-ALL-396368 — 396,368-article continuation results

HIST-ALL-396368-1 — Run 55 retained Run 54, added 10,000 source-ordered articles, reused 1,867,354 units, and translated 39,770 new units from 1,035 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-396368.dump`, SHA-256 `06786d6fc108f96819f151dcceb87d9ab8e6c351d4dc600f9134afd74dcccc29`.

HIST-ALL-396368-2 — Preparation took 206.32 seconds: source preflight 24.00, scope selection 18.51, extraction 38.54, reuse 84.74, batching 9.64, and verification 30.89 seconds. It passed all pre-Luna gates.

HIST-ALL-396368-3 — Six full concurrency-100 windows completed 436 measured requests and 600 drain requests. Mean measured throughput was 273.77 headwords per minute, ranging from 239.32 to 294.66. Peak runner memory was 201 MB and maximum database duty cycle was 6.22%. They recorded 23 measured validation rejections, 18 retries, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-396368-4 — The final 56 retries recursively isolated one singleton xref gloss for `ＪＳ`. Repeated `JavaScript` output failed `no_cyrillic`; the audited targeted-leaf path ingested `Джаваскрипт` without another Luna request. The short-window telemetry wrapper reported no measurement phase after productive work finished during ramp. PostgreSQL then proved zero unfinished work, terminal leaves, or unresolved errors. Fourteen blocked rows are preserved split-parent provenance.

HIST-ALL-396368-5 — Acceptance took 5.38 seconds and validation took 18.17 seconds. All 1,907,124 units were accepted with zero membership mismatches or blocking issues. Build took 111.01 seconds and independent verification took 212.81 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 55 took about 53 minutes from preparation start through verified export and tests.

~~~text
ZIP members:                345
schema-validated banks:     121
archive articles:       396,368
translated headwords:   394,078
headwords remaining:     37,467
SHA-256: 11ef06cb29b77dd24370789f01c3ad546209dd3644094e6be83fd168b9e4b8c4
~~~

HIST-ALL-396368-6 — Export 61 was verified. Run 55 is the latest completed checkpoint. The next 10,000-article continuation targets 406,368 articles at concurrency 100.

## HIST-ALL-406368 — 406,368-article continuation results

HIST-ALL-406368-1 — Run 56 retained Run 55, added 10,000 source-ordered articles, reused 1,907,124 units, and translated 37,260 new units from 1,052 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-406368.dump`, SHA-256 `2353e5cbcf4ad0b0a5031f4e1ac91654144eda076c097e3d62aea44f43fc5bb1`.

HIST-ALL-406368-2 — Preparation took 215.53 seconds: source preflight 21.33, scope selection 16.07, extraction 42.85, reuse 95.65, batching 10.32, and verification 29.32 seconds. It passed all pre-Luna gates. The time was close to the prior optimized 10,000-article runs and needed no database investigation.

HIST-ALL-406368-3 — Six full concurrency-100 windows completed 478 measured requests and 598 drain requests. Mean measured throughput was 301.64 headwords per minute, ranging from 279.97 to 317.27. Peak runner memory was 205 MB and maximum database duty cycle was 6.84%. They recorded 17 measured validation rejections, 19 retries, four splits, one timeout, five transport failures, and zero rate limits, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-406368-4 — The final 37-batch tail recursively isolated one singleton note for `チンチン`. Four Latin comparison spellings repeatedly failed `too_much_english`. The audited targeted path kept Italian `cincin`, transliterated the other forms, and passed validation without another Luna request. Its first saved repair still had three validator-counted ASCII words; the exact lease was marked interrupted and requeued before the corrected target was ingested. The short-window wrapper then reported no measurement phase because productive work finished outside a full measured phase. PostgreSQL proved zero unfinished work, terminal leaves, unresolved errors, or active leases. Thirteen blocked rows remain split-parent provenance.

HIST-ALL-406368-5 — Acceptance took 12.00 seconds and validation took 24.59 seconds. All 1,944,384 units were accepted with zero membership mismatches or blocking issues. Build took 114.57 seconds and independent verification took 216.26 seconds with session-only 512 MB `work_mem` and parallel gather disabled. The first test command omitted `PYTHONPATH=src` and stopped during collection; the corrected full suite passed all 113 tests with two expected PostgreSQL integration skips. Run 56 took about 49 minutes from preparation through verified export and tests.

~~~text
ZIP members:                349
schema-validated banks:     123
archive articles:       406,368
translated headwords:   404,076
headwords remaining:     27,469
SHA-256: f711d12be4b504511a42eb9f4a94ca1c7eb2dc1562bfb5bcc098107313d52474
~~~

HIST-ALL-406368-6 — Export 62 was verified. Run 56 is the latest completed checkpoint. Work paused here at the user's request; no Run 57 scope or batch was created.

## HIST-ALL-416368 — 416,368-article continuation results

HIST-ALL-416368-1 — Run 57 retained Run 56, added 10,000 source-ordered articles, reused 1,944,384 units, and translated 39,244 new units from 1,030 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-416368.dump`, SHA-256 `fa5a01dde88912497c02148e9e3fe55fe8e15e279d3e807f0c82fe55dd015720`. The host client was absent from `PATH`, so the version-matched PostgreSQL 17 container client created and validated a temporary dump before it was copied out and the temporary file removed. The backup took 4 minutes 10 seconds.

HIST-ALL-416368-2 — Preparation took 246.76 seconds: source preflight 25.91, scope selection 20.60, extraction 38.06, reuse 117.69, batching 6.62, and verification 37.89 seconds. It passed all pre-Luna gates. The longer reuse and verification times follow the growing source-run row count and remained well below the ten-minute investigation threshold.

HIST-ALL-416368-3 — Six full concurrency-100 windows completed 507 measured requests and 550 drain requests. Mean measured throughput was 327.77 headwords per minute, ranging from 318.65 to 339.99. Peak runner memory was 201 MB and maximum database duty cycle was 17.69%. They recorded 11 measured validation rejections, ten retries, one split, and zero rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits. The higher database duty cycle still showed no ingestion, pool, or lock bottleneck.

HIST-ALL-416368-4 — Several full windows took about six to seven minutes because their last drain requests completed slowly, while measured p95 latency remained 90.9 to 99.8 seconds. The final six-batch tail took 12 minutes 35 seconds while one failing branch recursively split and retained each successful sibling. It ended with zero terminal leaves, unfinished work, unresolved errors, or active leases, so no manual repair was needed. Seven blocked rows remain split-parent provenance. The short-window wrapper reported no measurement phase after all productive tail work completed outside a full measured phase.

HIST-ALL-416368-5 — Acceptance took 5.82 seconds and validation took 23.33 seconds. All 1,983,628 units were accepted with zero membership mismatches or blocking issues. Build took 116.00 seconds and independent verification took 225.21 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 57 took about 70 minutes from backup start through verified export and tests; the longer total was mainly the backup, slow drains, and recursive tail isolation rather than lower measured throughput.

~~~text
ZIP members:                365
schema-validated banks:     126
archive articles:       416,368
translated headwords:   414,056
headwords remaining:     17,489
SHA-256: fdae199fe1e9f584c61904325a4ca44f0dbeecca95ee8e31c04959710ded9470
~~~

HIST-ALL-416368-6 — Export 63 was verified. Run 57 is the latest completed checkpoint. The next 10,000-article continuation targets 426,368 articles at concurrency 100.

## HIST-ALL-426368 — 426,368-article continuation results

HIST-ALL-426368-1 — Run 58 retained Run 57, added 10,000 source-ordered articles, reused 1,983,628 units, and translated 40,898 new units from 1,071 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-426368.dump`, SHA-256 `3f5e0419a3c33a9faf8f29a61335c5bc1a1448820b4470ecb8156255285e3215`. Backup creation and validation took 4 minutes 24 seconds.

HIST-ALL-426368-2 — Preparation took 243.76 seconds: source preflight 23.27, scope selection 20.72, extraction 39.09, reuse 109.16, batching 11.81, and verification 39.70 seconds. It passed all pre-Luna gates and remained within the normal optimized range.

HIST-ALL-426368-3 — Six full concurrency-100 windows completed 504 measured requests and 599 drain requests. Mean measured throughput was 323.37 headwords per minute, ranging from 307.98 to 334.65. Peak runner memory was 196 MB and maximum database duty cycle was 15.23%. They recorded 14 measured validation rejections, 12 retries, two splits, one recovered rate limit, and zero timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-426368-4 — The final 11-batch tail took 7 minutes 7 seconds while several failing branches split. It isolated one singleton `JavaScript` xref gloss for `ジャバスク`; three unchanged Latin responses failed `no_cyrillic`. The audited targeted-leaf path reused the previously validated Russian form `Джаваскрипт` without another Luna request. PostgreSQL then proved zero unfinished work, terminal leaves, unresolved errors, or active leases. Twelve blocked rows remain split-parent provenance. The short-window wrapper reported no measurement phase after productive tail work completed outside a full measured phase.

HIST-ALL-426368-5 — Acceptance took 5.88 seconds and validation took 22.65 seconds. All 2,024,526 units were accepted with zero membership mismatches or blocking issues. Build took 118.17 seconds and independent verification took 222.74 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 58 took about 65 minutes from backup start through verified export and tests.

~~~text
ZIP members:                367
schema-validated banks:     128
archive articles:       426,368
translated headwords:   424,032
headwords remaining:      7,513
SHA-256: d192baef6c43cf0c3f30f5b304e2b73a97d082a9a74e8a03204390deb222dd35
~~~

HIST-ALL-426368-6 — Export 64 was verified. Run 58 is the latest completed checkpoint. The final continuation adds the remaining 7,517 source articles to reach all 433,885 articles.

## HIST-ALL-433885 — Full-corpus continuation results

HIST-ALL-433885-1 — Run 59 retained Run 58, added the final 7,517 source-ordered articles, reused 2,024,526 units, and translated 28,519 new units from 728 initial batches. The pre-run PostgreSQL backup is `work/backups/jitendex-postgresql-before-433885.dump`, SHA-256 `31653ba79d08813660d098e2a361af62ebeb0025c6202b6aa59dcd761230db3b`. Dump creation took 4 minutes 6 seconds. Listing, copying, hashing, and cleanup took about 28 seconds.

HIST-ALL-433885-2 — Preparation took 332.83 seconds: source preflight 33.94, scope selection 33.67, extraction 45.57, reuse 146.63, batching 10.17, and verification 62.84 seconds. It created 2,053,045 total units and passed every pre-Luna gate with zero gaps, claims, unresolved errors, or unbatched units.

HIST-ALL-433885-3 — Four full concurrency-100 windows completed 293 measured requests and 400 drain requests. Mean measured throughput was 283.15 headwords per minute, ranging from 242.65 to 325.99. Measured p50 latency ranged from 72.17 to 76.10 seconds and p95 from 91.08 to 98.52 seconds. Peak runner memory was 195 MB and maximum database duty cycle was 14.31%. The measured phases recorded three validation rejections and three retries, with zero splits, rate limits, timeouts, transport failures, database retries, claim collisions, stale leases, or lock waits.

HIST-ALL-433885-4 — Across the complete four windows, productive drains included four validation rejections and two timeouts among six rejected attempts; all six retried successfully. The final 41-batch tail took 3 minutes 1 second and completed without another rejected attempt. As expected for fewer than 60 remaining batches, the wrapper reported that it could not form a full measurement phase after productive work had emptied the queue. PostgreSQL then proved all 728 batches deterministically validated, all 2,053,045 units translated, and zero active, ready, leased, retryable, terminal, claimed, or unresolved work.

HIST-ALL-433885-5 — Acceptance took 4.28 seconds and validation took 23.88 seconds. All 2,053,045 units were accepted with zero membership mismatches or blocking issues. Build took 130.52 seconds and independent verification took 233.30 seconds with session-only 512 MB `work_mem` and parallel gather disabled. All 113 tests passed with two expected PostgreSQL integration skips. Run 59 took about 48 minutes from backup start through verified export, tests, and final audit.

~~~text
ZIP members:                369
schema-validated banks:     130
archive articles:       433,885
translated headwords:   431,545
headwords remaining:          0
SHA-256: 68b4ef51f06213428e0c5b223b6715e2099542e28456c9fe8e42df75587d127b
~~~

HIST-ALL-433885-6 — Export 65 was verified and Run 59 completes the full Jitendex corpus. The first preparation command used local port 5432 instead of Docker's published port 5433 and failed before database changes. Future commands should resolve the port with `docker port jitendex-postgres 5432/tcp`. The first verify command omitted `JITENDEX_POSTGRES_URL` and failed before reading the archive; the corrected command passed. Future verification shells must export the authoritative URL first.

## HIST-TAGS-RU-V1 — Run 59 approved Russian tags

HIST-TAGS-RU-V1-1 — Before the canonical import, PostgreSQL was backed up to `work/backups/jitendex-postgresql-before-tags-ru-v1.dump`. The validated dump SHA-256 is `9bf1d1ae89696232d919581ab3f2ba18c50637a76882962b8ad4ee6d265b7b7a`.

HIST-TAGS-RU-V1-2 — `terminology/jitendex-tags-ru.csv` reconciled all 236 source identities. Its SHA-256 is `f3f3649806a9de4fc7a0c1a96cffafc343d8d7d31592b0547266809c127a95fa`. The first import updated provenance only, created no replacement history, and the repeated import made no changes.

HIST-TAGS-RU-V1-3 — Yomitan export 66 is `dist/jitendex-articles-433885-ru-luna-v4-tags-ru-v1.zip`, SHA-256 `c157b41f3fc99a52d4099c8384e87c8e0ec8813e87c93f988a801c3e9fc63a58`. It contains 654,503 embedded tag occurrences, replaced 1,706 historical labels and 454 historical tooltips, localized eight tag-bank rows, and rewrote 136,832 tag-reference fields. Of 167,269 tag references, 137,523 changed; 29,746 `★` references were already canonical.

HIST-TAGS-RU-V1-4 — GoldenDict export 67 is `dist/jitendex-articles-433885-ru-goldendict-tags-ru-v1.zip`, SHA-256 `f81e9c8d41139a8cee09b5333587ef723797b12580e5e3e5db8a2714efe589e0`. Its verifier checked 654,503 embedded tags and 167,269 tag-bank badges with exact Russian labels and tooltips.

HIST-TAGS-RU-V1-5 — Both archives reproduced byte-for-byte on a second build. All 140 tests passed with two expected PostgreSQL integration skips. Yomitan schema validation and both independent archive verifiers passed. The Run 59 report is `reports/jitendex_tags/run59-tag-unification.json` and records zero missing mappings.

HIST-TAGS-RU-V1-6 — Automated browser control cannot open Chrome extension settings, so the clean-profile Yomitan import and representative POS, field, dialect, and tag-bank hover check remains a manual release gate. Export 65 was not modified.

HIST-TAGS-RU-V1-7 — The co-author metadata revision records Yuri Katkov in every supported archive. Verified exports are Yomitan 73, SHA-256 `c5c012e3d77c714785881c0f67b7c76cb79e1902c8b73c7d0e8232c23ec2208e`; GoldenDict 74, `eb13c6eed1903e7771b90a55a9553ba2e3812562956961cd1b2a068c74977594`; MDict 75, `069f0bebbfe21a0c015e3ac565e4cc7253c88f6e9b7f933fc3c6d164168af269`; PocketBook 76, `8c9bcdbc7de13bfe5271ad1a2ff57baf367f134eaab39e90022f56265188f008`; and Apple Dictionary 77, `0cfb5198b2ad502db3b4c76f78d0c8293e0c94a1dc1a1c6ce2828b27f607f482`.

HIST-TAGS-RU-V1-8 — The product rename revision uses the display name `Колобок 400k` and the Latin base name `jp-ru-kolobok-400k`. Verified exports are Yomitan 78, SHA-256 `063893f6d9453de4fec184b17860ea7e7608f9875a667e45d6046029e7736723`; GoldenDict 79, `c3180d533c3f472eed1e1d91004591117d9d40701aa4e25d19a2dbaaade923a0`; MDict 80, `2dd86064af2fbd62d4e40ad2a24a8b1f19b7e229260acc0a2d998b2ddc3de36d`; PocketBook 81, `be97c6bd3d3b49ec24bd08fe2e7ad45343994fa3d2832e295208daefdd2664ee`; and Apple Dictionary 82, `1b1116b61db7369621de8cf443f42de26d1ad39c03715f0aeaed4d0ecfd42b64`.

HIST-TAGS-RU-V1-9 — Dictionary version `1.0` uses compilation datetime `2026-08-15T21:04:30Z`. Installed titles, native descriptions, technical manifests, archive names, and internal payload names expose the version. Verified exports are Yomitan 83, SHA-256 `24c0164f6d645f6426bef5b09f5dfdc46952cf132aed6d8bc033800f9ff7824b`; GoldenDict 84, `774a4ec87862451f05992c7765b5a9bbc32ac96e54d134cadc5d81397f384aee`; MDict 85, `589608c811d6eb72f2b16f83c2929ce1e356a7eb12f466977ad34bcaad23ddb3`; PocketBook 86, `d03c892fe2db5fb0f93a65c54de729d41f1674ba7a89e97470b69a66a75e5d3f`; and Apple Dictionary 87, `f7879e122e6260e47def25c05a9b2846655ba8a52ad28c89f485a0a21ce26d46`.

HIST-TAGS-RU-V1-10 — Build times were 133.97 seconds for Yomitan, 147.35 for GoldenDict, 159.39 for MDict, 252.95 for PocketBook, and 334.65 for Apple Dictionary. All five format verifiers passed. All 150 tests passed with two expected integration skips.

HIST-TAGS-RU-V1-11 — The pinned Apple schema still points to retired HTTP module URLs. A local copy with the same schema rules and canonical HTTPS modules validated the DDK sample, but full-corpus `xmllint` remained CPU-bound for 90 minutes and was stopped. The version-only source change preserved dictionary structure. The pinned DDK build and the independent Apple bundle verifier passed. Future work should pin the module files and replace the non-streaming full-corpus schema gate before requiring it again.

## HIST-YOMITAN-V101 — Yomitan V1–V3 remediation and v1.0.1

HIST-YOMITAN-V101-1 — The published v1.0 Yomitan archive inherited Jitendex operational metadata. Its update button could read `https://jitendex.org/static/yomitan.json` and replace the Russian dictionary with English Jitendex. Installed v1.0 metadata cannot be repaired remotely, so users must manually import v1.0.1 once.

HIST-YOMITAN-V101-2 — The content audit separated intended Latin text from translation defects. Jitendex, JMdict, Tatoeba, brands, creator names, scientific names, source-language forms, grammar quotations, acronyms, and license identifiers remain. Raw `redirected from`, raw form restrictions, mixed-alphabet accidents such as `losили`, and 27 reviewed lexical misses were repaired.

HIST-YOMITAN-V101-3 — The pre-write backup is `work/backups/jitendex-postgresql-before-yomitan-v1.0.1.dump`, SHA-256 `8285e560a76c71e947695083469ece7f598046617d943a6838c8708d41f1ea61`. Run 59 contains 2,474 immutable canonicalization-history rows. The approved lexical manifest SHA-256 is `07746f61422324adc5bfd6d5fadb9a12cdc14fba76ca0af8e81883f98bdf0f26`, and the visible-Latin approval SHA-256 is `22c1fccd4a36bb74be8739ad2f0654362b595a461e6f5391af50faa9e0cd920b`.

HIST-YOMITAN-V101-4 — Final exports are Yomitan 91, SHA-256 `f0e8a6d8823398401994d0c7738aee4dca83b225bf276f9b08282cafbbac68b7`; GoldenDict 95, `506396de7a00009074d956a62fc66c56cf0f8645c0470ad1a75868b622c5be51`; MDict 96, `f22238c020b7ddebfeffc2df83256816fc23e00e02042b3eff321ce8c5145b4d`; PocketBook 97, `e757ea8ddde01a2380485c6f498610f86565dda2076a993e3c9c9611374d31cc`; and Apple Dictionary 98, `57b828929cb674aeb1bc0be9c833aadfc4185ea81d40855a6cf20be93c150c1c`.

HIST-YOMITAN-V101-5 — Independent exports 99–103 reproduced all five archives byte-for-byte. The full test suite collected 165 tests: 163 passed and two expected PostgreSQL integration tests skipped. The archive-level lexical audit scanned all 433,885 articles and decompressed every MDict record; all formats contain zero raw English redirect and form-restriction templates.

HIST-YOMITAN-V101-6 — The v1.0.1 Yomitan title remains `Колобок 400k`. Its revision is `2026.08.21.0-jp-ru-kolobok-400k-v1.0.1-tags-ru-v1`; its owned update index is `https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/yomitan.json`; and its owned release asset is `https://github.com/ganqqwerty/jp-ru-kolobok-dictionary/releases/download/v1.0.1/jp-ru-kolobok-400k-v1.0.1-yomitan.zip`.

HIST-YOMITAN-V101-7 — Full-corpus Apple `xmllint` is intentionally removed from this and future release gates. It did not complete within 90 minutes and duplicated more useful bounded checks. Apple builds now stream-parse the complete source XML, verify counts, run the pinned DDK compiler, and independently verify the compiled bundle.

HIST-YOMITAN-V101-8 — Clean-profile Yomitan rendering and owned-update smoke results must be recorded separately in `reports/yomitan_localization/run59-v1.0.1-smoke.json`; publication details are added only after the public release and Pages deployment are verified.

HIST-YOMITAN-V101-9 — The clean-profile smoke passed in Chrome `151.0.7922.169` arm64 with Yomitan `26.7.29.0` on macOS. All 18 lookup, rendering, localization, terminology, and updater checks passed. The update check read the local Kolobok index and downloaded the final ZIP exactly once. Immediately after import, the settings page temporarily showed two identical final-revision rows; after one settings-page reload, one enabled `Колобок 400k` row remained. The successful report was recorded against export 91 and completed Run 59.

HIST-YOMITAN-V101-10 — Release `https://github.com/ganqqwerty/jp-ru-kolobok-dictionary/releases/tag/v1.0.1` is public. Tag `v1.0.1` resolves to verified release commit `e8696066bd629ecd265783e5b5a3de43b933330b`, which is an ancestor of published `main`. GitHub Pages workflow `32482676304` succeeded, and the public page exposes all five versioned downloads plus the manual-upgrade warning.

HIST-YOMITAN-V101-11 — The post-publication audit fetched all seven release assets into a clean directory. Every archive matched `jp-ru-kolobok-400k-v1.0.1-SHA256SUMS.txt`; all five format verifiers passed on the registered byte-identical artifacts. The public update index is byte-identical to the staged index with SHA-256 `90017a061ee25d82c1f4fd312175d02c57a792ac1cdea295d21b3ee02e7638c7` and has no operational Jitendex URL. The final suite collected 174 tests: 172 passed and two expected PostgreSQL integration tests skipped.

## HIST-SURASURA-R1 — Surasura Japanese-to-Russian run 1

HIST-SURASURA-R1-1 — Run 1 translated `[JA-JA Onomatopoeia] surasura.zip` in the isolated PostgreSQL database `surasura_translation`. The source SHA-256 is `f8d882b8db922e9638d4d577885c0e939266a15d285c0807c5117ee59d13f56e`. The source contains 1,422 entries. One entry has an empty definition, so 1,421 entries produced 5,369 Japanese translation units.

HIST-SURASURA-R1-2 — The run used `gpt-5.6-luna`, medium reasoning, `prompts/translate_luna_ja_ru_v1.txt`, and concurrency 100. Luna finished in 376.10 seconds. It made 245 attempts, including six validation retries, one timeout, and one recursive split. There were zero rate limits, zero transport failures, and zero database failures. Total usage was 5,278,083 tokens.

HIST-SURASURA-R1-3 — The first JP-source rule protected Japanese tokens when a definition also contained short Latin text. This caused early validation retries. The extractor now treats a Japanese-dominant unit as translatable source text and does not require its Japanese or Latin fragments in the Russian target. The saved responses remained in the audit trail, and normal retry and split provenance produced the accepted replacements.

HIST-SURASURA-R1-4 — Acceptance stored 5,369 translations. Validation reported zero membership mismatches and zero blocking issues. `release_ready` remains false only because this Luna workflow has no optional Terra review rows, which matches the established Luna release behavior.

HIST-SURASURA-R1-5 — The verified archive is `dist/jp-ru-surasura-onomatopoeia-v1.0-yomitan.zip`, SHA-256 `7c19d03d91cf5a0bd44ba8e748a1fea390f088d704fd547385476122755c3015`. It contains all 1,422 source entries and one schema-valid term bank. A second export was byte-identical. The full test suite passed with two expected PostgreSQL integration skips.

HIST-SURASURA-R1-6 — The PostgreSQL backup is `work/surasura/backups/surasura-translation-run1.dump`, SHA-256 `2861fcade2a899ea30d118a3d8030a073af6e13dbf2e83fb1c6a8926adae6103`. The archive keeps the source author, attribution, and project URL. Its title is `surasura 擬声語 — русский` and its revision is `surasura-ru-2026.08.30`.
