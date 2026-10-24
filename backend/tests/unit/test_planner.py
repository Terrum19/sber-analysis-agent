"""Unit-тесты StepPlanner: генерация плана исследования."""

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


def test_reply_prompt_gives_plan_of_chosen_turn_as_draft():
    from app.core.agent.planner import describe_history

    first = {
        "question": "Сколько событий с суммой больше 150?",
        "steps": [
            {
                "step_id": 0,
                "tool": "filter_rows",
                "input": "$prev",
                "arguments": {"column": "amount", "operator": ">", "value": 150},
                "columns": [],
                "purpose": "Сумма больше 150",
            }
        ],
        "headline": "471",
        "result_columns": ["event_id", "amount"],
    }
    history = describe_history({"reply": [first], "turns": []})
    assert "Черновик плана" in history
    assert "- Сколько событий с суммой больше 150?" in history
    assert '"operator": ">", "value": 150' in history
    assert "Ответ на него: 471" in history
    assert "Прошлые ходы" not in history
    assert "Прошлые ходы" in describe_history({"turns": []})
