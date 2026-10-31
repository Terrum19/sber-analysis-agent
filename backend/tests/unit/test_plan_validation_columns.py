"""Preflight плана: лимит шагов, колонки и подсказка для динамики."""

from app.config import settings
from app.core.agent.planner import validate_plan
from app.models.research_step import ResearchPlan, ResearchStep


def test_columns_are_checked_before_execution() -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="group_by",
                input="$source",
                arguments={"by": ["region"], "column": "revenu", "function": "SUM"},
            )
        ]
    )
    errors = validate_plan(plan, columns={"region", "revenue"})
    assert len(errors) == 1
    assert "revenu" in errors[0]
    assert "revenue" in errors[0]


def test_column_preflight_skips_steps_over_previous_transform() -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="create_column",
                input="$source",
                arguments={"new_column": "revenue", "expression": "price * qty"},
            ),
            ResearchStep(
                step_id=1,
                tool="group_by",
                input="$prev",
                arguments={"by": [], "column": "revenue", "function": "SUM"},
            ),
        ]
    )
    # revenue ещё нет в исходной таблице, но шаг 1 читает результат шага 0.
    assert validate_plan(plan, columns={"price", "qty"}) == []


def test_max_steps_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_max_steps", 2)
    plan = ResearchPlan(
        steps=[
            ResearchStep(step_id=index, tool="aggregate", arguments={"column": "a"})
            for index in range(3)
        ]
    )
    assert any("максимум 2" in error for error in validate_plan(plan, columns={"a"}))


def test_time_hint_requires_a_date_column_or_time_step() -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="revenue_analysis",
                arguments={"revenue_column": "revenue"},
            )
        ]
    )
    errors = validate_plan(plan, columns={"revenue", "order_date"}, question="Выручка по месяцам")
    assert any("date_column" in error for error in errors)


def test_time_hint_is_satisfied_by_time_bucket() -> None:
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="time_bucket",
                arguments={
                    "date_column": "order_date",
                    "metric_column": "revenue",
                    "function": "SUM",
                },
            )
        ]
    )
    assert (
        validate_plan(plan, columns={"revenue", "order_date"}, question="Выручка по месяцам") == []
    )
