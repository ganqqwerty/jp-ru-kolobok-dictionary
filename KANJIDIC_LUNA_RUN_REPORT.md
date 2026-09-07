# KJR — KANJIDIC Luna run report

KJR-1 — This run translated the supplied English KANJIDIC2 Yomitan dictionary into Russian. It used the same audited Luna batch flow as the Jitendex work. It used a separate PostgreSQL database named `kanjidic_ru`.

## KJR-SRC — Frozen source

KJR-SRC-1 — The source file is `[Kanji] KANJIDIC (English) (Recommended).zip`. Its SHA-256 is `f5a7ed6ff6e77bef02f939506df92b37cd4338ec99aa653fcdf2874492fb96f0`.

KJR-SRC-2 — The source has 2 kanji banks, 10,350 entries, and 24,704 English meaning strings. The run kept the kanji, readings, tags, numeric codes, metadata, bank order, and entry order unchanged.

## KJR-RUN — Luna run

KJR-RUN-1 — Run 1 was a 36-entry pilot. It showed that the first prompt could change the grammatical role of an English phrase. Run 1 remains unaccepted audit evidence.

KJR-RUN-2 — Run 2 used `gpt-5.6-luna`, medium reasoning, `config.kanjidic.luna.toml`, and `prompts/translate_luna_kanjidic_ru_v1.txt`. It stored one meaning-set unit for each kanji entry.

KJR-RUN-3 — Preparation took 4.7 seconds. It created 10,350 units and 863 first-level batches. The validated pre-run backup is `work/backups/kanjidic-ru-before-full-run2.dump`. Its SHA-256 is `7aaeeb01edb4897518b2f19ba077acd6ec39668010b228f531447b0ff5582d92`.

KJR-RUN-4 — The 36-entry Run 2 pilot took 34.5 seconds. The full concurrency-100 window took 807.7 seconds. It finished all 10,350 entries and left zero unfinished units and zero active workers.

KJR-RUN-5 — Run 2 has 995 attempts: 883 accepted and 112 rejected. The full window reported 21 timeouts, 7 rate-limit signals, 91 rejected responses, 92 retries, 20 splits, zero transport failures, and zero database failures. The 20 blocked batch rows are split parent records. Their child batches cover every unit.

KJR-RUN-6 — Run 2 used 18,632,472 input tokens, including 168,192 cached input tokens, and 1,454,632 output tokens. It has exactly 10,350 accepted translations and zero unresolved validation issues.

## KJR-QA — Final quality checks

KJR-QA-1 — The archive audit found one English word and 12 English abbreviations, names, or transliterations that needed Russian forms. The approved manifest `reports/kanjidic_ru_v1_remediation.json` records all 13 before-and-after hashes. The canonicalization history contains 13 immutable rows.

KJR-QA-2 — The final visible Latin audit leaves only the structural symbols `H` and `X` and three scientific names: `Eumenes polifomis`, `Acrida chinensis`, and `Gryllotalpa africana`.

KJR-QA-3 — The final run-history fingerprint is `7b4fc0537cc7e707d1da3e4361378ad9b8f86b5a51ce713aed87bca1e8c4f93f`.

## KJR-OUT — Verified output

KJR-OUT-1 — The output is `dist/kanjidic-ru-v1.0-yomitan.zip`. It contains the original 2 kanji banks, the localized tag bank, and Russian product metadata.

KJR-OUT-2 — The pinned Yomitan kanji-bank v3 schema passed for all 10,350 entries. The output has 24,110 Russian meaning strings. Every entry has translated meanings.

KJR-OUT-3 — The verified ZIP SHA-256 is `7f66dfb8844d1c85e192cd9a97e253fe74a3268a0c3dc9e178b485e0d1b8c246`.

KJR-OUT-4 — The validated final database backup is `work/backups/kanjidic-ru-run2-final.dump`. Its SHA-256 is `f12adc5b73f16fe6139c62fe34ad09997b3d4e805f474c48fa24aac94dbf9b1f`.
