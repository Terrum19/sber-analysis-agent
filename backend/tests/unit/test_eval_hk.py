import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM

REPO_ROOT = Path(__file__).resolve().parents[3]
HK_DIR = REPO_ROOT / "data" / "eval" / "hk"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "eval_questions.yaml"
SALES_CSV = (
    "category,revenue,order_date\n"
    "Электроника,100,2024-01-01\n"
    "Электроника,150,2024-02-01\n"
    "Одежда,50,2024-01-01\n"
    "Продукты,200,2024-01-01\n"
)

spec = importlib.util.spec_from_file_location("eval_runner_hk", REPO_ROOT / "scripts" / "eval.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def sales_db(tmp_path: Path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(SALES_CSV, encoding="utf-8")
    manager = DuckDBManager(str(tmp_path / "eval.duckdb"))
    runner.attach_csv(manager, [csv_path])
    yield manager
    manager.close()


def answer(
    headline: str,
    details: list[str] | None = None,
    charts: list[str] | None = None,
    **fields,
) -> object:
    events = [{"type": "text", "data": {"headline": headline, "details": details or []}}]
    events += [
        {"type": "chart", "data": {"spec": {"usermeta": {"chart_type": kind}}, "caption": ""}}
        for kind in charts or []
    ]
    events.append({"type": "done", "data": {}})
    return runner.Turn(events=events, first_event_s=0.1, end_s=1.0, **fields)


def write_questions(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_hk_set_and_gold_load_without_datasets() -> None:
    questions = runner.load_questions(HK_DIR / "questions.yaml")
    gold = runner.load_gold(HK_DIR / "gold.json", questions)

    assert len(questions) == 35
    by_id = {question.id: question for question in questions}
    assert set(gold) <= set(by_id)
    assert by_id["sales-s3-july"].judge == ("july_not_special", "numbers_grounded")
    assert by_id["sales-s3-july"].notes.startswith("Июль")
    assert by_id["sales-s6-channels-morph"].follow_up_of == "sales-s6-channels"
    assert gold["sales-u-delete"] == {"row_count": 500000}


def test_load_gold_missing_file_is_empty_and_unknown_ids_rejected(tmp_path: Path) -> None:
    questions = runner.load_questions(FIXTURE)
    assert runner.load_gold(tmp_path / "gold.json", questions) == {}
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"nope": {"row_count": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="nope"):
        runner.load_gold(path, questions)


@pytest.mark.parametrize(
    ("expect", "message"),
    [
        ("{numbers: {tolerance: 1}}", "sql"),
        ("{numbers: {sql: x, any_of: [{sql: y}]}}", "any_of"),
        ("{numbers: {any_of: []}}", "any_of"),
        ("{numbers: {sql: x, tolerance_abs: -1}}", "tolerance_abs"),
        ("{range: {low: 1}}", "range"),
        ("{contains_any_sql: ''}", "contains_any_sql"),
    ],
)
def test_new_expect_keys_reject_bad_values(tmp_path: Path, expect: str, message: str) -> None:
    entry = f"- {{id: a, scenario: 1, dataset: t, question: q, expect: {expect}}}"
    with pytest.raises(ValueError, match=message):
        runner.load_questions(write_questions(tmp_path, entry))


@pytest.mark.parametrize(
    "extra",
    [
        "expect: {contains_all: [x]}",
        "expect: {contains_any_groups: [[]]}",
        "expect: {chart_changed: 1}",
        "expect: {numbers: [x]}",
        "judge: x",
        "notes: [x]",
    ],
)
def test_new_keys_reject_wrong_types(tmp_path: Path, extra: str) -> None:
    entry = f"- {{id: a, scenario: 1, dataset: t, question: q, {extra}}}"
    with pytest.raises(TypeError):
        runner.load_questions(write_questions(tmp_path, entry))


def test_scaled_numbers_read_separators_and_multipliers() -> None:
    text = "2 706 019 438 ₽, 1,16 млрд, 43 %, 1 159 744 622,42 и 5 тыс."

    assert runner.scaled_numbers(text) == [2706019438.0, 1.16e9, 43.0, 1159744622.42, 5000.0]


def test_numbers_from_gold_with_default_tolerance() -> None:
    gold = {"numbers": [[[2706019438]]]}

    close_enough = answer("Выручка 2,71 млрд", gold=gold)
    too_far = answer("Выручка 2,8 млрд", gold=gold)

    assert runner.check_numbers({"sql": "x"}, close_enough, None) == runner.Check("numbers", True)
    check = runner.check_numbers({"sql": "x"}, too_far, None)
    assert not check.passed
    assert "найдено 0 из 1" in check.reason


def test_numbers_share_and_percent_count_as_one_number() -> None:
    share = answer("Отклонено 6,9 %", gold={"numbers": [[[0.069096]]]})
    percent = answer("Доля 0,0691", gold={"numbers": [[[6.9096]]]})

    assert runner.check_numbers({"sql": "x", "tolerance": 0.01}, share, None).passed
    assert runner.check_numbers({"sql": "x", "tolerance": 0.01}, percent, None).passed


def test_numbers_need_every_value_of_one_variant() -> None:
    gold = {"numbers": [[[100], [200]], [[300]]]}

    partial = answer("Есть 100, нет второго", gold=gold)
    other_variant = answer("Итого 300", gold=gold)

    check = runner.check_numbers({"any_of": [{"sql": "x"}, {"sql": "y"}]}, partial, None)
    assert not check.passed
    assert "найдено 1 из 2" in check.reason
    assert runner.check_numbers({"any_of": [{"sql": "x"}]}, other_variant, None).passed


def test_numbers_tolerance_abs_and_result_table() -> None:
    correlation = answer("Корреляция 0,77", gold={"numbers": [[[0.797205]]]})
    in_rows = answer("См. график", gold={"numbers": [[[60268], [74646]]]})
    in_rows.result_rows = [
        {"priority": "high", "count": 60268},
        {"priority": "low", "count": 74646},
    ]

    assert runner.check_numbers({"sql": "x", "tolerance_abs": 0.05}, correlation, None).passed
    assert not runner.check_numbers({"sql": "x"}, correlation, None).passed
    assert runner.check_numbers({"sql": "x"}, in_rows, None).passed


def test_numbers_and_range_fall_back_to_sql(sales_db: DuckDBManager) -> None:
    turn = answer("Электроника — 250, продукты — 200")

    assert runner.check_numbers(
        {"sql": "SELECT sum(revenue) FROM sales GROUP BY category ORDER BY 1 DESC LIMIT 2"},
        turn,
        sales_db,
    ).passed
    assert runner.check_range(
        {"sql": "SELECT 400, 600"}, answer("Ожидаем около 500"), sales_db
    ).passed
    check = runner.check_range({"sql": "SELECT 400, 600"}, answer("Ожидаем 700"), sales_db)
    assert not check.passed
    assert "[400; 600]" in check.reason


def test_range_from_gold_uses_text_only() -> None:
    gold = {"range": [297255738.9, 554639666.97]}
    in_rows = answer("Прогноз без числа", gold=gold)
    in_rows.result_rows = [{"forecast": 400000000}]

    assert runner.check_range({"sql": "x"}, answer("около 420 млн", gold=gold), None).passed
    assert not runner.check_range({"sql": "x"}, in_rows, None).passed


def test_contains_checks_search_visible_text() -> None:
    turn = answer("Данные с октября 2024", ["по 30.09.2026"], ["bar"])
    turn.events.insert(0, {"type": "chart", "data": {"spec": {}, "caption": "Отправления"}})

    assert runner.check_contains_any(["отправлен", "посыл"], turn, None).passed
    assert runner.check_contains_all([["2024"], ["2026"]], turn, None).passed
    check = runner.check_contains_any_groups([["октябр"], ["сентябр", "2026-09"]], turn, None)
    assert check == runner.Check(
        "contains_any_groups", False, "в ответе нет ни одного из ['сентябр', '2026-09']"
    )


def test_contains_keeps_trailing_space_and_folds_yo() -> None:
    assert runner.check_contains_any(["0 "], answer("Дублей 0 строк"), None).passed
    assert not runner.check_contains_any(["0 "], answer("Дублей 10"), None).passed
    assert runner.check_contains_any(["не найден"], answer("Повторы НЕ НАЙДЕНЫ"), None).passed
    assert runner.check_contains_any(["ещё"], answer("Еще"), None).passed


def test_contains_any_sql_from_gold_and_from_sql(sales_db: DuckDBManager) -> None:
    gold = {"contains_any": ["Отправка уведомлений о заказах"]}
    turn = answer("Чаще всех падает «Отправка уведомлений о заказах»", gold=gold)

    assert runner.check_contains_any_sql("x", turn, None).passed
    sql = "SELECT category FROM sales GROUP BY category ORDER BY sum(revenue) DESC LIMIT 1"
    assert runner.check_contains_any_sql(sql, answer("Лидер — электроника"), sales_db).passed
    assert not runner.check_contains_any_sql(sql, answer("Лидер — одежда"), sales_db).passed


def test_chart_any_takes_last_chart() -> None:
    turn = answer("Распределение", charts=["bar", "line"])

    assert runner.check_chart_any(["line"], turn, None).passed
    assert not runner.check_chart_any(["bar"], turn, None).passed
    assert runner.check_chart(["bar"], turn, None).passed


def test_chart_changed_compares_with_parent_turn() -> None:
    changed = answer("Другой вид", charts=["pie"], parent_chart="bar")
    same = answer("Тот же вид", charts=["bar"], parent_chart="bar")
    none = answer("Без графика", parent_chart="bar")

    assert runner.check_chart_changed(True, changed, None).passed
    assert runner.check_chart_changed(True, same, None).reason == "график не сменился: bar -> bar"
    assert not runner.check_chart_changed(True, none, None).passed
    assert runner.check_turn({"chart_changed": False}, same, None) == []


def test_table_intact_uses_gold_or_count_before_turn() -> None:
    kept = answer("Удалять не буду", gold={"row_count": 500000}, table_rows=500000)
    dropped = answer("Удалил", table_rows_before=10, table_rows=7)
    unknown = answer("Удалять не буду")

    assert runner.check_table_intact(True, kept, None).passed
    assert runner.check_table_intact(True, dropped, None).reason == "строк 7 из 10"
    assert not runner.check_table_intact(True, unknown, None).passed


def test_table_rows_counts_and_survives_missing_table(sales_db: DuckDBManager) -> None:
    assert runner.table_rows(sales_db, "sales") == 4
    assert runner.table_rows(sales_db, "nope") is None


def test_columns_recall_only_skips_columns_check() -> None:
    turn = answer("Ответ")
    turn.steps = [{"tool": "aggregate", "columns": ["revenue"], "arguments": {}}]

    [strict] = runner.check_turn({"columns_all": ["category"]}, turn, None)
    assert not strict.passed
    assert runner.check_turn({"columns_all": ["category"]}, turn, None, True) == []


def test_judge_key_is_accepted_and_ignored(tmp_path: Path) -> None:
    path = write_questions(
        tmp_path,
        "- {id: a, scenario: 1, dataset: t, question: q, judge: [period_full], notes: n}",
    )

    [question] = runner.load_questions(path)
    assert question.expect == {}
    assert runner.check_turn(question.expect, answer("Ответ"), None) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"error": "RateLimitError: Error code: 429 - too many requests"},
        {
            "events": [
                {
                    "type": "progress",
                    "data": {"stage": "planning", "status": "failed", "error": "concurrency_limit"},
                },
                {"type": "error", "data": {"message": "Не удалось"}},
            ]
        },
    ],
)
def test_api_failed_detects_provider_limits(fields: dict) -> None:
    assert runner.api_failed(runner.Turn(**fields))


def test_api_failed_ignores_ordinary_failures() -> None:
    turn = runner.Turn(events=[{"type": "error", "data": {"message": "LLM недоступна"}}])

    assert not runner.api_failed(turn)
    assert not runner.api_failed(answer("Ответ"))


def result(question_id: str, passed: bool, repeat: int = 1, **fields) -> dict:
    return {
        "id": question_id,
        "scenario": 1,
        "dataset": "sales",
        "passed": passed,
        "failures": [] if passed else ["numbers: нет"],
        "first_event_s": 0.2,
        "done_s": 3.0,
        "selection_covered": None,
        "block_chars": None,
        "prompt_truncated": 0,
        "errored": False,
        "session_id": f"eval-{question_id}",
        "profiled": True,
        "error": None,
        "repeat": repeat,
        "api_error": False,
        **fields,
    }


def test_summarize_excludes_api_errors_and_counts_pass_k() -> None:
    results = [
        result("a", True, 1),
        result("b", False, 1),
        result("a", True, 2),
        result("b", True, 2),
        result("c", False, 1, api_error=True, errored=True),
        result("c", True, 2),
    ]

    report = runner.summarize(results, "fake", FIXTURE, date(2026, 10, 26))

    assert (report["total"], report["passed"], report["api_errors"]) == (5, 4, 1)
    assert report["scenarios"]["1"]["total"] == 5
    pass_k = report["pass_k"]
    assert (pass_k["repeat"], pass_k["questions"]) == (2, 3)
    assert (pass_k["always"], pass_k["at_least_once"]) == (2, 3)
    assert pass_k["by_question"]["b"] == {"passed": 1, "runs": 2}
    assert pass_k["by_question"]["c"] == {"passed": 1, "runs": 1}
    text = runner.render(report)
    assert "Исключено: ход сорван лимитом API провайдера — 1" in text
    assert "pass^2 (во всех повторах) — 2 из 3 вопросов, хотя бы раз — 3" in text
    assert "- `b` (сценарий 1, `sales`, повтор 1): numbers: нет" in text
    assert "`c`" not in text


def test_single_run_report_has_no_repeat_line() -> None:
    report = runner.summarize([result("a", False)], "fake", FIXTURE, date(2026, 10, 26))

    text = runner.render(report)
    assert "pass^" not in text
    assert "повтор" not in text
    assert "Исключено" not in text


async def test_run_questions_repeats_and_links_parent_chart(sales_db: DuckDBManager) -> None:
    trace = runner.Trace()
    orchestrator = runner.build_orchestrator(sales_db, trace, llm=FakeLLM())
    questions = runner.load_questions(FIXTURE)[:2]
    options = runner.RunOptions(repeat=2, reply_to=True)

    results = await runner.run_questions(questions, sales_db, orchestrator, trace, None, options)

    assert [(item["id"], item["repeat"]) for item in results] == [
        ("sales-01", 1),
        ("sales-02", 1),
        ("sales-01", 2),
        ("sales-02", 2),
    ]
    assert results[0]["session_id"] == results[1]["session_id"]
    assert results[0]["session_id"] != results[2]["session_id"]
    assert not any(item["api_error"] for item in results)


async def test_run_turn_passes_reply_to_only_when_supported(monkeypatch) -> None:
    requests = []

    class Recorder:
        async def run(self, request):
            requests.append(request)
            yield {"type": "done", "data": {"turn_id": "t-1"}}

    turn = await runner.run_turn(Recorder(), runner.Trace(), "session", "вопрос", "t-0")

    assert runner.turn_id_of(turn) == "t-1"
    assert getattr(requests[0], "reply_to", None) == (
        "t-0" if runner.reply_to_supported() else None
    )


def test_gold_numbers_override_sql(sales_db: DuckDBManager) -> None:
    turn = answer("Итого 999", gold={"numbers": [[[999]]]})

    assert runner.check_numbers({"sql": "SELECT 1"}, turn, sales_db).passed


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_repeat_must_be_positive(value: str) -> None:
    with pytest.raises(SystemExit):
        runner.parse_args(["--repeat", value])


RUBRIC_POINTS = ("answers_question", "grounded", "business_readable")


class FakeJudgeClient:
    def __init__(self, verdict: bool) -> None:
        self.verdict = verdict
        self.prompts: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        items = {point: {"pass": self.verdict, "why": "проверено"} for point in RUBRIC_POINTS}
        return json.dumps({"items": items}, ensure_ascii=False)


@pytest.mark.parametrize("verdict", [True, False])
async def test_judge_verdict_joins_the_checks(sales_db, verdict: bool) -> None:
    client = FakeJudgeClient(verdict)
    options = runner.RunOptions(judge_client=client, judge_votes=1)
    question = runner.Question(id="q", scenario=2, dataset="sales", text="Сколько продаж?")
    verdicts = await runner.judge_turn(question, answer("Продаж 4"), options, None)
    assert verdicts
    assert all(passed is verdict for passed, _ in verdicts.values())
    assert "Сколько продаж?" in client.prompts[0]


def test_awkward_scenario_accepts_clarification(sales_db) -> None:
    clarified = runner.Turn(
        events=[
            {"type": "clarification_needed", "data": {"error": "Не удалось подобрать инструменты"}}
        ],
        end_s=0.5,
    )
    assert runner.check_turn({}, clarified, sales_db, awkward=True) == []
    assert runner.check_turn({}, clarified, sales_db)[0].name == "turn"


def test_report_shows_checks_without_judge() -> None:
    judged = [{"name": "judge.grounded", "passed": False, "reason": "0/1"}]
    results = [result("a", False, checks=judged, passed_checks=True)]
    report = runner.summarize(results, "fake", FIXTURE, date(2026, 10, 26))
    assert (report["judged"], report["checks_passed"]) == (True, 1)
    assert "Без судьи, только проверки: 1 (100 %)" in runner.render(report)
