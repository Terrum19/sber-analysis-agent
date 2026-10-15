# Changelog

## [Unreleased] — Агентный цикл аналитического агента

### Добавлено
- **Модели 연구**: `ResearchStep`, `ResearchPlan`, `Insight` в `app/models/` с Pydantic-валидацией (Phase 1)
- **Реестр инструментов**: 5 тулов зарегистрированы в `app/core/tools/__init__.py` (Phase 2)
- **SessionMemory**: `build_context()` дополнена полями `datasource`, `columns`, `columns_summary` (Phase 3a)
- **WASM runtime**: stub `wasm_runtime.py` с pass-through вместо `NotImplementedError` (Phase 3b)
- **SSE endpoints**: `chat.py` подключён к `AgentOrchestrator.run()` и `ChatEvent` (Phase 4)
- **Structured output**: `LLMClient.complete_structured()` использует `instructor.from_openai()` (Phase 5)
- **FakeLLM**: `app/core/llm/fake_client.py` — фиксированные `ResearchPlan` и `Insight` для запуска без локальной LLM (Phase 6)
- **Env-инжекция**: `LLMClient.__new__()` возвращает `FakeLLM` при `USE_FAKE_LLM=true` (Phase 7)
- **Тесты**: `tests/unit/test_research_step.py`, обновлены `test_chat_api.py`, `test_agent_e2e.py`, `test_scenarios.py` — все с `FakeLLM` и in-memory DuckDB (Phase 8)

### Исправлено
- SSE события сериализуются компактно (`{"type":"text"}`) без пробелов после двоеточия; тесты скорректированы под этот формат
- Документ плана `agent-orchestrator.md`: все 8 фаз помечены ✅ DONE

### Известные ограничения (см. раздел "Next")
- `FakeLLM` не наследуется от `LLMClient`, но возвращается из `LLMClient.__new__` — аннотация `-> "LLMClient"` формально неверна
- `DuckDBManager` создаётся в нескольких местах (executor, insight_gen) — в тестах патчится через `monkeypatch` на общий in-memory экземпляр
- `StepExecutor` без обработки отсутствующих таблиц/инструментов — может падать на неизвестный `tool`
- `wasm_runtime.py` — stub (pass-through), нет реального wasmtime-py / Pyodide
- `ProfileMemory` не подключён в `SessionMemory` — нет долгосрочного контекста
- Нет интеграционного теста на `USE_FAKE_LLM=true` без `monkeypatch`

### Статистика тестов
- **263 passed, 11 skipped** (11 skipped — внешние сервисы ClickHouse/PostgreSQL)
- Время прогона: ~13 секунд
