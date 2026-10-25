#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_QUESTIONS = ROOT / "data" / "eval" / "questions.yaml"
DEFAULT_OUT_DIR = ROOT / "docs" / "reports"
QUESTION_KEYS = ("id", "scenario", "dataset", "question", "follow_up_of", "expect")
REQUIRED_KEYS = ("id", "scenario", "dataset", "question")
EXPECT_KEYS = ("tools_any", "columns_all", "answer_contains", "value", "chart_type_any", "refusal")
TURN_END_EVENTS = ("done", "clarification_needed")
REFUSAL_EVENTS = ("refusal", "clarification_needed", "error")
RESULT_ROWS = 1000
VALUE_EPSILON = 1e-9
SHOWN_VALUES = 5
FAKE_MODEL = "fake"

logger = logging.getLogger("eval")
WATCHED_PLANNER_FUNCTIONS = ("select_columns", "describe_columns", "describe_other_columns")


@dataclass(frozen=True)
class Question:
    id: str
    scenario: int
    dataset: str
    text: str
    follow_up_of: str | None = None
    expect: dict = field(default_factory=dict)


@dataclass
class Selection:
    columns: list[str]
    block_chars: int = 0


@dataclass
class Trace:
    steps: list[dict] = field(default_factory=list)
    selections: list[Selection] = field(default_factory=list)


@dataclass
class Turn:
    events: list[dict] = field(default_factory=list)
    first_event_s: float | None = None
    end_s: float | None = None
    steps: list[dict] = field(default_factory=list)
    selections: list[Selection] = field(default_factory=list)
    error: str | None = None
    result_rows: list[dict] | None = None
    result_error: str | None = None


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str = ""


def load_questions(path: Path) -> list[Question]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or []
    if not isinstance(entries, list):
        raise TypeError(f"{path}: ожидается список вопросов")
    questions: list[Question] = []
    for number, entry in enumerate(entries, 1):
        known = {question.id for question in questions}
        questions.append(parse_question(entry, known, f"{path}, вопрос {number}"))
    return questions


def parse_question(entry: object, known: set[str], where: str) -> Question:
    if not isinstance(entry, dict):
        raise TypeError(f"{where}: ожидается словарь")
    missing = [key for key in REQUIRED_KEYS if entry.get(key) in (None, "")]
    if missing:
        raise ValueError(f"{where}: нет полей {', '.join(missing)}")
    unknown = sorted(set(entry) - set(QUESTION_KEYS))
    if unknown:
        raise ValueError(f"{where}: неизвестные поля {', '.join(unknown)}")
    question_id = str(entry["id"])
    if question_id in known:
        raise ValueError(f"{where}: id {question_id} повторяется")
    parent = entry.get("follow_up_of")
    if parent is not None and str(parent) not in known:
        raise ValueError(f"{where}: follow_up_of {parent} должен стоять выше в наборе")
    expect = entry.get("expect") or {}
    if not isinstance(expect, dict):
        raise TypeError(f"{where}: expect должен быть словарём")
    unknown = sorted(set(expect) - set(EXPECT_KEYS))
    if unknown:
        raise ValueError(f"{where}: неизвестные проверки {', '.join(unknown)}")
    value = expect.get("value")
    if value is not None and not (isinstance(value, dict) and value.get("sql")):
        raise ValueError(f"{where}: value должен содержать sql")
    return Question(
        id=question_id,
        scenario=int(entry["scenario"]),
        dataset=str(entry["dataset"]),
        text=str(entry["question"]),
        follow_up_of=None if parent is None else str(parent),
        expect=expect,
    )


def select_questions(
    questions: list[Question], per_scenario: int | None = None, ids: list[str] | None = None
) -> list[Question]:
    by_id = {question.id: question for question in questions}
    unknown = sorted(set(ids or ()) - set(by_id))
    if unknown:
        raise ValueError(f"нет вопросов {', '.join(unknown)}")
    picked: set[str] = set()
    taken: Counter = Counter()
    for question in questions:
        if ids and question.id not in ids:
            continue
        if per_scenario is not None and taken[question.scenario] >= per_scenario:
            continue
        taken[question.scenario] += 1
        picked.add(question.id)
    for question_id in list(picked):
        parent = by_id[question_id].follow_up_of
        while parent is not None:
            picked.add(parent)
            parent = by_id[parent].follow_up_of
    return [question for question in questions if question.id in picked]


@contextmanager
def watch_columns(selection: Selection) -> Iterator[None]:
    from app.core.agent import planner as planner_module

    select = planner_module.select_columns
    describe = planner_module.describe_columns
    describe_others = planner_module.describe_other_columns
    blocks = {"described": "", "others": ""}

    def recorded_select(*args, **kwargs):
        selected = select(*args, **kwargs)
        selection.columns = [str(name) for name in selected]
        return selected

    def recorded_describe(*args, **kwargs):
        blocks["described"] = describe(*args, **kwargs)
        return blocks["described"]

    def recorded_others(*args, **kwargs):
        blocks["others"] = describe_others(*args, **kwargs)
        return blocks["others"]

    with (
        patch.object(planner_module, "select_columns", recorded_select),
        patch.object(planner_module, "describe_columns", recorded_describe),
        patch.object(planner_module, "describe_other_columns", recorded_others),
    ):
        try:
            yield
        finally:
            described, others = blocks["described"], blocks["others"]
            selection.block_chars = len(f"{described}\n{others}" if others else described)


class RecordingPlanner:
    def __init__(self, inner, trace: Trace) -> None:
        from app.core.agent import planner as planner_module

        missing = [name for name in WATCHED_PLANNER_FUNCTIONS if not hasattr(planner_module, name)]
        if missing:
            raise RuntimeError(f"в planner.py нет {', '.join(missing)}: обновите scripts/eval.py")
        self._inner = inner
        self.trace = trace

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def plan(self, question: str, context: dict):
        selection = Selection(columns=[str(name) for name in context.get("columns", [])])
        try:
            with watch_columns(selection):
                return await self._inner.plan(question, context)
        finally:
            self.trace.selections.append(selection)


class RecordingExecutor:
    def __init__(self, inner, trace: Trace) -> None:
        self._inner = inner
        self.trace = trace

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def execute(self, step, *args, **kwargs) -> str:
        table = await self._inner.execute(step, *args, **kwargs)
        self.trace.steps.append(
            {"tool": step.tool, "columns": list(step.columns), "arguments": dict(step.arguments)}
        )
        return table


def build_orchestrator(db, trace: Trace, llm=None):
    from app.core.agent.insight import InsightGenerator
    from app.core.agent.orchestrator import AgentOrchestrator
    from app.core.agent.planner import StepPlanner
    from app.core.agent.step_executor import StepExecutor

    return AgentOrchestrator(
        planner=RecordingPlanner(StepPlanner(llm=llm), trace),
        executor=RecordingExecutor(StepExecutor(db), trace),
        insight_gen=InsightGenerator(llm=llm, db=db),
        db=db,
    )


async def run_turn(orchestrator, trace: Trace, session_id: str, text: str) -> Turn:
    from app.api.schemas.chat import ChatRequest

    trace.steps, trace.selections = [], []
    turn = Turn()
    started = time.perf_counter()
    try:
        async for event in orchestrator.run(ChatRequest(session_id=session_id, question=text)):
            elapsed = round(time.perf_counter() - started, 3)
            if turn.first_event_s is None:
                turn.first_event_s = elapsed
            turn.events.append(event)
            if event["type"] in TURN_END_EVENTS:
                turn.end_s = elapsed
    except Exception as error:
        logger.exception("ход %s упал", session_id)
        turn.error = f"{type(error).__name__}: {error}"
    turn.steps, turn.selections = list(trace.steps), list(trace.selections)
    return turn


def read_result(turn: Turn, db) -> None:
    tables = [
        event["data"]["result_table"]
        for event in turn.events
        if event["type"] == "chart" and event["data"].get("result_table")
    ]
    if not tables:
        turn.result_error = "в ответе нет result_table"
        return
    try:
        turn.result_rows = db.to_records(tables[-1], limit=RESULT_ROWS)
    except duckdb.Error as error:
        turn.result_error = f"result_table {tables[-1]} не прочитана: {error}"


def events_of(turn: Turn, kind: str) -> list[dict]:
    return [event["data"] for event in turn.events if event["type"] == kind]


def used_tools(turn: Turn) -> list[str]:
    tools = [step["tool"] for step in turn.steps]
    tools += [
        data["tool"]
        for data in events_of(turn, "progress")
        if data.get("stage") == "step" and data.get("status") == "finished" and data.get("tool")
    ]
    return list(dict.fromkeys(tool for tool in tools if tool))


def answer_text(turn: Turn) -> str:
    parts = []
    for data in events_of(turn, "text"):
        parts.append(str(data.get("headline", "")))
        parts += [str(detail) for detail in data.get("details", [])]
    return "\n".join(parts)


def chart_types(turn: Turn) -> list[str]:
    kinds = []
    for data in events_of(turn, "chart"):
        spec = data.get("spec") or {}
        mark = spec.get("mark")
        kind = (spec.get("usermeta") or {}).get("chart_type")
        kind = kind or (mark.get("type") if isinstance(mark, dict) else mark)
        if kind:
            kinds.append(str(kind))
    return kinds


def refused(turn: Turn) -> bool:
    kinds = {event["type"] for event in turn.events}
    if turn.error is not None or kinds & set(REFUSAL_EVENTS):
        return True
    return "chart" not in kinds and not turn.steps


def mentioned(column: str, steps: list[dict]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(column)}(?!\w)", re.IGNORECASE)
    return any(
        pattern.search(
            json.dumps([step["columns"], step["arguments"]], ensure_ascii=False, default=str)
        )
        for step in steps
    )


def numbers(rows: list[dict]) -> list[float]:
    found = []
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
                continue
            if not math.isnan(float(value)):
                found.append(float(value))
    return found


def close(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance * abs(expected) + VALUE_EPSILON


def shown(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values[:SHOWN_VALUES]) or "нет"


def check_tools(expected: list[str], turn: Turn, db) -> Check:
    tools = used_tools(turn)
    if set(tools) & set(expected):
        return Check("tools_any", True)
    return Check(
        "tools_any", False, f"инструменты {', '.join(tools) or 'нет'}, ждали один из {expected}"
    )


def check_columns(expected: list[str], turn: Turn, db) -> Check:
    missing = [column for column in expected if not mentioned(column, turn.steps)]
    if not missing:
        return Check("columns_all", True)
    return Check("columns_all", False, f"в шагах нет колонок {', '.join(missing)}")


def check_answer(expected: list[str], turn: Turn, db) -> Check:
    text = answer_text(turn).casefold()
    missing = [part for part in expected if str(part).casefold() not in text]
    if not missing:
        return Check("answer_contains", True)
    return Check("answer_contains", False, f"в ответе нет {', '.join(map(str, missing))}")


def check_value(expected: dict, turn: Turn, db) -> Check:
    if turn.result_rows is None:
        return Check("value", False, turn.result_error or "в ответе нет result_table")
    tolerance = float(expected.get("tolerance") or 0)
    try:
        reference = numbers(db.query(expected["sql"]))
    except duckdb.Error as error:
        return Check("value", False, f"эталонный SQL не выполнился: {error}")
    if not reference:
        return Check("value", False, "эталонный SQL не вернул чисел")
    actual = numbers(turn.result_rows)
    missing = [
        value for value in reference if not any(close(found, value, tolerance) for found in actual)
    ]
    if not missing:
        return Check("value", True)
    return Check(
        "value",
        False,
        f"в result_table нет {shown(missing)} (допуск {tolerance:g}), есть {shown(actual)}",
    )


def check_chart(expected: list[str], turn: Turn, db) -> Check:
    kinds = chart_types(turn)
    if set(kinds) & set(expected):
        return Check("chart_type_any", True)
    return Check(
        "chart_type_any", False, f"график {', '.join(kinds) or 'нет'}, ждали один из {expected}"
    )


def check_refusal(expected: bool, turn: Turn, db) -> Check:
    if refused(turn) == bool(expected):
        return Check("refusal", True)
    if expected:
        return Check("refusal", False, "ждали отказ, агент ответил по данным")
    return Check("refusal", False, "агент не ответил: " + ending(turn))


CHECKS = {
    "tools_any": check_tools,
    "columns_all": check_columns,
    "answer_contains": check_answer,
    "value": check_value,
    "chart_type_any": check_chart,
    "refusal": check_refusal,
}


def ending(turn: Turn) -> str:
    if turn.error is not None:
        return turn.error
    for data in events_of(turn, "clarification_needed"):
        return f"clarification_needed: {data.get('error', '')}"
    for data in events_of(turn, "error"):
        failed = [
            f"{progress['stage']}: {progress['error']}"
            for progress in events_of(turn, "progress")
            if progress.get("status") == "failed" and progress.get("error")
        ]
        return f"error: {data.get('message', '')}" + (f" ({failed[-1]})" if failed else "")
    if turn.end_s is None:
        return "ход не дошёл до done"
    return "нет графика"


def check_turn(expect: dict, turn: Turn, db) -> list[Check]:
    checks = []
    if turn.error is not None or turn.end_s is None:
        checks.append(Check("turn", False, ending(turn)))
    for key in EXPECT_KEYS:
        if expect.get(key) is not None:
            checks.append(CHECKS[key](expect[key], turn, db))
    return checks


def selection_covered(expected: list[str] | None, turn: Turn) -> bool | None:
    if not expected or not turn.selections:
        return None
    selected = {column.casefold() for column in turn.selections[0].columns}
    return all(str(column).casefold() in selected for column in expected)


def question_result(
    question: Question, dataset: str, session_id: str | None, turn: Turn, checks: list[Check]
) -> dict:
    first = turn.selections[0] if turn.selections else None
    truncated = sum(
        int(event["data"].get("prompt_truncated") or 0)
        for event in turn.events
        if event["type"] in TURN_END_EVENTS
    )
    return {
        "id": question.id,
        "scenario": question.scenario,
        "dataset": dataset,
        "question": question.text,
        "follow_up_of": question.follow_up_of,
        "session_id": session_id,
        "passed": all(check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
        "failures": [
            f"{check.name}: {' '.join(check.reason.split())}"
            for check in checks
            if not check.passed
        ],
        "first_event_s": turn.first_event_s,
        "done_s": turn.end_s,
        "ended": next(
            (event["type"] for event in reversed(turn.events) if event["type"] in TURN_END_EVENTS),
            None,
        ),
        "tools": used_tools(turn),
        "selected_columns": None if first is None else first.columns,
        "block_chars": None if first is None else first.block_chars,
        "selection_covered": selection_covered(question.expect.get("columns_all"), turn),
        "prompt_truncated": truncated,
        "error": turn.error,
    }


def open_session(question: Question, dataset: str, db, sessions: dict[str, str]) -> str:
    from app.core.memory.session_memory import SessionMemory

    if question.follow_up_of is not None:
        if question.follow_up_of not in sessions:
            raise LookupError(f"сессии вопроса {question.follow_up_of} нет")
        return sessions[question.follow_up_of]
    columns = db.columns(dataset)
    if not columns:
        raise LookupError(f"таблицы {dataset} нет в DuckDB (make hk-data)")
    session_id = f"eval-{question.id}-{uuid.uuid4().hex[:8]}"
    SessionMemory(session_id, db=db).set_table(dataset, columns)
    return session_id


async def run_questions(
    questions: list[Question], db, orchestrator, trace: Trace, dataset: str | None = None
) -> list[dict]:
    sessions: dict[str, str] = {}
    results = []
    for question in questions:
        table = dataset or question.dataset
        try:
            session_id = open_session(question, table, db, sessions)
        except LookupError as error:
            check = Check("session", False, str(error))
            results.append(question_result(question, table, None, Turn(), [check]))
            continue
        sessions[question.id] = session_id
        turn = await run_turn(orchestrator, trace, session_id, question.text)
        read_result(turn, db)
        checks = check_turn(question.expect, turn, db)
        results.append(question_result(question, table, session_id, turn, checks))
        print(f"  {'ok  ' if results[-1]['passed'] else 'FAIL'} {question.id}", flush=True)
    return results


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)), 1) - 1]


def rate(passed: int, total: int) -> float | None:
    return round(100 * passed / total, 1) if total else None


def timing(results: list[dict], key: str) -> dict:
    values = [result[key] for result in results if result[key] is not None]
    return {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "count": len(values)}


def summarize(results: list[dict], model: str, questions_path: Path, day: date) -> dict:
    scenarios = {}
    for scenario in sorted({result["scenario"] for result in results}):
        own = [result for result in results if result["scenario"] == scenario]
        passed = sum(result["passed"] for result in own)
        scenarios[str(scenario)] = {
            "total": len(own),
            "passed": passed,
            "pass_rate": rate(passed, len(own)),
        }
    measured = [result for result in results if result["selection_covered"] is not None]
    covered = sum(result["selection_covered"] for result in measured)
    blocks = [result["block_chars"] for result in results if result["block_chars"] is not None]
    passed = sum(result["passed"] for result in results)
    return {
        "model": model,
        "date": day.isoformat(),
        "questions_file": display_path(questions_path),
        "total": len(results),
        "passed": passed,
        "pass_rate": rate(passed, len(results)),
        "scenarios": scenarios,
        "timing": {
            "first_event_s": timing(results, "first_event_s"),
            "done_s": timing(results, "done_s"),
        },
        "column_selection": {
            "measured": len(measured),
            "covered": covered,
            "recall": round(covered / len(measured), 3) if measured else None,
            "mean_block_chars": round(sum(blocks) / len(blocks)) if blocks else None,
        },
        "prompt_truncated": sum(1 for result in results if result["prompt_truncated"]),
        "questions": results,
    }


def display_path(path: Path) -> str:
    resolved = Path(path).resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(path)


def cell(value: object) -> str:
    return "—" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def render(report: dict) -> str:
    lines = [
        f"# Eval: {report['model']}, {report['date']}",
        "",
        (
            f"Набор `{report['questions_file']}`: вопросов {report['total']}, "
            f"прошло {report['passed']} ({cell(report['pass_rate'])} %)."
        ),
    ]
    if report["prompt_truncated"]:
        lines.append(
            f"Сервер LLM обрезал промпты в {report['prompt_truncated']} вопросах: "
            "такой прогон не годится для сравнения."
        )
    lines += [
        "",
        "## По сценариям",
        "",
        "| Сценарий | Вопросов | Прошло | % |",
        "|---|---|---|---|",
    ]
    for scenario, stats in report["scenarios"].items():
        name = f"{scenario} (неудобные)" if scenario == "0" else scenario
        lines.append(
            f"| {name} | {stats['total']} | {stats['passed']} | {cell(stats['pass_rate'])} |"
        )
    first, done = report["timing"]["first_event_s"], report["timing"]["done_s"]
    lines += [
        "",
        "## Время, с",
        "",
        "| | p50 | p95 |",
        "|---|---|---|",
        f"| До первого события | {cell(first['p50'])} | {cell(first['p95'])} |",
        f"| До конца хода | {cell(done['p50'])} | {cell(done['p95'])} |",
    ]
    selection = report["column_selection"]
    recall = selection["recall"]
    lines += [
        "",
        "## Отбор колонок",
        "",
        (
            f"Recall отбора: {selection['covered']} из {selection['measured']} "
            f"({cell(None if recall is None else round(100 * recall, 1))} %): все "
            "`expect.columns_all` вопроса попали в колонки, отобранные для планировщика."
        ),
        f"Средний размер блока колонок в промпте: {cell(selection['mean_block_chars'])} символов.",
        "",
        "## Провалы",
        "",
    ]
    failed = [result for result in report["questions"] if not result["passed"]]
    for result in failed:
        lines.append(
            f"- `{result['id']}` (сценарий {result['scenario']}, `{result['dataset']}`): "
            + "; ".join(result["failures"])
        )
    if not failed:
        lines.append("Нет.")
    return "\n".join(lines) + "\n"


def model_slug(model: str) -> str:
    return re.sub(r"[^\w.-]+", "-", model).strip("-") or "model"


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{report['date']}_{model_slug(report['model'])}"
    markdown, data = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    markdown.write_text(render(report), encoding="utf-8")
    data.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
    )
    return markdown, data


def attach_csv(db, paths: list[Path]) -> list[str]:
    from app.core.data import ingest
    from app.core.data.connectors.csv import CSVConnector, table_name

    connector = CSVConnector(
        {table_name(Path(path).name): str(Path(path).resolve()) for path in paths}
    )
    with db.lock:
        return list(ingest.attach(connector, db))


async def prepare_model() -> bool:
    import httpx

    from app.config import settings
    from app.core.llm import ollama_setup
    from app.core.llm.client import LLMClient

    if settings.llm_prepare_model:
        async with httpx.AsyncClient() as http:
            if not await ollama_setup.prepare_once(http):
                print(f"LLM не готова: {ollama_setup.status()}", file=sys.stderr)
                return False
    else:
        ollama_setup.mark_ready()
    try:
        await LLMClient().warm_up()
    except Exception:
        logger.warning("контекст сервера LLM не узнали", exc_info=True)
    return True


def fake_mode() -> bool:
    return os.getenv("USE_FAKE_LLM", "false").lower() == "true"


def configure_environment(args: argparse.Namespace, storage: Path) -> None:
    if args.fake:
        os.environ["USE_FAKE_LLM"] = "true"
    elif args.model:
        os.environ["LLM_MODEL"] = args.model
    os.environ["STORAGE_DIR"] = str(storage)
    os.environ["SQLITE_PATH"] = str(storage / "sessions.sqlite")
    if args.db:
        os.environ["DUCKDB_PATH"] = str(Path(args.db).resolve())


async def evaluate(args: argparse.Namespace) -> int:
    from app.config import settings
    from app.core.data.duckdb_manager import DuckDBManager

    questions = select_questions(load_questions(args.questions), args.per_scenario, args.ids)
    model = FAKE_MODEL if fake_mode() else args.model or settings.llm_model
    if not fake_mode() and not await prepare_model():
        return 2
    try:
        db = DuckDBManager(args.db or settings.duckdb_path)
    except duckdb.IOException as error:
        print(f"DuckDB не открылась (backend запущен?): {error}", file=sys.stderr)
        return 2
    try:
        if args.csv:
            attach_csv(db, args.csv)
        print(f"Eval: модель {model}, вопросов {len(questions)}", flush=True)
        trace = Trace()
        orchestrator = build_orchestrator(db, trace)
        results = await run_questions(questions, db, orchestrator, trace, args.dataset)
    finally:
        db.close()
    report = summarize(results, model, args.questions, date.today())
    markdown, data = write_report(report, args.out_dir)
    print(f"Прошло {report['passed']} из {report['total']} ({cell(report['pass_rate'])} %)")
    print(f"Отчёт: {markdown}\nJSON:  {data}")
    return 1 if any(result["error"] for result in results) else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eval агента на наборе вопросов (Q-02)")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--model", help="LLM_MODEL на время прогона и имя в отчёте")
    parser.add_argument("--fake", action="store_true", help="FakeLLM вместо модели")
    parser.add_argument("--db", type=Path, help="DuckDB с датасетами (по умолчанию DUCKDB_PATH)")
    parser.add_argument("--csv", type=Path, nargs="+", help="подключить CSV перед прогоном")
    parser.add_argument("--dataset", help="задать всем вопросам эту таблицу")
    parser.add_argument("--per-scenario", type=int, help="не больше N вопросов на сценарий")
    parser.add_argument("--ids", nargs="+", help="только эти вопросы")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with tempfile.TemporaryDirectory(prefix="eval-") as storage:
        configure_environment(args, Path(storage))
        return asyncio.run(evaluate(args))


if __name__ == "__main__":
    sys.exit(main())
