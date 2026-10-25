import importlib.util
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_eval_questions.py"
TOOLS = {"group_by", "aggregate", "filter_rows"}
CHART_TYPES = {"bar", "line", "table", "kpi"}


def load_script():
    spec = importlib.util.spec_from_file_location("check_eval_questions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_script()


def make_question(question_id: str = "sales-01", **fields) -> dict:
    question = {
        "id": question_id,
        "scenario": 2,
        "dataset": "sales",
        "question": "Какой регион чаще всего?",
        "follow_up_of": None,
        "expect": {
            "tools_any": ["group_by"],
            "columns_all": ["region"],
            "answer_contains": [],
            "value": {"sql": "SELECT 1", "tolerance": 0},
            "chart_type_any": ["bar"],
            "refusal": False,
        },
    }
    question.update(fields)
    return question


def with_expect(**fields) -> dict:
    question = make_question()
    question["expect"].update(fields)
    return question


def problems_of(question: dict) -> list[str]:
    return checker.question_problems(question, TOOLS, CHART_TYPES)


def full_set() -> list[dict]:
    questions = []
    for scenario in range(1, 7):
        for dataset_index in range(5):
            question = make_question(f"s{scenario}-{dataset_index}", scenario=scenario)
            question["dataset"] = f"table{dataset_index}"
            if scenario == 6:
                question["follow_up_of"] = f"s1-{dataset_index}"
            questions.append(question)
    for awkward_index in range(5):
        question = make_question(f"awkward-{awkward_index}", scenario=0)
        question["expect"].update(value=None, refusal=True)
        questions.append(question)
    return questions


def test_committed_question_set_passes_validation():
    questions = checker.load_questions(checker.DEFAULT_QUESTIONS)

    problems = checker.validate(questions, checker.known_tools(), checker.known_chart_types())

    assert problems == {}


def test_committed_question_set_fills_columns_for_every_answered_question():
    questions = checker.load_questions(checker.DEFAULT_QUESTIONS)

    unfilled = [
        question["id"]
        for question in questions
        if not question["expect"]["columns_all"]
        and not question["expect"]["refusal"]
        and question["scenario"] != 1
    ]

    assert unfilled == []


def test_valid_question_has_no_problems():
    assert problems_of(make_question()) == []


def test_null_value_is_allowed():
    assert problems_of(with_expect(value=None)) == []


def test_unknown_tool_is_reported():
    problems = problems_of(with_expect(tools_any=["group_by", "magic"]))

    assert any("magic" in problem for problem in problems)


def test_unknown_chart_type_is_reported():
    problems = problems_of(with_expect(chart_type_any=["pie"]))

    assert any("pie" in problem for problem in problems)


def test_missing_expect_key_is_reported():
    question = make_question()
    del question["expect"]["columns_all"]

    assert any("columns_all" in problem for problem in problems_of(question))


def test_extra_question_key_is_reported():
    question = make_question(comment="лишнее")

    assert any("comment" in problem for problem in problems_of(question))


@pytest.mark.parametrize(
    "value",
    [
        {"sql": "SELECT 1"},
        {"sql": "", "tolerance": 0},
        {"sql": "SELECT 1", "tolerance": -0.1},
        {"sql": "SELECT 1", "tolerance": "2%"},
        "SELECT 1",
    ],
)
def test_malformed_value_is_reported(value):
    assert any("value" in problem for problem in problems_of(with_expect(value=value)))


def test_refusal_with_reference_value_is_reported():
    problems = problems_of(with_expect(refusal=True))

    assert any("отказ" in problem for problem in problems)


def test_refusal_must_be_boolean():
    assert any("refusal" in problem for problem in problems_of(with_expect(refusal="yes")))


@pytest.mark.parametrize("scenario", [-1, 7, True, "2"])
def test_scenario_outside_range_is_reported(scenario):
    problems = problems_of(make_question(scenario=scenario))

    assert any("scenario" in problem for problem in problems)


def test_id_with_spaces_is_reported():
    assert any("id" in problem for problem in problems_of(make_question("sales 01")))


def test_repeated_column_is_reported():
    problems = problems_of(with_expect(columns_all=["region", "region"]))

    assert any("повторы" in problem for problem in problems)


def test_follow_up_scenario_needs_previous_question():
    problems = problems_of(make_question(scenario=6))

    assert any("follow_up_of" in problem for problem in problems)


def test_follow_up_to_unknown_question_is_reported():
    questions = [make_question("first"), make_question("second", follow_up_of="missing")]

    assert "second" in checker.follow_up_problems(questions)


def test_follow_up_to_later_question_is_reported():
    questions = [make_question("first", follow_up_of="second"), make_question("second")]

    assert "first" in checker.follow_up_problems(questions)


def test_follow_up_to_other_dataset_is_reported():
    other = make_question("first", dataset="support")
    questions = [other, make_question("second", follow_up_of="first")]

    problems = checker.follow_up_problems(questions)

    assert any("другой таблице" in problem for problem in problems["second"])


def test_repeated_id_is_reported():
    questions = [make_question("same"), make_question("same")]

    assert "same" in checker.follow_up_problems(questions)


def test_full_set_has_enough_coverage():
    assert checker.coverage_problems(full_set()) == []


def test_scenario_with_too_few_datasets_is_reported():
    questions = full_set()
    for question in questions:
        if question["scenario"] == 3:
            question["dataset"] = "table0"

    problems = checker.coverage_problems(questions)

    assert any("сценарий 3" in problem for problem in problems)


def test_too_few_refusals_are_reported():
    questions = [question for question in full_set() if question["id"] != "awkward-0"]

    problems = checker.coverage_problems(questions)

    assert any("refusal" in problem for problem in problems)


def test_validate_rejects_non_list_file():
    assert checker.validate({"id": "x"}, TOOLS, CHART_TYPES) != {}


@pytest.mark.parametrize(
    ("reference", "candidate", "tolerance", "expected"),
    [
        (100, 101, 0.02, True),
        (100, 103, 0.02, False),
        (2.5, 2.5, 0, True),
        (0, 0.001, 0.01, True),
        (0, 0.5, 0.01, False),
        ("Банковская карта", " банковская карта ", 0, True),
        ("Доставка", "Оплата", 0, False),
        (24, None, 0, False),
    ],
)
def test_matches_compares_with_relative_tolerance(reference, candidate, tolerance, expected):
    assert checker.matches(reference, candidate, tolerance) is expected


@pytest.fixture
def sales_db(tmp_path):
    path = tmp_path / "eval.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE sales (region VARCHAR, amount DOUBLE)")
    connection.execute("INSERT INTO sales VALUES ('Пермь', 10), ('Пермь', 20), ('Омск', 5)")
    connection.close()
    return path


def test_database_check_runs_reference_query(sales_db):
    question = with_expect(value={"sql": "SELECT sum(amount) FROM sales", "tolerance": 0})

    [result] = checker.check_database([question], sales_db)

    assert result["problems"] == []
    assert result["value"] == "35.0"


def test_database_check_reports_missing_column(sales_db):
    question = with_expect(columns_all=["region", "revenue"])

    [result] = checker.check_database([question], sales_db)

    assert any("revenue" in problem for problem in result["problems"])


def test_database_check_reports_missing_table(sales_db):
    question = make_question(dataset="support")

    [result] = checker.check_database([question], sales_db)

    assert any("support" in problem for problem in result["problems"])


@pytest.mark.parametrize(
    "sql",
    ["SELECT region FROM sales", "SELECT region, amount FROM sales LIMIT 1", "SELECT nope"],
)
def test_database_check_reports_non_scalar_or_broken_query(sales_db, sql):
    question = with_expect(value={"sql": sql, "tolerance": 0})

    [result] = checker.check_database([question], sales_db)

    assert any("value.sql" in problem for problem in result["problems"])


def test_database_check_skips_null_value(sales_db):
    [result] = checker.check_database([with_expect(value=None)], sales_db)

    assert result["problems"] == []
    assert result["value"] == "—"
