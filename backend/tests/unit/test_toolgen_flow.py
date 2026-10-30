"""End-to-end toolgen flow in the orchestrator and dashboard tiles."""

import pytest

from app.api.schemas.chat import ChatEvent, ChatRequest, ProgressPayload
from app.config import settings
from app.core.agent.dashboard_turn import DashboardTurn
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import PlanInvalid
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.core.tools.registry import tool_registry
from app.core.tools.toolgen.factory import ToolFactory
from app.models.dashboard_plan import OutlineTile
from app.models.generated_tool import GeneratedToolSpec
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep


def spec(name: str) -> GeneratedToolSpec:
    return GeneratedToolSpec(
        name=name,
        description="Count rows by a column",
        parameters=[{"name": "column", "type": "column"}],
        sql="SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        reference_sql="SELECT {column}, SUM(1) AS cnt FROM {input} GROUP BY {column}",
        checks=["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        sample_arguments=[{"name": "column", "value": "region"}],
    )


class ToolLLM:
    def __init__(self, *specs: GeneratedToolSpec) -> None:
        self.specs = list(specs)

    async def complete_structured(self, system: str, user: str, schema):
        return self.specs.pop(0)


class PlanScript:
    def __init__(self, *answers: ResearchPlan | Exception) -> None:
        self.answers = list(answers)
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class InsightGen:
    async def generate(self, question: str, result_table: str, **kwargs) -> Insight:
        return Insight(headline="Готово", details=["Строки посчитаны"], limits="")


@pytest.fixture(autouse=True)
def _cleanup_generated():
    yield
    tool_registry.unregister_generated()


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10), ('spb', 20), "
        "('msk', 30)) AS t(region, amount)"
    )
    SessionMemory("s1", db=manager).set_table("raw")
    yield manager
    manager.close()


def plan_for(name: str) -> ResearchPlan:
    return ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool=name,
                arguments={"column": "region"},
                purpose="Посчитать строки по регионам",
            )
        ],
        final_viz="bar",
    )


def agent(
    db: DuckDBManager,
    planner: PlanScript,
    tool_llm: ToolLLM,
) -> AgentOrchestrator:
    factory = ToolFactory(db=db, llm=tool_llm, registry=tool_registry, store=None)
    return AgentOrchestrator(planner=planner, insight_gen=InsightGen(), db=db, tool_factory=factory)


async def collect(agent: AgentOrchestrator, question: str = "вопрос") -> list[dict]:
    request = ChatRequest(session_id="s1", question=question)
    return [event async for event in agent.run(request)]


def stages(events: list[dict]) -> list[str]:
    return [
        f"{event['data']['stage']}:{event['data']['status']}"
        if event["type"] == "progress"
        else event["type"]
        for event in events
    ]


async def test_empty_plan_creates_tool_then_replans(db: DuckDBManager) -> None:
    name = "flow_count_regions"
    planner = PlanScript(ResearchPlan(steps=[]), plan_for(name))
    orch = agent(db, planner, ToolLLM(spec(name)))

    events = await collect(orch, "сколько строк по регионам")

    assert stages(events)[:6] == [
        "planning:started",
        "planning:finished",
        "toolgen:started",
        "toolgen:finished",
        "planning:started",
        "planning:finished",
    ]
    progress = [event["data"] for event in events if event["type"] == "progress"]
    finished = next(
        item for item in progress if item["stage"] == "toolgen" and item["status"] == "finished"
    )
    assert finished["tool"] == name
    assert len(planner.contexts[1]["created_tools"]) == 1
    assert "text" in stages(events)
    assert "chart" in stages(events)
    assert "done" in stages(events)
    assert tool_registry.get(name).kind == "transform"


async def test_unknown_tool_triggers_toolgen(db: DuckDBManager) -> None:
    name = "flow_unknown_to_created"
    planner = PlanScript(
        PlanInvalid(["шаг 0: неизвестный инструмент 'flow_unknown_to_created'"]),
        plan_for(name),
    )
    orch = agent(db, planner, ToolLLM(spec(name)))

    events = await collect(orch)

    progress = [event["data"] for event in events if event["type"] == "progress"]
    started = next(
        item for item in progress if item["stage"] == "toolgen" and item["status"] == "started"
    )
    assert started["tool"] == name
    assert "toolgen:finished" in stages(events)
    assert "done" in stages(events)


async def test_toolgen_disabled_keeps_old_empty_plan_behavior(
    db: DuckDBManager, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "toolgen_enabled", False)
    planner = PlanScript(ResearchPlan(steps=[]))
    orch = AgentOrchestrator(planner=planner, insight_gen=InsightGen(), db=db)

    events = await collect(orch)

    assert stages(events) == [
        "planning:started",
        "planning:finished",
        "clarification_needed",
    ]
    assert "toolgen:started" not in stages(events)


async def test_toolgen_failure_falls_back_to_clarification(db: DuckDBManager) -> None:
    planner = PlanScript(ResearchPlan(steps=[]))
    orch = agent(
        db,
        planner,
        ToolLLM(GeneratedToolSpec(feasible=False, reason="нет подходящих колонок")),
    )

    events = await collect(orch)

    assert stages(events)[-1] == "clarification_needed"
    assert "toolgen:failed" in stages(events)
    assert "done" not in stages(events)
    assert tool_registry.generated_tools() == []


async def test_toolgen_is_limited_per_turn(db: DuckDBManager, monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolgen_max_per_turn", 2)
    planner = PlanScript(*[ResearchPlan(steps=[]) for _ in range(3)])
    orch = agent(
        db,
        planner,
        ToolLLM(spec("flow_limit_a"), spec("flow_limit_b"), spec("flow_limit_c")),
    )

    events = await collect(orch)

    finished = [
        item
        for item in (event["data"] for event in events if event["type"] == "progress")
        if item["stage"] == "toolgen" and item["status"] == "finished"
    ]
    assert [item["tool"] for item in finished] == ["flow_limit_a", "flow_limit_b"]
    assert stages(events)[-1] == "clarification_needed"
    assert {tool.name for tool in tool_registry.generated_tools()} == {
        "flow_limit_a",
        "flow_limit_b",
    }


async def test_toolgen_progress_matches_schemas(db: DuckDBManager) -> None:
    name = "flow_schema_tool"
    planner = PlanScript(ResearchPlan(steps=[]), plan_for(name))
    orch = agent(db, planner, ToolLLM(spec(name)))

    events = await collect(orch)

    for event in events:
        ChatEvent(**event)
        if event["type"] == "progress":
            ProgressPayload(**event["data"])


async def test_dashboard_tile_is_built_after_toolgen(db: DuckDBManager) -> None:
    name = "flow_tile_tool"
    planner = PlanScript(ResearchPlan(steps=[]), plan_for(name))
    orch = agent(db, planner, ToolLLM(spec(name)))
    memory = SessionMemory("s1", db=db)
    memory.start_turn("Сколько строк по регионам?")
    events: list[dict] = []
    tile = OutlineTile(title="По регионам", question="Сколько строк по регионам?", intent="compare")

    execution = await DashboardTurn(orch)._tile(memory, 0, tile, events.append)

    assert execution is not None
    assert execution.result_table.startswith("temp_result_v")
    assert "toolgen:started" in stages(events)
    assert "toolgen:finished" in stages(events)
    assert "tile:started" in stages(events)
    assert "tile:finished" in stages(events)
    assert all(
        item.get("tile_id") == 0
        for item in (event["data"] for event in events if event["type"] == "progress")
    )
