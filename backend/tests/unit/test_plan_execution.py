"""Typed tool arguments, step references, plan validation and lookup re-planning."""

import asyncio

import pytest

from app.api.schemas.chat import ProgressPayload
from app.core.agent.orchestrator import (
    UNSUPPORTED_SUGGESTIONS,
    AgentOrchestrator,
    ClarificationNeeded,
)
from app.core.agent.planner import PlanInvalid, StepPlanner, validate_plan
from app.core.agent.step_executor import StepExecutor
from app.core.dashboards.templates import TEMPLATES
from app.core.data.duckdb_manager import DuckDBManager
from app.core.sandbox import codegen_runner
from app.core.sandbox.wasm_runtime import SandboxError
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchPlan, ResearchStep


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE raw AS SELECT * FROM (
            VALUES
                (1, DATE '2024-01-01', 'msk', 'a', 10.0),
                (2, DATE '2024-02-01', 'msk', 'b', 20.0),
                (3, DATE '2024-03-01', 'spb', 'a', 30.0),
                (4, DATE '2024-04-01', 'spb', 'b', 40.0),
                (5, DATE '2024-05-01', 'kzn', NULL, 50.0)
        ) AS rows(id, event_date, region, category, amount)
        """
    )
    yield manager
    manager.close()


def records(db: DuckDBManager, name: str, **arguments: object) -> list[dict]:
    return db.to_records(tool_registry.get(name).run(db, "raw", **arguments))


# --- типизированные аргументы и новые тулы ---


def test_filter_rows_supports_lists_ranges_and_nulls(db: DuckDBManager) -> None:
    rows = records(db, "filter_rows", column="region", operator="in", value=["msk", "kzn"])
    assert [row["id"] for row in rows] == [1, 2, 5]
    rows = records(
        db,
        "filter_rows",
        column="event_date",
        operator="BETWEEN",
        value=["2024-02-01", "2024-03-31"],
    )
    assert [row["id"] for row in rows] == [2, 3]
    rows = records(db, "filter_rows", column="category", operator="is null")
    assert [row["id"] for row in rows] == [5]
    rows = records(db, "filter_rows", column="amount", operator=">", value=25)
    assert [row["id"] for row in rows] == [3, 4, 5]


def test_filter_rows_rejects_value_shape_mismatch(db: DuckDBManager) -> None:
    with pytest.raises(ValueError, match="BETWEEN"):
        records(db, "filter_rows", column="amount", operator="BETWEEN", value=[1])


def test_group_by_multiple_keys_and_metrics(db: DuckDBManager) -> None:
    rows = records(
        db,
        "group_by",
        by=["region"],
        metrics=[
            {"function": "sum", "column": "amount", "alias": "revenue"},
            {"function": "COUNT_DISTINCT", "column": "category"},
        ],
    )
    msk = next(row for row in rows if row["region"] == "msk")
    assert msk["revenue"] == 30.0
    assert msk["count_distinct_category"] == 2


def test_sort_limit_returns_top_n(db: DuckDBManager) -> None:
    rows = records(db, "sort_limit", order_by="amount", limit=2)
    assert [row["amount"] for row in rows] == [50.0, 40.0]


def test_sample_is_reproducible(db: DuckDBManager) -> None:
    first = records(db, "sample", n=3, seed=7)
    second = records(db, "sample", n=3, seed=7)
    assert len(first) == 3
    assert first == second


def test_window_metric_and_partitioned_lag(db: DuckDBManager) -> None:
    rows = records(
        db, "window_metric", function="cumulative_sum", column="amount", order_column="event_date"
    )
    assert [row["cumulative_sum_amount"] for row in rows] == [10.0, 30.0, 60.0, 100.0, 150.0]
    rows = records(db, "window_metric", function="share_of_total", column="amount")
    assert sum(row["share_of_total_amount"] for row in rows) == pytest.approx(1.0)
    rows = records(db, "lag", column="amount", order_column="event_date", partition_by=["region"])
    spb_second = next(row for row in rows if row["id"] == 4)
    assert spb_second["lag_amount"] == 30.0
    assert next(row for row in rows if row["id"] == 3)["lag_amount"] is None


def test_time_bucket_aggregates_metric(db: DuckDBManager) -> None:
    rows = records(
        db,
        "time_bucket",
        date_column="event_date",
        interval="quarter",
        metric_column="amount",
        function="SUM",
    )
    assert [row["sum_amount"] for row in rows] == [60.0, 90.0]


def test_forecast_extrapolates_the_fitted_line_with_dates(db: DuckDBManager) -> None:
    rows = records(
        db,
        "forecast",
        date_column="event_date",
        metric_column="amount",
        periods=2,
        interval="month",
        include_history=True,
    )
    forecast = [row for row in rows if row["kind"] == "forecast"]
    assert [row["value"] for row in forecast] == pytest.approx([60.0, 70.0])
    assert [str(row["period"])[:10] for row in forecast] == ["2024-06-01", "2024-07-01"]
    assert len(rows) == 7


def test_stat_tests_report_p_values(db: DuckDBManager) -> None:
    rows = records(db, "mann_whitney_u", column_a="amount", column_b="category")
    assert 0 <= rows[0]["p_value"] <= 1
    rows = records(db, "anova_one_way", value_column="amount", group_column="region")
    assert rows[0]["groups"] == 3
    assert 0 <= rows[0]["p_value"] <= 1


def test_cyrillic_identifiers_are_accepted(db: DuckDBManager) -> None:
    db.execute("CREATE TABLE ru AS SELECT 'мск' AS регион, 10.0 AS выручка")
    table = tool_registry.get("group_by").run(
        db, "ru", by="регион", column="выручка", function="SUM"
    )
    assert db.to_records(table) == [{"регион": "мск", "sum_выручка": 10.0}]


def test_positional_and_named_arguments_combine(db: DuckDBManager) -> None:
    rows = db.to_records(tool_registry.get("aggregate").run(db, "raw", ["region"], limit=1))
    assert len(rows) == 1
    with pytest.raises(ValueError, match="too many positional"):
        tool_registry.get("aggregate").run(db, "raw", ["region", 1, "extra"])


# --- исполнитель: ссылки на шаги ---


def test_executor_resolves_step_and_source_references(db: DuckDBManager) -> None:
    executor = StepExecutor(db=db)
    filtered = asyncio.run(
        executor.execute(
            ResearchStep(tool="filter_rows", arguments={"column": "region", "value": "msk"}),
            "raw",
        )
    )
    tables = {0: filtered}
    from_step = asyncio.run(
        executor.execute(
            ResearchStep(tool="aggregate", input="$step_0", arguments={"column": "region"}),
            "raw",
            step_tables=tables,
        )
    )
    assert db.to_records(from_step) == [{"region": "msk", "cnt": 2}]
    joined = asyncio.run(
        executor.execute(
            ResearchStep(
                tool="join_tables",
                input="$source",
                arguments={"right_table": "$step_0", "condition": "t.id = t_right.id"},
            ),
            filtered,
            step_tables=tables,
            source_table="raw",
        )
    )
    assert len(db.to_records(joined)) == 2


def test_executor_rejects_reference_to_unexecuted_step(db: DuckDBManager) -> None:
    with pytest.raises(ValueError, match="ещё не выполнен"):
        asyncio.run(
            StepExecutor(db=db).execute(
                ResearchStep(tool="aggregate", input="$step_3", arguments={"column": "region"}),
                "raw",
            )
        )


def test_research_step_normalizes_argument_synonyms() -> None:
    step = ResearchStep.model_validate(
        {"tool": "aggregate", "params": {"column": "region"}, "input": 2}
    )
    assert step.arguments == {"column": "region"}
    assert step.input == "$step_2"


def test_plan_schema_requires_step_fields_and_known_templates() -> None:
    # Схема уходит модели в response_format: без required она пропускала
    # input и arguments, а tool присылала null.
    step = ResearchStep.model_json_schema()
    assert step["required"] == ["tool", "input", "arguments", "purpose"]
    assert step["properties"]["tool"]["anyOf"] == [{"type": "string"}]
    plan = ResearchPlan.model_json_schema()
    assert plan["required"] == ["steps"]
    template = plan["properties"]["template_id"]["anyOf"][0]
    assert template["enum"] == [item.id for item in TEMPLATES]


# --- планировщик: валидация до выполнения ---


def test_validate_plan_reports_tool_argument_and_reference_errors() -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(step_id=0, tool="no_such_tool"),
            ResearchStep(step_id=1, tool="filter_rows", arguments={"column": "x;"}),
            ResearchStep(
                step_id=2, tool="aggregate", input="$step_5", arguments={"column": "region"}
            ),
            ResearchStep(step_id=3, tool=None),
        ],
        result_step=9,
    )
    errors = validate_plan(plan)
    assert len(errors) == 5
    assert "неизвестный инструмент" in errors[0]
    assert "filter_rows" in errors[1]
    assert "$step_5" in errors[2]
    assert "не указан инструмент" in errors[3]
    assert "result_step=9" in errors[4]


def test_planner_retries_once_with_validation_errors() -> None:
    class ScriptedLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def complete_structured(self, system, user, schema):
            self.prompts.append(user)
            tool = "bad_tool" if len(self.prompts) == 1 else "aggregate"
            return ResearchPlan(steps=[ResearchStep(tool=tool, arguments={"column": "region"})])

    llm = ScriptedLLM()
    plan = asyncio.run(StepPlanner(llm=llm).plan("вопрос", {"columns": ["region"]}))
    assert plan.steps[0].tool == "aggregate"
    assert len(llm.prompts) == 2
    assert "bad_tool" in llm.prompts[1]
    # Модель видит, что именно она предложила, а не строит план с нуля.
    assert '"tool": "bad_tool"' in llm.prompts[1]


class PlansLLM:
    """Отдаёт планы по очереди и запоминает промпты."""

    def __init__(self, *tools: str) -> None:
        self.tools = list(tools)
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema):
        self.prompts.append(user)
        tool = self.tools.pop(0)
        return ResearchPlan(steps=[ResearchStep(tool=tool, arguments={"column": "region"})])


def test_planner_rejects_a_plan_still_invalid_after_the_retry() -> None:
    llm = PlansLLM("bad_tool", "worse_tool")
    with pytest.raises(PlanInvalid) as exc:
        asyncio.run(StepPlanner(llm=llm).plan("вопрос", {"columns": ["region"]}))
    assert len(llm.prompts) == 2
    assert exc.value.errors == ["шаг 0: неизвестный инструмент 'worse_tool'"]


class EmptyFirstLLM:
    """Сначала пустые планы (empty штук), потом план из одного шага."""

    def __init__(self, empty: int) -> None:
        self.empty = empty
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema):
        self.prompts.append(user)
        if len(self.prompts) <= self.empty:
            return ResearchPlan(steps=[])
        return ResearchPlan(steps=[ResearchStep(tool="aggregate", arguments={"column": "region"})])


def test_planner_asks_again_after_an_empty_plan() -> None:
    llm = EmptyFirstLLM(empty=1)
    plan = asyncio.run(StepPlanner(llm=llm).plan("вопрос", {"columns": ["region"]}))
    assert [step.tool for step in plan.steps] == ["aggregate"]
    assert len(llm.prompts) == 2
    assert "предыдущий план пустой" in llm.prompts[1]


def test_planner_keeps_a_second_empty_plan() -> None:
    # Вопрос не о данных: второй пустой план — честный ответ, дальше не спрашиваем.
    llm = EmptyFirstLLM(empty=2)
    plan = asyncio.run(StepPlanner(llm=llm).plan("вопрос", {"columns": ["region"]}))
    assert plan.steps == []
    assert len(llm.prompts) == 2


def test_planner_continuation_does_not_ask_again_after_an_empty_plan() -> None:
    llm = EmptyFirstLLM(empty=1)
    context = {"columns": ["region"], "next_step_id": 2}
    plan = asyncio.run(StepPlanner(llm=llm).plan("вопрос", context))
    assert plan.steps == []
    assert len(llm.prompts) == 1


def test_planner_checks_the_retry_and_returns_it_when_valid() -> None:
    llm = PlansLLM("bad_tool", "aggregate")
    plan = asyncio.run(StepPlanner(llm=llm).plan("вопрос", {"columns": ["region"]}))
    assert [step.tool for step in plan.steps] == ["aggregate"]
    assert len(llm.prompts) == 2


# --- оркестратор: $prev, analysis, result_step, lookup ---


class FakeMemory:
    turn_id = "turn-1"

    def __init__(self) -> None:
        self.clarifications: list[dict] = []
        self.steps: list[int] = []
        self.pending: dict | None = None
        self.checkpoint: dict | None = None

    def record_step(self, step, result_table=None) -> None:
        self.steps.append(step.step_id)

    def save_checkpoint(self, checkpoint: dict) -> None:
        self.checkpoint = checkpoint

    def set_pending_recovery(self, data: dict) -> None:
        self.pending = data

    def build_context(self, question: str) -> dict:
        return {"columns": [], "turns": []}


class ScriptedPlanner:
    def __init__(self, *plans: ResearchPlan) -> None:
        self.plans = list(plans)
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        return self.plans.pop(0)

    async def fix_step(self, step: ResearchStep, error: str, *_: object) -> ResearchStep:
        raise ValueError(f"шаг {step.step_id} не исправлен")


def orchestrator(db: DuckDBManager, planner=None) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=planner or ScriptedPlanner(),
        executor=StepExecutor(db=db),
        insight_gen=object(),
        db=db,
    )


def run_plan(agent: AgentOrchestrator, memory: FakeMemory, plan: ResearchPlan):
    return asyncio.run(agent._execute_plan(memory, plan, "raw", "вопрос"))


def test_analysis_step_does_not_replace_the_pipeline(db: DuckDBManager) -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="filter_rows",
                arguments={"column": "region", "operator": "IN", "value": ["msk", "spb"]},
            ),
            ResearchStep(
                step_id=1,
                tool="t_test_ind",
                purpose="Сравнить регионы",
                arguments={"column_a": "amount", "column_b": "region"},
            ),
            ResearchStep(
                step_id=2,
                tool="group_by",
                arguments={"by": "region", "column": "amount", "function": "SUM"},
            ),
        ]
    )
    memory = FakeMemory()
    execution = run_plan(orchestrator(db), memory, plan)
    result = db.to_records(execution.result_table)
    assert {row["region"] for row in result} == {"msk", "spb"}
    assert [purpose for purpose, _ in execution.supporting] == ["Сравнить регионы"]
    assert "p_value" in db.to_records(execution.supporting[0][1])[0]


def second_metric_plan(column: str) -> ResearchPlan:
    """Средняя сумма, затем подсчёт по column — без "input": "$source", как пишет модель."""
    return ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="group_by",
                arguments={"by": [], "column": "amount", "function": "AVG"},
            ),
            ResearchStep(
                step_id=1,
                tool="group_by",
                arguments={"by": column, "column": "id", "function": "COUNT"},
            ),
        ]
    )


def test_step_missing_a_source_column_reruns_on_the_source(db: DuckDBManager) -> None:
    memory = FakeMemory()
    execution = run_plan(orchestrator(db), memory, second_metric_plan("region"))
    assert {row["region"] for row in db.to_records(execution.result_table)} == {
        "msk",
        "spb",
        "kzn",
    }
    assert execution.steps[1].input == "$source"
    assert memory.steps == [0, 1]


def test_step_missing_a_column_everywhere_still_asks_the_user(db: DuckDBManager) -> None:
    memory = FakeMemory()
    with pytest.raises(ClarificationNeeded):
        run_plan(orchestrator(db), memory, second_metric_plan("status"))
    assert memory.pending["plan_steps"][1]["input"] == "$prev"
    assert memory.pending["attempts_made"] == 1


def test_step_failing_on_the_source_too_asks_the_user(db: DuckDBManager) -> None:
    class SourceFails(StepExecutor):
        async def execute(self, step, input_table, **kwargs):
            if step.input == "$source":
                raise ValueError("источник тоже не подошёл")
            return await super().execute(step, input_table, **kwargs)

    agent = orchestrator(db)
    agent.executor = SourceFails(db=db)
    memory = FakeMemory()
    with pytest.raises(ClarificationNeeded) as exc:
        run_plan(agent, memory, second_metric_plan("region"))
    assert exc.value.error == "источник тоже не подошёл"
    assert memory.pending["plan_steps"][1]["input"] == "$source"
    assert memory.pending["attempts_made"] == 2


def test_result_step_selects_the_answer_table(db: DuckDBManager) -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(step_id=0, tool="aggregate", arguments={"column": "region"}),
            ResearchStep(step_id=1, tool="sort_limit", arguments={"order_by": "cnt", "limit": 1}),
        ],
        result_step=0,
    )
    execution = run_plan(orchestrator(db), FakeMemory(), plan)
    assert len(db.to_records(execution.result_table)) == 3


def test_lookup_step_triggers_replanning_with_observation(db: DuckDBManager) -> None:
    replanned = ResearchPlan(
        steps=[
            ResearchStep(
                tool="filter_rows", input="$source", arguments={"column": "region", "value": "spb"}
            ),
        ]
    )
    planner = ScriptedPlanner(replanned)
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="search_categorical_values",
                arguments={"column": "region", "query": "SPB"},
            ),
            ResearchStep(
                step_id=1, tool="filter_rows", arguments={"column": "region", "value": "?"}
            ),
        ]
    )
    memory = FakeMemory()
    events: list[dict] = []
    agent = orchestrator(db, planner)
    execution = asyncio.run(agent._execute_plan(memory, plan, "raw", "вопрос", emit=events.append))
    # Достроенный план приходит целиком: выполненный lookup и новые шаги (#239).
    plans = [e["data"] for e in events if e["data"]["stage"] == "planning"]
    assert [[step["step_id"] for step in p["steps"]] for p in plans] == [[0, 1]]
    assert planner.contexts[0]["next_step_id"] == 1
    assert "spb" in planner.contexts[0]["executed"]
    assert memory.steps == [0, 1]
    assert [row["id"] for row in db.to_records(execution.result_table)] == [3, 4]


def test_finished_step_shows_operation_input_and_result(db: DuckDBManager) -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="filter_rows",
                arguments={"column": "region", "operator": "IN", "value": ["msk", "spb"]},
            ),
            ResearchStep(
                step_id=1,
                tool="group_by",
                arguments={"by": "region", "column": "amount", "function": "SUM"},
            ),
            ResearchStep(
                step_id=2,
                tool="t_test_ind",
                input="$step_0",
                arguments={"column_a": "amount", "column_b": "region"},
            ),
        ]
    )
    events: list[dict] = []
    agent = orchestrator(db)
    asyncio.run(agent._execute_plan(FakeMemory(), plan, "raw", "вопрос", emit=events.append))
    for event in events:
        ProgressPayload(**event["data"])
    finished = {
        e["data"]["step_id"]: e["data"] for e in events if e["data"]["status"] == "finished"
    }

    first = finished[0]
    assert first["arguments"] == {"column": "region", "operator": "IN", "value": ["msk", "spb"]}
    assert (first["input"], first["input_table"]) == ("$prev", "raw")
    assert "input_step_id" not in first
    assert first["code_language"] == "sql"
    assert "WHERE" in first["code"]
    assert first["rows"] == 4
    assert {row["region"] for row in first["preview"]} == {"msk", "spb"}

    second = finished[1]
    assert second["input_table"] == first["result_table"]
    assert second["input_step_id"] == 0
    assert second["rows"] == 2
    assert second["result_columns"][0]["name"] == "region"

    # Статистика посчитана в Python: кода нет, а результат и вход — есть.
    third = finished[2]
    assert (third["input"], third["input_step_id"]) == ("$step_0", 0)
    assert "code" not in third
    assert "p_value" in third["preview"][0]


def test_unreadable_step_result_keeps_the_turn_going(db: DuckDBManager) -> None:
    class GhostExecutor:
        db = None

        async def execute(self, step, input_table, **_):
            return "temp_result_v404"

    agent = orchestrator(db)
    agent.executor = GhostExecutor()
    events: list[dict] = []
    plan = ResearchPlan(steps=[ResearchStep(step_id=0, tool="aggregate")])
    execution = asyncio.run(
        agent._execute_plan(FakeMemory(), plan, "raw", "вопрос", emit=events.append)
    )
    assert execution.result_table == "temp_result_v404"
    finished = events[-1]["data"]
    assert finished["result_table"] == "temp_result_v404"
    assert "preview" not in finished


def test_sandbox_error_asks_the_user_without_fixing_the_step(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch, codegen_available
) -> None:
    async def failing_codegen(*args, **kwargs):
        raise SandboxError("код упал в песочнице")

    monkeypatch.setattr(codegen_runner, "run_generated_code", failing_codegen)
    step = ResearchStep(
        step_id=0, tool="run_python", arguments={"purpose": "Медиана по окну 7 дней с весами"}
    )
    memory = FakeMemory()
    with pytest.raises(ClarificationNeeded) as exc:
        run_plan(orchestrator(db), memory, ResearchPlan(steps=[step]))
    assert exc.value.suggestions == UNSUPPORTED_SUGGESTIONS
    assert "песочнице" in memory.pending["error"]
    assert memory.pending["attempts_made"] == 1


def test_skip_step_resumes_with_saved_step_tables(db: DuckDBManager) -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0, tool="filter_rows", arguments={"column": "region", "value": "msk"}
            ),
            ResearchStep(step_id=1, tool="aggregate", arguments={"column": "no_such_column"}),
            ResearchStep(
                step_id=2, tool="describe_stats", input="$step_0", arguments={"column": "amount"}
            ),
        ]
    )
    memory = FakeMemory()
    agent = orchestrator(db)
    with pytest.raises(ClarificationNeeded):
        run_plan(agent, memory, plan)
    pending = memory.pending
    assert pending["failed_step_id"] == 1

    class Request:
        question = "вопрос"
        recovery_response = '{"action": "skip_step"}'

    execution = asyncio.run(agent._apply_recovery(memory, pending, Request()))
    row = db.to_records(execution.result_table)[0]
    assert row["n"] == 2
    assert row["mean"] == 15.0


class CountingRuns(StepExecutor):
    def __init__(self, db: DuckDBManager) -> None:
        super().__init__(db=db)
        self.executed: list[int] = []

    async def execute(self, step: ResearchStep, *args: object, **kwargs: object) -> str:
        self.executed.append(step.step_id)
        return await super().execute(step, *args, **kwargs)


def resumable_plan() -> ResearchPlan:
    return ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0, tool="filter_rows", arguments={"column": "region", "value": "msk"}
            ),
            ResearchStep(step_id=1, tool="describe_stats", arguments={"column": "amount"}),
        ]
    )


def stop_after_first_step(db: DuckDBManager) -> dict:
    """Ход, остановленный после первого шага: чекпоинт, какой остался в памяти."""
    memory = FakeMemory()
    agent = orchestrator(db)
    plan = resumable_plan()
    run_plan(agent, memory, ResearchPlan(steps=plan.steps[:1]))
    checkpoint = memory.checkpoint
    checkpoint["plan"] = plan.model_dump(mode="json")
    return checkpoint


def resume(db: DuckDBManager, checkpoint: dict) -> tuple[CountingRuns, list[dict], object]:
    executor = CountingRuns(db)
    agent = AgentOrchestrator(
        planner=ScriptedPlanner(), executor=executor, insight_gen=object(), db=db
    )
    events: list[dict] = []

    class Request:
        question = "не используется"

    memory = FakeMemory()
    memory.question = "вопрос"
    question, execution = asyncio.run(
        agent._research(memory, None, Request(), events.append, resume=checkpoint)
    )
    assert question == "вопрос"
    return executor, events, execution


def test_execute_plan_saves_a_checkpoint_after_each_step(db: DuckDBManager) -> None:
    memory = FakeMemory()
    run_plan(orchestrator(db), memory, resumable_plan())
    checkpoint = memory.checkpoint
    assert checkpoint["kind"] == "research"
    assert checkpoint["input_table"] == checkpoint["source_table"] == "raw"
    assert set(checkpoint["step_tables"]) == {"0", "1"}
    assert [step["step_id"] for step in checkpoint["plan"]["steps"]] == [0, 1]


def test_resume_skips_steps_done_before_the_stop(db: DuckDBManager) -> None:
    checkpoint = stop_after_first_step(db)
    executor, events, execution = resume(db, checkpoint)
    # Первый шаг не выполняется заново, но фронт видит его готовым.
    assert executor.executed == [1]
    # План приходит снова: фронт продолжает ход в новом сообщении (#239).
    plan, *steps = events
    assert [step["step_id"] for step in plan["data"]["steps"]] == [0, 1]
    assert [(e["data"]["step_id"], e["data"]["status"]) for e in steps] == [
        (0, "finished"),
        (1, "started"),
        (1, "finished"),
    ]
    assert steps[0]["data"]["result_table"] == checkpoint["step_tables"]["0"]
    row = db.to_records(execution.result_table)[0]
    assert row["n"] == 2
    assert row["mean"] == 15.0


def test_resume_after_all_steps_only_picks_the_answer(db: DuckDBManager) -> None:
    memory = FakeMemory()
    run_plan(orchestrator(db), memory, resumable_plan())
    executor, _, execution = resume(db, memory.checkpoint)
    assert executor.executed == []
    # Последний шаг — стат-тест: $prev он не меняет, но ответ — его таблица.
    assert execution.result_table == memory.checkpoint["step_tables"]["1"]


def test_resume_reruns_a_step_whose_table_was_cleaned_up(db: DuckDBManager) -> None:
    checkpoint = stop_after_first_step(db)
    db.execute(f"DROP TABLE {checkpoint['step_tables']['0']}")
    executor, _, execution = resume(db, checkpoint)
    assert executor.executed == [0, 1]
    assert db.to_records(execution.result_table)[0]["n"] == 2


def test_result_step_ahead_of_trailing_lookup_is_dropped_without_retry() -> None:
    """Модель планирует разведку и заранее указывает result_step будущего шага."""
    plan = ResearchPlan(
        steps=[ResearchStep(step_id=0, tool="inspect_temp_table", arguments={"table": "$source"})],
        result_step=2,
    )
    assert validate_plan(plan) == []
    assert plan.result_step is None


def test_finish_falls_back_to_the_input_table_without_executed_steps(db: DuckDBManager) -> None:
    execution = orchestrator(db)._finish(ResearchPlan(steps=[]), [], {}, fallback="raw")
    assert execution.result_table == "raw"


@pytest.mark.parametrize("response", ["[1, 2]", ""])
def test_recovery_without_action_replans_and_rejects_an_empty_plan(
    db: DuckDBManager, response: str
) -> None:
    planner = ScriptedPlanner(ResearchPlan(steps=[]))
    pending = {
        "question": "исходный вопрос",
        "failed_step_id": 0,
        "plan_steps": [ResearchStep(step_id=0, tool="aggregate").model_dump()],
        "input_table": "raw",
        "error": "ошибка",
    }

    class Request:
        question = "уточнение"
        recovery_response = response

    with pytest.raises(ClarificationNeeded):
        asyncio.run(orchestrator(db, planner)._apply_recovery(FakeMemory(), pending, Request()))
    assert len(planner.contexts) == 1


class InvalidPlanner(ScriptedPlanner):
    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        raise PlanInvalid(["шаг 0: неизвестный инструмент 'bad_tool'"])


class CountingExecutor(StepExecutor):
    def __init__(self, db: DuckDBManager) -> None:
        super().__init__(db=db)
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return await super().execute(*args, **kwargs)


def invalid_plan_agent(db: DuckDBManager) -> tuple[AgentOrchestrator, CountingExecutor]:
    executor = CountingExecutor(db)
    agent = AgentOrchestrator(
        planner=InvalidPlanner(), executor=executor, insight_gen=object(), db=db
    )
    return agent, executor


def assert_plan_clarification(exc: pytest.ExceptionInfo) -> None:
    assert exc.value.step_id == -1
    assert "bad_tool" in exc.value.error
    assert exc.value.suggestions == ["Переформулировать вопрос"]


def test_invalid_plan_asks_to_rephrase_without_executing(db: DuckDBManager) -> None:
    agent, executor = invalid_plan_agent(db)

    class Memory(FakeMemory):
        table = "raw"

    class Request:
        question = "вопрос"
        recovery_response = None

    events: list[dict] = []
    with pytest.raises(ClarificationNeeded) as exc:
        asyncio.run(agent._research(Memory(), None, Request(), events.append))
    assert_plan_clarification(exc)
    assert executor.calls == 0
    assert events[-1]["data"]["status"] == "failed"


def test_invalid_replan_after_lookup_asks_to_rephrase(db: DuckDBManager) -> None:
    agent, executor = invalid_plan_agent(db)
    lookup = ResearchStep(
        step_id=0, tool="search_categorical_values", arguments={"column": "region", "query": "SPB"}
    )
    with pytest.raises(ClarificationNeeded) as exc:
        run_plan(agent, FakeMemory(), ResearchPlan(steps=[lookup]))
    assert_plan_clarification(exc)
    assert executor.calls == 1


def test_invalid_plan_after_recovery_text_asks_to_rephrase(db: DuckDBManager) -> None:
    agent, executor = invalid_plan_agent(db)
    pending = {
        "question": "исходный вопрос",
        "failed_step_id": 0,
        "plan_steps": [ResearchStep(step_id=0, tool="aggregate").model_dump()],
        "input_table": "raw",
        "error": "ошибка",
    }

    class Request:
        question = "уточнение"
        recovery_response = "считай по region"

    with pytest.raises(ClarificationNeeded) as exc:
        asyncio.run(agent._apply_recovery(FakeMemory(), pending, Request()))
    assert_plan_clarification(exc)
    assert executor.calls == 0
