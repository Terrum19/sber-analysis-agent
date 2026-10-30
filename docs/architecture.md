# Архитектура SberAnalyticalAgent

## Конвейер одного хода диалога

```
вопрос пользователя
  |
  v
[0] Router (правила, <10 мс) --> простой вопрос: шаблонный план без LLM
  |        «сколько строк», «общая сумма», «топ-N», «по месяцам», «распределение»
  |
  v
[1] Planner (LLM + Instructor) --> ResearchPlan {steps[], final_viz, dashboard_requested,
  |        dashboard_title, template_id, dashboard_tiles[]} (план дашборда: docs/visualization.md)
  |        вход: вопрос, топ-5 релевантных колонок (semantic_select), контекст сессии
  |        пустой план / неизвестный инструмент
  v
[1b] Toolgen (опц.): LLM проектирует декларативную спеку (SQL-шаблон или Python
  |        для WASM-песочницы) -> статические проверки -> smoke/reference/checks
  |        на выборке входной таблицы -> регистрация в ToolRegistry -> перепланирование.
  |        Код LLM в процесс бэкенда не импортируется (docs/architecture.md, sandbox.md).
  v
[2] StepExecutor: последовательное применение шагов
  |        тул = предзаписанный SQL/стат-тест (подстановка колонок)
  |        аргументы шага: позиционные columns + именованные arguments; имя
  |        аргумента может совпадать с параметром тула (columns у select_columns)
  |        нет тула -> WASM-песочница (кодогенерация LLM)
  |        каждый шаг -> temp_result_vN в DuckDB (state cache)
  |        устаревшие temp_result_vN удаляются по RESULT_TTL_S (docs/memory.md)
  |        SQL выполняется в рабочем потоке с SQL_TIMEOUT_S и interrupt()
  |        сбой -> recovery: планировщик исправляет шаг по тексту ошибки
  |        (AGENT_STEP_FIX_ATTEMPTS) -> уточнение у пользователя
  v
[3] InsightGenerator (LLM или шаблон для одной ячейки) --> Insight {headline, details, limits}
  |
  v
[4] Визуализация: chart_selector -> vega_spec_builder (Vega-Lite JSON)
  |        смена вида: chart_morph (БЕЗ нового вызова LLM, из state cache)
  v
[5] (опц.) DashboardGenerator -> DashboardStore (SQLite: плитки, Vega, снимки данных)
  |        плитки — из dashboard_tiles плана: по плитке на шаг, вывод LLM — только
  |        у insight_needed; плитки считаются параллельно (AGENT_MAX_PARALLEL_LLM)
  v
[6] (опц.) Экспорт: ExcelExporter (.xlsx) / PDFExporter (.pdf)
```

## Ключевые решения

| Решение | Обоснование |
|---|---|
| **DuckDB как единый движок** | SQL в едином диалекте к CSV/PostgreSQL/ClickHouse без предобработки |
| **ResearchStep + реестр тулов** | расширяемость: новый тул = файл, оркестратор не меняется |
| **Toolgen: спека, а не Python-модуль** | LLM создаёт декларативную спеку; SQL проверяется по AST DuckDB, Python исполняется только в WASM — импорт кода LLM в процесс бэкенда был бы RCE |
| **Семантический отбор топ-5 колонок** | схема неконтролируемого размера не влезает в контекст LLM |
| **WASM-песочница вместо Docker** | инкапсуляция кодогенерации без «жирного» контейнера |
| **chart_morph без LLM** | «покажи в другом виде» — быстро и дёшево, не нагружая локальную модель |
| **Vega-Lite JSON-конфиги** | фронт рендерит спеку; бэк не занимается отрисовкой |
| **4 уровня памяти** | сессия (последние 3 хода) / кеш результатов (DuckDB) / паспорт данных (DuckDB `_meta`) / реестр дашбордов |
| **Замкнутый контур** | Ollama/vLLM локально; интернет — только к разрешённым доменам (HuggingFace, пакеты) |
| **Контекст LLM на сервере, проверка в клиенте** | Ollama молча обрезает начало длинного промпта, а `options.num_ctx` в `/v1` не действует (0.24, 0.35.1). Контекст задаёт сервер (`LLM_NUM_CTX` = `OLLAMA_CONTEXT_LENGTH` / `--max-model-len`); клиент сверяет `usage.prompt_tokens` с контекстом сервера (0.24 упирается в него) и с оценкой по символам вместе со схемой тулов (0.35.1 оставляет ~половину контекста); контекст сервера спрашивается при старте бэкенда и переспрашивается, пока сервер его не назовёт; бюджет промпта — `core/llm/budget.py` |

## Бюджет хода и деградация

Каждый ход ограничен жёстким дедлайном `AGENT_TURN_TIMEOUT_S` (870 с = 14.5 мин).
`TurnBudget` (`core/agent/budget.py`) хранит общий остаток, лимиты этапов
(`planning`, `steps`, `insight`, `chart`, `dashboard`) и счётчик вызовов LLM.

| Что закончилось | Поведение |
|---|---|
| Общий дедлайн | `error` с текстом «Не удалось завершить ход за N минут» + `done{message:"timeout"}`; задача плана отменяется |
| Бюджет планирования | `clarification_needed`: попросить уточнить/сузить вопрос |
| Бюджет шага/SQL | шаг прерывается (`interrupt()`), recovery не тратит больше `AGENT_STEP_FIX_ATTEMPTS` |
| Бюджет инсайта | шаблонный `fallback_insight`: результат уже есть, LLM не вызывается |
| Бюджет дашборда | дашборд пропускается, текст и график остаются у пользователя |
| Лимит `AGENT_MAX_LLM_CALLS` | следующий `LLMClient.complete` поднимает `BudgetExceeded` до сети |

Лимиты: `AGENT_MAX_STEPS=8`, `LLM_MAX_TOKENS=1536`, `AGENT_MAX_REPLANS=1`,
`SQL_TIMEOUT_S=60`. Простые вопросы обслуживает `core/agent/router.py` без
планировщика; одна числовая ячейка получает шаблонный инсайт без LLM.
Метрики хода (`route`, `llm_calls`, `prompt_tokens`, `total_ms`, этапы) есть в
`done`, в итоговой строке лога и в SQLite (`GET /api/metrics/turns`, `/summary`).

## P1: semantic layer, DAG, typed schema, pool, cache

P1-возможности включаются флагами (`AGENT_SEMANTIC_LAYER`, `AGENT_DAG_EXECUTOR`,
`AGENT_TYPED_SCHEMA`, `AGENT_PLAN_CACHE`, `AGENT_RESULT_CACHE`), по умолчанию
выключены, чтобы не менять поведение P0.

- **Semantic layer** (`models/semantic.py`, `core/data/semantic_model.py`):
  метрики/измерения/joins хранятся в `_meta.semantic_model`, API —
  `GET/PUT/DELETE /api/datasources/tables/{table}/semantic-model`.
  `SemanticPlanner` маппит вопрос на метрики и время, `SemanticCompiler`
  собирает обычный `ResearchPlan` или SQL для dry-run.
- **DAG**: `ResearchStep.depends_on`, `PlanGraph` выводит зависимости из
  `$prev`/`$step_N`, `ParallelPlanExecutor` выполняет независимые слои
  параллельно; ошибка узла не запускает dependents.
- **Typed schema**: `core/tools/schema.py` строит `oneOf` по top-k тулам или
  компактный `tool.enum` для полного списка; `LLMClient.complete_structured`
  принимает `schema_override`.
- **Dry-run**: `DryRunValidator` проверяет SQL шагов через `EXPLAIN` до выполнения.
- **Model pool** (`core/llm/profiles.py`): роли planner/fast/sql/fallback,
  переопределения `LLM_MODEL_PLANNER/FAST/SQL` и `LLM_BASE_URL_FAST/SQL`.
- **Caches** (`core/cache/`): exact-match планы и результаты по
  question/schema/model/data_version, TTL и инвалидация по таблице.
- **Schema index** (`core/data/schema_index.py`): lexical всегда, embeddings —
  при установленном `sentence-transformers` и заданном `EMBED_MODEL`.

## Toolgen: инструмент как данные

Когда планировщик вернул пустой план (кроме просьбы `remember`) или сослался
на неизвестный инструмент, оркестратор вызывает `ToolFactory`
(`core/tools/toolgen/factory.py`). LLM возвращает `GeneratedToolSpec`:
SQL-шаблон с `{input}`/`{param}` либо Python-код, получающий `df` и `params`.
Спека проходит каскад:

1. статические правила — имя, параметры, AST DuckDB (один SELECT, только
   `{input}`/CTE, без DDL/DML, table functions, `SAMPLE`, `getenv`,
   `random/now/uuid`), AST Python (импорты, опасные вызовы, `result`);
2. сборка динамической Pydantic-модели аргументов и разбор
   `sample_arguments`;
3. smoke на выборке `TOOLGEN_SAMPLE_ROWS` строк: `EXPLAIN`/исполнение,
   непустой результат (если не `allow_empty`), уникальные колонки;
4. `reference_sql` с независимым запросом или SQL-`checks` над
   `__tool_result` — сравнение результатов.

Только после этого инструмент регистрируется
(`ToolRegistry.register_generated`) и, при `TOOLGEN_STORE=true`, сохраняется в
`STORAGE_DIR/tools/generated/<name>.json`; при старте проверенные спеки
загружаются снова. SQL исполняется в DuckDB с повторной AST-проверкой при
каждом вызове, Python — только в `WASMSandbox`. При неудаче поведение
прежнее: `clarification_needed`; `run_python` остаётся для разовых
вычислений. Лимиты — `TOOLGEN_MAX_TOOLS`, `TOOLGEN_MAX_PER_TURN`,
`TOOLGEN_ATTEMPTS`, `TOOLGEN_MAX_SQL_CHARS`, `TOOLGEN_MAX_CODE_CHARS`.


## Память

Подробности — в [memory.md](memory.md).

## События стрима чата

`POST /chat` отдаёт SSE: каждое событие — `ChatEvent {type, data}`
(`app/api/schemas/chat.py`).

| type | data | когда |
|---|---|---|
| `progress` | `ProgressPayload` (ниже) | по ходу хода: план, шаги, перепланирование, инсайт, график, дашборд |
| `table` | `{table, previous, title}` | агент сам взял таблицу под вопрос до плана: у сессии её не было (`previous: null`) или вопрос явно про другие, несвязанные данные. `title` — название таблицы (#211) или `null`. Фронт переносит ленту на эту таблицу и пишет «Беру данные «…»» |
| `text` | `{chunk}` — дельта текста при `INSIGHT_STREAM=true`; `{headline, details}` — вывод целиком, когда стрим выключен или сработал фолбэк | инсайт готов / стримится |
| `chart` | `{spec, caption, result_table?}` | Vega-Lite спека (нет, если план выбрал `final_viz: "none"`); `result_table` — `temp_result_vN` ответа (нет, если ответ без шагов): по нему плитка пересчитывается, а результат выгружается в Excel |
| `dashboard` | `DashboardPayload {dashboard_id, title, tiles}` | дашборд собран по запросу; приходит после `chart` (график и инсайт ответа отдаются и при запросе дашборда) |
| `clarification_needed` | `{step_id, error, suggestions}`, может быть `prompt_truncated` | шаг не выполнился, ждём `recovery_response`; `prompt_truncated` — как в `done` |
| `error` | `{message}` | сбой хода; дальше `done` |
| `done` | `{turn_id, route, llm_calls, prompt_tokens, total_ms}` (+ `message`, `prompt_truncated`) | ход завершён; `prompt_truncated` — сколько промптов хода сервер LLM обрезал (только если > 0); `message="timeout"` — сработал жёсткий дедлайн |

`ProgressPayload`: `stage` (`planning` / `replanning` / `toolgen` / `step` / `insight` /
`chart` / `dashboard`), `status` (`started` / `substage` / `finished` /
`failed` / `fixing`) и необязательные `step_id`, `tool`, `purpose`,
`total_steps`, `planned_steps`, `elapsed_ms`, `attempt`, `error`, `substage` —
пустые поля в событие не попадают. `planned_steps` — типизированный список
`{step_id, purpose?, tool?}`: `purpose` — назначение от модели, если его нет,
фронт показывает `tool`; «Шаг N» формируется на клиенте через i18n.
`substage` — реальная фаза планирования (`semantic` / `llm` / `validation`),
фронт рисует её под активной стадией. `elapsed_ms` у этапов (`planning`,
`replanning`, `toolgen`, `insight`, `chart`, `dashboard`) замеряется `perf_counter()` на
сервере и не включает сетевую задержку.

События прогресса по порядку:

| stage | status | поля | когда |
|---|---|---|---|
| `planning` | `started` | — | планировщик получил вопрос |
| `planning` | `substage` | `substage` | реальная фаза: `semantic` (отбор колонок/инструментов), `llm` (вызов модели), `validation` (проверка плана) |
| `planning` | `failed` | `error`, `elapsed_ms` | планировщик упал (LLM недоступна), дальше `error` и `done` |
| `planning` | `finished` | `total_steps`, `planned_steps`, `elapsed_ms` | план готов (`0` — инструментов нет; дальше toolgen или `clarification_needed`) |
| `toolgen` | `started` | `purpose`, `tool?` | агент создаёт инструмент (toolgen включён и есть LLM) |
| `toolgen` | `finished` | `tool`, `purpose`, `elapsed_ms` | инструмент проверен и зарегистрирован; дальше перепланирование |
| `toolgen` | `failed` | `purpose`, `elapsed_ms`, `error` | создать не удалось; дальше прежнее `clarification_needed` |
| `step` | `started` | `step_id`, `tool`, `purpose` | шаг начал выполняться |
| `step` | `fixing` | `step_id`, `tool`, `purpose`, `attempt`, `error` | шаг упал, планировщик исправляет его |
| `step` | `finished` | `step_id`, `tool`, `purpose`, `elapsed_ms` | шаг выполнен (время — с учётом исправлений) |
| `step` | `failed` | `step_id`, `tool`, `purpose`, `elapsed_ms`, `error` | шаг не исправился, дальше `clarification_needed` |
| `replanning` | `started` | — | lookup-шаг выполнен, план достраивается по увиденному |
| `replanning` | `substage` | `substage` | реальная фаза достройки плана |
| `replanning` | `finished` | `total_steps`, `planned_steps`, `elapsed_ms` | новый план готов, дальше новые `step` |
| `replanning` | `failed` | `error`, `elapsed_ms` | перепланирование упало, дальше `clarification_needed` |
| `insight` | `started` | — | план выполнен, генерируется инсайт (дальше `text {chunk}` или один `text {headline, details}` при выключенном стриме/фолбэке) |
| `insight` | `finished` | `elapsed_ms` | инсайт готов, дальше `text` |
| `chart` | `started` | — | строится график |
| `chart` | `finished` | `elapsed_ms` | график готов, дальше `chart` |
| `dashboard` | `started` | — | собирается дашборд (после `chart`) |
| `dashboard` | `finished` | `elapsed_ms` | дашборд готов, дальше `dashboard` |
| `insight` / `chart` | `failed` | `error`, `elapsed_ms` | этап упал, дальше `error` и `done` |
| `dashboard` | `failed` | `error`, `elapsed_ms` | дашборд не собран; ответ и график уже отданы, дальше `done` без `error` |

`planning` приходит на новый вопрос и на повторную попытку после toolgen (модель видит созданный инструмент); достройка плана после lookup-шага
идёт отдельной стадией `replanning` (её `finished`/`failed` закрывает
стадию явно, а не при старте следующего этапа), ответ на уточнение
(`recovery_response`) перепланирует через `_plan` и тоже шлёт `step` без
повторного `planning`. Во время `planning` и `replanning` приходят
`substage`-события из `StepPlanner.plan`: фронт показывает реальные фазы
`semantic` → `llm` → `validation`, а не таймерные догадки. У
`step/finished` и `step/failed` поля `tool` и `purpose` — от последней
версии шага (после исправления). Смена вида прошлого графика («сделай
таблицей») идёт без плана: сразу `chart` и `done`. Этапы `insight`,
`chart` и `dashboard` явно шлют `finished` (или `failed`) с `elapsed_ms`,
поэтому длительность стадии не зависит от момента прихода следующего
события.

План от LLM приходит одним structured-ответом, поэтому `planned_steps`
появляются только в `planning/finished` (и `replanning/finished`). Фронт
раскрывает их по одному; отдельные `step`-строки для шагов, уже
перечисленных в плане, не дублируются — статус, время и ошибка
показываются внутри раскрытого плана. Для настоящего построчного
стриминга плана нужен отдельный режим LLM-клиента
(`instructor.create_partial` / streaming JSON), это не входит в текущий
контракт `progress`.

### Стриминг вывода

При `INSIGHT_STREAM=true` (по умолчанию) финальный инсайт идёт дельтами:

`insight/started` → N × `text {chunk}` → `chart/started` → `chart` → `done`.

- Итоговый `text {headline, details}` не отправляется, если была хотя бы одна
  непустая дельта: фронт дописывает подряд идущие `text {chunk}` в одно сообщение.
- Первая строка текста — headline: она же `caption` графика, запасной заголовок
  плитки дашборда и headline в памяти сессии.
- Формат текста: первая строка — вывод; затем строки `- ` с уточнениями;
  последняя строка — `Ограничения: ...` (необязательна).
- Нет `complete_stream` или ошибка до первой дельты — structured-фолбэк
  (`generate()`): приходит один `text {headline, details}`.
- Ошибка после первой дельты не фолбэчится: `insight/failed` → `error` → `done`,
  уже показанный префикс остаётся в чате.
- `INSIGHT_STREAM=false` — прежний контракт: один `text {headline, details}`.
- Обрыв SSE (кнопка «стоп» на фронте) закрывает поток LLM: `run()` →
  `aclosing(_turn)` → отмена задачи стрима → `LLMClient.complete_stream`
  закрывает `AsyncStream`.
- Стримится только финальный инсайт; обзор таблицы и плитки дашборда —
  по-прежнему нестриминговые.
- Хук `_check_context` не читает SSE (иначе `aread()` забуферизует поток),
  поэтому для стримингового инсайта проверка `usage.prompt_tokens` не
  выполняется: промпт ограничен `SHOWN_ROWS=50`.


Вопрос про таблицу целиком («что передо мной?», «сколько строк и за какой
период?», «опиши данные») тоже идёт без плана (`core/data/overview.py`):
`insight/started` → `insight/finished` → `text` (`headline` — объём и
период из паспорта, `details` — описание LLM по абзацам) →
`chart/started` → `chart/finished` → `chart` (вид `table`, по строке на
колонку) → `done`. Вопрос должен целиком состоять из
обзорных фраз: «сколько строк по Москве?» идёт в планировщик. Описание
берётся из паспорта, LLM вызывается, только если паспорт изменился. LLM не
ответила — в `details` строка «Описание данных сейчас недоступно», факты и
таблица всё равно приходят. Таблица колонок не становится последним
графиком: «покажи в другом виде» перестраивает прошлый график исследования.

Ход всегда заканчивается `done` или `clarification_needed`: любой сбой
вне шагов (планировщик, инсайт, график) приходит как
`<stage>/failed` с текстом ошибки, затем `error` с сообщением для
пользователя и `done`. Упавший шаг сюда не относится — он идёт через
`fixing` и `clarification_needed`. Сбой дашборда ход не проваливает:
после `dashboard/failed` сразу `done`, ответ сохраняется в памяти сессии.

Пример успешного хода из одного шага без стриминга (`INSIGHT_STREAM=false`
или structured-фолбэк):

```json
{"type": "progress", "data": {"stage": "planning", "status": "started"}}
{"type": "progress", "data": {"stage": "planning", "status": "substage", "substage": "semantic"}}
{"type": "progress", "data": {"stage": "planning", "status": "substage", "substage": "llm"}}
{"type": "progress", "data": {"stage": "planning", "status": "substage", "substage": "validation"}}
{"type": "progress", "data": {"stage": "planning", "status": "finished", "total_steps": 1,
 "planned_steps": [{"step_id": 0, "purpose": "Выручка по регионам", "tool": "group_by"}],
 "elapsed_ms": 120}}
{"type": "progress", "data": {"stage": "step", "status": "started", "step_id": 0,
 "tool": "group_by", "purpose": "Выручка по регионам"}}
{"type": "progress", "data": {"stage": "step", "status": "finished", "step_id": 0,
 "tool": "group_by", "purpose": "Выручка по регионам", "elapsed_ms": 42}}
{"type": "progress", "data": {"stage": "insight", "status": "started"}}
{"type": "progress", "data": {"stage": "insight", "status": "finished", "elapsed_ms": 800}}
{"type": "text", "data": {"headline": "...", "details": ["..."]}}
{"type": "progress", "data": {"stage": "chart", "status": "started"}}
{"type": "progress", "data": {"stage": "chart", "status": "finished", "elapsed_ms": 15}}
{"type": "chart", "data": {"spec": {}, "caption": "..."}}
{"type": "done", "data": {}}
```

Тот же ход со стримингом (`INSIGHT_STREAM=true`): те же события плана и шагов,
затем дельты текста и график с подписью из первой строки.

```json
{"type": "progress", "data": {"stage": "insight", "status": "started"}}
{"type": "text", "data": {"chunk": "Выручка "}}
{"type": "text", "data": {"chunk": "выросла "}}
{"type": "text", "data": {"chunk": "на 10%\n- Рост в январе\nОграничения: только 2024 год"}}
{"type": "progress", "data": {"stage": "chart", "status": "started"}}
{"type": "chart", "data": {"spec": {}, "caption": "Выручка выросла на 10%"}}
{"type": "done", "data": {}}
```

Исправление шага:

```json
{"type": "progress", "data": {"stage": "step", "status": "fixing", "step_id": 1,
 "tool": "group_by", "purpose": "Выручка по регионам", "attempt": 1,
 "error": "Binder Error: Referenced column \"revenu\" not found ..."}}
```

`attempt` — номер попытки исправления (1..`STEP_FIX_ATTEMPTS`), `error` —
последняя ошибка выполнения шага (отклонённое валидацией исправление её не
меняет: причину отказа видит только планировщик). План и шаги идут задачей
(`AgentOrchestrator._research`), события из неё приходят через `emit` и
очередь в `run()` сразу, не дожидаясь конца плана; `insight` и `chart`
`run()` отдаёт сам (`yield`).

## Экспорт аналитических отчётов (PDF / Excel)

Экспорт оформляет ответ агента или целостный дашборд в готовый для стейкхолдеров документ через эндпоинты:
- `POST /api/artifacts/export` (`app/api/routes/artifacts.py`) — экспорт отдельного исследования или дашборда в .xlsx / .pdf;
- `GET /api/dashboards/{id}/export?format=xlsx|pdf` (`app/api/routes/dashboards.py`) — прямой экспорт дашборда.

### Конвейер экспорта PDF (`PdfExporter`)

```
ExportRequest {session_id, result_ref?, dashboard_id?, question?, headline?, details?, spec?}
   |
   +---> Проверка владения result_ref: плитка дашборда dashboard_id или ход сессии (owns_result)
   |
   +---> (опц.) charts.py: normalize_spec() + vl-convert-python -> chart_png (без сети)
   |
   +---> (опц.) DuckDBManager: чтение temp_result_vN (до 50 строк)
   |
   +---> PdfExporter (ReportLab Platypus + NumberedCanvas):
   |        1. Регистрация TrueType шрифта DejaVu Sans (кириллица без артефактов)
   |        2. XML-экранирование текста (xml.sax.saxutils.escape, ₽ -> руб.)
   |        3. Защита от LayoutError: усечение сверхдлинных текстов, разбивка на строки
   |        4. Сборка Flowables: Заголовок -> Зелёная линия (#088434) -> Вопрос -> Инсайт -> График -> Таблица
   |        5. Двухпроходная нумерация «Страница X из Y»
   v
FileResponse (application/pdf, inline/attachment)
```

Для дашбордов (`PdfExporter.export_dashboard`) формируется единый аналитический отчёт:
- Метаданные (название дашборда, дата среза, название базы данных/сессии, счётчик «Всего плиток»);
- Фирменная разделительная линия Сбера (`HRFlowable`, `#088434`);
- Сетка ключевых показателей (KPI);
- Карточки плиток с встроенным рендером диаграмм (`charts.py`), содержательными инсайтами и таблицами с автоподбором высоты.

### Конвейер экспорта Excel (`ExcelExporter`)

```
ExportRequest {session_id, result_ref?, dashboard_id?, question?, headline?, details?, spec?}
   |
   +---> Извлечение записей: DuckDBManager (temp_result_vN до 5000 строк) или spec.data.values / snapshot_data
   |
   +---> Защита от Formula Injection (CWE-1236): sanitize_excel_text / sanitize_excel_value
   |
   +---> Нормализация типов: timezone-aware datetime -> naive UTC, NaN -> "", Infinity -> строка, dict/list -> JSON
   |
   +---> Устранение Schema Drift: extract_table_columns (объединение ключей по всем строкам выборки)
   |
   +---> Одиночный отчёт:
   |        - Информационная карточка (Заголовок, Вопрос, Детали, Метаданные);
   |        - Стилизованная таблица (зебра-заливка #F7FAF7, шапка #088434, автофильтр, freeze panes);
   |        - Детерминированный подбор диаграммы по Vega-Lite spec.encoding (Bar/Line/Pie/Area) или эвристике данных;
   |        - Подавление диаграммы для чисто табличных представлений (is_table_spec);
   |        - Ограничение категорий на графике до 50 точек при сохранении всей таблицы.
   |
   +---> Дашборд:
            - Титульный лист «Сводка» с крупными плитками KPI (#F0F7F1), источником данных и реестром состава;
            - Гиперссылки для быстрой навигации к листам плиток (#'Имя_листа'!A1);
            - Отдельные листы под каждую плитку с кнопкой «← Вернуться в Сводку», снимком данных и диаграммой;
            - Автоматический расчёт ширины столбцов под длину содержимого.
   v
FileResponse (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)
```

### Ключевые гарантии и архитектурные решения

1. **Представительский формат (Executive Business Memo)**:
   - Отчёт структурирован для бизнес-пользователей: сразу видны вопрос, ключевой вывод (headline) и детали анализа, затем график и подтверждающие данные.
   - Векторная отрисовка ReportLab Platypus со строгой сеткой отступов, карточками с закруглением (`q_table`, `insight_table`) и корпоративной палитрой Сбера (#088434, #0F172A, #1E293B, #F8FAFC).
   - Единая фирменная зеленая линия-разделитель (#088434) как в PDF дашбордов, так и в PDF одиночных графиков.

2. **Полная безопасность Excel (CWE-1236 Formula Injection)**:
   - Все текстовые поля (заголовки, вопросы, детали, инсайты, названия колонок, названия листов) и ячейки проверяются: если строка начинается с `=`, `+`, `-`, `@`, `\t` или `\r`, она экранируется ведущим апострофом `'`, за исключением валидных чисел.

3. **Устойчивость к сложным типам данных и сменам схемы**:
   - `openpyxl` не поддерживает timezone-aware объекты `datetime` — они автоматически приводятся к naive datetime во избежание падения с `TypeError`.
   - Нестабильные схемы ответов (schema drift) корректно объединяются без потери колонок.

4. **Полностью автономный рендеринг графиков (`charts.py`)**:
   - Преобразование Vega-Lite спеки в растровый PNG (scale=2.0) выполняется библиотекой `vl-convert-python` на Rust V8.
   - Не требует запущенного Chromium, Node.js или сетевого доступа (полная совместимость с замкнутым контуром).
   - Автоматическая нормализация спеки: замена адаптивных значений фронтенда (`"width": "container"`) на фиксированные пропорции печатного листа.

5. **Корректная типографика и кириллица**:
   - Использование шрифта `DejaVu Sans` / `DejaVu Sans Bold`, упакованного в системный контейнер через `fonts-dejavu-core`.
   - Регистрация в ReportLab `pdfmetrics` с каскадным fallback-поиском путей (Linux, macOS, env vars).

6. **Безопасность и отказоустойчивость**:
   - **XSS / XML Injection Prevention**: пользовательский текст экранируется перед передачей в `Paragraph`, исключая сбои парсера `paraparser` и искажение стилей тегами (`<b>fake</b>` отобразится как текст).
   - **Устранение LayoutError**: длинные строки усекаются (`truncate_text`), ячейки таблицы ограничиваются (`MAX_CELL_LENGTH = 300`), а карточки разделены на построчные `Table`, предотвращая неделимые оверфлоу-блоки между страницами.
   - **Многопользовательская изоляция**: `result_ref` принимается, только если таблица принадлежит запросу. С `dashboard_id` — это таблица одной из плиток дашборда (дашборд должен существовать, сессия может быть уже удалена). Без него сессия `session_id` должна существовать, а `SessionMemory.owns_result` находит таблицу среди итога хода, таблицы его графика, таблиц шагов хода (`record_step(step, table)` пишет их в `results` хода) или таблиц ожидающего уточнения. Иначе — `404`.
   - **Проверка спецификации графика**: запрещены `data.url`, `data.sequence`, `data.graticule` и `data.sphere` в любом объекте `data` (в том числе в слоях и `lookup`); колонки данных с такими именами разрешены. `data.values` и `datasets` обрезаются до 1000 строк, спецификация — до 256 КБ.
   - **Ограничение рендера**: одновременно рендерится не больше двух графиков (`MAX_PARALLEL_RENDERS`), каждый в отдельном процессе с таймаутом 7 с; если слоты заняты дольше таймаута, отчёт собирается без графика.
   - **Строгие HTTP-коды**: при ошибках чтения или отсутствии таблицы возвращается `404 Not Found` вместо молчаливой генерации неполного документа. При внутренних ошибках генерации отдаётся статус `500` с подробным описанием ошибки.
