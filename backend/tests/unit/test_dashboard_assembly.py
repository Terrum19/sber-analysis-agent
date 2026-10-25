"""Оркестратор: дашборд по плиткам плана (B-04)."""

import json

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator, _arrange
from app.core.dashboards.models import DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import DashboardTilePlan, ResearchPlan, ResearchStep

STEP_SQL = {
    0: "SELECT region, SUM(amount) AS revenue FROM raw GROUP BY region ORDER BY region",
    1: "SELECT SUM(amount) AS revenue FROM raw",
    2: "SELECT region, COUNT(*) AS orders FROM raw GROUP BY region ORDER BY region",
}
TILES = [
    DashboardTilePlan(title="Выручка по регионам", step_id=0, insight_needed=True),
    DashboardTilePlan(title="Выручка", step_id=1, is_kpi=True),
    DashboardTilePlan(title="Заказы по регионам", step_id=2, chart_type="table"),
]


class TilePlanner:
    def __init__(self, tiles: list[DashboardTilePlan], template_id: str | None = None) -> None:
        self.tiles = tiles
        self.template_id = template_id

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        return ResearchPlan(
            steps=[ResearchStep(step_id=step_id, tool="group") for step_id in STEP_SQL],
            final_viz="bar",
            result_step=0,
            dashboard_requested=True,
            dashboard_title="Регионы",
            template_id=self.template_id,
            dashboard_tiles=self.tiles,
        )


class SqlExecutor:
    def __init__(self, db: DuckDBManager, failing: int | None = None) -> None:
        self.db = db
        self.failing = failing

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        if step.step_id == self.failing:
            raise RuntimeError("broken step")
        return self.db.save_step_result("raw", STEP_SQL[step.step_id])


class TileInsight:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.tiles: list[tuple[str, str, str]] = []

    async def generate(self, question: str, result_table: str, **_: object) -> Insight:
        return Insight(headline="Москва лидирует", details=[])

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        self.tiles.append((title, result_table, chart_type))
        if self.failing:
            raise RuntimeError("LLM timeout")
        return "Москва даёт больше выручки."


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), ('msk', 2.5)) "
        "AS rows(region, amount)"
    )
    SessionMemory("s1").set_table("raw", manager.columns("raw"))
    yield manager
    manager.close()


def orchestrator(db, planner, insight=None, failing=None) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=planner,
        executor=SqlExecutor(db, failing),
        insight_gen=insight or TileInsight(),
        db=db,
    )


async def collect(agent: AgentOrchestrator, **request: str) -> list[dict]:
    chat = ChatRequest(session_id="s1", question="Сделай дашборд по регионам", **request)
    return [event async for event in agent.run(chat) if event["type"] != "progress"]


def saved_dashboard(events: list[dict]):
    sent = next(event["data"] for event in events if event["type"] == "dashboard")
    return DashboardStore().get(sent["dashboard_id"])


def test_arrange_puts_kpis_first_and_charts_two_per_row():
    tiles = [DashboardTile(title=f"kpi {number}", is_kpi=True) for number in range(5)]
    tiles[1:1] = [DashboardTile(title=f"chart {number}") for number in range(3)]
    layouts = {tile.title: tile.layout for tile in _arrange(tiles)}
    assert [tile.title for tile in _arrange(tiles)][:5] == [f"kpi {number}" for number in range(5)]
    assert layouts["kpi 0"] == {"x": 0, "y": 0, "w": 3, "h": 2}
    assert layouts["kpi 3"] == {"x": 9, "y": 0, "w": 3, "h": 2}
    assert layouts["kpi 4"] == {"x": 0, "y": 2, "w": 3, "h": 2}
    assert layouts["chart 0"] == {"x": 0, "y": 4, "w": 6, "h": 4}
    assert layouts["chart 1"] == {"x": 6, "y": 4, "w": 6, "h": 4}
    assert layouts["chart 2"] == {"x": 0, "y": 8, "w": 6, "h": 4}
    assert tiles[0].layout == {"x": 0, "y": 0, "w": 6, "h": 4}


async def test_dashboard_follows_the_plan_tiles(db) -> None:
    insight = TileInsight()
    agent = orchestrator(db, TilePlanner(TILES, template_id="sales_quarterly"), insight)
    events = await collect(agent)
    assert [event["type"] for event in events] == ["text", "chart", "dashboard", "done"]
    dashboard = saved_dashboard(events)
    assert dashboard.title == "Регионы"
    assert dashboard.template_id == "sales_quarterly"
    assert [tile.title for tile in dashboard.tiles] == [
        "Выручка",
        "Выручка по регионам",
        "Заказы по регионам",
    ]
    kpi, revenue, orders = dashboard.tiles
    assert kpi.spec["usermeta"]["chart_type"] == "kpi"
    assert kpi.snapshot_data == [{"revenue": 16.5}]
    assert revenue.spec["title"] == "Выручка по регионам"
    assert revenue.insight == "Москва даёт больше выручки."
    assert orders.spec["usermeta"]["chart_type"] == "table"
    assert [tile.source_step_id for tile in dashboard.tiles] == [1, 0, 2]
    assert [title for title, _, _ in insight.tiles] == ["Выручка по регионам"]
    assert insight.tiles[0][2] == "bar"


async def test_tile_insight_failure_keeps_the_tile(db) -> None:
    agent = orchestrator(db, TilePlanner(TILES), TileInsight(failing=True))
    dashboard = saved_dashboard(await collect(agent))
    assert len(dashboard.tiles) == 3
    assert all(tile.insight is None for tile in dashboard.tiles)


async def test_plan_without_usable_tiles_falls_back_to_the_answer_chart(db) -> None:
    lost = [DashboardTilePlan(title="Нет шага", step_id=9)]
    agent = orchestrator(db, TilePlanner(lost, template_id="sales_quarterly"))
    events = await collect(agent)
    dashboard = saved_dashboard(events)
    assert dashboard.template_id is None
    assert len(dashboard.tiles) == 1
    chart = next(event["data"] for event in events if event["type"] == "chart")
    assert dashboard.tiles[0].spec == chart["spec"]
    assert dashboard.tiles[0].insight == "Москва лидирует"


async def test_skipped_step_drops_only_its_tile(db) -> None:
    agent = orchestrator(db, TilePlanner(TILES), failing=1)
    events = await collect(agent)
    assert [event["type"] for event in events] == ["clarification_needed"]
    agent.executor.failing = None
    skip = json.dumps({"action": "skip_step"})
    dashboard = saved_dashboard(await collect(agent, recovery_response=skip))
    assert [tile.source_step_id for tile in dashboard.tiles] == [0, 2]
    assert dashboard.title == "Регионы"
