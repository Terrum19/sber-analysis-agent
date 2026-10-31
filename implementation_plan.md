# Implementation Plan — P0/P1 для SberAnalyticalAgent

**Дата:** 28.10.2026  
**Статус:** план готов к исполнению, код ещё не менялся  
**Целевой контур:** GPU ≥ 24 ГБ, vLLM, замкнутый контур, модель 30–40B в 4-bit (FP8 — при 48 ГБ)  
**Горизонт:** P0 — до сдачи 31.10; P1 — после сдачи, без блокировки релиза

---

## [Overview]

### Цель

Сделать так, чтобы **любой ход агента завершался не более чем за 15 минут** на модели 30–40B, не потеряв качество ответа, и заложить архитектурную основу P1: управляемый semantic layer, DAG-исполнение, типизированные схемы тулов, пул моделей, кеши и полноценную наблюдаемость.

### Почему это нужно

Текущий конвейер (`AgentOrchestrator._turn` → `StepPlanner.plan` → `StepExecutor` → `InsightGenerator`) архитектурно правильный, но у него нет **общего бюджета хода**:

- `LLM_TIMEOUT_S=600`, `LLM_MAX_RETRIES=1`, `_complete_json_schema` делает 2 попытки, `PLAN_FIX_ATTEMPTS=1`, `STEP_FIX_ATTEMPTS=2`, `MAX_REPLANS=2`;
- `AGENT_MAX_STEPS` объявлен, но нигде не проверяется;
- `LLM_MAX_TOKENS=None` — генерация не ограничена;
- SQL-тулы выполняются синхронно в event loop, без таймаута запроса;
- 40 тулов занимают ~9,7 тыс. символов промпта, `AGENT_TOOL_TOPK` выключен после регресса;
- семантического слоя нет — только глоссарий и названия колонок.

Один план теоретически может стоить 20–40 минут; ход с recovery и дашбордом — больше. Поэтому P0 — это **deadline-first**, а не «поменять модель и надеяться».

### Scope

**P0 (до 31.10):**

| ID | Работа | Результат |
|---|---|---|
| P0-1 | Turn budget и hard deadline 870 с | Ход всегда завершается ≤ 15 мин |
| P0-2 | Лимиты шагов, токенов, LLM-вызовов, retry | Нет «бесконечных» планов и генерации |
| P0-3 | SQL в `asyncio.to_thread` + `interrupt()` + query timeout | Event loop и SSE не блокируются |
| P0-4 | Preflight-валидация колонок, enum тулов, `maxItems` | Ошибки ловятся до выполнения |
| P0-5 | Быстрый роутер и шаблонный инсайт | Простые вопросы без planner/LLM |
| P0-6 | Параллельные инсайты плиток дашборда | Дашборд не выбивает бюджет |
| P0-7 | vLLM-профиль и безопасный tool retrieval | Prefix caching, 4-bit, меньше prefill |
| P0-8 | Конфиг, зависимости, health, единая модель | Чистый запуск и предсказуемость |
| P0-9 | TurnMetrics и eval-гейт p95 ≤ 900 с | Доказуемость 15 минут |

**P1 (после 31.10):**

| ID | Работа | Результат |
|---|---|---|
| P1-1 | Semantic layer (metrics, dimensions, joins, dry-plan) | Управляемая семантика вместо догадок |
| P1-2 | DAG-план и параллельный executor | Независимые ветки, меньше latency |
| P1-3 | Типизированные схемы тулов + embeddings/reranker | Меньше `fix_step`, точнее schema linking |
| P1-4 | Model portfolio и каскад small/large | Дешёвые вызовы для простых задач |
| P1-5 | Кеши планов, результатов и семантики | Повторные вопросы дешевле |
| P1-6 | Расширенный eval и observability | Регрессии и SLO под контролем |
| P1-7 | Multi-table, ambiguity, prompt injection | Надёжность на реальных данных |

### Допущения

1. 15 минут — **end-to-end** на сложный вопрос, включая планирование, SQL, инсайт и дашборд.
2. 40B — ограничение на **общее** число параметров; MoE 30B-A3B (3,3B active) допустим и предпочтителен по latency.
3. Целевая модель запускается через vLLM; Ollama остаётся fallback для 7–14B/CPU.
4. P0 не меняет публичные контракты фронта, кроме **аддитивных** полей в `done`.
5. P1 внедряется за флагами и не ломает текущие планы/сессии.

### Критерии успеха P0

- В тесте с «медленной» LLM (sleep 10 с) и `AGENT_TURN_TIMEOUT_S=2` ход завершается `error`/`done` менее чем за 3 с, без зависшей задачи.
- На eval p95 до `done` ≤ 900 с; p50 ≤ 300 с на целевом GPU-профиле.
- Простые вопросы (`сколько строк`, `общая сумма`, `средняя`, `топ-N`, `по месяцам`) не вызывают planner; для одной ячейки — не вызывают LLM-инсайт.
- Тяжёлый SQL не блокирует event loop: тест с тикером каждые 10 мс и запросом на 1–2 с даёт тики во время запроса.
- Чистый `pip install -r requirements.txt` + `pytest` не падает на импорте `PIL`; приложение стартует с `.env.example`.
- В логе каждого хода есть `route`, `llm_calls`, `prompt_tokens`, `total_ms`.

### Целевая архитектура

```
вопрос
  -> Router (правила/эмбеддинги, <10 мс)
       -> fast path: шаблонный план (0 LLM)
       -> semantic mapper (P1)
  -> Planner (30–40B, JSON schema, <=8 шагов, <=240 с)
  -> Validator (schema, columns, time hints, dry-run P1)
  -> Executor (DAG P1; SQL timeout; LLM/кодогенерация параллельно)
  -> Insight (fast template или 30–40B, <=90 с)
  -> Chart / Dashboard (детерминированно; плитки параллельно)
  -> Metrics / eval / SLO
```

---

## [Types]

### P0.1. `TurnBudget` и `BudgetExceeded`

Новый файл `backend/app/core/agent/budget.py`.

```python
class BudgetExceeded(RuntimeError):
    """Ход не уложился в бюджет: этап, остаток и причина."""

    def __init__(self, stage: str, remaining_s: float, detail: str = "") -> None:
        self.stage = stage
        self.remaining_s = remaining_s
        self.detail = detail
        super().__init__(f"{stage}: осталось {remaining_s:.1f} с; {detail}".strip())


@dataclass(slots=True)
class TurnBudget:
    total_s: float
    stage_limits: dict[str, float]  # planning, steps, insight, chart, dashboard
    max_llm_calls: int
    max_steps: int
    started_at: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    stage_started: dict[str, float] = field(default_factory=dict)

    def remaining(self) -> float: ...
    def stage_remaining(self, stage: str) -> float: ...
    def timeout_for(self, stage: str, default_s: float) -> float: ...
    def consume_llm_call(self, stage: str) -> None: ...
    def enter_stage(self, stage: str) -> None: ...
    def leave_stage(self, stage: str) -> None: ...
    def snapshot(self) -> dict[str, float | int]: ...
```

ContextVar:

```python
_budget: ContextVar[TurnBudget | None] = ContextVar("turn_budget", default=None)

def current_budget() -> TurnBudget | None: ...
def set_budget(budget: TurnBudget | None) -> object: ...
def reset_budget(token: object) -> None: ...
```

`consume_llm_call` выбрасывает `BudgetExceeded`, если:
- `llm_calls >= max_llm_calls`;
- `remaining() <= 0`;
- `stage_remaining(stage) <= 0`.

### P0.2. Настройки `Settings`

`backend/app/config.py`:

```python
agent_turn_timeout_s: int = 870          # hard deadline, 14.5 мин
agent_plan_timeout_s: int = 240
agent_step_timeout_s: int = 60
agent_insight_timeout_s: int = 90
agent_dashboard_timeout_s: int = 120
agent_max_llm_calls: int = 6
agent_max_steps: int = 8
agent_max_parallel_llm: int = 2
agent_fast_insight: bool = True
agent_simple_router: bool = True
sql_timeout_s: int = 60
llm_max_tokens: int = 1536
llm_structured_mode: Literal["json_schema", "tools", "json_object"] = "json_schema"
```

Валидаторы:

- `llm_base_url`: убрать хвост `/chat/completions`, нормализовать `/v1`, предупредить, если URL не заканчивается на `/v1`.
- `llm_structured_mode`: добавить `json_object`.
- `llm_max_tokens`, `agent_turn_timeout_s`, `agent_max_steps`, `agent_max_llm_calls`, `sql_timeout_s`: `> 0`.
- `agent_max_steps`: использовать в `validate_plan`, а не только хранить.

### P0.3. `Route` и `TurnMetrics`

`backend/app/core/agent/router.py`:

```python
@dataclass(frozen=True)
class Route:
    kind: Literal[
        "simple_count",
        "simple_total",
        "simple_avg",
        "simple_top",
        "simple_time",
        "simple_distribution",
    ]
    plan: ResearchPlan
    reason: str
```

`backend/app/core/agent/metrics.py`:

```python
@dataclass
class TurnMetrics:
    session_id: str
    turn_id: str
    question: str
    route: str = "complex"
    model: str = ""
    stages: dict[str, float] = field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    budget_exceeded: str | None = None

    def record_llm(self, prompt_tokens: int, completion_tokens: int) -> None: ...
    def record_stage(self, name: str, seconds: float) -> None: ...
    def total_ms(self) -> int: ...
    def last_latency_ms(self) -> int | None: ...
    def as_log(self) -> dict: ...
```

ContextVar `current_metrics()` / `set_metrics()`.

### P0.4. Изменения моделей

- `ResearchPlan` — без изменения полей; ограничение `maxItems` добавляется в JSON schema на уровне `LLMClient._schema_for`, чтобы не ломать существующие планы и тесты.
- `Insight` — без изменения полей; `headline.maxLength=300`, `details.maxItems=5` в JSON schema.
- `ChatEvent` — `data: dict`, поэтому аддитивные поля `done` не требуют изменения типа. В `done` добавляются `llm_calls`, `prompt_tokens`, `total_ms`, `route`.
- `LLMStatusOut` — аддитивные optional-поля `context`, `structured_mode`, `max_tokens`, `last_latency_ms`.

### P1.1. Semantic layer

Новый файл `backend/app/models/semantic.py`:

```python
class SemanticMetric(BaseModel):
    name: str
    title: str
    description: str = ""
    aggregation: Literal["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN"]
    column: str | None = None
    sql: str | None = None  # для составных метрик; проверяется dry-run
    format: Literal["number", "currency", "percent", "duration"] = "number"
    synonyms: list[str] = Field(default_factory=list)


class SemanticDimension(BaseModel):
    name: str
    column: str
    kind: Literal["categorical", "temporal", "identifier"]
    synonyms: list[str] = Field(default_factory=list)
    time_grains: list[Literal["day", "week", "month", "quarter", "year"]] = Field(
        default_factory=list
    )


class SemanticJoin(BaseModel):
    left_table: str
    right_table: str
    left_column: str
    right_column: str
    relationship: Literal["many_to_one", "one_to_many", "one_to_one"]


class SemanticModel(BaseModel):
    table: str
    description: str = ""
    metrics: list[SemanticMetric] = Field(default_factory=list)
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    joins: list[SemanticJoin] = Field(default_factory=list)
    default_time_dimension: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    version: int = 1
```

### P1.2. DAG-план

`backend/app/models/research_step.py`:

```python
class ResearchStep(BaseModel):
    ...
    depends_on: list[int] = Field(default_factory=list)
```

`backend/app/core/agent/plan_graph.py`:

```python
@dataclass(frozen=True)
class PlanNode:
    step: ResearchStep
    depends_on: frozenset[int]


@dataclass(frozen=True)
class PlanGraph:
    nodes: dict[int, PlanNode]
    order: tuple[int, ...]

    @classmethod
    def from_plan(cls, plan: ResearchPlan) -> "PlanGraph": ...
    def layers(self) -> list[list[ResearchStep]]: ...
    def validate(self) -> list[str]: ...
```

Правила вывода зависимостей:

- `step.input == "$prev"` → зависит от последнего `transform`-шага;
- `step.input == "$step_N"` → зависит от N;
- `depends_on` добавляется к выведенным зависимостям;
- цикл, ссылка на несуществующий шаг и `$prev` без предыдущего transform — ошибка валидации.

### P1.3. Типизированные схемы тулов

`backend/app/core/tools/schema.py`:

```python
class ToolArgumentSchema(BaseModel):
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    args_model: type[ToolArgs]
    json_schema: dict[str, Any]


def tool_schema(tool: BaseTool) -> ToolSchema: ...
def planner_schema(tools: list[BaseTool], max_steps: int) -> dict[str, Any]: ...
```

Для top-k тулов генерируется `oneOf`-схема шагов; для полного списка — компактная схема с `tool.enum` и `arguments: object`. Это позволяет грамматике проверять имена тулов и аргументы, не раздувая промпт на 40 тулов.

### P1.4. Model portfolio

`backend/app/core/llm/profiles.py`:

```python
@dataclass(frozen=True)
class ModelProfile:
    role: Literal["planner", "fast", "sql", "fallback"]
    model: str
    base_url: str
    api_key: SecretStr
    context: int
    max_tokens: int
    quantization: str | None = None
    timeout_s: int = 300


class ModelPool:
    def get(self, role: str) -> LLMClient: ...
    def active(self, role: str) -> ModelProfile: ...
```

Новые настройки:

```python
llm_model_planner: str = ""       # пусто -> llm_model
llm_model_fast: str = ""          # пусто -> llm_model
llm_model_sql: str = ""           # пусто -> llm_model
llm_base_url_fast: str = ""       # пусто -> llm_base_url
llm_base_url_sql: str = ""
```

### P1.5. Кеши

`backend/app/core/cache/models.py`:

```python
@dataclass(frozen=True)
class CacheKey:
    namespace: str
    question_hash: str
    table: str
    schema_hash: str
    model: str
    prompt_version: str
    data_version: str


@dataclass
class CacheEntry:
    key: str
    payload: dict | list
    created_at: float
    expires_at: float
```

---

## [Files]

### P0 — новые файлы

| Путь | Назначение |
|---|---|
| `backend/app/core/agent/budget.py` | `TurnBudget`, `BudgetExceeded`, contextvar, `budget_from_settings` |
| `backend/app/core/agent/router.py` | быстрый роутер простых вопросов и шаблонные `ResearchPlan` |
| `backend/app/core/agent/metrics.py` | `TurnMetrics`, per-turn счётчики LLM/токенов |
| `backend/tests/unit/test_turn_budget.py` | unit-тесты бюджета |
| `backend/tests/unit/test_router.py` | шаблонные планы, отказ при неоднозначности |
| `backend/tests/unit/test_duckdb_timeout.py` | `interrupt()`, освобождение lock, живость соединения |
| `backend/tests/unit/test_event_loop_offload.py` | event loop не блокируется SQL |
| `backend/tests/unit/test_dashboard_parallel.py` | параллельные инсайты плиток |
| `backend/tests/unit/test_fast_insight.py` | шаблонный инсайт без LLM |
| `docs/reports/llm_choice.md` | таблица бенчмарка 30–40B моделей |
| `docs/reports/agent_research_2026-10-28.md` | сохранённый отчёт исследования |

### P0 — изменяемые файлы

| Путь | Изменения |
|---|---|
| `backend/app/config.py` | новые настройки, `json_object`, валидаторы URL/лимитов |
| `backend/app/core/agent/orchestrator.py` | бюджет, caps, router, fast insight, parallel dashboard, metrics, `done` |
| `backend/app/core/agent/planner.py` | max steps, preflight columns, time-hint validation, full-list fallback |
| `backend/app/core/agent/insight.py` | `fast_insight`, `fallback_insight`, budget, async DB reads |
| `backend/app/core/agent/step_executor.py` | preflight колонок, timeout, async execution |
| `backend/app/core/agent/turn_log.py` | route/llm_calls/tokens/total_ms в итоговой строке |
| `backend/app/core/llm/client.py` | budget timeout, `max_tokens`, `json_object`, schema overrides, metrics |
| `backend/app/core/data/duckdb_manager.py` | query/execute/save timeout, `interrupt`, async wrappers |
| `backend/app/core/tools/base.py` | `arun` через `asyncio.to_thread`, `column_fields`, подсказки аргументов |
| `backend/app/main.py` | startup summary с бюджетом/моделью; убрать TODO |
| `backend/app/api/routes/llm.py` | расширенный `/api/llm/status` |
| `backend/app/api/schemas/llm.py` | аддитивные optional-поля статуса |
| `backend/requirements.txt` | `Pillow>=10`; проверить `pypdf`, `vl-convert-python` |
| `backend/pyproject.toml` | `Pillow>=10`, `pytest-timeout>=2.3` (dev) |
| `.env.example` | новые AGENT_*/LLM_* переменные, единая модель |
| `docker-compose.yml` | единая модель по умолчанию, `LLM_NUM_CTX`, `LLM_MAX_TOKENS` |
| `infra/vllm/serve.sh` | prefix caching, 4-bit, `max-model-len`, `max-num-seqs` |
| `scripts/eval.py` | `--turn-timeout-s`, `--max-p95-s`, метрики в отчёте |
| `backend/tests/unit/test_orchestrator_unit.py` | новые события/бюджет |
| `backend/tests/unit/test_chat_api.py` | аддитивные поля `done` |
| `backend/tests/unit/test_llm_client_retries.py` | max_tokens/timeout/json_object |
| `docs/architecture.md` | раздел «Бюджет хода и деградация» |
| `README.md` | 15-минутный бюджет, модель, железо |

### P1 — новые файлы

| Путь | Назначение |
|---|---|
| `backend/app/models/semantic.py` | `SemanticModel`, `SemanticMetric`, `SemanticDimension`, `SemanticJoin` |
| `backend/app/core/data/semantic_model.py` | `SemanticRegistry`: загрузка/сохранение/валидация модели |
| `backend/app/core/data/semantic_planner.py` | `SemanticPlanner`: вопрос → метрики/измерения/фильтры |
| `backend/app/core/data/semantic_compiler.py` | `SemanticCompiler`: semantic plan → `ResearchPlan`/SQL |
| `backend/app/core/data/schema_index.py` | `SchemaIndex`, `SchemaRetriever`, embeddings + BM25 |
| `backend/app/core/agent/plan_graph.py` | `PlanGraph`, `PlanNode`, топологические слои |
| `backend/app/core/agent/parallel_executor.py` | `ParallelPlanExecutor` для DAG |
| `backend/app/core/agent/dry_run.py` | `DryRunValidator` на `EXPLAIN` |
| `backend/app/core/tools/schema.py` | `tool_schema`, `planner_schema` |
| `backend/app/core/llm/profiles.py` | `ModelProfile`, `ModelPool` |
| `backend/app/core/cache/plan_cache.py` | кеш планов |
| `backend/app/core/cache/result_cache.py` | кеш результатов/семантический кеш |
| `backend/app/api/routes/metrics.py` | `GET /api/metrics/turns`, `/summary` |
| `backend/tests/unit/test_semantic_model.py` | модель, загрузка, валидация |
| `backend/tests/unit/test_semantic_planner.py` | маппинг вопроса на метрики |
| `backend/tests/unit/test_plan_graph.py` | DAG, циклы, слои |
| `backend/tests/unit/test_parallel_executor.py` | параллелизм, изоляция ошибок |
| `backend/tests/unit/test_dry_run.py` | EXPLAIN-валидация |
| `backend/tests/unit/test_tool_schema.py` | oneOf/enum, аргументы |
| `backend/tests/unit/test_model_pool.py` | выбор роли, fallback |
| `backend/tests/unit/test_cache.py` | hit/miss/TTL/invalidation |
| `backend/tests/unit/test_metrics_api.py` | API метрик |

### P1 — изменяемые файлы

| Путь | Изменения |
|---|---|
| `backend/app/models/research_step.py` | `depends_on`, нормализация DAG-полей |
| `backend/app/core/agent/orchestrator.py` | semantic routing, DAG executor, dry-run, model pool |
| `backend/app/core/agent/planner.py` | semantic prompt, typed schema, retrieval, cache |
| `backend/app/core/agent/insight.py` | fast model role, semantic facts |
| `backend/app/core/data/semantic_select.py` | гибрид lexical + embeddings + reranker |
| `backend/app/core/memory/session_memory.py` | semantic model в `build_context`, data_version |
| `backend/app/core/memory/profile_memory.py` | `_meta.semantic_model`, `_meta.schema_embedding` (миграции) |
| `backend/app/core/data/ingest.py` | загрузка semantic model при attach/restore |
| `backend/app/api/routes/datasources.py` | CRUD semantic model |
| `backend/app/api/schemas/datasource.py` | схемы semantic model |
| `backend/app/core/tools/base.py` | `build_sql()`/`sql()` для dry-run |
| `backend/app/core/tools/registry.py` | `describe_for_schema`, `tool_schema` |
| `backend/app/core/llm/client.py` | `model`/`base_url` override, raw JSON schema |
| `backend/app/main.py` | model pool, metrics router, cache init |
| `scripts/eval.py` | новые метрики/наборы, budget regression |
| `docs/architecture.md`, `docs/memory.md`, `docs/prompts.md` | P1-архитектура |

### Удаляемые/перемещаемые

- P0: ничего.
- P1: `backend/app/core/agent/parser.py` — мёртвый код (не используется вне тестов); удалить после переноса тестов на `structured.py`.
- `backend/app/core/data/result_retention.py` — не удалять; расширить для pin кешей.

---

## [Functions]

### P0.1. Бюджет и жизненный цикл хода

`backend/app/core/agent/budget.py`:

```python
def budget_from_settings(settings: Settings) -> TurnBudget: ...
def current_budget() -> TurnBudget | None: ...
def set_budget(budget: TurnBudget | None) -> object: ...
def reset_budget(token: object) -> None: ...
```

`TurnBudget`:

- `remaining() -> float` — `total_s - (monotonic() - started_at)`.
- `stage_remaining(stage) -> float` — `min(remaining(), stage_limit - elapsed_stage)`; если этап не открыт — `remaining()`.
- `timeout_for(stage, default_s) -> float` — `max(0.1, min(default_s, remaining(), stage_remaining(stage)))`.
- `consume_llm_call(stage) -> None` — инкремент + проверки; `BudgetExceeded`.
- `enter_stage`/`leave_stage` — фиксируют время; `leave_stage` пишет в `TurnMetrics`.

`backend/app/core/agent/orchestrator.py`:

```python
async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
    budget = budget_from_settings(settings)
    metrics = TurnMetrics(session_id=..., turn_id=..., question=request.question)
    budget_token = set_budget(budget)
    metrics_token = set_metrics(metrics)
    try:
        async with asyncio.timeout(budget.total_s):
            async for event in self._turn(request):
                yield event
    except TimeoutError:
        # _failure_event(message: str | None = None) — расширить существующий helper
        yield _failure_event("Не удалось завершить ход за 15 минут")
        yield _done(..., message="timeout")
    finally:
        reset_budget(budget_token)
        reset_metrics(metrics_token)
        logger.info("метрики хода: %s", json.dumps(metrics.as_log(), ensure_ascii=False))
```

`_turn`:

- после `_morph` вызвать `router.simple_plan(...)`;
- при `Route` — пропустить `StepPlanner`, выполнить `_execute_plan`, эмитить `planning finished` с `total_steps`;
- при `BudgetExceeded` на этапе планирования — `clarification_needed` с текстом «Не удалось уложиться в бюджет планирования; уточните вопрос»;
- при `BudgetExceeded` на инсайте — `fallback_insight` и продолжить;
- при `BudgetExceeded` на дашборде — пропустить дашборд, отдать график.

`_plan`:

```python
async def _plan(self, question: str, context: dict) -> ResearchPlan:
    budget = current_budget()
    budget.enter_stage("planning")
    try:
        return await self.planner.plan(question, context)
    finally:
        budget.leave_stage("planning")
```

`_execute_plan`:

- `if len(plan.steps) > settings.agent_max_steps: raise PlanInvalid([...])`;
- перед шагом `budget.stage_remaining("steps")`;
- `MAX_REPLANS = 1`;
- `STEP_FIX_ATTEMPTS` из настроек (P0 default 1);
- после каждого шага `metrics.record_stage(f"step_{id}", elapsed)`.

```python
async def _run_step(...) -> tuple[ResearchStep, str]:
    budget = current_budget()
    if budget:
        budget.stage_remaining("steps")  # BudgetExceeded
    try:
        async with asyncio.timeout(
            budget.timeout_for("steps", settings.agent_step_timeout_s) if budget else None
        ):
            return step, await execute(step)
    except TimeoutError as exc:
        raise _StepFailed(step, BudgetExceeded("steps", 0, "SQL/шаг превысил лимит"), 1) from exc
```

### P0.2. LLM-клиент и structured output

`backend/app/core/llm/client.py`:

```python
async def complete(self, system: str, user: str, **kwargs: Any) -> str:
    budget = current_budget()
    timeout = budget.timeout_for("llm", settings.llm_timeout_s) if budget else settings.llm_timeout_s
    if budget:
        budget.consume_llm_call("llm")
    kwargs.setdefault("max_tokens", settings.llm_max_tokens)
    async with asyncio.timeout(timeout):
        resp = await self._client.chat.completions.create(
            ..., timeout=timeout, **kwargs
        )
    current_metrics().record_llm(prompt_tokens, completion_tokens)
    return content
```

```python
async def complete_structured(self, system, user, schema, *, schema_override=None) -> T:
    if settings.llm_structured_mode == "json_schema":
        return await self._complete_json_schema(system, user, schema, schema_override)
    if settings.llm_structured_mode == "json_object":
        return await self._complete_json_object(system, user, schema)
    return await self._complete_tools(...)
```

```python
async def _complete_json_schema(
    self, system: str, user: str, schema: type[T],
    schema_override: dict[str, Any] | None = None,
) -> T:
    json_schema = schema_override or self._schema_for(schema)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": json_schema},
    }
    ...
```

```python
async def _complete_json_object(self, system: str, user: str, schema: type[T]) -> T:
    raw = await self.complete(
        system, user, response_format={"type": "json_object"}
    )
    ...
```

```python
def _schema_for(self, schema: type[BaseModel]) -> dict[str, Any]:
    data = schema.model_json_schema()
    if schema is ResearchPlan:
        data["properties"]["steps"]["maxItems"] = settings.agent_max_steps
        data["$defs"]["ResearchStep"]["properties"]["tool"]["enum"] = [
            tool.name for tool in tool_registry.planner_tools()
        ]
    if schema is Insight:
        data["properties"]["headline"]["maxLength"] = 300
        data["properties"]["details"]["maxItems"] = 5
    return data
```

`_schema_for` импортирует `tool_registry` лениво внутри функции, чтобы не создать цикл импорта.

### P0.3. Валидация плана

`backend/app/core/agent/planner.py`:

```python
def validate_plan(
    plan: ResearchPlan,
    known_steps: int = 0,
    columns: set[str] | None = None,
    question: str = "",
) -> list[str]: ...
```

Проверки:

1. `len(plan.steps) <= settings.agent_max_steps`;
2. `tool` существует и не скрыт;
3. ссылки `$step_N` только на уже выполненные шаги;
4. `tool.parse_args` проходит;
5. если `columns` передан и шаг читает `$source`/`$prev` без предыдущего transform — колоночные аргументы есть в `columns`; ошибка с `difflib.get_close_matches`;
6. time-hint: если в вопросе есть `динамик|тренд|по месяцам|по неделям|по кварталам|по годам|во времени`, а в плане есть `revenue_analysis` без `date_column` и нет `time_bucket`/`trend_analysis` — ошибка «для динамики нужен date_column или time_bucket».

`StepPlanner.plan`:

```python
plan = await self._ask(prompt, start, template)
errors = validate_plan(plan, known_steps=start, columns=set(all_columns), question=question)
for _ in range(PLAN_FIX_ATTEMPTS):
    ...
    if errors and settings.agent_tool_topk:
        # full_prompt — тот же STEP_PLANNER, но tools=tool_registry.describe()
        plan = await self._ask(full_prompt + fix, start, template)  # fallback
    else:
        plan = await self._ask(prompt + fix, start, template)
```

### P0.4. Preflight колонок в исполнителе

`backend/app/core/tools/base.py`:

```python
def column_fields(self) -> set[str]:
    """Имена top-level аргументов с типом column/list[column]."""

def parse_args(self, columns=None, arguments=None) -> ToolArgs:
    ...
    # при extra_forbidden — подсказка ближайшего имени поля
```

`backend/app/core/agent/step_executor.py`:

```python
def _validate_columns(self, step: ResearchStep, table: str) -> None:
    tool = tool_registry.get(step.tool)
    args = tool.parse_args(step.columns, step.arguments)
    available = set(self.db.columns(table))
    for name in tool.column_fields():
        value = getattr(args, name, None)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and not item.startswith("$") and item not in available:
                hint = difflib.get_close_matches(item, available, n=1)
                raise ValueError(...)
```

Вызов — до `tool.arun`.

### P0.5. Router и fast insight

`backend/app/core/agent/router.py`:

```python
def simple_plan(
    question: str,
    profile: TableProfile | None,
    titles: dict[str, str] | None = None,
) -> Route | None: ...

def resolve_metric(question: str, profile: TableProfile | None, titles=None) -> str | None: ...
def resolve_category(question: str, profile: TableProfile | None, titles=None) -> str | None: ...
def resolve_date(question: str, profile: TableProfile | None) -> str | None: ...
```

Поддержанные шаблоны (только при высокой уверенности):

| Вопрос | План | `final_viz` |
|---|---|---|
| `сколько строк/записей` | `group_by(by=[], function="COUNT")` | `kpi` |
| `общая/суммарная <metric>` | `group_by(by=[], function="SUM", column=metric)` | `kpi` |
| `средняя <metric>` | `group_by(by=[], function="AVG", column=metric)` | `kpi` |
| `топ-N <category> по <metric>` | `group_by(by=[category], function="SUM", column=metric)` + `sort_limit` | `bar` |
| `<metric> по месяцам/неделям/кварталам/годам` | `time_bucket(date_column, interval, metric_column, function)` | `line` |
| `распределение <metric>` | `select_columns(columns=[metric])` | `histogram` |

Если колонка не найдена или вопрос содержит фильтры/несколько условий — `None` (идём в LLM).

`backend/app/core/agent/insight.py`:

```python
def fast_insight(
    self, question: str, result_table: str, steps: list[ResearchStep] | None = None
) -> Insight | None: ...

def fallback_insight(
    self, question: str, result_table: str, steps: list[ResearchStep] | None, error: str
) -> Insight: ...
```

`fast_insight`:

- результат — одна строка и одна числовая колонка;
- `headline = f"{question.strip()} — {format_value(value)}"`;
- `details = [f"Строк в результате: {n}", f"Колонка: {title or column}"]`;
- `limits = ""`.

`fallback_insight`:

- если есть результат — `headline = "Результат готов"`, `details` — число строк и значения;
- если результата нет — `headline = "Не удалось завершить анализ в отведённое время"`, `limits` — причина.

`generate_tile_insight`:

- одна числовая ячейка → `f"{title}: {value}"` без LLM;
- иначе — LLM с бюджетом `agent_insight_timeout_s`.

### P0.6. Параллельные плитки дашборда

`backend/app/core/agent/orchestrator.py`:

```python
async def _build_dashboard(...):
    specs = []
    for planned in plan.dashboard_tiles:
        table = execution.tables.get(planned.step_id)
        if table is None:
            continue
        specs.append((planned, self._tile_spec(planned, table, titles)))
    semaphore = asyncio.Semaphore(settings.agent_max_parallel_llm)
    insights = await asyncio.gather(
        *(self._tile_insight(planned, spec, semaphore) for planned, spec in specs),
        return_exceptions=True,
    )
    tiles = [...]
```

- `_tile_spec` — SQL/данные/спека, без LLM;
- `_tile_insight` — LLM/шаблон под `semaphore`;
- порядок плиток сохраняется;
- ошибка одной плитки не роняет дашборд.

### P0.7. SQL вне event loop и timeout

`backend/app/core/data/duckdb_manager.py`:

```python
def query(self, sql: str, params: dict | None = None, timeout_s: float | None = None) -> list[dict]: ...
def execute(self, sql: str, params: dict | None = None, timeout_s: float | None = None) -> None: ...
def save_step_result(self, source_table: str, sql: str, timeout_s: float | None = None) -> str: ...
def interrupt(self) -> None: ...
async def query_async(self, sql, params=None, timeout_s=None) -> list[dict]: ...
async def to_records_async(self, table: str, limit: int = 100) -> list[dict]: ...
async def columns_async(self, table: str) -> list[str]: ...
```

Внутри `query`/`execute`/`save_step_result`:

```python
timer = threading.Timer(timeout_s, self._con.interrupt) if timeout_s else None
if timer:
    timer.daemon = True
    timer.start()
try:
    ...
finally:
    if timer:
        timer.cancel()
```

`BaseTool.arun`:

```python
async def arun(self, ctx, db, input_table, columns=None, /, **arguments):
    return await asyncio.to_thread(self.run, db, input_table, columns, **arguments)
```

`InsightGenerator.generate` и `_dashboard_tile` используют `await db.to_records_async(...)` и `await db.query_async(...)`, а `insight_facts` вызывают через `asyncio.to_thread`.

### P0.8. Конфиг, health, модель

`backend/app/config.py`:

```python
@field_validator("llm_base_url")
@classmethod
def _normalize_base_url(cls, value: str) -> str:
    text = value.strip().rstrip("/")
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")]
    if not text.endswith("/v1"):
        logger.warning("LLM_BASE_URL не заканчивается на /v1: %s", text)
    return text
```

`backend/app/api/routes/llm.py`:

```python
@router.get("/status", response_model=LLMStatusOut)
def llm_status() -> LLMStatusOut:
    return LLMStatusOut(
        **ollama_setup.status(),
        context=budget.server_context() or settings.llm_num_ctx,
        structured_mode=settings.llm_structured_mode,
        max_tokens=settings.llm_max_tokens,
        last_latency_ms=metrics.last_latency_ms(),
    )
```

`backend/app/main.py`:

- `startup_summary` добавляет `turn_timeout`, `max_tokens`, `structured_mode`, `model`;
- убрать `TODO(round-2)` из `lifespan`.

`backend/app/core/logging_setup.py`:

- `startup_summary` — новые поля без ключей/DSN.

`scripts/eval.py`:

```python
parser.add_argument("--turn-timeout-s", type=int, default=None)
parser.add_argument("--max-p95-s", type=float, default=None)
```

- `--turn-timeout-s` выставляет `AGENT_TURN_TIMEOUT_S`;
- `--max-p95-s` добавляет в JSON `budget_p95_ok` и предупреждение/код возврата;
- в отчёт — `llm_calls_p50`, `llm_calls_p95` из `done.metrics`.

### P0.9. Метрики и итоговая строка

`backend/app/core/agent/turn_log.py`:

```python
def _finish(self, data: dict) -> None:
    ...
    message = (
        f"итог хода: {status} за {elapsed:.1f} с, "
        f"route={metrics.route}, llm_calls={metrics.llm_calls}, "
        f"prompt_tokens={metrics.prompt_tokens}, total_ms={int(elapsed * 1000)}"
    )
```

`LLMClient.complete`:

- `current_metrics().record_llm(prompt_tokens, completion_tokens)`;
- `metrics.model = self.model`.

`_done`:

```python
def _done(turn_id: str, *, message: str | None = None) -> dict:
    metrics = current_metrics()
    data = {"turn_id": turn_id}
    if message:
        data["message"] = message
    if metrics is not None:
        data.update(
            route=metrics.route,
            llm_calls=metrics.llm_calls,
            prompt_tokens=metrics.prompt_tokens,
            total_ms=metrics.total_ms(),
        )
    return {"type": "done", "data": data}
```

### P1.1. Semantic layer

`backend/app/core/data/semantic_model.py`:

```python
class SemanticRegistry:
    def load(self, table: str) -> SemanticModel | None: ...
    def save(self, model: SemanticModel) -> None: ...
    def delete(self, table: str) -> None: ...
    def validate(self, model: SemanticModel, columns: set[str]) -> list[str]: ...
    def load_yaml(self, path: Path) -> SemanticModel: ...
```

`backend/app/core/data/semantic_planner.py`:

```python
@dataclass(frozen=True)
class SemanticIntent:
    metrics: list[SemanticMetric]
    dimensions: list[SemanticDimension]
    filters: list[dict[str, Any]]
    time_range: tuple[str, str] | None
    time_grain: str | None
    joins: list[SemanticJoin]


class SemanticPlanner:
    def map(self, question: str, model: SemanticModel) -> SemanticIntent: ...
    def to_prompt(self, model: SemanticModel, intent: SemanticIntent) -> str: ...
```

`backend/app/core/data/semantic_compiler.py`:

```python
class SemanticCompiler:
    def compile(self, intent: SemanticIntent, model: SemanticModel) -> ResearchPlan: ...
    def to_sql(self, intent: SemanticIntent, model: SemanticModel) -> str: ...
```

`backend/app/core/agent/dry_run.py`:

```python
class DryRunValidator:
    def validate(self, plan: ResearchPlan, source_table: str) -> list[str]: ...
    def explain(self, sql: str) -> list[dict]: ...
```

`backend/app/core/tools/base.py` (P1):

```python
def build_sql(self, db, input_table: str, args: Any) -> str | None:
    """Вернуть SQL без выполнения; переопределяется SQL-тулами."""
```

`BaseTool.execute` вызывает `build_sql` и `db.save_step_result`. Для stat-тестов/codegen `build_sql` возвращает `None`; dry-run их пропускает.

`backend/app/core/memory/profile_memory.py`:

- `SCHEMA` дополняется:
  - `_meta.semantic_model(table_name VARCHAR PRIMARY KEY, model JSON, version INTEGER, updated_at TIMESTAMPTZ)`;
  - `_meta.schema_embedding(table_name, object_type, object_name, text_hash, model, vector JSON, updated_at, PRIMARY KEY(...))`;
- методы `load_semantic_model`, `save_semantic_model`, `delete_semantic_model`.

`backend/app/api/routes/datasources.py`:

```python
@router.get("/tables/{table}/semantic-model", response_model=SemanticModelOut)
def get_semantic_model(...): ...

@router.put("/tables/{table}/semantic-model", response_model=SemanticModelOut)
def put_semantic_model(...): ...
```

### P1.2. DAG и параллельный executor

`backend/app/core/agent/plan_graph.py`:

```python
class PlanGraph:
    @classmethod
    def from_plan(cls, plan: ResearchPlan) -> "PlanGraph": ...
    def validate(self) -> list[str]: ...
    def layers(self) -> list[list[ResearchStep]]: ...
    def dependents(self, step_id: int) -> set[int]: ...
```

`backend/app/core/agent/parallel_executor.py`:

```python
class ParallelPlanExecutor:
    async def execute(
        self,
        graph: PlanGraph,
        input_table: str,
        source_table: str,
        emit: Emit,
    ) -> Execution: ...
```

Правила:

- слой выполняется `asyncio.gather` с `Semaphore(agent_max_parallel_steps)`;
- SQL-шаги, пишущие `temp_result_vN`, сериализуются `db.lock`; параллелизм даёт эффект для LLM/codegen и независимых чтений;
- ошибка узла: dependents не выполняются, `pending_recovery` хранит `node_id` и слой;
- `result_step` выбирается как в текущем `_finish`;
- при `AGENT_DAG_EXECUTOR=false` — старый линейный путь.

`backend/app/models/research_step.py`:

- `depends_on: list[int] = Field(default_factory=list)`;
- валидатор: неотрицательные id, нет самоссылки.

`backend/app/core/llm/prompts/step_planner.py`:

- добавить `depends_on` в пример и правило: «для независимых плиток дашборда — `$source`, зависимости не нужны; для веток — `depends_on`».

### P1.3. Типизированные тулы и retrieval

`backend/app/core/tools/schema.py`:

```python
def tool_schema(tool: BaseTool) -> ToolSchema:
    """Pydantic-схема аргументов тула + JSON schema."""

def planner_schema(tools: list[BaseTool], max_steps: int) -> dict[str, Any]:
    """JSON schema ResearchPlan с oneOf по выбранным тулам."""
```

`backend/app/core/llm/client.py`:

```python
async def complete_structured(
    self, system, user, schema, *, schema_override: dict | None = None
) -> T: ...
```

`backend/app/core/data/schema_index.py`:

```python
class SchemaIndex:
    def rebuild(self, table: str, profile: TableProfile, semantic: SemanticModel | None) -> None: ...
    def search(self, question: str, table: str, top_k: int = 8) -> list[SchemaObject]: ...

class SchemaRetriever:
    def retrieve(self, question: str, table: str, top_k: int = 8) -> list[SchemaObject]: ...
```

Гибрид:

- lexical score (текущий `rank_columns`) — всегда;
- vector score (BGE-M3 / multilingual-e5) — если модель доступна;
- reranker (`bge-reranker-v2-m3`) — top-20 → top-8;
- при недоступности embeddings — fallback на lexical, без падения.

`backend/app/core/data/semantic_select.py`:

```python
def rank_columns(..., retriever: SchemaRetriever | None = None) -> list[str]: ...
```

### P1.4. Model pool

`backend/app/core/llm/profiles.py`:

```python
class ModelPool:
    def __init__(self, profiles: list[ModelProfile]) -> None: ...
    def get(self, role: str) -> LLMClient: ...
    def active(self, role: str) -> ModelProfile: ...
```

`LLMClient.__init__`:

```python
def __init__(
    self,
    http_client: httpx.AsyncClient | None = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: SecretStr | None = None,
    timeout_s: int | None = None,
) -> None: ...
```

`StepPlanner`:

```python
def __init__(self, llm: LLMClient | None = None, pool: ModelPool | None = None):
    self.llm = llm or (pool.get("planner") if pool else LLMClient())
```

`InsightGenerator`:

- `fast_insight`/tile insight → `pool.get("fast")`;
- сложный инсайт → `pool.get("planner")`.

`ModelRouter` (P1, простые правила):

- `simple` → fast;
- `complex` → planner;
- `sql_generation` → sql;
- при недоступности роли — planner/fallback.

### P1.5. Кеши

`backend/app/core/cache/plan_cache.py`:

```python
class PlanCache:
    def get(self, key: CacheKey) -> ResearchPlan | None: ...
    def put(self, key: CacheKey, plan: ResearchPlan, ttl_s: int) -> None: ...
    def invalidate(self, table: str) -> None: ...
```

`backend/app/core/cache/result_cache.py`:

```python
class ResultCache:
    def get(self, key: CacheKey) -> CachedResult | None: ...
    def put(self, key: CacheKey, result: CachedResult, ttl_s: int) -> None: ...
    def invalidate(self, table: str) -> None: ...
```

Ключ:

- `schema_hash` — SHA256 от колонок/типов/описаний/семантической модели;
- `data_version` — `row_count` + `profiled_at` + максимум по времени;
- `prompt_version` — версия промптов/схемы;
- `model` — имя модели.

Правила:

- exact match по `question_hash` — P1 MVP;
- semantic match (embedding) — только при высоком пороге и той же таблице;
- не кешировать вопросы с `recovery_response`, `reply_to` и `temperature > 0`;
- TTL по умолчанию 1 час; инвалидация при `ingest.attach/restore`, `PATCH datasource`, изменении semantic model.

### P1.6. Eval и observability

`backend/app/core/agent/metrics.py`:

- `TurnMetrics` → SQLite `turn_metrics` (session_id, turn_id, route, model, stages JSON, llm_calls, tokens, total_ms, budget_exceeded, created_at);
- `MetricsStore.record`, `summary`, `list_turns`.

`backend/app/api/routes/metrics.py`:

```python
@router.get("/turns", response_model=list[TurnMetricsOut])
def turns(limit: int = 100, session_id: str | None = None): ...

@router.get("/summary", response_model=MetricsSummaryOut)
def summary(days: int = 7): ...
```

`backend/app/main.py`:

- `app.include_router(metrics.router, prefix="/api/metrics")`;
- optional OpenTelemetry instrumentation behind `OTEL_ENABLED`.

`scripts/eval.py`:

- новые проверки: `llm_calls`, `total_ms`, `budget_exceeded`;
- отчёт: p50/p95 по этапам, pass^k, стоимость (если известна), plan validity.

### P1.7. Multi-table, ambiguity, security

`backend/app/core/data/semantic_planner.py`:

```python
@dataclass(frozen=True)
class Ambiguity:
    question: str
    candidates: list[str]
    reason: str


class ClarificationPolicy:
    def should_ask(self, intent: SemanticIntent) -> Ambiguity | None: ...
```

- если несколько метрик/измерений с одинаковым score — задать один уточняющий вопрос, не строить план.

`backend/app/core/agent/planner.py`:

- `validate_plan` проверяет join по `SemanticModel.joins`; произвольные join-условия запрещены;
- `join_tables` получает `right_table` только из semantic graph.

`backend/app/core/llm/prompts/*`:

- данные/значения колонок помечаются как untrusted;
- запрещено выполнять инструкции из данных;
- `SYSTEM_ANALYST` дополняется правилом.

---

## [Classes]

### P0 — новые

| Класс | Файл | Ключевые методы | Ответственность |
|---|---|---|---|
| `BudgetExceeded` | `backend/app/core/agent/budget.py` | `__init__` | сигнал превышения бюджета/лимита |
| `TurnBudget` | `backend/app/core/agent/budget.py` | `remaining`, `stage_remaining`, `timeout_for`, `consume_llm_call`, `enter_stage`, `leave_stage`, `snapshot` | единый бюджет хода |
| `Route` | `backend/app/core/agent/router.py` | dataclass | результат быстрого роутера |
| `TurnMetrics` | `backend/app/core/agent/metrics.py` | `record_llm`, `record_stage`, `as_log`, `total_ms` | per-turn метрики |

### P0 — изменяемые

| Класс | Файл | Изменения |
|---|---|---|
| `AgentOrchestrator` | `backend/app/core/agent/orchestrator.py` | бюджет, router, caps, fast insight, parallel dashboard, metrics, `done` |
| `StepPlanner` | `backend/app/core/agent/planner.py` | max steps, preflight, time-hint, full-list fallback |
| `InsightGenerator` | `backend/app/core/agent/insight.py` | `fast_insight`, `fallback_insight`, budget, async DB |
| `LLMClient` | `backend/app/core/llm/client.py` | budget timeout, `max_tokens`, `json_object`, schema overrides, metrics |
| `DuckDBManager` | `backend/app/core/data/duckdb_manager.py` | timeout, `interrupt`, async wrappers |
| `BaseTool` | `backend/app/core/tools/base.py` | async offload, `column_fields`, arg hints |
| `StepExecutor` | `backend/app/core/agent/step_executor.py` | preflight, timeout |
| `TurnLog` | `backend/app/core/agent/turn_log.py` | route/llm_calls/tokens |
| `Settings` | `backend/app/config.py` | новые поля/валидаторы |

### P1 — новые

| Класс | Файл | Ключевые методы | Ответственность |
|---|---|---|---|
| `SemanticModel` | `backend/app/models/semantic.py` | Pydantic | управляемая семантика таблицы |
| `SemanticRegistry` | `backend/app/core/data/semantic_model.py` | `load`, `save`, `delete`, `validate`, `load_yaml` | хранение/валидация semantic model |
| `SemanticPlanner` | `backend/app/core/data/semantic_planner.py` | `map`, `to_prompt` | вопрос → метрики/измерения/фильтры |
| `SemanticCompiler` | `backend/app/core/data/semantic_compiler.py` | `compile`, `to_sql` | semantic intent → `ResearchPlan`/SQL |
| `DryRunValidator` | `backend/app/core/agent/dry_run.py` | `validate`, `explain` | проверка SQL до выполнения |
| `PlanGraph` | `backend/app/core/agent/plan_graph.py` | `from_plan`, `validate`, `layers`, `dependents` | DAG плана |
| `ParallelPlanExecutor` | `backend/app/core/agent/parallel_executor.py` | `execute` | исполнение слоёв DAG |
| `ToolSchema` | `backend/app/core/tools/schema.py` | dataclass | typed schema тула |
| `SchemaIndex` | `backend/app/core/data/schema_index.py` | `rebuild`, `search` | индекс колонок/метрик |
| `SchemaRetriever` | `backend/app/core/data/schema_index.py` | `retrieve` | гибридный retrieval |
| `ModelProfile` | `backend/app/core/llm/profiles.py` | dataclass | профиль модели |
| `ModelPool` | `backend/app/core/llm/profiles.py` | `get`, `active` | выбор клиента по роли |
| `PlanCache` | `backend/app/core/cache/plan_cache.py` | `get`, `put`, `invalidate` | кеш планов |
| `ResultCache` | `backend/app/core/cache/result_cache.py` | `get`, `put`, `invalidate` | кеш результатов |
| `MetricsStore` | `backend/app/core/agent/metrics.py` | `record`, `summary`, `list_turns` | SQLite-метрики |

### P1 — изменяемые

- `AgentOrchestrator` — semantic routing, DAG, dry-run, model pool, cache.
- `StepPlanner` — semantic prompt, typed schema, retrieval, cache.
- `InsightGenerator` — fast model role, semantic facts.
- `SessionMemory` — semantic model и `data_version` в `build_context`.
- `ProfileMemory` — новые таблицы `_meta.semantic_model`, `_meta.schema_embedding`.
- `BaseTool` — `build_sql`/`sql` для dry-run.
- `ToolRegistry` — `describe_for_schema`, `tool_schema`.

### Удаляемые

- P0: нет.
- P1: `backend/app/core/agent/parser.py` — мёртвый код; тесты перенести на `structured.py`.

---

## [Dependencies]

### P0

| Зависимость | Версия | Зачем |
|---|---|---|
| `Pillow` | `>=10` | `backend/app/core/export/pdf.py` импортирует `PIL` на верхнем уровне; без него приложение не стартует |
| `pytest-timeout` | `>=2.3` | тесты бюджета/таймаутов не должны зависать |
| `pypdf` | `>=4.3` | уже в dev; проверить наличие в CI/venv |
| `vl-convert-python` | `>=1.6` | уже в runtime; проверить, что не тянет лишнего |
| `vLLM` | pinned infra | 30–40B, prefix caching, structured outputs, 4-bit |
| `Qwen3-30B-A3B-Instruct-2507` / `Qwen3-32B` | 4-bit checkpoint | primary/quality fallback |

Никаких новых фреймворков оркестрации в P0. `httpx2`, `openai`, `instructor`, `duckdb`, `fastapi` не меняются.

### P1

| Зависимость | Версия | Зачем |
|---|---|---|
| `sentence-transformers` | `>=3.0` | BGE-M3 / multilingual-e5 embeddings |
| `FlagEmbedding` | optional | альтернатива для BGE-M3 |
| `rank-bm25` | optional | lexical BM25 fallback |
| `sqlglot` | `>=25` | AST-валидация/dialect, если понадобится |
| `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi` | `>=1.27` | traces |
| `prometheus-client` | `>=0.20` | метрики |
| `langfuse` | optional self-hosted | LLM observability |
| `pytest-benchmark` | optional | latency-регрессии |

Embeddings-модель: `BAAI/bge-m3` (мультиязычная, 8k context) или `intfloat/multilingual-e5-small` для CPU. Reranker: `BAAI/bge-reranker-v2-m3`. Модели скачиваются на сборке/при первом запуске; в закрытом контуре — заранее в volume.

### Модели и лицензии

- Qwen3-30B-A3B-Instruct-2507 — Apache 2.0, 30,5B total / 3,3B active, 262k context.
- Qwen3-32B — Apache 2.0, 32,8B, 32k native / 131k YaRN.
- Gemma 3 27B — Gemma license, 27,4B, 128k, 140 языков.
- gpt-oss-20b — Apache 2.0, 21B total / 3,6B active, 128k.
- XiYanSQL-QwenCoder-32B / OmniSQL-32B — проверить лицензию и доступность 4-bit checkpoint.

---

## [Testing]

### P0 — unit-тесты

| Файл | Что проверяет |
|---|---|
| `test_turn_budget.py` | `remaining`, `stage_remaining`, `timeout_for`, max calls, `BudgetExceeded` |
| `test_router.py` | шаблоны → ожидаемые tool/args; неоднозначность → `None`; фильтры не перехватываются |
| `test_plan_validation_columns.py` | неверная колонка ловится до исполнения; `fix_step` не вызывается |
| `test_llm_client_budget.py` | `max_tokens` пробрасывается; timeout = min; `json_object`; budget cancellation |
| `test_duckdb_timeout.py` | долгий `SELECT` прерывается; lock освобождён; соединение живо |
| `test_event_loop_offload.py` | тикер каждые 10 мс тикает во время SQL в thread |
| `test_fast_insight.py` | одна ячейка → LLM не вызывается; таймаут → шаблонный fallback |
| `test_dashboard_parallel.py` | 3 плитки по 100 мс → <300 мс, порядок сохранён |
| `test_config_profiles.py` | нормализация `LLM_BASE_URL`, `json_object`, невалидные значения |
| `test_orchestrator_budget.py` | медленная LLM + `AGENT_TURN_TIMEOUT_S=2` → ход <3 с, `error`+`done` |

### P0 — обновляемые тесты

- `test_orchestrator_unit.py` — новые события/бюджет, `done` с метриками.
- `test_chat_api.py` — аддитивные поля `done`; старый тест `{"type":"done","data":{}}` обновить.
- `test_llm_client_retries.py` — max_tokens/timeout/json_object.
- `test_plan_execution.py` — preflight/time-hint.
- `test_insight_generator.py` — fast/fallback.

### P0 — интеграционные и eval

```bash
cd backend && pytest -q
cd backend && pytest -q tests/unit/test_turn_budget.py tests/unit/test_router.py \
  tests/unit/test_duckdb_timeout.py tests/unit/test_event_loop_offload.py
python3 scripts/eval.py --fake --per-scenario 1
python3 scripts/eval.py --questions data/eval/hk/questions.yaml \
  --columns-recall-only --repeat 1 --model qwen3-30b-a3b
python3 scripts/eval.py --questions data/eval/hk/questions.yaml \
  --columns-recall-only --repeat 3 --max-p95-s 900 --model qwen3-32b
```

Критерии:

- 0 collection errors после установки `Pillow`/`pypdf`;
- p95 ≤ 900 с, p50 ≤ 300 с на целевом GPU;
- `budget_p95_ok=true`;
- pass rate не ниже baseline −5 п.п.;
- в `done` есть `llm_calls`/`prompt_tokens`/`total_ms`.

### P0 — ручной smoke

1. `make up` на чистой машине с `.env.example`.
2. CSV → «что передо мной?» → «общая сумма amount» → «топ-5 пользователей по сумме» → «покажи по месяцам» → «другая форма» → дашборд → PDF/XLSX.
3. Отключить клиент во время планирования → ход должен закрыться `disconnected`, задача отменена.
4. Убить LLM/Ollama → ход должен завершиться `error` в пределах бюджета, без зависания.

### P1 — unit-тесты

| Файл | Что проверяет |
|---|---|
| `test_semantic_model.py` | загрузка/сохранение/валидация, неизвестные колонки, YAML |
| `test_semantic_planner.py` | «выручка» → метрика, «по месяцам» → time grain, ambiguous → clarification |
| `test_plan_graph.py` | вывод зависимостей, циклы, `$prev` без transform, слои |
| `test_parallel_executor.py` | параллелизм слоёв, изоляция ошибок, dependents не выполняются |
| `test_dry_run.py` | `EXPLAIN` ловит missing column/type, не выполняет запись |
| `test_tool_schema.py` | oneOf/enum, typed args, fallback-схема |
| `test_model_pool.py` | роли, fallback, отсутствие ключей в логах |
| `test_cache.py` | hit/miss/TTL/invalidation, отсутствие cross-session leak |
| `test_metrics_api.py` | `/api/metrics/turns`, `/summary` |
| `test_schema_index.py` | lexical + vector, fallback без embeddings, recall |

### P1 — eval

- Набор ≥150 вопросов:
  - 35 HK + 51 базовых;
  - +30 multi-table/join;
  - +20 ambiguity/clarification;
  - +10 prompt injection/untrusted data;
  - +10 wide schema/long context.
- Метрики: pass rate, pass^k, p50/p95, LLM calls, tokens, plan validity, column recall, groundedness, clarification rate.
- CI: PR — FakeLLM; nightly — реальная модель; p95 regression >10% — fail.
- SLO: p95 ≤ 900 с, p50 ≤ 300 с; ошибки `budget_exceeded` ≤1%.

---

## [Implementation Order]

### P0 — 3 дня до сдачи

| День | Шаг | Работа | Файлы | Гейт |
|---|---|---|---|---|
| 1, утро | 1 | Конфиг/зависимости/health | `config.py`, `requirements.txt`, `pyproject.toml`, `.env.example`, `llm.py` | приложение импортируется, `pytest --collect-only` без ошибок |
| 1, утро | 2 | `TurnBudget` + `TurnMetrics` | `budget.py`, `metrics.py` | unit-тесты бюджета |
| 1, день | 3 | Интеграция бюджета в LLM/orchestrator | `client.py`, `orchestrator.py` | медленный FakeLLM → ход < 3 с |
| 1, вечер | 4 | Caps: max tokens/steps/calls/replans | `config.py`, `planner.py`, `orchestrator.py` | план >8 шагов отклоняется |
| 2, утро | 5 | SQL offload + `interrupt()` | `duckdb_manager.py`, `tools/base.py`, `insight.py` | event loop тикает; SQL прерывается |
| 2, день | 6 | Preflight + schema overrides | `planner.py`, `step_executor.py`, `client.py`, `tools/base.py` | неверная колонка ловится до шага |
| 2, вечер | 7 | Router + fast insight | `router.py`, `insight.py`, `orchestrator.py` | простые вопросы без planner/LLM |
| 3, утро | 8 | Parallel dashboard | `orchestrator.py` | 3 плитки <300 мс в тесте |
| 3, день | 9 | vLLM/tool retrieval/бенчмарк | `infra/vllm/serve.sh`, `quality_fixes.py`, `.env.example` | p95 ≤900 с на 35 HK |
| 3, вечер | 10 | Eval/доки/фиксы | `scripts/eval.py`, docs | `pytest` + eval зелёные |

Правила P0:

- каждый шаг за флагом, если меняет поведение (`AGENT_SIMPLE_ROUTER`, `AGENT_FAST_INSIGHT`, `AGENT_TOOL_TOPK`);
- бюджет и caps включены всегда (safety);
- никаких миграций, ломающих старые сессии;
- после шага 4 — обязательный прогон `pytest` и `eval --fake`;
- после шага 7 — сравнение pass rate с baseline; при падении >5 п.п. флаг выключить.

### P1 — 6–8 недель после сдачи

| Неделя | Шаг | Работа | Зависимости | Гейт |
|---|---|---|---|---|
| 1 | 1 | `SemanticModel` + `SemanticRegistry` + YAML/БД | — | модель валидируется, API CRUD |
| 1–2 | 2 | `SemanticPlanner` + prompt + `validate_plan` | 1 | 3 датасета, метрики находятся |
| 2 | 3 | `build_sql` + `DryRunValidator` | 1 | EXPLAIN ловит 90% ошибок колонок |
| 2–3 | 4 | `PlanGraph` + DAG executor за флагом | — | линейные планы эквивалентны |
| 3 | 5 | Typed `planner_schema` + fallback | 1, 2 | fix_step −50% на eval |
| 3–4 | 6 | `SchemaIndex` + embeddings/reranker | 1 | recall колонок +; fallback без модели |
| 4 | 7 | `ModelPool` + fast/planner/sql | 2 | fast обрабатывает ≥60% простых |
| 4–5 | 8 | `PlanCache` + `ResultCache` | 1, 2, 7 | hit rate ≥20%, нет stale |
| 5 | 9 | `MetricsStore` + `/api/metrics` + OTel | P0 metrics | p50/p95 по этапам |
| 5–6 | 10 | Eval ≥150 вопросов + nightly | 1–9 | p95 ≤900, регресс ≤10% |
| 6–7 | 11 | Multi-table joins + ambiguity + security | 2, 4 | join только по semantic graph |
| 7–8 | 12 | Стабилизация, документация, rollout | все | feature flags, rollback |

### Feature flags и rollout

| Флаг | P0 default | P1 default | Rollback |
|---|---|---|---|
| `AGENT_TURN_TIMEOUT_S` | 870 | 870 | увеличить/уменьшить |
| `AGENT_MAX_STEPS` | 8 | 8–12 | поднять |
| `AGENT_MAX_LLM_CALLS` | 6 | 8 | поднять |
| `AGENT_SIMPLE_ROUTER` | true | true | false |
| `AGENT_FAST_INSIGHT` | true | true | false |
| `AGENT_TOOL_TOPK` | false | true (с fallback) | false |
| `AGENT_DAG_EXECUTOR` | — | false → true | false |
| `AGENT_SEMANTIC_LAYER` | — | false → true | false |
| `LLM_STRUCTURED_MODE` | `json_schema` | `json_schema` | `json_object`/`tools` |

---

## Риски и меры

| Риск | Вероятность | Влияние | Мера |
|---|---|---|---|
| 30–40B не помещается в 24 ГБ | средняя | высокое | 4-bit AWQ/GPTQ; Qwen3-30B-A3B; fallback 14B; замер VRAM |
| 32B dense слишком медленный | высокая | высокое | primary MoE 30B-A3B; 32B — quality fallback |
| vLLM не поддерживает нужный checkpoint/флаг | средняя | среднее | проверить версию; `--quantization`; fallback `bitsandbytes` |
| `interrupt()` оставляет соединение в плохом состоянии | низкая | высокое | тест; при сбое — переоткрыть соединение |
| Router ошибочно перехватывает сложный вопрос | средняя | высокое | только высоконадёжные шаблоны; флаг; fallback в LLM |
| Fast insight ухудшает бизнес-текст | средняя | среднее | включать только для одной ячейки; сравнение eval |
| Preflight ложно rejects `$step_N` | средняя | среднее | проверять только `$source`/первый шаг; fallback |
| DAG параллелизм не даёт выигрыша из-за DuckDB lock | высокая | среднее | честно: параллелить LLM/codegen; writer pool — P2 |
| Semantic layer требует ручной разметки | высокая | среднее | начать с 3 таблиц; автогенерация черновика из паспорта |
| Кеш отдаёт устаревшие данные | средняя | высокое | `data_version`, TTL, инвалидация на attach/restore |
| Судья eval ±14% | высокая | среднее | 3 голоса, ручная калибровка, pass^k |

### План отката

- Все новые поведения — за env-флагами; выключение возвращает текущий путь.
- Новые таблицы `_meta` — аддитивные, `CREATE TABLE IF NOT EXISTS`; старые данные не мигрируются.
- `depends_on` — опциональное поле; старые планы читаются как линейные.
- Semantic layer отсутствует → planner работает по текущим колонкам.
- Model pool недоступен → fallback на основной `LLMClient`.

---

## Definition of Done

### P0

- [ ] `pytest -q` зелёный; 0 collection errors.
- [ ] Любой ход завершается ≤870 с; тест с медленной LLM проходит.
- [ ] `done` содержит `route`, `llm_calls`, `prompt_tokens`, `total_ms`.
- [ ] SQL не блокирует event loop; query timeout работает.
- [ ] Простые вопросы не вызывают planner; одна ячейка — не вызывает LLM-инсайт.
- [ ] Preflight ловит неверные колонки до выполнения.
- [ ] Eval 35 HK: p95 ≤900 с, pass rate не ниже baseline −5 п.п.
- [ ] `docs/reports/llm_choice.md` заполнен; модель по умолчанию зафиксирована.
- [ ] `make up` на чистой машине с `.env.example` работает.

### P1

- [ ] Semantic model для ≥3 таблиц; API CRUD; planner использует метрики.
- [ ] Dry-run ловит ≥90% ошибок колонок на eval.
- [ ] DAG executor за флагом; линейные планы эквивалентны.
- [ ] Typed schema снижает `fix_step` на ≥50%.
- [ ] Model pool: fast-модель обслуживает ≥60% простых/инсайтовых вызовов.
- [ ] Кеш: hit rate ≥20% на повторных вопросах; 0 stale-ответов в тестах.
- [ ] Eval ≥150 вопросов; nightly; p95 ≤900 с; регресс ≤10%.
- [ ] `/api/metrics/turns` и `/summary` работают; трассировка включена.
- [ ] Multi-table join только по semantic graph; ambiguity → clarification.

---

## Приложение: научная база

- CHESS: schema selector, information retriever, candidate generator, unit tester. [GitHub](https://github.com/ShayanTalaei/CHESS), [arXiv:2405.16755](https://arxiv.org/abs/2405.16755).
- XiYan-SQL: M-Schema, multi-generator, refiner, selection model. [GitHub](https://github.com/XGenerationLab/XiYan-SQL), [arXiv:2411.08599](https://arxiv.org/abs/2411.08599).
- Alpha-SQL: MCTS, 32B zero-shot 69,7% BIRD. [arXiv:2502.17248](https://arxiv.org/abs/2502.17248).
- OmniSQL: SynSQL-2.5M, 7B/14B/32B. [arXiv:2503.02240](https://arxiv.org/abs/2503.02240).
- Spider 2.0: 632 enterprise-задачи, o1-preview 21,3%. [arXiv:2411.07763](https://arxiv.org/abs/2411.07763).
- WrenAI: MDL, dry-plan validation, AI context layer. [Canner/WrenAI](https://github.com/Canner/WrenAI).
- SuperSonic: semantic layer + rule/LLM parser + corrector. [tencentmusic/supersonic](https://github.com/tencentmusic/supersonic).
- LLMCompiler: parallel function calling, 3,7× latency. [arXiv:2312.04511](https://arxiv.org/abs/2312.04511).
- ReWOO: plan without observations, 5× token efficiency. [arXiv:2305.18323](https://arxiv.org/abs/2305.18323).
- Anthropic Building Effective Agents: [ссылка](https://www.anthropic.com/research/building-effective-agents).
- 12-factor agents: [humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents).
- FrugalGPT: [arXiv:2305.05176](https://arxiv.org/abs/2305.05176).
- RouteLLM: [arXiv:2406.18665](https://arxiv.org/abs/2406.18665).
- vLLM APC: [docs](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html).
- vLLM Structured Outputs: [docs](https://docs.vllm.ai/en/latest/features/structured_outputs.html).
- DSBench: [arXiv:2409.07703](https://arxiv.org/abs/2409.07703).
- InfiAgent-DABench: [arXiv:2401.05507](https://arxiv.org/abs/2401.05507).
- Data Interpreter: [arXiv:2402.18679](https://arxiv.org/abs/2402.18679).
- DeepAnalyze: [arXiv:2510.16872](https://arxiv.org/abs/2510.16872).

---

## Итог

P0 — это не «переписать агента», а **семь инженерных гарантий**: бюджет, лимиты, SQL вне event loop, preflight, быстрые пути, параллельные плитки и метрики. Они дают 15 минут без смены модели и без semantic layer.

P1 — это уже качество: semantic layer, DAG, typed schemas, retrieval, model pool, cache, eval. Его можно делать после сдачи, не блокируя релиз.















