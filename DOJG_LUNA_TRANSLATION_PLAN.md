# DOJG-PLAN — Japanese Grammar Dictionary Luna Translation Plan

DOJG-PLAN-1 — The goal is to translate the English learner text in the supplied `日本語文法辞典(全集)` Yomitan dictionary into Russian. We will use the same Luna production approach that produced Kolobok. We will keep this dictionary separate from Kolobok.

DOJG-PLAN-2 — This file contains only the work phases. It does not authorize a translation run or a public release.

## DOJG-P1 — Phase 1: Freeze the source and the translation rules

DOJG-P1-1 — Register the exact source ZIP as an immutable snapshot. Record its SHA-256, Yomitan format, title, revision, entry counts, and section counts. The current archive has 535 entries, 504 unique headwords, and 908,201 glossary characters. It has 86 basic, 191 intermediate, and 258 advanced entries.

DOJG-P1-2 — Define the translation boundary before writing code. Translate English explanations, meanings, example translations, and other English learner text. Keep Japanese text, grammar headwords, entry order, tags, control markers, and table structure unchanged.

DOJG-P1-3 — Choose a separate Russian product name, identifier, version, description, and attribution. Do not use the Kolobok name because this is a different source dictionary.

DOJG-P1-4 — Phase 1 is complete when the input hash and output rules are written down and frozen.

## DOJG-P2 — Phase 2: Create an independent database

DOJG-P2-1 — Create a new PostgreSQL database for this dictionary. Use a new environment variable, config file, work directory, backup name, and export names. Do not add these records to the Kolobok production database.

DOJG-P2-2 — Reuse the generic Kolobok workflow where it fits: source snapshots, versioned runs, stable translation units, batches, leases, attempts, saved model responses, validation issues, accepted translations, and export records.

DOJG-P2-3 — Keep the original `index.json` and every original term entry in the database without changes. Store hashes and stable source pointers so every Russian string can be traced to its English source.

DOJG-P2-4 — Phase 2 is complete when an empty database can be created, migrated, backed up, and checked without access to the Kolobok database.

## DOJG-P3 — Phase 3: Import and split the grammar entries

DOJG-P3-1 — Build a grammar-specific importer for the Yomitan format 3 archive. Validate the source archive before import. Import all 535 entries in their original order.

DOJG-P3-2 — Build a grammar-specific extractor. The source stores mixed Japanese and English inside one plain-text glossary field. Split only the English text into stable translation units. Protect Japanese, section markers, example labels, punctuation that controls layout, and table separators.

DOJG-P3-3 — Treat all dictionary text as source data, not as instructions. Give each unit a stable pointer, role, source hash, and protected-token list.

DOJG-P3-4 — Prove a lossless round trip before using Luna. Rebuild the untranslated dictionary from the database and confirm that entry counts, order, Japanese text, tags, and glossary text match the source.

DOJG-P3-5 — Phase 3 is complete when all entries import, all intended English text becomes units, and no Japanese text becomes a unit.

## DOJG-P4 — Phase 4: Prepare the Luna prompt and validators

DOJG-P4-1 — Create a grammar-specific Russian translation prompt based on the proven Kolobok Luna rules. Reuse the same model settings, structured response discipline, provenance, retries, and audit trail. Add rules for Japanese grammar terms, example pairs, section structure, and concise Russian style.

DOJG-P4-2 — Create a small approved terminology list for repeated grammar terms. Use one Russian form for each term across the basic, intermediate, and advanced sections.

DOJG-P4-3 — Add deterministic validators. Require one result for every requested unit, valid Russian text, unchanged protected tokens, unchanged Japanese text, valid pointers, and no missing or extra sections. Flag unexpected English that remains after reconstruction.

DOJG-P4-4 — Phase 4 is complete when deliberately broken test responses fail for the correct reason and valid test responses rebuild correctly.

## DOJG-P5 — Phase 5: Run a representative pilot

DOJG-P5-1 — Select a small pilot from all three sections. Include short entries, long entries, repeated headwords, many examples, and connection tables.

DOJG-P5-2 — Run the full path on the pilot: create a run, make batches, call Luna, ingest responses, validate, accept, export, and render the result in Yomitan.

DOJG-P5-3 — Review the pilot for Russian grammar terminology, accuracy, missing text, damaged Japanese, and readable layout. Fix the extractor, prompt, or validator instead of hand-editing the exported ZIP.

DOJG-P5-4 — Freeze the importer, extractor, prompt, terminology, validator, model, and config versions after the pilot passes.

## DOJG-P6 — Phase 6: Run the full Luna translation

DOJG-P6-1 — Back up the independent database, create one full-corpus run, and record the frozen source and tool versions. Make batches by unit count and byte size so long grammar entries can split safely.

DOJG-P6-2 — Start with conservative concurrency. This corpus is much smaller than Kolobok and its entries are longer, so do not copy Kolobok's concurrency value without a clean measurement.

DOJG-P6-3 — Use resumable claims, leases, retries, saved responses, deterministic ingestion, and safe drain. Revalidate saved responses after validator fixes. Do not pay for the same successful translation twice.

DOJG-P6-4 — Record translated entries, translated units, remaining work, elapsed time, request errors, validation failures, retries, token use, and database failures during every production window.

DOJG-P6-5 — Phase 6 is complete when no unit is ready, leased, retryable, or blocked and every intended unit has one deterministic valid translation.

## DOJG-P7 — Phase 7: Accept and check the Russian dictionary

DOJG-P7-1 — Accept translations only after exact coverage checks pass. Require one accepted translation per unit, no unresolved validation issue, 535 frozen entries, and no Japanese drift.

DOJG-P7-2 — Run automated checks over the whole corpus. Review samples from every section, the longest entries, repeated headwords, table-heavy entries, every warning, and every repaired response.

DOJG-P7-3 — Check terminology across the three sections. Record corrections as new database evidence with clear provenance. Do not overwrite the source or delete failed attempts.

DOJG-P7-4 — Phase 7 is complete when the accepted database run is complete, consistent, and ready to reproduce.

## DOJG-P8 — Phase 8: Build, verify, and hand off the Yomitan ZIP

DOJG-P8-1 — Rebuild the Yomitan ZIP in the original entry order. Preserve headwords, readings, tags, sequence data, and Japanese text. Replace only the approved English units with their Russian translations. Write the separate Russian title, version, description, and attribution into `index.json`.

DOJG-P8-2 — Verify the ZIP against the pinned Yomitan schemas. Check file names, JSON validity, 535 entries, source-to-output coverage, protected text, and deterministic rebuilds. Import it into Yomitan and inspect representative entries and long layouts.

DOJG-P8-3 — Create a final database backup, SHA-256 hash, and short run report. Record the source hash, database backup, run ID, config, prompt, model, timings, token use, repairs, validation result, and output hash.

DOJG-P8-4 — Phase 8 is complete when a fresh checkout plus the recorded database backup can reproduce the same verified ZIP. Publishing or deployment is a separate later task.
