# Changelog

## [Unreleased] — Агентный цикл аналитического агента

### Добавлено
- **Модели**: `ResearchStep`, `ResearchPlan`, `Insight` в `app/models/` с Pydantic-валидацией (Phase 1)
- **Реестр инструментов**: 5 тулов зарегистрированы в `app/core/tools/__init__.py` (Phase 2)
- **SessionMemory**: `build_context()` дополнена полями `datasource`, `columns`, `columns_summary` (Phase 3a)
- **WASM runtime**: stub `wasm_runtime.py` с pass-through вместо `NotImplementedError` (Phase 3b)
- **SSE endpoints**: `chat.py` подключён к `AgentOrchestrator.run()` и `ChatEvent` (Phase 4)
- **Structured output**: `LLMClient.complete_structured()` использует `instructor.from_openai()` (Phase 5)
- **FakeLLM**: `app/core/llm/fake_client.py` — фиксированные `ResearchPlan` и `Insight` для запуска без локальной LLM (Phase 6)
- **Env-инжекция**: `LLMClient.__new__()` возвращает `FakeLLM` при `USE_FAKE_LLM=true` (Phase 7)
- **Тесты Phase 8**: `tests/unit/test_research_step.py`, обновлены `test_chat_api.py`, `test_agent_e2e.py`, `test_scenarios.py` — все с `FakeLLM` и in-memory DuckDB

### Новые unit-тесты (покрытие компонентов)
- `tests/unit/test_step_executor.py` — выполнение шагов, ValueError на отсутствующую таблицу/неизвестный инструмент, delegation на codegen
- `tests/unit/test_planner.py` — `StepPlanner.plan()` с FakeLLM, пустой вопрос, колонки в контексте
- `tests/unit/test_insight_generator.py` — `InsightGenerator.generate()` с FakeLLM, пустая таблица, инъекция `db`
- `tests/unit/test_duckdb_manager.py` — `query`, `execute` DDL, `save_step_result`, `to_records` с лимитом
- `tests/unit/test_tools_registry.py` — 5 зарегистрированных тулов, `get()` известного/неизвестного (KeyError)
- `tests/unit/test_wasm_stub.py` — pass-through поведение, статус `stub`, отсутствие payload
- `tests/unit/test_session_memory.py` — `build_context()` с профилем, `record_step/answer`, `set_datasource`, persist/load
- `tests/unit/test_orchestrator_unit.py` — `AgentOrchestrator.run()` с моками, eventi text/chart/done, error branch
- `tests/unit/test_config_env.py` — настройки wasm/llm/storage по умолчанию
- `tests/unit/test_error_handling.py` — ошибки StepExecutor (таблица/инструмент)
- `tests/unit/test_parser_edge_cases.py` — пустая строка, пробелы, JSON с текстом, кириллица
- `tests/unit/test_fake_llm_edge_cases.py` — неизвестная схема (model_validate), фиксированная строка
- `tests/unit/test_session_memory_persistence.py` — файл сессии создаётся/загружается, изоляция сессий
- `tests/unit/test_profile_memory_persistence.py` — сохранение/загрузка паспорта, отсутствующая таблица → {}

### Новые integration-тесты
- `tests/integration/test_agent_cycle.py` — полный цикл Orchestrator→Planner→Executor→Insight→Viz, dashboard_requested
- `tests/integration/test_memory_integration.py` — память сохраняется между двумя запросами в одной сессии
- `tests/integration/test_fake_llm_switch.py` — `USE_FAKE_LLM=true` через env var без `monkeypatch`

### Новые contract-тесты
- `tests/contract/test_chat_event_schema.py` — все 5 типов событий (text/chart/dashboard/error/done), компактная JSON-сериализация
- `tests/contract/test_api_response_headers.py` — `content-type: text/event-stream`, `cache-control`, `x-accel-buffering`, отсутствие `content-encoding`
- `tests/contract/test_sse_serialization.py` — формат `data: {...}\n\n`, компактный JSON без пробелов после двоеточия

### Новые E2E-тесты
- `tests/e2e/test_scenarios_extended.py` — пустой вопрос, смена типа визуализации, большой вопрос

### Новые 벤чмарк-тесты
- `tests/benchmark/test_orchestrator_benchmark.py` — время полного цикла с FakeLLM

### Исправления в коде (не только тесты)
- **`step_executor.py`**: `tool_registry.get()` выбрасывает `KeyError` (не возвращает `None`) — исправлена обработка отсутствующего инструмента
- **`insight.py`**: `InsightGenerator` принимает опциональный `db` параметр; импорт `DuckDBManager` на уровне модуля
- **`orchestrator.py`**: порядок инициализации (`self.db` перед `InsightGenerator(db=self.db)`), `db` передаётся в `InsightGenerator`
- **`tests/conftest.py`**: автоочистка мусорных JSON-файлов сессий после каждого теста

### Исправлено
- SSE события сериализуются компактно (`{"type":"text"}`) без пробелов после двоеточия; тесты скорректированы под этот формат
- Документ плана `agent-orchestrator.md`: все 8 фаз помечены ✅ DONE, добавлен раздел дополнительных исправлений

### Известные ограничения
- `FakeLLM` не наследуется от `LLMClient`, но возвращается из `LLMClient.__new__` — аннотация `-> "LLMClient"` формально неверна
- `wasm_runtime.py` — stub (pass-through), нет реального wasmtime-py / Pyodide (WASM-рантайм отложен по запросу)

### Статистика тестов
- **242 passed, 11 skipped** (11 skipped — внешние сервисы ClickHouse/PostgreSQL)
- Время прогона: ~42 секунды (по секциям)
