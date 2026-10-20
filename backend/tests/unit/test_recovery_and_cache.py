"""Исправление упавшего шага по тексту ошибки и кеш результатов шагов."""

import asyncio

import pytest

from app.api.schemas.chat import ChatEvent, ChatRequest, ProgressPayload
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.recovery import STEP_FIX_ATTEMPTS, fixable
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.sandbox.wasm_runtime import SandboxError
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep


class ScriptedLLM:
    """LLM с ответами по очереди для каждой схемы; исключение в очереди — бросается."""

    def __init__(self, plan: ResearchPlan, fixes: list[ResearchStep | Exception]) -> None:
        self.answers = {"ResearchPlan": [plan], "ResearchStep": list(fixes)}
        self.prompts: dict[str, list[str]] = {"ResearchPlan": [], "ResearchStep": []}

    async def complete_structured(self, system: str, user: str, schema: type) -> object:
        self.prompts[schema.__name__].append(user)
        answer = self.answers[schema.__name__].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeInsight:
    async def generate(self, question: str, result_table: str, **_: object) -> Insight:
        return Insight(headline="Москва лидирует", details=["msk > spb"])


def group_by(column: str, **changes: object) -> ResearchStep:
    step = {
        "tool": "group_by",
        "arguments": {"by": ["region"], "column": column, "function": "SUM"},
        "purpose": "Выручка по регионам",
    }
    return ResearchStep(**{**step, **changes})


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), ('msk', 2.5)) "
        "AS rows(region, revenue)"
    )
    yield manager
    manager.close()


def make_agent(db: DuckDBManager, llm: ScriptedLLM) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=StepPlanner(llm),
        executor=StepExecutor(db),
        insight_gen=FakeInsight(),
        db=db,
    )


async def collect(agent: AgentOrchestrator) -> list[dict]:
    request = ChatRequest(session_id="s1", question="Выручка по регионам")
    return [event async for event in agent.run(request)]


def kinds(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


async def test_wrong_column_is_fixed_by_planner(db) -> None:
    plan = ResearchPlan(steps=[group_by("revenu")], final_viz="bar")
    llm = ScriptedLLM(plan, [group_by("revenue", step_id=7, purpose="")])
    events = await collect(make_agent(db, llm))

    assert kinds(events) == ["progress", "text", "chart", "done"]
    progress = events[0]["data"]
    assert progress["stage"] == "step"
    assert progress["status"] == "fixing"
    assert progress["step_id"] == 0
    assert progress["attempt"] == 1
    assert "revenu" in progress["error"]
    ChatEvent(**events[0])
    ProgressPayload(**progress)
    # Планировщик видит ошибку и колонки входной таблицы шага.
    prompt = llm.prompts["ResearchStep"][0]
    assert "Candidate bindings" in prompt
    assert "revenue: DECIMAL(3,1)" in prompt
    assert SessionMemory("s1").get_pending_recovery() is None
    step = SessionMemory("s1")._state["steps"][0]
    # Исправленный шаг сохраняет номер и назначение исходного.
    assert (step["step_id"], step["purpose"]) == (0, "Выручка по регионам")


async def test_unfixed_step_asks_the_user(db) -> None:
    plan = ResearchPlan(steps=[group_by("revenu")], final_viz="bar")
    llm = ScriptedLLM(plan, [group_by("revenu2"), group_by("revenu3")])
    events = await collect(make_agent(db, llm))

    assert kinds(events) == ["progress", "progress", "clarification_needed"]
    assert [event["data"]["attempt"] for event in events[:2]] == [1, 2]
    assert "revenu3" in events[-1]["data"]["error"]
    pending = SessionMemory("s1").get_pending_recovery()
    assert pending["attempts_made"] == STEP_FIX_ATTEMPTS + 1 == 3
    assert pending["plan_steps"][0]["arguments"]["column"] == "revenu3"


async def test_invalid_fix_counts_as_attempt(db) -> None:
    plan = ResearchPlan(steps=[group_by("revenu")], final_viz="bar")
    unknown = ResearchStep(tool="no_such_tool", purpose="Выручка по регионам")
    llm = ScriptedLLM(plan, [unknown, group_by("revenue")])
    events = await collect(make_agent(db, llm))

    assert kinds(events) == ["progress", "progress", "text", "chart", "done"]
    # Вторая попытка получает ошибку валидации первой.
    assert "неизвестный инструмент 'no_such_tool'" in llm.prompts["ResearchStep"][1]


async def test_unavailable_planner_stops_fixing(db) -> None:
    plan = ResearchPlan(steps=[group_by("revenu")], final_viz="bar")
    llm = ScriptedLLM(plan, [ConnectionError("LLM недоступна")])
    events = await collect(make_agent(db, llm))

    assert kinds(events) == ["progress", "clarification_needed"]
    # Пользователь видит ошибку шага, а не сбой LLM.
    assert "revenu" in events[-1]["data"]["error"]
    assert SessionMemory("s1").get_pending_recovery()["attempts_made"] == 2


async def test_broken_input_reference_shows_previous_columns(db) -> None:
    plan = ResearchPlan(steps=[group_by("revenue", input="$step_9")], final_viz="bar")
    llm = ScriptedLLM(plan, [group_by("revenue")])
    # Переспрос планировщика ссылку не исправил — шаг падает при выполнении.
    llm.answers["ResearchPlan"].append(plan)
    events = await collect(make_agent(db, llm))

    assert kinds(events) == ["progress", "text", "chart", "done"]
    assert "region: VARCHAR" in llm.prompts["ResearchStep"][0]


async def test_disconnect_cancels_the_running_plan(db) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    class HangingPlanner(StepPlanner):
        async def fix_step(self, *_: object) -> ResearchStep:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    plan = ResearchPlan(steps=[group_by("revenu")], final_viz="bar")
    agent = make_agent(db, ScriptedLLM(plan, []))
    agent.planner = HangingPlanner(agent.planner.llm)
    stream = agent.run(ChatRequest(session_id="s1", question="Выручка по регионам"))
    assert (await anext(stream))["type"] == "progress"
    await started.wait()
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)


def test_fixable_errors() -> None:
    step = group_by("revenue")
    assert fixable(step, ValueError("bad column"))
    assert not fixable(step, NotImplementedError("unsupported"))
    assert not fixable(step, SandboxError("crash"))
    # Код кодогенерации исправляет сам раннер песочницы.
    codegen = ResearchStep(needs_llm_codegen=True, purpose="медиана окна")
    assert not fixable(codegen, ValueError("bad column"))


def test_state_cache_persists_step_result() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT * FROM (VALUES (1), (2)) AS rows(value)")
    cache = StateCache(db)
    table = cache.put("raw", "SELECT value * 2 AS doubled FROM raw")
    assert table == "temp_result_v1"
    assert cache.get(table) == [{"doubled": 2}, {"doubled": 4}]
    assert db.query(f"SELECT * FROM {table}") == [{"doubled": 2}, {"doubled": 4}]
    db.close()
