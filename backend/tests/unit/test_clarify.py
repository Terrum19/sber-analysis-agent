"""Глубокое исследование (A-28): уточняющие вопросы до хода и ответы в ходе."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.chat import ChatRequest
from app.core.agent import clarify
from app.core.agent.clarify import ClarifyFailed, clarify_questions
from app.core.agent.explainer import describe_journal
from app.core.agent.insight import describe_clarifications as insight_block
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner, clarified, describe_turns
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory import session_bundle
from app.core.memory.session_memory import SessionMemory
from app.core.tools.base import ToolContext
from app.core.tools.registry import tool_registry
from app.models.clarify import ClarifyAnswer, ClarifyOption, ClarifyQuestion
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

ANSWERS = [
    {"question": "Зачем вам анализ?", "answer": "Найти просадку", "axis": "goal"},
    {"question": "Какой период?", "answer": "январь 2024", "axis": "period"},
]
REVENUE = ResearchStep(
    step_id=0,
    tool="group_by",
    arguments={"by": ["region"], "column": "amount", "function": "SUM"},
    purpose="Выручка по регионам",
)


def question(axis: str, *options: tuple[str, list[str]]) -> ClarifyQuestion:
    return ClarifyQuestion(
        axis=axis,
        text=f"Вопрос {axis}?",
        options=[ClarifyOption(text=text, columns=columns) for text, columns in options],
    )


GOAL = question("goal", ("Найти просадку", []), ("Сравнить", []))
REGION = question("breakdown", ("По регионам", ["region"]), ("По дням", ["day"]))
FORMAT = question("format", ("Одно число", []), ("Рейтинг", []))
INVENTED = question("metric", ("Прибыль", ["profit"]), ("Сумма", ["amount"]))


class ScriptedLLM:
    """Ответы по очереди; исключение в очереди — бросается."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema: type) -> object:
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES "
        "('msk', DATE '2024-01-01', 10.0), ('spb', DATE '2024-01-02', 4.0)) "
        "AS rows(region, day, amount)"
    )
    SessionMemory("s1").set_table("raw")
    yield manager
    manager.close()


def ask(db, llm, text: str = "проанализируй продажи", reply_to: str | None = None):
    return asyncio.run(clarify_questions(db, SessionMemory("s1"), text, reply_to, llm=llm))


async def test_fake_llm_asks_only_about_a_broad_question() -> None:
    fake = FakeLLM()
    clear = await fake.complete_structured(
        "", "Вопрос пользователя: выручка за 2025\n", ClarifyAnswer
    )
    assert clear.enough_info and clear.questions == []
    broad = await fake.complete_structured(
        "", "Вопрос пользователя: проанализируй продажи\ncategory", ClarifyAnswer
    )
    assert not broad.enough_info
    assert len(broad.questions) == 3
    assert all(len(item.options) >= 2 for item in broad.questions)
    assert broad.questions[1].options[0].columns == ["category"]


def test_clear_question_needs_no_questions(db) -> None:
    llm = ScriptedLLM(ClarifyAnswer(enough_info=True, questions=[GOAL]))
    assert ask(db, llm, "выручка msk по дням за январь 2024") == []


def test_small_talk_and_overview_skip_the_model(db) -> None:
    llm = ScriptedLLM()
    for text in ("привет", "что передо мной?", "сделай таблицей"):
        assert ask(db, llm, text) == []
    assert llm.prompts == []


def test_broad_question_gets_questions_with_options(db) -> None:
    llm = ScriptedLLM(ClarifyAnswer(questions=[GOAL, REGION, FORMAT]))
    questions = ask(db, llm)
    assert [item.axis for item in questions] == ["goal", "breakdown", "format"]
    assert all(len(item.options) >= 2 for item in questions)
    prompt = llm.prompts[0]
    assert "проанализируй продажи" in prompt
    assert "region" in prompt


def test_option_with_invented_column_is_dropped(db) -> None:
    broken = question("metric", ("Прибыль", ["profit"]), ("Сумма", ["amount"]), ("Дни", ["DAY"]))
    llm = ScriptedLLM(ClarifyAnswer(questions=[GOAL, broken, FORMAT]))
    questions = ask(db, llm)
    assert [option.text for option in questions[1].options] == ["Сумма", "Дни"]


def test_question_left_with_one_option_is_dropped_and_retried(db) -> None:
    llm = ScriptedLLM(
        ClarifyAnswer(questions=[GOAL, INVENTED, FORMAT]),
        ClarifyAnswer(questions=[GOAL, REGION, FORMAT]),
    )
    questions = ask(db, llm)
    assert [item.axis for item in questions] == ["goal", "breakdown", "format"]
    assert "profit" in llm.prompts[1]
    assert "годных вопросов 2" in llm.prompts[1]


def test_duplicate_axis_and_duplicate_option_are_dropped(db) -> None:
    twice = question("goal", ("Найти просадку", []), ("найти просадку", []), ("Прогноз", []))
    llm = ScriptedLLM(ClarifyAnswer(questions=[twice, GOAL, REGION, FORMAT]))
    questions = ask(db, llm)
    assert [item.axis for item in questions] == ["goal", "breakdown", "format"]
    assert [option.text for option in questions[0].options] == ["Найти просадку", "Прогноз"]


def test_model_failure_is_retried_with_the_reason(db) -> None:
    llm = ScriptedLLM(RuntimeError("timeout"), ClarifyAnswer(questions=[GOAL, REGION, FORMAT]))
    assert len(ask(db, llm)) == 3
    assert "timeout" in llm.prompts[1]


def test_two_questions_are_enough_after_all_attempts(db) -> None:
    short = ClarifyAnswer(questions=[GOAL, REGION])
    llm = ScriptedLLM(short, RuntimeError("timeout"), ClarifyAnswer(questions=[GOAL]))
    questions = ask(db, llm)
    assert [item.axis for item in questions] == ["goal", "breakdown"]
    assert len(llm.prompts) == clarify.ATTEMPTS


def test_no_questions_after_all_attempts_fails(db) -> None:
    llm = ScriptedLLM(ClarifyAnswer(), ClarifyAnswer(questions=[GOAL]), RuntimeError("timeout"))
    with pytest.raises(ClarifyFailed, match="timeout"):
        ask(db, llm)


def test_prompt_has_past_turns_their_clarifications_reply_and_memories(db) -> None:
    memory = SessionMemory("s1")
    turn = memory.start_turn("выручка по регионам", clarifications=ANSWERS)
    memory.finish_turn("ok", headline="msk впереди")
    memory.remember("выручка — без НДС")
    llm = ScriptedLLM(ClarifyAnswer(enough_info=True))
    ask(db, llm, "а теперь по дням", reply_to=turn)
    prompt = llm.prompts[0]
    assert "Вопрос продолжает прошлый ход" in prompt
    assert "Ход 1. Вопрос: выручка по регионам" in prompt
    assert "Какой период? — январь 2024" in prompt
    assert "msk впереди" in prompt
    assert "выручка — без НДС" in prompt


def test_clarify_endpoint(db, monkeypatch) -> None:
    from app.api import deps
    from app.api.routes import sessions
    from app.main import app

    async def fake_clarify(_db, memory, text, reply_to=None, llm=None):
        if memory.table == "boom":
            raise ClarifyFailed("timeout")
        return [] if "2024" in text else [GOAL, REGION]

    monkeypatch.setattr(sessions, "clarify_questions", fake_clarify)
    app.dependency_overrides[deps.get_db] = lambda: db
    try:
        client = TestClient(app)
        clear = client.post("/api/sessions/s1/clarify", json={"question": "выручка за 2024"})
        assert clear.json() == {"enough_info": True, "questions": []}
        broad = client.post("/api/sessions/s1/clarify", json={"question": "продажи"}).json()
        assert not broad["enough_info"]
        assert broad["questions"][1] == {
            "id": "2",
            "axis": "breakdown",
            "text": "Вопрос breakdown?",
            "options": ["По регионам", "По дням"],
            "multi": False,
        }
        assert client.post("/api/sessions/nope/clarify", json={"question": "x"}).status_code == 404
        SessionMemory("s2").set_title("пусто")
        assert client.post("/api/sessions/s2/clarify", json={"question": "x"}).status_code == 409
        SessionMemory("s3").set_table("boom")
        failed = client.post("/api/sessions/s3/clarify", json={"question": "x"})
        assert failed.status_code == 502
        assert "timeout" in failed.json()["detail"]
    finally:
        app.dependency_overrides.clear()


class RecordingPlanner:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        return ResearchPlan(steps=[REVENUE], final_viz="none")


class RecordingInsight:
    def __init__(self) -> None:
        self.clarifications: list[list[dict]] = []

    async def generate(self, question, result_table, clarifications=None, **_) -> Insight:
        self.clarifications.append(list(clarifications or []))
        return Insight(headline="готово", details=[])


def run_turn(agent: AgentOrchestrator, **fields: object) -> list[dict]:
    async def collect() -> list[dict]:
        request = ChatRequest(session_id="s1", question="проанализируй продажи", **fields)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect())


def test_clarifications_reach_plan_insight_and_turn_journal(db) -> None:
    planner, insight = RecordingPlanner(), RecordingInsight()
    agent = AgentOrchestrator(
        planner=planner, executor=StepExecutor(db=db), insight_gen=insight, db=db
    )
    events = run_turn(agent, clarifications=ANSWERS)
    assert events[-1]["type"] == "done"
    assert planner.contexts[0]["clarifications"] == ANSWERS
    assert insight.clarifications == [ANSWERS]
    assert SessionMemory("s1").turns[0]["clarifications"] == ANSWERS

    run_turn(agent)
    assert planner.contexts[1]["clarifications"] == []
    assert insight.clarifications[1] == []
    assert "clarifications" not in SessionMemory("s1").turns[1]
    assert planner.contexts[1]["turns"][0]["clarifications"] == ANSWERS


def test_no_turn_has_no_clarifications() -> None:
    assert SessionMemory("empty").clarifications == []


class PromptLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema: type) -> ResearchPlan:
        self.prompts.append(user)
        return ResearchPlan(route="answer", steps=[], final_viz="none")


async def test_planner_prompt_has_clarifications_block() -> None:
    llm = PromptLLM()
    await StepPlanner(llm=llm).plan("продажи", {"columns": ["region"], "clarifications": ANSWERS})
    prompt = llm.prompts[0]
    assert "Перед исследованием пользователь уточнил задачу" in prompt
    assert "- Зачем вам анализ? — Найти просадку (цель пользователя)" in prompt
    await StepPlanner(llm=llm).plan("продажи", {"columns": ["region"]})
    assert "уточнил задачу" not in llm.prompts[1]


def test_columns_are_chosen_by_question_and_answers() -> None:
    assert clarified("продажи", ANSWERS) == "продажи Найти просадку январь 2024"
    assert clarified("продажи", []) == "продажи"


def test_insight_states_the_goal() -> None:
    block = insight_block(ANSWERS)
    assert "- Какой период? — январь 2024" in block
    assert "Цель пользователя: Найти просадку" in block
    assert "Цель пользователя" not in insight_block(ANSWERS[1:])
    assert insight_block([]) == ""


def test_past_turn_clarifications_are_in_history_views(db) -> None:
    turn = {"number": 1, "question": "продажи", "steps": [], "clarifications": ANSWERS}
    assert "Уточнения: Зачем вам анализ? — Найти просадку (цель)" in describe_turns([turn])
    assert "Уточнения: Зачем вам анализ?" in describe_journal([turn], None)

    memory = SessionMemory("s1")
    memory.start_turn("продажи", clarifications=ANSWERS)
    memory.finish_turn("ok", headline="готово")
    memory.start_turn("что я уточнял?")
    tool = tool_registry.get("read_history")
    table = asyncio.run(tool.arun(ToolContext(history=memory), db, "raw", None, turn=1))
    found = db.to_records(table)[0]["question"]
    assert (
        found
        == "продажи (уточнения: Зачем вам анализ? — Найти просадку (цель); Какой период? — январь 2024)"
    )


def test_clarifications_travel_in_the_session_bundle() -> None:
    turn = {
        "turn_id": "t1",
        "question": "продажи",
        "status": "ok",
        "steps": [],
        "clarifications": ANSWERS,
    }
    exported = session_bundle.BundleTurn.model_validate(session_bundle.exported_turn(turn))
    assert session_bundle.imported_turn(exported)["clarifications"] == ANSWERS


def test_runaway_question_text_is_cut_in_the_retry_prompt(db) -> None:
    runaway = ClarifyQuestion(axis="goal", text="Какая цель? " + "текст промпта " * 50, options=[])
    llm = ScriptedLLM(
        ClarifyAnswer(questions=[runaway]), ClarifyAnswer(questions=[GOAL, REGION, FORMAT])
    )
    assert len(ask(db, llm)) == 3
    retry = llm.prompts[1].split("Твой прошлый ответ не подошёл")[1]
    assert "Какая цель?" in retry
    assert len(retry) < 400


def test_schema_requires_options_and_columns() -> None:
    schema = ClarifyAnswer.model_json_schema()["$defs"]
    assert schema["ClarifyQuestion"]["required"] == ["axis", "text", "options", "multi"]
    assert schema["ClarifyOption"]["required"] == ["text", "columns"]
