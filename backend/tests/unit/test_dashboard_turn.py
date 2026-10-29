"""Дашборд по кнопке (#224): план плиток, шаги плиток, выбор графика LLM, идеи."""

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent import dashboard_turn
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.step_executor import StepExecutor
from app.core.dashboards import ideas
from app.core.dashboards.quick_tiles import quick_plan
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory
from app.models.dashboard_plan import (
    ChartChoice,
    ChartChoices,
    DashboardIdea,
    DashboardIdeas,
    DashboardOutline,
    OutlineTile,
)
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

TILE_SQL = {
    "Выручка по регионам": "SELECT region, SUM(amount) AS revenue FROM raw GROUP BY region",
    "Сколько всего выручки": "SELECT SUM(amount) AS revenue FROM raw",
    "Выручка по дням": "SELECT day, SUM(amount) AS revenue FROM raw GROUP BY day ORDER BY day",
    "Сломанная плитка": "broken",
}
OUTLINE = DashboardOutline(
    title="Продажи",
    tiles=[
        OutlineTile(title="Регионы", question="Выручка по регионам", intent="compare"),
        OutlineTile(title="Выручка", question="Сколько всего выручки", intent="kpi"),
        OutlineTile(title="По дням", question="Выручка по дням", intent="trend"),
        OutlineTile(title="Сломано", question="Сломанная плитка", intent="compare"),
    ],
)
IDEAS = DashboardIdeas(
    ideas=[DashboardIdea(title=f"Идея {number}", description="описание") for number in range(4)]
)


class ScriptedLLM:
    """Ответы по имени схемы, по очереди; исключение в очереди — бросается."""

    def __init__(self, **answers: list) -> None:
        self.answers = answers
        self.prompts: dict[str, list[str]] = {}

    async def complete_structured(self, system: str, user: str, schema: type) -> object:
        self.prompts.setdefault(schema.__name__, []).append(user)
        answer = self.answers[schema.__name__].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class SqlPlanner:
    """План плитки — один шаг, SQL которого задан вопросом плитки."""

    def __init__(self, llm: ScriptedLLM) -> None:
        self.llm = llm
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        start = context["next_step_id"]
        return ResearchPlan(
            steps=[ResearchStep(step_id=start, tool="group", arguments={"sql": TILE_SQL[question]})]
        )


class SqlExecutor:
    def __init__(self, db: DuckDBManager) -> None:
        self.db = db

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        if step.arguments["sql"] == "broken":
            raise RuntimeError("broken step")
        return self.db.save_step_result("raw", step.arguments["sql"])

    def fix_step(self, *_: object) -> None:
        raise AssertionError("not used")


class NoInsight:
    async def generate(self, *_: object, **__: object) -> Insight:
        raise AssertionError("в режиме дашборда инсайт хода не строится")


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))
    monkeypatch.setattr(ideas, "_cache", {})


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES "
        "('msk', DATE '2024-01-01', 10.0), ('spb', DATE '2024-01-02', 4.0), "
        "('msk', DATE '2024-01-03', 2.5), ('ekb', DATE '2024-01-04', 7.0)) "
        "AS rows(region, day, amount)"
    )
    SessionMemory("s1").set_table("raw")
    yield manager
    manager.close()


def agent(db, llm: ScriptedLLM) -> AgentOrchestrator:
    planner = SqlPlanner(llm)
    planner.fix_step = _no_fix
    return AgentOrchestrator(
        planner=planner, executor=SqlExecutor(db), insight_gen=NoInsight(), db=db
    )


async def _no_fix(*_: object, **__: object) -> ResearchStep:
    raise ValueError("нечем исправить")


async def collect(orchestrator: AgentOrchestrator, **request: object) -> list[dict]:
    chat = ChatRequest(session_id="s1", mode="dashboard", **request)
    return [event async for event in orchestrator.run(chat)]


def kinds(events: list[dict]) -> list[str]:
    return [event["type"] for event in events if event["type"] != "progress"]


def stages(events: list[dict], stage: str) -> list[dict]:
    return [
        event["data"]
        for event in events
        if event["type"] == "progress" and event["data"]["stage"] == stage
    ]


def saved(events: list[dict]):
    sent = next(event["data"] for event in events if event["type"] == "dashboard")
    return DashboardStore().get(sent["dashboard_id"])


def choices(*charts: ChartChoice) -> ChartChoices:
    return ChartChoices(charts=list(charts))


async def test_idea_builds_dashboard_with_llm_chart_choice(db) -> None:
    llm = ScriptedLLM(
        DashboardOutline=[OUTLINE],
        ChartChoices=[
            choices(
                ChartChoice(tile_id=0, chart_type="bar", x="region", y="revenue"),
                ChartChoice(tile_id=1, chart_type="kpi"),
                # 4 точки — line подходит; по дням, а не по регионам.
                ChartChoice(tile_id=2, chart_type="line", x="day", y="revenue"),
            )
        ],
    )
    idea = DashboardIdea(id="1", title="Продажи", description="выручка")
    events = await collect(agent(db, llm), question="Продажи", idea=idea)

    assert kinds(events) == ["dashboard_outline", "dashboard", "text", "done"]
    outline = next(event["data"] for event in events if event["type"] == "dashboard_outline")
    assert [tile["intent"] for tile in outline["tiles"]] == ["compare", "kpi", "trend", "compare"]
    # Запрос — идея: переспрашивать, хватает ли информации, не нужно.
    assert "enough_info всегда true" in llm.prompts["DashboardOutline"][0]
    assert [(tile["tile_id"], tile["status"]) for tile in stages(events, "tile")].count(
        (3, "failed")
    ) == 1
    assert all(step["tile_id"] in range(4) for step in stages(events, "step"))

    dashboard = saved(events)
    assert dashboard.title == "Продажи"
    built = {tile.title: tile.spec["usermeta"]["chart_type"] for tile in dashboard.tiles}
    assert built == {"Регионы": "bar", "Выручка": "kpi", "По дням": "line"}
    assert dashboard.tiles[0].is_kpi
    text = next(event["data"] for event in events if event["type"] == "text")
    assert "Не удалось построить: Сломано." in text["details"]
    assert SessionMemory("s1").recent_turns()[-1]["status"] == "ok"


async def test_rejected_choice_is_asked_again_then_falls_back_to_intent(db) -> None:
    llm = ScriptedLLM(
        DashboardOutline=[OUTLINE],
        ChartChoices=[
            choices(
                # kpi не по одному числу, колонки нет — оба отклоняются.
                ChartChoice(tile_id=0, chart_type="kpi"),
                ChartChoice(tile_id=1, chart_type="bar", x="nope"),
                ChartChoice(tile_id=2, chart_type="histogram", y="revenue"),
            ),
            choices(ChartChoice(tile_id=0, chart_type="bar", x="region", y="revenue")),
        ],
    )
    events = await collect(agent(db, llm), question="Продажи по регионам и дням")
    retry = llm.prompts["ChartChoices"][1]
    assert "плитка 0: kpi — только когда результат одно число" in retry
    assert "колонки 'nope' нет в результате" in retry
    built = {tile.title: tile.spec["usermeta"]["chart_type"] for tile in saved(events).tiles}
    # Плитка 1 — одно число: kpi по intent; плитка 2 — 4 точки по дням: line по intent.
    assert built == {"Регионы": "bar", "Выручка": "kpi", "По дням": "line"}


async def test_chart_choice_failure_uses_intent(db) -> None:
    llm = ScriptedLLM(DashboardOutline=[OUTLINE], ChartChoices=[RuntimeError("timeout")])
    events = await collect(agent(db, llm), question="Продажи по регионам и дням")
    built = {tile.title: tile.spec["usermeta"]["chart_type"] for tile in saved(events).tiles}
    assert built == {"Регионы": "bar", "Выручка": "kpi", "По дням": "line"}


async def test_vague_request_takes_the_first_idea(db) -> None:
    llm = ScriptedLLM(
        DashboardOutline=[DashboardOutline(enough_info=False), OUTLINE],
        DashboardIdeas=[IDEAS],
        ChartChoices=[choices()],
    )
    events = await collect(agent(db, llm), question="Сделай дашборд")
    assert kinds(events) == [
        "dashboard_fallback",
        "dashboard_outline",
        "dashboard",
        "text",
        "done",
    ]
    fallback = events[[event["type"] for event in events].index("dashboard_fallback")]["data"]
    assert fallback["idea"]["title"] == "Идея 0"
    assert [idea["title"] for idea in fallback["alternatives"]] == ["Идея 1", "Идея 2", "Идея 3"]
    assert "Идея 0. описание" in llm.prompts["DashboardOutline"][1]


async def test_too_few_tiles_is_an_error(db) -> None:
    broken = DashboardOutline(
        title="x",
        tiles=[
            OutlineTile(title="a", question="Сломанная плитка"),
            OutlineTile(title="b", question="Выручка по регионам"),
        ],
    )
    events = await collect(agent(db, ScriptedLLM(DashboardOutline=[broken])), question="а б в")
    assert kinds(events) == ["dashboard_outline", "error", "done"]
    assert "построено плиток 1 из 2" in events[-2]["data"]["message"]
    assert SessionMemory("s1").recent_turns()[-1]["status"] == "failed"


async def test_outline_without_tiles_for_an_idea_is_asked_again_then_fails(db) -> None:
    llm = ScriptedLLM(DashboardOutline=[DashboardOutline(), DashboardOutline()])
    idea = DashboardIdea(title="Продажи")
    events = await collect(agent(db, llm), question="Продажи", idea=idea)
    assert len(llm.prompts["DashboardOutline"]) == 2
    assert kinds(events) == ["error", "done"]
    assert stages(events, "dashboard")[-1]["status"] == "failed"


async def test_dashboard_needs_a_table(db) -> None:
    SessionMemory("s2")
    chat = ChatRequest(session_id="s2", question="дашборд", mode="dashboard")
    events = [event async for event in agent(db, ScriptedLLM()).run(chat)]
    assert kinds(events) == ["error", "done"]


async def test_disconnect_cancels_the_build(db) -> None:
    llm = ScriptedLLM(DashboardOutline=[OUTLINE])
    chat = ChatRequest(session_id="s1", question="Продажи", mode="dashboard")
    stream = agent(db, llm).run(chat)
    await anext(stream)
    await stream.aclose()


def test_focus_puts_chosen_columns_first() -> None:
    rows = [{"a": 1, "b": "x", "c": 2}]
    choice = ChartChoice(tile_id=0, x="b", y="c", color="missing")
    assert list(dashboard_turn._focus(rows, choice)[0]) == ["b", "c", "a"]
    assert dashboard_turn._focus([], choice) == []
    assert dashboard_turn._rejection(choice, []) == "результат пуст"


def test_by_intent_falls_back_to_table() -> None:
    tile = OutlineTile(title="t", question="q", intent="trend")
    assert dashboard_turn._by_intent(0, tile, [{"a": "x"}]).chart_type == "table"


def test_unknown_values_are_normalized() -> None:
    assert OutlineTile(title="t", question="q", intent="Pie").intent == "compare"
    assert ChartChoice(tile_id=0, chart_type="pie").chart_type == "table"
    assert ChartChoice(tile_id=0, chart_type=" Line ").chart_type == "line"


async def test_ideas_are_cached_per_table(db) -> None:
    llm = ScriptedLLM(DashboardIdeas=[IDEAS])
    first = await ideas.generate_ideas(db, "raw", llm)
    again = await ideas.generate_ideas(db, "raw", llm)
    assert [idea.id for idea in first] == ["1", "2", "3", "4"]
    assert again is first
    assert "region" in llm.prompts["DashboardIdeas"][0]


async def test_empty_ideas_are_an_error(db) -> None:
    llm = ScriptedLLM(DashboardIdeas=[DashboardIdeas(ideas=[DashboardIdea(title=" ")])])
    with pytest.raises(ValueError):
        await ideas.generate_ideas(db, "raw", llm)


async def test_fake_llm_builds_a_dashboard_from_a_vague_request() -> None:
    fake = FakeLLM()
    outline = await fake.complete_structured("", "Запрос: дашборд\n", DashboardOutline)
    assert not outline.enough_info
    outline = await fake.complete_structured("", "Запрос: продажи по регионам\n", DashboardOutline)
    assert len(outline.tiles) == 4
    picked = await fake.complete_structured("", "--- плитка 0:\n--- плитка 3:", ChartChoices)
    assert [choice.tile_id for choice in picked.charts] == [0, 3]
    assert len((await fake.complete_structured("", "", DashboardIdeas)).ideas) == 4


def test_ideas_endpoint(db, monkeypatch) -> None:
    from app.api import deps
    from app.api.routes import sessions
    from app.main import app

    async def fake_ideas(_db, table: str, llm=None) -> list[DashboardIdea]:
        if table == "boom":
            raise RuntimeError("timeout")
        return IDEAS.ideas

    monkeypatch.setattr(sessions, "generate_ideas", fake_ideas)
    app.dependency_overrides[deps.get_db] = lambda: db
    try:
        client = TestClient(app)
        ok = client.post("/api/sessions/s1/dashboard-ideas")
        assert ok.status_code == 200
        assert len(ok.json()["ideas"]) == 4
        assert client.post("/api/sessions/nope/dashboard-ideas").status_code == 404
        SessionMemory("s2").set_title("пусто")
        assert client.post("/api/sessions/s2/dashboard-ideas").status_code == 409
        SessionMemory("s3").set_table("boom")
        failed = client.post("/api/sessions/s3/dashboard-ideas")
        assert failed.status_code == 502
        assert "timeout" in failed.json()["detail"]
    finally:
        app.dependency_overrides.clear()


# --- Вариант quick_tiles: шаги простых плиток из полей плана, без планировщика.

KINDS = {"region": "text", "day": "temporal", "amount": "numeric"}


class HybridExecutor(SqlExecutor):
    """Шаги планировщика-заглушки — по SQL, шаги из полей плитки — настоящими тулами."""

    def __init__(self, db: DuckDBManager) -> None:
        super().__init__(db)
        self.tools = StepExecutor(db)

    async def execute(self, step: ResearchStep, input_table: str, **kwargs: object) -> str:
        if "sql" in step.arguments:
            return await super().execute(step, input_table)
        return await self.tools.execute(step, input_table, **kwargs)


def quick_agent(db, llm: ScriptedLLM) -> tuple[AgentOrchestrator, SqlPlanner]:
    planner = SqlPlanner(llm)
    planner.fix_step = _no_fix
    orchestrator = AgentOrchestrator(
        planner=planner, executor=HybridExecutor(db), insight_gen=NoInsight(), db=db
    )
    return orchestrator, planner


def simple(title: str, **fields: object) -> OutlineTile:
    return OutlineTile(title=title, question="Выручка по регионам", kind="simple", **fields)


async def test_simple_tiles_skip_the_planner(db) -> None:
    outline = DashboardOutline(
        title="Продажи",
        tiles=[
            simple("Выручка", metric="amount", agg="SUM"),
            simple("Топ регионов", metric="amount", agg="SUM", by="region", top=2),
            simple("По дням", agg="COUNT", period="day", date_column="day"),
            simple(
                "Москва", metric="amount", agg="AVG", filter_column="region", filter_value="msk"
            ),
            OutlineTile(title="Сложная", question="Выручка по дням", kind="complex"),
        ],
    )
    llm = ScriptedLLM(DashboardOutline=[outline], ChartChoices=[RuntimeError("timeout")])
    orchestrator, planner = quick_agent(db, llm)
    events = await collect(orchestrator, question="Продажи по регионам и дням")

    assert kinds(events) == ["dashboard_outline", "dashboard", "text", "done"]
    # Планировщик позван только для сложной плитки.
    assert [context["next_step_id"] for context in planner.contexts] == [500]
    tiles = {tile.title: tile for tile in saved(events).tiles}
    assert len(tiles) == 5
    top = db.query(f"SELECT * FROM {tiles['Топ регионов'].result_table}")
    assert top == [{"region": "msk", "sum_amount": 12.5}, {"region": "ekb", "sum_amount": 7.0}]
    assert tiles["Выручка"].spec["usermeta"]["chart_type"] == "kpi"
    assert tiles["Москва"].snapshot_data is not None


async def test_failed_simple_tile_falls_back_to_the_planner(db) -> None:
    # Фильтр числа строкой падает в DuckDB — плитку строит планировщик.
    broken = simple("Сломано", agg="COUNT", filter_column="amount", filter_value="много")
    outline = DashboardOutline(
        title="x", tiles=[broken, simple("Выручка", metric="amount", agg="SUM")]
    )
    llm = ScriptedLLM(DashboardOutline=[outline], ChartChoices=[RuntimeError("timeout")])
    orchestrator, planner = quick_agent(db, llm)
    events = await collect(orchestrator, question="Продажи по регионам и дням")
    assert [context["next_step_id"] for context in planner.contexts] == [150]
    assert len(saved(events).tiles) == 2


def test_quick_plan_builds_steps_from_fields() -> None:
    tile = simple(
        "t",
        metric="amount",
        agg="SUM",
        by="region",
        top=3,
        filter_column="day",
        filter_value="2024-01-01",
    )
    plan = quick_plan(tile, KINDS, start=100)
    assert [(step.step_id, step.tool, step.input) for step in plan.steps] == [
        (100, "filter_rows", "$source"),
        (101, "group_by", "$prev"),
        (102, "sort_limit", "$prev"),
    ]
    assert plan.steps[2].arguments["order_by"] == "sum_amount"
    assert plan.result_step == 102
    trend = quick_plan(
        simple("t", agg="COUNT", by="region", period="month", date_column="day"), KINDS, 0
    )
    assert trend.steps[0].arguments["by"] == ["region"]


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "complex", "agg": "COUNT"},
        {"agg": None},
        {"agg": "COUNT", "by": "nope"},
        {"agg": "SUM"},
        {"agg": "SUM", "metric": "region"},
        {"agg": "COUNT", "period": "month", "date_column": "region"},
        {"agg": "COUNT", "filter_column": "region"},
        {"agg": "COUNT", "by": "region", "top": 0},
    ],
)
def test_quick_plan_rejects_fields_that_do_not_fit(fields) -> None:
    tile = OutlineTile(title="t", question="q", **{"kind": "simple", **fields})
    plan = quick_plan(tile, KINDS, 0)
    # top=0 — не ошибка: поле отбрасывается, sort_limit не добавляется.
    if fields.get("top") == 0:
        assert [step.tool for step in plan.steps] == ["group_by"]
    else:
        assert plan is None


def test_tile_fields_are_normalized() -> None:
    tile = OutlineTile(
        title="t", question="q", kind="Simple", agg="sum", period="Month", by=" ", top=500
    )
    assert (tile.kind, tile.agg, tile.period, tile.by, tile.top) == (
        "simple",
        "SUM",
        "month",
        None,
        None,
    )
    odd = OutlineTile(title="t", question="q", kind="tricky", agg="mode", period="decade")
    assert (odd.kind, odd.agg, odd.period) == ("complex", None, None)
