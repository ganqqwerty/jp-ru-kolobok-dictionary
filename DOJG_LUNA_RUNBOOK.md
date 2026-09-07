# DOJG-RUN — Japanese grammar dictionary Luna runbook

DOJG-RUN-1 — This runbook reproduces the Russian translation of the supplied Dictionary of Japanese Grammar. It uses the Kolobok Luna workflow, but it keeps all data in the separate `dojg_translation` PostgreSQL database.

DOJG-RUN-2 — Use `config.dojg.luna.toml`, `prompts/translate_luna_dojg_ru_v2.txt`, and `terminology/dojg-ru-v1.json`. Do not change these files during one run.

## DOJG-SRC — Frozen source

DOJG-SRC-1 — The source archive SHA-256 must be `d75584cb93ec505e56842c2276012dd281f444811373152770b1e1dc2eea63ac`. Stop if the hash differs.

DOJG-SRC-2 — The source must have Yomitan format 3, 535 entries, 504 unique headwords, and one term bank. The section counts must be 86 basic, 191 intermediate, and 258 advanced entries.

DOJG-SRC-3 — The importer must produce 9,408 translation units with extractor `extractor-dojg-v4`. The untranslated database round trip must match every source term entry.

## DOJG-DB — Separate database

DOJG-DB-1 — Set `DOJG_POSTGRES_URL` to the `dojg_translation` database. Never point this variable at the Kolobok database.

DOJG-DB-2 — Create or migrate the database with this command.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml init-db
```

DOJG-DB-3 — Acquire the pinned Yomitan schemas once and store their hashes with this command.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml acquire-schemas
```

DOJG-DB-4 — Make a PostgreSQL custom-format backup before a new productive run. Store it under `work/dojg/backups/` and record its SHA-256.

## DOJG-PREP — Prepare a full run

DOJG-PREP-1 — Set `DOJG_SOURCE_ZIP` to the frozen source archive. Create the full run with the last accepted pilot as the reuse source.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml prepare "$DOJG_SOURCE_ZIP" \
  --scope full --source-run-id 4
```

DOJG-PREP-2 — Confirm that the new run has 535 entries and 9,408 units. Run 5 starts with 366 reused pilot units and 9,042 ready units in 240 batches.

DOJG-PREP-3 — Check the run before Luna work. There must be no unresolved errors, leased batches, or active DOJG runner.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml status --run-id 5
```

## DOJG-LUNA — Translate with Luna

DOJG-LUNA-1 — Use `gpt-5.6-luna` with medium reasoning. Start with conservative concurrency because grammar batches are longer than Kolobok batches.

DOJG-LUNA-2 — Use the productive online window driver. Give each window a unique ID. Use a 30-second ramp, at least 90 steady seconds, and a productive drain.

```sh
PYTHONPATH=src .venv/bin/python scripts/run_luna_online_window.py \
  --config config.dojg.luna.toml --run-id 5 \
  --window-id dojg-r5-c20-1 --concurrency 20 \
  --ramp-seconds 30 --steady-seconds 90 --minimum-completed 20 \
  --request-timeout-seconds 300
```

DOJG-LUNA-3 — Check other Luna runners before every window. If a 100-worker dictionary run is active, keep DOJG at the measured 20-worker setting and do not increase it. Record the overlap in the run report.

DOJG-LUNA-4 — After each window, record elapsed time, attempts, accepted responses, validation rejections, retries, splits, tokens, rate limits, timeouts, transport errors, and database errors.

DOJG-LUNA-5 — Keep all failed attempts and saved responses. Revalidate a saved response after a validator fix. Do not call Luna again for an already successful response.

DOJG-LUNA-6 — A split parent may stay blocked. Completion requires zero terminal blocked leaf batches, zero ready or leased units, and 9,408 translated units.

## DOJG-ACCEPT — Accept and export

DOJG-ACCEPT-1 — Accept only after exact coverage and integrity checks pass.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml accept --run-id 5
```

DOJG-ACCEPT-R1 — Apply only reviewed corrections from the pinned audit file. This command validates every changed target and records old and new hashes in the database.

```sh
PYTHONPATH=src .venv/bin/python scripts/repair_dojg_translations.py \
  --config config.dojg.luna.toml --run-id 5 \
  --repairs repairs/dojg-run5-final.json
```

DOJG-ACCEPT-2 — Export the accepted run to the release path.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml export --run-id 5 \
  --output dist/jp-ru-dojg-v1.0-yomitan.zip
```

DOJG-ACCEPT-3 — Verify the output against the frozen source and pinned schemas.

```sh
PYTHONPATH=src .venv/bin/python scripts/dojg_dictionary.py \
  --config config.dojg.luna.toml verify --run-id 5 \
  dist/jp-ru-dojg-v1.0-yomitan.zip
```

DOJG-ACCEPT-4 — Export a second copy to a temporary path and compare SHA-256 values. The two hashes must match.

DOJG-ACCEPT-5 — Import the final ZIP into Yomitan manually. Open `site-home/dojg-yomitan-check.html` through a local HTTP server. Hover its six yellow terms in the browser that has Yomitan, complete the seven checks, and record the result. Browser automation cannot inspect the Yomitan popup.

## DOJG-FINISH — Final evidence

DOJG-FINISH-1 — Create a final custom-format database backup and record its SHA-256. Restore it into a fresh database and run the export and verify commands again.

DOJG-FINISH-2 — Run the full test suite with `PYTHONPATH=src .venv/bin/pytest -q`.

DOJG-FINISH-3 — Record the source hash, run ID, frozen versions, timings, token use, repairs, output hash, backup hash, and verification result in `DOJG_LUNA_RUN_REPORT.md`.
