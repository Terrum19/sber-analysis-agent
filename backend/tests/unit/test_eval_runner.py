import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from app.core.agent.planner import StepPlanner
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.semantic_select import select_columns
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "eval_questions.yaml"
SALES_CSV = (
    "category,revenue,order_date\n"
    "Электроника,100,2024-01-01\n"
    "Электроника,150,2024-02-01\n"
    "Электроника,120,2024-03-01\n"
    "Одежда,50,2024-01-01\n"
    "Одежда,75,2024-02-01\n"
    "Продукты,200,2024-01-01\n"
)

spec = importlib.util.spec_from_file_location("eval_runner", REPO_ROOT / "scripts" / "eval.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def sales_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(SALES_CSV, encoding="utf-8")
    return path


@pytest.fixture
def sales_db(tmp_path: Path, sales_csv: Path):
    manager = DuckDBManager(str(tmp_path / "eval.duckdb"))
    runner.attach_csv(manager, [sales_csv])
    yield manager
    manager.close()


def make_turn(events: list[dict], steps: list[dict] | None = None, **fields) -> object:
    fields.setdefault("first_event_s", 0.1 if events else None)
    fields.setdefault("end_s", 1.0 if any(event["type"] == "done" for event in events) else None)
    return runner.Turn(events=events, steps=steps or [], **fields)


ANSWER_EVENTS = [
    {"type": "progress", "data": {"stage": "step", "status": "finished", "tool": "aggregate"}},
    {"type": "text", "data": {"headline": "Выручка растёт в июле", "details": ["Пик — 2024"]}},
    {
        "type": "chart",
        "data": {"spec": {"usermeta": {"chart_type": "line"}}, "result_table": "temp_result_v1"},
    },
    {"type": "done", "data": {}},
]
ANSWER_STEPS = [
    {"tool": "aggregate", "columns": [], "arguments": {"sql": 'SELECT sum("Revenue"), order_date'}}
]


def write_questions(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_questions_reads_issue_56_format() -> None:
    questions = runner.load_questions(FIXTURE)

    assert [question.id for question in questions] == ["sales-01", "sales-02", "sales-03", "odd-01"]
    first, follow_up = questions[0], questions[1]
    assert (first.scenario, first.dataset, first.follow_up_of) == (2, "sales", None)
    assert first.expect["value"]["tolerance"] == 0.0
    assert follow_up.follow_up_of == "sales-01"
    assert questions[3].expect == {"refusal": True}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("- {id: a, scenario: 1, question: q}", "нет полей dataset"),
        ("- {id: a, scenario: 1, dataset: t, question: q, extra: 1}", "неизвестные поля extra"),
        ("- {id: a, scenario: 1, dataset: t, question: q, expect: {tool: [x]}}", "проверки tool"),
        ("- {id: a, scenario: 1, dataset: t, question: q, expect: {value: {tolerance: 1}}}", "sql"),
        ("- {id: a, scenario: 6, dataset: t, question: q, follow_up_of: b}", "стоять выше"),
        (
            (
                "- {id: a, scenario: 1, dataset: t, question: q}\n"
                "- {id: a, scenario: 1, dataset: t, question: q}"
            ),
            "повторяется",
        ),
    ],
)
def test_load_questions_rejects_malformed_entries(tmp_path: Path, entry: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        runner.load_questions(write_questions(tmp_path, entry))


@pytest.mark.parametrize(
    "text",
    ["id: a", "- just a string", "- {id: a, scenario: 1, dataset: t, question: q, expect: [x]}"],
)
def test_load_questions_rejects_wrong_types(tmp_path: Path, text: str) -> None:
    with pytest.raises(TypeError):
        runner.load_questions(write_questions(tmp_path, text))


def test_select_questions_limits_scenarios_and_keeps_parents() -> None:
    questions = runner.load_questions(FIXTURE)
    extra = runner.Question(id="sales-04", scenario=2, dataset="sales", text="ещё")

    limited = runner.select_questions([*questions, extra], per_scenario=1)
    chosen = runner.select_questions(questions, ids=["sales-02"])

    assert [question.id for question in limited] == ["sales-01", "sales-02", "sales-03", "odd-01"]
    assert [question.id for question in chosen] == ["sales-01", "sales-02"]
    with pytest.raises(ValueError, match="нет вопросов zz"):
        runner.select_questions(questions, ids=["zz"])


def test_checks_pass_on_matching_answer(sales_db: DuckDBManager) -> None:
    expect = {
        "tools_any": ["seasonality", "aggregate"],
        "columns_all": ["revenue", "order_date"],
        "answer_contains": ["ИЮЛ", "2024"],
        "chart_type_any": ["line", "bar"],
        "refusal": False,
    }

    checks = runner.check_turn(expect, make_turn(ANSWER_EVENTS, ANSWER_STEPS), sales_db)

    assert [check.name for check in checks] == list(expect)
    assert all(check.passed for check in checks)


def test_failed_checks_record_reasons(sales_db: DuckDBManager) -> None:
    expect = {
        "tools_any": ["seasonality"],
        "columns_all": ["revenue", "region"],
        "answer_contains": ["август"],
        "chart_type_any": ["bar"],
        "refusal": True,
    }

    checks = runner.check_turn(expect, make_turn(ANSWER_EVENTS, ANSWER_STEPS), sales_db)

    reasons = {check.name: check.reason for check in checks if not check.passed}
    assert reasons == {
        "tools_any": "инструменты aggregate, ждали один из ['seasonality']",
        "columns_all": "в шагах нет колонок region",
        "answer_contains": "в ответе нет август",
        "chart_type_any": "график line, ждали один из ['bar']",
        "refusal": "ждали отказ, агент ответил по данным",
    }


def test_missing_expect_keys_are_skipped(sales_db: DuckDBManager) -> None:
    assert runner.check_turn({}, make_turn(ANSWER_EVENTS), sales_db) == []
    assert runner.check_turn({"refusal": None}, make_turn(ANSWER_EVENTS), sales_db) == []


def test_turn_without_done_fails_with_reason(sales_db: DuckDBManager) -> None:
    events = [
        {"type": "progress", "data": {"stage": "planning", "status": "failed", "error": "timeout"}},
        {"type": "error", "data": {"message": "Не удалось ответить"}},
    ]
    crashed = make_turn([], error="RuntimeError: boom")

    [check] = runner.check_turn({}, make_turn(events), sales_db)

    assert check == runner.Check(
        "turn", False, "ход упал: error: Не удалось ответить (planning: timeout)"
    )
    assert runner.check_turn({}, crashed, sales_db)[0].reason == "ход упал: RuntimeError: boom"


def test_refusal_is_a_refusal_event_or_a_plain_text_reply(sales_db: DuckDBManager) -> None:
    refusal = make_turn(
        [
            {"type": "refusal", "data": {"message": "Погоды в данных нет"}},
            {"type": "done", "data": {}},
        ]
    )
    small_talk = make_turn(
        [
            {"type": "text", "data": {"headline": "Я анализирую данные"}},
            {"type": "done", "data": {}},
        ]
    )
    for turn in (refusal, small_talk):
        assert runner.check_turn({"refusal": True}, turn, sales_db) == [
            runner.Check("refusal", True)
        ]


def test_canned_clarification_is_not_a_refusal(sales_db: DuckDBManager) -> None:
    canned = make_turn(
        [
            {
                "type": "clarification_needed",
                "data": {
                    "step_id": -1,
                    "error": "Не удалось подобрать инструменты под этот вопрос.",
                },
            }
        ],
        end_s=0.5,
    )
    [check] = runner.check_turn({"refusal": True}, canned, sales_db)
    assert not check.passed
    assert check.reason.startswith("ждали отказ, агент запросил уточнение: ")
    turn_check, refusal_check = runner.check_turn({"refusal": False}, canned, sales_db)
    assert turn_check.reason.startswith("агент запросил уточнение: ")
    assert refusal_check.passed
    assert [check.name for check in runner.check_turn({}, canned, sales_db)] == ["turn"]


def test_dashboard_check_needs_the_dashboard_event(sales_db: DuckDBManager) -> None:
    dashboard = {"type": "dashboard", "data": {"dashboard_id": "d1", "tiles": []}}
    built = make_turn([*ANSWER_EVENTS[:-1], dashboard, {"type": "done", "data": {}}])
    assert runner.check_turn({"dashboard": True}, built, sales_db) == [
        runner.Check("dashboard", True)
    ]
    [check] = runner.check_turn({"dashboard": True}, make_turn(ANSWER_EVENTS), sales_db)
    assert check == runner.Check("dashboard", False, "дашборда нет")


def test_error_event_is_neither_refusal_nor_answer(sales_db: DuckDBManager) -> None:
    failed = make_turn(
        [{"type": "error", "data": {"message": "LLM недоступна"}}, {"type": "done", "data": {}}]
    )
    for expected in (True, False):
        checks = runner.check_turn({"refusal": expected}, failed, sales_db)
        assert [check.passed for check in checks] == [False, False]
        assert checks[0].reason.startswith("ход упал: ")
        assert checks[1].reason.startswith("ход упал, отказ не проверить: ")


def test_answer_without_chart_after_steps_is_not_a_refusal(sales_db: DuckDBManager) -> None:
    text_only = [
        {"type": "text", "data": {"headline": "Всего 12 событий"}},
        {"type": "done", "data": {}},
    ]
    turn = make_turn(text_only, steps=ANSWER_STEPS)
    assert runner.check_turn({"refusal": False}, turn, sales_db) == [runner.Check("refusal", True)]


def test_route_answer_needs_a_reply_without_steps(sales_db: DuckDBManager) -> None:
    reply = make_turn(
        [
            {"type": "text", "data": {"headline": "Сложил выручку по месяцам"}},
            {"type": "done", "data": {}},
        ]
    )
    canned = make_turn(
        [
            {
                "type": "clarification_needed",
                "data": {"error": "Не удалось подобрать инструменты под этот вопрос."},
            }
        ],
        end_s=0.5,
    )
    assert runner.check_turn({"route": "answer"}, reply, sales_db) == [runner.Check("route", True)]
    # Отказ тоже без шагов, но это не ответ.
    route = runner.check_turn({"route": "answer"}, canned, sales_db)[-1]
    assert not route.passed and route.reason.startswith("без ответа: clarification_needed")
    [computed] = runner.check_turn({"route": "answer"}, make_turn(ANSWER_EVENTS), sales_db)
    assert computed == runner.Check("route", False, "посчитан шагами: aggregate")


def test_route_data_needs_steps(sales_db: DuckDBManager) -> None:
    reply = make_turn([{"type": "text", "data": {"headline": "Так"}}, {"type": "done", "data": {}}])
    assert runner.check_turn({"route": "data"}, make_turn(ANSWER_EVENTS), sales_db) == [
        runner.Check("route", True)
    ]
    assert runner.check_turn({"route": "data"}, reply, sales_db) == [
        runner.Check("route", False, "шагов не было")
    ]


def test_explain_question_set_loads() -> None:
    questions = runner.load_questions(REPO_ROOT / "data" / "eval" / "explain" / "questions.yaml")
    routes = {question.expect.get("route") for question in questions}
    assert routes == {"answer", "data", None}
    assert all(question.dataset.startswith("t_0") for question in questions)


@pytest.mark.parametrize(
    ("rows", "tolerance", "passed"),
    [
        ([{"category": "Одежда", "n": 2}], 0.0, True),
        ([{"share": 2.03}], 0.02, True),
        ([{"share": 2.03}], 0.01, False),
        ([{"flag": True}, {"n": None}], 0.5, False),
    ],
)
def test_value_compares_reference_sql_with_result_table(
    sales_db: DuckDBManager, rows: list[dict], tolerance: float, passed: bool
) -> None:
    sql = "SELECT count(*) FROM sales WHERE category = 'Одежда'"
    turn = make_turn(ANSWER_EVENTS, result_rows=rows)

    check = runner.check_value({"sql": sql, "tolerance": tolerance}, turn, sales_db)

    assert check.passed is passed
    if not passed:
        assert check.reason.startswith("в result_table нет 2 ")


def test_value_reports_missing_result_and_broken_reference(sales_db: DuckDBManager) -> None:
    no_result = make_turn(ANSWER_EVENTS, result_error="в ответе нет result_table")
    with_result = make_turn(ANSWER_EVENTS, result_rows=[{"n": 1}])

    missing = runner.check_value({"sql": "SELECT 1"}, no_result, sales_db)
    broken = runner.check_value({"sql": "SELECT * FROM nope"}, with_result, sales_db)
    empty = runner.check_value({"sql": "SELECT 'a'"}, with_result, sales_db)

    several = runner.check_value({"sql": "SELECT 1 UNION ALL SELECT 2"}, with_result, sales_db)

    assert missing.reason.startswith("в ответе нет result_table; в тексте ответа нет 1")
    assert broken.reason.startswith("эталонный SQL не выполнился: ")
    assert empty.reason.startswith("в result_table нет a ")
    assert several.reason.startswith("эталонный SQL вернул 2 строк")


def test_value_matches_strings_ignoring_case(sales_db: DuckDBManager) -> None:
    turn = make_turn(ANSWER_EVENTS, result_rows=[{"category": " одежда "}])
    sql = "SELECT 'Одежда'"
    assert runner.check_value({"sql": sql}, turn, sales_db).passed


def test_value_on_many_rows_does_not_pass_by_coincidence(sales_db: DuckDBManager) -> None:
    rows = [{"n": number} for number in range(runner.VALUE_MAX_ROWS + 1)]
    turn = make_turn(ANSWER_EVENTS, result_rows=rows)
    check = runner.check_value({"sql": "SELECT 2"}, turn, sales_db)
    assert not check.passed
    assert "совпадение числа не засчитывается" in check.reason


def test_value_without_result_table_is_found_in_the_answer_text(sales_db: DuckDBManager) -> None:
    overview = [
        {"type": "text", "data": {"headline": "В таблице 12\u00a0345 строк", "details": []}},
        {"type": "done", "data": {}},
    ]
    turn = make_turn(overview, result_error="в ответе нет result_table")
    assert runner.check_value({"sql": "SELECT 12345"}, turn, sales_db).passed
    assert not runner.check_value({"sql": "SELECT 1234"}, turn, sales_db).passed


def test_zero_reference_uses_tolerance_as_absolute(sales_db: DuckDBManager) -> None:
    turn = make_turn(ANSWER_EVENTS, result_rows=[{"n": 0.01}])
    assert runner.check_value({"sql": "SELECT 0", "tolerance": 0.02}, turn, sales_db).passed
    assert not runner.check_value({"sql": "SELECT 0"}, turn, sales_db).passed


def test_answer_contains_normalises_spaces_and_yo(sales_db: DuckDBManager) -> None:
    text = [{"type": "text", "data": {"headline": "Итого 1\u202f234, всё ещё растёт"}}]
    turn = make_turn([*text, {"type": "done", "data": {}}])
    check = runner.check_answer(["1 234", "все еще"], turn, sales_db)
    assert check.passed


def test_columns_ignore_sql_literals_and_argument_keys() -> None:
    steps = [
        {
            "tool": "filter_rows",
            "columns": [],
            "arguments": {"value": "x", "sql": "SELECT * FROM t WHERE region = 'revenue'"},
        }
    ]
    assert not runner.mentioned("revenue", steps)
    assert not runner.mentioned("value", steps)
    assert runner.mentioned("region", steps)


def test_failed_check_does_not_stop_the_run(sales_db: DuckDBManager, monkeypatch) -> None:
    def broken(expected, turn, db):
        raise RuntimeError("boom")

    monkeypatch.setitem(runner.CHECKS, "tools_any", broken)
    [check] = runner.check_turn({"tools_any": ["group_by"]}, make_turn(ANSWER_EVENTS), sales_db)
    assert check == runner.Check("tools_any", False, "проверка упала: RuntimeError: boom")


@pytest.mark.parametrize(
    ("expect", "message"),
    [
        ({"columns_all": [2023]}, "columns_all должен быть списком строк"),
        ({"answer_contains": "июл"}, "answer_contains должен быть списком строк"),
        ({"refusal": "yes"}, "refusal должен быть true или false"),
        ({"dashboard": 1}, "dashboard должен быть true или false"),
        ({"value": {"sql": "SELECT 1", "tolerance": -1}}, "tolerance должен быть числом"),
        ({"route": "explain"}, "route должен быть одним из answer, data"),
    ],
)
def test_expect_types_are_validated(expect: dict, message: str) -> None:
    entry = {"id": "q", "scenario": 1, "dataset": "sales", "question": "?", "expect": expect}
    with pytest.raises((TypeError, ValueError), match=message):
        runner.parse_question(entry, set(), "вопрос 1")


def test_failures_fold_multiline_reasons_into_one_line() -> None:
    question = runner.Question(id="q", scenario=1, dataset="sales", text="?")
    check = runner.Check("value", False, "эталонный SQL не выполнился: Catalog Error\n\nLINE 1: x")

    folded = runner.question_result(question, "sales", None, make_turn([]), [check])

    assert folded["failures"] == ["value: эталонный SQL не выполнился: Catalog Error LINE 1: x"]


class ScriptedOrchestrator:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        for event in self.events:
            yield event


async def test_run_turn_measures_first_event_and_turn_end(monkeypatch) -> None:
    ticks = iter([10.0, 10.5, 11.0, 12.25])
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(ticks))
    orchestrator = ScriptedOrchestrator(
        [
            {"type": "progress", "data": {"stage": "planning", "status": "started"}},
            {"type": "text", "data": {"headline": "ответ"}},
            {"type": "done", "data": {}},
        ]
    )

    turn = await runner.run_turn(orchestrator, runner.Trace(), "session", "вопрос")

    assert (turn.first_event_s, turn.end_s) == (0.5, 2.25)
    assert orchestrator.requests[0].session_id == "session"


async def test_run_turn_records_crash() -> None:
    class Broken:
        async def run(self, request):
            yield {"type": "progress", "data": {}}
            raise RuntimeError("boom")

    turn = await runner.run_turn(Broken(), runner.Trace(), "session", "вопрос")

    assert turn.error == "RuntimeError: boom"
    assert turn.end_s is None


def test_percentile_uses_nearest_rank() -> None:
    values = [float(value) for value in range(20, 0, -1)]

    assert runner.percentile(values, 0.5) == 10.0
    assert runner.percentile(values, 0.95) == 19.0
    assert runner.percentile([3.0], 0.95) == 3.0
    assert runner.percentile([], 0.5) is None


async def test_follow_up_runs_in_parent_session(sales_db: DuckDBManager, monkeypatch) -> None:
    bound = []
    set_table = SessionMemory.set_table

    def recorded_set_table(memory, table):
        bound.append(memory.session_id)
        set_table(memory, table)

    monkeypatch.setattr(SessionMemory, "set_table", recorded_set_table)
    trace = runner.Trace()
    orchestrator = runner.build_orchestrator(sales_db, trace, llm=FakeLLM())

    results = await runner.run_questions(
        runner.load_questions(FIXTURE), sales_db, orchestrator, trace
    )

    sessions = [result["session_id"] for result in results]
    assert sessions[0] == sessions[1]
    assert len(set(sessions)) == 3
    assert bound == [sessions[0], sessions[2], sessions[3]]
    assert SessionMemory(sessions[1], db=sales_db).table == "sales"
    assert all(result["ended"] == "done" for result in results)
    assert results[0]["passed"], results[0]["failures"]
    assert results[0]["tools"] == ["aggregate"]


async def test_missing_table_fails_question_and_its_follow_ups(sales_db: DuckDBManager) -> None:
    trace = runner.Trace()
    orchestrator = runner.build_orchestrator(sales_db, trace, llm=FakeLLM())
    questions = runner.load_questions(FIXTURE)[:2]

    results = await runner.run_questions(questions, sales_db, orchestrator, trace, "nope")

    assert [result["failures"] for result in results] == [
        ["session: таблицы nope нет в DuckDB (make hk-data)"],
        ["session: сессии вопроса sales-01 нет"],
    ]


async def test_recording_planner_keeps_plan_and_records_selection(
    sales_db: DuckDBManager,
) -> None:
    memory = SessionMemory("selection", db=sales_db)
    memory.set_table("sales")
    context = memory.build_context("Какая выручка по категориям?")
    trace = runner.Trace()
    planner = runner.RecordingPlanner(StepPlanner(llm=FakeLLM()), trace)

    recorded = await planner.plan("Какая выручка по категориям?", context)
    plain = await StepPlanner(llm=FakeLLM()).plan("Какая выручка по категориям?", context)

    expected = select_columns(
        "Какая выручка по категориям?",
        context["columns"],
        profile=context["profile"],
        titles=context["titles"],
    )
    [selection] = trace.selections
    assert recorded == plain
    assert selection.columns == expected
    assert selection.block_chars > 0
    assert planner.llm is planner._inner.llm


def test_recording_planner_fails_fast_without_planner_hooks(monkeypatch) -> None:
    monkeypatch.delattr("app.core.agent.planner.describe_other_columns")

    with pytest.raises(RuntimeError, match="describe_other_columns"):
        runner.RecordingPlanner(object(), runner.Trace())


def test_selection_covered_uses_first_planner_call() -> None:
    first = runner.Selection(["Revenue", "date"], profiled=True)
    turn = make_turn([], selections=[first, runner.Selection([])])

    assert runner.selection_covered(["revenue", "date"], turn) is True
    assert runner.selection_covered(["revenue", "region"], turn) is False
    assert runner.selection_covered(None, turn) is None
    assert runner.selection_covered(["revenue"], make_turn([])) is None
    unprofiled = make_turn([], selections=[runner.Selection(["Revenue", "date"])])
    assert runner.selection_covered(["revenue"], unprofiled) is None


def result(question_id: str, scenario: int, passed: bool, **fields) -> dict:
    return {
        "id": question_id,
        "scenario": scenario,
        "dataset": "sales",
        "passed": passed,
        "failures": [] if passed else ["value: в result_table нет 2"],
        "first_event_s": 0.2,
        "done_s": 3.0,
        "selection_covered": None,
        "block_chars": None,
        "prompt_truncated": 0,
        "errored": False,
        "session_id": f"eval-{question_id}",
        "profiled": True,
        "error": None,
        **fields,
    }


SAMPLE_RESULTS = [
    result("sales-01", 2, True, selection_covered=True, block_chars=300),
    result("sales-02", 2, False, selection_covered=False, block_chars=500, done_s=9.0),
    result("odd-01", 0, True, done_s=None, profiled=None),
]


def test_summarize_counts_scenarios_timing_and_recall() -> None:
    report = runner.summarize(SAMPLE_RESULTS, "fake", FIXTURE, date(2026, 10, 25))

    assert (report["total"], report["passed"], report["pass_rate"]) == (3, 2, 66.7)
    assert report["scenarios"] == {
        "0": {"total": 1, "passed": 1, "pass_rate": 100.0},
        "2": {"total": 2, "passed": 1, "pass_rate": 50.0},
    }
    assert report["timing"]["done_s"] == {"p50": 3.0, "p95": 9.0, "count": 2}
    assert report["column_selection"] == {
        "measured": 2,
        "covered": 1,
        "recall": 0.5,
        "mean_block_chars": 400,
        "unprofiled": 0,
    }
    assert (report["errored"], report["no_session"]) == (0, 0)
    assert report["questions_file"] == "backend/tests/fixtures/eval_questions.yaml"


def test_write_report_names_files_by_date_and_model(tmp_path: Path) -> None:
    report = runner.summarize(SAMPLE_RESULTS, "qwen2.5-coder:7b", FIXTURE, date(2026, 10, 25))

    paths = runner.report_paths(tmp_path / "reports", date(2026, 10, 25), "qwen2.5-coder:7b", None)
    markdown, data = runner.write_report(report, *paths)

    assert markdown.name == "eval_2026-10-25_qwen2.5-coder-7b.md"
    assert data.name == "eval_2026-10-25_qwen2.5-coder-7b.json"
    text = markdown.read_text(encoding="utf-8")
    assert "вопросов 3, прошло 2 (66.7 %)" in text
    assert "| 0 (неудобные) | 1 | 1 | 100 |" in text
    assert "| До конца хода | 3 | 9 |" in text
    assert "Recall отбора: 1 из 2 (50 %)" in text
    assert "Средний размер блока колонок в промпте: 400 символов" in text
    assert "- `sales-02` (сценарий 2, `sales`): value: в result_table нет 2" in text
    assert json.loads(data.read_text(encoding="utf-8")) == report


def test_main_runs_on_fake_llm(tmp_path: Path, sales_csv: Path, monkeypatch) -> None:
    for name in ("USE_FAKE_LLM", "STORAGE_DIR", "SQLITE_PATH", "DUCKDB_PATH"):
        monkeypatch.setenv(name, "")
    out_dir = tmp_path / "out"

    code = runner.main(
        [
            "--fake",
            "--questions",
            str(FIXTURE),
            "--csv",
            str(sales_csv),
            "--db",
            str(tmp_path / "main.duckdb"),
            "--per-scenario",
            "1",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    [data] = out_dir.glob("eval_*_fake.json")
    report = json.loads(data.read_text(encoding="utf-8"))
    assert report["model"] == "fake"
    assert [question["id"] for question in report["questions"]] == [
        "sales-01",
        "sales-02",
        "sales-03",
        "odd-01",
    ]
    assert report["column_selection"]["measured"] == 3


def test_report_tag_goes_into_the_file_name(tmp_path: Path) -> None:
    markdown, data = runner.report_paths(tmp_path, date(2026, 10, 25), "qwen:7b", "before")
    assert markdown.name == "eval_2026-10-25_qwen-7b_before.md"
    assert data.name == "eval_2026-10-25_qwen-7b_before.json"


def test_empty_lists_and_planless_turns_skip_their_checks(sales_db: DuckDBManager) -> None:
    overview = make_turn(
        [
            {"type": "text", "data": {"headline": "Таблица продаж"}},
            {"type": "chart", "data": {"spec": {"usermeta": {"chart_type": "table"}}}},
            {"type": "done", "data": {}},
        ]
    )
    expect = {"tools_any": [], "chart_type_any": [], "columns_all": ["date"]}
    assert runner.check_turn(expect, overview, sales_db) == []
    planned = make_turn(ANSWER_EVENTS, selections=[runner.Selection(["date"], profiled=True)])
    [check] = runner.check_turn({"columns_all": ["date"]}, planned, sales_db)
    assert check.name == "columns_all"


def test_clarification_after_a_failed_step_is_not_a_refusal(sales_db: DuckDBManager) -> None:
    failed_step = make_turn(
        [
            {"type": "progress", "data": {"stage": "step", "status": "failed", "step_id": 0}},
            {"type": "clarification_needed", "data": {"error": "Binder Error"}},
        ],
        end_s=0.5,
    )
    [check] = runner.check_turn({"refusal": True}, failed_step, sales_db)
    assert check.reason.startswith("ждали отказ, агент запросил уточнение: ")
