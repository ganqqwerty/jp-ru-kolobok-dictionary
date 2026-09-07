# DOJG-REPORT — Japanese grammar dictionary Luna run report

DOJG-REPORT-1 — Run 5 completed the Russian translation of the supplied Dictionary of Japanese Grammar. The run used the Kolobok Luna workflow in the separate `dojg_translation` PostgreSQL database. It did not change the Kolobok database and did not publish the result.

## DOJG-REP-SRC — Frozen source

DOJG-REP-SRC-1 — The source ZIP SHA-256 is `d75584cb93ec505e56842c2276012dd281f444811373152770b1e1dc2eea63ac`. The file has 398,490 bytes and uses Yomitan format 3.

DOJG-REP-SRC-2 — The source has 535 entries, 504 unique headwords, 908,201 glossary characters, and one term bank. It has 86 basic, 191 intermediate, and 258 advanced entries.

DOJG-REP-SRC-3 — Extractor `extractor-dojg-v4` created 9,408 English learner-text units. No Japanese text became a unit. The untranslated database rebuild matched every source entry.

## DOJG-REP-FROZEN — Frozen run inputs

DOJG-REP-FROZEN-1 — Run 5 used `gpt-5.6-luna` with medium reasoning, pipeline `dojg-v1`, and config `config.dojg.luna.toml`. The config SHA-256 is `dee13d5f429f7b0217fd4d046e8f59e21678a55cee5e6301a1094a9be63232a2`.

DOJG-REP-FROZEN-2 — The prompt is `prompts/translate_luna_dojg_ru_v2.txt`, SHA-256 `1749e4b9771083cd393a9f1a6ef7592f6f05c6a6669fe3a82bc9f82c0d92ec24`.

DOJG-REP-FROZEN-3 — The terminology file is `terminology/dojg-ru-v1.json`, SHA-256 `e606f70bc9ed9fb644eb752b58add50a8bba869f05ebbaab6d6633b75711e40c`.

## DOJG-REP-PILOT — Pilot

DOJG-REP-PILOT-1 — The pilot covered all three sections, repeated headwords, short and long entries, and connection tables. Pilot runs 1 to 3 exposed over-protection and two false extraction cases. The extractor and validator were fixed instead of editing the ZIP.

DOJG-REP-PILOT-2 — Pilot run 4 reused 366 valid Luna translations and completed with zero remaining work. Its verified archive is `work/dojg/dojg-pilot-run4.zip`, SHA-256 `ddb974f213019de2eef700f6a4150e352f71d3b09d0c94478416daa18af165d3`.

## DOJG-REP-RUN — Full Luna run

DOJG-REP-RUN-1 — The pre-run backup is `work/dojg/backups/dojg-before-full-run5.dump`, SHA-256 `f4e2d996cbe1f7b624a8c29f5d76cf516c396b7185dc6d9b6ac36669130bba66`. It has 1,689,536 bytes.

DOJG-REP-RUN-2 — Run 5 started with 535 entries and 9,408 units. It reused 366 accepted pilot units. Luna translated 9,042 new units from 240 initial batches.

DOJG-REP-RUN-3 — The measured concurrency-20 window used a 30-second ramp, 90-second measured phase, and productive drain. It completed 17 measured requests and 20 drain requests. The measured phase translated 578 units, reached 385.31 units per minute, and had p50 latency 80.05 seconds and p95 latency 97.50 seconds. It had two validation rejections and no measured rate limit, timeout, transport failure, or database retry. The minimum measured completion target was not reached, so the report marks this window incomplete.

DOJG-REP-RUN-4 — Other Luna jobs used up to 200 workers on the same account during the full run. DOJG stayed at the measured concurrency of 20. Shared load caused HTTP fallback, rate limits, and five-minute request timeouts. Every failed request returned safely to the queue.

DOJG-REP-RUN-5 — The database records 410 attempts: 260 accepted, 129 rejected, and 21 interrupted. It records 7,609,013 input tokens, including 411,136 cached input tokens, and 1,109,415 output tokens. Total recorded use is 8,718,428 tokens. The attempt period lasted about 66 minutes.

DOJG-REP-RUN-6 — The first direct runner was stopped to reload a corrected validator. It requeued all in-flight work. One complete interrupted response, two saved responses, and two split-child responses were recovered without another Luna call.

DOJG-REP-RUN-7 — The last runner processed the long tail with retries and 18 recursive splits. A dosage table joined `mg` with following digits after placeholder removal. A narrow validator fix accepted the unchanged formula. The saved parent response then satisfied the remaining child batch without another Luna call.

DOJG-REP-RUN-8 — Final run state is complete. All 9,408 units are translated and accepted. There are 261 deterministically validated batches, 17 blocked split-parent provenance rows, zero terminal blocked batches, and zero unresolved validation errors.

## DOJG-REP-QA — Acceptance and quality checks

DOJG-REP-QA-1 — The full residual-English audit separated names, formulas, and quoted object language from learner text. It found 15 accepted targets that still had an English word or label. The audited correction file is `repairs/dojg-run5-final.json`, SHA-256 `90da6073d1f202903bc26b47642269e00c2126be58e59dcd22f0d26e2702df8e`.

DOJG-REP-QA-2 — The 15 corrections fixed two remaining adverbs, one malformed number phrase, ten grammar-label cells, one unquoted Japanese term with Russian transliteration, and one romanized Japanese term marked as quoted object language. Each database change records old and new target hashes, a reason, and the correction-file hash.

DOJG-REP-QA-3 — The sample audit covered all three source sections, 30 repeated headwords, the five longest entries, formula tables, the dosage formula, quoted etymology, and every corrected target. The longest entry is `によらず` with 5,527 glossary characters. No temporary Japanese placeholder or known untranslated marker remains in the archive.

DOJG-REP-QA-4 — The full test suite passed 194 tests. Two PostgreSQL integration tests were skipped as expected.

## DOJG-REP-OUT — Verified output

DOJG-REP-OUT-1 — The final archive is `dist/jp-ru-dojg-v1.0-yomitan.zip`. It has 483,760 bytes. Its SHA-256 is `0cbcdcaf2443785474d7f6f63206b254e73bda15e9e27fb066cfe273b9aaddbd`.

DOJG-REP-OUT-2 — Verification passed the pinned Yomitan schemas, 535-entry coverage, 9,408 accepted-unit coverage, exact accepted-database content, non-glossary field preservation, Japanese text preservation, layout delimiters, metadata, and the residual-English gate.

DOJG-REP-OUT-3 — A second export was byte-identical to the final archive. Its SHA-256 was also `0cbcdcaf2443785474d7f6f63206b254e73bda15e9e27fb066cfe273b9aaddbd`.

## DOJG-REP-REPRO — Backup and reproduction

DOJG-REP-REPRO-1 — The final custom-format PostgreSQL backup is `work/dojg/backups/dojg-run5-final.dump`. It has 2,724,594 bytes. Its SHA-256 is `92b7e0dea41654017751d31ad2a092f1177e14f68b9caf27d792e01d3647db2f`.

DOJG-REP-REPRO-2 — The backup was restored into a fresh `dojg_translation_repro` database. Export and verification passed from the restored database. The restored archive was byte-identical to the final archive. The temporary reproduction database was removed after the check.

## DOJG-REP-MANUAL — Manual Yomitan gate

DOJG-REP-MANUAL-1 — Automated browser tools cannot inspect the Yomitan popup. The local page `site-home/dojg-yomitan-check.html` provided the final ZIP download, six large hover targets, expected results, saved checkboxes, and a pass message.

DOJG-REP-MANUAL-2 — The user completed the Yomitan check on 2026-08-31 and reported that all checks worked. The manual gate passed for `て`, `に基づいて`, `はたして`, `によらず`, `ないしは`, and `といったところだ`. The later public-site work is recorded in DOJG-REP-SITE.

## DOJG-REP-SITE — Public Yomitan verification site

DOJG-REP-SITE-1 — The public site is `https://dojg-russian-yomitan-check.ganqturgon.chatgpt.site`. It requires no login. The final page has 22 review cards and 53 Yomitan hover targets across basic, intermediate, and advanced grammar.

DOJG-REP-SITE-2 — The site includes repeated headwords, related grammar families, kana and kanji spelling variants, formula-heavy entries, long entries, saved local progress, and a reset control. It stores check state only in the visitor's browser.

DOJG-REP-SITE-3 — The public download is byte-identical to `dist/jp-ru-dojg-v1.0-yomitan.zip`, SHA-256 `0cbcdcaf2443785474d7f6f63206b254e73bda15e9e27fb066cfe273b9aaddbd`. The live page returned HTTP 200 without authentication. Open Graph and X metadata use the public URL and the project social-preview image.
