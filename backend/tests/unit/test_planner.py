"""Unit-тесты StepPlanner: генерация плана исследования."""

import pytest

from app.core.agent.planner import StepPlanner
from app.core.llm.fake_client import FakeLLM
from app.models.research_step import ResearchPlan


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
