#!/usr/bin/env python3
"""Прогон агента по eval-набору: каждая задача — новая сессия, цепочки — в одной.

Пишет в каталог прогона results.jsonl (запись на задачу и повтор): события хода,
итоговую таблицу результата, отобранные колонки, вызовы LLM и время. Проверки и
судья — отдельно, scripts/eval/score.py, чтобы их можно было перезапускать без
агента.

База агента копируется в каталог прогона: шаги агента пишут temp_result_*,
а рабочая база с паспортами остаётся чистой. Модель берётся из окружения
(LLM_BASE_URL, LLM_MODEL, LLM_API_KEY), как у backend.

    python3 scripts/eval/run_eval.py --db backend/storage/duckdb/main.duckdb \\
        --parallel 4 --repeat 1 [--only sales-s1-overview support-s6-priority]
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "data" / "eval" / "hk"
RESULT_ROWS = 200
# Ответ провайдера «слишком много запросов»: у vyceai на бесплатном плане до 4 одновременных.
API_LIMIT = re.compile(r"Error code: 429|rate_limit|concurrency_limit")

# Вызовы LLM текущей задачи: список задаёт run_task, пишет TaskLLM.
_calls: ContextVar[list | None] = ContextVar("eval_llm_calls", default=None)


class TaskLLM:
    """Прокси LLM-клиента: время и исход каждого вызова — в записи своей задачи."""

    def __init__(self, inner) -> None:
        self.inner = inner

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    async def complete(self, system: str, user: str, **kwargs):
        return await self._timed("complete", self.inner.complete(system, user, **kwargs))

    async def complete_structured(self, system: str, user: str, schema):
        return await self._timed(
            "complete_structured", self.inner.complete_structured(system, user, schema)
        )

    async def _timed(self, method: str, call):
        start, error = time.perf_counter(), None
        try:
            return await call
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            calls = _calls.get()
            if calls is not None:
                calls.append(
                    {"method": method, "s": round(time.perf_counter() - start, 2), "error": error}
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Прогон агента по eval-набору")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.yaml")
    parser.add_argument(
        "--db", type=Path, required=True, help="DuckDB с загруженными датасетами (копируется)"
    )
    parser.add_argument("--run-dir", type=Path, help="по умолчанию backend/storage/eval/<время>")
    parser.add_argument("--parallel", type=int, default=4, help="сколько цепочек одновременно")
    parser.add_argument("--repeat", type=int, default=1, help="повторов каждой задачи (pass^k)")
    parser.add_argument("--only", nargs="+", help="id задач; цепочка тянет за собой предков")
    parser.add_argument("--scenarios", nargs="+", type=int, help="только эти сценарии")
    parser.add_argument("--turn-timeout", type=int, default=1200, help="лимит хода, с")
    parser.add_argument(
        "--reply-to",
        action="store_true",
        help="продолжение цепочки отвечает на ход follow_up_of через reply_to (#173)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def prepare_environment(args: argparse.Namespace) -> Path:
    """Каталог прогона и пути хранилищ — до импорта app.* (settings — синглтон)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = (args.run_dir or ROOT / "backend" / "storage" / "eval" / stamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "main.duckdb"
    shutil.copyfile(args.db, db_path)
    os.environ["STORAGE_DIR"] = str(run_dir)
    os.environ["SQLITE_PATH"] = str(run_dir / "sessions.sqlite")
    os.environ["DUCKDB_PATH"] = str(db_path)
    logging.basicConfig(
        filename=run_dir / "agent.log",
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run_dir


def git_commit() -> str:
    """Коммит кода агента: отчёты разных версий сравниваются по нему."""
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def load_cycle_module():
    """bind_session и build_orchestrator — из полного e2e-цикла, без копий."""
    path = ROOT / "scripts" / "run_full_llm_cycle.py"
    spec = importlib.util.spec_from_file_location("run_full_llm_cycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_orchestrator(db, llm, sandbox):
    """Свой оркестратор на цепочку — как API, который создаёт его на каждый запрос."""
    from app.core.agent.insight import InsightGenerator
    from app.core.agent.orchestrator import AgentOrchestrator
    from app.core.agent.planner import StepPlanner
    from app.core.agent.step_executor import StepExecutor
    from app.core.memory.state_cache import StateCache

    return AgentOrchestrator(
        planner=StepPlanner(llm=llm),
        executor=StepExecutor(db=db, llm=llm, sandbox=sandbox),
        insight_gen=InsightGenerator(llm=llm, db=db),
        cache=StateCache(db),
        db=db,
    )


def chains(tasks: list[dict], args: argparse.Namespace) -> list[list[dict]]:
    """Задачи цепочками: корень и его продолжения по follow_up_of, по порядку."""
    by_id = {task["id"]: task for task in tasks}
    wanted = set(by_id)
    if args.only:
        missing = [task_id for task_id in args.only if task_id not in by_id]
        if missing:
            raise SystemExit(f"Нет задач: {', '.join(missing)}")
        wanted = set()
        for task_id in args.only:
            while task_id is not None:
                wanted.add(task_id)
                task_id = by_id[task_id].get("follow_up_of")
    if args.scenarios:
        wanted = {task_id for task_id in wanted if by_id[task_id]["scenario"] in args.scenarios}
    result, index = [], {}
    for task in tasks:
        if task["id"] not in wanted:
            continue
        parent = task.get("follow_up_of")
        if parent in index:
            index[task["id"]] = index[parent]
            result[index[parent]].append(task)
        else:
            index[task["id"]] = len(result)
            result.append([task])
    return result


def selected_columns(session_id: str, question: str, db) -> list[str]:
    """Колонки, которые семантический отбор покажет планировщику (метрика из #57)."""
    from app.core.data.semantic_select import select_columns
    from app.core.memory.session_memory import SessionMemory

    context = SessionMemory(session_id, db=db).build_context(question)
    if context["profile"] is None:
        return list(context["columns"])
    return select_columns(
        question, context["columns"], profile=context["profile"], titles=context["titles"]
    )


async def run_turn(
    orchestrator, session_id: str, question: str, record: dict, reply_to: str | None
) -> None:
    from app.api.schemas.chat import ChatEvent, ChatRequest

    request = {"session_id": session_id, "question": question}
    if reply_to is not None:
        request["reply_to"] = reply_to  # поле есть с #173
    async for event in orchestrator.run(ChatRequest(**request)):
        ChatEvent(**event)  # тот же контракт, что у SSE
        kind, data = event["type"], event["data"]
        record["event_counts"][kind] = record["event_counts"].get(kind, 0) + 1
        if record["first_event_s"] is None:
            record["first_event_s"] = round(time.perf_counter() - record["_start"], 2)
        if kind == "progress":
            if data.get("tool") and data.get("status") == "started":
                record["tools"].append(data["tool"])
            if data.get("status") == "failed":
                record["errors"].append(f"{data.get('stage')}: {data.get('error', '')}"[:500])
        elif kind == "text":
            record["text"].append(
                {"headline": data.get("headline", ""), "details": data.get("details", [])}
            )
        elif kind == "chart":
            spec = data["spec"]
            record["charts"].append(
                {
                    "type": spec.get("usermeta", {}).get("chart_type", spec.get("mark")),
                    "caption": data.get("caption", ""),
                    "result_table": data.get("result_table"),
                    "rows": len(spec.get("data", {}).get("values", [])),
                }
            )
        elif kind == "dashboard":
            record["dashboard"] = data.get("dashboard_id")
        elif kind == "error":
            record["errors"].append(data.get("message", "")[:500])
        elif kind == "clarification_needed":
            record["clarification"] = {
                "error": str(data.get("error", ""))[:1000],
                "suggestions": data.get("suggestions", []),
            }
            record["turn_id"] = data.get("turn_id")
            record["status"] = "clarification"
        elif kind == "done":
            record["turn_id"] = data.get("turn_id")  # с #171
            if record["status"] == "no_done":
                record["status"] = "done"


async def run_task(
    orchestrator,
    db,
    task: dict,
    session_id: str,
    repeat: int,
    timeout: int,
    reply_to: str | None = None,
):
    record = {
        "id": task["id"],
        "repeat": repeat,
        "scenario": task["scenario"],
        "dataset": task["dataset"],
        "question": task["question"],
        "session_id": session_id,
        "reply_to": reply_to,
        "turn_id": None,
        "status": "no_done",
        "event_counts": {},
        "tools": [],
        "text": [],
        "charts": [],
        "dashboard": None,
        "clarification": None,
        "errors": [],
        "first_event_s": None,
        "_start": time.perf_counter(),
    }
    record["selected_columns"] = selected_columns(session_id, task["question"], db)
    calls: list = []
    token = _calls.set(calls)
    try:
        await asyncio.wait_for(
            run_turn(orchestrator, session_id, task["question"], record, reply_to), timeout
        )
    except TimeoutError:
        record["status"] = "timeout"
    except Exception:
        record["status"] = "exception"
        record["errors"].append(traceback.format_exc()[-1500:])
        logging.getLogger("eval").exception("задача %s упала", task["id"])
    finally:
        _calls.reset(token)
    record["seconds"] = round(time.perf_counter() - record.pop("_start"), 2)
    record["llm_calls"] = calls
    if any(call["error"] and API_LIMIT.search(call["error"]) for call in calls):
        # Лимит провайдера, а не провал агента: score.py такие ходы не считает.
        record["status"] = "api_error"
    result_table = next(
        (chart["result_table"] for chart in reversed(record["charts"]) if chart["result_table"]),
        None,
    )
    record["result"] = db.to_records(result_table, limit=RESULT_ROWS) if result_table else None
    [record["dataset_rows"]] = [
        row["n"] for row in db.query(f'SELECT count(*) AS n FROM "{task["dataset"]}"')
    ]
    return record


async def run(args: argparse.Namespace, run_dir: Path) -> int:
    from app.config import settings
    from app.core.data.duckdb_manager import DuckDBManager
    from app.core.llm.client import LLMClient
    from app.core.sandbox.wasm_runtime import WASMSandbox

    cycle = load_cycle_module()
    tasks = yaml.safe_load(args.questions.read_text(encoding="utf-8"))
    groups = chains(tasks, args)
    db = DuckDBManager()
    llm, sandbox = TaskLLM(LLMClient()), WASMSandbox()
    sandbox_ok = sandbox.available()
    meta = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "structured_mode": settings.llm_structured_mode,
        "sandbox": sandbox_ok,
        "commit": git_commit(),
        "reply_to": args.reply_to,
        "parallel": args.parallel,
        "repeat": args.repeat,
        "tasks": sum(len(group) for group in groups),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))

    out = (run_dir / "results.jsonl").open("a", encoding="utf-8")
    semaphore = asyncio.Semaphore(args.parallel)
    done = 0
    total = meta["tasks"] * args.repeat

    async def run_chain(group: list[dict], repeat: int) -> None:
        nonlocal done
        async with semaphore:
            session_id = f"eval-{group[0]['id']}-r{repeat}"
            cycle.bind_session(session_id, group[0]["dataset"], db)
            orchestrator = new_orchestrator(db, llm, sandbox)
            turn_ids: dict[str, str | None] = {}
            for task in group:
                reply_to = turn_ids.get(task.get("follow_up_of")) if args.reply_to else None
                record = await run_task(
                    orchestrator, db, task, session_id, repeat, args.turn_timeout, reply_to
                )
                turn_ids[task["id"]] = record["turn_id"]
                out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                out.flush()
                done += 1
                print(
                    f"[{done}/{total}] {task['id']} r{repeat}: {record['status']}, "
                    f"{record['seconds']}s, LLM {len(record['llm_calls'])}",
                    flush=True,
                )

    try:
        await asyncio.gather(
            *(run_chain(group, repeat) for repeat in range(args.repeat) for group in groups)
        )
    finally:
        out.close()
        db.close()
    print(f"Готово: {run_dir / 'results.jsonl'}")
    return 0


def main() -> int:
    args = parse_args()
    run_dir = prepare_environment(args)
    sys.path.insert(0, str(ROOT / "backend"))
    return asyncio.run(run(args, run_dir))


if __name__ == "__main__":
    sys.exit(main())
