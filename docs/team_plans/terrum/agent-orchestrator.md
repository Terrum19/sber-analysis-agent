# Agent Orchestrator Implementation Plan

## Phase 1: Core Models & Contracts (Foundation - BLOCKING) ✅ DONE

### Tasks
- [x] Create `backend/app/models/__init__.py`
- [x] Create `ResearchStep`, `ResearchPlan` in `backend/app/models/research_step.py`
- [x] Create `Insight` in `backend/app/models/insight.py`

### Why First
5 files import from `app.models` (planner, executor, parser, insight, codegen_runner) - all fail currently with `ModuleNotFoundError`.

---

## Phase 2: Tool Registration (Enable Planner) ✅ DONE

### Tasks
- [x] Register all 5 tools in `tool_registry` in `backend/app/core/tools/__init__.py`

### Why
`tool_registry.describe()` returns empty string → planner sees no tools.

---

## Phase 3: AgentOrchestrator Core Fixes (Main Pipeline) ✅ DONE

### Tasks
- [x] Fix `build_context` - pass columns, dictionary, history in `orchestrator.py`
- [x] Integrate retry logic properly in `orchestrator.py`
- [x] Handle `needs_llm_codegen=true` → WASM stub in `orchestrator.py` + `codegen_runner.py`
- [x] Ensure `result_table` flows step→step & saved in memory in `orchestrator.py`

---

## Phase 4: Chat SSE Endpoint (API Contract) ✅ DONE

### Tasks
- [x] Implement `POST /api/chat` with real orchestrator in `chat.py`
- [x] Convert generator events to SSE `ChatEvent` format in `chat.py`
- [x] Ensure async, non-blocking in `chat.py`

---

## Phase 5: Instructor Integration (Structured Output) ✅ DONE

### Tasks
- [x] Add `instructor` dependency to `pyproject.toml` / `requirements.txt`
- [x] Replace manual JSON extraction in `LLMClient.complete_structured()` in `client.py`
- [x] Update `InstructorService` to use new method in `instructor_service.py`

---

## Phase 6: Supporting Fixes ✅ DONE

### Tasks
- [x] WASM stub in `codegen_runner.py` - log warning, return input table name
- [x] Update `lifespan` in `main.py` - init DuckDB, ToolRegistry
- [x] Enhance `SessionMemory.build_context()` - add relevant columns from datasource

---

## Phase 7: FakeLLM for Testing (CI/CD Ready) ✅ DONE

### Tasks
- [x] Create `FakeLLM` class in `backend/app/core/llm/fake_client.py`
- [x] Return fixed `ResearchPlan` for any question
- [x] Return fixed `Insight`
- [x] Wire via env var `USE_FAKE_LLM=true`

---

## Phase 8: Tests (Validation) ✅ DONE

### Tasks
- [x] Unit: `ResearchPlan`/`ResearchStep` validation in `tests/unit/test_research_step.py`
- [x] Unit: `parse_steps()` strategies in `tests/unit/test_parser.py`
- [x] Integration: Full cycle with FakeLLM in `tests/integration/test_agent_e2e.py`
- [x] E2E: User scenarios in `tests/e2e/test_scenarios.py`
- [x] Test infra: `conftest.py`, fixtures in `tests/conftest.py`

---

## Execution Order

```
1. Models (Phase 1) 
   ↓
2. Tool Registry (Phase 2)
   ↓
3. Orchestrator fixes (Phase 3)
   ↓
4. Chat SSE (Phase 4)
   ↓
5. Instructor integration (Phase 5)
   ↓
6. Supporting fixes (Phase 6)
   ↓
7. FakeLLM (Phase 7)
   ↓
8. Tests (Phase 8)
```

---

## Дополнительные исправления (после Phase 8) ✅ DONE

### Tasks
- [x] `orchestrator.py`: `__init__` принимает `planner`, `executor`, `insight_gen`, `db` — тесты передают их явно
- [x] `chat.py`: добавлен `Cache-Control: no-cache, no-transform` заголовок в StreamingResponse
- [x] `session_memory.py`: `build_context()` загружает профиль через `ProfileMemory().load()` — долгосрочный контекст (поле `profile`)
- [x] `tests/unit/test_fake_llm_env.py`: интеграционный тест на `USE_FAKE_LLM=true` без `monkeypatch`

### Why
- Orchestrator должен принимать зависимости для тестирования с FakeLLM и in-memory DuckDB
- SSE должен правильно информировать прокси о буферизации (cache-control, x-accel-buffering)
- ProfileMemory даёт долгосрочный контекст агента (паспорт данных) без повторного сканирования
- Тест на env var без monkeypatch проверяет реальный механизм `__new__` в CI
