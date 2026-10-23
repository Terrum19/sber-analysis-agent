"""Report tools leave the working table to the next research step."""

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.step_executor import StepExecutor, tool_kind
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep


class FixedPlanner:
    def __init__(self, steps: list[ResearchStep]) -> None:
        self.steps = steps

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        return ResearchPlan(steps=self.steps, final_viz="table")


class RecordingInsight:
    def __init__(self) -> None:
        self.tables: list[str] = []

    async def generate(self, question: str, result_table: str, **kwargs: object) -> Insight:
        self.tables.append(result_table)
        return Insight(headline="готово", details=[])


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES "
        "(10.0, 3.0, 'a'), (12.0, 4.0, 'a'), (30.0, 9.0, 'b'), (33.0, 11.0, 'b')) "
        "AS rows(price, qty, product)"
    )
    yield manager
    manager.close()


def test_report_tools_do_not_transform_data() -> None:
    assert tool_kind(ResearchStep(tool="t_test_ind")) == "analysis"
    assert tool_kind(ResearchStep(tool="search_categorical_values")) == "lookup"
    assert tool_kind(ResearchStep(tool="create_column")) == "transform"
    assert tool_kind(ResearchStep(tool="unknown_tool")) == "transform"
    assert tool_kind(ResearchStep(tool=None)) == "transform"


async def test_steps_after_a_report_still_see_the_data(db) -> None:
    steps = [
        ResearchStep(step_id=1, tool="create_column", columns=["revenue", "price * qty"]),
        ResearchStep(step_id=2, tool="t_test_ind", columns=["revenue", "product"]),
        ResearchStep(step_id=3, tool="bootstrap_ci", columns=["revenue"]),
        ResearchStep(step_id=4, tool="pearson_corr", columns=["price", "qty"]),
    ]
    insight = RecordingInsight()
    SessionMemory("chain").set_table("raw", db.columns("raw"))
    agent = AgentOrchestrator(
        planner=FixedPlanner(steps), executor=StepExecutor(db), insight_gen=insight
    )
    events = [
        event async for event in agent.run(ChatRequest(session_id="chain", question="выручка"))
    ]
    assert [event["type"] for event in events if event["type"] == "error"] == []
    assert insight.tables == ["temp_result_v4"]
    assert set(db.to_records("temp_result_v4")[0]) == {"correlation", "p_value", "n"}
