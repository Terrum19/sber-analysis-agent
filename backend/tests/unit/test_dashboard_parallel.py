"""Плитки дашборда: LLM-выводы параллельны и не роняют дашборд."""

import asyncio
import time

from app.core.agent.orchestrator import AgentOrchestrator, Execution
from app.core.dashboards.models import DashboardTile
from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import DashboardTilePlan, ResearchPlan, ResearchStep

TILES = [
    DashboardTilePlan(title="Выручка", step_id=0, insight_needed=True),
    DashboardTilePlan(title="Заказы", step_id=1, insight_needed=True),
    DashboardTilePlan(title="Регионы", step_id=2, insight_needed=True),
]


class SlowInsight:
    def __init__(self) -> None:
        self.titles: list[str] = []

    async def generate(self, question: str, result_table: str, **_: object):  # pragma: no cover
        raise AssertionError("плитки не должны звать общий инсайт")

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        self.titles.append(title)
        await asyncio.sleep(0.12)
        return f"{title}: готово"


def make_agent(db: DuckDBManager, insight: SlowInsight) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=object(),
        executor=object(),
        insight_gen=insight,
        db=db,
    )


def execution() -> Execution:
    return Execution(
        result_table="temp_result_v3",
        tables={0: "temp_result_v1", 1: "temp_result_v2", 2: "temp_result_v3"},
        plan=ResearchPlan(
            steps=[
                ResearchStep(step_id=0, tool="group_by"),
                ResearchStep(step_id=1, tool="group_by"),
                ResearchStep(step_id=2, tool="group_by"),
            ],
            dashboard_requested=True,
            dashboard_tiles=TILES,
        ),
    )


async def test_tile_insights_run_in_parallel_and_keep_order() -> None:
    db = DuckDBManager(":memory:")
    for number in (1, 2, 3):
        db.execute(f"CREATE TABLE temp_result_v{number} AS SELECT {number} AS value")
    insight = SlowInsight()
    agent = make_agent(db, insight)
    fallback = DashboardTile(title="fallback", spec={"usermeta": {"chart_type": "table"}})
    started = time.perf_counter()
    event = await agent._build_dashboard("s1", "дашборд", execution(), {}, fallback)
    elapsed = time.perf_counter() - started
    assert event["type"] == "dashboard"
    assert [tile["title"] for tile in event["data"]["tiles"]] == ["Выручка", "Заказы", "Регионы"]
    assert insight.titles == ["Выручка", "Заказы", "Регионы"]
    assert elapsed < 0.30
    db.close()


async def test_one_failing_tile_does_not_lose_the_others() -> None:
    class Failing(SlowInsight):
        async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
            if title == "Заказы":
                raise RuntimeError("LLM timeout")
            return await super().generate_tile_insight(title, result_table, chart_type)

    db = DuckDBManager(":memory:")
    for number in (1, 2, 3):
        db.execute(f"CREATE TABLE temp_result_v{number} AS SELECT {number} AS value")
    fallback = DashboardTile(title="fallback", spec={"usermeta": {"chart_type": "table"}})
    event = await make_agent(db, Failing())._build_dashboard("s1", "дашборд", execution(), {}, fallback)
    tiles = event["data"]["tiles"]
    assert [tile["title"] for tile in tiles] == ["Выручка", "Заказы", "Регионы"]
    assert tiles[1].get("insight") is None
    db.close()
