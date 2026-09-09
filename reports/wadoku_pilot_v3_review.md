# WPR3 — Structured rendering repairs

WPR3-1 — Fixed the five reported articles through shared rendering code. Protected references now retain their structure instead of joining romaji and Japanese. Abbreviation notes name the expanded word and its source kana reading. Templates occupy their own block. Domain and register labels use sense-scoped badges. Media captions, Wikipedia identifiers, related-ID containers and Steinhaus numbers stay in source data, not the visible glossary.

WPR3-2 — The full XML supplies reference headwords, including targets outside the 150-entry pilot. References use Yomitan internal search links. An unresolved target without inline Japanese fails export rather than displaying a raw ID. The production German, Russian and checkpoint export paths also receive this index. A link does not guarantee that its target exists in the enabled dictionaries.

WPR3-3 — Source 7654829 is 居る (いる); source 7833911 is 打ち切り (うちきり). Their main-reference edges describe source organization, not a proven deinflection relation. The renderer labels them related articles. No new automatic article merging or lemma inference was added.

WPR3-4 — Added prompt v4 for future sense-level runs and protected fragment context to pilot batch preparation. Existing v3 calls were not changed or rerun. Pilot 3 reuses their translations and the previous 22 audited corrections. Do not overwrite old manifests to prepare v4; use a new run directory. No additional Luna tokens were needed for these structural repairs.

WPR3-5 — Rebuild with `PYTHONPATH=src .venv/bin/python scripts/run_wadoku_pilot_standalone.py export`, followed by `site`. Output is under `work/wadoku-xml/pilot-v3/`. The older pilot ZIPs remain intact. Manual Yomitan testing is still required; no browser visual pass is claimed.

WPR3-6 — A later complete manual content review found additional failures. The open issue list is `reports/wadoku_pilot_manual_problems.md`; its coverage ledger is `reports/wadoku_pilot_manual_coverage.md`. Passing archive schemas did not catch these content defects. This review does not approve the pilot for a full rebuild.
