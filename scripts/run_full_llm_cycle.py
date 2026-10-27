#!/usr/bin/env python3
"""
Полный цикл платформы из CLI — без фронтенда, но со всеми модулями:

  источник (CSV/PostgreSQL/ClickHouse через коннектор)
  -> профиль сразу после подключения -> паспорт таблицы
  -> описание таблицы, если есть (комментарии колонок, документ) -> названия
     и описания колонок в паспорте
  -> сессия, привязанная к таблице
  -> вопрос: отбор колонок -> план -> шаги (тулы / песочница) -> инсайт
     -> график -> дашборд -> экспорт в Excel
  -> отчёт прогона (этапы, время, вызовы LLM).

Время в отчёте делится на два вида. «Ожидание API» — это HTTP-попытки, которые
закончились 429, 5xx, таймаутом или обрывом связи, вместе с паузой до следующего
повтора: клиент openai сам повторяет такие запросы, снаружи их не видно. С
локальной моделью этого нет. «Чистое» время = всё время минус ожидание API.

backend/run_llm_agent.py остаётся тестом одного оркестратора; этот скрипт —
e2e-прогон того, как платформа должна работать целиком.

Каждый прогон пишет всё в свой каталог (backend/storage/runs/<время>/): DuckDB,
SQLite сессий и дашбордов, спеки графиков, xlsx, лог и report.json —
рабочий storage/ не затрагивается.

Запуск из корня репозитория:
    python3 scripts/run_full_llm_cycle.py --csv data/sample_datasets/demo_events.csv
    python3 scripts/run_full_llm_cycle.py --csv a.csv b.csv --table b --description README.md
    python3 scripts/run_full_llm_cycle.py --pg "postgresql://user:pass@host/db" --table orders
    USE_FAKE_LLM=true python3 scripts/run_full_llm_cycle.py --csv data.csv --questions q.txt

С --questions скрипт работает без диалога и возвращает ненулевой код,
если хоть один ход не дошёл до done (e2e-критерий). Вопрос, начинающийся
с «↩», — ответ на прошлый ход (reply_to = turn_id его события done).
"""

import argparse
import asyncio
import cProfile
import json
import logging
import os
import sys
import time
import traceback
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

LINE = "-" * 72
SKIP_STEP = "Пропустить этот шаг и продолжить"
VEGA_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/vega@5.30.0"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@5.21.0"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@6.26.0"></script>
</head><body><div id="chart"></div>
<script>vegaEmbed("#chart", {spec});</script></body></html>
"""

# Этапы внутри хода: их время уже входит в «turns».
NESTED_STAGES = ("select_columns", "export")
# Как проблема API называется в выводе.
API_PROBLEMS = {"429": "429", "5xx": "5xx", "timeout": "таймаут", "network": "обрыв связи"}


# --- Ожидание API ---


@dataclass
class Attempt:
    """Одна HTTP-попытка к LLM. problem=None, если пришёл ответ модели."""

    started: float
    problem: str | None


# HTTP-попытки текущего вызова LLM; список задаёт CountingLLM на время вызова.
_attempts: ContextVar[list[Attempt] | None] = ContextVar("llm_attempts", default=None)


class ApiTransport(httpx.AsyncBaseTransport):
    """Транспорт клиента openai: записывает каждую попытку, включая его же повторы."""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = time.perf_counter()
        problem = "network"
        try:
            response = await self.inner.handle_async_request(request)
        except httpx.TimeoutException:
            problem = "timeout"
            raise
        else:
            if response.status_code == 429:
                problem = "429"
            elif response.status_code >= 500:
                problem = "5xx"
            else:
                problem = None
            return response
        finally:
            attempts = _attempts.get()
            if attempts is not None:
                attempts.append(Attempt(started, problem))

    async def aclose(self) -> None:
        await self.inner.aclose()


def api_wait(attempts: list[Attempt], end: float) -> tuple[float, Counter]:
    """Сколько вызов потерял на API: плохие попытки вместе с паузой до следующей."""
    waited, problems = 0.0, Counter()
    for index, attempt in enumerate(attempts):
        if attempt.problem is None:
            continue
        problems[attempt.problem] += 1
        following = attempts[index + 1].started if index + 1 < len(attempts) else end
        waited += following - attempt.started
    return waited, problems


def format_problems(problems: dict[str, int]) -> str:
    return ", ".join(f"{API_PROBLEMS[name]}×{count}" for name, count in problems.items())


# --- Отчёт прогона ---


@dataclass
class Report:
    """Что произошло за прогон: этапы, вызовы LLM, ходы диалога."""

    run_dir: Path
    stage: str = "startup"
    stages: dict[str, float] = field(default_factory=dict)
    stages_api_wait: dict[str, float] = field(default_factory=dict)
    api_wait: float = 0.0
    api_problems: Counter = field(default_factory=Counter)
    llm: dict[str, dict] = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    passport: dict = field(default_factory=dict)
    turns: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @contextmanager
    def measure(self, name: str):
        """Засечь этап; вызовы LLM внутри него записываются на этот этап."""
        previous, self.stage = self.stage, name
        start, waited = time.perf_counter(), self.api_wait
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + time.perf_counter() - start
            self.stages_api_wait[name] = (
                self.stages_api_wait.get(name, 0.0) + self.api_wait - waited
            )
            self.stage = previous

    def llm_call(
        self, method: str, seconds: float, failed: bool, waited: float, problems: Counter
    ) -> None:
        entry = self.llm.setdefault(
            self.stage,
            {"calls": 0, "failed": 0, "seconds": 0.0, "api_wait_s": 0.0, "api_problems": {}},
        )
        entry["calls"] += 1
        entry["failed"] += int(failed)
        entry["seconds"] += seconds
        entry["api_wait_s"] += waited
        for name, count in problems.items():
            entry["api_problems"][name] = entry["api_problems"].get(name, 0) + count
        self.api_wait += waited
        self.api_problems.update(problems)

    def clean(self, stage: str) -> float:
        """Время этапа без ожидания API."""
        return self.stages[stage] - self.stages_api_wait.get(stage, 0.0)

    def save(self) -> Path:
        path = self.run_dir / "report.json"
        payload = {
            "source": self.source,
            "passport": self.passport,
            "stages_s": {name: round(value, 3) for name, value in self.stages.items()},
            "stages_clean_s": {name: round(self.clean(name), 3) for name in self.stages},
            "api_wait": {"seconds": round(self.api_wait, 3), "problems": dict(self.api_problems)},
            "llm": self.llm,
            "turns": self.turns,
            "warnings": self.warnings,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return path


class CountingLLM:
    """Прокси LLM-клиента: считает вызовы, их время и ожидание API по этапам отчёта."""

    def __init__(self, inner, report: Report) -> None:
        self.inner = inner
        self.report = report

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    async def complete(self, system: str, user: str, **kwargs):
        return await self._timed("complete", self.inner.complete(system, user, **kwargs))

    async def complete_structured(self, system: str, user: str, schema):
        return await self._timed(
            "complete_structured", self.inner.complete_structured(system, user, schema)
        )

    async def _timed(self, method: str, call):
        attempts: list[Attempt] = []
        token = _attempts.set(attempts)
        start = time.perf_counter()
        failed = True
        try:
            result = await call
            failed = False
            return result
        finally:
            end = time.perf_counter()
            _attempts.reset(token)
            waited, problems = api_wait(attempts, end)
            self.report.llm_call(method, end - start, failed, waited, problems)


# --- Подготовка окружения ---


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Полный цикл платформы из CLI (e2e без фронтенда)."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", nargs="+", metavar="PATH", help="CSV-файлы (таблица = имя файла)")
    source.add_argument("--pg", metavar="DSN", help="PostgreSQL DSN")
    source.add_argument("--ch", metavar="DSN", help="ClickHouse DSN")
    parser.add_argument("--pg-schema", help="схема PostgreSQL")
    parser.add_argument("--table", help="таблица сессии (по умолчанию — первая из источника)")
    parser.add_argument(
        "--description", type=Path, help="документ-описание таблиц (README, словарь колонок)"
    )
    parser.add_argument("--session", default="cli-session", help="session_id")
    parser.add_argument("--questions", type=Path, help="файл с вопросами (по одному на строку)")
    parser.add_argument(
        "--run-dir", type=Path, help="каталог прогона (по умолчанию backend/storage/runs/<время>)"
    )
    parser.add_argument("--db", help="файл DuckDB (повторный прогон берёт паспорт из кеша)")
    parser.add_argument("--no-export", action="store_true", help="не выгружать результат в xlsx")
    parser.add_argument(
        "--cprofile", action="store_true", help="сохранить профиль Python в run.prof"
    )
    parser.add_argument("--log-level", default="INFO", help="уровень лога в agent.log")
    parser.add_argument(
        "--check-context",
        type=int,
        nargs="?",
        const=12_000,
        metavar="CHARS",
        help="проверить, доходит ли до модели промпт CHARS символов (по умолчанию 12000)",
    )
    return parser.parse_args()


def prepare_environment(args: argparse.Namespace) -> Path:
    """Каталог прогона и пути хранилищ. Вызывается до импорта app.*.

    settings — синглтон уровня модуля: переменные окружения должны быть
    выставлены раньше, чем app.config будет импортирован. Переменные
    окружения важнее .env, поэтому прогон не трогает рабочий storage/.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = (args.run_dir or BACKEND_DIR / "storage" / "runs" / stamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_DIR"] = str(run_dir)
    os.environ["SQLITE_PATH"] = str(run_dir / "sessions.sqlite")
    os.environ["DUCKDB_PATH"] = str(Path(args.db).resolve() if args.db else run_dir / "main.duckdb")

    logging.basicConfig(
        filename=run_dir / "agent.log",
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run_dir


# Вопрос с этим префиксом — ответ на прошлый ход (reply_to).
REPLY_MARK = "↩"


def read_questions(path: Path | None):
    """Вопросы из файла или из stdin."""
    if path is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                print(f"\n> {line}")
                yield line
        return
    print("\nВведите вопрос (или 'exit' для выхода):")
    while True:
        try:
            line = input("\n> ").strip()
        except EOFError:
            return
        if line.lower() in ("exit", "quit", "q"):
            return
        if line:
            yield line


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


# --- Этапы ---


async def check_llm(llm, context_chars: int | None = None) -> bool:
    from app.config import settings
    from app.core.llm import budget

    section("LLM")
    print(f"  Модель:   {llm.model}")
    print(f"  Endpoint: {settings.llm_base_url}")
    print(f"  Fake:     {os.getenv('USE_FAKE_LLM', 'false')}")
    try:
        answer = await llm.complete(system="Ты полезный ассистент.", user="Скажи просто: привет")
    except Exception as error:  # noqa: BLE001 — дальше без LLM смысла нет
        print(f"\n  [ERROR] LLM недоступна: {error}")
        return False
    print(f"  Ответ:    {answer[:80]!r}")
    server_ctx = budget.server_context()
    print(f"  Контекст: сервер {server_ctx or '?'}, LLM_NUM_CTX {settings.llm_num_ctx}")
    if server_ctx and server_ctx < settings.llm_num_ctx:
        print("  [WARN] контекст сервера меньше LLM_NUM_CTX: длинные промпты будут обрезаны")
    if context_chars:
        print(f"  Проверка: промпт {context_chars} символов...")
        try:
            probe = await budget.probe_context(llm, context_chars)
        except Exception as error:  # noqa: BLE001 — на длинный промпт сервер может ответить 400
            print(f"  [ERROR] длинный промпт не прошёл: {error}")
            return False
        print(
            f"  prompt_tokens={probe['prompt_tokens']}, символов на токен "
            f"{probe['chars_per_token']}, пароль из начала виден: {probe['password_seen']}"
        )
        if probe["truncated"]:
            print(f"  [WARN] промпт обрезан сервером: {probe['truncated']}")
    return True


def connect_source(args: argparse.Namespace, db, report: Report):
    """Подключить источник и сразу профилировать таблицы — как API датасорсов."""
    from app.core.data import ingest
    from app.core.data.connectors.csv import CSVConnector, table_name
    from app.core.data.connectors.factory import build_connector
    from app.core.data.profiling import format_quality, outlier_summary, schema_overview

    section("Источник данных")
    if args.csv:
        paths: dict[str, str] = {}
        for raw in args.csv:
            wanted = table_name(Path(raw).name)
            name, suffix = wanted, 2
            while name in paths:
                name, suffix = f"{wanted}_{suffix}", suffix + 1
            paths[name] = str(Path(raw).resolve())
        connector = CSVConnector(paths)
        kind = "csv"
    else:
        kind = "postgres" if args.pg else "clickhouse"
        connector = build_connector(kind, args.pg or args.ch, args.pg_schema)

    with db.lock:
        profiles = ingest.attach(connector, db)
    tables = list(profiles)
    print(f"  Тип: {kind}, таблиц: {len(tables)}")
    for csv_report in getattr(connector, "reports", []):
        extra = []
        if csv_report.numeric_text_columns:
            extra.append(f"числа из текста: {', '.join(csv_report.numeric_text_columns)}")
        if csv_report.tolerant:
            extra.append("терпимый разбор")
        if csv_report.all_text:
            extra.append("все колонки текстом")
        print(
            f"  - {csv_report.table}: {csv_report.rows} строк, "
            f"кодировка {csv_report.encoding}, разделитель {csv_report.delimiter!r}"
            + (f" ({'; '.join(extra)})" if extra else "")
        )

    for table, profile in profiles.items():
        section(f"Профиль таблицы «{table}»")
        print(f"  Строк: {profile.row_count}, колонок: {profile.column_count}")
        print("\n  Схема:")
        print("    " + schema_overview(profile).replace("\n", "\n    "))
        print("\n  Качество:")
        print("    " + format_quality(profile).replace("\n", "\n    "))
        outliers = outlier_summary(profile)
        if outliers:
            print("\n  Выбросы (IQR):")
            for column, info in outliers.items():
                print(
                    f"    - {column}: {info['count']} вне "
                    f"[{info['lower_fence']:.4g}; {info['upper_fence']:.4g}]"
                )
        report.passport[table] = {"rows": profile.row_count, "columns": profile.column_count}
    report.source = {"kind": kind, "tables": tables}
    return connector, tables


async def describe_tables(args, db, llm, connector, tables: list[str], report: Report) -> None:
    """Описание таблиц (комментарии колонок, документ) -> паспорт; без описания — ничего."""
    from app.core.data import ingest

    document = args.description.read_text(encoding="utf-8") if args.description else None
    described = await ingest.describe(connector, db, tables, document, llm)
    if not described:
        print("\n  Описания таблиц нет: паспорт — только профиль, LLM не вызывалась")
        report.warnings.append("описания таблиц нет: названий и описаний колонок в паспорте нет")
        return
    for table, rows in described.items():
        section(f"Описание колонок «{table}»")
        for row in rows:
            title = f" «{row.title}»" if row.title else ""
            print(
                f"  - {row.column}{title} ({row.description_source or '—'}): {row.description or '—'}"
            )
        failed = sum(1 for row in rows if row.fingerprint is None)
        if failed:
            report.warnings.append(f"{table}: LLM не разобрала документ для {failed} колонок")
        report.passport[table] |= {
            "titled": sum(1 for row in rows if row.title),
            "described": sum(1 for row in rows if row.description),
            "llm_failed_columns": failed,
        }


def bind_session(session_id: str, table: str, db) -> None:
    """Привязать сессию к таблице источника (по ней оркестратор берёт паспорт и подписи)."""
    from app.core.memory.profile_memory import ProfileMemory
    from app.core.memory.session_memory import SessionMemory

    profile = ProfileMemory(db).load_profile(table)
    if profile is not None:
        columns = [column.name for column in profile.columns]
    else:
        columns = [
            row["column_name"]
            for row in db.query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = $tbl ORDER BY ordinal_position",
                {"tbl": table},
            )
        ]
    SessionMemory(session_id, db=db).set_table(table)
    print(f"\n  Сессия «{session_id}» привязана к таблице «{table}» ({len(columns)} колонок)")


def build_orchestrator(db, llm):
    from app.core.agent.insight import InsightGenerator
    from app.core.agent.orchestrator import AgentOrchestrator
    from app.core.agent.planner import StepPlanner
    from app.core.agent.step_executor import StepExecutor
    from app.core.memory.state_cache import StateCache
    from app.core.sandbox.wasm_runtime import WASMSandbox
    from app.core.tools.registry import tool_registry

    section("Оркестратор")
    sandbox = WASMSandbox()
    print(f"  WASM-песочница: {'доступна' if sandbox.available() else 'НЕ доступна'}")
    print(f"  Тулы планировщика ({len(tool_registry.planner_tools())}):")
    print("    " + tool_registry.describe().replace("\n", "\n    "))
    orchestrator = AgentOrchestrator(
        planner=StepPlanner(llm=llm),
        executor=StepExecutor(db=db, llm=llm, sandbox=sandbox),
        insight_gen=InsightGenerator(llm=llm, db=db),
        cache=StateCache(db),
        db=db,
    )
    return orchestrator, sandbox.available()


def show_planner_view(session_id: str, question: str, db) -> list[str]:
    """Что увидит планировщик: топ-K колонок после семантического отбора."""
    from app.core.data.semantic_select import select_columns
    from app.core.memory.session_memory import SessionMemory
    from app.core.viz.chart_morph import detect_morph

    memory = SessionMemory(session_id, db=db)
    if detect_morph(question) is not None and memory.last_chart() is not None:
        print("  [планировщик] не нужен: смена вида прошлого графика")
        return []
    context = memory.build_context(question)
    if context["profile"] is None:
        print("  [планировщик] паспорта нет — колонки без отбора")
        return context["columns"]
    selected = select_columns(
        question, context["columns"], profile=context["profile"], titles=context["titles"]
    )
    print(f"  [планировщик] таблица «{context['datasource']}», отобраны колонки:")
    for column in selected:
        title = context["titles"].get(column)
        print(f"    - {column}" + (f" «{title}»" if title else ""))
    for turn in context["turns"]:
        print(f"  [память] {turn['question']}: {[step['tool'] for step in turn['steps']]}")
    return selected


def ask_recovery(data: dict, interactive: bool) -> str | None:
    """Ответ на clarification_needed: JSON-действие, свободный текст или None (отмена)."""
    if not interactive:
        return None
    suggestions = data.get("suggestions", [])
    for number, suggestion in enumerate(suggestions, 1):
        print(f"    {number}. {suggestion}")
    print("    Enter — отменить ход")
    try:
        choice = input("  выбор> ").strip()
    except EOFError:
        return None
    if not choice:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(suggestions):
        picked = suggestions[int(choice) - 1]
        if picked == SKIP_STEP:
            return json.dumps({"action": "skip_step"})
        try:
            return input(f"  {picked}: ").strip() or None
        except EOFError:
            return None
    return choice


async def run_turn(
    orchestrator,
    args,
    question: str,
    db,
    report: Report,
    number: int,
    reply_to: str | None = None,
) -> dict:
    """Один ход диалога, включая повторы после уточнений."""
    from app.api.schemas.chat import ChatEvent, ChatRequest
    from app.core.dashboards.store import DashboardStore

    turn = {"question": question, "events": {}, "charts": [], "status": "no_done"}
    if reply_to is not None:
        turn["reply_to"] = reply_to
    interactive = args.questions is None
    with report.measure("select_columns"):
        turn["selected_columns"] = show_planner_view(args.session, question, db)

    request = ChatRequest(session_id=args.session, question=question, reply_to=reply_to)
    start, waited, problems = time.perf_counter(), report.api_wait, report.api_problems.copy()
    while request is not None:
        pending, request = request, None
        first = True
        async for event in orchestrator.run(pending):
            ChatEvent(**event)  # тот же контракт, что у SSE-эндпоинта
            kind, data = event["type"], event["data"]
            turn["events"][kind] = turn["events"].get(kind, 0) + 1

            if kind == "progress":
                attempt = f" попытка {data['attempt']}" if data.get("attempt") else ""
                print(
                    f"  [{data['stage']}:{data['status']}{attempt}] "
                    f"шаг {data.get('step_id', '?')} {data.get('tool') or ''}"
                    + (f" — {data['error']}" if data.get("error") else "")
                )
            elif kind == "text":
                print(f"\n  [ИНСАЙТ] {data.get('headline', '')}")
                for detail in data.get("details", []):
                    print(f"    - {detail}")
            elif kind == "chart":
                spec = data["spec"]
                chart_type = spec.get("usermeta", {}).get("chart_type", spec.get("mark", "?"))
                name = f"turn{number:02d}_chart{len(turn['charts']) + 1}"
                (report.run_dir / f"{name}.vl.json").write_text(
                    json.dumps(spec, ensure_ascii=False, indent=1, default=str)
                )
                (report.run_dir / f"{name}.html").write_text(
                    VEGA_HTML.format(
                        title=data.get("caption", ""),
                        spec=json.dumps(spec, ensure_ascii=False, default=str),
                    )
                )
                rows = len(spec.get("data", {}).get("values", []))
                via = " (смена вида из кеша, без планировщика)" if first else ""
                print(f"\n  [ГРАФИК] {chart_type}, строк {rows}{via} -> {name}.html")
                turn["charts"].append({"type": chart_type, "rows": rows, "file": f"{name}.html"})
            elif kind == "dashboard":
                dashboard = DashboardStore().get(data["dashboard_id"])
                print(f"\n  [ДАШБОРД] {data['dashboard_id']}")
                for tile in dashboard.tiles if dashboard else []:
                    rows = len(tile.snapshot_data or [])
                    print(f"    - плитка «{tile.title}»: {tile.result_table}, снимок {rows} строк")
                turn["dashboard"] = data["dashboard_id"]
            elif kind == "error":
                print(f"\n  [ERROR] шаг {data.get('step', '?')}: {data.get('message', '')}")
            elif kind == "clarification_needed":
                print(f"\n  [УТОЧНЕНИЕ] шаг {data.get('step_id')}: {data.get('error')}")
                answer = ask_recovery(data, interactive)
                turn["status"] = "clarification"
                if answer is not None:
                    request = ChatRequest(
                        session_id=args.session, question=question, recovery_response=answer
                    )
                    print(f"  -> повтор хода с recovery_response={answer!r}")
            elif kind == "done":
                turn["status"] = "done"
                turn["turn_id"] = data.get("turn_id")
            if data.get("prompt_truncated"):
                turn["prompt_truncated"] = (
                    turn.get("prompt_truncated", 0) + data["prompt_truncated"]
                )
                print(f"  [WARN] сервер LLM обрезал промптов: {data['prompt_truncated']}")
                report.warnings.append(
                    f"«{question}»: сервер LLM обрезал промптов: {data['prompt_truncated']}"
                )
            first = False
    turn["seconds"] = round(time.perf_counter() - start, 3)
    turn["api_wait_s"] = round(report.api_wait - waited, 3)
    turn["clean_seconds"] = round(turn["seconds"] - turn["api_wait_s"], 3)
    turn["api_problems"] = dict(report.api_problems - problems)

    if turn["status"] == "done" and not args.no_export:
        with report.measure("export"):
            turn["export"] = export_result(args.session, db, report.run_dir, number)
    return turn


def export_result(session_id: str, db, run_dir: Path, number: int) -> str | None:
    """Выгрузить таблицу последнего графика в xlsx (как /api/artifacts/export)."""
    import duckdb
    from app.core.export.excel import ExcelExporter
    from app.core.memory.session_memory import SessionMemory

    last = SessionMemory(session_id, db=db).last_chart()
    if last is None:
        return None
    try:
        records = db.to_records(last["result_table"], limit=500)
    except duckdb.Error:
        return None
    if not records:
        return None
    path = run_dir / f"turn{number:02d}_{last['result_table']}.xlsx"
    ExcelExporter().export(records, str(path))
    print(f"  [ЭКСПОРТ] {path.name} ({len(records)} строк); PDF не реализован")
    return path.name


def print_summary(report: Report) -> None:
    section("Итог прогона")
    print("  Чистое время = всё время минус ожидание API (429, 5xx, таймауты, обрывы связи")
    print("  и паузы перед повтором) — то, чего нет при локальном запуске модели.\n")
    print(f"    {'этап':<16} {'всего':>9} {'чистое':>9}")
    for name, seconds in report.stages.items():
        details = []
        llm = report.llm.get(name)
        if llm:
            details.append(
                f"LLM {llm['calls']} вызовов ({llm['seconds'] - llm['api_wait_s']:.1f}s чистых)"
            )
        waited = report.stages_api_wait.get(name, 0.0)
        if waited:
            problems = format_problems(llm["api_problems"]) if llm else ""
            details.append(f"ожидание API {waited:.1f}s" + (f": {problems}" if problems else ""))
        line = f"    {name:<16} {seconds:8.2f}s {report.clean(name):8.2f}s  {', '.join(details)}"
        print(line.rstrip())

    for number, turn in enumerate(report.turns, 1):
        timing = f"{turn['seconds']}s"
        if turn.get("api_wait_s"):
            timing = (
                f"{turn['clean_seconds']}s чистых (всего {turn['seconds']}s, ожидание API "
                f"{turn['api_wait_s']}s: {format_problems(turn['api_problems'])})"
            )
        print(f"  Ход {number}: {turn['status']}, {timing} — {turn['question']}")

    total = sum(seconds for name, seconds in report.stages.items() if name not in NESTED_STAGES)
    if report.api_wait:
        share = report.api_wait / total * 100 if total else 0.0
        print(
            f"\n  [!] Ожидание API: {report.api_wait:.1f}s из {total:.1f}s ({share:.0f}%) — "
            f"{format_problems(report.api_problems)}. Сравнивать с локальным запуском "
            f"по чистому времени: {total - report.api_wait:.1f}s."
        )
    else:
        print(f"\n  Ожидания API не было: {total:.1f}s — чистое время.")
    for warning in report.warnings:
        print(f"  [!] {warning}")
    print(f"\n  Каталог прогона: {report.run_dir}")


# --- Главный цикл ---


async def run(args: argparse.Namespace, report: Report) -> int:
    from openai import DefaultAsyncHttpxClient

    from app.core.data.duckdb_manager import DuckDBManager
    from app.core.llm.client import LLMClient

    http_client = DefaultAsyncHttpxClient(transport=ApiTransport())
    llm = CountingLLM(LLMClient(http_client=http_client), report)
    with report.measure("llm_check"):
        if not await check_llm(llm, args.check_context):
            return 2

    db = DuckDBManager()
    try:
        with report.measure("attach"):
            connector, tables = connect_source(args, db, report)
        table = args.table or tables[0]
        if table not in tables:
            print(f"\n  [ERROR] таблицы «{table}» нет в источнике: {', '.join(tables)}")
            return 2

        with report.measure("describe"):
            await describe_tables(args, db, llm, connector, tables, report)

        bind_session(args.session, table, db)
        orchestrator, sandbox_ok = build_orchestrator(db, llm)
        if not sandbox_ok:
            report.warnings.append("WASM-песочница недоступна: codegen-шаги упадут")

        last_turn_id = None
        for number, question in enumerate(read_questions(args.questions), 1):
            print(LINE)
            reply_to = None
            if question.startswith(REPLY_MARK):
                question = question.removeprefix(REPLY_MARK).strip()
                reply_to = last_turn_id
            with report.measure("turns"):
                try:
                    turn = await run_turn(
                        orchestrator, args, question, db, report, number, reply_to
                    )
                except Exception as error:  # noqa: BLE001 — ход падает, прогон идёт дальше
                    traceback.print_exc()
                    turn = {
                        "question": question,
                        "status": "exception",
                        "error": repr(error),
                        "seconds": 0,
                    }
            report.turns.append(turn)
            last_turn_id = turn.get("turn_id")
            print(LINE)
    finally:
        db.close()

    failed = [turn for turn in report.turns if turn["status"] != "done"]
    if args.questions is not None and (failed or not report.turns):
        return 1
    return 0


def main() -> int:
    args = parse_args()
    report = Report(run_dir=prepare_environment(args))
    profiler = cProfile.Profile() if args.cprofile else None
    if profiler is not None:
        profiler.enable()
    try:
        code = asyncio.run(run(args, report))
    except KeyboardInterrupt:
        code = 130
    finally:
        if profiler is not None:
            profiler.disable()
            profiler.dump_stats(report.run_dir / "run.prof")
    print_summary(report)
    print(f"  Отчёт: {report.save()}")
    return code


if __name__ == "__main__":
    sys.exit(main())
