"""Ход-пояснение (route=answer): «как ты посчитал?» отвечается по журналу ходов, без шагов."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.explainer import EMPTY_JOURNAL, Explainer, describe_journal
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

SESSION = "explain"
FILTER = ResearchStep(
    step_id=0,
    tool="filter_rows",
    arguments={"column": "region", "operator": "!=", "value": "kzn"},
    purpose="Без Казани",
)
REVENUE = ResearchStep(
    step_id=1,
    tool="group_by",
    arguments={"by": ["region"], "column": "revenue", "function": "SUM"},
    purpose="Выручка по регионам",
)
DATA = ResearchPlan(steps=[FILTER, REVENUE], final_viz="bar")
ANSWER = ResearchPlan(route="answer", final_viz="none")


class ScriptedPlanner:
    """План по вопросу (по умолчанию — расчёт выручки); запоминает контексты."""

    def __init__(self, plans: dict[str, ResearchPlan] | None = None) -> None:
        self.plans = plans or {}
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        return self.plans.get(question, DATA).model_copy(deep=True)


class FixedInsight:
    async def generate(self, question, result_table, **_) -> Insight:
        return Insight(headline="Больше всего выручки в msk", details=["Казань исключена"])


class ScriptedLLM:
    """Ответы пояснения по очереди; запоминает промпты."""

    def __init__(self, *replies: Insight | Exception) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema):
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), "
        "('kzn', 1.0), ('msk', 5.0)) AS rows(region, revenue)"
    )
    SessionMemory(SESSION).set_table("sales")
    yield manager
    manager.close()


def agent_with(db, planner, llm: ScriptedLLM) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=planner,
        executor=StepExecutor(db=db),
        insight_gen=FixedInsight(),
        db=db,
        explainer=Explainer(llm=llm, db=db),
    )


def ask(agent: AgentOrchestrator, question: str, reply_to: str | None = None) -> list[dict]:
    async def collect() -> list[dict]:
        request = ChatRequest(session_id=SESSION, question=question, reply_to=reply_to)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect())


def kinds(events: list[dict]) -> list[str]:
    return [event["type"] for event in events if event["type"] != "progress"]


def turns() -> list[dict]:
    return SessionMemory(SESSION)._state["turns"]


def test_how_did_you_count_is_answered_from_the_journal(db):
    llm = ScriptedLLM(Insight(headline="Сложил revenue по region, Казань исключил"))
    agent = agent_with(db, ScriptedPlanner({"Как ты посчитал?": ANSWER}), llm)
    ask(agent, "Выручка по регионам")

    events = ask(agent, "Как ты посчитал?")

    assert kinds(events) == ["text", "done"]
    assert events[-2]["data"]["headline"] == "Сложил revenue по region, Казань исключил"
    # Ни одного шага: ни событий шагов, ни шагов в ходе.
    assert not [e for e in events if e["type"] == "progress" and e["data"]["stage"] == "step"]
    last = turns()[-1]
    assert (last["status"], last["steps"]) == ("ok", [])
    prompt = llm.prompts[0]
    # В журнале — шаги с аргументами, строки после каждого шага, ответ с деталями, результат.
    assert '"operator": "!=", "value": "kzn"' in prompt
    assert "Таблица: sales (4 строк)" in prompt
    assert "строк после шага: 3" in prompt  # после фильтра
    assert "строк после шага: 2" in prompt  # после группировки
    assert "Ответ: Больше всего выручки в msk" in prompt
    assert "- Казань исключена" in prompt
    assert "msk | 15.0" in prompt
    assert "Вопрос пользователя: Как ты посчитал?" in prompt


def test_answer_details_of_data_turn_are_saved(db):
    agent = agent_with(db, ScriptedPlanner(), ScriptedLLM())
    ask(agent, "Выручка по регионам")
    assert turns()[-1]["details"] == ["Казань исключена"]


def test_answer_route_with_steps_still_computes(db):
    # Планировщик противоречит себе: шаги важнее маршрута, пояснение не вызывается.
    confused = ResearchPlan(route="answer", steps=[REVENUE.model_copy(update={"step_id": 0})])
    llm = ScriptedLLM()
    agent = agent_with(db, ScriptedPlanner({"Почему упала выручка?": confused}), llm)

    events = ask(agent, "Почему упала выручка?")

    assert "chart" in kinds(events)
    assert llm.prompts == []
    assert [step["tool"] for step in turns()[-1]["steps"]] == ["group_by"]


def test_question_without_history_says_nothing_was_computed(db):
    llm = ScriptedLLM(Insight(headline="Я ещё ничего не считал"))
    agent = agent_with(db, ScriptedPlanner({"Как ты посчитал выручку?": ANSWER}), llm)

    events = ask(agent, "Как ты посчитал выручку?")

    assert kinds(events) == ["text", "done"]
    assert f"Журнал ходов этой сессии (старые выше):\n{EMPTY_JOURNAL}" in llm.prompts[0]


def test_invented_number_gets_one_retry(db):
    llm = ScriptedLLM(
        Insight(headline="Учёл 1234 строки"),
        Insight(headline="Учёл все строки, кроме Казани"),
    )
    agent = agent_with(db, ScriptedPlanner({"Сколько строк учёл?": ANSWER}), llm)
    ask(agent, "Выручка по регионам")

    events = ask(agent, "Сколько строк учёл?")

    assert events[-2]["data"]["headline"] == "Учёл все строки, кроме Казани"
    assert len(llm.prompts) == 2
    assert "числа 1234, которых нет в журнале" in llm.prompts[1]


def test_invented_number_in_details_is_caught_too(db):
    llm = ScriptedLLM(
        Insight(headline="Сложил выручку", details=["p-value равен 0.003"]),
        Insight(headline="Сложил выручку", details=["Тестов не было"]),
    )
    agent = agent_with(db, ScriptedPlanner({"А p-value?": ANSWER}), llm)
    ask(agent, "Выручка по регионам")

    events = ask(agent, "А p-value?")

    assert events[-2]["data"]["details"] == ["Тестов не было"]


def test_number_from_journal_needs_no_retry(db):
    llm = ScriptedLLM(Insight(headline="После фильтра осталось 3 строки из 4, msk — 15"))
    agent = agent_with(db, ScriptedPlanner({"Сколько строк учёл?": ANSWER}), llm)
    ask(agent, "Выручка по регионам")

    ask(agent, "Сколько строк учёл?")

    assert len(llm.prompts) == 1


def test_explainer_failure_ends_the_turn_with_error(db):
    llm = ScriptedLLM(RuntimeError("LLM timeout"))
    agent = agent_with(db, ScriptedPlanner({"Как ты посчитал?": ANSWER}), llm)

    events = ask(agent, "Как ты посчитал?")

    assert kinds(events) == ["error", "done"]
    assert turns()[-1]["status"] == "failed"


def test_remember_with_question_about_method_confirms_in_details(db):
    plan = ResearchPlan(route="answer", remember=["выручка без НДС"])
    llm = ScriptedLLM(Insight(headline="Сложил revenue", details=[]))
    agent = agent_with(db, ScriptedPlanner({"Как считал? И запомни: без НДС": plan}), llm)
    ask(agent, "Выручка по регионам")

    events = ask(agent, "Как считал? И запомни: без НДС")

    assert events[-2]["data"]["details"] == ["Запомнено: «выручка без НДС»"]
    assert "- выручка без НДС" in llm.prompts[0]


def test_follow_up_after_explanation_continues_the_computed_turn(db):
    planner = ScriptedPlanner({"Как ты посчитал?": ANSWER})
    agent = agent_with(db, planner, ScriptedLLM(Insight(headline="Сложил revenue")))
    ask(agent, "Выручка по регионам")
    ask(agent, "Как ты посчитал?")

    ask(agent, "А теперь только msk")

    # Пояснение в истории есть, но без плана: план продолжения берётся из хода 1.
    history = planner.contexts[-1]["turns"]
    assert [(t["question"], bool(t["steps"])) for t in history] == [
        ("Выручка по регионам", True),
        ("Как ты посчитал?", False),
    ]


def test_reply_to_an_explanation_falls_back_to_recent_turns(db):
    planner = ScriptedPlanner({"Как ты посчитал?": ANSWER})
    agent = agent_with(db, planner, ScriptedLLM(Insight(headline="Сложил revenue")))
    ask(agent, "Выручка по регионам")
    explained = next(e for e in ask(agent, "Как ты посчитал?") if e["type"] == "done")

    ask(agent, "Пересчитай без фильтра", reply_to=explained["data"]["turn_id"])

    # У хода-пояснения нет плана: черновика нет, планировщик видит обычные ходы.
    assert planner.contexts[-1]["reply"] == []
    assert len(planner.contexts[-1]["turns"]) == 2


def test_chart_morph_after_explanation_uses_the_computed_chart(db):
    agent = agent_with(
        db, ScriptedPlanner({"Как ты посчитал?": ANSWER}), ScriptedLLM(Insight(headline="Так"))
    )
    ask(agent, "Выручка по регионам")
    ask(agent, "Как ты посчитал?")

    events = ask(agent, "Покажи это в виде таблицы")

    assert kinds(events) == ["chart", "done"]


def test_reply_to_an_old_turn_brings_it_and_its_result(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "context_turns", 2)
    planner = ScriptedPlanner({"Как ты это посчитал?": ANSWER})
    llm = ScriptedLLM(Insight(headline="Так"))
    agent = agent_with(db, planner, llm)
    first = next(e for e in ask(agent, "Выручка по регионам") if e["type"] == "done")
    for number in range(3):
        ask(agent, f"Вопрос {number}")

    ask(agent, "Как ты это посчитал?", reply_to=first["data"]["turn_id"])

    prompt = llm.prompts[0]
    assert "Ход 1 (пользователь отвечает на этот ход). Вопрос: Выручка по регионам" in prompt
    assert "Ход 3." in prompt and "Ход 4." in prompt and "Ход 2." not in prompt
    # Результат показан только у хода, о котором спрашивают.
    assert prompt.count("Результат (") == 1


def test_journal_survives_dropped_results_and_legacy_turns(db):
    old = {
        "number": 1,
        "question": "Старый ход",
        "tables": ["sales", "regions"],
        "steps": [{"tool": "unknown_tool", "arguments": {"day": "2024-01-01"}}],
        "headline": "ответ",
        "result_table": "temp_result_v999",
    }
    failed = {"number": 2, "question": "Упал", "status": "failed", "steps": []}

    journal = describe_journal([old, failed], db)

    assert "Таблица: sales (4 строк), regions" in journal
    assert "unknown_tool [?]" in journal
    assert "строк после шага" not in journal
    assert "таблица результата больше недоступна" in journal
    assert "Ответ: не получен (ход упал)" in journal


def test_braces_in_questions_do_not_break_the_prompt(db):
    llm = ScriptedLLM(Insight(headline="Так"))
    question = "Как ты посчитал {tools} и {journal}?"
    agent = agent_with(db, ScriptedPlanner({question: ANSWER}), llm)
    ask(agent, "Выручка по {region}")

    ask(agent, question)

    assert "Вопрос: Выручка по {region}" in llm.prompts[0]
    assert f"Вопрос пользователя: {question}" in llm.prompts[0]


@pytest.mark.parametrize(
    ("raw", "route"),
    [
        ("answer", "answer"),
        (" ANSWER ", "answer"),
        ("explain", "data"),
        (None, "data"),
        (3, "data"),
    ],
)
def test_route_is_tolerant_to_model_output(raw, route):
    assert ResearchPlan.model_validate({"route": raw, "steps": []}).route == route


def test_route_defaults_to_data():
    assert ResearchPlan.model_validate({"steps": []}).route == "data"


def test_planner_prompt_asks_for_route():
    class PromptLLM:
        prompt = ""

        async def complete_structured(self, system, user, schema):
            PromptLLM.prompt = user
            return ResearchPlan(route="answer")

    plan = asyncio.run(StepPlanner(llm=PromptLLM()).plan("как ты посчитал?", {"columns": []}))

    assert plan.route == "answer"
    assert '"route": "data"' in PromptLLM.prompt
    assert 'Если сомневаешься — "data"' in PromptLLM.prompt
