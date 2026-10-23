# Память платформы (4 уровня)

## 1. Краткосрочная память сессии — `core/memory/session_memory.py`
- **Хранит:** историю текстового диалога + историю ResearchStep.
- **Зачем:** контекст для «А теперь покажи то же самое в другом виде».
- **Как:** SQLite/файл по `session_id`; в промпт идёт **скользящее окно**
  последних 3-5 сообщений + сжатая выжимка предыдущих шагов (state summary).
  Весь чат целиком в контекст не отдаём — убьёт контекст и замедлит генерацию.
- **Таблица сессии:** `set_table(table, columns)` привязывает сессию к
  таблице DuckDB, `table` возвращает её (`None` — данные не подключены).
  Без таблицы оркестратор отвечает событием `error` «Сначала подключите
  данные». Таблица задаётся при создании сессии (`POST /api/sessions`,
  поле `table` обязательно, неизвестная таблица — 404) и меняется через
  `PATCH /api/sessions/{id}`; колонки берутся из `DuckDBManager.columns()`.
- **Метаданные и список:** `set_meta(title, datasource_id)` при
  `POST /api/sessions` пишет в состояние сессии название, id источника и
  `created_at` (ISO, UTC). Источник правды — SQLite: `GET /api/sessions/{id}`
  собирает ответ из `SessionMemory` (404, если `exists()` ложно), сессия и
  её метаданные переживают перезапуск. `GET /api/sessions` — по 30 сессий
  для сайдбара по `updated_at`, недавно изменённые первыми (`?offset=` —
  следующие страницы)
  (`SessionMemory.list_sessions` — один запрос, метаданные через
  `json_extract`). При смене таблицы `datasource_id` сбрасывается: новая
  таблица может быть из другого источника; `PATCH` на ту же таблицу
  обновляет только колонки. Таблицы для
  выбора — `GET /api/datasources/tables` (`DuckDBManager.user_tables()`): таблицы и представления схемы
  `main` (включая временные представления PostgreSQL) без `temp_result_vN`
  и схемы `_meta`.
- **История по таблицам:** сообщения и последний график хранятся отдельно
  для каждой таблицы (`state["tables"][table]`); при возврате к таблице они
  снова доступны. Шаги и `pending_recovery` при смене таблицы сбрасываются.
- **Последний график:** `record_chart(chart_type, result_table, caption)` и
  `last_chart()` — вид и `temp_result_vN` последнего показанного графика
  текущей таблицы. По ним оркестратор перестраивает график на просьбу
  сменить вид без планировщика и без повторного SQL
  (см. [visualization.md](visualization.md)).

## 2. Память результатов и промежуточных данных — `core/data/duckdb_manager.py` (state cache)
- **Хранит:** результаты шагов как temp-таблицы `temp_result_vN` в DuckDB.
- **Зачем:** не пересчитывать тяжёлые запросы при смене визуализации.
- **Как:** каждый успешный шаг -> `save_step_result()` -> уникальная ссылка.
- **Чтение:** `StateCache.get(table, limit)` — срез temp-таблицы для графика
  (оркестратор берёт до 500 строк, как и `build_vega_spec`).
- **Очистка:** `temp_result_vN` — постоянные таблицы файла DuckDB, поэтому
  устаревшие удаляются (`core/data/result_retention.py`). `save_step_result()`
  пишет время создания в служебную таблицу `_meta.result_meta(name, created_at)`
  (схема `_meta` не попадает в список таблиц пользователя);
  `cleanup_results(keep, older_than_s)` удаляет `temp_result_vN` старше
  `RESULT_TTL_S` (по умолчанию 6 ч), кроме `keep`:
  - `result_table` всех плиток дашбордов (`dashboard_tiles` в SQLite) — без
    срока, плитки пересчитываются из этих таблиц;
  - `last_chart.result_table` всех таблиц (`state["tables"]`) сессий,
    обновлённых за последние 24 ч, — чтобы сменить вид графика без SQL.

  Таблица без строки в `_meta.result_meta` (создана до появления учёта) получает
  текущее время и живёт ещё полный срок. Строки удалённых таблиц остаются в
  `_meta.result_meta`: номера не используются повторно, и старая ссылка на
  `temp_result_vN` не укажет на чужие данные. Очистка идёт при старте
  приложения (`lifespan`) и в фоне раз в `RESULT_TTL_S / 4`; ошибка очистки
  только пишется в лог. Место в файле DuckDB после удаления не
  возвращается системе, а переиспользуется под новые таблицы.

## 3. Метапамять: паспорт данных и глоссарий — `core/memory/profile_memory.py`
- **Хранит:** структуру таблиц, статистику, названия и описания колонок из
  описания таблицы, бизнес-словарь.
- **Зачем:** не сканировать источник заново на каждый вопрос; агент
  всегда держит «паспорт» в контексте.
- **Как:** постоянные (не TEMP) таблицы схемы `_meta` в файле DuckDB
  (`DUCKDB_PATH`): переживают перезапуск приложения и не пересекаются с
  таблицами пользователя (коннекторы смотрят только в схему `main`).
  Профиль строит `ingest.attach()` сразу после подключения источника
  (`POST /api/datasources/*`, скрипты загрузки); названия и описания колонок
  дописывает `ingest.describe()`, если у таблицы есть описание. JSON-файлы
  `storage/profiles/` больше не используются; старые не переносятся —
  паспорт построится заново при следующем подключении.

### Таблицы

| Таблица | Строка | Содержимое |
|---|---|---|
| `_meta.table_passport` | таблица | `row_count`, `column_count`, `glossary` (JSON), `summary` (заполняется отдельной задачей), `profiled_at`, `enriched_at`, `description_status`, `description_run` |
| `_meta.column_passport` | колонка, PK `(table_name, column_name)` | `position` (порядок как в `DESCRIBE`), `dtype`, `kind`, статистика `ColumnStats`, название, описание, `fingerprint`, `enriched_at` |

Полный DDL — `SCHEMA` в `profile_memory.py`. Статистика колонки — поля
`ColumnStats` (см. [data_connectors.md](data_connectors.md#профилирование-при-подключении)),
но `min`, `max`, `mode` и `distinct` — зарезервированные слова SQL, поэтому
колонки называются `min_value`, `max_value`, `mode_value`, `distinct_count`.
Значения без фиксированного типа (`min_value`, `max_value`, `mode_value`,
`min_date`, `max_date`, `top_values` — `[{value, count}]`) хранятся как `JSON`;
при загрузке тип восстанавливается по `dtype`: `DATE` -> `date`, `TIMESTAMP*` ->
`datetime` (у `TIMESTAMPTZ` смещение сохраняется), `UUID` -> `uuid.UUID`.
Бывший блок `outliers` — `lower_fence`/`upper_fence` (границы Тьюки, 1,5·IQR) и
`outlier_count`; `outlier_summary(profile)` строит его из загруженного профиля.

Описание колонки:

| Колонка | Смысл |
|---|---|
| `title` | человекочитаемое название из документа-описания (до 40 символов) или `NULL` |
| `description` | комментарий источника как есть или описание из документа (не длиннее `ENRICH_DESCRIPTION_MAX_CHARS`) |
| `description_source` | `source` (комментарий) / `document` / `none` |
| `fingerprint` | sha256 от (имя, тип, комментарий источника, хеш документа); `NULL` после отказа LLM |

В паспортах, созданных до появления названий, остаются колонки `aliases` и
`description_original`: конструктор `ProfileMemory` добавляет `title`
(`ADD COLUMN IF NOT EXISTS`), старые колонки больше не читаются.

`description_status` в `_meta.table_passport` — статус переноса описания:
`none` (по умолчанию: описания нет), `pending` (ставит `ingest.mark_pending`, он же сбрасывает в `none`
перед фоновым `ingest.describe`), `ready` или `failed` (ставит
`ingest.describe`: `failed`, если у какой-либо колонки после переноса нет
`fingerprint` или энричер упал). В старых паспортах колонку добавляет
конструктор (`ADD COLUMN IF NOT EXISTS ... DEFAULT 'none'`). Фоновый
`describe` не переживает рестарт, поэтому при старте приложения
`fail_pending()` переводит оставшиеся `pending` в `failed`.

`description_run` — запуск переноса: `ingest.mark_pending` при каждом
подключении пишет новый. Фоновый `describe` от прошлого подключения той же
таблицы сверяет его перед вызовом LLM и перед записью и, если таблицу
подключили заново, не пишет ни описания колонок, ни статус.

### API `ProfileMemory(db)`

Конструктор принимает `DuckDBManager` и создаёт схему (`CREATE ... IF NOT
EXISTS`). Записи — одной транзакцией под `DuckDBManager.lock`
(`DuckDBManager.transaction()`), при ошибке — откат.

- `save_profile(profile, glossary=None)` — upsert строки таблицы и строк
  колонок (`INSERT ... ON CONFLICT DO UPDATE`): обновляются только
  статистика, `position`, `dtype`, `kind`; название, описание и `fingerprint`
  не затираются. Колонки, которых больше нет в таблице, удаляются.
  `glossary=None` оставляет сохранённый словарь.
- `set_description_status(table, status, run=None)` / `load_description_status(table)` —
  статус переноса описания (`none` / `pending` / `ready` / `failed`; без
  паспорта — `none`). С `run` статус меняется, только если запуск всё ещё текущий.
- `begin_description(table, run, described)` / `load_description_run(table)` —
  новый запуск переноса (`pending` или `none`) и текущий запуск.
- `fail_pending(run=None) -> list[str]` — `pending` → `failed` у всех таблиц
  (при старте, `lifespan` в `main.py`) или только у таблиц запуска `run`
  (фоновый перенос упал); возвращает имена таких таблиц.
- `load_profile(table) -> TableProfile | None` — профиль, равный
  сохранённому (`None`, если паспорта нет). `profiled_at` — строка ISO в UTC.
- `load_glossary(table) -> dict | None`.
- `save_enrichment(table, rows)` / `load_enrichment(table) -> list[ColumnEnrichment]`
  (`app/models/enrichment.py`) — названия и описания колонок.
- `delete(table)` — удалить паспорт таблицы.

### Подключение — `core/data/ingest.py`

1. `attach(connector, db) -> dict[str, TableProfile]` (под `db.lock`):
   `connector.connect(db)`, сразу за ним `profile_table` каждой таблицы и
   `save_profile`. Синхронно: без профиля планировщик не отберёт колонки к
   первому вопросу. Профилирование упало — паспорта уже профилированных
   таблиц удаляются, коннектор откатывает таблицы (`rollback`, если есть).
2. `describe(connector, db, tables, document=None, llm=None)`: для каждой
   таблицы берёт комментарии колонок (`connector.column_comments`); если нет ни
   комментариев, ни документа — таблица пропускается, `SchemaEnricher` не
   создаётся, LLM не вызывается. API датасорсов запускает `describe` фоновой
   задачей после ответа; её сбой пишется в лог и подключение не отменяет.

### Описание таблицы — `core/data/schema_enrichment.py`

`SchemaEnricher(db, llm=None).enrich(table, comments=None, document=None)`
переносит в паспорт названия и описания колонок и больше ничего не делает:
профиль не считает (его нет — `LookupError`), описаний не придумывает.

1. Комментарий источника (`comments`) — описание колонки как есть, без LLM:
   `source`. Пустой, из пробелов или равный имени колонки комментарий не
   считается.
2. Документ-описание (`document`: README, словарь колонок; приходит файлом
   `description` в `POST /api/datasources/csv` или полем `description` в
   `POST /api/datasources/db`) LLM разбирает промптом `column_extract.py`:
   название (до 40 символов, не равно имени колонки) и описание (не длиннее
   `ENRICH_DESCRIPTION_MAX_CHARS`) — только то, что в документе написано.
   Комментарий источника важнее описания из документа. Документ длиннее
   `ENRICH_DOCUMENT_MAX_CHARS` (20 000) обрезается.
3. Нет ни комментариев, ни документа — паспорт не меняется.
4. Колонки с тем же `fingerprint`, что сохранён, не обрабатываются;
   `save_enrichment` — только колонки этого запуска.

Колонки уходят в LLM пачками по `ENRICH_BATCH_SIZE` (25) через
`LLMClient.complete_structured` (схема `EnrichmentBatch`). LLM вызывается вне
`db.lock`. В ответе учитываются только запрошенные колонки (лишние — в лог).
Колонка, о которой в документе ничего нет (`null`), — не отказ: она
сохраняется без названия и описания и с `fingerprint`.

Отказ LLM (исключение, невалидный JSON, нет колонки в ответе) не прерывает
перенос: колонка сохраняется с комментарием источника (`source`) или без
описания (`none`), без названия и с `fingerprint = NULL` — при следующем
запуске она снова уйдёт в LLM; пишется `logging.warning` (неожиданное
исключение клиента — `logging.exception` с traceback). После таймаута или
ошибки соединения (`openai.APIConnectionError`) LLM до конца запуска больше не
вызывается, чтобы каждая пачка не ждала `LLM_TIMEOUT_S` с ретраями.

Паспорт хранится по имени таблицы/представления в DuckDB: если позже под
тем же именем подключат другие данные, профиль построится заново, а
названия и описания колонок с теми же именем, типом, комментарием и
документом останутся прежними.

## 4. Дашборды — `core/dashboards/store.py`
- **Хранит:** дашборды и их плитки: Vega-Lite спеку, шаг и temp-таблицу,
  построившие плитку, инсайт, раскладку на канвасе и снимок данных
  (до `SPEC_ROW_LIMIT` строк) с временем снятия.
- **Зачем:** повторный доступ из чата и из UI; перетаскивание на канвасе;
  плитка показывает данные, даже если её temp-таблицу уже почистили.
- **Как:** `DashboardStore` пишет в тот же SQLite, что и `SessionMemory`
  (`settings.sqlite_path`): таблицы `dashboards` и `dashboard_tiles`
  (`ON DELETE CASCADE`, `PRAGMA foreign_keys = ON`), индекс по `session_id`.
  Методы: `create`, `get`, `list(session_id)` (сводки по `updated_at`,
  новые первыми), `update(id, title, tiles)` (плитки заменяются в одной
  транзакции), `delete`, `update_snapshot(tile_id, records)`.
  `DashboardGenerator.generate` снимает данные плиток с `result_table` и
  сохраняет дашборд. Старые JSON из `storage/dashboards/` не мигрируются.
- **REST (`api/routes/dashboards.py`):**

  | Метод | Путь | Ответ |
  |---|---|---|
  | GET | `/api/dashboards?session_id=` (необязателен) | `list[DashboardSummary]`, новые по `updated_at` первыми |
  | GET | `/api/dashboards/{id}` | `DashboardOut` / 404 |
  | POST | `/api/dashboards` | `DashboardOut`, 201 |
  | PUT | `/api/dashboards/{id}` | `DashboardOut` / 404 |
  | DELETE | `/api/dashboards/{id}` | 204 / 404 |
  | POST | `/api/dashboards/{id}/recalculate` | `DashboardOut` / 404 |

  `DashboardCreate {session_id, title, tiles}` — `title` обязателен;
  `DashboardUpdate {title?, tiles?}` — `tiles` заменяют все плитки.
  Плитки на входе — `DashboardTileIn`: `result_table` только
  `temp_result_vN` (оно идёт в SQL как имя таблицы), иначе 422. Id плиток
  при POST всегда новые; при PUT id плитки этого дашборда сохраняется,
  чужой или повторный id заменяется новым (`dashboard_tiles.id` —
  глобальный ключ), так что GET → POST дублирует дашборд.
  `recalculate` (`core/dashboards/recalculate.py`) перечитывает
  `result_table` каждой плитки и пересобирает спеку запрошенным типом
  графика (`usermeta.requested_type`, иначе `chart_type`: если в прошлый
  раз построился запасной вид, на новых данных можно вернуться к
  запрошенному), с прежними подписями колонок и заголовком. В спеке без
  `usermeta` (собрана не `build_vega_spec`) заменяются только
  `data.values`, остальное не трогается. Если таблицы уже нет, снимок и
  спека остаются, а плитка приходит со
  `stale: true`.  Флаг не хранится — он есть только в ответе пересчёта.
