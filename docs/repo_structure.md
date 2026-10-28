# Что и где лежит: структура репозитория SberAnalyticalAgent

Документ описывает каждую папку и каждый файл репозитория: зачем он нужен
и какую роль играет в системе. Обновляется при добавлении новых модулей.

---

## Корень

```
sberAnalyticalAgent/
├── README.md              — что это, как поднять локально (одна команда)
├── CONTRIBUTING.md        — конвенции: как добавить тул, коннектор, промпт
├── Makefile               — make install / seed / hk-data / dev / up / up-gpu / test / lint / eval
├── .env.example           — пример конфигурации (LLM endpoint, пути, лимиты)
├── .gitignore             — рантайм (storage/), веса (models/), data/hk_datasets/, node_modules
├── docker-compose.yml     — backend + frontend + Ollama (профиль cpu или gpu)
├── backend/               — FastAPI + Python: весь интеллект системы
├── frontend/              — Next.js + React: чат и канвас
├── infra/                 — обвязка запуска (не в корне, чтобы корень был чистым)
├── docs/                  — живая документация по подсистемам (этот файл здесь же)
├── data/                  — демо-датасеты + бизнес-словари (для тестов/демо)
├── scripts/               — утилиты: seed-данные, скачивание модели, миграции
└── models/                — веса LLM (локально, НЕ в git)
```

---

## backend/ — ядро системы

```
backend/
├── pyproject.toml / requirements.txt  — зависимости (fastapi, duckdb, instructor...)
├── Dockerfile                          — образ backend для docker compose
├── app/
│   ├── main.py            — точка входа FastAPI: роуты, lifespan, /health
│   ├── config.py          — pydantic-settings: LLM URL/модель, пути, лимиты (из .env)
│   │
│   ├── api/               — тонкий HTTP/SSE слой, без бизнес-логики
│   │   ├── deps.py             — зависимости FastAPI (get_db: общий DuckDBManager)
│   │   ├── routes/
│   │   │   ├── sessions.py     — CRUD сессий диалога
│   │   │   ├── chat.py         — SSE-эндпоинт диалога (стрим текста/картинок/дашбордов)
│   │   │   ├── datasources.py  — подключение CSV / PostgreSQL / ClickHouse
│   │   │   ├── dashboards.py   — REST дашбордов: список, CRUD, пересчёт плиток
│   │   │   └── artifacts.py    — экспорт в Excel/PDF, скачивание
│   │   └── schemas/            — Pydantic-контракты API (фронт <-> бэк)
│   │       ├── chat.py         — ChatRequest, ChatEvent (text/chart/dashboard/error/done)
│   │       ├── datasource.py   — DatasourceCreate/Out
│   │       └── dashboard.py    — DashboardOut/TileOut/Create/Update, DashboardSummary
│   │
│   ├── core/              — ЯДРО: агент, данные, тулы, память, визуализация
│   │   ├── llm/
│   │   │   ├── client.py            — единый клиент к LLM (OpenAI-совместимый: vLLM/Ollama)
│   │   │   ├── instructor_service.py — structured output через Instructor + Pydantic
│   │   │   └── prompts/             — ВСЕ промпты системы, по файлу на задачу
│   │   │       ├── system_analyst.py   — системный промпт: «ты аналитик + тулы»
│   │   │       ├── schema_describe.py  — «что передо мной за данные» (сценарий №1)
│   │   │       ├── column_extract.py   — названия и описания колонок из документа-описания, ответ JSON
│   │   │       ├── step_planner.py     — вопрос -> список ResearchStep
│   │   │       ├── insight_summary.py  — результат -> вывод на бизнес-языке
│   │   │       └── chart_selector.py   — выбор типа визуализации
│   │   │
│   │   ├── agent/          — ОРКЕСТРАЦИЯ: конвейер исследования
│   │   │   ├── orchestrator.py    — главный цикл: вопрос -> план -> шаги -> инсайт -> viz
│   │   │   ├── planner.py         — NL -> [ResearchStep] (вызов LLM)
│   │   │   ├── step_executor.py   — последовательное выполнение шагов
│   │   │   ├── parser.py          — ROBUST-парсинг вывода LLM (несколько стратегий)
│   │   │   ├── insight.py         — генерация финального вывода
│   │   │   └── recovery.py        — ошибки: какие сбои шага исправляет планировщик
│   │   │
│   │   ├── data/           — слой данных поверх DuckDB
│   │   │   ├── duckdb_manager.py  — коннекты, temp-таблицы результатов (state cache)
│   │   │   ├── connectors/        — единый интерфейс коннекторов
│   │   │   │   ├── base.py            — BaseConnector (единый контракт)
│   │   │   │   ├── csv.py             — сырые CSV -> таблицы DuckDB (готов)
│   │   │   │   ├── factory.py         — build_connector: выбор коннектора по kind
│   │   │   │   ├── pg_dsn.py          — разбор/проверка DSN PostgreSQL, маскирование пароля
│   │   │   │   ├── postgres.py        — PostgreSQL -> DuckDB через postgres_scanner (готов)
│   │   │   │   └── clickhouse.py      — ClickHouse -> DuckDB через clickhouse_scanner
│   │   │   ├── ingest.py          — подключение: connect -> профиль -> описание таблицы в паспорт
│   │   │   ├── overview.py        — «что передо мной?»: описание таблицы от LLM, кеш в паспорте
│   │   │   ├── restore.py         — источники БД после рестарта: запомнить, переподключить, забыть
│   │   │   ├── profiling.py       — метрики: NaN%, min/max, медиана, квартили, std
│   │   │   ├── schema_enrichment.py — названия и описания колонок из описания таблицы -> паспорт
│   │   │   └── semantic_select.py — топ-5 колонок под запрос (семант. сходство)
│   │   │
│   │   ├── tools/          — РЕЕСТР ТУЛОВ (расширяемый; оркестратор не меняется)
│   │   │   ├── registry.py        — регистрация/диспетчеризация тулов по имени, алиасы, describe() для планировщика
│   │   │   ├── base.py            — BaseTool: вход -> DuckDB-таблица; кавычки для имён, проверка SQL-выражений; transforms_data
│   │   │   ├── inspect_temp_table.py — паспорт промежуточной таблицы: схема, типы, строки, образцы, статистика
│   │   │   ├── run_wasm_python.py — мост шага в WASM-песочницу (не зарегистрирован, пока нет песочницы)
│   │   │   ├── sql/               — предзаписанные SQL с подстановкой колонок
│   │   │   │   ├── atomic.py      — атомарные тулы: select/filter/create/rename, join/left join,
│   │   │   │   │                    group_by, pivot, time_bucket, lag/lead
│   │   │   │   ├── aggregate.py   — группировка + count
│   │   │   │   └── time_series.py — агрегация по month/quarter/week/day
│   │   │   ├── stats/             — статистические тесты (SciPy/NumPy); тулы-отчёты, данные не меняют
│   │   │   │   ├── _common.py           — чтение выборок, типизированные литералы, сохранение результата
│   │   │   │   ├── correlation.py       — корреляция двух колонок средствами DuckDB
│   │   │   │   ├── pearson_corr.py / spearman_corr.py — корреляции с p_value
│   │   │   │   ├── linear_regression.py — y ~ x: slope, intercept, r_squared, p_value, stderr
│   │   │   │   ├── anova_one_way.py     — ANOVA по группам
│   │   │   │   ├── t_test_ind.py / t_test_dep.py — Welch t-test (выборки или группы), парный t-test
│   │   │   │   ├── mann_whitney_u.py    — тест Манна-Уитни
│   │   │   │   ├── chi_square.py        — хи-квадрат тест независимости
│   │   │   │   ├── ks_test.py / shapiro_wilk.py — проверка нормальности
│   │   │   │   ├── describe_stats.py    — n, mean, std, min, max, квартили, skew, kurtosis
│   │   │   │   ├── bootstrap_ci.py / bootstrap_corr.py — bootstrap-ДИ статистики и корреляции
│   │   │   │   ├── detect_anomalies.py  — выбросы по IQR или z-score (в группе, по периодам), score
│   │   │   │   └── search_categorical_values.py — fuzzy-поиск реальных значений категории
│   │   │   ├── funnel/            — воронки и когорты
│   │   │   │   ├── _common.py           — порядок шагов и подсчёт пользователей по шагам
│   │   │   │   ├── funnel_steps.py / funnel_drop_off.py — конверсия и потери между шагами
│   │   │   │   └── cohort_analysis.py / retention_matrix.py — когорты и удержание
│   │   │   ├── domain/            — готовые аналитические пакеты
│   │   │   │   ├── _common.py           — ось времени, линейный тренд, сезоны, скользящее среднее
│   │   │   │   ├── revenue_analysis.py  — выручка: сумма, среднее, медиана, динамика, сегменты
│   │   │   │   ├── product_performance.py / avg_order_value.py — метрики товаров, средний чек
│   │   │   │   ├── conversion_funnel.py — полная воронка по пользователям
│   │   │   │   └── trend_analysis.py / seasonal_decompose.py — тренд, сезонные индексы, разложение
│   │   │   └── timeseries/        — анализ временных рядов
│   │   │       ├── seasonality.py — сезонные пики (каждый июль / 12 месяцев)
│   │   │       ├── period_compare.py  — один месяц/квартал/день недели по годам, отклонения от средних
│   │   │       ├── lag_correlation.py — corr(x, y со сдвигом lag) для lag = 0..max_lag
│   │   │       └── forecast.py    — экстраполяция линейным трендом на N периодов
│   │   │
│   │   ├── sandbox/        — выполнение кода, сгенерированного LLM
│   │   │   ├── wasm_runtime.py    — WASM-песочница (Pyodide): лимиты RAM + таймаут
│   │   │   └── codegen_runner.py  — кодогенерация и прогон кода шага (когда тула нет)
│   │   │
│   │   ├── memory/         — 4 уровня памяти (см. docs/memory.md)
│   │   │   ├── datasource_store.py  — подключённые источники БД (SQLite, DSN без пароля)
│   │   │   ├── session_memory.py    — (1) диалог + шаги, sliding window, SQLite
│   │   │   ├── state_cache.py       — (2) результаты в temp-таблицах DuckDB
│   │   │   └── profile_memory.py    — (3) «паспорт данных»: профиль/названия/описания (DuckDB _meta)
│   │   │
│   │   ├── viz/            — визуализация
│   │   │   ├── chart_selector.py    — какой график под вопрос
│   │   │   ├── vega_spec_builder.py — таблица -> Vega-Lite JSON-спека
│   │   │   └── chart_morph.py       — перерисовать в другой вид БЕЗ вызова LLM
│   │   │
│   │   ├── dashboards/     — генерация и хранение дашбордов (4-й уровень памяти)
│   │   │   ├── models.py        — Dashboard, DashboardTile, DashboardSummary
│   │   │   ├── store.py         — DashboardStore: дашборды и плитки в SQLite сессий
│   │   │   ├── templates.py     — шаблоны дашбордов по ролям колонок, match(columns)
│   │   │   ├── generator.py     — собрать дашборд из плиток, снять данные, сохранить
│   │   │   └── recalculate.py   — пересчёт плиток по temp-таблицам (stale без таблицы)
│   │   │
│   │   └── export/         — выгрузка инсайта в делимый файл
│   │       ├── excel.py           — openpyxl: таблицы + графики -> .xlsx
│   │       └── pdf.py             — Typst/ReportLab -> .pdf-отчёт
│   │
│   ├── models/            — доменные Pydantic-модели (контракты конвейера)
│   │   ├── research_step.py   — ResearchStep: тул, колонки, parameters, флаг LLM-кодген
│   │   ├── insight.py         — Insight: headline, details, limits
│   │   ├── enrichment.py      — ColumnEnrichment/EnrichmentBatch: названия и описания колонок
│   │   └── datasource.py      — Datasource: тип, путь/DSN, таблицы
│   │
│   └── workers/           — опц.: фоновые задачи (тяжёлые отчёты, экспорт)
│
├── storage/               — РАНТАЙМ-состояние (gitignored)
│   ├── sessions/          — SQLite: истории диалогов, дашборды и их плитки
│   ├── duckdb/            — *.duckdb: сырые данные, temp-таблицы шагов, паспорт данных (_meta)
│   └── exports/           — .xlsx / .pdf для скачивания
│
└── tests/
    ├── unit/          — парсер шагов, chart_morph, семантический отбор, профилирование,
    │                    тулы (контракты, эталоны SciPy), цепочка шагов
    ├── integration/   — коннекторы -> DuckDB -> агент end-to-end, паспорт demo_events
    ├── benchmark/     — производительность профилирования (pytest -m benchmark)
    └── e2e/           — сценарии: «что передо мной» -> ответ -> график -> дашборд
```

---

## frontend/ — клиент

```
frontend/
├── package.json / tsconfig.json / next.config.mjs / tailwind.config.ts
├── src/
│   ├── app/                    — Next.js App Router
│   │   ├── layout.tsx / globals.css
│   │   ├── page.tsx                    — главная: создать сессию
│   │   ├── session/[id]/page.tsx       — экран диалога (чат + канвас)
│   │   └── dashboard/[id]/page.tsx     — просмотр сохранённого дашборда
│   ├── components/
│   │   ├── chat/ChatPanel.tsx          — ввод вопроса + стрим ответов (SSE)
│   │   ├── canvas/Canvas.tsx           — канвас: клик -> «дообследуй», drag&drop
│   │   ├── canvas/DashboardTile.tsx    — плитка: рендер Vega-Lite спеки
│   │   └── charts/ChartRenderer.tsx    — универсальный рендерер (Vega-Lite/ECharts)
│   ├── store/                  — Zustand: состояние сессии и канваса
│   │   ├── sessionStore.ts             — сообщения, плитки, выбранный элемент
│   │   └── canvasStore.ts              — раскладка, запрос дообследования
│   ├── api/client.ts           — клиент бэкенда: createSession, streamChat (SSE)
│   └── types/                  — общие типы (дубль контрактов backend schemas)
│       ├── chat.ts                     — события стрима чата
│       └── dashboard.ts                — конфиг дашборда
```

---

## infra/, docs/, data/, scripts/

```
infra/
├── local-dev.sh        — подъём backend + frontend локально (замкнутый контур)
├── nginx.conf         — опц.: раздача фронта + прокси API (SSE без буферизации)
├── ollama/entrypoint.sh — Ollama в compose: сервер + скачивание LLM_MODEL при первом запуске
└── vllm/serve.sh      — запуск Qwen через vLLM (GPU); для CPU/NPU — Ollama

docs/
├── architecture.md     — конвейер одного хода диалога, ключевые решения
├── repo_structure.md   — ЭТОТ документ (что и где лежит)
├── prompts.md          — назначение каждого промпта и как его менять
├── memory.md           — 4 уровня памяти: реализация и жизненный цикл
├── data_connectors.md  — как добавить новый источник (CSV/PG/CH)
├── visualization.md    — Vega-Lite спеки, смена вида без LLM, рендер на фронте
├── eval.md             — eval агента: запуск, проверки, формат отчёта
├── reports/            — отчёты замеров (eval_<дата>_<модель>.md, llm_perf/)
└── roadmap.md          — MVP -> финал (чекпоинт 2 -> итог)

data/
├── sample_datasets/        — demo_users.csv, demo_events.csv (для тестов/демо)
├── hk_datasets/            — датасеты хакатона: только README, CSV вне git (.gitignore)
└── business_dictionary/    — пример бизнес-глоссария пользователя (опц. фича)

scripts/
├── download_model.sh  — подсказки по скачиванию весов Qwen (в models/, вне git)
├── seed_demo_data.py  — генерация демо-CSV (100 пользователей + 500 событий)
├── load_hk_datasets.py — загрузка и профиль датасетов хакатона (make hk-data)
├── eval.py            — eval агента на data/eval/questions.yaml (make eval, docs/eval.md)
├── run_profile_benchmark.py — бенчмарк profile_table
└── migrate.py         — опц.: миграции storage (SQLite/реестр)
```

---

## Ключевые решения, заложенные в структуру

- **Tool Registry + `core/tools/`** — новый стат-тест или SQL-шаблон добавляется
  файлом и регистрируется в `registry.py`, оркестратор (`orchestrator.py`) не
  меняется. Тул-отчёт (`transforms_data = False`) не меняет рабочую таблицу:
  следующий шаг получает те же данные.
- **`core/llm/prompts/` по файлам** — промпт описания колонок, планировщика шагов и инсайта
  версионируется и тестируется независимо от кода.
- **`parser.py` отдельным модулем** — robust-парсинг вывода LLM вынесен из
  планировщика, чтобы стратегии парсинга менялись/тестировались изолированно.
- **4 уровня памяти** — `memory/` (сессия, state cache, паспорт данных) и
  `dashboards/store.py`, каждый со своей зоной ответственности и хранилищем
  (SQLite / DuckDB temp / DuckDB _meta / SQLite).
- **`viz/chart_morph.py`** — «перерисуй в другом виде» решается без нового вызова
  LLM, из сохранённого состояния (state cache).
- **`dashboards/`** — плитка хранит Vega-спеку, temp-таблицу и снимок данных,
  поэтому дашборд доступен повторно из чата и из UI (`/dashboard/[id]`).
- **`sandbox/` изолирован** — WASM-рантайм подменяем, не задевая остальное.
- **`storage/` целиком в `.gitignore`** — рантайм не мусорит в репозитории;
  веса моделей (`models/`) тоже вне git.
