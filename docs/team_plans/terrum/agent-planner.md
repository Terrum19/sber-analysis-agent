# Планировщик и инструменты агента (plan-and-execute)

## Статус: ✅ ИТЕРАЦИЯ 1 ВЫПОЛНЕНА · ✅ ИТЕРАЦИЯ 2 ВЫПОЛНЕНА

Ветка: `feature/agent-planner` (в неё влиты `feature/tools` и `dev`). Изменения не закоммичены.

- [Итерация 1](#итерация-1) — типизированные аргументы тулов, виды шагов, ссылки
  `$prev/$source/$step_N`, валидация плана, достраивание после lookup.
- [Итерация 2](#итерация-2) — починка интеграции после слияния с `tools`/`dev`,
  восстановление после сбоя шага на фронте, паспорт данных в промпте,
  повторы LLM, запуск на облачной модели.

---

## Итерация 1

### Зачем

Ревью `feature/tools` показало: сами тулы в основном корректны, но контракт
между тулами и планировщиком/исполнителем слишком тонкий, поэтому большую
часть набора нельзя было надёжно использовать:

- все аргументы передавались позиционным `columns: list[str]`, а планировщик
  видел только `name: description` — порядок аргументов знали лишь `ValueError`;
- исполнитель перестал передавать `step.parameters` — `interval`, `n_bootstraps`,
  `seasonality_period` и т.п. молча игнорировались;
- пример в промпте планировщика был невалиден (`filter_rows` с одной колонкой);
- каждый шаг получал результат предыдущего: стат-тест или поиск значений
  заменял рабочую таблицу, следующие шаги теряли данные;
- lookup-тулы (`search_categorical_values`, `inspect_temp_table`) бесполезны
  в plan-and-execute: план фиксирован до того, как их результат виден;
- кодоген был заглушкой, возвращавшей вход как результат — инсайт строился
  по данным, которые никто не считал;
- не хватало базовых операций (сортировка/топ-N, группировка по нескольким
  колонкам, фильтры `IN`/`BETWEEN`, оконные функции по группам, выборка);
- `forecast` давал смещённый прогноз, у части стат-тестов не было p-value.

---

### Что было сделано

#### Фаза 1: Типизированные аргументы тулов ✅

**Файлы:** `backend/app/core/tools/base.py`, все тулы в `backend/app/core/tools/`

- У каждого тула — pydantic-модель `Args`; тулы реализуют
  `execute(db, input_table, args)`.
- `BaseTool.run(db, table, columns=None, **arguments)`: позиционный `columns`
  раскладывается по полям `Args` в порядке объявления (чисто списочное поле
  забирает остаток), именованные аргументы дополняют. Старые планы и тесты
  работают без изменений.
- Ошибка валидации → `ValueError` с ожидаемой сигнатурой.
- Типы аргументов: `Ident` (колонка), `Expr` (SQL-выражение), `TableRef`
  (таблица или `$step_N`); `literal()` — числа без кавычек, строки в кавычках.
- `identifier()` принимает Unicode (кириллические имена колонок).
- `describe()` строится из модели:
  `filter_rows [transform]: …; arguments={column: column, value: …, operator: =|…|IN|BETWEEN|IS NULL… = '='}`.

#### Фаза 2: Виды шагов и ссылки на таблицы ✅

**Файлы:** `backend/app/models/research_step.py`, `backend/app/core/agent/step_executor.py`

- У тула есть `kind`:
  - `transform` — таблица → таблица, продолжает конвейер;
  - `analysis` — стат-тест/сводка, не заменяет основную таблицу;
  - `lookup` — разведка данных, после неё план достраивается.
- `ResearchStep`:
  - `input` — `$prev` (по умолчанию: результат последнего transform-шага),
    `$source` (исходная таблица) или `$step_N`;
  - `arguments: dict` — именованные аргументы; синонимы от LLM
    (`args`, `params`, `parameters`) сливаются в него; `input: 2` → `$step_2`;
  - `columns` остался как позиционный формат.
- `ResearchPlan.result_step` — какой шаг показать в ответе и на графике
  (`None` — последний).
- `StepExecutor.execute(step, input_table, step_tables=None, source_table=None)`
  подставляет ссылки во `input` и внутри аргументов. В аргументах ссылкой
  считается только `$step_N` (колонка может называться `step_1`); во `input`
  допускается и `step_N`. Ссылка на невыполненный шаг → `ValueError`.

Пример шага:

```json
{"tool": "t_test_ind", "input": "$step_0",
 "arguments": {"column_a": "amount", "column_b": "region"},
 "purpose": "Сравнить регионы"}
```

#### Фаза 3: Оркестратор — выполнение плана ✅

**Файл:** `backend/app/core/agent/orchestrator.py`

- `_execute_plan` и `_apply_recovery` возвращают `Execution`:
  `result_table`, `supporting` (результаты analysis-шагов),
  `final_viz`, `dashboard_requested`.
- Только transform-шаги двигают `$prev`; результаты analysis-шагов уходят
  в инсайт как сопутствующие.
- После lookup-шага планировщик вызывается снова с выполненными шагами и
  первыми 20 строками результата; новые шаги нумеруются продолжением
  (не более `MAX_REPLANS = 2` раз за вопрос).
- Пустой план → `clarification_needed` «Не удалось подобрать инструменты»
  (раньше инсайт строился по сырым данным).
- `pending_recovery` дополнительно хранит `step_tables`, `source_table`,
  `result_step` — «пропустить шаг» корректно продолжает план со ссылками
  на ранние шаги; перепланирование видит выполненные шаги и ошибку.
- Для `NotImplementedError` кнопка «Уточнить колонки» не предлагается.

#### Фаза 4: Планировщик и промпты ✅

**Файлы:** `backend/app/core/agent/planner.py`, `backend/app/core/llm/prompts/step_planner.py`,
`system_analyst.py`, `insight_summary.py`, `backend/app/core/agent/insight.py`

- Промпт описывает `arguments`, `input`, виды шагов, `result_step`, правило
  «lookup-шаг — последним», содержит корректный пример (фильтр по датам →
  `group_by` → `sort_limit`). Вместо кодогена — пустой план.
- `validate_plan()` до выполнения проверяет: тул существует, аргументы
  валидны, `$step_N` указывает на более ранний шаг, `result_step` существует.
  При ошибках LLM переспрашивается один раз со списком ошибок.
- Контекст для достраивания плана: `context["executed"]`, `context["next_step_id"]`.
- Инсайт получает сам вопрос (раньше шаблон его терял) и сопутствующие
  результаты с подсказкой, как читать p-value.

#### Фаза 5: Набор инструментов ✅

**Файлы:** `backend/app/core/tools/**`, `backend/app/core/tools/registry.py`

Новые тулы:

| Тул | Что делает |
|---|---|
| `sort_limit` | сортировка и топ-N |
| `sample` | воспроизводимая выборка (`n` или `fraction`, `seed`) |
| `window_metric` | `cumulative_sum`, `rolling_avg`, `pct_change`, `rank`/`dense_rank`/`row_number`, `share_of_total`, с `partition_by` |

Расширены:

- `group_by` — несколько колонок `by`, несколько агрегатов `metrics`,
  функции `COUNT_DISTINCT`, `MEDIAN`, `STDDEV`;
- `filter_rows` — `IN`, `NOT IN`, `BETWEEN`, `IS NULL`, `IS NOT NULL`;
- `lag` / `lead` — `partition_by`;
- `time_bucket` — `function(metric_column)`, интервал `year`, доп. разрезы `by`;
- `seasonality` — `quarter`, `dayofweek`, `hour`;
- `aggregate` — `limit`.

Исправлены:

- `forecast` — формула экстраполяции (было `y_mean + slope·x`, стало
  `y_mean + slope·(x − x_mean)`: ряд 10…50 теперь даёт 60, 70); агрегация
  по `interval`, даты будущих периодов, `include_history`;
- p-value во всех стат-тестах (`anova_one_way`, `mann_whitney_u`, `t_test_dep`,
  корреляции, `linear_regression`); `mann_whitney_u` принимает аргументы как
  `t_test_ind` (две колонки или значение + группа);
- NaN/inf из scipy пишутся как `NULL`; даты в результатах — типизированные
  `DATE`/`TIMESTAMP`, а не строки;
- `search_categorical_values` — вхождение подстрокой считается сильным совпадением.

Реестр:

- `describe()` показывает только видимые тулы (40 штук);
- дубли `time_series`, `correlation`, `product_performance`, `conversion_funnel`
  остаются исполнимыми для старых планов, но планировщику не показываются;
- убрана повторная регистрация в `tools/__init__.py`.

#### Фаза 6: Кодоген ✅

**Файлы:** `backend/app/core/sandbox/codegen_runner.py`, `backend/app/core/agent/recovery.py`

- `run_generated_code` бросает `NotImplementedError` вместо возврата копии входа.
- `with_retry` не повторяет `NotImplementedError` (повтор бессмысленен).

#### Фаза 7: Тесты ✅

- Новый `backend/tests/unit/test_plan_execution.py`: новые тулы и аргументы,
  ссылки исполнителя, валидация плана и повтор, `$prev` vs analysis,
  `result_step`, достраивание после lookup, кнопки для неподдерживаемого шага,
  «пропустить шаг» со ссылками на ранние шаги.
- Обновлены ожидания в `test_tools.py`, `test_tool_registry.py`,
  `test_tools_registry.py`, `test_step_executor.py`, `test_recovery_and_cache.py`;
  тестовые заглушки в `test_orchestrator_*.py` принимают новые keyword-аргументы.
- Итог: те же 16 failed + 2 errors, что и до изменений (см. ниже), остальное
  проходит; новых падений нет.

---

### Известные ограничения / следующая итерация

- **WASM-песочница** не трогалась: `run_wasm_python` виден планировщику и
  по-прежнему возвращает вход как результат. Нужно скрыть или заставить падать.
- **Кодоген** не реализован — только честная ошибка.
- **`parser._fallback_step()`** всё ещё возвращает шаг с кодогеном (парсер
  в приложении не используется, только в тестах).
- **Имена колонок с пробелами** не поддерживаются: идентификаторы не
  экранируются, CSV-заголовки не нормализуются.
- **Воронки** упорядочивают шаги по первому появлению в данных, а не по
  последовательности действий пользователя.
- **Достраивание плана** привязано к lookup-шагам; общего цикла
  «наблюдение → решение» (ReAct) нет.
- **Валидация** не проверяет существование колонок (колонки создаются по ходу
  плана, нужен вывод схемы шагов).
- **Предсуществующие падения тестов**, не связанные с этой работой:
  - `test_orchestrator_viz.py` / `test_orchestrator_unit.py` — устарели
    относительно текущего оркестратора (аргумент `cache=`, нет `db`);
  - `test_session_memory*.py`, `test_profile_memory*.py` — незакоммиченные
    изменения памяти;
  - `test_chi_square_and_ks_test_return_reference_fields` — несовместимость
    версий SciPy/NumPy в окружении;
  - `test_config_env.py` — зависит от локального `.env`.
- **`backend/test_orchestrator_real_llm.py`** (локальный скрипт) ожидает строку
  от `_apply_recovery` — теперь там `Execution`, нужно `.result_table`.

> Падения тестов и скрипт `test_orchestrator_real_llm.py` из этого списка
> исправлены в итерации 2.

---

### Файлы изменены

- `backend/app/core/tools/base.py`, `registry.py`, `__init__.py`,
  `inspect_temp_table.py`, `run_wasm_python.py`
- `backend/app/core/tools/sql/` — `atomic.py`, `aggregate.py`, `time_series.py`
- `backend/app/core/tools/stats/` — все тулы, `_common.py`, новый `_args.py`
- `backend/app/core/tools/timeseries/` — `forecast.py`, `seasonality.py`
- `backend/app/core/tools/funnel/`, `backend/app/core/tools/domain/` — все тулы
- `backend/app/core/agent/` — `orchestrator.py`, `planner.py`, `step_executor.py`,
  `insight.py`, `recovery.py`
- `backend/app/core/llm/prompts/` — `step_planner.py`, `system_analyst.py`,
  `insight_summary.py`
- `backend/app/core/sandbox/codegen_runner.py`
- `backend/app/models/research_step.py`
- `backend/tests/unit/` — новый `test_plan_execution.py` и обновлённые тесты выше

---

## Итерация 2

### Зачем

После слияния `feature/tools` (коммит `60717f6`) и `dev` ядро не запускалось:

- `SessionMemory.build_context()` вызывал `ProfileMemory().load(...)` — после
  слияния у `ProfileMemory` обязательный аргумент `db`, а метода `load` нет.
  Это вызывается на каждом ходе, агентный цикл падал;
- оркестратор держал два разных DuckDB: исполнитель писал `temp_result_vN`
  в один, а график и инсайт читали из другого;
- потерян контракт `dev`, на который рассчитывают viz и дашборды
  (`cache=StateCache`, смена вида графика без планировщика, подписи осей);
- дефолтная модель в конфиге расходилась с `dev`/README (`qwen2.5:3b`);
- тесты писали в настоящее хранилище — плодились `backend/backend/storage/...`;
- CI (`ruff check`, `ruff format --check`) не проходил.

Прогон закоммиченной ветки: 31 failed / 407 passed / 2 errors.

### Что было сделано

#### Фаза 1: Интеграция ядра ✅

**Файлы:** `backend/app/core/agent/orchestrator.py`, `backend/app/core/memory/session_memory.py`,
`backend/app/config.py`

- **Один DuckDB на ход.** `AgentOrchestrator(planner, executor, insight_gen, cache, db)`:
  база берётся из `db`, иначе из `cache`, иначе у исполнителя. Исполнитель,
  инсайт, кеш и паспорт работают с одной базой.
- **Возвращён контракт `dev`:**
  - параметр `cache=StateCache`;
  - «покажи в другом виде» / «сделай таблицей» обслуживается *до*
    планирования, из кеша, без LLM и SQL; если таблица кеша пропала — обычный
    план;
  - `build_vega_spec(chart_type, result_table, db=..., titles=...)` — на осях
    человекочитаемые названия из паспорта.
- **`SessionMemory.build_context`:**
  - без DuckDB профиль просто не загружается (`profile=None`), без падения;
  - `set_datasource` нигде не вызывается, поэтому колонки берутся из паспорта
    таблицы — раньше планер получал пустой список колонок.
- **Конфиг:** дефолтная модель снова `qwen2.5-coder:14b`, как в `dev`, README
  и `.env.example`.

#### Фаза 2: Восстановление после сбоя шага ✅

**Файлы:** `backend/app/core/agent/orchestrator.py`, `frontend/src/components/chat/ChatPanel.tsx`,
`frontend/src/api/client.ts`, `frontend/src/types/chat.ts`

Раньше фронт объявлял тип `clarification_needed`, но не обрабатывал его и не
присылал `recovery_response`. Бэкенд же в состоянии ожидания отвечал ошибкой
на любой новый вопрос — после первого сбоя шага сессия была заблокирована.

- **Бэкенд:**
  - новый вопрос без `recovery_response` сбрасывает ожидание уточнения
    (`pending_recovery`) и обрабатывается как обычно;
  - при восстановлении вопрос берётся из сохранённого контекста
    (`pending["question"]`) — инсайт и график строятся по исходному вопросу,
    а не по тексту уточнения.
- **Фронт:** на `clarification_needed` агент пишет «Не получилось выполнить шаг:
  … Как продолжить?» и показывает кнопки:

| Кнопка | Что отправляется |
|---|---|
| Пропустить этот шаг и продолжить | `recovery_response = {"action": "skip_step"}` |
| Уточнить колонки | следующее сообщение пользователя уходит как `recovery_response` (перепланирование) |
| Переформулировать вопрос | ничего; следующее сообщение — новый вопрос |

#### Фаза 3: Номера результатов шагов ✅

**Файл:** `backend/app/core/data/duckdb_manager.py`

`DuckDBManager` создаётся на каждый запрос, и счётчик `temp_result_vN`
начинался с 1. Следующий вопрос (в том числе из другой сессии) перезаписывал
таблицу прошлого графика, и «смена вида» молча показывала чужие данные.

- `save_step_result` продолжает нумерацию от наибольшего `temp_result_vN`
  в базе;
- `CREATE OR REPLACE` заменён на `CREATE` с переходом к следующему номеру,
  если имя одновременно занял параллельный запрос.

#### Фаза 4: Планировщик — паспорт данных в промпте ✅

**Файлы:** `backend/app/core/data/semantic_select.py`, `backend/app/core/agent/planner.py`,
`backend/app/core/llm/prompts/step_planner.py`, `backend/app/core/memory/session_memory.py`

Контур замкнутый, поэтому значения из данных модели отдавать можно.

- **Вместо голых имён колонок** промпт получает выжимку паспорта
  (`describe_columns`): тип, описание из обогащения, диапазон для чисел и дат,
  до 10 частых значений для категорий:

```
- category [text, VARCHAR]; 4 разных, частые значения: "Одежда", "Продукты", "Электроника", "електроника"
- revenue [numeric, INTEGER]; от 50000 до 200000, медиана 125000
- date [temporal, DATE]; с 2024-01-01 по 2024-02-01
```

- **Правило в промпте:** значения для `filter_rows` брать из частых значений;
  если одно значение записано по-разному — фильтровать `IN` по всем вариантам;
  если значения нет среди частых — сначала `search_categorical_values`.
  Частые варианты написания модель видит без отдельного шага разведки.
- **Отбор колонок (`select_columns`)** учитывает не только имя:
  - русские алиасы из обогащения паспорта («выручка» → `revenue`);
  - совпадение со значениями категорий («электроники» → `category`);
  - словоформы — общее начало из 5 букв («выручку» = «выручка»).
  Раньше русский вопрос не совпадал с латинскими именами колонок, и в топ-5
  попадали случайные колонки.
- **Переспрос после ошибки валидации** содержит JSON прошлого плана — модель
  исправляет его, а не строит план с нуля (на облачной модели второй вызов
  занимал 66 с вместо 24 с).
- Если план кончается lookup-шагом, `result_step`, указывающий на будущие шаги,
  сбрасывается без переспроса — план всё равно будет достроен.
- В промпте перечислены допустимые `final_viz`
  (`bar, line, area, scatter, histogram, box, heatmap, table, auto`).

#### Фаза 5: Клиент LLM ✅

**Файлы:** `backend/app/core/llm/client.py`, `backend/app/config.py`, `.env.example`

- **Повторы.** По умолчанию instructor повторял любое исключение. С fallback-ами
  (список, JSON-режим) ответ 401/404 давал до 7 запросов, а с переспросом
  планера — до 14 на один план. Теперь:
  - instructor повторяет только ответ, не прошедший валидацию схемы
    (`STRUCTURED_ATTEMPTS = 3`);
  - авторизация, «модель не найдена», недоступность сервера пробрасываются сразу
    (сетевые ошибки, 429 и 5xx клиент `openai` уже повторил сам);
  - 400/422 по-прежнему уходят в JSON-режим: сервер может не поддерживать
    `tool_choice`.
- **Настройки:**
  - новый `llm_api_key` (`SecretStr`, в логах скрыт) для облачных
    OpenAI-совместимых провайдеров во время разработки; пустой → `not-needed`;
  - `.env` ищется рядом с кодом, а не в текущей папке: корень репозитория
    (docker-compose), затем `backend/.env` (приоритетнее). Раньше при запуске
    не из `backend/` настройки молча брались по умолчанию.

#### Фаза 6: API, тесты, CI ✅

- **Роуты:** незакоммиченные правки `sessions`, `dashboards`, `artifacts` падали
  с `NameError` (не хватало импортов). Экспорт Excel принимает только
  `result_ref` вида `temp_result_vN` — раньше значение от клиента подставлялось
  в SQL как есть.
- **`ks_test`** падал на scipy 1.18 (`kstest(..., "norm", args=...)`), переведён
  на `stats.norm(loc, scale).cdf`.
- **Изоляция тестов:** `tests/conftest.py` направляет `storage_dir`,
  `duckdb_path`, `sqlite_path` во временную папку; тест дефолтов конфига
  читает `Settings(_env_file=None)`, а не локальный `.env`.
- **Обновлены устаревшие тесты:** `test_profile_memory_persistence`
  (`load_profile` вместо `load`), `test_session_memory`, ожидание
  `clarification_needed` в `test_orchestrator_viz`.
- **Новые тесты:** нумерация `temp_result_vN` между менеджерами, сброс ожидания
  уточнения, ответ на исходный вопрос после восстановления, повторы LLM-клиента
  на подставном HTTP-транспорте, отбор колонок по алиасам и значениям,
  выжимка паспорта, `result_step` при плане с lookup в конце.
- **`ruff check --fix` и `ruff format`** по `app` и `tests`.
- **Симулятор `backend/test_orchestrator_real_llm.py`** (локальный скрипт):
  проверка готовности опрашивает настроенный endpoint, а не жёстко
  `localhost:11434`; предупреждает о неверном формате ключа OpenRouter;
  логгер `instructor` на INFO (раньше промпт печатался дважды).

### Итог

| | Закоммиченная ветка | Итерация 2 |
|---|---|---|
| pytest | 31 failed / 407 passed / 2 errors | 473 passed, 12 skipped |
| `ruff check` / `ruff format --check` | не проходят | проходят |
| coverage (CI, порог 50%) | — | 90% |
| фронт: `tsc`, `next lint`, `next build` | — | проходят |
| симулятор `--verify --llm fake` | падал | все сценарии PASS |

Реальная модель: подключение через OpenRouter проверено, планер строит
корректный по смыслу план. Полный цикл на `qwen2.5-coder:14b` локально не
прогонялся.

### Перед мёржем

1. Закоммитить вместе с правками неотслеживаемые файлы, без которых ветка
   не работает: `backend/app/core/tools/stats/_args.py` (импортируют 8 модулей),
   `backend/tests/unit/test_plan_execution.py`.
2. Не коммитить или перенести в `scripts/` локальные скрипты
   `backend/test_*_real*.py`, `backend/test_session_memory_sqlite.py`.
3. Прогнать полный цикл на целевой модели:
   `python3 backend/test_orchestrator_real_llm.py --verify`.

### Известные ограничения / следующая итерация

- **Сокращение списка инструментов.** Описания 40 тулов — больше половины
  промпта (~3,9 тыс. токенов); 14B-модель в них путается. План:
  1. набор из 30–50 вопросов по демо-данным с ожидаемыми тулами, прогон на
     `qwen2.5-coder:14b` с полным и сокращённым списком;
  2. если нужно — отбор без вызова LLM: базовый набор всегда, группы
     (временные ряды, воронки, стат-тесты) по словам вопроса и типам колонок
     паспорта; остальные тулы — только имя и описание без сигнатуры;
  3. страховка: ошибка аргументов → переспрос с сигнатурой нужного тула;
     пустой план → повтор с полным списком.
  Список тулов стоит в начале промпта и не меняется, поэтому кеширование
  начала промпта (prefix caching в vLLM/llama.cpp) уже снимает часть затрат
  по времени.
- **Разведка вариантов написания.** Частые варианты видны в паспорте, но редкие
  (не попавшие в топ-10) находит только `search_categorical_values` — это
  дополнительный вызов LLM на достройку плана. Нужна отдельная проработка
  вместе с сокращением списка тулов.
- **Скорость рассуждающих моделей:** `nemotron-…-reasoning` отвечает 24–66 с
  на вызов, а на вопрос нужно 2–4 вызова. Для отладки лучше модели без
  рассуждений.
- **Относительные пути хранилища** (`backend/storage/...`) считаются от
  текущей папки: при запуске из `backend/` данные попадут в
  `backend/backend/storage`. В тестах изолировано, для запуска приложения —
  нет.
- **Чат создаёт новый `DuckDBManager` на каждый запрос**, а не берёт
  `app.state.db`. Нумерация `temp_result_vN` теперь от этого не страдает,
  но соединение одно на запрос.
- **«Покажи таблицей»** не распознаётся как смена вида (`detect_morph`
  ловит «сделай таблицей»), запускается полный план.

### Файлы изменены

- `backend/app/config.py`, `.env.example`
- `backend/app/core/agent/` — `orchestrator.py`, `planner.py`
- `backend/app/core/data/` — `duckdb_manager.py`, `semantic_select.py`
- `backend/app/core/memory/session_memory.py`
- `backend/app/core/llm/client.py`, `prompts/step_planner.py`
- `backend/app/core/tools/stats/ks_test.py`
- `backend/app/api/routes/` — `artifacts.py`, `dashboards.py`, `sessions.py`
- `frontend/src/` — `components/chat/ChatPanel.tsx`, `api/client.ts`, `types/chat.ts`
- `backend/tests/conftest.py`; `backend/tests/unit/` — `test_config_env.py`,
  `test_orchestrator_viz.py`, `test_session_memory.py`,
  `test_profile_memory_persistence.py`, `test_duckdb_manager.py`,
  `test_plan_execution.py`, `test_semantic_select.py`, новый
  `test_llm_client_retries.py`; `tests/integration/test_memory_integration.py`
- `ruff format` затронул ещё ~38 файлов в `backend/app` и `backend/tests`
  (только форматирование)
- локально: `backend/test_orchestrator_real_llm.py`
