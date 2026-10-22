# Визуализация (сценарии №4 и №6)

## Сборка спеки — `core/viz/vega_spec_builder.py`

`build_vega_spec(chart_type, result_table, db=None, data=None, titles=None, title=None)`
возвращает Vega-Lite v5 JSON, который фронт рисует без серверной отрисовки.

- **Данные.** Если `data` не передан, строки выгружаются из `result_table`
  через `db.to_records(result_table, limit=500)` (`SPEC_ROW_LIMIT`). Значения
  приводятся к JSON: `date`/`datetime` -> ISO-строка, `Decimal` -> `float`,
  `NaN`/`inf` -> `null`, прочее -> строка.
- **Типы каналов** определяются по значениям колонки (пропуски не учитываются):
  даты и строки вида `2024-01`, `2024-01-05`, `2024-01-05 10:00` — `temporal`;
  числа (кроме булевых) — `quantitative`; остальное — `nominal`.
- **Заголовки осей** — из `titles` (колонка -> подпись). Оркестратор берёт их
  из паспорта таблицы сессии: `column_aliases()` читает словари `glossary` и
  `titles` (название колонки из описания таблицы, первая буква заглавная).
  Без названия — имя колонки; для счётчика — «Количество».

| Вид | mark | x | y | прочее |
|---|---|---|---|---|
| `bar` | bar | первая категория (дата — ordinal) | сумма первой метрики или count | |
| `line`, `area` | line / area | дата; иначе колонка с «порядковым» именем (year, month, год, месяц, …) как ordinal; иначе категория; иначе первая метрика | сумма метрики или count | color — категория |
| `histogram` | bar | первая метрика, `bin: {maxbins: 20}` | count | |
| `scatter` | point | первая метрика | вторая метрика | color — категория |
| `box` | boxplot | категория (или одна общая коробка) | первая метрика | |
| `heatmap` | rect | первая категория | вторая категория | color — сумма метрики или count |
| `table` | text | колонка (через `fold`) | номер строки | text — значение |
| `kpi` | `{type: text, fontSize: 48}` | — | — | text — первая метрика первой строки |

В каждом виде заполнен `tooltip`. Если данных не хватает (например, `scatter`
с одной числовой колонкой), строится ближайший подходящий вид: scatter/box/heatmap
-> bar, bar без категорий -> histogram, kpi без чисел -> table, без колонок
вовсе -> пустая таблица.

**KPI.** Одно крупное число. В `data.values` лежит весь результат, а
`transform` оставляет только первую строку:

```json
{"mark": {"type": "text", "fontSize": 48},
 "transform": [{"window": [{"op": "row_number", "as": "_row"}]},
               {"filter": "datum._row == 1"}],
 "encoding": {"text": {"field": "total", "type": "quantitative", "title": "Выручка"},
              "tooltip": [{"field": "total", "type": "quantitative", "title": "Выручка"}]},
 "title": "Выручка за квартал"}
```

Подпись плитки — `title` спеки. Метрика — первая числовая колонка, её
подпись — из `titles`. Из KPI морфинг ведёт в `bar` и `table`.

## Выбор вида — `core/viz/chart_selector.py`

`select_chart(question, result_table, requested="auto", records=None)`:
явный `requested` возвращается как есть; если `records` — одна строка с
одной числовой колонкой, вид — `kpi`; иначе эвристики по словам вопроса
(«распределение» -> histogram, «динамика»/«по месяцам» -> line,
«сравни» -> bar, «связь»/«корреляция» -> scatter, по умолчанию bar).
Оркестратор пока не передаёт `records`, поэтому `kpi` выбирается только
явно (тип плитки в плане дашборда, B-03/B-04).

## Событие `dashboard` — `api/schemas/chat.py`

`DashboardPayload {dashboard_id, title, tiles: DashboardTileOut[]}`.
`DashboardPayload.from_dashboard(dashboard)` копирует плитки без
`snapshot_data` (в SSE оно `null`): данные уже лежат в
`spec.data.values`. Полный дашборд со снимками отдаёт
`GET /api/dashboards/{id}`.

`usermeta` (стандартное поле Vega-Lite для метаданных):

```json
{"chart_type": "line", "requested_type": "line", "result_table": "temp_result_v2",
 "columns": [{"field": "month", "title": "Месяц", "type": "temporal"}]}
```

`chart_type` — фактически построенный вид; по нему работают морфинг и фронт.
Имена колонок с `.`, `[`, `]` экранируются в `field` (`field_ref`).

## Смена вида без LLM — `core/viz/chart_morph.py`

- `detect_morph(question)` возвращает целевой вид, `"auto"` или `None`.
  Названный вид («таблицей», «линейным графиком», «гистограммой», «точечной»,
  «ящик с усами», «тепловой картой», «с заливкой», «столбчатой») засчитывается
  только вместе с глаголом перерисовки или отсылкой к прошлому ответу
  («сделай», «перестрой», «замени», «в виде», «то же самое», «а теперь», …),
  поэтому «Построй гистограмму распределения суммы» остаётся новым вопросом.
  «В другом виде», «по-другому», «другой график», «смени тип графика» — `"auto"`.
- `morph(current_type, to_type, data, titles=None, result_table=None)`:
  явно названный вид строится как есть; для `"auto"` берётся первый вид из
  `MORPH_MAP[current_type]`, который на этих данных отличается от текущего.

## Оркестратор — `core/agent/orchestrator.py`

1. Если `detect_morph()` распознал смену вида и в `SessionMemory` есть
   `last_chart()`, строки берутся из кешированной `temp_result_vN` через
   `StateCache.get(table, limit=500)`, спека перестраивается `morph()`, в поток
   уходят события `chart` и `done`. Планировщик, шаги и `InsightGenerator`
   не вызываются; в DuckDB выполняется только `SELECT * FROM temp_result_vN LIMIT 500`.
2. Если прошлого графика нет или temp-таблица пропала (`duckdb.Error`), ход
   идёт обычным путём: планировщик -> шаги -> инсайт -> `build_vega_spec`
   по `db` исполнителя шагов -> `record_chart()`.

## Фронтенд — `components/charts/ChartRenderer.tsx`

- `usermeta.chart_type == "table"` -> HTML-таблица по `data.values`
  с заголовками из `usermeta.columns`;
- `renderer == "echarts"` (опция в `option`) или объект с `series` без `$schema`
  -> `echarts-for-react` (только на клиенте);
- иначе -> Vega-Lite (`react-vega`), ширина по контейнеру.
