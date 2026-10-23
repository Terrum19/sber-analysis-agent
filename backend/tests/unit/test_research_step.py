"""Unit-тесты моделей ResearchStep и ResearchPlan."""

import pytest
from pydantic import ValidationError

from app.models.research_step import ResearchPlan, ResearchStep


def test_research_step_minimal() -> None:
    """Минимальный шаг с обязательными полями."""
    step = ResearchStep(step_id=0, tool=None)
    assert step.step_id == 0
    assert step.tool is None
    assert step.columns == []
    assert step.purpose == ""


def test_research_step_full() -> None:
    """Полный шаг со всеми полями."""
    step = ResearchStep(
        step_id=1,
        tool="aggregate",
        columns=["category", "value"],
        purpose="Сгруппировать и посчитать",
    )
    assert step.step_id == 1
    assert step.tool == "aggregate"
    assert step.columns == ["category", "value"]
    assert step.purpose == "Сгруппировать и посчитать"


def test_research_plan_empty() -> None:
    """Пустой план исследования."""
    plan = ResearchPlan(steps=[])
    assert plan.steps == []
    assert plan.final_viz == "auto"
    assert plan.dashboard_requested is False


def test_research_plan_with_steps() -> None:
    """План с шагами."""
    plan = ResearchPlan(
        steps=[
            ResearchStep(step_id=0, tool="aggregate", columns=["x"]),
            ResearchStep(step_id=1, tool="time_series", columns=["t", "y"]),
        ],
        final_viz="line",
        dashboard_requested=True,
    )
    assert len(plan.steps) == 2
    assert plan.final_viz == "line"
    assert plan.dashboard_requested is True


def test_research_step_validation_error() -> None:
    """Проверка валидации Pydantic."""
    with pytest.raises(ValidationError):
        ResearchStep(step_id="not an int")


def test_research_plan_null_fields_fall_back_to_defaults() -> None:
    """LLM присылает null вместо final_viz и dashboard_requested."""
    plan = ResearchPlan.model_validate_json(
        '{"steps": [{"tool": "inspect_temp_table", "arguments": {}}], '
        '"final_viz": null, "dashboard_requested": null, "result_step": null}'
    )
    assert plan.final_viz == "auto"
    assert plan.dashboard_requested is False
    assert plan.result_step is None
