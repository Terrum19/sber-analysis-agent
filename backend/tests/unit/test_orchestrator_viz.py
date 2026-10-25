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
        SessionMemory(session).set_table("raw", manager.columns("raw"))
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
        "chart_type": "bar",
        "result_table": "temp_result_v1",
        "caption": "Москва лидирует",
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
    data_reads = [sql for sql in db.statements if "_meta." not in sql]
    assert data_reads == ["SELECT * FROM temp_result_v1 LIMIT 500"]
    assert STEP_SQL not in db.statements

    events = await collect(agent, "Сделай таблицей")
    assert chart_of(events)["spec"]["usermeta"]["chart_type"] == "table"
    assert chart_of(events)["caption"] == "Москва лидирует"
    assert agent.planner.calls == 1
    assert SessionMemory("s1").last_chart()["chart_type"] == "table"


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
    events = await collect(agent, "Создай дашборд по регионам")
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
    ] == ["step", "step", "insight", "text", "chart", "chart", "done"]
    assert insight.question == "Сравни выручку по регионам"


def test_chart_without_step_result_has_no_result_table() -> None:
    assert "result_table" not in _chart_event({}, "caption", "raw")["data"]


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
    events = await collect(agent_with(db, planner), "Создай дашборд по регионам")
    assert [event["type"] for event in events] == ["text", "chart", "dashboard", "done"]
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
