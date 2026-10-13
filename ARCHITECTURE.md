# AI-Native BI-платформа (закрытый контур) — Финальная архитектура

> Утверждено. Одна LLM (Qwen2.5-Coder-14B Q4_K_M через llama.cpp), движок — DuckDB + Parquet-кэш, PG/CH только read-only snapshot, UI — Next.js (чат слева + канвас справа).

## 0. Принципы

1. **Детерминировано где можно.** Профили, топы, пересечения ID — SQL/DuckDB без LLM. LLM только для: семантики, Text-to-SQL, Python, объяснений, Viz-спеки, дашбордов.
2. **Один диалект SQL.** Агент пишет только DuckDB SQL. PG/CH — только офлайн-снапшот в Parquet.
3. **Ничего не мутируем.** `/data/raw` и `/data/cache` — read-only для песочницы. Записи агента — в `/tmp/shared` + SQLite.
4. **Закрытый контур.** Без облаков. Сеть есть только api<->ui<->llm. У sandbox сети нет.
5. **Воспроизводимость.** Ответ чата = `sql + chart_spec + artifact_id`. Дашборд = сохраненные `sql + chart_spec`.

## 1. Общая схема

```
[UI Next: Chat | Canvas] → [API FastAPI Orchestrator] → [LLM llama.cpp Qwen2.5-Coder]
      ↓                          ↓                              ↓
[SQLite sessions]        [DuckDB + Parquet Cache]        [Docker Sandbox no-net]
      ↓                          ↑
[Profiler + catalog.duckdb(semantics+joins) ] ← [CSV / PG / CH loaders]
```

Контейнеры: `api, llm, ui, sandbox-worker` (on-demand docker run).
Папки: `/data/raw/` оригиналы, `/data/cache/*.parquet` кэш, `/tmp/shared/run_<id>/` только result.parquet.
Хранилища метаданных (НИКАКИХ json/yaml-файлов): `catalog.duckdb` (profile_columns, profile_topk, join_edges, semantic_tables, semantic_columns, semantic_synonyms) + `platform.db` SQLite (sessions, messages, artifacts{sql,chart_spec,df_path}, dashboards, widgets, sources, sandbox_runs, eval_runs). JSON используется ТОЛЬКО как формат по проводу (LLM ↔ API ↔ UI) и поле metrics_json/chart_spec внутри строк SQLite.

## 2. Модули

### M1. Ingestion + Storage
Вход: сырые CSV либо `{type: postgres|clickhouse, dsn, tables[]}`.
Выход: `table_id → /data/cache/<table_id>.parquet + VIEW`.
Как: CSV через `read_csv_auto` → `COPY TO parquet` → `VIEW read_parquet`. DB через `ATTACH ... (READ_ONLY)` → `COPY (SELECT * FROM src.tbl) TO parquet`.
API: `POST /sources/csv`, `POST /sources/db`, `GET /sources`.

### M2. Profiler
Вход: `table_id`. Выход: таблицы `profile_columns + profile_topk` в `catalog.duckdb`.
Метрики SQL: `logical_type, count, null%, n_unique=APPROX_COUNT_DISTINCT (exact=False на >200k строк), min/max (numeric/date), топ-20 (по USING SAMPLE на больших), samples`. В промпт отдаётся только ужатый snapshot <8 КБ.
Покрывает сценарий №1.

### M3. Join Graph
Кандидаты в ID: `n_unique>100 AND n_unique/rows>0.2` OR имя `*id*key*код*`. Сэмпл 20к уникальных, `containment=|A∩B|/min(|A|,|B|)`. Ребро если >0.8. Выход: таблица `join_edges` в `catalog.duckdb`.

### M4. Semantic Layer
LLM один раз по профилю генерит строки в `semantic_tables/columns/synonyms` в `catalog.duckdb`. В Text-to-SQL подмешивается ужатый snapshot из таблиц (<8КБ).

### M5. LLM Serving (одна модель)
`llama.cpp-server -m qwen2.5-coder-14b-q4_k_m.gguf -c 32768 -n 4096`. OpenAI-совместимый `/v1/chat/completions`, JSON-mode где нужен JSON. Клиент `src/llm/client.py` с ретраями. Промпты в `src/agent/prompts/*.md`: validator, router, text2sql, fix_sql, python_gen, viz, dashboard, answer, enrich. Fallback без GPU: тот же код, модель 7B.

### M6. Agent Orchestrator
`POST /chat {session_id, message}`:
1. load_session → history, last_sql, last_schema, last_chart, last_df.
2. Validator (LLM json): `{grounded, tables, mapping, suggestions[2-3]}`. Если !grounded — вернуть suggestions, SQL не делать.
3. Router (LLM json): `{track: sql|python|viz_only|dashboard}`.
4. SQL-track: Text-to-SQL → EXPLAIN dry-run → exec LIMIT 500 → on error fix_sql до 3 попыток → result.parquet. Python-track: выбор шаблона + params → sandbox.
5. Viz: Vega-Lite spec из `{question, schema, rows}`.
6. Answer: бизнес-объяснение на русском.
7. save artifact `{sql, chart_spec, df_path}`.

### M7. Sandbox (Docker без сети)
`python:3.11-slim + duckdb pandas pyarrow statsmodels sklearn`. Запуск `docker run --rm --network none --memory 2g --cpus 2 -v /data:/data:ro -v /tmp/shared:/tmp:rw sandbox-worker python /tmp/run_<id>/run.py`. Только чтение `/data/cache`, запись только в `/tmp/run_<id>/`. Таймаут 60с. Шаблоны `src/sandbox/templates/: seasonality.py, lag_corr.py, forecast_whatif.py`.

### M8. Viz Engine
Агент возвращает Vega-Lite JSON `{mark: bar|line|point|arc, encoding:{x,y,color}}`. UI рендерит vega-embed. Смена вида без перезапроса SQL.

### M9. Memory (SQLite platform.db)
`Sessions, messages(role,content,tool_json), artifacts(sql,chart_spec,df_path), dashboards, widgets, sources`. В промпт — последние 8 сообщений + last_sql/schema/chart.

### M10. Guardrails
До SQL: grounded-проверка, уточнение метрики, отклонение. После: если rows==0 — объяснить и предложить ослабить фильтр, пустой график не показывать.

### M11. Dashboard Builder
`POST /dashboards/from-chat {session_id,title}` → LLM генерит 3-4 виджета → dry-run каждого SQL → INSERT. `GET /dashboards/:id`.

### M12. UI (Next.js)
`/` слева ChatPanel, справа Canvas (Таблица|График|Дашборд). `/dashboards/:id` сетка dnd-kit. Графики vega-embed, таблицы tanstack-table, стейт zustand.

## 3. Маппинг на сценарии
№1:M2+M4 | №2:M2+M4+M6 | №3:M6+M8 | №4:M8 | №5:M11+M12 | №6:M9+M10 | №7:M7season | №8:M6 GROUP BY HAVING | №9:M7lag | №10:M7forecast.

## 4. Порядок
Неделя 1 (чекпоинт №2): M1+M2+M3+M4+M5+M6(sql)+M9(min)+M12(chat+table). Неделя 2: M6(python)+M7+M8+M10+M11+M12(canvas+dash).

## 5. Риски
14B без GPU медленно → fallback 7B. Галлюцинации JOIN → join_graph + dry-run + fix-loop. Тяжелые CSV → read_parquet + LIMIT + APPROX.

