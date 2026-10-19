"""Оркестратор: автосборка графика и смена вида из кеша без LLM и SQL."""

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.memory.artifact_registry import ArtifactRegistry
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
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
    def __init__(self, final_viz: str = "bar") -> None:
        self.calls = 0
        self.final_viz = final_viz

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.calls += 1
        steps = [ResearchStep(step_id=1, tool="group", purpose="выручка по регионам")]
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

    async def generate(self, question: str, result_table: str, **_: object) -> Insight:
        self.calls += 1
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
    return [event async for event in agent.run(ChatRequest(session_id=session, question=question))]


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


async def test_failed_step_reports_error(db, monkeypatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("app.core.agent.recovery.asyncio.sleep", no_sleep)
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
    saved = ArtifactRegistry().get(dashboard["data"]["dashboard_id"])
    assert saved["tiles"][0]["spec"]["mark"] == "bar"


def test_state_cache_put_and_get(db) -> None:
    cache = StateCache(db)
    table = cache.put("raw", "SELECT region FROM raw")
    assert cache.get(table, limit=1) == [{"region": "msk"}]


async def test_new_question_drops_pending_clarification(db, monkeypatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("app.core.agent.recovery.asyncio.sleep", no_sleep)
    executor = FakeExecutor(db, fail=True)
    agent = AgentOrchestrator(planner=FakePlanner(), executor=executor, insight_gen=FakeInsight())
    await collect(agent, "Сравни выручку по регионам")
    assert SessionMemory("s1").get_pending_recovery() is not None

    executor.fail = False
    events = await collect(agent, "Покажи выручку по регионам")
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    assert SessionMemory("s1").get_pending_recovery() is None


async def test_recovery_answers_the_original_question(db, monkeypatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    class QuestionSpy(FakeInsight):
        async def generate(self, question: str, result_table: str, **_: object) -> Insight:
            self.question = question
            return await super().generate(question, result_table)

    monkeypatch.setattr("app.core.agent.recovery.asyncio.sleep", no_sleep)
    executor = FakeExecutor(db, fail=True)
    insight = QuestionSpy()
    agent = AgentOrchestrator(planner=FakePlanner(), executor=executor, insight_gen=insight)
    await collect(agent, "Сравни выручку по регионам")

    executor.fail = False
    request = ChatRequest(
        session_id="s1", question="по сумме amount", recovery_response="по сумме amount"
    )
    events = [event async for event in agent.run(request)]
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    assert insight.question == "Сравни выручку по регионам"
