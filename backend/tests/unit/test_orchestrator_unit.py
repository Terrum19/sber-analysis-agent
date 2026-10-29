"""Unit-тесты AgentOrchestrator с моками компонентов."""

import time

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator, _elapsed_ms
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory


class FakeExecutor:
    """МOCK исполнителя: возвращает фиксированную таблицу."""

    async def execute(self, step, input_table, **_):
        return "temp_result_v1"


class FakeInsightGen:
    """МOCK генератора инсайта."""

    async def generate(self, question, result_table, **_):
        from app.models.insight import Insight

        return Insight(
            headline="Мок инсайт",
            details=["Деталь 1"],
            limits="",
        )


class FakePlanner:
    """МOCK планировщика."""

    async def plan(self, question, context):
        from app.models.research_step import ResearchPlan, ResearchStep

        return ResearchPlan(
            steps=[
                ResearchStep(
                    step_id=0,
                    tool="aggregate",
                    columns=["category"],
                    purpose="Мок шаг",
                )
            ],
            final_viz="bar",
            dashboard_requested=False,
        )


@pytest.fixture
def orchestrator():
    db = DuckDBManager(path=":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    for session in ("s1", "s2"):
        SessionMemory(session).set_table("raw")
    return AgentOrchestrator(
        planner=FakePlanner(),
        executor=FakeExecutor(),
        insight_gen=FakeInsightGen(),
        db=db,
    )


def test_run_returns_events(orchestrator):
    import asyncio

    events = []

    async def _collect():
        async for event in orchestrator.run(ChatRequest(session_id="s1", question="Тест")):
            events.append(event)

    asyncio.run(_collect())
    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types


def test_run_error_in_step(orchestrator):
    import asyncio

    class FailingExecutor:
        async def execute(self, step, input_table, **_):
            raise ValueError("Таблица не найдена")

    orch = AgentOrchestrator(
        planner=FakePlanner(),
        executor=FailingExecutor(),
        insight_gen=FakeInsightGen(),
        db=orchestrator.db,
    )
    events = []

    async def _collect():
        async for event in orch.run(ChatRequest(session_id="s2", question="Тест ошибки")):
            events.append(event)

    asyncio.run(_collect())
    types = [e["type"] for e in events]
    assert "clarification_needed" in types
    assert "done" not in types
    # Проверяем содержимое события уточнения
    clar_events = [e for e in events if e["type"] == "clarification_needed"]
    assert len(clar_events) == 1
    assert clar_events[0]["data"]["step_id"] == 0
    assert "Таблица не найдена" in clar_events[0]["data"]["error"]
    assert "suggestions" in clar_events[0]["data"]


def collect(orchestrator, session_id: str) -> list[dict]:
    import asyncio

    async def _collect():
        request = ChatRequest(session_id=session_id, question="Тест")
        return [event async for event in orchestrator.run(request)]

    return asyncio.run(_collect())


def stages(events: list[dict]) -> list[str]:
    return [
        f"{event['data']['stage']}:{event['data']['status']}"
        if event["type"] == "progress"
        else event["type"]
        for event in events
    ]


def test_progress_events_precede_the_answer(orchestrator):
    from app.api.schemas.chat import ChatEvent, ProgressPayload

    events = collect(orchestrator, "s1")

    assert stages(events) == [
        "planning:started",
        "planning:finished",
        "step:started",
        "step:finished",
        "insight:started",
        "insight:finished",
        "text",
        "chart:started",
        "chart:finished",
        "chart",
        "done",
    ]
    for event in events:
        ChatEvent(**event)
        if event["type"] == "progress":
            ProgressPayload(**event["data"])
    progress = [event["data"] for event in events if event["type"] == "progress"]
    planned = ProgressPayload(**progress[1]).planned_steps
    assert planned is not None and planned[0].tool == "aggregate"
    assert progress[0] == {"stage": "planning", "status": "started"}
    assert progress[1]["stage"] == "planning"
    assert progress[1]["status"] == "finished"
    assert progress[1]["total_steps"] == 1
    assert progress[1]["planned_steps"] == [
        {"step_id": 0, "purpose": "Мок шаг", "tool": "aggregate"}
    ]
    assert progress[1]["elapsed_ms"] >= 1
    assert progress[2] == {
        "stage": "step",
        "status": "started",
        "step_id": 0,
        "tool": "aggregate",
        "purpose": "Мок шаг",
    }
    assert progress[3]["status"] == "finished"
    assert progress[3]["step_id"] == 0
    assert progress[3]["elapsed_ms"] >= 1
    assert progress[4] == {"stage": "insight", "status": "started"}
    assert progress[5]["stage"] == "insight"
    assert progress[5]["status"] == "finished"
    assert progress[5]["elapsed_ms"] >= 1
    assert progress[6] == {"stage": "chart", "status": "started"}
    assert progress[7]["stage"] == "chart"
    assert progress[7]["status"] == "finished"
    assert progress[7]["elapsed_ms"] >= 1


def test_planning_reports_real_substages(orchestrator):
    class SubstagePlanner(FakePlanner):
        async def plan(self, question, context, on_substage=None):
            for name in ("semantic", "llm", "validation"):
                if on_substage is not None:
                    on_substage(name)
            return await super().plan(question, context)

    orchestrator.planner = SubstagePlanner()
    events = collect(orchestrator, "s1")
    substages = [
        event["data"]
        for event in events
        if event["type"] == "progress" and event["data"].get("status") == "substage"
    ]
    assert [(event["stage"], event["substage"]) for event in substages] == [
        ("planning", "semantic"),
        ("planning", "llm"),
        ("planning", "validation"),
    ]


def test_failed_step_reports_progress(orchestrator):
    class FailingExecutor:
        async def execute(self, step, input_table, **_):
            raise NotImplementedError("Инструмент недоступен")

    orchestrator.executor = FailingExecutor()
    events = collect(orchestrator, "s2")

    assert stages(events) == [
        "planning:started",
        "planning:finished",
        "step:started",
        "step:failed",
        "clarification_needed",
    ]
    failed = events[3]["data"]
    assert failed["step_id"] == 0
    assert failed["error"] == "Инструмент недоступен"
    assert failed["elapsed_ms"] >= 1


def test_empty_plan_reports_zero_steps(orchestrator):
    from app.models.research_step import ResearchPlan

    class EmptyPlanner:
        async def plan(self, question, context):
            return ResearchPlan(steps=[], final_viz="bar", dashboard_requested=False)

    orchestrator.planner = EmptyPlanner()
    events = collect(orchestrator, "s1")

    assert stages(events) == ["planning:started", "planning:finished", "clarification_needed"]
    assert events[1]["data"]["total_steps"] == 0


def test_planner_failure_ends_the_stream(orchestrator):
    class FailingPlanner:
        async def plan(self, question, context):
            raise RuntimeError("LLM timeout")

    orchestrator.planner = FailingPlanner()
    events = collect(orchestrator, "s1")

    assert stages(events) == ["planning:started", "planning:failed", "error", "done"]
    assert events[1]["data"]["error"] == "LLM timeout"
    assert events[2]["data"]["message"]


def test_insight_failure_ends_the_stream(orchestrator):
    class FailingInsightGen:
        async def generate(self, question, result_table, **_):
            raise RuntimeError("LLM timeout")

    orchestrator.insight_gen = FailingInsightGen()
    events = collect(orchestrator, "s1")

    assert stages(events)[-4:] == ["insight:started", "insight:failed", "error", "done"]
    assert events[-3]["data"]["error"] == "LLM timeout"


def test_dashboard_failure_keeps_the_answer(orchestrator, monkeypatch):
    from app.core.dashboards.generator import DashboardGenerator
    from app.models.research_step import ResearchPlan

    class DashboardPlanner(FakePlanner):
        async def plan(self, question, context):
            plan = await super().plan(question, context)
            return ResearchPlan(steps=plan.steps, final_viz="bar", dashboard_requested=True)

    async def failing_generate(self, *args, **kwargs):
        raise RuntimeError("SQLite недоступна")

    orchestrator.planner = DashboardPlanner()
    monkeypatch.setattr(DashboardGenerator, "generate", failing_generate)
    events = collect(orchestrator, "s1")

    assert stages(events)[-6:] == [
        "chart:started",
        "chart:finished",
        "chart",
        "dashboard:started",
        "dashboard:failed",
        "done",
    ]
    assert "error" not in stages(events)
    assert events[-2]["data"]["error"] == "SQLite недоступна"


def test_small_talk_is_answered_without_the_planner(orchestrator):
    class NoPlanner:
        async def plan(self, question, context):
            raise AssertionError("планировщик не нужен для «привет»")

    orchestrator.planner = NoPlanner()
    events = list(_drain(orchestrator, "привет"))
    assert [event["type"] for event in events] == ["text", "done"]
    assert "Здравствуйте" in events[0]["data"]["headline"]


def test_small_talk_needs_no_table(orchestrator):
    events = list(_drain(orchestrator, "привет", session_id="no-table"))
    assert [event["type"] for event in events] == ["text", "done"]


def test_single_number_result_is_shown_as_kpi(orchestrator):
    from app.models.research_step import ResearchPlan, ResearchStep

    class OneNumberPlanner:
        async def plan(self, question, context):
            step = ResearchStep(step_id=0, tool="aggregate", columns=["a"], purpose="Итог")
            return ResearchPlan(steps=[step], final_viz="auto")

    orchestrator.db.execute("CREATE OR REPLACE TABLE temp_result_v1 AS SELECT 3 AS total")
    orchestrator.planner = OneNumberPlanner()
    events = list(_drain(orchestrator, "Какая общая сумма?"))
    chart = next(event for event in events if event["type"] == "chart")
    assert chart["data"]["spec"]["usermeta"]["chart_type"] == "kpi"


def _drain(orchestrator, question: str, session_id: str = "s1"):
    import asyncio

    async def run():
        request = ChatRequest(session_id=session_id, question=question)
        return [event async for event in orchestrator.run(request)]

    return asyncio.run(run())


def test_done_counts_truncated_prompts(orchestrator):
    from app.core.llm.budget import report_truncation

    class TruncatedPlanner(FakePlanner):
        async def plan(self, question, context):
            # Планировщик работает в задаче хода: обрезка из неё тоже должна дойти до done.
            report_truncation(12_000, 4096, "упёрся в контекст")
            return await super().plan(question, context)

    class TruncatedInsightGen(FakeInsightGen):
        async def generate(self, question, result_table, **_):
            report_truncation(9000, 4096, "упёрся в контекст")
            return await super().generate(question, result_table)

    orchestrator.planner = TruncatedPlanner()
    orchestrator.insight_gen = TruncatedInsightGen()
    events = collect(orchestrator, "s1")

    assert events[-1]["data"]["prompt_truncated"] == 2
    # Следующий ход без обрезок — в done только turn_id.
    orchestrator.planner, orchestrator.insight_gen = FakePlanner(), FakeInsightGen()
    assert list(collect(orchestrator, "s2")[-1]["data"]) == ["turn_id"]


def test_clarification_counts_truncated_prompts(orchestrator):
    from app.core.llm.budget import report_truncation
    from app.models.research_step import ResearchPlan

    class TruncatedEmptyPlanner:
        async def plan(self, question, context):
            # Обрезанный промпт: модель без списка тулов не построила план — ход кончится уточнением.
            report_truncation(12_000, 4096, "упёрся в контекст")
            return ResearchPlan(steps=[], final_viz="bar", dashboard_requested=False)

    orchestrator.planner = TruncatedEmptyPlanner()
    events = collect(orchestrator, "s1")

    assert events[-1]["type"] == "clarification_needed"
    assert events[-1]["data"]["prompt_truncated"] == 1


def ask(orchestrator, question: str) -> list[dict]:
    import asyncio

    async def _collect():
        request = ChatRequest(session_id="s1", question=question)
        return [event async for event in orchestrator.run(request)]

    return asyncio.run(_collect())


class OverviewLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def complete_structured(self, system, user, schema):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return schema(display_name="Числа", summary="Передо мной числа.\n\nПропусков нет.")


class NoPlanner:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def plan(self, question, context):
        raise AssertionError("обзорный вопрос не идёт в планировщик")


OVERVIEW_STAGES = [
    "insight:started",
    "insight:finished",
    "text",
    "chart:started",
    "chart:finished",
    "chart",
    "done",
]


def test_overview_question_skips_planner(orchestrator):
    llm = OverviewLLM()
    orchestrator.planner = NoPlanner(llm)
    events = ask(orchestrator, "Что передо мной?")

    assert stages(events) == OVERVIEW_STAGES
    assert events[2]["data"] == {
        "headline": "raw: 2 строки, 1 колонка, колонок с датами нет — период не определить",
        "details": ["Передо мной числа.", "Пропусков нет."],
    }
    spec = events[5]["data"]["spec"]
    assert spec["usermeta"]["chart_type"] == "table"
    assert [row["Колонка"] for row in spec["data"]["values"]] == ["a"]
    assert stages(ask(orchestrator, "Сколько строк?")) == OVERVIEW_STAGES
    assert llm.calls == 1
    assert SessionMemory("s1").last_chart() is None


def test_overview_without_llm_still_answers(orchestrator):
    from app.core.agent.orchestrator import SUMMARY_UNAVAILABLE

    orchestrator.planner = NoPlanner(OverviewLLM(RuntimeError("LLM недоступна")))
    events = ask(orchestrator, "Что за данные?")

    assert stages(events) == OVERVIEW_STAGES
    assert events[2]["data"]["details"] == [SUMMARY_UNAVAILABLE]


def test_overview_failure_ends_the_stream(orchestrator, monkeypatch):
    from app.core.data import overview

    def broken(db, table):
        raise RuntimeError("профиль не построен")

    monkeypatch.setattr(overview, "passport", broken)
    orchestrator.planner = NoPlanner(OverviewLLM())
    events = ask(orchestrator, "Что передо мной?")

    assert stages(events) == ["insight:started", "insight:failed", "error", "done"]
    assert events[1]["data"]["error"] == "профиль не построен"


def test_elapsed_ms_is_never_zero() -> None:
    assert _elapsed_ms(time.perf_counter()) == 1
    assert _elapsed_ms(time.perf_counter() - 2.5) == 2500
