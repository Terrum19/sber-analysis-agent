"""Режим «Проверка» (A-29): проверки шагов, критик ответа и исправление по вердикту."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.assurance import checks
from app.core.agent.assurance.critic import Critic, settle
from app.core.agent.assurance.evidence import build_evidence, describe_findings
from app.core.agent.assurance.loop import REPAIR_FAILED
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep
from app.models.review import CHECKS, Review, ReviewCheck, ReviewIssue, StepFinding

SESSION = "assurance"
QUESTION = "Выручка по регионам"
BY_REGION = ResearchStep(
    step_id=0,
    tool="group_by",
    arguments={"by": ["region"], "column": "revenue", "function": "SUM"},
    purpose="Выручка по регионам",
)
PLAN = ResearchPlan(steps=[BY_REGION], final_viz="bar")
MEDIAN = ResearchStep(
    step_id=1,
    tool="describe_stats",
    input="$source",
    columns=["revenue"],
    purpose="Медиана выручки",
)
OUTLIERS = ReviewIssue(
    check="outliers",
    severity="material",
    message="выручку msk делает один заказ",
    evidence="max 1000 при медиане 10",
    fix="посчитать медиану",
)


def review(verdict: str, *issues: ReviewIssue, caveats: list[str] | None = None) -> Review:
    return Review(
        checks=[ReviewCheck(id=key, status="ok", evidence="по уликам") for key in CHECKS],
        issues=list(issues),
        verdict=verdict,
        caveats=caveats or [],
    )


class ScriptedPlanner:
    """Планы по очереди (последний повторяется); исправления шагов — по очереди."""

    def __init__(self, *plans: ResearchPlan | Exception, fixes: list | None = None) -> None:
        self.plans = list(plans) or [PLAN]
        self.fixes = list(fixes or [])
        self.contexts: list[dict] = []
        self.fix_errors: list[str] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        if isinstance(plan, Exception):
            raise plan
        return plan.model_copy(deep=True)

    async def fix_step(self, step, error, columns, seen) -> ResearchStep:
        self.fix_errors.append(error)
        fixed = self.fixes.pop(0)
        if isinstance(fixed, Exception):
            raise fixed
        return fixed


class Critics:
    """LLM критика: ответы по очереди; промпты запоминаются."""

    def __init__(self, *replies: Review | Exception) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema):
        assert schema is Review
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Insights:
    """Вывод по очереди; замечания проверки (review) запоминаются."""

    def __init__(self, llm: Critics, *headlines: str) -> None:
        self.llm = llm
        self.headlines = list(headlines) or ["Больше всего выручки в msk"]
        self.reviews: list[str] = []

    async def generate(self, question, result_table, review: str = "", **_) -> Insight:
        self.reviews.append(review)
        headline = self.headlines.pop(0) if len(self.headlines) > 1 else self.headlines[0]
        return Insight(headline=headline, details=["по всей таблице"])


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES "
        "('msk', 10.0, DATE '2024-01-05'), ('msk', 1000.0, DATE '2024-02-05'), "
        "('spb', 4.0, DATE '2024-02-10'), ('kzn', NULL, DATE '2024-03-01')"
        ") AS rows(region, revenue, sold_at)"
    )
    SessionMemory(SESSION).set_table("sales")
    yield manager
    manager.close()


def agent_with(db, planner: ScriptedPlanner, critics: Critics, *headlines: str):
    insights = Insights(critics, *headlines)
    agent = AgentOrchestrator(
        planner=planner, executor=StepExecutor(db=db), insight_gen=insights, db=db
    )
    return agent, insights


def ask(agent, question: str = QUESTION, verify: bool = True) -> list[dict]:
    async def collect() -> list[dict]:
        request = ChatRequest(session_id=SESSION, question=question, verify=verify)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect())


def progress(events: list[dict], stage: str) -> list[dict]:
    return [e["data"] for e in events if e["type"] == "progress" and e["data"]["stage"] == stage]


def of(events: list[dict], kind: str) -> list[dict]:
    return [event["data"] for event in events if event["type"] == kind]


def last_turn() -> dict:
    return SessionMemory(SESSION)._state["turns"][-1]


# --- ход в режиме проверки ---


def test_without_verify_the_stream_is_unchanged(db):
    agent, _ = agent_with(db, ScriptedPlanner(), Critics())

    events = ask(agent, verify=False)

    assert not progress(events, "check") and not progress(events, "review")
    assert not of(events, "review")
    assert "revision" not in of(events, "text")[0]
    assert "revision" not in of(events, "chart")[0]


def test_ok_verdict_keeps_the_draft_and_saves_the_review(db):
    critics = Critics(review("ok"))
    agent, insights = agent_with(db, ScriptedPlanner(), critics)

    events = ask(agent)

    checked = progress(events, "check")
    assert [(e["status"], e["step_id"]) for e in checked] == [("started", 0), ("finished", 0)]
    assert checked[1]["findings"] == []
    assert [e["revision"] for e in of(events, "text")] == [0]
    assert [e["revision"] for e in of(events, "chart")] == [0]
    assert [(e["status"], e["round"]) for e in progress(events, "review")] == [
        ("started", 1),
        ("finished", 1),
    ]
    (verdict,) = of(events, "review")
    assert (verdict["verdict"], verdict["action"], verdict["round"]) == ("ok", "ok", 1)
    assert [check["id"] for check in verdict["checks"]] == list(CHECKS)
    assert verdict["checks"][0]["title"] == CHECKS["answers_question"]
    assert events[-1]["type"] == "done"
    # Улики критика посчитаны кодом: строки шагов, выбросы, неиспользованная дата.
    prompt = critics.prompts[0]
    assert "строк на входе 4, на выходе 3" in prompt
    assert "выбросов по IQR" in prompt
    assert "sold_at" in prompt
    assert "Больше всего выручки в msk" in prompt
    turn = last_turn()
    assert turn["review"]["verdict"] == "ok" and "draft" not in turn
    assert insights.reviews == [""]


def test_caveat_goes_to_limits_without_a_new_answer(db):
    critics = Critics(review("caveat", caveats=["Казань без выручки"]))
    agent, _ = agent_with(db, ScriptedPlanner(), critics)

    events = ask(agent)

    (verdict,) = of(events, "review")
    assert (verdict["action"], verdict["caveats"]) == ("caveat", ["Казань без выручки"])
    assert len(of(events, "text")) == 1
    assert not progress(events, "repair")
    assert last_turn()["review"]["caveats"] == ["Казань без выручки"]


def test_extend_plan_adds_steps_and_reviews_again(db):
    critics = Critics(review("extend_plan", OUTLIERS), review("extend_plan", OUTLIERS))
    planner = ScriptedPlanner(PLAN, ResearchPlan(steps=[MEDIAN]))
    agent, insights = agent_with(db, planner, critics, "msk впереди", "msk впереди по медиане")

    events = ask(agent)

    context = planner.contexts[1]
    assert "выручку msk делает один заказ" in context["review"]
    assert "$step_0: group_by" in context["executed"]
    assert context["next_step_id"] == 1
    repair = progress(events, "repair")
    assert [(e["status"], e["action"]) for e in repair] == [
        ("started", "extend_plan"),
        ("finished", "extend_plan"),
    ]
    assert repair[0]["reason"] == "выручку msk делает один заказ"
    # Шаги исправления — с revision 1: прежний шаг готов, новый выполнен и проверен.
    revised = [e for e in progress(events, "step") if e.get("revision") == 1]
    assert {(e["step_id"], e["status"]) for e in revised} >= {(0, "finished"), (1, "finished")}
    assert any(e.get("revision") == 1 and e["step_id"] == 1 for e in progress(events, "check"))
    texts = of(events, "text")
    assert [(t["headline"], t["revision"]) for t in texts] == [
        ("msk впереди", 0),
        ("msk впереди по медиане", 1),
    ]
    # Ответ — прежняя таблица по регионам, медиана — сопутствующий результат.
    assert [c["revision"] for c in of(events, "chart")] == [0, 1]
    assert "выручку msk делает один заказ" in insights.reviews[1]
    rounds = of(events, "review")
    assert [(r["round"], r["verdict"]) for r in rounds] == [(1, "extend_plan"), (2, "caveat")]
    assert rounds[1]["caveats"] == ["выручку msk делает один заказ"]
    assert "Второго исправления не будет" in critics.prompts[1]
    turn = last_turn()
    assert turn["headline"] == "msk впереди по медиане"
    assert turn["draft"]["headline"] == "msk впереди"
    assert turn["first_review"]["verdict"] == "extend_plan"
    assert turn["review"]["round"] == 2


def test_extended_transform_becomes_the_answer(db):
    top = ResearchStep(step_id=1, tool="sort_limit", arguments={"order_by": "revenue", "limit": 1})
    critics = Critics(review("extend_plan", OUTLIERS), review("ok"))
    agent, _ = agent_with(db, ScriptedPlanner(PLAN, ResearchPlan(steps=[top])), critics)

    events = ask(agent)

    charts = of(events, "chart")
    assert charts[0]["result_table"] != charts[1]["result_table"]
    assert of(events, "review")[1]["verdict"] == "ok"


def test_fix_insight_rewrites_the_text_and_keeps_the_chart(db):
    issue = OUTLIERS.model_copy(update={"check": "claims", "severity": "minor"})
    critics = Critics(review("fix_insight", issue), review("ok"))
    agent, insights = agent_with(db, ScriptedPlanner(), critics, "Рост из-за msk", "msk впереди")

    events = ask(agent)

    assert [t["revision"] for t in of(events, "text")] == [0, 1]
    assert [c["revision"] for c in of(events, "chart")] == [0]
    assert "Прошлый вариант вывода" in insights.reviews[1]
    assert "Рост из-за msk" in insights.reviews[1]
    assert last_turn()["headline"] == "msk впереди"


def test_replan_runs_a_new_plan(db):
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="group_by",
                arguments={"by": ["region"], "column": "revenue", "function": "MEDIAN"},
                purpose="Медиана по регионам",
            )
        ],
        final_viz="bar",
    )
    critics = Critics(review("replan", OUTLIERS), review("ok"))
    planner = ScriptedPlanner(PLAN, plan)
    agent, _ = agent_with(db, planner, critics)

    events = ask(agent)

    assert "Прошлый план отклонён" in planner.contexts[1]["review"]
    planned = [e for e in progress(events, "planning") if e.get("revision") == 1]
    assert [e["status"] for e in planned] == ["started", "finished"]
    assert [c["revision"] for c in of(events, "chart")] == [0, 1]
    assert of(events, "review")[0]["action"] == "replan"


def test_failed_repair_becomes_a_caveat_without_a_second_round(db):
    critics = Critics(review("extend_plan", OUTLIERS))
    agent, _ = agent_with(db, ScriptedPlanner(PLAN, RuntimeError("модель недоступна")), critics)

    events = ask(agent)

    assert [e["status"] for e in progress(events, "repair")] == ["started", "failed"]
    rounds = of(events, "review")
    assert [(r["round"], r["action"]) for r in rounds] == [(1, "extend_plan"), (1, "caveat")]
    problems = REPAIR_FAILED.format(problems="выручку msk делает один заказ")
    assert rounds[1]["caveats"] == [problems]
    assert len(of(events, "text")) == 1
    assert events[-1]["type"] == "done"
    assert last_turn()["status"] == "ok"
    assert SessionMemory(SESSION).get_pending_recovery() is None


def test_failed_repair_step_clears_the_pending_clarification(db):
    broken = ResearchStep(step_id=1, tool="describe_stats", input="$source", columns=["nope"])
    critics = Critics(review("extend_plan", OUTLIERS))
    planner = ScriptedPlanner(PLAN, ResearchPlan(steps=[broken]), fixes=[ValueError("нет")] * 2)
    agent, _ = agent_with(db, planner, critics)

    events = ask(agent)

    assert progress(events, "repair")[-1]["status"] == "failed"
    assert SessionMemory(SESSION).get_pending_recovery() is None


def test_empty_extension_is_a_failed_repair(db):
    critics = Critics(review("extend_plan", OUTLIERS))
    agent, _ = agent_with(db, ScriptedPlanner(PLAN, ResearchPlan(steps=[])), critics)

    events = ask(agent)

    assert progress(events, "repair")[-1]["error"] == "планировщик не дополнил план"


def test_critic_failure_leaves_the_plain_answer(db):
    critics = Critics(RuntimeError("таймаут"), RuntimeError("таймаут"))
    agent, _ = agent_with(db, ScriptedPlanner(), critics)

    events = ask(agent)

    assert [e["status"] for e in progress(events, "review")] == ["started", "failed"]
    assert not of(events, "review")
    assert events[-1]["type"] == "done"
    assert "review" not in last_turn()


def test_second_round_failure_keeps_the_fixed_answer(db):
    critics = Critics(review("fix_insight", OUTLIERS), RuntimeError("x"), RuntimeError("x"))
    agent, _ = agent_with(db, ScriptedPlanner(), critics, "черновик", "исправлено")

    events = ask(agent)

    assert progress(events, "review")[-1] == {
        "stage": "review",
        "status": "failed",
        "round": 2,
        "error": "критик не ответил: x",
    }
    turn = last_turn()
    assert turn["headline"] == "исправлено"
    assert turn["review"]["verdict"] == "fix_insight"
    assert turn["draft"]["headline"] == "черновик"


def test_retry_after_an_invalid_critic_reply(db):
    critics = Critics(ValueError("не по схеме"), review("ok"))
    agent, _ = agent_with(db, ScriptedPlanner(), critics)

    events = ask(agent)

    assert of(events, "review")[0]["verdict"] == "ok"
    assert "Прошлый ответ отклонён: не по схеме" in critics.prompts[1]


def test_turn_without_steps_is_not_reviewed(db):
    agent, _ = agent_with(db, ScriptedPlanner(), Critics())

    events = ask(agent, question="привет")

    assert not progress(events, "review")


def test_fake_llm_review_is_ok():
    reply = asyncio.run(FakeLLM().complete_structured("", "", Review))
    assert reply.verdict == "ok" and len(reply.checks) == len(CHECKS)


# --- проверки шага в коде ---


def test_empty_filter_is_fixed_by_the_planner(db):
    wrong = ResearchStep(
        step_id=0, tool="filter_rows", arguments={"column": "region", "value": "MSK"}
    )
    right = wrong.model_copy(update={"arguments": {"column": "region", "value": "msk"}})
    planner = ScriptedPlanner(ResearchPlan(steps=[wrong], final_viz="table"), fixes=[right])
    agent, _ = agent_with(db, planner, Critics(review("ok")))

    events = ask(agent)

    assert "результат пуст" in planner.fix_errors[0]
    steps = progress(events, "step")
    assert [e["status"] for e in steps] == ["started", "finished", "fixing", "finished"]
    assert steps[-1]["rows"] == 2
    assert progress(events, "check")[-1]["findings"] == []


def test_failed_anomaly_fix_keeps_the_step_and_its_findings(db):
    wrong = ResearchStep(
        step_id=0, tool="filter_rows", arguments={"column": "region", "value": "MSK"}
    )
    same = wrong.model_copy()
    planner = ScriptedPlanner(
        ResearchPlan(steps=[wrong], final_viz="table"), fixes=[RuntimeError("нет"), same]
    )
    critics = Critics(review("ok"))
    agent, _ = agent_with(db, planner, critics)

    events = ask(agent)

    assert len(planner.fix_errors) == 2
    findings = progress(events, "check")[-1]["findings"]
    assert [f["code"] for f in findings] == ["empty"]
    assert "аномалии: результат пуст" in critics.prompts[0]


def step(tool: str, **arguments: object) -> ResearchStep:
    return ResearchStep(step_id=0, tool=tool, arguments=arguments)


def save(db, sql: str) -> str:
    db.execute(f"CREATE OR REPLACE TABLE out AS {sql}")
    return "out"


def codes(findings: list[StepFinding]) -> list[str]:
    return [finding.code for finding in findings]


def test_check_codes(db):
    db.execute("CREATE TABLE big AS SELECT range AS id, range % 7 AS k FROM range(1000)")
    collapsed = save(db, "SELECT * FROM big WHERE id < 3")
    assert codes(
        checks.inspect_step(db, step("filter_rows", column="id"), "big", collapsed, "")
    ) == ["filter_collapse"]
    blown = save(db, "SELECT * FROM sales, (VALUES (1), (2)) t(x)")
    assert codes(checks.inspect_step(db, step("join_tables"), "sales", blown, "")) == [
        "join_blowup"
    ]
    single = save(db, "SELECT sum(revenue) AS total FROM sales")
    found = checks.inspect_step(db, step("group_by", by=[]), "sales", single, "Выручка по месяцам")
    assert codes(found) == ["single_row"]
    assert codes(checks.repairable(found)) == ["single_row"]
    assert "одна строка" in checks.fix_feedback(found)
    nulls = save(db, "SELECT * FROM sales")
    found = checks.inspect_step(
        db, step("select_columns", columns=["region", "revenue"]), "sales", nulls, ""
    )
    assert codes(found) == []
    db.execute(
        "CREATE TABLE holes AS SELECT * FROM (VALUES (1, NULL), (2, NULL), (3, 5), (4, 6)) t(id, v)"
    )
    found = checks.inspect_step(db, step("select_columns", columns=["v"]), "holes", "holes", "")
    assert codes(found) == ["nulls"]
    constant = save(db, "SELECT region, 7 AS n FROM sales")
    assert codes(checks.inspect_step(db, step("sort_limit"), "sales", constant, "")) == ["constant"]
    assert checks.inspect_step(db, step("filter_rows"), "missing", "missing", "") == []


def test_partial_periods_of_time_bucket(db):
    bucket = step("time_bucket", date_column="sold_at", interval="month")
    table = tool_registry_run(db, bucket)
    found = checks.inspect_step(db, bucket, "sales", table, "")
    messages = [f.message for f in found if f.code == "partial_period"]
    assert messages == [
        "первый период 2024-01-01 неполный: данные с 2024-01-05",
        "последний период 2024-03-01 неполный: данные по 2024-03-01",
    ]
    daily = step("time_bucket", date_column="sold_at", interval="day")
    assert "partial_period" not in codes(
        checks.inspect_step(db, daily, "sales", tool_registry_run(db, daily), "")
    )
    broken = step("time_bucket", interval="fortnight")
    assert checks._partial_periods(db, broken, "sales", "sales", {}) == []


def tool_registry_run(db, bucket: ResearchStep) -> str:
    from app.core.tools.registry import tool_registry

    return tool_registry.get("time_bucket").run(db, "sales", **bucket.arguments)


# --- вердикт и улики ---


def test_settle_enforces_the_rules():
    raw = Review(
        checks=[
            ReviewCheck(id="period", status="ok"),
            ReviewCheck(id="period", status="issue"),
            ReviewCheck(id="weather", status="issue"),
        ],
        issues=[
            ReviewIssue(check="period", severity="material", message="декабрь неполный"),
            ReviewIssue(check="weather", message="дождь"),
            ReviewIssue(check="nulls", message=" "),
        ],
        verdict="replan",
    )
    settled = settle(raw)
    # material без улик — minor, а без material перепланирования нет: оговорка.
    assert [(i.check, i.severity) for i in settled.issues] == [("period", "minor")]
    assert settled.verdict == "caveat"
    assert settled.caveats == ["декабрь неполный"]
    assert [c.id for c in settled.checks] == list(CHECKS)
    period = next(c for c in settled.checks if c.id == "period")
    assert period.status == "issue"
    assert settle(Review(verdict="fix_insight")).verdict == "ok"
    assert settle(Review(verdict="caveat")).verdict == "ok"
    assert settle(review("extend_plan", OUTLIERS), round=2).verdict == "caveat"


def test_review_schema_is_lenient():
    parsed = Review.model_validate(
        {
            "verdict": " OK ",
            "checks": [{"id": "period", "status": "maybe"}],
            "issues": [{"check": "period", "severity": "MATERIAL", "message": "x"}],
        }
    )
    assert parsed.verdict == "ok"
    assert parsed.checks[0].status == "n/a"
    assert parsed.issues[0].severity == "material"
    assert Review.model_validate({"verdict": None}).verdict == "ok"


def test_critic_gives_up_after_attempts():
    critic = Critic(Critics(RuntimeError("a"), RuntimeError("b")))
    with pytest.raises(RuntimeError, match="критик не ответил: b"):
        asyncio.run(critic.review("улики"))


def test_evidence_survives_missing_tables(db):
    text = build_evidence(
        db,
        question="вопрос",
        steps=[BY_REGION],
        tables={0: "nowhere"},
        inputs={},
        result_table="nowhere",
        supporting=[("тест", "nowhere")],
        findings={0: [StepFinding(code="empty", message="результат пуст")]},
        insight=Insight(headline="ответ", limits="мало данных"),
        source="absent",
        memories=["выручка без НДС"],
    )
    assert "строк на входе ?, на выходе ?" in text
    assert "не прочитан" in text
    assert "выручка без НДС" in text
    assert "Ограничения: мало данных" in text
    assert describe_findings({0: [StepFinding(code="empty", message="пусто")]}) == "- шаг 0: пусто"


def test_evidence_describes_text_and_empty_columns(db):
    db.execute("CREATE TABLE void (region VARCHAR, revenue DOUBLE)")
    text = build_evidence(
        db,
        question="вопрос",
        steps=[BY_REGION],
        tables={0: "void"},
        inputs={0: "void"},
        result_table="void",
        supporting=[],
        findings={},
        insight=Insight(headline="ответ"),
        source="void",
        titles={"region": "Регион"},
    )
    assert "- revenue: таблица пуста" in text
    assert "- region (VARCHAR): пропусков 0, разных значений 0" in text
    assert "результат пуст" not in text
