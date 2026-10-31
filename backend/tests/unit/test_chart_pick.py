"""График ответа в чате: агент выбирает таблицу шага, вид и колонки."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.models.chart_choice import AnswerChart
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

BY_STORE = (
    "SELECT store_id, category, SUM(amount) AS revenue, SUM(qty) AS qty FROM raw "
    "WHERE store_id < 5 GROUP BY ALL ORDER BY store_id, category"
)
ALL_STORES = "SELECT store_id, SUM(amount) AS revenue FROM raw GROUP BY ALL ORDER BY store_id"
CORRELATION = "SELECT 0.42 AS r, 0.01 AS p_value"
TOTAL = "SELECT SUM(amount) AS revenue FROM raw"


class Planner:
    """План из шагов (tool, sql); llm не нужен — его роль играет chart_llm."""

    def __init__(self, *steps: tuple[str, str], final_viz: str = "auto") -> None:
        self.steps = steps
        self.final_viz = final_viz

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        steps = [
            ResearchStep(step_id=number, tool=tool, purpose=f"шаг {tool}", arguments={"sql": sql})
            for number, (tool, sql) in enumerate(self.steps, start=1)
        ]
        return ResearchPlan(steps=steps, final_viz=self.final_viz)


class SqlExecutor:
    def __init__(self, db: DuckDBManager) -> None:
        self.db = db

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        return self.db.save_step_result("raw", step.arguments["sql"])


class FixedInsight:
    async def generate(self, *_: object, **__: object) -> Insight:
        return Insight(headline="Ответ", details=[])


def answer(step_id: int | None, chart_type: str, **columns: object) -> AnswerChart:
    """Ответ модели: колонки, которых нет в columns, — явный null."""
    blank = dict.fromkeys(("x", "y", "color", "limit"))
    return AnswerChart(step_id=step_id, chart_type=chart_type, **{**blank, **columns})


class ChartLLM:
    """Ответы AnswerChart по очереди; исключение в очереди — бросается."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema: type) -> object:
        assert schema is AnswerChart
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT i % 60 AS store_id, "
        "CASE WHEN i // 60 % 2 = 0 THEN 'еда' ELSE 'техника' END AS category, "
        "(i * 7 % 100)::DOUBLE AS amount, i % 13 AS qty FROM range(400) t(i)"
    )
    SessionMemory("s1").set_table("raw")
    yield manager
    manager.close()


async def ask(db, planner: Planner, llm: ChartLLM, question: str = "Связь выручки и магазина"):
    agent = AgentOrchestrator(
        planner=planner, executor=SqlExecutor(db), insight_gen=FixedInsight(), db=db, chart_llm=llm
    )
    request = ChatRequest(session_id="s1", question=question)
    events = [event async for event in agent.run(request)]
    return next((event["data"] for event in events if event["type"] == "chart"), None)


async def test_chart_is_drawn_from_the_step_behind_the_statistics(db) -> None:
    llm = ChartLLM(answer(1, "bar", x="store_id", y="revenue", color="category"))
    planner = Planner(("group", BY_STORE), ("pearson_corr", CORRELATION))
    chart = await ask(db, planner, llm)

    spec = chart["spec"]
    assert spec["usermeta"]["chart_type"] == "bar"
    assert spec["encoding"]["x"]["field"] == "store_id"
    assert spec["encoding"]["x"]["type"] == "nominal"
    assert spec["encoding"]["color"]["field"] == "category"
    assert len(spec["data"]["values"]) == 10
    assert spec["title"] == "шаг group"
    assert chart["result_table"] == SessionMemory("s1").last_chart()["result_table"]
    # Модель видела обе таблицы: и шаг группировки, и строку стат-теста.
    assert "--- шаг 1: шаг group (group, transform)" in llm.prompts[0]
    assert "--- шаг 2: шаг pearson_corr (pearson_corr, analysis)" in llm.prompts[0]
    assert "store_id (quantitative, уникальных 5)" in llm.prompts[0]


async def test_rejected_choice_is_asked_again_with_the_reason(db) -> None:
    llm = ChartLLM(
        answer(1, "bar", x="store_id", y="revenue"),
        answer(1, "bar", x="store_id", y="revenue", limit=5),
    )
    chart = await ask(db, Planner(("group", ALL_STORES), ("group", TOTAL)), llm)

    assert "у 'store_id' 60 значений" in llm.prompts[1]
    revenue = [row["revenue"] for row in chart["spec"]["data"]["values"]]
    assert len(revenue) == 5
    assert revenue == sorted(revenue, reverse=True)


async def test_unknown_step_is_asked_again(db) -> None:
    llm = ChartLLM(
        answer(None, "bar"),
        answer(1, "line", x="store_id", y="revenue"),
    )
    chart = await ask(db, Planner(("group", ALL_STORES), ("group", TOTAL)), llm)
    assert "шага None нет среди таблиц" in llm.prompts[1]
    assert chart["spec"]["usermeta"]["chart_type"] == "line"


async def test_without_a_choice_the_old_selection_is_kept(db) -> None:
    llm = ChartLLM(RuntimeError("timeout"))
    planner = Planner(("group", BY_STORE), ("pearson_corr", CORRELATION))
    chart = await ask(db, planner, llm)
    # Как до выбора агентом: результат хода — строка стат-теста, таблица.
    assert chart["spec"]["usermeta"]["chart_type"] == "table"
    assert chart["spec"]["data"]["values"] == [{"r": 0.42, "p_value": 0.01}]


async def test_single_number_needs_no_choice(db) -> None:
    llm = ChartLLM()
    chart = await ask(db, Planner(("group", TOTAL)), llm, question="Сколько всего выручки")
    assert llm.prompts == []
    assert chart["spec"]["usermeta"]["chart_type"] == "kpi"


async def test_no_chart_plan_needs_no_choice(db) -> None:
    llm = ChartLLM()
    planner = Planner(("group", ALL_STORES), ("group", TOTAL), final_viz="none")
    assert await ask(db, planner, llm) is None
    assert llm.prompts == []


async def test_planned_view_is_a_hint(db) -> None:
    llm = ChartLLM(answer(1, "line", x="store_id", y="revenue"))
    await ask(db, Planner(("group", ALL_STORES), ("group", TOTAL), final_viz="line"), llm)
    assert "Планировщик заранее предложил вид: line." in llm.prompts[0]


async def test_failed_pick_query_falls_back_instead_of_failing_the_turn(db, monkeypatch) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("disk")

    llm = ChartLLM(answer(1, "bar", x="store_id", y="revenue", limit=3))
    monkeypatch.setattr("app.core.agent.chart_pick.top_rows", broken)
    chart = await ask(db, Planner(("group", BY_STORE), ("pearson_corr", CORRELATION)), llm)
    assert chart["spec"]["usermeta"]["chart_type"] == "table"


class Thinking(ChartLLM):
    """Первые slow вызовов модель «думает» дольше срока, дальше отвечает по очереди."""

    def __init__(self, slow: int, *answers: object) -> None:
        super().__init__(*answers)
        self.slow = slow
        self.calls = 0

    async def complete_structured(self, system: str, user: str, schema: type) -> object:
        self.calls += 1
        if self.calls <= self.slow:
            await asyncio.sleep(10)
        return await super().complete_structured(system, user, schema)


async def test_slow_choice_is_asked_again(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "chart_choice_timeout_s", 0.05)
    llm = Thinking(1, answer(1, "bar", x="store_id", y="revenue", color="category"))
    planner = Planner(("group", BY_STORE), ("pearson_corr", CORRELATION))
    chart = await ask(db, planner, llm)
    assert llm.calls == 2
    assert chart["spec"]["usermeta"]["chart_type"] == "bar"


async def test_choice_silent_twice_falls_back(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "chart_choice_timeout_s", 0.05)
    llm = Thinking(2)
    planner = Planner(("group", BY_STORE), ("pearson_corr", CORRELATION))
    chart = await ask(db, planner, llm)
    assert llm.calls == 2
    assert chart["spec"]["usermeta"]["chart_type"] == "table"
