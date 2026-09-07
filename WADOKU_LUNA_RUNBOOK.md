# WDK — Wadoku Luna run

WDK-1 — This run translates the Wadoku Japanese-German Yomitan dictionary into Russian. It uses `gpt-5.6-luna`, medium reasoning, `config.wadoku.luna.toml`, and `prompts/translate_luna_de_ru_v1.txt`.

## WDK-STATE — Current state

WDK-STATE-1 — Wadoku run 2 uses the separate PostgreSQL database `wadoku`. This database is the source of truth. Do not use the Jitendex production database for this run.

WDK-STATE-2 — The source has 575,990 articles and 569,958 unique Japanese headwords. Run 2 started with 575,990 translation units in 18,179 batches. Splitting added more batches during the run.

WDK-STATE-3 — Run 1 is preparation and smoke evidence. Its smoke batch stored six valid Russian translations. Run 2 is the completed optimized production run.

WDK-STATE-4 — The full run started with 100 workers. Two later runners added 50 workers each after health checks showed no rate or quota errors. PostgreSQL leases keep the 200 workers separate.

## WDK-PREP — Preparation result

WDK-PREP-1 — Preparation finished on 2026-08-30 in 273.251 seconds. It produced zero articles without units.

WDK-PREP-2 — Batch manifests use 457,566,783 bytes. Peak preparation memory was 3,814,719,488 bytes.

WDK-PREP-3 — Optimized run 2 preparation finished in 83.254 seconds. The unchanged 24 KB byte limit packed the work into 18,179 batches. This is 81.1% fewer model requests than run 1.

WDK-PREP-4 — Run 2 batch manifests use 439,745,545 bytes. Peak preparation memory was 3,810,738,176 bytes.

## WDK-INC — Incident

WDK-INC-1 — The first smoke response finished, but the coordinator waited in the old workload progress query. The query used correlated subqueries and did not scale to 575,990 units.

WDK-INC-2 — The coordinator stopped cleanly. The saved response was recovered under its original attempt, recorded in the audit log, and passed normal validation.

WDK-INC-3 — The new set-based workload query completes in about 4.4 seconds, including the headword count.

## WDK-RUN — Full translation

WDK-RUN-1 — Production uses `scripts/run_codex_batches.py` with `config.wadoku.luna.toml`, run 2, a 30-second ramp, and a 240-second request timeout.

WDK-RUN-2 — Require zero claimed attempts and zero leased batches after every window. Preserve all attempts, responses, validation issues, and audit events.

## WDK-FINISH — Accept and export

WDK-FINISH-1 — After all batches reach deterministic validation, accept translations and validate run 2 with `translationctl` and `config.wadoku.luna.toml`.

WDK-FINISH-2 — Export with `scripts/wadoku_dictionary.py` to `dist/wadoku-jp-ru.zip`. Verify the archive schema, article count, source structure, Russian text, and SHA-256 hash.

WDK-FINISH-3 — After all worker leases end, run `scripts/revalidate_blocked_responses.py`. It accepts only saved responses that pass the current validator. It records a new attempt and audit event for every recovered batch.

WDK-FINISH-4 — Repair the remaining mixed-script or untranslated definitions with explicit Russian targets. Do not weaken the validator for these model errors.

WDK-FINISH-5 — Run `scripts/wadoku_dictionary.py verify` against the source and output archives. This checks the pinned Yomitan schema, Japanese-to-Russian metadata, archive members, row count, protected term fields, Russian coverage, and archive hash.

WDK-FINISH-6 — After all leases end, use `scripts/requeue_transport_blocked.py` to find batches whose last attempt has a transport error and no response. Run it first without `--apply`, then apply the audited requeue and process those batches again.

## WDK-DONE — Verified completion

WDK-DONE-1 — Run 2 finished with 575,990 accepted translations for 575,990 source units. It has zero untranslated units, zero unresolved validation issues, and zero batch-membership mismatches.

WDK-DONE-2 — Docker became unavailable during the run. The emergency SQLite database `work/wadoku-recovery/progress.sqlite3` rebuilt deterministic Run 2, replayed 16,736 valid saved response batches, applied all 176 prepared repairs, and processed the remaining Luna batches. A guarded merge then copied the 50,802 missing translations and 1,636 accepted attempt records into PostgreSQL. It changed no existing translation.

WDK-DONE-3 — The verified Yomitan archive is `dist/wadoku-jp-ru.zip`. Its SHA-256 is `e7a43da1f1fbad8dcbc51858d5da6d6b1e697ee7b04e1aafcd1bb7ade9ad0bbc`.

WDK-DONE-4 — The archive has 575,990 articles and 946,754 definitions. It has 575,866 articles with Cyrillic Russian text and 124 language-neutral articles: 121 unchanged acronyms or numbers and three scientific names.

WDK-DONE-5 — The final source-of-truth backup is `work/backups/wadoku-final-postgresql.dump`. Its SHA-256 is `fd84466d7f3667a0806a261443b22bcbeb8ee3f755bdf64c714f24d68a5061cb`. `pg_restore --list` validated the dump.

WDK-DONE-6 — PostgreSQL `wadoku` now contains 575,990 accepted translations for 575,990 unique units. It has zero unresolved errors and zero active leases. The final archive in WDK-DONE-3 was exported from this PostgreSQL database only.

WDK-DONE-7 — The emergency SQLite backup remains at `work/backups/wadoku-jp-ru-final.sqlite3`. Its SHA-256 is `766928f7f9546fe95f3d45238e0fe3a337b2673368ba37476d48526f6f4f7cd3`. Keep it as recovery evidence, not as the export source.
