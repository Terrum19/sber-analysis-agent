"""Unit-тесты StepPlanner: генерация плана исследования."""

import json

import pytest

from app.core.agent.planner import StepPlanner, validate_plan
from app.core.llm.fake_client import FakeLLM
from app.models.research_step import ResearchPlan, ResearchStep


@pytest.fixture
def planner():
    return StepPlanner(llm=FakeLLM())


def test_plan_returns_research_plan(planner):
    import asyncio

    context = {
        "columns": ["category", "revenue", "date"],
        "columns_summary": "category, revenue, date",
        "datasource": "raw",
        "summary": "",
        "messages": [],
        "question": "Сколько категорий?",
        "profile": {},
    }
    plan = asyncio.run(planner.plan("Сколько категорий?", context))
    assert isinstance(plan, ResearchPlan)
    assert len(plan.steps) >= 1
    assert plan.final_viz in {"bar", "auto", "line"}


def test_plan_empty_question(planner):
    import asyncio

    context = {
        "columns": [],
        "columns_summary": "неизвестно",
        "datasource": "raw",
        "summary": "",
        "messages": [],
        "question": "",
        "profile": {},
    }
    plan = asyncio.run(planner.plan("", context))
    assert isinstance(plan, ResearchPlan)


def test_plan_includes_columns_in_context(planner):
    import asyncio

    context = {
        "columns": ["x", "y", "z"],
        "columns_summary": "x, y, z",
        "datasource": "raw",
        "summary": "",
        "messages": [],
        "question": "Тест колонок",
        "profile": {},
    }
    plan = asyncio.run(planner.plan("Тест колонок", context))
    assert isinstance(plan, ResearchPlan)


def test_step_without_tool_is_always_invalid(codegen_available):
    plan = ResearchPlan(steps=[ResearchStep(step_id=0, tool=None, purpose="что-нибудь посчитать")])
    assert validate_plan(plan) == ["шаг 0: не указан инструмент"]


def test_run_python_step_is_valid_only_when_the_tool_is_available(monkeypatch):
    from app.config import settings

    step = ResearchStep(
        step_id=0,
        tool="run_python",
        arguments={"purpose": "Медиана выручки по скользящему окну 7 дней с весами"},
    )
    plan = ResearchPlan(steps=[step])
    monkeypatch.setattr(settings, "codegen_enabled", True)
    monkeypatch.setattr("app.core.tools.codegen.sandbox_available", lambda: True)
    assert validate_plan(plan) == []
    monkeypatch.setattr(settings, "codegen_enabled", False)
    assert validate_plan(plan) == ["шаг 0: неизвестный инструмент 'run_python'"]


def test_prompt_shows_previous_turns_with_plan_arguments():
    import asyncio

    class PromptSpy(FakeLLM):
        async def complete_structured(self, system, user, schema):
            self.prompt = user
            return await super().complete_structured(system, user, schema)

    llm = PromptSpy()
    turn = {
        "question": "Выручка по месяцам",
        "steps": [
            {
                "step_id": 0,
                "tool": "time_bucket",
                "input": "$source",
                "arguments": {"date_column": "date", "column": "revenue", "function": "SUM"},
                "columns": [],
                "purpose": "Выручка по месяцам",
            }
        ],
        "headline": "Пик в июле",
        "result_columns": ["bucket", "sum_revenue"],
    }
    context = {"columns": ["date", "revenue"], "turns": [turn]}
    asyncio.run(StepPlanner(llm=llm).plan("А теперь только для Одежды", context))
    assert "1. Вопрос: Выручка по месяцам" in llm.prompt
    assert '"date_column": "date"' in llm.prompt
    assert '"columns"' not in llm.prompt.split("Прошлые ходы")[1].split("Уже выполненные")[0]
    assert "Ответ: Пик в июле" in llm.prompt
    assert "Колонки результата: bucket, sum_revenue" in llm.prompt


def test_prompt_without_previous_turns_says_none():
    from app.core.agent.planner import describe_turns

    assert describe_turns([]) == "нет"


def _turn(steps: list[dict], tables: list[str]) -> dict:
    return {"question": "Выручка по регионам", "tables": tables, "steps": steps}


def test_previous_turn_on_other_table_is_marked():
    from app.core.agent.planner import describe_turns

    step = {"step_id": 0, "tool": "group_by", "input": "$source", "arguments": {"by": ["region"]}}
    same = describe_turns([_turn([step], ["sales"])], "sales")
    other = describe_turns([_turn([step], ["sales"])], "events")
    assert "Таблица: sales\n" in same
    assert "Таблица: sales (другая таблица" in other


def test_previous_plan_is_renumbered_from_zero_with_refs():
    from app.core.agent.planner import describe_turns

    # План хода после уточнения: шаги 3 и 4, шаг 4 ссылается на 3.
    steps = [
        {"step_id": 3, "tool": "filter_rows", "input": "$source", "arguments": {"column": "x"}},
        {
            "step_id": 4,
            "tool": "join",
            "input": "step_3",
            "arguments": {"right": "$step_3", "on": ["id"]},
        },
    ]
    plan = json.loads(describe_turns([_turn(steps, [])]).split("План: ")[1].split("\n")[0])
    assert [step["step_id"] for step in plan] == [0, 1]
    assert plan[1]["input"] == "step_0"
    assert plan[1]["arguments"]["right"] == "$step_0"
