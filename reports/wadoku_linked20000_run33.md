# W20K — Wadoku linked 20,000 run 33

W20K-1 — Run 33 used frozen scope `0d871014f1ef547e9a1f1711b6accb8602cdd517662b81fd2b44b1bd763c093a`. The scope contains 20,000 source entries and follows source references transitively. The source reports one target ID, `2796791`, that does not exist in the source XML.

## W20K-RESULT — Completion

W20K-RESULT-1 — PostgreSQL records 20,000 classified articles, 20,000 translated articles, and 48,401 accepted Russian translation units out of 48,401 units. Luna produced every accepted Russian target. Astra reviewed samples and rejected faulty attempts but did not write replacement target text.

W20K-RESULT-2 — The complete workflow lasted 6 hours 29 minutes 37 seconds, including retries, semantic review, prompt work, and pauses between commands. Classification occupied 4 hours 14 minutes 48 seconds of wall time. Translation, retries, and alignment repair occupied 2 hours 14 minutes 49 seconds of wall time.

W20K-RESULT-3 — Classification made 20,107 terminal attempts and rejected 107, an error rate of 0.532%. Translation made 8,185 terminal attempts including repair stages and rejected 507, an error rate of 6.194%. All rejected work was retried or replaced; no translation unit remains incomplete. The database count includes three accepted responses that Astra later invalidated; the append-only command journals keep their original terminal event.

W20K-RESULT-4 — Classification used 287,850,650 input tokens and 9,859,799 output tokens. Translation and its repairs used 166,683,956 input tokens and 8,446,596 output tokens. Cached-input and reasoning-token fields remain available in the machine-readable report.

## W20K-EXPORT — Dictionaries and review site

W20K-EXPORT-1 — The JP→RU archive has 30,624 term rows and SHA-256 `61a2aaa6793f15bf5d461313ce720ce0a302afd85ef53e2754a6db7c93535e94`. The JP→DE archive has 30,624 term rows and SHA-256 `d7c4145cf4219fac87b9de40a3cbbe2bd362fad0615dc5c947f0d81722a9dae8`. Both archives contain 19,470 lexical owner articles and passed eight Yomitan bank-schema checks.

W20K-EXPORT-2 — Each archive contains 17,424 internal dictionary-link occurrences. The final archive-wide audit found zero links whose query lacks a lookup key in the same archive.

W20K-EXPORT-3 — The review site has four tabs, one for each next block of 5,000 source entries. Each tab contains 100 deterministic sample entries. Known issue entries replace random entries, so the complete visible sample stays at 400 entries.

W20K-EXPORT-4 — The public review site is `https://wadoku-linked20000-review.ganqturgon.chatgpt.site/`.

## W20K-ERRORS — Typical findings

W20K-ERRORS-1 — Two multi-article Luna responses shifted translations into the next article. Deterministic alignment checks rejected the first case. Astra found a paraphrased second case. Six affected articles were retried as single-article Luna jobs. The final database contains only the repaired translations.

W20K-ERRORS-2 — The old extractor skipped four entries whose only translatable value was inside an XML `title` element. The extractor now creates title units. Luna translated all four entries and Astra checked them.

W20K-ERRORS-3 — The final diagnostic export reports 182 source or structure limits: 83 unresolved pronunciation records, 52 unresolved Japanese transcriptions, 24 missing label mappings, 12 unresolved lexical ownership decisions, 7 unresolved lookup decisions, 3 literal template siblings, and 1 ambiguous grouped pitch/sense alignment. These entries preserve their source material and show a visible warning.

W20K-ERRORS-4 — Two minor content issues remain visible. One Russian example keeps `Ōsaka` instead of `Осака`. One source etymon says `Plumpbum`, likely a source typo for `plumbum`. The prompt now blocks the first pattern in future runs. The second needs a verified source correction.

W20K-ERRORS-5 — The final exporter originally produced ten broken link occurrences to six in-scope template articles. Diagnostic fallback had restored literal keys such as `…匹` after classification had approved `匹`. The exporter now applies each valid classified alias even when another form remains unresolved. The final link audit is clean.

## W20K-AUDIT — Durable evidence

W20K-AUDIT-1 — The machine-readable progress report is `work/wadoku-xml/linked20000-v16-v10-20260919/run/progress-report.json`, SHA-256 `549420df8c52d5ea0e4f085fc2c505ace9571987cf85aa4854a779658fe49bf5`.

W20K-AUDIT-2 — The three Astra review records have SHA-256 values `30e660e9b3c30267e4d708c6ded7249007f743681ab175ebd6755b71862a634c`, `d4a2102477e9a7fcbb3cad9b10e717a9119ab711863bc1f2b228576032bd5c06`, and `c971e18ec07b5f19b88e5b67ca3cbf1d34f26498a6969c87fdffc7d47928a86c`.

W20K-AUDIT-3 — The repair journals are `alignment-repair.jsonl`, SHA-256 `3d13f1df2743a4b01e112bc065fa6cbba8dca39ca5326c7fcd7b20b592eb3886`, and `alignment-singletons.jsonl`, SHA-256 `b4568ac00124097b2c22601285f3fd2b7da562d7d6cc95aef2ee39edc843bc8b`.

W20K-AUDIT-4 — The public review issue list is `site/dist/issues.json`, SHA-256 `fd773af15a44cbc9a2db87dda06b91a9ff32ad5070cced505c377cb9190b6794`.

## W20K-SAMPLE — Last accepted batch sample

W20K-SAMPLE-1 — The deterministic sample from the last accepted translation batch is `心理学綱要` (`しんりがくこうよう`, entry `244428`). Luna translated its title as `«Основы психологии»` with high confidence.
