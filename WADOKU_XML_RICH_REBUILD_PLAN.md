# WXR — Wadoku XML rich Yomitan build plan

WXR-1 — Build two rich Yomitan dictionaries directly from the official Wadoku XML. The first is Japanese-to-German and preserves the original German text without Luna translation. The second is Japanese-to-Russian and uses the same structure with Russian text.

WXR-2 — Keep the current `dist/wadoku-jp-ru.zip` as the complete flat Russian V1 release. Build the rich German and Russian editions as separate artifacts until every release gate passes.

## WXR-OUT — Required outputs

WXR-OUT-1 — Produce `dist/wadoku-jp-de-rich.zip` as the direct, structure-preserving Japanese-to-German edition.

WXR-OUT-2 — The Japanese-to-German edition must not call Luna or replace German learner-facing text. It is a faithful rendering of the official XML in Yomitan format.

WXR-OUT-3 — Produce `dist/wadoku-jp-ru-rich.zip` as the translated Japanese-to-Russian edition.

WXR-OUT-4 — Generate both editions from one canonical PostgreSQL snapshot and one shared structural renderer. Language-specific text must be an overlay, not a separate structural conversion.

WXR-OUT-5 — Give both editions the same Wadoku entry IDs, sequence grouping, form order, sense order, references, pronunciation, and pitch data.

WXR-OUT-6 — Set `sourceLanguage` to `ja` in both archives. Set `targetLanguage` to `de` in the German archive and `ru` in the Russian archive.

WXR-OUT-7 — Use title `Wadoku — Japanisch–Deutsch (reich)` and revision `2026.07.05.0-wadoku-rich-de` for German. Use title `Wadoku — японско-русский словарь (полный)` and revision `2026.07.05.0-wadoku-rich-ru` for Russian.

WXR-OUT-8 — Produce an accepted-only Russian Yomitan checkpoint after the pilot and when the operator requests one. Include only entries whose translatable blocks are all accepted; never fill missing Russian blocks with German. Store checkpoints under `work/wadoku-xml/checkpoints`, mark the title and description as incomplete, validate their Yomitan schemas, and audit every export in PostgreSQL. Only the complete archives go to `dist`.

## WXR-SRC — Source and provenance

WXR-SRC-1 — Use `https://www.wadoku.de/downloads/xml-export/wadoku-xml-20260705.tar.xz`. This plan pins the 2026-07-05 release. A later release needs a new source audit and new expected counts before use.

WXR-SRC-2 — Require byte size `25,697,088` and SHA-256 `1028ad3e5d64a14097ae0fef47e57d4615238028b1d0315dbe28082660f07d3b` for the archive. After extraction, require `0d6309c00f6735db8d27b79cda8b54b76b0ccdd6422d3345c53b48ed0c69d70e` for `wadoku.xml`, `ecee955186f50b055687c3992761b0bf6fe9e84804c4b3543a7e2172955ca04f` for `entry.xsd`, and `3e2793cb6b21879abef92003268699e61c98b96462969a2d6d0d9ef388ba051a` for `LICENSE`. Never modify these files.

WXR-SRC-3 — Validate `wadoku.xml` against the bundled `entry.xsd` before import. Stop if validation fails.

WXR-SRC-4 — Record source counts before transformation. Count entries, written forms, readings, senses, translations, definitions, explanations, references, usage labels, etymologies, accents, and pronunciation records.

WXR-SRC-5 — Treat the official XML entry ID as the stable source identity. Do not replace it with an ordinal or a generated Yomitan row number.

WXR-SRC-6 — Preserve the Wadoku license and attribution in the Yomitan index and release documentation.

WXR-SRC-7 — The expected audit counts are 446,501 entries, 756,859 written forms, 446,501 hiragana readings, 512,363 senses, 867,198 translation containers, 866,347 `tr` nodes, 49,587 definitions, 44,744 explanations, 43,412 etymologies, 324,764 references, 203,159 usage nodes, 289,531 accents, and 446,501 pronunciation records. Preparation must stop when any count differs.

WXR-SRC-8 — The extracted paths are `wadoku-xml-20260705/wadoku.xml`, `wadoku-xml-20260705/entry.xsd`, and `wadoku-xml-20260705/LICENSE`. The license file points to the Wadoku license pages. Copy the file into both release archives and include its text and links in the release report.

## WXR-BASE — Current baseline

WXR-BASE-1 — The flat V1 source has 575,990 Yomitan rows. Each row has one plain German glossary string.

WXR-BASE-2 — V1 has no definition tags, grammar rules, sequence grouping, or metadata bank. It also has no stable Wadoku entry IDs.

WXR-BASE-3 — The matching 2022 XML has 410,140 entries, 468,745 senses, and 781,952 translation nodes. The flat source removed the sense boundaries.

WXR-BASE-4 — Use V1 as Russian translation-reuse evidence and as a behavior baseline. Do not use it as the structural source for either rich edition.

## WXR-DB — Authoritative storage

WXR-DB-1 — PostgreSQL `wadoku` remains the only source of truth. SQLite may be used only for an emergency recovery copy.

WXR-DB-2 — Create a new immutable source snapshot for the official XML archive. Give the rich rebuild a new run identity. Do not modify completed run 2.

WXR-DB-3 — Export both rich editions only from PostgreSQL. Record every export in the PostgreSQL audit log.

WXR-DB-4 — Store one official XML entry as one `article` row. Store the lossless normalized entry tree and extracted semantic blocks in `article.raw_json`. Do not add a parallel normalized Wadoku source schema for this release.

WXR-DB-5 — Keep the XML entry ID, source path, source text, source hash, and protected tokens for every translation unit.

WXR-DB-6 — Preserve Russian translation attempts, model identity, token use, validation issues, repairs, acceptance time, and export provenance with the pipeline tables. Never delete rejected or interrupted attempts.

WXR-DB-7 — Record the German build as a deterministic source rendering. It has import and export provenance but no model attempt records.

WXR-DB-8 — Link the German and Russian exports to the same canonical XML snapshot so their structures can be compared directly.

WXR-DB-9 — Add migration `0010` in both database migration directories. It adds `wadoku` to `source_snapshot.kind`, adds nullable `run.dictionary_snapshot_id` and unique nullable `run.run_identity_sha256`, and makes the legacy `jitendex_snapshot_id` and `kaishi_snapshot_id` fields nullable. It also adds `run_article.prepared_at`, nullable `attempt.dispatched_at`, `translation.accepted_at`, and `translation.acceptance_method`. Backfill `dictionary_snapshot_id` from `jitendex_snapshot_id` for old runs. A Wadoku run sets `dictionary_snapshot_id` to its Wadoku snapshot and leaves both legacy snapshot fields null.

WXR-DB-10 — `translation_reuse` contains `target_translation_id` as its primary key and translation foreign key, `source_translation_id` as a translation foreign key, `source_segment_index` as an integer, `mapping_rule` as text, and `created_at`. Use segment index `0` and mapping rule `exact_single_block_v1`.

WXR-DB-11 — Migration `0010` also adds the `translation_reuse` table from WXR-DB-10. PostgreSQL is authoritative. The SQLite migration keeps schema compatibility only; production commands must not write to SQLite.

WXR-DB-12 — Compute `run_identity_sha256` from the dictionary snapshot hash, selection hash, extractor version, prompt hash, terminology hash, limits JSON, and pipeline version. `prepare` returns the existing run when this identity already exists. This makes preparation safe to resume without a parallel run.

WXR-DB-13 — Set `run_article.prepared_at` when the canonical article joins the run. Set `translation.accepted_at` exactly once when a result becomes accepted. Set `acceptance_method` to `luna`, `v1_exact_reuse`, `manual`, or `legacy`. Backfill old accepted rows with their translation creation time and method `legacy`; do not claim that this backfill is the original historical acceptance time.

WXR-DB-14 — Treat `attempt.created_at` as claim time, `attempt.dispatched_at` as the real request launch time, and `attempt.completed_at` as terminal time. The Luna runner sets `dispatched_at` exactly once when it launches the request process. An attempt interrupted before launch keeps a null `dispatched_at`. Do not infer dispatch time from stdout or file modification times.

## WXR-MODEL — Canonical entry model

WXR-MODEL-1 — Represent one official XML `<entry>` as one canonical entry object. Generate one or more Yomitan lookup rows from that object only during export.

WXR-MODEL-2 — Preserve every `<orth>` value, its order, `midashigo` state, type, and relation to the entry.

WXR-MODEL-3 — Preserve the hiragana reading, `hatsuon`, every accent value, and their order.

WXR-MODEL-4 — Preserve entry-level and sense-level grammar. This includes part of speech, verb class, godan row, transitivity, adjective properties, particles, prefixes, suffixes, and `suru` behavior.

WXR-MODEL-5 — Preserve ordered senses and ordered translation alternatives inside each sense.

WXR-MODEL-6 — Preserve usage domains, register, time, style, hints, preference, and other usage attributes at their original level.

WXR-MODEL-7 — Preserve definitions, explanations, etymologies, foreign forms, scientific names, dates, counters, seasonal-word data, and external links.

WXR-MODEL-8 — Preserve references by target Wadoku entry ID and sense ID. Keep their types, such as main entry, synonym, antonym, alternative reading, and alternative transcription.

WXR-MODEL-9 — Preserve source order everywhere. Never sort senses, alternatives, forms, or references by their rendered text.

WXR-MODEL-10 — Keep the original XML-derived object available after export so a generated Yomitan row can be traced back to one entry and one source path.

WXR-MODEL-11 — Keep original German semantic blocks in the canonical object even after Russian translations exist. Russian data must never overwrite the German source.

## WXR-IMP — Import implementation

WXR-IMP-1 — Add a streaming XML importer. It must not load the complete XML tree into memory.

WXR-IMP-2 — Add a dedicated pipeline version such as `wadoku-xml-v1`. Do not reuse `extractor-plain-glossary-v1`.

WXR-IMP-3 — Add one `config.wadoku.xml.luna.toml` file and one `work/wadoku-xml` directory. The German export reads the canonical source rows directly. The Russian export overlays accepted translations from the same run. Do not create a second database, run, or configuration for German.

WXR-IMP-4 — Add an import report with source counts, normalized counts, skipped nodes, unsupported nodes, and exact reasons for every skip.

WXR-IMP-5 — Require zero silently skipped entries and zero silently skipped learner-facing text nodes.

WXR-IMP-6 — Add fixture entries for multiple orthographies, multiple senses, nested explanations, references, scientific names, pitch accents, irregular forms, and very large entries.

WXR-IMP-7 — Complete and validate the German Yomitan renderer before creating Russian translation units. This separates XML parsing defects from translation defects.

## WXR-TR — Translation-unit design

WXR-TR-1 — For the Russian edition, translate semantic blocks, not arbitrary XML text fragments. A Russian phrase needs enough German context to remain grammatical.

WXR-TR-2 — Use one stable unit for each ordered translation block, definition, explanation, description, and etymology block that contains German learner text after protected fragments are removed. A block that is fully protected remains shared source structure and does not call Luna.

WXR-TR-3 — Include the headword, reading, part of speech, domain, register, sense position, and nearby alternatives in the Luna context.

WXR-TR-4 — Do not translate Japanese forms, readings, Wadoku IDs, reference targets, numeric accent values, dates, URLs, formulas, or scientific names.

WXR-TR-5 — Replace protected inline fragments with stable placeholders before Luna. Require every placeholder exactly once and in the original order. Reinsert the saved fragment tree during Russian rendering.

WXR-TR-6 — Localize controlled labels through reviewed code tables. Do not ask Luna to translate the same part-of-speech or domain label thousands of times.

WXR-TR-7 — Preserve one output value for every input semantic block. Do not allow Luna to merge blocks or change their order.

WXR-TR-8 — Create a new versioned Luna prompt for the XML structure. Require Russian learner-facing text and exact protected-token preservation.

WXR-TR-9 — Run a 100-request pilot with concurrency 10, startup time 10 seconds, and request timeout 240 seconds. The first 100 batches must cover every block role, blocks with zero, one, and several placeholders, small and large blocks, and every still-pending unit from WXR-SAMPLE. Review the fixed sample and report request failures, validator failures, input and output tokens, latency, and protected-token failures before the full run.

WXR-TR-10 — Use production concurrency 100, startup time 30 seconds, and request timeout 240 seconds after the pilot gates pass. Keep these values unless real rate-limit or timeout evidence requires a recorded change. Use the existing PostgreSQL lease and retry workflow.

WXR-TR-11 — This section applies only to the Japanese-to-Russian edition. The Japanese-to-German edition uses the original semantic blocks directly.

WXR-TR-12 — Every translation manifest uses `schema_version: 2` and `pipeline: "wadoku-xml-v1"`. Its article context contains dictionary name, entry ID, expression, reading, entry grammar, sense path, nearby source blocks, and the unit role. Each unit contains its ID, source hash, exact German source text, protected tokens, and local context.

WXR-TR-13 — Update the shared batch builder to recognize the Wadoku canonical object and pipeline. It must not call Jitendex tag-catalog or Kaishi-evidence logic for a Wadoku run.

## WXR-REUSE — Reuse of V1 translations

WXR-REUSE-1 — Reuse a V1 Russian translation only when an exact and deterministic mapping to one XML semantic block exists.

WXR-REUSE-2 — Normalize only the expression and reading to Unicode NFC. Match on NFC expression, NFC reading, and exact German source text. Do not trim, case-fold, change whitespace, or normalize the German text. Never match on a Japanese headword alone.

WXR-REUSE-3 — Decode the V1 source JSON and require an array containing exactly one German string with no newline. Decode the V1 target JSON and require an array containing exactly one non-empty Russian string. Require one unique new block with byte-identical plain German `source_text` and no protected placeholder.

WXR-REUSE-4 — Reject every multi-block or multi-target V1 candidate. The V1 prompt allowed a variable-length Russian definition list, so equal item counts do not prove item-to-block alignment.

WXR-REUSE-5 — Send ambiguous mappings to Luna. Do not split an old Russian paragraph heuristically and call it accepted.

WXR-REUSE-6 — Record reused units with their V1 run, translation ID, source hash, and mapping rule.

WXR-REUSE-7 — Report the exact reuse rate before the full Luna run. Use it to estimate remaining requests and cost.

WXR-REUSE-8 — Exclude every duplicate reuse key before matching, even when its Russian targets agree. Run 2 currently has 107 duplicate key groups covering 216 rows; 41 groups contain conflicting targets.

WXR-REUSE-9 — Preserve the original accepted attempt ID when a target is reused, calculate the new target hash, and insert its `translation_reuse` row. Do not create a fake Luna attempt.

WXR-REUSE-10 — The run 2 audit found 251,866 one-German-string and one-Russian-string rows, which is a 43.727% structural upper bound before XML matching. The 382,680 equal-cardinality rows and 66.439% figure are diagnostics only, not reusable candidates. Duplicate keys, changed XML, repeated blocks, and placeholders reduce the real reuse rate further.

## WXR-YOM — Rich Yomitan mapping

WXR-YOM-1 — Generate a lookup row for each searchable written form. Give all rows from one XML entry the same sequence ID.

WXR-YOM-2 — Set `sequenced` in `index.json` and use the Wadoku entry ID as the sequence value so related rows can merge.

WXR-YOM-3 — Use definition tags only for grammar and labels that apply to the complete entry. Render sense-specific domain, register, history, and usage labels inside their own sense so they do not appear to apply to other senses.

WXR-YOM-4 — Add a German tag bank for the German edition and a Russian tag bank for the Russian edition. Both tag banks must use the same stable tag codes.

WXR-YOM-5 — Set Yomitan deinflection `rules` to an empty string in the first rich release. Show Wadoku grammar in tags and structured content. Add deinflection mappings only in a later audited change.

WXR-YOM-6 — Use structured-content definitions. Render each sense as a separate section and keep ordered translation alternatives inside it.

WXR-YOM-7 — Render definitions, explanations, etymologies, scientific names, dates, and notes with visible labels. Do not concatenate them without separators.

WXR-YOM-8 — Render cross-references with their type and Japanese target text. Keep them as visible text in the first rich release. Do not add an unverified internal-link format.

WXR-YOM-9 — Set every row score to `0`. Use stable term tags for observed orthography types and primary or related-form status. Do not invent ranking weights.

WXR-YOM-10 — Generate a term metadata bank for pitch accent and supported pronunciation details. Keep unsupported `hatsuon` notation visible in structured content.

WXR-YOM-11 — Include the official source date, source URL, license, attribution, pipeline version, target language, and edition revision in each `index.json`.

WXR-YOM-12 — Put at most 10,000 rows in each term, term-metadata, and tag bank. Order output by XML entry order and then source form order. Use a fixed ZIP timestamp of 1980-01-01 so equal inputs produce byte-identical archives.

WXR-YOM-13 — Use one shared mapping from canonical XML structure to Yomitan structure for both language editions.

WXR-YOM-14 — The German renderer must reproduce the original German block text deterministically. The Russian renderer must replace only the corresponding learner-facing block values.

## WXR-VAL — Validation gates

WXR-VAL-1 — Validate the XML against its XSD. After import, require matching counts for entries, forms, readings, senses, and learner-facing semantic blocks. This catches source loss.

WXR-VAL-2 — Require every German semantic block to appear exactly once in the German rendering. This catches missing or duplicated dictionary content.

WXR-VAL-5 — Require one accepted Russian result for every translatable unit, zero unresolved blocking issues, non-empty Russian text, intact protected tokens, a Russian label for every observed controlled code or value, and one complete article-log record for every run article.

WXR-VAL-7 — Validate both final archives against the pinned Yomitan schemas and test their ZIP integrity.

WXR-VAL-10 — Install both candidate ZIPs in Yomitan. Check a small fixed sample for lookup, merged senses, tags, references, and pitch accent.

WXR-VAL-12 — Create and validate a final PostgreSQL dump before release.

WXR-VAL-14 — Compare the German and Russian archives structurally. Their entry IDs, forms, readings, senses, references, and pronunciation metadata must match.

## WXR-QA — Language review

WXR-QA-1 — Review a small fixed sample with simple entries, complex entries, and low-confidence Russian translations.

WXR-QA-2 — Review localized grammar, domain, and register labels once as controlled terminology.

WXR-QA-4 — Record Russian corrections as explicit replacement targets with an audit trail. Do not weaken validators to accept model errors.

## WXR-PHASE — Delivery phases

WXR-PHASE-1 — Phase A creates the source snapshot, streaming importer, canonical entry model, and structural inventory report.

WXR-PHASE-2 — Phase B creates and validates `dist/wadoku-jp-de-rich.zip` with original German text. This proves the complete structure before Luna work starts.

WXR-PHASE-3 — Phase C defines Russian translation units, controlled Russian labels, the Luna prompt, and validators.

WXR-PHASE-4 — Phase D measures safe V1 reuse and imports only mappings that pass the exact rules in WXR-REUSE.

WXR-PHASE-5 — Phase E runs the bounded pilot, then runs the remaining Luna batches through PostgreSQL, repairs failures, and accepts deterministic results.

WXR-PHASE-6 — Phase F exports `dist/wadoku-jp-ru-rich.zip`, runs all structural and language gates, and creates the final PostgreSQL dump.

WXR-PHASE-7 — Phase G runs the final Yomitan smoke tests, records known limitations, and releases both editions.

## WXR-DONE — Completion criteria

WXR-DONE-1 — The official XML archive and its SHA-256 are recorded and restorable.

WXR-DONE-2 — PostgreSQL contains every official entry, form, reading, sense, German semantic block, and metadata node defined in WXR-BLOCK and WXR-CANON with stable provenance.

WXR-DONE-3 — PostgreSQL contains an accepted Russian result for every learner-facing German unit.

WXR-DONE-4 — Both rich ZIPs preserve the same sense boundaries, grammar, usage, forms, references, pronunciation, and pitch data.

WXR-DONE-5 — The essential source-fidelity, Russian coverage, Yomitan schema, ZIP integrity, structural parity, and smoke checks all pass.

WXR-DONE-6 — `reports/wadoku_xml_rich_release.md` states the source SHA-256, core counts, reuse counts, Luna request and failure counts, both archive SHA-256 values, schema hashes, smoke result, and PostgreSQL backup SHA-256.

WXR-DONE-7 — Both release archives are exported only from PostgreSQL. Russian V1 and the emergency SQLite evidence remain unchanged.

WXR-DONE-8 — The German archive renders the canonical source directly and contains no Luna-generated text.

WXR-DONE-9 — PostgreSQL can reconstruct how every semantic block was handled, including source preservation, exact V1 reuse, every Luna attempt, retries, usage, validation, acceptance, and manual correction with UTC timestamps.

WXR-DONE-10 — The final deterministic article log and its summary exist under `work/wadoku-xml/logs`; their SHA-256 values appear in `reports/wadoku_xml_rich_release.md`.

## WXR-ENV — Required environment

WXR-ENV-1 — Run all commands from `/Users/iuriikatkov/Documents/ChatGPT/jitendex-translations` with the repository `.venv`. The operator must provide a working `WADOKU_POSTGRES_URL` for database `wadoku` and normal Codex access for `gpt-5.6-luna`. These two credentials are the only external state this plan assumes.

WXR-ENV-2 — The PostgreSQL container used by the backup commands is `jitendex-postgres`. Its database is `wadoku` and its database user is `jitendex`.

WXR-ENV-3 — Before a write, confirm that `WADOKU_POSTGRES_URL` is present, points to database `wadoku`, migrations are current, the full test suite passes, and no other Wadoku batch runner holds leases. Never print the database password.

## WXR-FILES — Files to add or change

WXR-FILES-1 — Add `src/jitendex_ru/wadoku_xml.py`. It owns streaming XML parsing, lossless node conversion, semantic-block extraction, controlled-label rendering, exact V1 reuse mapping, structured-content rendering, and structural comparison.

WXR-FILES-2 — Add `scripts/wadoku_xml_dictionary.py`. It exposes the commands `db-check`, `prepare`, `source-report`, `export-de`, `reuse-v1`, `make-batches`, `pilot-check`, `export-checkpoint`, `progress`, `article-log`, `failures`, `replace-target`, `export-ru`, `verify-de`, `verify-ru`, and `compare`.

WXR-FILES-3 — Add `config.wadoku.xml.luna.toml`. Base it on `config.wadoku.luna.toml`, set pipeline version `wadoku-xml-v1`, translation-prompt version `translate-luna-wadoku-xml-ru-v1`, target language `ru`, work directory `work/wadoku-xml`, model `gpt-5.6-luna`, reasoning effort `medium`, batch limits 100 units and 24,576 bytes, and request timeout 240 seconds. Add explicit paths for `terminology/ru-v1.json` and `terminology/wadoku-xml-labels-v1.json`. This run has no review phase; store the SHA-256 of empty bytes in the required legacy review-prompt field.

WXR-FILES-4 — Add `prompts/translate_luna_wadoku_xml_ru_v1.txt`. It uses the existing response schema version 2 and requires one scalar `target_text` for each input semantic block. It must forbid block merging, block reordering, and protected-token changes.

WXR-FILES-5 — Add `terminology/wadoku-xml-labels-v1.json`. It holds reviewed German and Russian labels for section names, parts of speech, reference types, orthography types, usage categories, every distinct `usg` text value, and other structural codes. Each item has stable `code`, `category`, integer `order`, `de`, and `ru` fields. Preparation fails when an observed controlled code or value has no complete item.

WXR-FILES-6 — Add `tests/test_wadoku_xml.py` and small XML fixtures. Cover multiple forms, senses, nested explanations, references, names, scientific names, dates, pitch, devoicing, unknown structural codes, protected text, large entries, deterministic ZIP output, strict one-to-one reuse, rejected multi-block reuse, article-log completeness, claim-dispatch-completion timestamp order, and progress calculations.

WXR-FILES-7 — Add PostgreSQL and SQLite migration `0010` described in WXR-DB-9 through WXR-DB-14. Add the four pinned Yomitan schemas described in WXR-SCHEMA if they are not already present.

WXR-FILES-8 — Update `src/jitendex_ru/batch.py` to use `dictionary_snapshot_id` for a Wadoku run, build the context in WXR-TR-12, identify manifest pipeline `wadoku-xml-v1`, emit schema version 2, and skip Jitendex and Kaishi lookups.

WXR-FILES-9 — Update `src/jitendex_ru/validate_response.py` to accept schema version 2 for pipeline `wadoku-xml-v1`. Apply the existing scalar checks, require every protected token, and allow a non-Cyrillic unchanged target only when removing protected tokens leaves no translatable source text.

WXR-FILES-10 — Update `src/jitendex_ru/schema_validation.py` to validate index, term, term-metadata, and tag banks with their four pinned schemas. Return counts for every bank type and fail on an unknown or missing required bank sequence.

WXR-FILES-11 — Update `src/jitendex_ru/run_integrity.py` so progress uses `dictionary_snapshot_id` when `jitendex_snapshot_id` is null. The existing Luna runner must then report Wadoku headword, article, and unit progress correctly.

WXR-FILES-12 — Update `src/jitendex_ru/cli.py` so `validate` treats review as not required only for pipeline `wadoku-xml-v1`. For this pipeline, `release_ready` requires all units accepted, zero blocking issues, and zero batch-membership mismatches.

WXR-FILES-13 — Create `reports/wadoku_xml_rich_release.md` at release time and `reports/wadoku_xml_rich_smoke.json` during the manual check. The release report includes the final article-log paths and hashes. Both follow the repository documentation-ID rules where Markdown applies.

WXR-FILES-14 — Update deterministic acceptance in `src/jitendex_ru/jpdb_scope.py` so newly accepted model results set `accepted_at` once and set acceptance method `luna`. It must preserve `v1_exact_reuse` and `manual` values already present.

WXR-FILES-15 — Update `scripts/run_codex_batches.py` so the launch callback stores `attempt.dispatched_at` and a matching audit event when the request process starts. The write is idempotent and never replaces an existing dispatch time.

## WXR-LOG — Article translation usage log

WXR-LOG-1 — PostgreSQL is the authoritative log. Reconstruct the ledger from `run`, `run_article`, `article`, `translation_unit`, `batch_item`, `batch`, `attempt`, `translation`, `translation_reuse`, `validation_issue`, `translation_canonicalization_history`, and `audit_event`. Do not maintain a second mutable ledger that can diverge.

WXR-LOG-2 — `article-log` writes one canonical JSON object per run article in source entry order. It writes `work/wadoku-xml/logs/run-<run-id>-articles.jsonl.gz` with gzip timestamp zero and `work/wadoku-xml/logs/run-<run-id>-summary.json`. A partial run may produce a partial snapshot with status `in_progress`; only the complete snapshot is a release artifact.

WXR-LOG-3 — Every article object contains schema version, run ID, source snapshot hash, Wadoku entry ID, database article ID, expression, reading, source ordinal, run creation time, article preparation time, current status, completion time, unit counts, and ordered `blocks` and `attempts` arrays.

WXR-LOG-4 — Every semantic block records block index, XML path, role, plain German source hash, prompt-text hash, target hash, translation ID, acceptance time, confidence, and final method. The final method is exactly one of `source_preserved`, `v1_exact_reuse`, `luna`, or `manual_replacement`.

WXR-LOG-5 — A reused block records source run ID, source translation ID, source attempt ID, mapping rule, reuse timestamp, and both source hashes. A Luna block records the accepted attempt ID. A manually corrected block records every canonicalization-history row with actor, reason, previous hash, replacement hash, and timestamp.

WXR-LOG-6 — The article `attempts` array contains every current-run attempt for a batch that included the article, including rejected and interrupted attempts. Record batch ID, attempt ID, worker ID, configured model, effective model, reasoning effort, transport, prompt hash, API request identifiers, claim time, dispatch time, completion time, outcome, finish reason, status reason, latency, input tokens, cached input tokens, output tokens, total tokens, and sanitized error details.

WXR-LOG-7 — Token usage belongs to a complete batch attempt. Mark it with `usage_scope: "batch_attempt"`. Do not divide tokens among articles or blocks and present the result as exact usage.

WXR-LOG-8 — Store database timestamps in UTC and serialize them as RFC 3339 with a `Z` suffix. Never replace a real timestamp with file modification time. For legacy accepted rows, label the `created_at` backfill described in WXR-DB-13 as estimated.

WXR-LOG-9 — An article is complete when all of its translatable units are accepted. Its completion time is the latest acceptance time. An article with no translatable unit is complete at `run_article.prepared_at` with method `source_preserved`. An article with any pending or failed unit has null completion time.

WXR-LOG-10 — Include all validation issue codes, creation times, resolution times, and waiver reasons related to the article or its attempts. Never hide a resolved problem from the log.

WXR-LOG-11 — Exclude database URLs, passwords, lease tokens, and raw authentication data. Local request and response paths may appear only as repository-relative paths. Keep source and target dictionary text because it is required to audit translation decisions.

WXR-LOG-12 — `article-log` validates one record per `run_article`, unique entry and article IDs, complete block coverage, valid method provenance, and legal timestamp order. In particular, claim time cannot follow dispatch time, and dispatch time cannot follow completion time. It records the output paths, record count, gzip SHA-256, uncompressed-content SHA-256, summary SHA-256, and status in `audit_event`.

WXR-LOG-13 — The summary records article and block totals by final method, attempt totals by outcome, both failure percentages from WXR-REPORT-6, token totals, terminal attempts with missing usage, first and last event times, incomplete-record count, run status, and the hashes from WXR-LOG-12.

## WXR-REPORT — Orchestrator progress reporting

WXR-REPORT-1 — The orchestrator treats PostgreSQL as the metric source. Runner stdout is useful live evidence but never overrides database counts. The `progress` command reads one consistent snapshot and adds an `orchestrator_progress` audit event with the same UTC timestamp and metrics.

WXR-REPORT-2 — At the start of every WXR-CMD step, report the visible step ID, run ID when known, action, expected output, configured concurrency, step start time, and total run start time. Do this before starting a long command.

WXR-REPORT-3 — While Luna requests are active, send the user a compact progress update at least once every 60 seconds. Also report immediately when the pilot finishes, concurrency changes, a usage or authentication boundary appears, PostgreSQL fails, failures rise sharply, a checkpoint ZIP is ready, a step finishes, or user action is required.

WXR-REPORT-4 — Every live update contains UTC timestamp, step ID, run ID, step elapsed time, run elapsed time, active requests and configured limit, live-complete articles, remaining articles, article percentage, live-complete units, remaining units, exact V1-reused units, Luna-complete units, accepted attempts, rejected attempts, interrupted attempts, failed-query percentage, validation-rejection percentage, rolling throughput, cumulative tokens, and ETA when stable.

WXR-REPORT-5 — A live-complete unit is accepted or belongs to a `deterministic_validated` batch. A live-complete article has no other translatable unit. This definition includes accepted V1 reuse and source-preserved articles. Label these numbers `live translated`; do not describe them as finally accepted until WXR-CMD-10 passes.

WXR-REPORT-6 — Compute failed-query percentage as `rejected attempts / (accepted attempts + rejected attempts) × 100`. Exclude claimed and interrupted attempts from this denominator and show interrupted attempts separately. Compute validation-rejection percentage with the same denominator, but count only rejected attempts that have deterministic validation issues.

WXR-REPORT-7 — Show cumulative input, cached-input, output, and total tokens from completed attempts. Do not count missing usage as zero; report the number of terminal attempts whose usage is still missing.

WXR-REPORT-8 — Compute rolling throughput from database snapshots over the latest five complete minutes. Show an ETA only after at least ten minutes and 500 terminal attempts, and only when the rolling rate is positive. Mark the ETA as an estimate and remove it when the rate becomes unstable.

WXR-REPORT-9 — Write `Snapshot: <RFC 3339 UTC>` and then format the user update as this compact table. Follow it with at most two short sentences about a milestone, incident, or next action.

| Step | Run | Step time | Run time | Active | Articles done / left | Units done / left | Failed queries | Validation rejects | Rate | ETA |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `WXR-CMD-9` | `<id>` | `<elapsed>` | `<elapsed>` | `<active>/100` | `<done> / <left> (<pct>)` | `<done> / <left>` | `<bad>/<terminal> (<pct>)` | `<bad>/<terminal> (<pct>)` | `<articles/min>` | `<estimate or —>` |

WXR-REPORT-10 — Do not report success from a process exit code alone. After each step, query PostgreSQL, state the exact output or invariant that passed, and name the next step. If a command failed, state the last durable database state and whether automatic retry is safe.

WXR-REPORT-11 — If the user asks for status, answer from a new PostgreSQL snapshot immediately. Include the snapshot time and do not reuse an older table from memory.

WXR-REPORT-12 — The final user report states the run ID, total elapsed time, exact reuse count and percentage, Luna request outcomes and failure percentages, token totals, accepted article and unit counts, final ZIP paths and hashes, article-log paths and hashes, PostgreSQL dump hash, smoke result, and any remaining limitation. Say `complete` only after every WXR-VAL gate passes.

WXR-REPORT-13 — Use these commands for a long step. `start` creates the step timer, each `snapshot` reads and stores current metrics, and `finish` records the verified end state. The orchestrator converts the returned JSON to the WXR-REPORT-9 table.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py progress \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --step-id WXR-CMD-9 --event start
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py progress \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --step-id WXR-CMD-9 --event snapshot
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py progress \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --step-id WXR-CMD-9 --event finish
```

## WXR-CANON — Canonical PostgreSQL contract

WXR-CANON-1 — `article.expression` is the first `<orth>` whose `midashigo` is not `true`. If none exists, use the first `<orth>`. `article.reading` is the first `<hira>`. `article.sequence` is the integer official entry ID. `entry_ordinal` is the source document order.

WXR-CANON-2 — `article.raw_json` has this top-level shape. The implementation must version the shape and reject unknown versions.

```json
{
  "schema_version": 1,
  "namespace": "http://www.wadoku.de/xml/entry",
  "entry_id": 123,
  "tree": {},
  "blocks": []
}
```

WXR-CANON-3 — Every node in `tree` has `tag`, `attributes`, `text`, `tail`, and `children`. `tag` is the namespace-free XML name. Attribute keys use stable sorted order. `text` and `tail` are exact strings, including whitespace. `children` remain in XML order. This tree preserves all elements, attributes, mixed text, and ordering even when the first renderer does not give a node special styling.

WXR-CANON-4 — Every object in `blocks` has `xml_path`, `role`, `sense_path`, `source_text`, `prompt_text`, `protected_fragments`, and `has_translatable_text`. `source_text` is the complete plain German rendering used for fidelity and V1 matching. `prompt_text` contains protected placeholders. Each protected-fragment object has `placeholder`, `text`, and its lossless `tree`. `role` is one of `translation`, `definition`, `explanation`, `etymology`, or `description`. `sense_path` is null for entry-level blocks. The array follows XML document order.

WXR-CANON-5 — Create one `translation_unit` for every block whose `has_translatable_text` is true. Its JSON pointer is `/blocks/N`. Store `prompt_text` as unit source text, its hash as unit source hash, and the ordered placeholders as protected tokens. Keep the plain source and XML path in the block. Use unit ID `wdx-` plus the first 32 hexadecimal characters of SHA-256 over archive SHA, NUL, entry ID, NUL, role, NUL, XML path, NUL, and prompt-text SHA.

WXR-CANON-6 — The original archive remains the byte-level source record. The generic JSON tree is the queryable lossless record. The importer must round-trip the semantic text and structural counts; it does not need to reproduce the original XML byte formatting.

WXR-CANON-7 — Use a dedicated rich exporter. Do not pass these articles through the old flat `materialize_run` or `apply_article` path because that path cannot preserve the XML structure.

WXR-CANON-8 — Select every imported Wadoku article. Set `selection_sha256` to SHA-256 of the UTF-8 string `wadoku-all:` followed by the pinned archive SHA-256. Set `terminology_sha256` to SHA-256 of canonical JSON containing the two terminology-file hashes from WXR-FILES-3. No frequency or Kaishi scope participates in this run.

WXR-CANON-9 — Insert one `run_article` row for every imported article. Its structural fingerprint covers the canonical tree but excludes Russian target text. This lets runner progress and structural parity use the existing run boundary.

WXR-CANON-10 — Insert one `source_snapshot` with kind `wadoku`, version `2026-07-05`, the exact archive URL and hash, archive local path, and extractor version `wadoku-xml-v1`. Store the XML, XSD, and license hashes, byte sizes, and source counts in `metadata_json`.

## WXR-BLOCK — Semantic block and label rules

WXR-BLOCK-1 — Select the outermost `tr`, `def`, `expl`, `etym`, and `descr` elements as learner-facing semantic blocks. When one selected element appears inside another selected element, create only the outer unit. This prevents duplicate text.

WXR-BLOCK-2 — Render a block source recursively in document order. Respect Wadoku `hasPrecedingSpace` and `hasFollowingSpace` attributes. Do not trim or collapse whitespace after the canonical source string is made.

WXR-BLOCK-3 — Protect Japanese forms and readings, pronunciation strings, romanization, accent numbers, foreign forms, scientific names, special characters, dates, reference targets, URLs, Steinhaus notation, wiki targets, related Japanese terms, and titles. The protected element names are `orth`, `hira`, `hatsuon`, `romaji`, `accent`, `jap`, `transcr`, `foreign`, `scientif`, `specchar`, `birthdeath`, `date`, `ref`, `sref`, `link`, `steinhaus`, `wikide`, `wikija`, `ruigo`, and `title`. Number placeholders inside each block as `⟦WDXP0001⟧`, `⟦WDXP0002⟧`, and so on. Preparation fails if source text already contains this pattern.

WXR-BLOCK-4 — Localize structural codes and repeated values from `gramGrp`, `usg`, `seasonword`, `count`, orthography types, reference types, and relevant attributes through `terminology/wadoku-xml-labels-v1.json`. Translate each distinct label once and reuse it. Do not ask Luna to translate the same metadata value for every entry.

WXR-BLOCK-5 — If visible German learner text exists outside a selected block, a protected element, or a known controlled-label node, preparation fails with the entry ID and XML path. Do not add a generic silent fallback.

WXR-BLOCK-6 — The German renderer reads every block from its original subtree. The Russian renderer reads accepted `target_text`, verifies its placeholder sequence, and replaces each placeholder with the saved protected subtree. A fully protected block uses the source subtree in both editions. Both render grammar, usage, references, pronunciation, and other structural metadata from the shared tree.

## WXR-ROW — Exact Yomitan row contract

WXR-ROW-1 — Emit Yomitan term-bank version 3 rows as `[expression, reading, definitionTags, rules, score, glossary, sequence, termTags]`. `expression`, `reading`, and `sequence` follow WXR-CANON-1. `rules` is an empty string, `score` is zero, and `glossary` contains one structured-content object for the full entry.

WXR-ROW-2 — Emit a lookup row for every distinct `<orth>` whose `midashigo` is not `true`. If that set is empty, emit rows for all distinct `<orth>` values. Keep original form order. Show every main and related form inside the structured entry even when it is not a lookup row.

WXR-ROW-3 — Render each sense as a visible ordered section. Keep alternatives, definitions, explanations, etymologies, labels, and references at their original entry or sense level. The German and Russian render trees must have the same node shape; only semantic block text and controlled label strings can differ.

WXR-ROW-4 — Emit pitch rows to `term_meta_bank_N.json` as `[term, "pitch", {"reading": reading, "pitches": [{"position": position}]}]`. Emit one pitch object for each `<accent>` integer. When `[Dev]` can be mapped safely from `hatsuon`, add one-based mora position `devoice`; otherwise keep the notation visible in structured content.

WXR-ROW-5 — Set `format` to `3`, `sequenced` to true, `sourceLanguage` to `ja`, and the correct target language in `index.json`. Put the official source URL in `url`, license and Wadoku credit in `attribution`, and source date, archive hash, pipeline version, edition revision, and PostgreSQL export audit ID in `description`. Include the original `LICENSE` as a separate archive member.

WXR-ROW-6 — Emit tag-bank version 3 rows as `[code, category, order, notes, score]`. Use stable codes shared by both editions, categories `grammar`, `usage`, `orthography`, or `reference`, explicit order from the terminology file, localized notes, and score zero.

WXR-ROW-7 — The one glossary item has shape `{"type":"structured-content","content":{"tag":"div","content":[...]}}`. The root content shows forms, reading and pronunciation, entry grammar, an ordered-list `ol` of senses, and entry-level references or notes in that order.

WXR-ROW-8 — Render every sense as one `li`. Inside it, keep source order and use `div` containers for usage labels, alternatives, definitions, explanations, etymology, and references. Put the localized section label in a `span` with `style.fontWeight` set to `bold`, then the value. Mark German learner text with `lang: "de"`, Russian learner text with `lang: "ru"`, and Japanese text with `lang: "ja"`. Use only schema-supported `div`, `span`, `ol`, `ul`, and `li` tags and inline style; do not add a CSS file.

## WXR-SCHEMA — Pinned archive schemas

WXR-SCHEMA-1 — Pin Yomitan schemas to commit `77e200428902abf4fa48284df92da7af3dcb4162`. Copy `dictionary-index-schema.json`, `dictionary-term-bank-v3-schema.json`, `dictionary-term-meta-bank-v3-schema.json`, and `dictionary-tag-bank-v3-schema.json` from `ext/data/schemas` into the repository schema directory. Record their hashes in the implementation report.

WXR-SCHEMA-2 — Validate every generated JSON file against its matching pinned schema before ZIP creation. Then test the ZIP central directory, required filenames, consecutive bank numbering, UTF-8 JSON decoding, and archive SHA-256.

WXR-SCHEMA-3 — The schema source base is `https://raw.githubusercontent.com/yomidevs/yomitan/77e200428902abf4fa48284df92da7af3dcb4162/ext/data/schemas/`. Download only the four filenames in WXR-SCHEMA-1.

## WXR-REUSE-ALG — Exact reuse procedure

WXR-REUSE-ALG-1 — Read only accepted run 2 translations from PostgreSQL. Join each result to its V1 article expression and reading, complete German source string, complete Russian target list, source translation ID, accepted attempt ID, and source hash.

WXR-REUSE-ALG-2 — Build the candidate key from NFC expression, NFC reading, and the exact full German source string. Remove every key that has more than one database row before any mapping attempt.

WXR-REUSE-ALG-3 — Require old source shape `[german]`, no newline in `german`, and old target shape `[russian]`. Require exactly one new block whose plain `source_text` equals `german` byte for byte. Exclude duplicate old keys, repeated matching blocks, and blocks with protected placeholders.

WXR-REUSE-ALG-4 — For each passing pair, insert the one Russian target for the one new block, retain the old accepted attempt ID as origin evidence, compute the target hash, set acceptance method `v1_exact_reuse`, and add the `translation_reuse` row. Every unmatched block remains pending for Luna.

WXR-REUSE-ALG-5 — `reuse-v1` runs as a report-only command unless `--apply` is present. Its JSON report includes source rows, unique keys, duplicate exclusions, source-cardinality mismatches, new-XML mismatches, reusable blocks, applied blocks, and remaining blocks. Re-running `--apply` must make no additional changes.

## WXR-SAMPLE — Fixed review sample

WXR-SAMPLE-1 — Use `インスリン` to check etymology, usage, pitch, pronunciation, and references.

WXR-SAMPLE-2 — Use `ころっと` to check several senses and ordered alternatives.

WXR-SAMPLE-3 — Use `人買い` to check multiple written forms and senses.

WXR-SAMPLE-4 — Use `バーン･ジョーンズ` to check a proper name, definition, foreign text, and dates.

WXR-SAMPLE-5 — Use `暴食` to check register labels and references.

WXR-SAMPLE-6 — Use `三ケ日人骨` to check a definition, reference, and entry metadata.

## WXR-CMD — Execution commands

WXR-CMD-1 — Create the working source directory, download the pinned archive, verify it, extract it, and validate the XML before database writes.

```bash
cd /Users/iuriikatkov/Documents/ChatGPT/jitendex-translations
mkdir -p work/wadoku-xml/source
curl -fL https://www.wadoku.de/downloads/xml-export/wadoku-xml-20260705.tar.xz \
  -o work/wadoku-xml/source/wadoku-xml-20260705.tar.xz
test "$(stat -f '%z' work/wadoku-xml/source/wadoku-xml-20260705.tar.xz)" = '25697088'
echo '1028ad3e5d64a14097ae0fef47e57d4615238028b1d0315dbe28082660f07d3b  work/wadoku-xml/source/wadoku-xml-20260705.tar.xz' | shasum -a 256 -c -
tar -xf work/wadoku-xml/source/wadoku-xml-20260705.tar.xz \
  -C work/wadoku-xml/source
echo '0d6309c00f6735db8d27b79cda8b54b76b0ccdd6422d3345c53b48ed0c69d70e  work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml' | shasum -a 256 -c -
echo 'ecee955186f50b055687c3992761b0bf6fe9e84804c4b3543a7e2172955ca04f  work/wadoku-xml/source/wadoku-xml-20260705/entry.xsd' | shasum -a 256 -c -
echo '3e2793cb6b21879abef92003268699e61c98b96462969a2d6d0d9ef388ba051a  work/wadoku-xml/source/wadoku-xml-20260705/LICENSE' | shasum -a 256 -c -
xmllint --noout \
  --schema work/wadoku-xml/source/wadoku-xml-20260705/entry.xsd \
  work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml
```

WXR-CMD-1A — After the importer exists, create a read-only source and label inventory. Fill every missing `de` and `ru` value in the terminology file, then rerun this command until `missing_controlled_labels` is zero. This command must also match every count in WXR-SRC-7.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py source-report \
  --source work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml \
  --xsd work/wadoku-xml/source/wadoku-xml-20260705/entry.xsd \
  --labels terminology/wadoku-xml-labels-v1.json \
  --output work/wadoku-xml/source-report.json
```

WXR-CMD-2 — Check the environment, database identity, active leases, and repository before the first write. At this point `db-check` accepts schema version 9 or 10, but it must fail if the URL does not name database `wadoku` or any Wadoku batch lease is active.

```bash
test -n "$WADOKU_POSTGRES_URL"
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py db-check \
  --config config.wadoku.xml.luna.toml
```

WXR-CMD-3 — Create a recoverable pre-run PostgreSQL dump. Copy it out of the container, list its contents, and record its hash.

```bash
mkdir -p work/backups
docker exec jitendex-postgres pg_dump -U jitendex -d wadoku -Fc \
  -f /tmp/wadoku-xml-before-run.dump
docker exec jitendex-postgres pg_restore -l \
  /tmp/wadoku-xml-before-run.dump >/dev/null
docker cp jitendex-postgres:/tmp/wadoku-xml-before-run.dump \
  work/backups/wadoku-xml-before-run.dump
shasum -a 256 work/backups/wadoku-xml-before-run.dump
```

WXR-CMD-3A — Apply migration `0010` only after WXR-CMD-3 succeeds. Then require schema version 10 and rerun the complete test suite against the migrated code.

```bash
PYTHONPATH=src .venv/bin/translationctl \
  --config config.wadoku.xml.luna.toml init-db
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py db-check \
  --config config.wadoku.xml.luna.toml --require-schema-version 10
PYTHONPATH=src .venv/bin/python -m pytest -q
```

WXR-CMD-4 — Prepare the new immutable source snapshot and run. The command prints the new run ID and JSON source report. Do not assume the run ID is 3. Set `WADOKU_XML_RUN_ID` to the printed value for all later commands.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py prepare \
  --config config.wadoku.xml.luna.toml \
  --archive work/wadoku-xml/source/wadoku-xml-20260705.tar.xz \
  --source work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml \
  --xsd work/wadoku-xml/source/wadoku-xml-20260705/entry.xsd \
  --license work/wadoku-xml/source/wadoku-xml-20260705/LICENSE
export WADOKU_XML_RUN_ID='<printed run id>'
```

WXR-CMD-5 — Export and verify the German edition before translation work.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py export-de \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --output dist/wadoku-jp-de-rich.zip
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py verify-de \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --archive dist/wadoku-jp-de-rich.zip
```

WXR-CMD-6 — Report V1 reuse, review the report, and then apply the exact matches. The second apply run must report zero new inserts.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py reuse-v1 \
  --config config.wadoku.xml.luna.toml --source-run-id 2 \
  --target-run-id "$WADOKU_XML_RUN_ID"
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py reuse-v1 \
  --config config.wadoku.xml.luna.toml --source-run-id 2 \
  --target-run-id "$WADOKU_XML_RUN_ID" --apply
```

WXR-CMD-7 — Create only 100 representative pilot batches from the remaining pending units. The Wadoku command loads the explicit terminology paths from its configuration and calls the shared batch packer. It writes exact batch and unit IDs plus coverage to the pilot-selection report. Do not create full-run batches yet.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py make-batches \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --max-articles 100 --max-units 100 --max-bytes 24576 \
  --pilot-batches 100 --pilot-only \
  --pilot-output work/wadoku-xml/pilot-selection.json
```

WXR-CMD-8 — Run the bounded pilot. Then require every selected pilot unit to have a deterministic valid target and review WXR-SAMPLE. Stop here unless `pilot-check` and the human review pass.

```bash
PYTHONPATH=src .venv/bin/python scripts/run_codex_batches.py \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --kind translation --concurrency 10 --worker-prefix wadoku-xml-pilot \
  --max-submissions 100 --startup-seconds 10 \
  --request-timeout-seconds 240 --progress-interval 30
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py pilot-check \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --selection work/wadoku-xml/pilot-selection.json \
  --output work/wadoku-xml/pilot-result.json
```

WXR-CMD-8A — After the pilot passes, create normal batches for every remaining ready unit.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py make-batches \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --max-articles 100 --max-units 100 --max-bytes 24576
```

WXR-CMD-8B — Accept the pilot results and export the accepted-only pilot checkpoint from PostgreSQL. `export-checkpoint` chooses a deterministic filename that contains the run ID and accepted complete-entry count.

```bash
PYTHONPATH=src .venv/bin/translationctl \
  --config config.wadoku.xml.luna.toml accept-translations \
  --run-id "$WADOKU_XML_RUN_ID"
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py export-checkpoint \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --output-dir work/wadoku-xml/checkpoints
```

WXR-CMD-9 — Run the remaining production batches after the pilot passes. Wrap this step with WXR-REPORT-13 and send the WXR-REPORT-9 user table at the required cadence while the runner is active.

```bash
PYTHONPATH=src .venv/bin/python scripts/run_codex_batches.py \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --kind translation --concurrency 100 --worker-prefix wadoku-xml-full \
  --startup-seconds 30 --request-timeout-seconds 240 \
  --progress-interval 30
```

WXR-CMD-10 — Accept deterministic valid results and run database validation. Require `accepted_units` to equal `units`, `blocking_issues` and `batch_membership_mismatches` to equal zero, and `release_ready` to be true. Pipeline `wadoku-xml-v1` does not require separate review rows.

```bash
PYTHONPATH=src .venv/bin/translationctl \
  --config config.wadoku.xml.luna.toml accept-translations \
  --run-id "$WADOKU_XML_RUN_ID"
PYTHONPATH=src .venv/bin/translationctl \
  --config config.wadoku.xml.luna.toml validate \
  --run-id "$WADOKU_XML_RUN_ID"
```

WXR-CMD-11 — Export Russian only after WXR-CMD-10 passes. Verify both archives and compare their structures.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py export-ru \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --output dist/wadoku-jp-ru-rich.zip
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py verify-ru \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --archive dist/wadoku-jp-ru-rich.zip
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py compare \
  --de dist/wadoku-jp-de-rich.zip --ru dist/wadoku-jp-ru-rich.zip
```

WXR-CMD-11A — After WXR-SMOKE passes and any correction has been re-exported and rechecked, create the final deterministic article translation log. Require status `complete` and one record per run article.

```bash
mkdir -p work/wadoku-xml/logs
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py article-log \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --require-status complete \
  --output "work/wadoku-xml/logs/run-${WADOKU_XML_RUN_ID}-articles.jsonl.gz" \
  --summary "work/wadoku-xml/logs/run-${WADOKU_XML_RUN_ID}-summary.json"
```

WXR-CMD-12 — Create the final PostgreSQL dump only after WXR-CMD-11A passes. Restore it into one new, exactly named temporary database, confirm the Wadoku source hash and run exist, drop only that temporary database, copy the dump outside the container, and record its hash. `createdb` must fail rather than overwrite an existing database with the same name.

```bash
docker exec jitendex-postgres pg_dump -U jitendex -d wadoku -Fc \
  -f /tmp/wadoku-xml-final-postgresql.dump
docker exec jitendex-postgres pg_restore -l \
  /tmp/wadoku-xml-final-postgresql.dump >/dev/null
WADOKU_RESTORE_CHECK_DB="wadoku_xml_restore_check_${WADOKU_XML_RUN_ID}"
docker exec jitendex-postgres createdb -U jitendex "$WADOKU_RESTORE_CHECK_DB"
docker exec jitendex-postgres pg_restore -U jitendex --exit-on-error \
  -d "$WADOKU_RESTORE_CHECK_DB" /tmp/wadoku-xml-final-postgresql.dump
docker exec jitendex-postgres psql -U jitendex -d "$WADOKU_RESTORE_CHECK_DB" \
  -v ON_ERROR_STOP=1 -c "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM source_snapshot WHERE kind='wadoku' AND sha256='1028ad3e5d64a14097ae0fef47e57d4615238028b1d0315dbe28082660f07d3b') AND EXISTS (SELECT 1 FROM run WHERE id=${WADOKU_XML_RUN_ID}) THEN 1 ELSE 0 END;"
docker exec jitendex-postgres dropdb -U jitendex "$WADOKU_RESTORE_CHECK_DB"
docker cp jitendex-postgres:/tmp/wadoku-xml-final-postgresql.dump \
  work/backups/wadoku-xml-final-postgresql.dump
shasum -a 256 work/backups/wadoku-xml-final-postgresql.dump \
  dist/wadoku-jp-de-rich.zip dist/wadoku-jp-ru-rich.zip \
  "work/wadoku-xml/logs/run-${WADOKU_XML_RUN_ID}-articles.jsonl.gz" \
  "work/wadoku-xml/logs/run-${WADOKU_XML_RUN_ID}-summary.json"
```

## WXR-FAIL — Resume and failure rules

WXR-FAIL-1 — Every mutating command must use PostgreSQL transactions and stable IDs. Re-running `prepare`, `reuse-v1 --apply`, acceptance, or export with the same inputs must not duplicate rows or change accepted data.

WXR-FAIL-2 — After an interrupted Luna run, wait for or expire its PostgreSQL leases, then rerun WXR-CMD-9 with the same run ID. Do not create a replacement run and do not move live state to SQLite.

WXR-FAIL-3 — If output validation fails, keep the candidate ZIP and report for diagnosis, fix the importer or renderer, and re-export from the same PostgreSQL snapshot. Never patch JSON inside a ZIP by hand.

WXR-FAIL-4 — If source hashes, XSD validation, or expected source counts fail, stop before import. A later Wadoku release requires an explicit plan update rather than weaker checks.

WXR-FAIL-5 — If PostgreSQL becomes unavailable, stop production writes. An emergency SQLite copy can preserve evidence, but work resumes only after PostgreSQL contains that evidence and a reconciliation report proves row and hash equality.

WXR-FAIL-6 — If migration `0010` fails, stop before source import. Keep the pre-run dump. Diagnose and test the restore in a separate database before any live restore.

WXR-FAIL-7 — After a runner stops, write the unresolved-failure report. When no lease is active, retry each reported retryable batch with the existing command, then rerun WXR-CMD-9. Do not delete attempts or validation issues.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py failures \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --output work/wadoku-xml/unresolved-failures.json
PYTHONPATH=src .venv/bin/translationctl \
  --config config.wadoku.xml.luna.toml retry '<batch id from the report>'
```

WXR-FAIL-8 — Apply a manual sample correction only through `replace-target`. Its input JSON contains `unit_id`, scalar `target_text`, `actor`, and `reason`. The command reruns scalar and placeholder validation, writes the old value to `translation_canonicalization_history` with mapping source `manual-wadoku-qa`, updates the accepted target, hash, and acceptance method `manual` in one transaction, and writes an audit event.

```bash
PYTHONPATH=src .venv/bin/python scripts/wadoku_xml_dictionary.py replace-target \
  --config config.wadoku.xml.luna.toml --run-id "$WADOKU_XML_RUN_ID" \
  --input work/wadoku-xml/manual-replacement.json
```

WXR-FAIL-9 — If failures show that the versioned prompt is wrong for many entries, stop the run. Do not change the prompt hash inside an existing run. Create a new prompt version and a new run plan instead of mixing prompt provenance.

## WXR-SMOKE — Manual Yomitan check

WXR-SMOKE-1 — Remove an older candidate with the same dictionary revision, import both final ZIPs through Yomitan dictionary settings, and look up every term in WXR-SAMPLE.

WXR-SMOKE-2 — Confirm that both editions find the same forms and show the same sense count, order, grammar, references, pronunciation, and pitch. Confirm that the German edition shows original German blocks and the Russian edition shows the matching accepted Russian blocks.

WXR-SMOKE-3 — Record Yomitan version, browser version, archive hashes, date, tester, and a pass or exact failure for every sample in `reports/wadoku_xml_rich_smoke.json`. Manual smoke testing is the only browser step; do not add browser automation for this release.

## WXR-NONGOAL — Deliberate limits

WXR-NONGOAL-1 — Do not add fuzzy, embedding, or heuristic V1 translation reuse.

WXR-NONGOAL-2 — Do not add Yomitan deinflection rules, ranking weights, or unverified internal reference links in the first rich release.

WXR-NONGOAL-3 — Do not create a separate German database, German run, parallel XML schema, or production SQLite workflow.

WXR-NONGOAL-4 — Do not require review of every translation. Keep deterministic automated checks, the fixed pilot review, and the fixed final sample.

## WXR-ORDER — Required execution order

WXR-ORDER-1 — Run the read-only source acquisition in WXR-CMD-1. Implement WXR-FILES, run WXR-CMD-1A, complete the controlled labels, and pass the tests. Then run WXR-CMD-2 through WXR-CMD-4.

WXR-ORDER-2 — Complete WXR-CMD-5 and fix all German fidelity defects before creating or translating Russian work.

WXR-ORDER-3 — Complete exact reuse in WXR-CMD-6, create only pilot batches in WXR-CMD-7, and pass WXR-CMD-8. Then create the remaining batches and pilot checkpoint in WXR-CMD-8A and WXR-CMD-8B before the full run in WXR-CMD-9.

WXR-ORDER-4 — Complete WXR-CMD-10 and WXR-CMD-11, then run WXR-SMOKE. Apply any correction through WXR-FAIL-8 and repeat export, verification, comparison, and smoke testing. After smoke passes, complete WXR-CMD-11A and WXR-CMD-12. Release only when all seven WXR-VAL gates pass and the release report contains the hashes and counts required by WXR-DONE-6.
