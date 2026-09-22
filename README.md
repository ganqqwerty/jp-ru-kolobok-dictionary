# README — Колобок 400k translation pipeline

README-1 — This repository builds `Колобок 400k`, a Russian dictionary derived from Jitendex. The deterministic pipeline covers acquisition, imports, audit state, translation, validation, and reproducible exports for Yomitan, GoldenDict, MDict, PocketBook, and Apple Dictionary.

README-2 — The active `lexicographer-v2` pipeline treats the English Jitendex article as
evidence, not as text to translate. Terra first understands the article and its
preservation inventory, then authors Russian wording from the Japanese term,
examples, and linguistic metadata. English synonyms inside one sense are sent
as a single `glossary_set`; the response may contain a different number of
Russian definitions. The assembler changes only that glossary content and
scalar Russian labels/examples while retaining tables, Japanese examples,
ruby, links, sense structure, media, and attribution.

README-3 — Runs are isolated in the same SQLite database. `run.pipeline_version`, prompt
hashes, run-scoped unit IDs, and `run_article` fingerprints keep v2 units,
batches, attempts, reviews, and exports separate from the completed scalar-v1
results. Do not delete run 1 when starting v2.

README-4 — Requires Python 3.12+, `zstd` for modern Anki packages, and the two pinned
source artifacts. Install locally with:

```sh
python -m pip install -e '.[test]'
cp config.example.toml config.toml
translationctl --config config.toml init-db
translationctl --config config.toml acquire
translationctl --config config.toml import-sources
```

README-5 — Run `translationctl --help` for the complete staged workflow. Worker and review
agents only read manifests from `work/inbox` and write strict JSON to outbox
paths; they never edit the database or source JSON.

README-6 — The normal sequence after import is `resolve-scope`, review the unresolved JSON
from `report scope`, `apply-resolutions`, `extract-units`, `make-batches`, then
repeat `claim`/`ingest-response`. After all translation batches pass,
`make-review-batches` starts the independent review pass. `validate`, `build`,
and `verify` close the release gate. A third failed attempt is split
deterministically; a single-unit failure is blocked for adjudication.

README-7 — `claim --batch-id ID` can reserve a specific ready batch for a focused pilot.

README-8 — `acquire` also downloads the two Yomitan JSON schemas from an immutable upstream
commit and verifies their configured SHA-256 values. `verify` validates every
emitted bank against those pinned copies.

## README-TEST — Test execution

README-TEST-1 — Run tests from the repository root with the virtual-environment
interpreter and an explicit `PYTHONPATH=src`. Do not call bare `pytest`: the project
uses a `src/` layout, so an environment without an editable install otherwise stops
during collection with `ModuleNotFoundError: jitendex_ru` and executes no tests.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
```

README-TEST-2 — A collection error is not a failed test result. Record test counts
only after pytest reaches execution and prints its final pass, fail, and skip summary.

## README-YU — Yomitan v1.0 update warning

README-YU-1 — Do not use Yomitan's update button for the published `Колобок 400k v1.0` archive. Its update metadata points to upstream Jitendex and can replace the Russian dictionary with English Jitendex.

README-YU-2 — The installed v1.0 metadata cannot be repaired remotely. Keep the current archive installed until v1.0.1 is published, then download and import v1.0.1 manually once.

README-YU-3 — Jitendex, JMdict, Tatoeba, creator names, and license text intentionally remain in attribution. They are not evidence that the updater is safe; only operational `indexUrl` and `downloadUrl` determine the update channel.

README-YU-4 — Version v1.0.1 uses the stable installed title `Колобок 400k` and the owned update index `https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/yomitan.json`. After the one-time manual import from v1.0, future update checks stay inside the Kolobok release channel.

## README-YTP — Plain-text Yomitan export

README-YTP-1 — `export-yomitan-plain` converts a finished rich Yomitan archive into a
second archive for readers with incomplete structured-content support. It keeps term,
reading, tag, score, sequence, and redirect data. It changes only the glossary display.

```sh
translationctl export-yomitan-plain \
  dist/jp-ru-kolobok-400k-v1.0.1-yomitan.zip \
  --output dist/jp-ru-kolobok-400k-v1.0.1-yomitan-plain.zip
translationctl verify-yomitan-plain \
  dist/jp-ru-kolobok-400k-v1.0.1-yomitan-plain.zip \
  --source dist/jp-ru-kolobok-400k-v1.0.1-yomitan.zip
```

README-YTP-2 — The plain archive has a separate installed title and revision. It has no
update URL, because the published update channel points to the rich archive.

README-YTP-3 — Ruby becomes bracketed readings. Lists keep visible markers. Tables use
text columns and short form markers. External links show their URLs. Images become text
placeholders, and the archive does not include CSS or media files.

## README-YL — Light Yomitan export

README-YL-1 — `export-yomitan-light` keeps all search terms, readings, redirects,
word forms, and Russian definitions. It removes examples, external links,
attribution from article display, form tables, images, and rich formatting.

```sh
translationctl export-yomitan-light \
  dist/jp-ru-kolobok-400k-v1.0.1-yomitan.zip \
  --output dist/jp-ru-kolobok-400k-v1.0.1-yomitan-light.zip
translationctl verify-yomitan-light \
  dist/jp-ru-kolobok-400k-v1.0.1-yomitan-light.zip \
  --source dist/jp-ru-kolobok-400k-v1.0.1-yomitan.zip
```

README-YL-2 — The light archive has its own installed title. Its update channel is
empty, so Yomitan cannot replace it with the full archive. License credit remains
in dictionary metadata.

## README-GD — GoldenDict export

README-GD-1 — `export-goldendict --run-id ID --output dist/jp-ru-kolobok-400k-v1.0-goldendict.zip`
builds the accepted database run as a reproducible StarDict 2.4.2 bundle. Run
`verify-goldendict PATH` before release.

README-GD-2 — Extract the ZIP into one folder. Add that folder as a GoldenDict
dictionary source and rescan dictionaries. The bundle contains HTML articles,
reading aliases, internal links, CSS, and referenced media.

README-GD-3 — The exporter converts AVIF graphics to PNG for compatibility with
older GoldenDict renderers. SVG glyphs remain SVG so they stay sharp.

## README-EX — Rich dictionary exporters

README-EX-1 — The PocketBook, Apple Dictionary, and MDict exporters start from the
same localized structured article model. They retain lists, tables, ruby,
examples, links, media, semantic classes, tags, tooltips, and attribution when
the target can express them. Read [JITENDEX_EXPORTER_PLAN.md](JITENDEX_EXPORTER_PLAN.md)
and the contracts under `reports/exporters/` before release work.

README-EX-2 — PocketBook requires an external, hashed `converter.exe` and a pinned
`jaK` or `jaR` language directory. On non-Windows hosts it also requires Wine.
The package remains experimental until the device gates in
`reports/exporters/pocketbook-capabilities.md` pass.

```sh
translationctl export-pocketbook --run-id ID --output dist/jp-ru-kolobok-400k-v1.0-pocketbook.zip \
  --compiler /path/to/converter.exe --compiler-sha256 SHA256 \
  --language-dir /path/to/jaK
translationctl verify-pocketbook dist/jp-ru-kolobok-400k-v1.0-pocketbook.zip
```

README-EX-3 — Apple Dictionary requires the archived Dictionary Development Kit
`build_dict.sh`. The standard build performs a streaming full-corpus structure check,
runs the pinned DDK compiler, and verifies the compiled bundle. Full-corpus RELAX NG
validation is intentionally outside the release path because `xmllint` did not finish
within 90 minutes. Use the pinned schema only on the representative probe after a
renderer change. The exporter does not install the bundle.

```sh
translationctl export-apple-dictionary --run-id ID \
  --output dist/jp-ru-kolobok-400k-v1.0-apple-dictionary.zip \
  --build-tool /path/to/build_dict.sh --build-tool-sha256 SHA256
translationctl verify-apple-dictionary dist/jp-ru-kolobok-400k-v1.0-apple-dictionary.zip
```

README-EX-4 — MDict uses the pinned `mdict-utils` writer to emit deterministic,
unencrypted MDict 2.0 MDX and MDD files. The package is marked experimental until
the real-client matrix in `reports/exporters/mdict-capabilities.md` passes.

| ID | Format | Export | SHA-256 |
|---|---|---|---|
| README-STATE-1 | Yomitan `v1.0` | 83 | `24c0164f6d645f6426bef5b09f5dfdc46952cf132aed6d8bc033800f9ff7824b` |
| README-STATE-2 | GoldenDict `v1.0` | 84 | `774a4ec87862451f05992c7765b5a9bbc32ac96e54d134cadc5d81397f384aee` |
| README-STATE-3 | MDict `v1.0` | 85 | `589608c811d6eb72f2b16f83c2929ce1e356a7eb12f466977ad34bcaad23ddb3` |
| README-STATE-4 | PocketBook `v1.0` | 86 | `d03c892fe2db5fb0f93a65c54de729d41f1674ba7a89e97470b69a66a75e5d3f` |
| README-STATE-5 | Apple Dictionary `v1.0` | 87 | `f7879e122e6260e47def25c05a9b2846655ba8a52ad28c89f485a0a21ce26d46` |
| README-STATE-10 | Yomitan `v1.0.1` | 91 | `f0e8a6d8823398401994d0c7738aee4dca83b225bf276f9b08282cafbbac68b7` |
| README-STATE-11 | GoldenDict `v1.0.1` | 95 | `506396de7a00009074d956a62fc66c56cf0f8645c0470ad1a75868b622c5be51` |
| README-STATE-12 | MDict `v1.0.1` | 96 | `f22238c020b7ddebfeffc2df83256816fc23e00e02042b3eff321ce8c5145b4d` |
| README-STATE-13 | PocketBook `v1.0.1` | 97 | `e757ea8ddde01a2380485c6f498610f86565dda2076a993e3c9c9611374d31cc` |
| README-STATE-14 | Apple Dictionary `v1.0.1` | 98 | `57b828929cb674aeb1bc0be9c833aadfc4185ea81d40855a6cf20be93c150c1c` |

```sh
translationctl export-mdict --run-id ID --output dist/jp-ru-kolobok-400k-v1.0-mdict.zip
translationctl verify-mdict dist/jp-ru-kolobok-400k-v1.0-mdict.zip
```

README-EX-5 — Every exporter writes a deterministic ZIP manifest, loss ledger,
capability profile, source and tool hashes, attribution, and installation note.
Any omitted rich-content feature fails the build.

README-STATE-8 — Every current `Колобок 400k v1.0` archive credits Yuri Katkov as co-author of the Russian edition. The five archives and their internal payloads use the versioned Latin base name `jp-ru-kolobok-400k-v1.0` and are published in release `run59-tags-ru-v1`.

README-STATE-9 — Version `1.0` records compilation datetime `2026-08-15T21:04:30Z` in installed dictionary metadata and technical manifests.

README-STATE-15 — Version `1.0.1` records compilation datetime `2026-08-21T08:52:59Z`. Independent exports 99–103 reproduced all five archives byte-for-byte, and the cross-format lexical audit found no raw English redirect or restriction templates.

## README-DB — Database schema

README-DB-1 — The current SQLite schema is version 7. `init-db` creates it, and normal database initialization upgrades older frequency tables in place.

README-DB-2 — Frequency provenance has three layers. `frequency_source` pins each active source snapshot and rank limit. `frequency_term` stores one row per exact normalized term. `frequency_article` maps that exact term to every matching Jitendex article.

README-DB-3 — Frequency ranks are not identities and may repeat. `frequency_term` is keyed by `(source, source_sha256, term)`. `frequency_article` is keyed by `(source, source_sha256, term, article_id)`. This preserves tied ranks and makes every article mapping traceable to its headword.

README-DB-4 — The version-7 migration preserves version-6 terms and mappings while adding the term to each article mapping. Back up the production database and run SQLite integrity and foreign-key checks before continuing a release.

README-DB-5 — Use [JPDB_LUNA_ORCHESTRATION_RUNBOOK.md](JPDB_LUNA_ORCHESTRATION_RUNBOOK.md) for operational commands. The combined six-list top-40k scope is a one-off supplement described in [FREQUENCY_TOP40K_TRANSLATION_PLAN.md](FREQUENCY_TOP40K_TRANSLATION_PLAN.md); normal releases continue by JPDB frequency.

## README-DEMO — Public demos

README-DEMO-1 — The [project home page](https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/) introduces the Russian dictionary and links to its current downloads.

README-DEMO-3 — The [translation comparison](https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/comparison/) shows public samples from the completed dictionary batches.
