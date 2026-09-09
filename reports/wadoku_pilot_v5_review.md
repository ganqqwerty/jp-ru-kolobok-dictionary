# WP5 — Wadoku pilot 5 result

WP5-1 — Pilot 5 started on 2026-09-09. It uses seed `258591692390325971447032771762343909660`. The selection has 180 unique articles. None of them appears in pilots 3 or 4.

## WP5-SCOPE — Selection

WP5-SCOPE-1 — The selection has 20 frequent words from Kaishi.

WP5-SCOPE-2 — The selection has 20 names and titles.

WP5-SCOPE-3 — The selection has 30 grammar and function words.

WP5-SCOPE-4 — The selection has 30 templates and suffix-search articles.

WP5-SCOPE-5 — The selection has 20 fixed complex articles.

WP5-SCOPE-6 — The selection has 30 articles with structural risks.

WP5-SCOPE-7 — The selection has 15 random control articles.

WP5-SCOPE-8 — The selection has 15 articles with usage examples.

## WP5-TR — Translation

WP5-TR-1 — Luna used `prompts/translate_luna_wadoku_xml_ru_v4.txt`. The run used 26 requests with concurrency 5. It translated 607 units.

WP5-TR-2 — Luna reported high confidence for 587 units and medium confidence for 20 units.

## WP5-EX — Usage examples

WP5-EX-1 — The official Wadoku XML supplied 61 example candidates for the selected example articles. The pilot accepted 26 and rejected 35. It left zero candidates unresolved.

WP5-EX-2 — The formal audit found all 26 accepted examples in the responses and in the Yomitan ZIP. The audit status is `pass`.

WP5-EX-3 — `work/wadoku-xml/pilot-v5/pilot-state.sqlite3` is the source of truth for this standalone pilot. It stores the candidate list, all decisions, 26 example units, 26 translations, and the export audit. This pilot does not use PostgreSQL.

## WP5-OUT — Output

WP5-OUT-1 — The archive is `work/wadoku-xml/pilot-v5/wadoku-jp-ru-rich-pilot-5-2026-09-09.zip`. Its SHA-256 is `d0dbb02eeb3b3ad6156c17c40494497e8a5cd43f6a569b883a311f9f3896451f`.

WP5-OUT-2 — The archive metadata title is `Wadoku RU · пилот 5 · 2026-09-09`. Its revision records the pilot number, date, selection method, article count, prompt version, and example format.

WP5-OUT-3 — The archive has 180 articles, 301 lookup rows, and 221 pronunciation metadata rows. The Yomitan schema check passed.

WP5-OUT-4 — The public test site is `https://wadoku-pilot-5-review.ganqturgon.chatgpt.site`. It shows every article, every exported lookup form, kanji and kana variants, Yomitan word-form rules, and the 15 example articles.

## WP5-RISK — Structural results

WP5-RISK-1 — Open templates use their final fixed segment as the lookup key. The archive contains no literal ellipsis lookup row.

WP5-RISK-2 — Wadoku article 7318797 stores accent `2—5`. Yomitan needs one exact pitch position. The pilot omits that range from pitch metadata and records the limitation instead of inventing one value.

WP5-RISK-3 — The pilot added Russian labels for the Wadoku register values `vulg.` and `poet.`. This keeps those source labels visible in the exported articles.
