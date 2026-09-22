# WRUN — Скриптовый пилот Wadoku

WRUN-1 — Новые команды используют `config.wadoku.rich.luna.toml` (v3). Старый `config.wadoku.xml.luna.toml` оставлен для истории v2. Перед запуском задать `WADOKU_POSTGRES_URL` для базы `wadoku_rich_pilot`. Команды отказываются запускать работников на другой базе. Ниже команды выполняются из корня репозитория с `PYTHONPATH=src:scripts`.

WRUN-2 — The profile selects translation v16, review v9, classification v10, and transcription v1. Translation v16 keeps Japanese authoritative, treats German as secondary evidence, and makes clear that Luna writes only Russian. The paired German dictionary comes directly from the verified XML. `--prompt` selects another version explicitly. A resumed run finds its frozen prompt by hash, so v16 does not change older runs.

## WRUN-CYCLE — Окно, анализ, продолжение

WRUN-3 — Продолжить подготовленный прогон: `.venv/bin/python scripts/wadoku_translate_window.py --run-id 16 --max-batches 10 --context-budget 128000`. Здесь N — число батчей, допустимо 1–100; параллелизм задаёт отдельный `--concurrency`. Скрипт сохраняет состав окна и ограничивает повторы. Законченное окно требует разбора ошибок, но не нового разрешения пользователя. Если пользователь запросил проверку всего набора в основном потоке, выборка или рецензия Luna её не заменяют.

WRUN-4 — Получить отчёт: `.venv/bin/python scripts/wadoku_rich.py --database-name wadoku_rich_pilot window-report --run-id 16 --ordinal 20`. Оркестратор записывает фактическую выборку, проверенные сомнения и решение через `analyze-window --run-id 16 --ordinal 20 --analysis PATH.json`. Отчёт содержит хеш результата; устаревший анализ не принимается. Не заполнять анализ формально и не редактировать переводы вручную вместо общего исправления.

WRUN-5 — Проверить следующую порцию переводов: `.venv/bin/python scripts/wadoku_review_window.py --run-id 16 --max-batches 10`. `--unresolved-only` выбирает ещё не проверенные этим промптом спорные результаты. `--recheck --article-ids ID...` перепроверяет до десяти статей текущего прогона, в том числе ранее принятых. Это внутренние ID статей базы, не исходные номера Wadoku.

WRUN-6 — Для продолжения уже созданной очереди проверки добавить `--resume` и сохранить прежние параметры выбора и `--prompt`. Этот режим не создаёт новые задания из изменившихся кандидатов. Повторы получают диагностические ошибки; фактический промпт, схема, хеши и расход сохраняются. Старые отказы не удаляются.

## WRUN-OUTPUT — Покрытие и сборка

WRUN-7 — Проверить весь прогон: `.venv/bin/python scripts/wadoku_rich.py --database-name wadoku_rich_pilot status --run-id 16`. Считать покрытие по последним переводам всех исходных статей. Исторические blocked-батчи не равны текущему числу открытых смысловых вопросов.

WRUN-8 — `assembly-preflight --run-id 16` проверяет общий путь структуры и поиска на исходном тексте, а не качество русского перевода. `export-candidate --run-id 16 --output PATH.zip --license PATH_TO_LICENSE` требует полного принятого покрытия и собирает архив через тот же путь. Он не выдаёт разрешение на выпуск.

WRUN-9 — `export-candidate --diagnostic` допускает непринятые переводы только для явно помеченного диагностического архива. Пропущенные переводы и структурные ошибки по-прежнему запрещены. Такой архив не доказывает завершение пилота. Старые ZIP не перезаписываются.

WRUN-10 — Текущий прогон 16 содержит 193 исходные статьи и 940 принятых фрагментов; его перевод остаётся на замороженном v7. Полный запуск словаря не выполнялся. Проверка группировки, примеров и Yomitan остаётся отдельной работой. Политика браузера заблокировала доступ к расширению; пользователь получил диагностический ZIP для ручной проверки. Итоги и ограничения записаны в `reports/wadoku_run15_prompt_regression.md`.

WRUN-11 — Дополнительный контрольный прогон 19: 100 новых статей, 279 фрагментов, перевод v8, проверка всего набора в основном потоке. Итоги: `reports/wadoku_focus100_astra_review.md`. Окно 1 имеет решение repair; перевод не исправлен вручную и не объявлен релизом. `scripts/wadoku_review_dump.py --run-id 19 --output PATH.json` сохраняет исходники, переводы, ID и контекст для такого разбора.

WRUN-12 — `scripts/wadoku_focus_scope.py` воспроизводит отдельный стресс-набор из 40 шаблонов, 20 суффиксальных статей и 40 словосочетаний. Новая подготовка использует `--candidate-scope`; `--unclassified-source-only` допустим только для этого диагностического формата. Он явно сохраняет нерешённую структуру и не разрешает выпуск. Короткие статьи нового прогона пакуются по лимиту байт и параметру soft_max_articles; старая упаковка сохранена для старых прогонов.

WRUN-13 — Для сравнения промптов повторять сохранённый набор: `scripts/wadoku_focus_scope.py --repeat-from work/wadoku-xml/focus100/scope.json --revision UNIQUE_REVISION --output NEW_SCOPE.json`. Команда сохраняет исходные ID, порядок и хеши, создаёт новый scope и не переносит решения классификатора. Классификацию запускать с новым scope, `--limit 100` и `--concurrency 5`; максимум три попытки на запись. Не выбирать новые статьи вместо неудачных.

WRUN-14 — Если заказана вычитка всех 100 после перевода, подготовить весь scope и выбрать все его задания через `--max-batches 100`. До полного покрытия смотреть только счётчики, расход и технические ошибки. `wadoku_review_dump.py` отказывается создавать пакет с пропущенными переводами. После полного покрытия основной поток читает весь пакет, включая примеры и high-confidence ответы; Luna-рецензент эту проверку не заменяет.

WRUN-15 — Контракт rich-v4 выделяет точные известные грамматические пометы в метаданные и передаёт область их действия переводчику. Повтор немецкого слова рядом с русским эквивалентом получает узкую проверку и штатный ограниченный retry. Остальная латиница остаётся предметом смысловой проверки, не безусловного запрета. Старые rich-v3 запросы не получают новую проверку задним числом.

WRUN-16 — Контракт классификации v7 сохраняет suffix_only и prefix_only для граничных слотов. Для внутреннего слота используется internal_prefix: весь фиксированный текст до первого слота становится ключом, полная конструкция остаётся внутри статьи. Например, 費用が…だけかかる ищется по 費用が. Экспорт объединяет конструкции с одинаковыми ключом и чтением, но сохраняет отдельные заголовки, значения и примеры. Не переносить питч и правила спряжения полной фразы на ключ. При отсутствии начального текста или подтверждённого чтения префикса сохранять неопределённость. Владельцы вне выборки по-прежнему требуют отдельного решения.

WRUN-17 — Повтор того же набора — run 21: 100 статей, 255 переводимых фрагментов, v9. Все результаты вычитаны основным потоком только после полного покрытия. Решение repair: есть улучшения, но появились смысловые регрессии и немецкие остатки. См. reports/wadoku_focus100_repeat_review.md. Послепрогонные исправления нормализации проверены на сохранённых классификациях без записи; они не изменили замороженный run 21.

WRUN-18 — `scripts/wadoku_repeat_run.py --scope-id ID --work-dir PATH --concurrency 100 --articles-per-batch 6 --event-log PATH/pipeline.jsonl` выполняет повтор готового набора из 100 статей. Классификация имеет максимум три прохода. Перевод группирует до шести статей, также ограничивая размер запроса и число фрагментов. Крупные статьи идут отдельно. Оба этапа допускают 1–100 работников; фактический параллелизм ограничен числом заданий. Перевод использует общий небольшой пул PostgreSQL и освобождает соединение до вызова Luna.

WRUN-19 — Журналы pipeline.jsonl, classification.jsonl и translation.jsonl дописываются, не перезаписываются. Они содержат UTC, монотонную длительность, ID запуска, задания и попытки, исходные ID статей, пути запросов/ответов, коды ошибок и результат повтора. Каждая команда создаёт отдельный summary.json с общим временем и пиковым числом работников. command_finished=completed означает завершение команды, не одобрение переводов: отклонённые попытки и покрытие смотреть отдельно.

WRUN-20 — rich-v5 передаёт обычные лексические XML token как source_lexical_terms, а музыкальные обозначения как required_literals. Проверка ловит непреведённое исходное слово внутри русской строки и потерянное обозначение; научные имена и защищённые объекты не объявляются немецкими остатками. Старые rich-v4 запросы не получают новый контракт задним числом. Передача владельца и lookup-решения переводчику убрана: это не основание снижать уверенность в переводе.

WRUN-21 — Перевод v12 следует подходу Колобка: Luna самостоятельно переводит с японского; немецкий помогает определить текущий смысл. При настоящем конфликте следовать японскому и указать low confidence с причиной. Не копировать число и порядок немецких синонимов. Полный японский пример сохраняет детали, даже если немецкая подсказка их опускает.

WRUN-22 — rich-v6 добавляет japanese и task_type. XSatz и японская конечная пунктуация выделяют предложения; примеры получают example_translation. Остальные определения и пояснения различаются по исходной роли. task_type задаёт смысл задания, а role сохраняет прежний JSON-тип и совместимость сборщика. Старые проекции не меняются.

WRUN-23 — Сбой транспорта переводит точную арендованную задачу в retryable. Повторы ждут 5 и 10 секунд без занятого соединения PostgreSQL; после третьего сбоя задача блокируется без дробления. Ошибки содержимого используют прежний ограниченный retry/split. Неполное покрытие завершает команду ошибкой, даже если остальные ответы приняты. Логи содержат все попытки; успешный процесс не означает смыслового одобрения. Перед запуском проверить резерв всех запросов, включая промпт, схему ответа и запас на вывод.

WRUN-24 — Run 25 перевёл те же 100 статей на v12/rich-v6 с группировкой до шести статей: 258 фрагментов, все вычитаны основным потоком. Решение repair. Отчёт reports/wadoku_run25_japanese_primary_review.md содержит оставшиеся ошибки и отделяет проверенные изменения от ещё не испытанных поправок v13 и транспортного backoff.

## WRUN-SITE — Страница результата

WRUN-25 — После полного технического покрытия создать пакет проверки: `.venv/bin/python scripts/wadoku_review_dump.py --run-id RUN_ID --output SCOPE_ROOT/review-packet.json`. Затем создать диагностический Yomitan ZIP и страницу: `.venv/bin/python scripts/wadoku_inspection_site.py --run-id RUN_ID --root SCOPE_ROOT`. Scope в `SCOPE_ROOT/scope.json` должен быть тем же замороженным набором. Генератор отказывается смешивать другой набор или неполный перевод.

WRUN-26 — Проверить `export-report.json`: число исходных статей, покрытие, хеш ZIP и отсутствие пропущенных переводов. Проверить, что страница открывается, поиск работает, а ZIP доступен. Опубликовать страницу как публичный Wadoku demo Site. На странице всегда явно писать модель перевода, состояние ручной вычитки и `не релиз`. Публикация диагностического сайта входит в обычное завершение батча и не требует отдельного ручного одобрения. Она не означает одобрение перевода или готовность выпуска.

## WRUN-20K — Link-closed 20,000-entry run

WRUN-27 — Create one exact 20,000-entry scope with `.venv/bin/python scripts/wadoku_prefix_scope.py --size 20000 --link-closed --output SCOPE_ROOT/scope.json`. The selector keeps the largest source prefix whose full transitive `ref` and `sref` closure fits. It then adds safe source-order fillers with their closures. Every resolvable link target stays inside the scope. The manifest records broken source IDs separately.

WRUN-28 — Run the frozen scope with `.venv/bin/python scripts/wadoku_repeat_run.py --scope-id SCOPE_ID --work-dir SCOPE_ROOT/run --concurrency 80 --articles-per-batch 6 --event-log SCOPE_ROOT/run/pipeline.jsonl`. The command needs no per-window approval. It retries bounded technical failures, stops on unresolved coverage, and writes `progress-report.json` after translation finishes.

WRUN-29 — `progress-report.json` is the stable progress artifact. The runner refreshes it after every classification window and after translation. It records classified and translated article counts, elapsed wall time, attempt error rate, error classes, up to 20 failed-attempt samples, dangling source links, and one deterministic article from the latest completed batch. Before translation, this is a classification article; after translation, it includes translated units. Keep request and response paths in the event logs so later analysis can reproduce typical failures.

WRUN-30 — After exact translation coverage, create `review-packet.json`, then run `scripts/wadoku_inspection_site.py`. The exporter writes paired JP→RU and JP→DE Yomitan ZIP files from the same frozen source and structural decisions. Luna supplies only Russian text. The German ZIP uses the verified German XML directly.

WRUN-31 — For a scope above 5,000 entries, the review page creates one tab per consecutive 5,000-entry scope block. Each tab shows a deterministic random sample of 100 articles by default. The page reports sample issues and whole-export issue counts, and offers both Yomitan ZIP files. Publish this page as the normal public diagnostic site after generation.

WRUN-32 — Classification measures the saved prompt, request, and response schema as UTF-8 bytes, then adds a separate 16 KiB runtime allowance and a candidate-dependent output reserve. This remains conservative because non-ASCII request bytes overestimate model tokens. The runner rejects a request before claiming it if the complete reservation exceeds 128,000. Large source entries must pass `--dry-run --entry-ids ID` before a stopped scope is resumed.

## WRUN-COST — Smaller worker requests

WRUN-33 — New translations use prompt v19 and lossless XML context transport. Scripts send repeated source trees once and refer to them by context ID. All Japanese evidence, German aids, attributes, mixed text, unit boundaries and order remain intact. Before dispatch, scripts reconstruct the canonical request and check exact equality. They use canonical JSON if conversion changes anything or increases bytes. Stored manifests, output schemas and validators do not change. Frozen older prompts keep their previous transport.

WRUN-34 — Every attempt saves its actual `.wire.json` request beside its prompt and schema. The database audit and worker event record its hash, canonical bytes and wire bytes. Context reservations use the actual wire bytes plus the unchanged schema, prompt, runtime allowance and output reserve. Byte reduction is not token or price reduction; report measured input, cached input, output and retry usage separately after a real run.

WRUN-35 — Keep Luna medium, the current article cap, unit/output limits, classification and retry checks. Do not guess ownership, lexical independence or template handling to save a model call. Reuse already accepted work through the existing resume procedure. Do not retranslate it merely to benchmark transport. Before a large new run, use the next requested representative 100-entry pilot and read all completed results in the main thread. Check sense alignment, examples, internal slots, references and mixed XML text against the source and the previous quality baseline. Only then consider larger batches; change one factor at a time.

WRUN-36 — This change reduces translation context only. Classification was the largest measured cost and remains unchanged. Grouped classification needs a separate request/response contract and quality pilot; no savings from it are claimed here. Do not bypass semantic classification or shorten evidence without such a test. No paid pilot starts merely because this procedure changed.

## WRUN-30K — Extend the completed scope by 10,000 entries

WRUN-37 — Build a 30,000-entry scope with `scripts/wadoku_prefix_scope.py --size 30000 --link-closed --extend-from OLD_SCOPE/scope.json --output NEW_SCOPE/scope.json`. The selector retains all 20,000 old entry IDs and their canonical source records. It adds exactly 10,000 source-order entries with transitive reference closure. Verify the new scope count, exact overlap, unchanged retained hashes, and no resolvable links outside the scope.

WRUN-38 — Run `scripts/wadoku_repeat_run.py --scope-id NEW_ID --work-dir NEW_SCOPE/run --concurrency 80 --articles-per-batch 6 --reuse-from-scope OLD_ID --reuse-from-run OLD_RUN --stop-after-classification --event-log NEW_SCOPE/run/pipeline.jsonl`. The command revalidates and reuses old classification decisions when the current candidate context matches, then classifies the rest with Luna. It stops if any classification remains unresolved.

WRUN-39 — Prepare the full new run with `scripts/wadoku_translate_window.py --scope-id NEW_ID --candidate-scope --prepare-only --reuse-from-run OLD_RUN --articles-per-batch 6 --context-budget 192000`. The script copies only accepted targets whose article, unit pointer, role and source hash match, before making new batches. Translate a representative first window with the frozen run ID, inspect its complete answers in the main thread, and check usage and rejection rates. Resume the remaining batches only after that check. Then generate the review packet, both Yomitan ZIP files and the diagnostic site using WRUN-25 through WRUN-31.
