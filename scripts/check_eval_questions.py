import argparse
import math
import numbers
import re
import sys
import time
from collections import Counter
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_QUESTIONS = ROOT / "data" / "eval" / "questions.yaml"
QUESTION_KEYS = {"id", "scenario", "dataset", "question", "follow_up_of", "expect"}
EXPECT_KEYS = {
    "tools_any",
    "columns_all",
    "answer_contains",
    "value",
    "chart_type_any",
    "refusal",
}
OPTIONAL_EXPECT_KEYS = {"dashboard"}
VALUE_KEYS = {"sql", "tolerance"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SCENARIOS = range(7)
AWKWARD_SCENARIO = 0
FOLLOW_UP_SCENARIO = 6
MIN_PER_SCENARIO = 5
MIN_DATASETS_PER_SCENARIO = 5
MIN_AWKWARD = 5
MEMORY_LIMIT = "1GB"


def load_questions(path: Path) -> list:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or []


def known_tools() -> set[str]:
    from app.core.tools.registry import tool_registry

    return {tool.name for tool in tool_registry.planner_tools()}


def known_chart_types() -> set[str]:
    from app.core.viz.vega_spec_builder import CHART_TYPES

    return set(CHART_TYPES)


def is_text_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def is_number(value) -> bool:
    return isinstance(value, numbers.Number) and not isinstance(value, bool)


def value_problems(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) != VALUE_KEYS:
        return ["value: null или {sql, tolerance}"]
    problems = []
    if not isinstance(value["sql"], str) or not value["sql"].strip():
        problems.append("value.sql: пустой запрос")
    tolerance = value["tolerance"]
    if not is_number(tolerance) or tolerance < 0 or not math.isfinite(tolerance):
        problems.append("value.tolerance: неотрицательное число")
    return problems


def unknown_names(names, known: set[str]) -> list[str]:
    return sorted(set(names) - known) if is_text_list(names) else []


def repeated_names(names) -> list[str]:
    if not is_text_list(names):
        return []
    return [name for name, count in Counter(names).items() if count > 1]


def expect_problems(expect, tools: set[str], chart_types: set[str]) -> list[str]:
    if not isinstance(expect, dict):
        return ["expect: должен быть словарём"]
    problems = []
    if missing := EXPECT_KEYS - set(expect):
        problems.append(f"expect: нет ключей {sorted(missing)}")
    if extra := set(expect) - EXPECT_KEYS - OPTIONAL_EXPECT_KEYS:
        problems.append(f"expect: лишние ключи {sorted(extra)}")
    for key in ("tools_any", "columns_all", "answer_contains", "chart_type_any"):
        if key in expect and not is_text_list(expect[key]):
            problems.append(f"expect.{key}: список непустых строк")
    if unknown := unknown_names(expect.get("tools_any"), tools):
        problems.append(f"expect.tools_any: нет таких тулов {unknown}")
    if unknown := unknown_names(expect.get("chart_type_any"), chart_types):
        problems.append(f"expect.chart_type_any: нет таких типов {unknown}")
    if repeated := repeated_names(expect.get("columns_all")):
        problems.append(f"expect.columns_all: повторы {repeated}")
    problems += value_problems(expect.get("value"))
    if "dashboard" in expect and not isinstance(expect["dashboard"], bool):
        problems.append("expect.dashboard: true или false")
    refusal = expect.get("refusal")
    if not isinstance(refusal, bool):
        problems.append("expect.refusal: true или false")
    elif refusal and expect.get("value") is not None:
        problems.append("expect.value: у отказа эталона нет, нужен null")
    return problems


def question_problems(entry, tools: set[str], chart_types: set[str]) -> list[str]:
    if not isinstance(entry, dict):
        return ["запись должна быть словарём"]
    problems = []
    if missing := QUESTION_KEYS - set(entry):
        problems.append(f"нет ключей {sorted(missing)}")
    if extra := set(entry) - QUESTION_KEYS:
        problems.append(f"лишние ключи {sorted(extra)}")
    if not isinstance(entry.get("id"), str) or not ID_PATTERN.match(entry["id"]):
        problems.append("id: строчные латинские буквы, цифры и дефисы")
    scenario = entry.get("scenario")
    if isinstance(scenario, bool) or scenario not in SCENARIOS:
        problems.append("scenario: целое от 0 до 6")
    for key in ("dataset", "question"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            problems.append(f"{key}: непустая строка")
    follow_up = entry.get("follow_up_of")
    if follow_up is not None and not isinstance(follow_up, str):
        problems.append("follow_up_of: null или id вопроса")
    if scenario == FOLLOW_UP_SCENARIO and follow_up is None:
        problems.append("follow_up_of: у сценария 6 нужен предыдущий вопрос")
    if "expect" in entry:
        problems += expect_problems(entry["expect"], tools, chart_types)
    return problems


def follow_up_problems(questions: list) -> dict[str, list[str]]:
    seen: dict[str, dict] = {}
    problems: dict[str, list[str]] = {}
    for entry in questions:
        if not isinstance(entry, dict):
            continue
        question_id = entry.get("id")
        found = []
        if question_id in seen:
            found.append("id повторяется")
        parent_id = entry.get("follow_up_of")
        if isinstance(parent_id, str):
            parent = seen.get(parent_id)
            if parent is None:
                found.append(f"follow_up_of: «{parent_id}» не встречался выше")
            elif parent.get("dataset") != entry.get("dataset"):
                found.append(f"follow_up_of: «{parent_id}» задан по другой таблице")
        if found:
            problems.setdefault(str(question_id), []).extend(found)
        if isinstance(question_id, str):
            seen.setdefault(question_id, entry)
    return problems


def coverage(questions: list) -> dict[int, dict]:
    summary = {scenario: {"questions": 0, "datasets": set()} for scenario in SCENARIOS}
    for entry in questions:
        if isinstance(entry, dict) and entry.get("scenario") in summary:
            row = summary[entry["scenario"]]
            row["questions"] += 1
            row["datasets"].add(entry.get("dataset"))
    return summary


def coverage_problems(questions: list) -> list[str]:
    problems = []
    for scenario, row in coverage(questions).items():
        if scenario == AWKWARD_SCENARIO:
            continue
        if row["questions"] < MIN_PER_SCENARIO:
            problems.append(
                f"сценарий {scenario}: вопросов {row['questions']} < {MIN_PER_SCENARIO}"
            )
        if len(row["datasets"]) < MIN_DATASETS_PER_SCENARIO:
            problems.append(
                f"сценарий {scenario}: таблиц {len(row['datasets'])} < {MIN_DATASETS_PER_SCENARIO}"
            )
    refusals = sum(
        1
        for entry in questions
        if isinstance(entry, dict)
        and entry.get("scenario") == AWKWARD_SCENARIO
        and isinstance(entry.get("expect"), dict)
        and entry["expect"].get("refusal") is True
    )
    if refusals < MIN_AWKWARD:
        problems.append(f"неудобных вопросов с refusal: true {refusals} < {MIN_AWKWARD}")
    return problems


def validate(questions, tools: set[str], chart_types: set[str]) -> dict[str, list[str]]:
    if not isinstance(questions, list):
        return {"<файл>": ["ожидается список вопросов"]}
    problems: dict[str, list[str]] = {}
    for position, entry in enumerate(questions, start=1):
        found = question_problems(entry, tools, chart_types)
        if found:
            key = entry.get("id") if isinstance(entry, dict) else None
            problems.setdefault(str(key or f"#{position}"), []).extend(found)
    for question_id, found in follow_up_problems(questions).items():
        problems.setdefault(question_id, []).extend(found)
    if found := coverage_problems(questions):
        problems["<набор>"] = found
    return problems


def matches(reference, candidate, tolerance: float) -> bool:
    if candidate is None or reference is None:
        return candidate is None and reference is None
    if is_number(reference) and is_number(candidate):
        expected, actual = float(reference), float(candidate)
        if expected == 0:
            return abs(actual) <= tolerance
        return abs(actual - expected) <= tolerance * abs(expected)
    return str(reference).strip().casefold() == str(candidate).strip().casefold()


def table_columns(connection, table: str) -> list[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [name for (name,) in rows]


def reference_value(connection, sql: str):
    cursor = connection.execute(sql)
    if len(cursor.description) != 1:
        raise ValueError(f"запрос вернул {len(cursor.description)} колонок, нужна одна")
    rows = cursor.fetchmany(2)
    if len(rows) != 1:
        raise ValueError(f"запрос вернул {len(rows)} строк, нужна одна")
    return rows[0][0]


def check_entry(connection, entry: dict, columns_by_table: dict) -> dict:
    table = entry["dataset"]
    if table not in columns_by_table:
        columns_by_table[table] = table_columns(connection, table)
    columns = columns_by_table[table]
    result = {"id": entry["id"], "dataset": table, "value": "", "ms": "", "problems": []}
    if not columns:
        result["problems"].append(f"таблицы {table} нет в базе")
        return result
    if missing := [name for name in entry["expect"]["columns_all"] if name not in columns]:
        result["problems"].append(f"columns_all: нет колонок {missing}")
    value = entry["expect"]["value"]
    if value is None:
        result["value"] = "—"
        return result
    started = time.perf_counter()
    try:
        result["value"] = repr(reference_value(connection, value["sql"]))
    except (duckdb.Error, ValueError) as error:
        result["problems"].append(f"value.sql: {type(error).__name__}: {error}".splitlines()[0])
    result["ms"] = str(round((time.perf_counter() - started) * 1000))
    return result


def check_database(questions: list, path: Path) -> list[dict]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        connection.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
        columns_by_table: dict[str, list[str]] = {}
        return [check_entry(connection, entry, columns_by_table) for entry in questions]
    finally:
        connection.close()


def render_coverage(questions: list) -> str:
    lines = ["| Сценарий | Вопросов | Таблиц | Таблицы |", "|---|---|---|---|"]
    for scenario, row in coverage(questions).items():
        datasets = ", ".join(sorted(str(name) for name in row["datasets"]))
        lines.append(f"| {scenario} | {row['questions']} | {len(row['datasets'])} | {datasets} |")
    per_dataset = Counter(entry.get("dataset") for entry in questions)
    lines += ["", "| Таблица | Вопросов |", "|---|---|"]
    lines += [f"| {name} | {count} |" for name, count in sorted(per_dataset.items())]
    return "\n".join(lines)


def render_results(results: list[dict]) -> str:
    lines = ["| id | Таблица | Эталон | мс | Проблемы |", "|---|---|---|---|---|"]
    for result in results:
        cells = [result["id"], result["dataset"], result["value"], result["ms"]]
        cells.append("; ".join(result["problems"]) or "ok")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка eval-набора вопросов (Q-01)")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--db", type=Path, help="DuckDB с загруженными датасетами")
    arguments = parser.parse_args()

    questions = load_questions(arguments.questions)
    problems = validate(questions, known_tools(), known_chart_types())
    for question_id, found in problems.items():
        for problem in found:
            print(f"✗ {question_id}: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"Вопросов: {len(questions)}, схема в порядке.\n")
    print(render_coverage(questions))
    if arguments.db is None:
        return 0
    if not arguments.db.is_file():
        print(f"Нет базы {arguments.db}, запустите make hk-data", file=sys.stderr)
        return 1
    results = check_database(questions, arguments.db)
    print()
    print(render_results(results))
    return 1 if any(result["problems"] for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
