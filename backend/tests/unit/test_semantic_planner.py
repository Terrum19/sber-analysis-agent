"""Semantic planner/compiler: вопрос -> метрики -> план/SQL."""

from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.semantic_compiler import SemanticCompiler
from app.core.data.semantic_planner import SemanticPlanner
from app.models.semantic import SemanticDimension, SemanticMetric, SemanticModel


def model() -> SemanticModel:
    return SemanticModel(
        table="sales",
        metrics=[
            SemanticMetric(
                name="revenue",
                title="Выручка",
                aggregation="SUM",
                column="amount",
                synonyms=["выручка", "доход"],
            )
        ],
        dimensions=[
            SemanticDimension(
                name="month",
                column="event_date",
                kind="temporal",
                synonyms=["месяц", "по месяцам"],
                time_grains=["month"],
            ),
            SemanticDimension(name="region", column="region", kind="categorical"),
        ],
        default_time_dimension="month",
    )


def test_maps_metric_and_time_grain() -> None:
    intent = SemanticPlanner().map("Выручка по месяцам", model())
    assert [metric.name for metric in intent.metrics] == ["revenue"]
    assert intent.time_grain == "month"
    assert any(dimension.name == "month" for dimension in intent.dimensions)


def test_no_metric_means_no_guess() -> None:
    intent = SemanticPlanner().map("Покажи что-нибудь интересное", model())
    assert intent.metrics == []


def test_compiler_builds_time_bucket_plan() -> None:
    intent = SemanticPlanner().map("Выручка по месяцам", model())
    plan = SemanticCompiler().compile(intent, model())
    assert plan.final_viz == "line"
    step = plan.steps[0]
    assert step.tool == "time_bucket"
    assert step.arguments == {
        "date_column": "event_date",
        "interval": "month",
        "function": "SUM",
        "by": [],
        "metric_column": "amount",
    }


def test_compiler_builds_kpi_plan() -> None:
    intent = SemanticPlanner().map("Общая выручка", model())
    plan = SemanticCompiler().compile(intent, model())
    assert plan.final_viz == "kpi"
    assert plan.steps[0].tool == "group_by"
    assert plan.steps[0].arguments["metrics"][0]["function"] == "SUM"


def test_step_planner_uses_semantic_layer_before_llm(monkeypatch) -> None:
    import asyncio

    from app.config import settings
    from app.core.agent.planner import StepPlanner

    class NoLLM:
        async def complete_structured(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("semantic layer не должен звать LLM")

    monkeypatch.setattr(settings, "agent_semantic_layer", True)
    plan = asyncio.run(
        StepPlanner(llm=NoLLM()).plan(
            "Выручка по месяцам",
            {
                "columns": ["event_date", "amount", "region"],
                "semantic_model": model(),
            },
        )
    )
    assert plan.steps[0].tool == "time_bucket"
    assert plan.steps[0].arguments["metric_column"] == "amount"


def test_compiler_sql_is_valid_for_duckdb() -> None:
    db = DuckDBManager(":memory:")
    db.execute(
        "CREATE TABLE sales AS SELECT DATE '2024-01-01' AS event_date, "
        "'msk' AS region, 10.0 AS amount"
    )
    intent = SemanticPlanner().map("Выручка по месяцам", model())
    sql = SemanticCompiler().to_sql(intent, model())
    rows = db.query(sql)
    assert rows[0]["revenue"] == 10.0
    db.close()
