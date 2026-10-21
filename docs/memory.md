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

## 3. Метапамять: паспорт данных и глоссарий — `core/memory/profile_memory.py`
- **Хранит:** структуру таблиц, статистику, описания и человекочитаемые
  алиасы колонок, бизнес-словарь.
- **Зачем:** не сканировать источник заново на каждый вопрос; агент
  всегда держит «паспорт» в контексте.
- **Как:** постоянные (не TEMP) таблицы схемы `_meta` в файле DuckDB
  (`DUCKDB_PATH`): переживают перезапуск приложения и не пересекаются с
  таблицами пользователя (коннекторы смотрят только в схему `main`).
  Строится вызовом `SchemaEnricher.enrich()`; `POST /api/datasources/*`
  его пока не вызывают — это отдельная задача. JSON-файлы
  `storage/profiles/` больше не используются; старые не переносятся —
  паспорт построится заново при следующем энричинге.

### Таблицы

| Таблица | Строка | Содержимое |
|---|---|---|
| `_meta.table_passport` | таблица | `row_count`, `column_count`, `glossary` (JSON), `summary` (заполняется отдельной задачей), `profiled_at`, `enriched_at` |
| `_meta.column_passport` | колонка, PK `(table_name, column_name)` | `position` (порядок как в `DESCRIBE`), `dtype`, `kind`, статистика `ColumnStats`, описание, алиасы, `fingerprint`, `enriched_at` |

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

Энричинг колонки:

| Колонка | Смысл |
|---|---|
| `description` | итоговое описание (не длиннее `ENRICH_DESCRIPTION_MAX_CHARS`, кроме отказа LLM) |
| `description_original` | описание из источника как есть (`NULL`, если его нет) |
| `description_source` | `source` / `source_compressed` / `generated` / `none` |
| `aliases` | `VARCHAR[]`, 0–4 алиаса |
| `fingerprint` | sha256 от (имя, тип, описание из источника, бизнес-словарь); `NULL` после отказа LLM |

### API `ProfileMemory(db)`

Конструктор принимает `DuckDBManager` и создаёт схему (`CREATE ... IF NOT
EXISTS`). Записи — одной транзакцией под `DuckDBManager.lock`
(`DuckDBManager.transaction()`), при ошибке — откат.

- `save_profile(profile, glossary=None)` — upsert строки таблицы и строк
  колонок (`INSERT ... ON CONFLICT DO UPDATE`): обновляются только
  статистика, `position`, `dtype`, `kind`; описание, алиасы и `fingerprint`
  не затираются. Колонки, которых больше нет в таблице, удаляются.
  `glossary=None` оставляет сохранённый словарь.
- `load_profile(table) -> TableProfile | None` — профиль, равный
  сохранённому (`None`, если паспорта нет). `profiled_at` — строка ISO в UTC.
- `load_glossary(table) -> dict | None`.
- `save_enrichment(table, rows)` / `load_enrichment(table) -> list[ColumnEnrichment]`
  (`app/models/enrichment.py`) — описания и алиасы колонок.
- `delete(table)` — удалить паспорт таблицы.

### Энричинг — `core/data/schema_enrichment.py`

`SchemaEnricher(db, llm=None).enrich(table, comments=None, glossary=None)`:

1. Профиль таблицы -> `save_profile` (статистика обновляется при каждом запуске).
2. Колонки, у которых `fingerprint` совпал с сохранённым, в LLM не идут.
3. Описание из `comments` (`BaseConnector.column_comments()`):
   - пустое, из одних пробелов или совпадает с именем колонки (без учёта
     регистра) — генерируется LLM по имени, типу и профилю (топ-значения,
     min/max, доля пропусков) с учётом словаря: `generated`;
   - не длиннее `ENRICH_DESCRIPTION_MAX_CHARS` (200; длина исходной строки) —
     остаётся посимвольно как есть, LLM его не переписывает: `source`;
   - длиннее — LLM сжимает до одного предложения не длиннее порога
     (промпт `description_compress.py`): `source_compressed`.
4. Алиасы — до 4 на колонку с учётом итогового описания и словаря (промпт
   `alias_gen.py`, тот же вызов генерирует недостающие описания). Алиасы
   без пустых, дублей (без учёта регистра), имени колонки и длиннее 40
   символов; лишние отбрасываются.
5. `save_enrichment` — только колонки, обработанные в этом запуске.

Колонки уходят в LLM пачками по `ENRICH_BATCH_SIZE` (25) через
`LLMClient.complete_structured` (схема `EnrichmentBatch`). LLM вызывается вне
`db.lock`. В ответе учитываются только запрошенные колонки (лишние — в лог).

Отказ LLM (исключение, невалидный JSON, нет колонки в ответе, пустое или
слишком длинное описание) не прерывает энричинг: колонка сохраняется с
описанием из источника (`source`) или без него (`none`), с пустыми алиасами и
`fingerprint = NULL` — при следующем запуске она снова уйдёт в LLM; пишется
`logging.warning` (неожиданное исключение клиента, например ответ без
`choices`, — `logging.exception` с traceback). После таймаута или ошибки соединения (`openai.APIConnectionError`)
LLM до конца запуска больше не вызывается, чтобы каждая пачка не ждала
`LLM_TIMEOUT_S` с ретраями. Длинное описание из источника при отказе остаётся
несжатым.

Если процесс упал после `save_profile`, но до `save_enrichment`, статистика
уже новая, а описания и `fingerprint` — от прошлого запуска (у новых колонок
`NULL`). Следующий `enrich()` по несовпавшему `fingerprint` обработает ровно
незавершённые колонки. Два параллельных `enrich()` одной таблицы не портят
паспорт (записи — транзакциями под `db.lock`), но оба вызовут LLM, и
останется результат последнего.

Паспорт хранится по имени таблицы/представления в DuckDB: если позже под
тем же именем подключат другие данные, статистика обновится, а описания
и алиасы колонок с теми же именем, типом и комментарием останутся прежними.

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
