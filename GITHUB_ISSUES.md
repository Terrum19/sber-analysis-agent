# GitHub Issues — AI-Native BI (закрытый контур)

Всего 16 задач. Порядок: #1→#16. Метки: `infra, ingestion, profiler, llm, agent, sandbox, viz, ui`.

---
## #1. Infra: docker-compose + скелет API + контракты

Разработать базовую инфраструктуру, которая:
- Поднимает `api (FastAPI) + llm (llama.cpp-server) + ui (Next.js)` одной командой.
- Создаёт папки `/data/raw, /data/cache, /tmp/shared` и SQLite `platform.db`.
- Предоставляет health-чеки для каждого сервиса.

Зависимости от других задач:
- Нет. Это первая задача, от неё зависят все остальные.

Требования к реализации:
1. `docker-compose.yml`: сервисы api (порт 8000), llm (порт 8080, image llama.cpp server + volume /models), ui (порт 3000). Сеть bridge между ними.
2. `src/main.py`: FastAPI с роутерами-заглушками `chat, sources, dashboards, artifacts` + `GET /health`.
3. `src/store/db.py`: создание таблиц sessions, messages, artifacts, dashboards, widgets, sources (схема из ARCHITECTURE.md M9).
4. `src/config.py`: пути DATA_RAW, DATA_CACHE, TMP_SHARED, LLM_URL, MODEL_ID через env.

Тесты:
- `tests/test_infra.py`: GET /health == 200; таблицы в platform.db существуют.

Критерии приёмки:
- `docker compose up --build` поднимается без интернета (после build).
- `curl localhost:8000/health` → {"status":"ok"}.
- Папки и platform.db создаются автоматически.

План работы (2 часа): compose (40 мин), FastAPI скелет (40 мин), SQLite схема (30 мин), тесты (30 мин).

## #2. Ingestion: CSV Loader в DuckDB-кэш

Разработать модуль загрузки сырых CSV, который:
- Принимает CSV в произвольном виде без предобработки снаружи.
- Регистрирует VIEW в DuckDB и материализует Parquet-кэш.
- Возвращает схему для профайлера.

Зависимости:
- Требует #1 (api скелет, папки, конфиг).

Требования к реализации:
1. Создать `src/ingestion/csv_loader.py`:
```python
def load_csv(path: str, table_id: str) -> TableInfo:
    # 1. COPY оригинал в /data/raw/<table_id>.csv
    # 2. con.sql(f"CREATE OR REPLACE VIEW {table_id} AS SELECT * FROM read_csv_auto('/data/raw/...', HEADER=true, SAMPLE_SIZE=100000)")
    # 3. con.sql(f"COPY (SELECT * FROM {table_id}) TO '/data/cache/{table_id}.parquet' (FORMAT PARQUET)")
    # 4. con.sql(f"CREATE OR REPLACE VIEW {table_id} AS SELECT * FROM read_parquet('/data/cache/...')")
    # 5. вернуть {table_id, rows: SELECT COUNT(*), columns: DESCRIBE}
```
2. `POST /sources/csv` (multipart file + table_id опционально) → `{table_id, rows, columns[]}`.
3. Санитизация table_id: только `[a-z0-9_]` (защита от SQL-инъекции в имени VIEW).
4. `GET /sources` → список из sources-таблицы.

Тесты:
- Юнит: загрузка `tests/fixtures/appeals.csv` (100 строк, русские заголовки, пропуски) → rows==100, parquet-файл существует.
- Интеграционный: повторная загрузка того же table_id перезаписывает кэш (идемпотентность).

Критерии приёмки:
- Сырой CSV с кривыми типами грузится без ручной предобработки.
- Оригинал в /data/raw не меняется, аналитика читает только /data/cache.
- Время загрузки 100k строк < 10 сек.

План работы (3 часа): loader (1.5ч), API (30мин), тесты+фикстуры (1ч).

## #3. Ingestion: PG/CH read-only snapshotter

Разработать модуль снапшотов СУБД, который:
- Подключается к PostgreSQL/ClickHouse только на чтение.
- Выгружает указанные таблицы в Parquet-кэш.
- Агент никогда не ходит в прод напрямую.

Зависимости: требует #1, #2 (переиспользует создание VIEW → parquet).

Требования к реализации:
1. `src/ingestion/db_snapshot.py`:
```python
def snapshot_table(source_type: str, dsn: str, src_table: str, table_id: str) -> TableInfo:
    # postgres: INSTALL postgres; LOAD postgres; ATTACH '<dsn>' AS src (TYPE POSTGRES, READ_ONLY);
    # COPY (SELECT * FROM src.<src_table>) TO '/data/cache/<table_id>.parquet'
```
2. Для ClickHouse: через `clickhouse-connect` (`SELECT *` чанками 50k) если ATTACH недоступен, либо `INSTALL chdb`.
3. `POST /sources/db {type, dsn, tables[]}` → фоновый job, `GET /jobs/:id` прогресс.
4. DSN не хранить в открытом виде — только в env/job-памяти, в sources писать только `{type, src_table, rows, loaded_at}`.

Тесты: мок DuckDB ATTACH (docker pg/ch не обязательны); проверить SQL-генерацию и запись реестра.

Критерии приёмки:
- Снапшот 2 таблиц из локального PG работает, повторный снапшот обновляет parquet.
- Нет ни одного пути, где агентский SQL исполняется в исходной СУБД.

План работы (3 часа).
## #4. Profiler: каталог в DuckDB (стабильно на больших данных)

Разработать фоновый профайлер, который:
- По каждой колонке считает тип, null%, n_unique, min/max, топ-K, сэмплы.
- Пишет результат НЕ в JSON, а в `catalog.duckdb` (таблицы profile_columns + profile_topk) — стабильно на 10M+ строк.
- Отдаёт ужатый JSON-слепок только для промпта LLM (топ-3 таблицы, без сырых сэмплов).
- Закрывает сценарий №1 («Что передо мной? Период? Сколько?»).

Почему не catalog.json:
- JSON грузится целиком в память API: на 100 таблицах x 100 колонок с топами это сотни МБ и OOM.
- JSON нельзя запросить частично (дай только 1 колонку) и нельзя обновить конкурентно — любой повторный profile перетирает файл.
- DuckDB-файл даёт точечные SELECT, UPSERT по (table_id, column), транзакции и работу без pandas.

Зависимости: требует #2 (таблицы в кэше).

Требования к реализации
1. Хранилище (создать `src/discovery/store.py`):
```python
CATALOG_DB = '/data/cache/catalog.duckdb'
# schema:
# profile_columns(table_id TEXT, column_name TEXT, logical_type TEXT, count_big BIGINT,
#   null_pct DOUBLE, n_unique BIGINT, n_unique_exact BOOLEAN, min_val TEXT, max_val TEXT,
#   is_date_guess BOOLEAN, sample_size BIGINT, profiled_at TIMESTAMP, PRIMARY KEY(table_id, column_name))
# profile_topk(table_id TEXT, column_name TEXT, value TEXT, cnt BIGINT, rank INT,
#   PRIMARY KEY(table_id, column_name, rank))
# def upsert_column(p: ColumnProfile): ...  # DELETE+INSERT в транзакции или INSERT OR REPLACE
# def get_table_snapshot(table_id: str, max_cols=50, topk=5) -> dict  # ужатый JSON для промпта
# def get_column(table_id, column) -> dict  # точечный запрос для UI
```
2. Двухфазный профайлинг (создать `src/discovery/profiler.py`):
```python
SAMPLE_ROWS = 200_000  # порог: если COUNT(*) > SAMPLE_ROWS → дорогие метрики по сэмплу
@dataclass
class ColumnProfile:
    table: str; column: str; logical_type: str
    count: int; null_pct: float; n_unique: int; n_unique_exact: bool
    min_val: str|None; max_val: str|None
    topk: list[tuple[str,int]]; samples: list[str]; sample_size: int

def profile_table(table_id: str) -> list[ColumnProfile]:
    # Фаза A (точно, по всей таблице, 1 проход):
    #   rows = SELECT COUNT(*) FROM t
    #   cols = DESCRIBE SELECT * FROM t
    # Фаза B (по колонке):
    #   если rows <= SAMPLE_ROWS:
    #     SELECT COUNT(*), SUM(col IS NULL), APPROX_COUNT_DISTINCT(col), MIN(col), MAX(col)
    #     SELECT col, COUNT(*) c GROUP BY col ORDER BY c DESC LIMIT 20  (точный топ-20)
    #   иначе (большая таблица):
    #     создать TEMP VIEW s AS SELECT * FROM t USING SAMPLE 200000 ROWS
    #     null_pct, MIN/MAX считать по s (дешево и достаточно для UI)
    #     n_unique = APPROX_COUNT_DISTINCT(col) по s, n_unique_exact=False
    #     топ-K через сэмпл: SELECT col, COUNT(*) GROUP BY col ORDER BY 2 DESC LIMIT 20
    #   samples: SELECT col WHERE col IS NOT NULL LIMIT 5 (по s или t — без разницы)
```
3. Правила стабильности:
- Никаких SELECT DISTINCT без LIMIT. Никакого GROUP BY по всей 10M-таблице — только по сэмплу.
- `APPROX_COUNT_DISTINCT` вместо `COUNT(DISTINCT)` всегда (на 10M это разница 30с vs 5мин).
- MIN/MAX только для numeric/date (по DESCRIBE-типу); для VARCHAR пропустить (дорого и бесполезно).
- Один проход на колонку, не три: null_pct + approx_unique + min/max в одном SELECT.
- Memory guard: `PRAGMA memory_limit='1GB'; PRAGMA threads=4` в соединении профайлера.
4. Автоопределение дат: только если logical_type VARCHAR И n_unique/rows < 0.5 (иначе TRY_CAST по 10M строк — вечность): `SELECT AVG(TRY_CAST(col AS DATE) IS NOT NULL) FROM s` → is_date_guess = avg > 0.5.
5. API: `POST /sources/:id/profile` → job с прогрессом (колонка i/N), `GET /sources/:id/catalog` → ужатый snapshot (не весь DuckDB!), `GET /sources/:id/columns/:col` → точечный профиль + топ-20.

Тесты
- Модульные (`tests/unit/test_profiler.py`): на `appeals.csv` (100 строк) — null_pct, топ-5, min/max даты совпадают с ручным подсчётом; мок rows > SAMPLE_ROWS → убедиться что GROUP BY идёт по сэмплу (проверить SQL-строку содержит USING SAMPLE).
- Интеграционные (`tests/integration/test_profiler_big.py`): сгенерить 1M строк (`scripts/gen_big_fixture.py`) → профиль < 60 сек, n_unique_exact=False, catalog.duckdb < 5 МБ.
- Стабильность: повторный profile той же таблицы идемпотентен (строк в profile_columns столько же, не дубли).

Критерии приёмки
- Таблица 100k x 20 колонок < 15 сек; 1M строк < 60 сек; 10M строк не роняет API (сэмпл-режим).
- catalog.duckdb перечитывается точечными SELECT без загрузки всего каталога в память.
- Snapshot для промпта < 8 КБ на таблицу (влезает в контекст даже при 5 таблицах).
- На основе snapshot можно ответить «сколько строк, за какой период, какие колонки».

План работы (предлагаемый)
- Хранилище store.py + схема (1 час): catalog.duckdb, UPSERT, snapshot/get_column.
- Двухфазный profiler.py (2 часа): фаза A/B, сэмпл-режим, memory guard, угадывание дат.
- API + job с прогрессом (1 час).
- Тесты + gen_big_fixture на 1M строк (1.5 часа).

## #5. Join Graph: автопоиск связей (таблица в catalog.duckdb, без JSON-файла)

Разработать модуль поиска внешних ключей, который:
- Находит пары колонок из разных таблиц с пересекающимися ID.
- Сохраняет рёбра в таблицу `join_edges` в `catalog.duckdb` (НЕ в join_graph.json — файл целиком грузится в память и перетирается при rebuild).
- Используется в Text-to-SQL промпте для корректных JOIN (точечный SELECT рёбер нужных таблиц).

Зависимости: требует #4 (profile_columns в catalog.duckdb + get_table_snapshot).

Требования к реализации:
1. Схема (в `src/discovery/store.py`, тот же catalog.duckdb что в #4):
```python
# join_edges(a_table TEXT, a_col TEXT, b_table TEXT, b_col TEXT,
#   containment DOUBLE, jaccard DOUBLE, checked_at TIMESTAMP,
#   PRIMARY KEY(a_table, a_col, b_table, b_col))
# def upsert_edge(e: JoinEdge): ...  # INSERT OR REPLACE, без перетирания чужих рёбер
# def get_edges(table_ids: list[str]) -> list[JoinEdge]  # только рёбра нужных таблиц для промпта
```
2. `src/discovery/joins.py`:
```python
def find_joins(table_ids: list[str]) -> list[JoinEdge]:
    # кандидаты из profile_columns: n_unique>100 AND n_unique/rows>0.2 OR re.match(r'.*(id|key|код)', col, re.I)
    # по каждой паре: SELECT DISTINCT col LIMIT 20000 → set A, set B (в API-процессе, не в sandbox)
    # containment = |A∩B|/min(|A|,|B|); jaccard = |A∩B|/|A∪B|
    # ребро если containment > 0.8; upsert_edge сразу, не копить в памяти
```
3. `POST /discovery/joins/rebuild {table_ids[]}` → считает и пишет в таблицу, возвращает `{edges_found: N}`. `GET /discovery/joins?tables=a,b` → точечный SELECT.
4. Исключить пары внутри одной таблицы и колонки с n_unique < 50. На больших таблицах DISTINCT идёт по `USING SAMPLE 20000`.

Тесты:
- Фикстуры `appeals.csv (executor_id)` + `executors.csv (id)` с 95% пересечением → ребро в таблице, containment > 0.8.
- Две несвязанные колонки → ребра нет.
- Повторный rebuild идемпотентен (UPSERT, дублей нет).

Критерии приёмки:
- Для 2 таблиц по 50k строк поиск < 20 сек.
- Precision на синтетике: нет ложных рёбер при пороге 0.8.
- `get_edges(['appeals'])` не грузит рёбра чужих таблиц.

План работы (2.5 часа).

## #6. Semantic Layer: словарь бизнес-языком (таблицы в catalog.duckdb, без YAML-файла)

Разработать модуль семантического обогащения, который:
- Один раз проходит LLM по профилю колонок и генерит человекочитаемый словарь.
- Сохраняет в таблицы `semantic_tables + semantic_columns + semantic_synonyms` в `catalog.duckdb` (НЕ в semantics.yaml: YAML перетирается целиком, не правится точечно, ломается при concurrent enrich).
- Закрывает сценарий №2 («Что означает поле?»).

Зависимости: требует #4 (профиль), #5 (рёбра), #7 (LLM клиент) — но API-контракт можно пилить параллельно на моке.

Требования к реализации:
1. Схема (тот же catalog.duckdb):
```python
# semantic_tables(table_id TEXT PK, business_name TEXT, description TEXT, updated_at TIMESTAMP)
# semantic_columns(table_id TEXT, column_name TEXT, business_name TEXT, description TEXT, updated_at TIMESTAMP,
#   PRIMARY KEY(table_id, column_name))
# semantic_synonyms(table_id TEXT, column_name TEXT, synonym TEXT, PRIMARY KEY(table_id, column_name, synonym))
# def upsert_semantics(table_id, cols: list[ColumnSemantics]): ...  # merge по (table,column), ручные правки не затираются если updated_at новее
# def get_semantics_snapshot(table_ids: list[str]) -> dict  # ужатый слепок <8КБ для промпта Text-to-SQL
```
2. `src/discovery/enrich.py`: промпт из `prompts/enrich.md` + `{column, logical_type, top5, samples}` → LLM (JSON-режим по проводу, НЕ файл) → валидация pydantic `ColumnSemantics{business_name, description, synonyms[], examples[]}` → upsert в таблицы.
3. `POST /sources/:id/enrich` → генерация, `GET /sources/:id/semantics` → слепок из таблиц. `PUT /semantics` → `UPDATE semantic_columns` (точечно, без перезаписи всего словаря).
4. Промпт на русском, business_name обязательно на русском, synonyms 2-4 шт.

Тесты:
- Мок LLM: проверить upsert/merge, точечный UPDATE не трогает соседние колонки.
- Живой тест: 5 колонок appeals → бизнес-названия осмысленны (ручная проверка).

Критерии приёмки:
- Парсинг ответа LLM стабилен (JSON-mode + retry 3), битый ответ не портит существующие строки.
- Text-to-SQL со snapshot точнее чем без (проверить на 5 вопросах вручную).

План работы (3 часа): схема+upsert (1ч), enrich (1ч), API+тесты (1ч).
## #7. LLM: одна модель через llama.cpp (клиент + промпты)

Разработать LLM-слой, который:
- Подключается к локальному llama.cpp-server с Qwen2.5-Coder-14B.
- Даёт единый интерфейс chat/json для всех модулей агента.
- Хранит все системные промпты как файлы.

Зависимости: требует #1 (compose). Блокирует #6, #9, #10, #11, #14.

Требования к реализации:
1. `src/llm/client.py`:
```python
class LLMClient:
    def __init__(self, base_url: str, timeout=120): ...
    def chat(self, system: str, user: str, temperature=0.1) -> str: ...
    def chat_json(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        # response_format json_object + parse + retry 3 при ValidationError
```
2. `src/agent/prompts/*.md`: validator, router, text2sql, fix_sql, python_gen, viz, dashboard, answer, enrich. Промпты на русском, требовать ответ JSON где нужен парсинг.
3. `llm/Dockerfile` или models-инструкция: откуда брать `qwen2.5-coder-14b-instruct-q4_k_m.gguf`, команда `llama.cpp-server -m /models/qwen.gguf -c 32768 -n 4096`.
4. Fallback: env MODEL_ID позволяет подсунуть 7B без смены кода.

Тесты: мок httpx; chat_json с битой схемой → retry и затем ошибка с понятным текстом.

Критерии приёмки:
- `chat_json` стабильно парсит ответы модели (3/3 попыток на тестах).
- Все промпты лежат в файлах, а не захардкожены.
- Локальный инференс отвечает < 30 сек на Text-to-SQL запрос.

План работы (2.5 часа).

## #8. Agent: Validator + Router (grounding + выбор трека)

Разработать входной контур агента, который:
- Проверяет, что вопрос вообще про наши данные (grounding), иначе предлагает 2-3 переформулировки.
- Выбирает трек: sql | python | viz_only | dashboard.
- Не пускает «неудобные» вопросы в SQL.

Зависимости: требует #6 (semantics), #7 (LLM).

Требования к реализации:
1. `src/agent/validator.py`:
```python
class ValidationResult(BaseModel):
    grounded: bool; tables: list[str]; mapping: dict[str,str]
    issues: list[str]; suggestions: list[str]  # 2-3 переформулировки из реальных business_name
```
2. `src/agent/router.py`:
```python
class Route(BaseModel):
    track: Literal['sql','python','viz_only','dashboard']; reason: str
    # python если слова сезонность/прогноз/корреляция/лаг; dashboard если собери дашборд; viz_only если в другом виде + есть last_chart
```
3. `POST /chat` на этом этапе: если !grounded → вернуть suggestions; если viz_only без last_chart → попросить уточнить.
4. Юнит-тесты на 10 примерах: 3 grounded-sql, 2 python, 2 dashboard, 3 неgrounded.

Критерии приёмки:
- Вопрос «сколько стоит биткоин?» → suggestions, SQL не генерируется.
- «Покажи сезонность обращений» → track=python. «Собери дашборд» → track=dashboard.
- Точность роутера на 20 ручных вопросах >= 85%.

План работы (3 часа): промпты (1ч), код (1ч), тесты+калибровка (1ч).

## #9. Agent: Text-to-SQL + Self-Fix (до 3 попыток)

Разработать SQL-трек, который:
- Генерит DuckDB SQL из вопроса + semantics + join_graph.
- Валидирует через EXPLAIN dry-run и чинит ошибки до 3 попыток.
- Возвращает result.parquet (LIMIT 500) + схему.
- Закрывает сценарии №2, №3, №8.

Зависимости: требует #7, #8, #5 (join_graph для JOIN).

Требования к реализации:
1. `src/agent/text2sql.py`:
```python
def generate_sql(question: str, semantics: Semantics, joins: JoinGraph, history: History) -> str:
    # system=prompts/text2sql.md, user={question, semantics_ужатый, joins, last_sql}
    # правила: только SELECT, только известные таблицы/колонки, LIMIT 500, даты через TRY_CAST
def exec_with_fix(sql: str) -> DataFrame:
    # for attempt in 1..3: try EXPLAIN + SELECT → return; except Exception e → sql = fix_sql(sql, str(e))
```
2. Ужатие semantics: только таблицы из validator.tables (макс 3), иначе контекст переполнится.
3. Дубликаты (сценарий №8): подсказать в text2sql.md шаблон `GROUP BY <cols> HAVING COUNT(*)>1`.
4. Сохранение: `/tmp/shared/run_<id>/result.parquet` + `sql` в artifacts.

Тесты:
- `tests/fixtures/eval_sql.py` (EVAL_QUESTIONS: list[dict] как Python-константы, НЕ json-файл): 15 вопросов (агрегации, фильтры, топы, дубликаты, JOIN по executor_id) → метрика Execution Accuracy: SQL исполнился без ошибок И вернул непустой результат там где ожидается.
- Цель: >= 70% на 14B.
- Scripts: `scripts/eval_sql.py` считает метрику и пишет в `eval_runs(suite='sql', metrics_json)` + печатает в консоль.

Критерии приёмки:
- eval_sql >= 70%, self-fix чинит >= 50% изначально битых запросов.
- Ни один сгенерированный SQL не содержит DROP/DELETE/INSERT/UPDATE (блок-лист).
- Latency SQL-трека (без LLM ожидания) < 2 сек на запрос.

План работы (5 часов): text2sql+fix (2ч), eval-набор 15 вопросов (1.5ч), калибровка промпта (1.5ч).
## #10. Sandbox: Docker без сети (исполнение Python от LLM)

Разработать изолированную песочницу, которая:
- Исполняет Python-код от LLM без доступа к сети и к записи в /data.
- Принимает run.py, возвращает запись в SQLite `sandbox_runs` + `result.parquet` (НЕ stdout.json/chart.json файлы: их нельзя запросить частично, теряются при рестарте, плодят мусор в /tmp).
- Нужна для сценариев №7, №9, №10.

Зависимости: требует #1 (папки). Блокирует #11.

Требования к реализации:
1. `sandbox/Dockerfile`: `FROM python:3.11-slim; pip install duckdb pandas pyarrow statsmodels scikit-learn` (без jupyter, без requests/torch).
2. `src/sandbox/runner.py`:
```python
def run_in_sandbox(run_id: str, code: str, timeout=60) -> SandboxResult:
    # write /tmp/shared/run_<id>/run.py
    # docker run --rm --network none --memory 2g --cpus 2 -v /data:/data:ro -v /tmp/shared:/tmp:rw sandbox-worker python /tmp/run_<id>/run.py
    # run.py пишет метрики В СТРОКУ stdout (print(json.dumps({...}))) → перехватываем pipe, парсим, пишем в SQLite sandbox_runs(run_id, status, metrics_json, error, df_path)
    # НИКАКИХ stdout.json/chart.json файлов: chart_spec лежит в metrics_json в той же строке sandbox_runs
```
3. Внутри run.py разрешено только: `read_parquet('/data/cache/*.parquet')`, запись в `/tmp/run_<id>/`. Импорты из белого списка.
4. Проверка безопасности: тест с `import socket; socket.connect(...)` → ошибка; `open('/data/cache/x.parquet','w')` → PermissionError (read-only mount).

Тесты:
- Позитивный: скрипт считает mean по parquet → в sandbox_runs status=ok.
- Негативные: сеть недоступна; запись в /data запрещена; бесконечный цикл убивается по timeout.

Критерии приёмки:
- Все 4 теста проходят. Timeout 60с соблюдается. Образ собирается офлайн-зависимостями (requirements зафиксированы).

План работы (3 часа): Dockerfile (30мин), runner (1ч), тесты безопасности (1.5ч).

## #11. Agent Python-инструменты: сезонность, лаги, прогноз

Разработать 3 шаблона анализа, которые:
- LLM не пишет с нуля, а заполняет params-блок.
- Исполняются в sandbox (#10) поверх result.parquet.
- Закрывают сценарии №7, №9, №10.

Зависимости: требует #10, #7.

Требования к реализации:
1. `src/sandbox/templates/seasonality.py`: вход `{df_path, date_col, metric, freq}` → `seasonal_decompose + ACF` → выход `{period, seasonal_strength, chart_spec(line)}`.
2. `src/sandbox/templates/lag_corr.py`: вход `{df_path, date_col, col_c, col_d, max_lag}` → `for lag in 1..N: df[C].shift(lag).corr(df[D])` → `{best_lag, best_corr, table}`.
3. `src/sandbox/templates/forecast_whatif.py`: вход `{df_path, date_col, metric, horizon, whatif:{param, delta}}` → HoltWinters/LinearRegression → `{forecast[], whatif_result}`.
4. `src/agent/python_gen.py`: LLM по вопросу выбирает шаблон и пишет только JSON params (не весь код) → подстановка в шаблон → sandbox.

Тесты: синтетика `tests/fixtures/synth_{season,lag,trend}.csv` с известным периодом 12, лагом 5, трендом → шаблоны находят период=12, лаг=5, прогноз с MAPE < 20%.

Критерии приёмки:
- На синтетике все 3 шаблона дают правильный ответ без ручной правки.
- LLM-генерация params корректна в 4/5 случаев (ручная проверка на 5 вопросах).

План работы (5 часов): шаблоны (3ч), python_gen (1ч), синтетика+тесты (1ч).

## #12. Viz Engine: Vega-Lite spec в SQLite artifacts (формат по проводу, не файл)

Разработать модуль визуализации, который:
- Возвращает декларативный chart_spec, а не картинку.
- Меняет вид по фразе в другом виде без нового SQL.
- Закрывает сценарий №4.

Зависимости: требует #7, #9 (result_schema).

Требования к реализации:
1. `src/viz/spec.py`: `def make_spec(question, schema, sample_rows) -> ChartSpec` через prompts/viz.md. Правила: 1 метрика+категории→bar; дата+метрика→line; 2 числа→point; доля→arc.
2. `ChartSpec {mark, encoding:{x,y,color,tooltip}, title}` — валидация pydantic, рендер через vega-embed на фронте.
3. `POST /artifacts/:id/rechart {mark}` → меняет только mark в сохранённом spec, данные берёт из df_path.
4. Лимит данных для графика: top 50 точек (сэмпл на бэке, чтобы не тащить 500 строк в браузер зря — фронт режет сам).

Тесты: 6 пар (schema→ожидаемый mark) → spec валиден, mark угадан в 5/6.

Критерии приёмки:
- Фраза в другом виде не вызывает новый SQL (проверка по логам: нет DuckDB-запроса).
- Spec рендерится без ошибок в vega-embed.

План работы (2.5 часа).

## #13. Memory: контекст сессии (SQLite)

Разработать память диалога, которая:
- Хранит историю + last_sql/schema/chart/df_path.
- Подмешивает сжатый контекст в каждый промпт.
- Закрывает сценарий №6 (а теперь только за 2024).

Зависимости: требует #1 (platform.db), #9 (artifacts).

Требования к реализации:
1. `src/store/sessions.py`: `create_session, add_message(role,content,tool_json), get_context(session_id) -> {last_8_messages, last_sql, last_schema, last_chart, last_df_path}`.
2. `POST /chat` всегда читает контекст; `GET /sessions/:id/history` для UI.
3. Уточняющие вопросы (только за 2024) резолвятся модификацией last_sql: LLM получает last_sql + дельту и пишет новый SQL, а не с нуля.
4. Лимит истории в промпте: последние 8 сообщений, старые — дропаются (без суммаризации на MVP).

Тесты: сценарий из 3 реплик (вопрос → в другом виде → только за 2024) → второй шаг без SQL, третий с модифицированным SQL содержащим 2024.

Критерии приёмки:
- Контекстный диалог из `tests/fixtures/eval_context.py` (5 диалогов как Python-константы, НЕ json-файл: фикстуры должны импортироваться и типизироваться) проходит 4/5.
- Сессия переживает рестарт API (данные в SQLite, не в памяти).

План работы (2.5 часа).
## #14. Dashboard Builder: персистентные дашборды из чата

Разработать конструктор дашбордов, который:
- По команде собери дашборд генерит 3-4 виджета (динамика + срез + топ).
- Сохраняет sql+chart_spec в БД, отдаёт ссылку.
- Закрывает сценарий №5.

Зависимости: требует #9 (sql gen), #12 (spec), #13 (контекст).

Требования к реализации:
1. `src/dashboards/builder.py`: system=prompts/dashboard.md, вход `{session_context, title}` → выход `[{title, sql, chart_spec}]` → dry-run каждого SQL (EXPLAIN) → INSERT dashboards, widgets.
2. `POST /dashboards/from-chat {session_id, title}` → `{dashboard_id, url: /dashboards/:id}`. `GET /dashboards/:id` → `{title, widgets[{title, sql, chart_spec, data_url}]}`. `GET /dashboards` → список.
3. `GET /widgets/:id/data` → исполнить сохранённый sql свежими данными (переисполнение, не кэш).
4. Виджеты комплементарны: запретить 2 одинаковых mark подряд (проверка в коде, не надеяться на LLM).

Тесты: from-chat на сессии с appeals → 3 виджета, все SQL валидны, GET возвращает структуру.

Критерии приёмки:
- Дашборд переживает рестарт (SQLite). Открывается по ссылке. Кнопка обновить данные переисполняет SQL.

План работы (3 часа).

## #15. UI: Next.js Chat слева + Canvas справа

Разработать интерфейс, где:
- Слева чат с LLM (история, suggestions-кнопки, ввод).
- Справа канвас с табами Таблица / График / Дашборд-конструктор.
- Демо от Что передо мной? до Собери дашборд кликается без консоли.

Зависимости: требует все API (#2-#14) минимум как заглушки; верстать можно параллельно.

Требования к реализации:
1. `ui/` Next.js 14 + shadcn + zustand + vega-embed + tanstack-table + dnd-kit.
2. Роут `/`: `ChatPanel` (POST /chat, рендер suggestions как кликабельные чипы) + `Canvas` (табы; таблица из /artifacts/:id/data; график из chart_spec; кнопка в другом виде → POST /artifacts/:id/rechart).
3. Роут `/dashboards/:id`: сетка виджетов drag-n-drop, кнопка обновить.
4. Роут `/sources`: загрузка CSV (POST /sources/csv), список таблиц + статусы profile/enrich, кнопка Профилировать.
5. Next proxy: `/api/*` → `http://api:8000/*` (чтобы не было CORS-боли).

Тесты: ручной чек-лист demo.md (10 шагов от загрузки CSV до дашборда) + `pnpm build` без ошибок TS.

Критерии приёмки:
- Весь демо-сценарий проходится мышью. Графики читаются без пояснений (балл за UX).
- suggestions и переформулировки кликабельны. Дашборд открывается по ссылке из чата.

План работы (8 часов): layout+chat (3ч), canvas таблица+график (2ч), sources (1ч), dashboards (2ч).

## #16. Сквозной Eval + Demo-набор (приёмка всего)

Собрать оценочный стенд, который:
- Меряет качество всей платформы одной командой.
- Даёт демо-данные для чекпоинта №2 и финала.

Зависимости: требует всё (#1-#15).

Требования к реализации:
1. `data/demo/appeals.csv, executors.csv` (CSV — это сырые данные, им можно) + `tests/fixtures/eval_data.py` (EVAL_SQL 15 вопросов, EVAL_CONTEXT 5 диалогов, EVAL_CHARTS 6 схем как типизированные Python-константы, НЕ json-файлы).
2. `scripts/eval_all.py`: гоняет eval_sql (Execution Accuracy ≥70%), роутер (≥85%), контекст (4/5), rechart без SQL, sandbox-безопасность; пишет в SQLite `eval_runs(id, started_at, suite, passed, failed, metrics_json)` + печатает таблицу (НЕ report.json: прогоны должны быть сравнимы во времени через SELECT, а не перетиранием файла).
3. `scripts/benchmark.py`: latency SQL-трека < 2с (без LLM), ответ чата end-to-end (с 14B) < 60с.
4. `docs/demo.md`: пошаговый сценарий защиты (что кликать/спрашивать, ожидаемые ответы).

Критерии приёмки:
- `python scripts/eval_all.py` зелёный. Отчёт приложен к релизу заочного этапа.
- Демо по docs/demo.md репетируется за 7 минут (тайминг финала).

План работы (4 часа).

---
Суммарно: ~41 час кодинга. Неделя 1: #1-#9 + #13(min) + #15(chat+table). Неделя 2: #10-#12, #14-#16.
