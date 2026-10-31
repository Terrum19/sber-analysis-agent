# Реализация `build_vega_spec` в `vega_spec_builder.py`

## Статус: ✅ ВЫПОЛНЕНО

Все фазы реализованы и валидированы.

## Что было сделано

### Фаза 1: `vega_spec_builder.py` — полная реализация ✅

**Файл:** `backend/app/core/viz/vega_spec_builder.py`

- Новая сигнатура: `build_vega_spec(chart_type, result_table=None, data=None)`.
- Модуль не подключается к БД сам — данные принимаются готовыми списком словарей.
- `_detect_column_types()` — авто-определение типов колонок (quantitative / temporal / nominal) по типам Python-значений.
- `_build_encoding()` — формирование блока `encoding` для всех 8 типов:
  - `bar`: x=номинальная/количественная, y=количественная
  - `line`: x=временная/номинальная, y=количественная
  - `area`: x=временная/номинальная, y=количественная
  - `scatter`: x=количественная, y=количественная
  - `histogram`: x=количественная (bin), y=count
  - `box`: x=номинальная, y=количественная, mark=boxplot
  - `heatmap`: x=номинальная, y=номинальная, color=количественная
  - `table`: без encoding, только данные
- `usermeta` с `chart_type`, `source_table`, `columns`, `row_count`, `encoding_channels`.

### Фаза 2: `chart_morph.py` — исправление вызова ✅

**Файл:** `backend/app/core/viz/chart_morph.py`

- Исправлен вызов: `build_vega_spec(target, result_table=result_table, data=data)` (раньше данные уходили в позиционный `result_table`).
- Добавлен опциональный параметр `result_table: str | None = None`.
- Обратная совместимость с тестами сохранена.

### Фаза 3: `orchestrator.py` — загрузка данных и передача ✅

**Файл:** `backend/app/core/agent/orchestrator.py`

- Добавлен импорт `DuckDBManager` и `self.db = DuckDBManager()` в `__init__`.
- Перед вызовом `build_vega_spec` данные загружаются: `data = self.db.to_records(result_table)`.
- Вызов: `build_vega_spec(chart_type, result_table=result_table, data=data)`.

### Фаза 4: Валидация ✅

- `tests/unit/test_chart_morph.py`: 2/2 passed.
- Все unit-тесты: 172 passed, 1 failed (предсуществующая ошибка в `test_chat_api.py` про `cache-control`, не関連 к изменениям).
- Ручное тестирование всех 8 типов графиков + пустые данные — все отрабатывают без ошибок, возвращают корректную Vega-Lite спеку.

---

## Что НЕ входит в эту задачу

- Параметр `db` внутри `build_vega_spec` — модуль визуализации не должен знать о DuckDB (оркестратор загружает данные и передаёт их).
- Сложная семантическая детекция без схемы — используется базовая авто-детекция по типам значений.
- Изменения во фронтенде — фронт уже принимает Vega-спеку, мы просто наполняем её содержимым.

---

## Файлы изменены

1. `backend/app/core/viz/vega_spec_builder.py` — основная реализация.
2. `backend/app/core/viz/chart_morph.py` — исправление вызова.
3. `backend/app/core/agent/orchestrator.py` — загрузка и передача данных.
4. `docs/team_plans/terrum/vega-spec-implementation.md` — этот документ (план + отчёт о выполнении).