"""Оркестратор: автосборка графика и смена вида из кеша без LLM и SQL."""

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator, _chart_event
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

STEP_SQL = "SELECT region, SUM(amount) AS revenue FROM raw GROUP BY region ORDER BY region"


class SpyDB(DuckDBManager):
    def __init__(self) -> None:
        super().__init__(":memory:")
        self.statements: list[str] = []

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        self.statements.append(sql)
        return super().query(sql, params)

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.statements.append(sql)
        super().execute(sql, params)


class FakePlanner:
    def __init__(
        self,
        final_viz: str = "bar",
        tool: str = "group",
        purpose: str = "выручка по регионам",
        arguments: dict | None = None,
    ) -> None:
        self.calls = 0
        self.final_viz = final_viz
        self.tool = tool
        self.purpose = purpose
        self.arguments = arguments or {}

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.calls += 1
        steps = [
            ResearchStep(step_id=1, tool=self.tool, purpose=self.purpose, arguments=self.arguments)
        ]
        return ResearchPlan(steps=steps, final_viz=self.final_viz)


class FakeExecutor:
    def __init__(self, db: DuckDBManager, fail: bool = False) -> None:
        self.db = db
        self.calls = 0
        self.fail = fail

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("broken step")
        return self.db.save_step_result(input_table, STEP_SQL)


class FakeInsight:
    def __init__(self) -> None:
        self.calls = 0
        self.supporting: list[tuple[str, str]] = []

    async def generate(
        self, question: str, result_table: str, supporting=(), **_: object
    ) -> Insight:
        self.calls += 1
        self.supporting = list(supporting)
        return Insight(headline="Москва лидирует", details=["msk > spb"])


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))
    return tmp_path


@pytest.fixture
def db():
    manager = SpyDB()
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), ('msk', 2.5)) "
        "AS rows(region, amount)"
    )
    for session in ("s1", "fresh"):
        SessionMemory(session).set_table("raw")
    manager.statements.clear()
    yield manager
    manager.close()


@pytest.fixture
def agent(db):
    orchestrator = AgentOrchestrator(
        planner=FakePlanner(), executor=FakeExecutor(db), insight_gen=FakeInsight()
    )
    return orchestrator


async def collect(agent: AgentOrchestrator, question: str, session: str = "s1") -> list[dict]:
    """События ответа без progress: их проверяет test_orchestrator_unit."""
    request = ChatRequest(session_id=session, question=question)
    return [event async for event in agent.run(request) if event["type"] != "progress"]


def chart_of(events: list[dict]) -> dict:
    return next(event["data"] for event in events if event["type"] == "chart")


async def test_first_answer_builds_filled_spec(agent, db) -> None:
    profile = profile_table(db, "raw")
    ProfileMemory(db).save_profile(profile, {"revenue": "выручка", "region": "регион"})
    events = await collect(agent, "Сравни выручку по регионам")
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    chart = chart_of(events)
    spec = chart["spec"]
    assert chart["caption"] == "Москва лидирует"
    assert chart["result_table"] == "temp_result_v1"
    assert spec["title"] == "выручка по регионам"
    assert spec["mark"] == "bar"
    assert spec["data"]["values"] == [
        {"region": "msk", "revenue": 12.5},
        {"region": "spb", "revenue": 4.0},
    ]
    assert spec["encoding"]["x"]["title"] == "Регион"
    assert spec["encoding"]["y"]["title"] == "Выручка"
    assert SessionMemory("s1").last_chart() == {
        "type": "bar",
        "title": "Москва лидирует",
        "result_table": "temp_result_v1",
    }


async def test_view_change_is_served_from_cache(agent, db) -> None:
    await collect(agent, "Сравни выручку по регионам")
    db.statements.clear()

    events = await collect(agent, "А теперь покажи то же самое в другом виде")

    assert [event["type"] for event in events] == ["chart", "done"]
    assert chart_of(events)["result_table"] == "temp_result_v1"
    spec = chart_of(events)["spec"]
    assert spec["usermeta"]["chart_type"] == "line"
    assert spec["data"]["values"][0] == {"region": "msk", "revenue": 12.5}
    assert agent.planner.calls == 1
    assert agent.executor.calls == 1
    assert agent.insight_gen.calls == 1
    # Каталог (таблицы, колонки, паспорт) читает проверка перехода к другим данным.
    catalog = ("_meta", "duckdb_tables()", "information_schema")
    data_reads = [sql for sql in db.statements if not any(mark in sql for mark in catalog)]
    assert data_reads == ["SELECT * FROM temp_result_v1 LIMIT 500"]
    assert STEP_SQL not in db.statements

    events = await collect(agent, "Сделай таблицей")
    assert chart_of(events)["spec"]["usermeta"]["chart_type"] == "table"
    assert chart_of(events)["caption"] == "Москва лидирует"
    assert agent.planner.calls == 1
    assert SessionMemory("s1").last_chart()["type"] == "table"


async def test_view_change_without_previous_chart_plans_anew(agent) -> None:
    events = await collect(agent, "Покажи в другом виде", session="fresh")
    assert agent.planner.calls == 1
    assert chart_of(events)["spec"]["usermeta"]["chart_type"] == "bar"


async def test_missing_cached_table_falls_back_to_planner(agent, db) -> None:
    await collect(agent, "Сравни выручку по регионам")
    db.execute("DROP TABLE temp_result_v1")
    await collect(agent, "Сделай линейным графиком")
    assert agent.planner.calls == 2
    assert agent.executor.calls == 2


async def test_failed_step_reports_error(db) -> None:
    agent = AgentOrchestrator(
        planner=FakePlanner(),
        executor=FakeExecutor(db, fail=True),
        insight_gen=FakeInsight(),
        cache=StateCache(db),
    )
    events = await collect(agent, "Сравни выручку по регионам")
    assert [event["type"] for event in events] == ["clarification_needed"]
    assert events[0]["data"]["step_id"] == 1
    assert events[0]["data"]["error"] == "broken step"
    assert SessionMemory("s1").get_pending_recovery()["failed_step_id"] == 1


async def test_dashboard_request_saves_spec(db) -> None:
    agent = AgentOrchestrator(
        planner=FakePlanner(),
        executor=FakeExecutor(db),
        insight_gen=FakeInsight(),
        cache=StateCache(db),
    )
    original = agent.planner.plan

    async def plan_with_dashboard(question: str, context: dict) -> ResearchPlan:
        plan = await original(question, context)
        return plan.model_copy(update={"dashboard_requested": True})

    agent.planner.plan = plan_with_dashboard
    events = await collect(agent, "Выручка по регионам")
    dashboard = next(event for event in events if event["type"] == "dashboard")
    # B-06: событие содержит плитки для превью в чате, но не снимок данных
    assert dashboard["data"]["title"] == "Дашборд"
    assert dashboard["data"]["tiles"]
    sent = dashboard["data"]["tiles"][0]
    assert sent["spec"]["mark"] == "bar"
    assert sent["layout"]
    assert "snapshot_data" not in sent
    saved = DashboardStore().get(dashboard["data"]["dashboard_id"])
    assert saved.tiles[0].spec["mark"] == "bar"
    assert saved.tiles[0].result_table is not None
    assert saved.tiles[0].snapshot_data


def test_state_cache_put_and_get(db) -> None:
    cache = StateCache(db)
    table = cache.put("raw", "SELECT region FROM raw")
    assert cache.get(table, limit=1) == [{"region": "msk"}]


async def test_new_question_drops_pending_clarification(db) -> None:
    executor = FakeExecutor(db, fail=True)
    agent = AgentOrchestrator(planner=FakePlanner(), executor=executor, insight_gen=FakeInsight())
    await collect(agent, "Сравни выручку по регионам")
    assert SessionMemory("s1").get_pending_recovery() is not None

    executor.fail = False
    events = await collect(agent, "Покажи выручку по регионам")
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    assert SessionMemory("s1").get_pending_recovery() is None


async def test_recovery_answers_the_original_question(db) -> None:
    class QuestionSpy(FakeInsight):
        async def generate(self, question: str, result_table: str, **_: object) -> Insight:
            self.question = question
            return await super().generate(question, result_table)

    executor = FakeExecutor(db, fail=True)
    insight = QuestionSpy()
    agent = AgentOrchestrator(planner=FakePlanner(), executor=executor, insight_gen=insight)
    await collect(agent, "Сравни выручку по регионам")

    executor.fail = False
    request = ChatRequest(
        session_id="s1", question="по сумме amount", recovery_response="по сумме amount"
    )
    events = [event async for event in agent.run(request)]
    assert [
        event["data"]["stage"] if event["type"] == "progress" else event["type"] for event in events
    ] == [
        "step",
        "step",
        "insight",
        "insight",
        "text",
        "chart",
        "chart",
        "chart",
        "done",
    ]
    assert insight.question == "Сравни выручку по регионам"
    # Ответ на уточнение закрывает тот же ход, а не заводит новый.
    turns = SessionMemory("s1")._state["turns"]
    assert [(t["question"], t["status"]) for t in turns] == [("Сравни выручку по регионам", "ok")]
    assert events[-1]["data"]["turn_id"] == turns[0]["turn_id"]


async def test_done_carries_turn_id(agent) -> None:
    first = await collect(agent, "Сравни выручку по регионам")
    second = await collect(agent, "Привет")
    turns = SessionMemory("s1")._state["turns"]
    assert [first[-1]["data"]["turn_id"], second[-1]["data"]["turn_id"]] == [
        t["turn_id"] for t in turns
    ]


async def test_steps_of_failed_turn_stay_out_of_next_plan(db) -> None:
    class TwoStepPlanner(FakePlanner):
        def __init__(self) -> None:
            super().__init__()
            self.contexts: list[dict] = []

        async def plan(self, question: str, context: dict) -> ResearchPlan:
            self.contexts.append(context)
            steps = [
                ResearchStep(step_id=0, tool="group", purpose="выручка по регионам"),
                ResearchStep(step_id=1, tool="broken", purpose="сломанный шаг"),
            ]
            return ResearchPlan(steps=steps, final_viz="bar")

    class BrokenToolExecutor(FakeExecutor):
        async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
            if step.tool == "broken" and self.fail:
                raise RuntimeError("broken step")
            return self.db.save_step_result(input_table, STEP_SQL)

    planner = TwoStepPlanner()
    executor = BrokenToolExecutor(db, fail=True)
    agent = AgentOrchestrator(planner=planner, executor=executor, insight_gen=FakeInsight())
    events = await collect(agent, "Сравни выручку по регионам")
    assert events[-1]["type"] == "clarification_needed"
    assert events[-1]["data"]["turn_id"] == SessionMemory("s1")._state["turns"][0]["turn_id"]

    executor.fail = False
    await collect(agent, "Покажи выручку по регионам")
    # Ход с уточнением в контексте есть, но шаг 0 (он выполнился) — нет.
    turns = planner.contexts[-1]["turns"]
    assert [(t["question"], t["status"], t["steps"]) for t in turns] == [
        ("Сравни выручку по регионам", "clarification", [])
    ]

    await collect(agent, "А по месяцам?")
    turns = planner.contexts[-1]["turns"]
    assert [t["question"] for t in turns] == [
        "Сравни выручку по регионам",
        "Покажи выручку по регионам",
    ]
    assert [len(t["steps"]) for t in turns] == [0, 2]


def test_chart_without_step_result_has_no_result_table() -> None:
    assert "result_table" not in _chart_event({}, "caption", "raw")["data"]


async def test_reply_to_morphs_chart_of_the_chosen_turn(agent, db) -> None:
    first = await collect(agent, "Сравни выручку по регионам")
    await collect(agent, "Сравни выручку по регионам ещё раз")
    request = ChatRequest(
        session_id="s1", question="Сделай таблицей", reply_to=first[-1]["data"]["turn_id"]
    )
    events = [event async for event in agent.run(request)]
    assert chart_of(events)["result_table"] == "temp_result_v1"
    assert SessionMemory("s1")._state["turns"][-1]["reply_to"] == first[-1]["data"]["turn_id"]


def agent_with(db: DuckDBManager, planner: FakePlanner) -> AgentOrchestrator:
    return AgentOrchestrator(planner=planner, executor=FakeExecutor(db), insight_gen=FakeInsight())


async def test_chart_without_purpose_is_titled_by_columns(db) -> None:
    agent = agent_with(db, FakePlanner(purpose=""))
    spec = chart_of(await collect(agent, "Сравни выручку по регионам"))["spec"]
    assert spec["title"] == "revenue по region"
    assert spec["title"] != "Москва лидирует"


async def test_no_chart_answer_sends_only_text(db) -> None:
    agent = agent_with(db, FakePlanner(final_viz="none"))
    request = ChatRequest(session_id="s1", question="Кто лидирует?")
    events = [event async for event in agent.run(request)]
    assert [event["type"] for event in events if event["type"] != "progress"] == ["text", "done"]
    assert all(event["data"]["stage"] != "chart" for event in events if event["type"] == "progress")
    assert SessionMemory("s1").last_chart() is None


async def test_no_chart_is_ignored_when_dashboard_is_requested(db) -> None:
    planner = FakePlanner(final_viz="none")
    original = planner.plan

    async def plan_with_dashboard(question: str, context: dict) -> ResearchPlan:
        plan = await original(question, context)
        return plan.model_copy(update={"dashboard_requested": True})

    planner.plan = plan_with_dashboard
    agent = agent_with(db, planner)
    raw_events = [
        event
        async for event in agent.run(
            ChatRequest(session_id="s1", question="Выручка по регионам")
        )
    ]
    events = [event for event in raw_events if event["type"] != "progress"]
    assert [event["type"] for event in events] == ["text", "chart", "dashboard", "done"]
    dashboard_progress = [
        event["data"]
        for event in raw_events
        if event["type"] == "progress" and event["data"]["stage"] == "dashboard"
    ]
    assert [event["status"] for event in dashboard_progress] == ["started", "finished"]
    assert dashboard_progress[1]["elapsed_ms"] >= 1
    assert chart_of(events)["spec"]["mark"] == "bar"


async def test_analysis_result_is_shown_as_table(db) -> None:
    agent = agent_with(
        db, FakePlanner(final_viz="bar", tool="describe_stats", purpose="статистики")
    )
    spec = chart_of(await collect(agent, "Опиши выручку"))["spec"]
    assert spec["usermeta"]["chart_type"] == "table"
    assert spec["title"] == "статистики"


def stats_agent(db: DuckDBManager, rows: int) -> AgentOrchestrator:
    db.execute(f"INSERT INTO raw SELECT 'msk', range // 25 FROM range({rows - 3})")
    planner = FakePlanner(
        final_viz="table",
        tool="describe_stats",
        purpose="распределение выручки",
        arguments={"column": "amount"},
    )
    return agent_with(db, planner)


async def test_distribution_question_draws_histogram_of_the_column(db) -> None:
    agent = stats_agent(db, rows=25)
    chart = chart_of(await collect(agent, "Покажи распределение amount"))
    spec = chart["spec"]
    assert spec["usermeta"]["chart_type"] == "histogram"
    assert spec["title"] == "распределение выручки"
    assert len(spec["data"]["values"]) == 25
    assert set(spec["data"]["values"][0]) == {"amount"}
    assert agent.insight_gen.supporting == [("распределение выручки", "temp_result_v1")]
    assert chart["result_table"] == "temp_result_v2"


async def test_distribution_of_few_values_shows_the_statistics(db) -> None:
    agent = stats_agent(db, rows=5)
    chart = chart_of(await collect(agent, "Покажи распределение amount"))
    assert chart["spec"]["usermeta"]["chart_type"] == "table"
    assert chart["result_table"] == "temp_result_v1"
    assert agent.insight_gen.supporting == []


async def test_distribution_is_drawn_from_a_sample_of_the_whole_column(db) -> None:
    agent = stats_agent(db, rows=SPEC_ROW_LIMIT * 4)
    spec = chart_of(await collect(agent, "Покажи распределение amount"))["spec"]
    amounts = [row["amount"] for row in spec["data"]["values"]]
    assert len(amounts) == SPEC_ROW_LIMIT
    assert max(amounts) > SPEC_ROW_LIMIT // 25


def test_distribution_needs_the_input_of_the_shown_step(db) -> None:
    agent = agent_with(db, FakePlanner())
    step = ResearchStep(step_id=1, tool="describe_stats", arguments={"column": "amount"})
    assert agent._distribution_values("Покажи распределение amount", step, {}) is None


class SqlExecutor(FakeExecutor):
    def __init__(self, db: DuckDBManager, sql: str) -> None:
        super().__init__(db)
        self.sql = sql

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        self.calls += 1
        return self.db.save_step_result(input_table, self.sql)


async def test_trend_result_is_drawn_as_line(db) -> None:
    sql = (
        "SELECT DATE '2024-01-01' + INTERVAL (range) MONTH AS period, range * 1.0 AS value,"
        " range * 1.0 AS trend FROM range(12)"
    )
    planner = FakePlanner(final_viz="table", tool="trend_analysis", purpose="тренд выручки")
    agent = AgentOrchestrator(
        planner=planner, executor=SqlExecutor(db, sql), insight_gen=FakeInsight()
    )
    spec = chart_of(await collect(agent, "Покажи тренд выручки"))["spec"]
    assert spec["usermeta"]["chart_type"] == "line"
    assert spec["title"] == "тренд выручки"


async def test_histogram_of_the_named_column_of_a_wide_result(db) -> None:
    db.execute("INSERT INTO raw SELECT 'msk', range FROM range(30)")
    sql = "SELECT region, amount, amount * 2 AS doubled FROM raw"
    agent = AgentOrchestrator(
        planner=FakePlanner(final_viz="histogram", tool="filter_rows", purpose="суммы"),
        executor=SqlExecutor(db, sql),
        insight_gen=FakeInsight(),
    )
    chart = chart_of(await collect(agent, "Распределение amount"))
    assert chart["spec"]["usermeta"]["chart_type"] == "histogram"
    assert set(chart["spec"]["data"]["values"][0]) == {"amount"}
    assert chart["result_table"] == "temp_result_v2"


async def test_failed_distribution_sample_keeps_the_statistics(db, monkeypatch) -> None:
    agent = stats_agent(db, rows=25)

    def broken(*_: object) -> str:
        raise RuntimeError("sample failed")

    monkeypatch.setattr(agent, "_distribution_values", broken)
    events = await collect(agent, "Покажи распределение amount")
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    assert chart_of(events)["spec"]["usermeta"]["chart_type"] == "table"
