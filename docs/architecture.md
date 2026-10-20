# Архитектура SberAnalyticalAgent

## Конвейер одного хода диалога

```
вопрос пользователя
  |
  v
[1] Planner (LLM + Instructor) --> ResearchPlan {steps[], final_viz, dashboard_requested}
  |        вход: вопрос, топ-5 релевантных колонок (semantic_select), контекст сессии
  v
[2] StepExecutor: последовательное применение шагов
  |        тул = предзаписанный SQL/стат-тест (подстановка колонок)
  |        нет тула -> WASM-песочница (кодогенерация LLM)
  |        каждый шаг -> temp_result_vN в DuckDB (state cache)
  |        сбой -> recovery: ретрай с текстом ошибки -> уточнение у пользователя
  v
[3] InsightGenerator (LLM) --> Insight {headline, details, limits}
  |
  v
[4] Визуализация: chart_selector -> vega_spec_builder (Vega-Lite JSON)
  |        смена вида: chart_morph (БЕЗ нового вызова LLM, из state cache)
  v
[5] (опц.) DashboardGenerator -> DashboardStore (SQLite: плитки, Vega, снимки данных)
  v
[6] (опц.) Экспорт: ExcelExporter (.xlsx) / PDFExporter (.pdf)
```

## Ключевые решения

| Решение | Обоснование |
|---|---|
| **DuckDB как единый движок** | SQL в едином диалекте к CSV/PostgreSQL/ClickHouse без предобработки |
| **ResearchStep + реестр тулов** | расширяемость: новый тул = файл, оркестратор не меняется |
| **Семантический отбор топ-5 колонок** | схема неконтролируемого размера не влезает в контекст LLM |
| **WASM-песочница вместо Docker** | инкапсуляция кодогенерации без «жирного» контейнера |
| **chart_morph без LLM** | «покажи в другом виде» — быстро и дёшево, не нагружая локальную модель |
| **Vega-Lite JSON-конфиги** | фронт рендерит спеку; бэк не занимается отрисовкой |
| **4 уровня памяти** | сессия (скользящее окно) / кеш результатов (DuckDB) / паспорт данных (DuckDB `_meta`) / реестр дашбордов |
| **Замкнутый контур** | Ollama/vLLM локально; интернет — только к разрешённым доменам (HuggingFace, пакеты) |

## Память

Подробности — в [memory.md](memory.md).
