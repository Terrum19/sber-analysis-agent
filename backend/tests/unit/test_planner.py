"""Unit-тесты StepPlanner: генерация плана исследования."""

import pytest

from app.core.agent.planner import PlanInvalid, StepPlanner, apply_template, validate_plan
from app.core.dashboards.templates import match
from app.core.llm.fake_client import FakeLLM
from app.core.llm.prompts.step_planner import NO_TILES
from app.models.research_step import DashboardTilePlan, ResearchPlan, ResearchStep

SALES_COLUMNS = ["order_date", "order_id", "product", "quantity", "price"]


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


class ScriptedLLM(FakeLLM):
    def __init__(self, *plans: ResearchPlan) -> None:
        super().__init__()
        self.plans = list(plans)
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema):
        if schema is ResearchPlan:
            self.prompts.append(user)
            return self.plans.pop(0).model_copy(deep=True)
        return await super().complete_structured(system, user, schema)


def total(step_id: int, column: str, by: list[str] | None = None) -> ResearchStep:
    return ResearchStep(
        step_id=step_id,
        tool="group_by",
        input="$source",
        arguments={"by": by or [], "column": column, "function": "SUM"},
        purpose=f"Сумма {column}",
    )


def tile(title: str, step_id: int, **fields) -> DashboardTilePlan:
    return DashboardTilePlan(title=title, step_id=step_id, **fields)


def dashboard_plan(*tiles: DashboardTilePlan) -> ResearchPlan:
    return ResearchPlan(
        steps=[
            total(0, "revenue", ["category"]),
            total(1, "revenue"),
            total(2, "revenue", ["date"]),
        ],
        dashboard_requested=True,
        dashboard_tiles=list(tiles),
    )


def test_tiles_on_existing_steps_are_valid():
    plan = dashboard_plan(tile("По категориям", 0), tile("Выручка", 1, is_kpi=True))
    assert validate_plan(plan) == []


def test_tile_on_missing_step_is_an_error():
    plan = dashboard_plan(tile("По категориям", 0), tile("Призрак", 7))
    assert validate_plan(plan) == ["плитка «Призрак»: step_id=7 — такого шага нет"]


def test_tile_may_reference_a_step_executed_before_the_replan():
    plan = ResearchPlan(steps=[total(3, "revenue")], dashboard_tiles=[tile("Ранний шаг", 1)])
    assert validate_plan(plan, known_steps=3) == []


def test_requested_dashboard_without_tiles_is_an_error():
    assert validate_plan(dashboard_plan()) == [NO_TILES]


def test_tiles_mark_the_plan_as_a_dashboard():
    plan = ResearchPlan(steps=[total(0, "revenue")], dashboard_tiles=[tile("Выручка", 0)])
    assert plan.dashboard_requested is True


def test_null_tiles_from_the_llm_mean_no_tiles():
    plan = ResearchPlan.model_validate_json(
        '{"steps": [], "dashboard_tiles": null, "dashboard_requested": null}'
    )
    assert plan.dashboard_tiles == []
    assert plan.dashboard_requested is False


def test_plan_ending_with_lookup_drops_tiles_on_future_steps():
    lookup = ResearchStep(
        step_id=1,
        tool="search_categorical_values",
        arguments={"column": "category", "query": "электро"},
    )
    plan = ResearchPlan(
        steps=[total(0, "revenue"), lookup],
        dashboard_tiles=[tile("Выручка", 0), tile("Будущий шаг", 5)],
    )
    assert validate_plan(plan) == []
    assert [planned.step_id for planned in plan.dashboard_tiles] == [0]


def test_question_without_dashboard_words_gets_no_dashboard_prompt():
    import asyncio

    llm = ScriptedLLM(ResearchPlan(steps=[total(0, "revenue")]))
    plan = asyncio.run(StepPlanner(llm=llm).plan("Выручка по категориям", {"columns": ["revenue"]}))
    assert "dashboard_tiles" not in llm.prompts[0]
    assert plan.template_id is None


def test_dashboard_without_template_asks_for_three_to_six_tiles():
    import asyncio

    llm = ScriptedLLM(dashboard_plan(tile("Выручка", 1, is_kpi=True), tile("По датам", 2)))
    context = {"columns": ["category", "revenue", "date"]}
    plan = asyncio.run(StepPlanner(llm=llm).plan("Сделай дашборд по выручке", context))
    assert "от 3 до 6 плиток" in llm.prompts[0]
    assert plan.template_id is None
    assert [planned.step_id for planned in plan.dashboard_tiles] == [1, 2]


def test_matching_columns_set_template_id_and_template_tiles():
    import asyncio

    reply = ResearchPlan(
        steps=[
            total(0, "quantity", ["order_date"]),
            total(1, "quantity", ["product"]),
            total(2, "quantity"),
            total(3, "price"),
        ],
        dashboard_requested=True,
        dashboard_tiles=[
            tile("выручка по кварталам", 0),
            tile("Самые продаваемые товары", 1),
            tile("Выручка", 2, chart_type="bar"),
            tile("Средний чек заказа", 3),
        ],
    )
    llm = ScriptedLLM(reply)
    context = {"columns": SALES_COLUMNS}
    plan = asyncio.run(
        StepPlanner(llm=llm).plan("Сделай дашборд с ежеквартальной статистикой продаж", context)
    )
    assert "Продажи по кварталам" in llm.prompts[0]
    assert "date — order_date" in llm.prompts[0]
    assert plan.template_id == "sales_quarterly"
    assert plan.dashboard_title == "Продажи по кварталам"
    assert [planned.title for planned in plan.dashboard_tiles] == [
        "Выручка по кварталам",
        "Топ товаров по выручке",
        "Выручка",
        "Средний чек",
    ]
    assert [planned.step_id for planned in plan.dashboard_tiles] == [0, 1, 2, 3]
    assert [planned.chart_type for planned in plan.dashboard_tiles] == ["bar", "bar", "kpi", "kpi"]
    assert [planned.is_kpi for planned in plan.dashboard_tiles] == [False, False, True, True]
    assert plan.dashboard_tiles[0].insight_needed is True


def template_reply(*titles: str) -> ResearchPlan:
    return ResearchPlan(
        steps=[total(number, "quantity") for number in range(len(titles))],
        dashboard_tiles=[tile(title, number) for number, title in enumerate(titles)],
    )


def test_template_applies_even_when_the_llm_plans_no_dashboard():
    import asyncio

    titles = [planned.title for planned in match(SALES_COLUMNS).tiles]
    llm = ScriptedLLM(ResearchPlan(steps=[total(0, "quantity")]), template_reply(*titles))
    plan = asyncio.run(StepPlanner(llm=llm).plan("Сводка продаж", {"columns": SALES_COLUMNS}))
    assert "в dashboard_tiles нет плиток шаблона" in llm.prompts[1]
    assert plan.dashboard_requested
    assert plan.template_id == "sales_quarterly"
    assert [planned.title for planned in plan.dashboard_tiles] == titles


def test_missing_template_tiles_are_asked_for_by_name():
    import asyncio

    titles = [planned.title for planned in match(SALES_COLUMNS).tiles]
    llm = ScriptedLLM(template_reply(*titles[:2]), template_reply(*titles))
    plan = asyncio.run(StepPlanner(llm=llm).plan("Дашборд продаж", {"columns": SALES_COLUMNS}))
    assert all(f"«{title}»" in llm.prompts[1] for title in titles[2:])
    assert f"«{titles[0]}»" not in llm.prompts[1].split("нет плиток шаблона")[1]
    assert plan.template_id == "sales_quarterly"
    assert len(plan.dashboard_tiles) == len(titles)


def test_template_left_incomplete_after_the_reask_degrades_to_a_plain_dashboard():
    import asyncio

    titles = [planned.title for planned in match(SALES_COLUMNS).tiles]
    llm = ScriptedLLM(template_reply(*titles[:2]), template_reply(*titles[:2]))
    plan = asyncio.run(StepPlanner(llm=llm).plan("Дашборд продаж", {"columns": SALES_COLUMNS}))
    assert plan.dashboard_requested
    assert plan.template_id is None
    assert [planned.title for planned in plan.dashboard_tiles] == titles[:2]


def test_template_without_any_tiles_after_the_reask_keeps_the_answer():
    import asyncio

    empty = ResearchPlan(steps=[total(0, "quantity")])
    llm = ScriptedLLM(empty, empty.model_copy(deep=True))
    plan = asyncio.run(StepPlanner(llm=llm).plan("Дашборд продаж", {"columns": SALES_COLUMNS}))
    assert plan.dashboard_requested
    assert plan.template_id is None
    assert plan.dashboard_tiles == []


def test_template_is_not_used_for_a_question_without_a_dashboard():
    import asyncio

    llm = ScriptedLLM(ResearchPlan(steps=[total(0, "quantity")]))
    plan = asyncio.run(
        StepPlanner(llm=llm).plan("Сколько всего продано", {"columns": SALES_COLUMNS})
    )
    assert "Продажи по кварталам" not in llm.prompts[0]
    assert not plan.dashboard_requested
    assert plan.template_id is None


def test_dashboard_without_tiles_is_asked_again():
    import asyncio

    fixed = dashboard_plan(tile("По категориям", 0), tile("Выручка", 1), tile("По датам", 2))
    llm = ScriptedLLM(dashboard_plan(), fixed)
    context = {"columns": ["category", "revenue", "date"]}
    plan = asyncio.run(StepPlanner(llm=llm).plan("Собери панель по выручке", context))
    assert NO_TILES in llm.prompts[1]
    assert len(plan.dashboard_tiles) == 3


def test_dashboard_without_tiles_after_the_reask_is_rejected():
    import asyncio

    llm = ScriptedLLM(dashboard_plan(), dashboard_plan())
    context = {"columns": ["category", "revenue", "date"]}
    with pytest.raises(PlanInvalid) as raised:
        asyncio.run(StepPlanner(llm=llm).plan("Дашборд по выручке", context))
    assert raised.value.errors == [NO_TILES]


def test_renumbering_steps_keeps_tile_references():
    import asyncio

    reply = ResearchPlan(
        steps=[total(1, "revenue", ["category"]), total(2, "revenue")],
        dashboard_tiles=[tile("По категориям", 1), tile("Выручка", 2, is_kpi=True)],
    )
    llm = ScriptedLLM(reply)
    context = {"columns": ["category", "revenue"], "next_step_id": 4}
    plan = asyncio.run(StepPlanner(llm=llm).plan("Дашборд по выручке", context))
    assert [step.step_id for step in plan.steps] == [4, 5]
    assert [planned.step_id for planned in plan.dashboard_tiles] == [4, 5]


def test_template_pairs_renamed_tiles_by_position_only_when_counts_match():
    template = match(SALES_COLUMNS)
    renamed = [tile(f"Плитка {number}", number) for number in range(len(template.tiles))]
    full = ResearchPlan(steps=[], dashboard_requested=True, dashboard_tiles=list(renamed))
    apply_template(full, template)
    assert [planned.step_id for planned in full.dashboard_tiles] == list(range(len(renamed)))

    exact = tile(template.tiles[1].title, 7)
    partial = ResearchPlan(steps=[], dashboard_requested=True, dashboard_tiles=[renamed[0], exact])
    apply_template(partial, template)
    assert [(planned.title, planned.step_id) for planned in partial.dashboard_tiles] == [
        (template.tiles[1].title, 7)
    ]
