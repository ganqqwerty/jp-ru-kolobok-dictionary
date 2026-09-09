# WPR2 — Revised Wadoku pilot

WPR2-1 — Rebuilt the same frozen 150 source entries. The archive contains 333 lookup rows, not 333 independent articles. Output: `work/wadoku-xml/pilot-v2/wadoku-jp-ru-rich-pilot-150.zip`. SHA-256: `70338a868b512767ca614ff233b1279ea24c19e61c355a5b2e0ec5155f35ce91`.

WPR2-2 — Grouped unrestricted German equivalents by sense before translation. Luna now returns a concise Russian list for each group. Restricted blocks and protected fragments stay separate. The lossless source tree stays unchanged. Removed repeated field labels and redundant reading, form, and pronunciation sections from the glossary. Forms, grammar tags and pitch remain in native export fields.

WPR2-3 — Read the returned text for all 150 entries. Applied 22 source-hash-bound corrections in `terminology/wadoku-pilot-v2-editorial.json`. These address synonym noise, particle explanations, noun meanings rendered as verbs, names, and mistranslations. Corrected grammar labels separately. These corrections improve the pilot; they do not prove that Luna will avoid the same errors on unseen entries.

WPR2-4 — Used `prompts/translate_luna_wadoku_xml_ru_v3.txt` with Luna CLI, medium reasoning, 22 calls and concurrency 5. The 543 units used 508,940 input tokens and 63,334 output tokens; reported reasoning tokens were 11,874. Each batch has at most 18 articles; byte and unit limits also bound packing. The manifests and per-call usage remain under `work/wadoku-xml/pilot-v2/`.

WPR2-5 — Archive schema validation passed. The focused XML, sense projection and batch-runner suite passed 29 tests. The test page previews all articles from exported data and includes Kaishi sentences and inflection/phrase contrasts. Browser policy blocked opening the local preview. No visual or real Yomitan popup pass is claimed. Disable the first pilot before importing this one.

WPR2-6 — This standalone pilot does not validate the planned LLM article classification, child-sense merging or complete lookup-template expansion. Its suffix aliases are limited. The selected の articles do not cover every function of の. Native pitch is exported, but full pronunciation metadata such as devoicing still needs review. Production database batching still needs the sense-level contract; do not launch a full rebuild from this result alone.

WPR2-7 — Reproduce without new Luna calls: `PYTHONPATH=src .venv/bin/python scripts/run_wadoku_pilot_standalone.py export`, then the same command with `site`. Run tests with `PYTHONPATH=src .venv/bin/pytest -q tests/test_wadoku_sense_projection.py tests/test_wadoku_xml.py tests/test_codex_batch_runner.py`.

WPR2-8 — The correction to 素晴らしい distinguishes praise from extraordinary degree. Reference: [Shogakukan Digital Daijisen](https://dictionary.goo.ne.jp/word/%E7%B4%A0%E6%99%B4%E3%81%97%E3%81%84/). The broader lesson is to interpret German equivalents together, not translate each synonym literally.
