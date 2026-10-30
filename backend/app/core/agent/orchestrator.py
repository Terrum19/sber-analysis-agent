"""Главный цикл исследования (orchestrator).

Конвейер одного хода диалога:
  вопрос -> planner (LLM: план шагов) -> step_executor (DuckDB/тулы/песочница)
  -> insight (вывод) -> viz (график) / dashboards (панель) -> экспорт.

Каждый результат шага сохраняется в DuckDB как temp_result_vN.
Шаг по умолчанию берёт результат последнего transform-шага ($prev),
но может сослаться на источник ($source) или любой шаг ($step_N).
Результаты analysis-шагов (стат-тесты) не заменяют основную таблицу,
а уходят в инсайт как сопутствующие. После lookup-шага (разведка
данных) план достраивается с учётом увиденного.

Реплика без запроса к данным («привет», «спасибо», «что ты умеешь»)
получает готовый ответ без планировщика (agent/triage.py).

Просьба сменить вид прошлого графика («покажи в другом виде», «сделай
таблицей») обслуживается без планировщика и без повторного SQL: спека
перестраивается chart_morph из кешированной temp_result_vN.

Вопрос про таблицу целиком («что передо мной?», «сколько строк и за какой
период?») тоже идёт без планировщика: факты и таблица колонок — из
паспорта, описание — из кеша паспорта или от LLM (core/data/overview.py).

О ходе работы фронт узнаёт из событий ``progress``: планирование
(started/finished), каждый шаг (started/finished/failed), начало
инсайта и графика.

Упавший шаг планировщик исправляет по тексту ошибки (до
``STEP_FIX_ATTEMPTS`` раз, см. agent/recovery.py), о каждой попытке
фронт получает событие ``progress`` со статусом ``fixing``. Не помогло —
оркестратор выдаёт SSE-событие ``clarification_needed`` и сохраняет в
SessionMemory состояние ожидания уточнения (``pending_recovery``). При следующем
запросе от того же ``session_id`` с полем ``recovery_response``
оркестратор загружает сохранённый контекст, применяет ответ
пользователя и повторяет только неудачный шаг (или перепланирует).

Каждое сообщение пользователя — ход в SessionMemory (``start_turn``):
шаги плана пишутся в него, итог и статус — в ``finish_turn``. Ответ на
уточнение продолжает тот же ход. ``turn_id`` хода фронт получает в
событии ``done`` (и ``clarification_needed``).

После каждого шага ход пишет чекпоинт в SessionMemory (``save_checkpoint``):
план, выполненные шаги и их таблицы. Остановленный пользователем или упавший
ход фронт продолжает запросом с ``resume_turn``: выполненные шаги не
повторяются, оставшиеся досчитываются, дальше обычные инсайт и график.

Просьбу запомнить («запомни, что выручка — без НДС») распознаёт планировщик:
поле плана ``remember``. Оркестратор пишет записи в воспоминания сессии и
подтверждает их в ответе; просьба без вопроса к данным отвечается одним
подтверждением, без шагов и инсайта. Воспоминания сессии идут в инсайт.
Шаги получают память сессии: по ней read_history ищет прошлые ходы.

Вопрос о прошлых ответах агента («как ты это посчитал?», «что значит
p-value в ответе?») планировщик помечает маршрутом ``answer``: шагов нет,
ответ строит agent/explainer.py по журналу ходов сессии. Туда же идёт любой
пустой план (кроме просьбы только запомнить): ответ словами вместо отказа.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

import duckdb

from app.api.schemas.chat import ChatRequest, DashboardPayload, PlanStepOut, ProgressPayload
from app.core.agent.chart_pick import ChartPicker
from app.core.agent.explainer import Explainer
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import PlanInvalid, StepPlanner, is_dashboard_question
from app.core.agent.recovery import STEP_FIX_ATTEMPTS, fixable
from app.core.agent.step_executor import StepExecutor, resolve_input, tool_kind
from app.core.agent.triage import small_talk_reply
from app.core.agent.turn_log import TurnLog, describe_plan
from app.core.dashboards.generator import DashboardGenerator
from app.core.dashboards.models import DashboardTile
from app.core.data import overview, relations
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.table_vectors import TableVectors
from app.core.llm.budget import track_truncations
from app.core.llm.client import LLMClient
from app.core.logging_setup import set_turn_id
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.sandbox.wasm_runtime import SandboxError
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.core.viz.chart_morph import detect_morph, morph
from app.core.viz.chart_selector import (
    MIN_HISTOGRAM_VALUES,
    NO_CHART,
    asks_distribution,
    fits,
    histogram_column,
    is_single_number,
    select_chart,
)
from app.core.viz.vega_spec_builder import (
    SPEC_ROW_LIMIT,
    build_vega_spec,
    chart_title,
    column_aliases,
    infer_field_types,
)
from app.models.insight import Insight
from app.models.research_step import (
    PREVIOUS,
    SOURCE,
    DashboardTilePlan,
    ResearchPlan,
    ResearchStep,
)

NO_TABLE_MESSAGE = "Сначала подключите данные"
# «Продолжить» ход, у которого нет чекпоинта или который уже ответил.
NOTHING_TO_RESUME = "Продолжить нечего: задайте вопрос заново"
# «Продолжить» ход, который ещё считается: страница перезагрузилась, а ход на
# бэкенде идёт дальше (прокси не обрывает запрос к бэкенду).
STILL_RUNNING = "Ответ ещё готовится — нажмите «Продолжить» чуть позже"
# turn_id ходов, которые сейчас идут в этом процессе: второй раз их не запускаем.
RUNNING_TURNS: set[str] = set()
# pending_recovery уточнения «какие данные взять?» и вопрос пользователю (#165).
TABLE_CHOICE = "table"
TABLE_QUESTION = "Вопрос подходит к разным данным. По каким из них ответить?"
# Ход упал вне шагов (планировщик, инсайт, график): текст ошибки — в progress.
FAILURE_MESSAGE = "Не удалось ответить на вопрос, попробуйте ещё раз"
# Плитка дашборда и экспорт принимают только результаты шагов (temp_result_vN).
RESULT_TABLE = re.compile(r"temp_result_v\d+")
# DuckDB: колонки нет во входной таблице шага.
MISSING_COLUMN = re.compile(r'Referenced column "([^"]+)" not found')

# Подтверждение записи в воспоминания сессии (#197).
REMEMBERED = "Запомнено: {notes}"

SUMMARY_UNAVAILABLE = "Описание данных сейчас недоступно: модель не ответила."
SCHEMA_CAPTION = "Связанные таблицы"
TABLE_WORDS = ("таблица", "таблицы", "таблиц")
# Вид чекпоинта хода: план с шагами или дашборд по кнопке.
RESEARCH_CHECKPOINT = "research"
DASHBOARD_CHECKPOINT = "dashboard"
# Событие, которым кончается ход: после него поток закрывается.
TURN_END_EVENTS = ("done", "clarification_needed")

# Отправка события фронту из глубины плана (progress), пока run() стримит.
Emit = Callable[[dict], None]

# Готовые варианты ответа, которые фронт отображает кнопками.
CLARIFICATION_SUGGESTIONS = [
    "Переформулировать вопрос",
    "Уточнить колонки",
    "Пропустить этот шаг и продолжить",
]
# Код в песочнице не отработал и после исправлений — колонки не помогут.
UNSUPPORTED_SUGGESTIONS = [
    "Переформулировать вопрос",
    "Пропустить этот шаг и продолжить",
]

DISTRIBUTION_TOOL = "describe_stats"

# Сколько раз план можно достроить после lookup-шагов (защита от циклов).
MAX_REPLANS = 2
# Сколько строк результата lookup-шага показать планировщику.
OBSERVATION_ROWS = 20
# Сколько строк результата шага приходит в событии finished (#239); остальное —
# GET /sessions/{id}/results/{table}.
STEP_PREVIEW_ROWS = 5

DASHBOARD_TITLE = "Дашборд"
GRID_COLUMNS = 12
KPI_WIDTH, KPI_HEIGHT = 3, 2
CHART_WIDTH, CHART_HEIGHT = 6, 4


@dataclass
class Execution:
    """Итог выполнения плана."""

    result_table: str
    # (purpose, temp-таблица) — результаты analysis-шагов для инсайта.
    supporting: list[tuple[str, str]] = field(default_factory=list)
    final_viz: str = "auto"
    dashboard_requested: bool = False
    tables: dict[int, str] = field(default_factory=dict)
    plan: ResearchPlan = field(default_factory=ResearchPlan)
    # Выполненные шаги: вывод знает, какие фильтры и периоды уже применены.
    steps: list[ResearchStep] = field(default_factory=list)
    # Шаг -> его входная таблица (вторая таблица join_tables не учитывается).
    inputs: dict[int, str] = field(default_factory=dict)
    result_purpose: str = ""
    result_kind: str = "transform"
    result_tool: str = ""
    # Что записано в воспоминания по просьбе этого хода.
    remembered: list[str] = field(default_factory=list)
    # Ответ по журналу ходов (маршрут answer): шагов нет.
    reply: Insight | None = None
    # Вопрос о таблице целиком (маршрут overview): ответ — обзор таблицы.
    overview: bool = False


class ClarificationNeeded(Exception):
    """Исключение: шаг не прошёл после N попыток, нужен уточняющий ответ пользователя."""

    def __init__(self, step_id: int, error: str, suggestions: list[str]) -> None:
        self.step_id = step_id
        self.error = error
        self.suggestions = suggestions
        super().__init__(error)


class _StepFailed(Exception):
    """Шаг не выполнился и не исправился: последняя версия шага и ошибка.

    ``executions`` — сколько раз шаг выполнялся (исходный и исправленные
    варианты); отклонённое валидацией исправление не выполняется и не
    считается.
    """

    def __init__(self, step: ResearchStep, error: Exception, executions: int) -> None:
        self.step = step
        self.error = error
        self.executions = executions
        super().__init__(str(error))


def _ignore(event: dict) -> None:
    """emit по умолчанию: события некому отдавать."""


def _progress_event(stage: str, status: str, **fields: object) -> dict:
    payload = ProgressPayload(stage=stage, status=status, **fields)
    return {"type": "progress", "data": payload.model_dump(exclude_none=True)}


def _step_event(step: ResearchStep, status: str, **fields: object) -> dict:
    return _progress_event(
        "step",
        status,
        step_id=step.step_id,
        tool=step.tool,
        purpose=step.purpose,
        input=step.input,
        arguments=step.arguments,
        **fields,
    )


def _plan_event(steps: list[ResearchStep]) -> dict:
    """План хода целиком: фронт показывает его строками до выполнения (#239)."""
    return _progress_event(
        "planning",
        "finished",
        total_steps=len(steps),
        steps=[
            PlanStepOut(
                step_id=step.step_id,
                tool=step.tool,
                purpose=step.purpose,
                input=step.input,
                arguments=step.arguments,
            )
            for step in steps
        ],
    )


def _fixing_event(step: ResearchStep, attempt: int, error: Exception) -> dict:
    return _step_event(step, "fixing", attempt=attempt, error=str(error))


def _chart_event(spec: dict, caption: str, result_table: str) -> dict:
    """График с таблицей результата: из неё фронт пересчитывает плитку и выгружает Excel.

    Ответ без шагов строится по исходной таблице — её не отдаём.
    """
    data = {"spec": spec, "caption": caption}
    if RESULT_TABLE.fullmatch(result_table):
        data["result_table"] = result_table
    return {"type": "chart", "data": data}


def _failure_event() -> dict:
    return {"type": "error", "data": {"message": FAILURE_MESSAGE}}


def _done(turn_id: str) -> dict:
    return {"type": "done", "data": {"turn_id": turn_id}}


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))


class AgentOrchestrator:
    """Оркестратор одного хода диалога."""

    def __init__(
        self,
        planner: StepPlanner | None = None,
        executor: StepExecutor | None = None,
        insight_gen: InsightGenerator | None = None,
        cache: StateCache | None = None,
        db: DuckDBManager | None = None,
        explainer: Explainer | None = None,
        chart_llm: LLMClient | None = None,
    ) -> None:
        # Один DuckDB на весь ход: temp_result_vN, созданные исполнителем,
        # должны читаться графиком, инсайтом и кешем из той же базы.
        shared = db or (cache.db if cache is not None else None)
        self.executor = executor or StepExecutor(shared)
        self.db = shared or getattr(self.executor, "db", None) or DuckDBManager()
        self.planner = planner or StepPlanner()
        self.insight_gen = insight_gen or InsightGenerator(db=self.db)
        # Пояснение — тем же клиентом LLM, что и вывод (у моков вывода клиента нет).
        llm = getattr(self.insight_gen, "llm", None)
        self.explainer = explainer or Explainer(llm=llm, db=self.db)
        self.cache = cache or StateCache(self.db)
        self.profiles = ProfileMemory(self.db)
        self.vectors = TableVectors(self.db)
        # Модель планировщика выбирает и график; без неё — эвристика select_chart.
        self.chart_picker = ChartPicker(chart_llm or getattr(self.planner, "llm", None), self.db)
        # Ход, который ведёт этот оркестратор (один на запрос): он в RUNNING_TURNS.
        self._running: str | None = None

    async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Выполнить исследование и вернуть поток событий для фронта.

        Если сервер LLM обрезал промпты хода, последнее событие хода (done или
        clarification_needed) несёт их число в prompt_truncated: eval (#58)
        отбрасывает такие прогоны. Обрезанный промпт чаще всего и кончается
        уточнением: модель без списка тулов строит план, шаг падает.
        """
        truncations = track_truncations()
        turn_log = TurnLog(request.session_id)
        try:
            # aclosing: клиент отключился — ход закрывается сразу, с отменой задачи плана.
            async with aclosing(self._turn(request)) as events:
                while (event := await turn_log.next_event(events)) is not None:
                    if event["type"] in TURN_END_EVENTS and truncations:
                        event["data"]["prompt_truncated"] = len(truncations)
                    turn_log.observe(event)
                    yield event
        except Exception as error:
            turn_log.crashed(error)
            raise
        finally:
            turn_log.close()
            RUNNING_TURNS.discard(self._running)

    async def _turn(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Один ход: события для фронта до done или clarification_needed."""
        memory = SessionMemory(request.session_id, db=self.db)
        pending = memory.get_pending_recovery()
        if pending is not None and not request.recovery_response:
            # Новый вопрос вместо ответа на уточнение — прошлый сбой забываем,
            # иначе сессия навсегда застрянет в ожидании recovery_response.
            memory.clear_pending_recovery()
            pending = None
        if request.resume_turn is not None:
            # «Продолжить» остановленный ход: таблица и план уже есть в чекпоинте.
            async for event in self._resume(memory, request):
                yield event
            return
        # Ответ на уточнение продолжает ход, в котором упал шаг.
        if pending is None or not memory.resume_turn(pending.get("turn_id")):
            memory.start_turn(request.question, reply_to=request.reply_to)
        turn_id = memory.turn_id
        set_turn_id(turn_id)
        self._mark_running(turn_id)
        logger.info("ход начат: вопрос=%r", request.question)

        catalog: list[str] = []
        if pending is not None and pending.get("kind") == TABLE_CHOICE:
            # Ответ на «какие данные взять?»: дальше обычный ход по исходному вопросу.
            memory.clear_pending_recovery()
            chosen, request = _table_answer(pending, request)
            logger.info("ответ на выбор таблицы: %s", chosen)
            if chosen is None:
                # Свободный текст вместо варианта — уточнение к вопросу, а таблицу
                # выберет планировщик из предложенных.
                catalog = list(pending["options"].values())
            previous = memory.table
            memory.use_table(chosen or catalog[0])
            # Варианты — всегда другие данные, чем таблица сессии.
            yield _table_event(self.db, memory.table, previous)
            pending = None

        dashboard = request.mode == "dashboard" or (
            pending is None and is_dashboard_question(request.question)
        )
        if (
            not dashboard
            and pending is None
            and (reply := small_talk_reply(request.question)) is not None
        ):
            # «Привет», «спасибо», «что ты умеешь» — без плана, LLM и графика.
            logger.info("маршрут: small talk")
            memory.finish_turn("ok", headline=reply)
            yield {"type": "text", "data": {"headline": reply, "details": []}}
            yield _done(turn_id)
            return

        # Small talk выше: «привет» отвечается и без подключённой таблицы.
        table = memory.table
        choice = None
        if pending is None and table is None:
            # Таблица не выбрана — подбираем по словам вопроса без вызова LLM (#165).
            similarity = await self.vectors.similarity(request.question)
            choice = relations.choose_table(self.db, request.question, similarity)
        elif (
            pending is None
            and request.idea is None
            and not overview.is_overview_question(request.question)
        ):
            # Вопрос явно про другие, несвязанные данные — переходим к ним. Идея
            # дашборда предложена по таблице сессии, а «что передо мной?» — всегда
            # про неё: их не перепроверяем.
            choice = await self._switch(table, request.question)
        if choice is not None:
            if choice.options:
                # Вопрос подходит к разным несвязанным данным: спрашиваем, какие взять.
                logger.info("маршрут: уточнение таблицы %s", choice.options)
                memory.set_pending_recovery(
                    {
                        "kind": TABLE_CHOICE,
                        "question": request.question,
                        "options": choice.options,
                        "turn_id": turn_id,
                    }
                )
                memory.finish_turn("clarification")
                yield {
                    "type": "clarification_needed",
                    "data": {
                        "step_id": -1,
                        "error": TABLE_QUESTION,
                        "suggestions": list(choice.options),
                        "turn_id": turn_id,
                        "kind": TABLE_CHOICE,
                    },
                }
                return
            if choice.table is not None:
                logger.info(
                    "таблица по вопросу: %s (была %s), каталог: %s",
                    choice.table,
                    table,
                    choice.catalog,
                )
                yield _table_event(self.db, choice.table, table)
                table, catalog = choice.table, choice.catalog
                memory.use_table(table)
        if table is None:
            memory.finish_turn("failed")
            yield {"type": "error", "data": {"message": NO_TABLE_MESSAGE}}
            yield _done(turn_id)
            return

        # Дашборд по кнопке (#224) — свой маршрут, без обзора и плана хода.
        # Просьба о дашборде текстом в обычном чате идёт туда же, а не в старую
        # сборку плиток планировщиком с шаблонами. Таблица уже выбрана выше:
        # дашборд без таблицы сессии берёт её так же, как вопрос (#165).
        if dashboard:
            from app.core.agent.dashboard_turn import DashboardTurn

            logger.info("маршрут: дашборд")
            async for event in DashboardTurn(self).run(memory, request):
                yield event
            return

        titles = self._titles(table)

        if pending is None and overview.is_overview_question(request.question):
            logger.info("маршрут: обзор таблицы")
            async for event in self._overview(memory, table):
                yield event
            return

        if pending is None:
            # Смена вида прошлого графика — из кеша, без планировщика и SQL.
            morphed = self._morph(memory, request.question, titles)
            if morphed is not None:
                logger.info("маршрут: смена вида графика")
                yield morphed
                yield _done(turn_id)
                return

        logger.info("маршрут: %s", "план" if pending is None else "ответ на уточнение")
        async for event in self._answer(memory, request, titles, pending=pending, catalog=catalog):
            yield event

    async def _resume(self, memory: SessionMemory, request: ChatRequest) -> AsyncIterator[dict]:
        """Продолжить ход с чекпоинта: выполненные шаги и плитки не повторяются.

        Ход уже ответил (страница перезагрузилась, и ответ до неё не дошёл) —
        отдаётся его сохранённый ответ, без LLM и шагов.
        """
        if request.resume_turn in RUNNING_TURNS:
            logger.info("ход %s ещё идёт: второй раз не запускаем", request.resume_turn)
            yield {"type": "error", "data": {"message": STILL_RUNNING}}
            yield {"type": "done", "data": {}}
            return
        checkpoint = memory.resume_checkpoint(request.resume_turn)
        answered = None if checkpoint else memory.answered_turn(request.resume_turn)
        if answered is not None:
            set_turn_id(answered["turn_id"])
            logger.info("ход уже ответил: отдаём сохранённый ответ")
            for event in self._replay(answered):
                yield event
            yield _done(answered["turn_id"])
            return
        if checkpoint is None:
            logger.info("продолжать нечего: ход %s", request.resume_turn)
            yield {"type": "error", "data": {"message": NOTHING_TO_RESUME}}
            yield {"type": "done", "data": {}}
            return
        set_turn_id(memory.turn_id)
        self._mark_running(memory.turn_id)
        logger.info("ход продолжен: вопрос=%r", memory.question)
        if checkpoint["kind"] == DASHBOARD_CHECKPOINT:
            from app.core.agent.dashboard_turn import DashboardTurn

            async for event in DashboardTurn(self).run(memory, request, resume=checkpoint):
                yield event
            return
        titles = self._titles(checkpoint["source_table"])
        async for event in self._answer(memory, request, titles, resume=checkpoint):
            yield event

    def _mark_running(self, turn_id: str) -> None:
        self._running = turn_id
        RUNNING_TURNS.add(turn_id)

    def _replay(self, turn: dict) -> list[dict]:
        """Сохранённый ответ хода: вывод и график из его таблицы результата.

        Таблицу графика уже удалила очистка — только вывод.
        """
        events = [
            {
                "type": "text",
                "data": {"headline": turn["headline"] or "", "details": turn.get("details") or []},
            }
        ]
        chart = turn.get("chart")
        if chart is None:
            return events
        try:
            records = self.cache.get(chart["result_table"], limit=SPEC_ROW_LIMIT)
        except duckdb.Error:
            logger.warning("таблицы графика %s уже нет: только вывод", chart["result_table"])
            return events
        titles = self._titles(turn["tables"][0]) if turn["tables"] else {}
        spec = build_vega_spec(
            chart["type"], chart["result_table"], data=records, titles=titles, title=chart["title"]
        )
        events.append(_chart_event(spec, chart["title"], chart["result_table"]))
        return events

    def _titles(self, table: str) -> dict[str, str]:
        return column_aliases(
            {
                "glossary": self.profiles.load_glossary(table),
                "titles": {row.column: row.title for row in self.profiles.load_enrichment(table)},
            }
        )

    async def _answer(
        self,
        memory: SessionMemory,
        request: ChatRequest,
        titles: dict[str, str],
        *,
        pending: dict | None = None,
        catalog: list[str] | None = None,
        resume: dict | None = None,
    ) -> AsyncIterator[dict]:
        """План и шаги (или их продолжение), затем инсайт, график и дашборд — до done."""
        turn_id = memory.turn_id
        # План и шаги идут задачей: события прогресса, которые она шлёт
        # через emit, отдаются фронту сразу, не дожидаясь конца плана.
        events: asyncio.Queue[dict | None] = asyncio.Queue()
        task = asyncio.create_task(
            self._research(memory, pending, request, events.put_nowait, catalog, resume)
        )
        task.add_done_callback(lambda _: events.put_nowait(None))
        try:
            while (event := await events.get()) is not None:
                yield event
            question, execution = await task
        except ClarificationNeeded as exc:
            memory.finish_turn("clarification", reason=exc.error)
            yield {
                "type": "clarification_needed",
                "data": {
                    "step_id": exc.step_id,
                    "error": exc.error,
                    "suggestions": exc.suggestions,
                    "turn_id": turn_id,
                },
            }
            return
        except Exception as exc:
            # Сбой вне шагов (планировщик, LLM недоступна): без error и done
            # фронт так и показывал бы «идёт планирование».
            logger.exception("<<< run() ход прерван")
            memory.finish_turn("failed", reason=str(exc))
            yield _failure_event()
            yield _done(turn_id)
            return
        finally:
            # Клиент отключился посреди плана — задача больше не нужна.
            task.cancel()
        result_table = execution.result_table
        confirmation = _confirmation(execution.remembered)

        if execution.overview:
            async for event in self._overview(memory, memory.table):
                yield event
            return

        if execution.reply is not None:
            # Вопрос о прошлых ответах: ответ по журналу, без инсайта и графика.
            reply = execution.reply
            if confirmation:
                reply.details.append(confirmation)
            memory.finish_turn("ok", headline=reply.headline, details=reply.details)
            yield {"type": "text", "data": {"headline": reply.headline, "details": reply.details}}
            yield _done(turn_id)
            return

        if confirmation and not execution.steps:
            # Только просьба запомнить: вопроса к данным нет.
            memory.finish_turn("ok", headline=confirmation)
            yield {"type": "text", "data": {"headline": confirmation, "details": []}}
            yield _done(turn_id)
            return

        # --- Финальный блок: инсайт -> чарт -> дашборд -> done ---
        # График по таблицам шагов агент выбирает, пока пишется вывод.
        picking = self._start_chart_pick(question, execution)
        stage = "insight"
        try:
            logger.info(">>> run() генерация инсайта")
            yield _progress_event("insight", "started")
            insight = await self.insight_gen.generate(
                question,
                result_table,
                supporting=execution.supporting,
                steps=execution.steps,
                memories=memory.context_memories(),
                result_tool=execution.result_tool,
                source=memory.table,
                inputs=execution.inputs,
            )
            if confirmation:
                insight.details.append(confirmation)
            logger.info(
                "<<< run() инсайт: headline=%r details=%r", insight.headline, insight.details
            )
            yield {
                "type": "text",
                "data": {"headline": insight.headline, "details": insight.details},
            }

            stage = "chart"
            picked = await picking if picking is not None else None
            if picked is not None:
                chart_type, chart_table, records = picked.chart_type, picked.table, picked.records
                columns = {"x": picked.x, "y": picked.y, "color": picked.color}
                purpose = picked.purpose
            else:
                records = self.db.to_records(result_table, limit=SPEC_ROW_LIMIT)
                view = _requested_view(execution, records)
                chart_type = select_chart(question, result_table, view, records, titles)
                if chart_type == NO_CHART:
                    logger.info("<<< run() ответ без графика")
                    memory.finish_turn(
                        "ok",
                        headline=insight.headline,
                        result_table=result_table,
                        result_columns=self._result_columns(result_table),
                        details=insight.details,
                    )
                    yield _done(turn_id)
                    return
                if chart_type == "histogram":
                    result_table, records = self._histogram_values(
                        question, result_table, records, titles
                    )
                chart_table, columns, purpose = result_table, {}, execution.result_purpose
            yield _progress_event("chart", "started")
            logger.debug(">>> run() построение графика chart_type=%s", chart_type)
            spec = build_vega_spec(
                chart_type,
                chart_table,
                data=records,
                titles=titles,
                title=purpose or chart_title(records, titles),
                **columns,
            )
            logger.debug(
                "<<< run() график построен: mark=%s строк=%d",
                spec.get("mark", "?"),
                len(spec.get("data", {}).get("values", [])),
            )
            memory.record_chart(spec["usermeta"]["chart_type"], chart_table, insight.headline)
            yield _chart_event(spec, insight.headline, chart_table)
        except Exception as exc:
            logger.exception("<<< run() этап %s упал", stage)
            memory.finish_turn("failed", reason=f"{stage}: {exc}")
            yield _progress_event(stage, "failed", error=str(exc))
            yield _failure_event()
            yield _done(turn_id)
            return
        finally:
            # Ход упал или клиент ушёл раньше графика — выбор больше не нужен.
            if picking is not None:
                picking.cancel()

        if execution.dashboard_requested:
            fallback = DashboardTile(
                title=spec.get("title") or insight.headline,
                spec=spec,
                result_table=chart_table,
                insight=insight.headline,
            )
            try:
                event = await self._build_dashboard(
                    request.session_id, question, execution, titles, fallback
                )
            except Exception as exc:
                # Ответ и график уже у пользователя: ход не провален, нет
                # только дашборда — без error, дальше обычный done.
                logger.exception("<<< run() дашборд не собран")
                yield _progress_event("dashboard", "failed", error=str(exc))
            else:
                yield event

        memory.finish_turn(
            "ok",
            headline=insight.headline,
            result_table=result_table,
            result_columns=self._result_columns(result_table),
            details=insight.details,
        )
        yield _done(turn_id)

    # --- Вспомогательные методы ---

    def _start_chart_pick(self, question: str, execution: Execution) -> asyncio.Task | None:
        """Задача выбора графика агентом; None — графику не нужен выбор LLM.

        Без графика (final_viz=none) выбирать нечего; выборка для гистограммы
        (_finish) — не таблица шага, её вид уже решён.
        """
        if execution.final_viz == NO_CHART and not execution.dashboard_requested:
            return None
        if execution.result_table not in execution.tables.values():
            return None
        return asyncio.create_task(
            self.chart_picker.pick(question, execution.steps, execution.tables, execution.final_viz)
        )

    async def _research(
        self,
        memory: SessionMemory,
        pending: dict | None,
        request: ChatRequest,
        emit: Emit,
        catalog: list[str] | None = None,
        resume: dict | None = None,
    ) -> tuple[str, Execution]:
        """План и шаги хода: вопрос, на который отвечаем, и итог выполнения.

        catalog — таблицы-кандидаты, когда таблица сессии не выбрана и по словам
        вопроса не определилась: планировщик выбирает одну из них в input
        первого шага, и она становится таблицей сессии. resume — чекпоинт
        остановленного хода: план не составляется, досчитываются его шаги.
        """
        if resume is not None:
            plan = ResearchPlan.model_validate(resume["plan"])
            emit(_plan_event(plan.steps))
            tables = existing_results(
                self.db, {int(key): value for key, value in resume["step_tables"].items()}
            )
            execution = await self._execute_plan(
                memory,
                plan,
                resume["input_table"],
                resume["question"],
                step_tables=tables,
                source_table=resume["source_table"],
                emit=emit,
            )
            return memory.question or resume["question"], execution
        if pending is not None:
            # Возобновление после уточнения (pending_recovery).
            question = pending.get("question") or request.question
            execution = await self._apply_recovery(memory, pending, request, emit)
            memory.clear_pending_recovery()
            return question, execution

        # Обычный путь: новый вопрос -> план -> выполнение шагов.
        context = memory.build_context(request.question)
        if catalog:
            context["catalog"] = relations.describe_catalog(self.db, catalog, request.question)
            tables = catalog
        else:
            context["related"] = self._related(memory.table, request.question)
            tables = [memory.table, *(item.table for item in context["related"])]
        logger.debug(">>> run() планируем шаги (planner.plan)")
        emit(_progress_event("planning", "started", tables=tables, turn_id=memory.turn_id))
        try:
            plan = await self._plan(request.question, context)
        except Exception as exc:
            emit(_progress_event("planning", "failed", error=str(exc)))
            raise
        emit(_plan_event(plan.steps))
        chosen = plan.steps[0].input if plan.steps else None
        if catalog and chosen in catalog and chosen != memory.table:
            logger.info("планировщик выбрал таблицу %s", chosen)
            memory.use_table(chosen)
        logger.debug(
            "<<< run() план получен: шагов=%d final_viz=%s dashboard=%s",
            len(plan.steps),
            plan.final_viz,
            plan.dashboard_requested,
        )
        remembered = [item["text"] for text in plan.remember if (item := memory.remember(text))]
        if remembered:
            logger.info("<<< run() запомнено: %s", remembered)
        if plan.route == "overview" and not plan.steps:
            # Обзорный вопрос, который не узнали шаблоны is_overview_question.
            logger.info("маршрут: обзор таблицы (от планировщика)")
            return request.question, Execution(
                result_table="", remembered=remembered, overview=True
            )
        if not plan.steps and (plan.route == "answer" or not remembered):
            # Вопрос о прошлых ответах. Модель часто возвращает пустой план, не
            # сменив route: пустой план — тоже ответ словами, а не отказ «не
            # удалось подобрать инструменты». План с шагами при route=answer
            # выполняется: в сомнении лучше посчитать, чем ответить без расчёта.
            logger.info("маршрут: пояснение по журналу ходов (route=%s)", plan.route)
            emit(_progress_event("insight", "started"))
            reply = await self.explainer.answer(request.question, memory)
            return request.question, Execution(result_table="", remembered=remembered, reply=reply)
        if remembered and not plan.steps:
            return request.question, Execution(result_table="", remembered=remembered)
        for s in plan.steps:
            logger.info(
                "    шаг %d: tool=%s input=%s args=%s columns=%s purpose=%r",
                s.step_id,
                s.tool,
                s.input,
                s.arguments,
                s.columns,
                s.purpose,
            )
        _require_steps(plan)
        execution = await self._execute_plan(
            memory, plan, memory.table, request.question, emit=emit
        )
        execution.remembered = remembered
        return request.question, execution

    async def _switch(self, table: str, question: str) -> relations.TableChoice | None:
        """Переход к другим данным; сбой каталога — остаёмся, ход идёт как раньше."""
        try:
            similarity = await self.vectors.similarity(question)
            return relations.switch_table(self.db, table, question, similarity)
        except Exception:
            logger.exception("переход от таблицы %s не проверен", table)
            return None

    def _related(self, table: str, question: str) -> list[relations.Related]:
        """Связанные с таблицей хода таблицы; сбой каталога — план без них."""
        try:
            return relations.related_tables(self.db, table, question)
        except duckdb.Error:
            logger.exception("связанные таблицы %s не прочитаны", table)
            return []

    async def _plan(self, question: str, context: dict) -> ResearchPlan:
        """План от планировщика; невалидный и после переспроса — уточнение у пользователя."""
        started_at = time.perf_counter()
        try:
            plan = await self.planner.plan(question, context)
        except PlanInvalid as exc:
            raise ClarificationNeeded(
                step_id=-1,
                error="Не удалось составить корректный план: " + "; ".join(exc.errors),
                suggestions=["Переформулировать вопрос"],
            ) from exc
        logger.info("план за %.1f с: %s", time.perf_counter() - started_at, describe_plan(plan))
        return plan

    async def _execute_plan(
        self,
        memory: SessionMemory,
        plan: ResearchPlan,
        input_table: str,
        question: str,
        *,
        step_tables: dict[int, str] | None = None,
        source_table: str | None = None,
        emit: Emit = _ignore,
        checkpoint: bool = True,
    ) -> Execution:
        """Последовательно выполнить шаги плана.

        transform-шаги продолжают конвейер ($prev); analysis-шаги не меняют
        $prev и попадают в инсайт как сопутствующие результаты; после
        lookup-шага план достраивается с учётом увиденного (до MAX_REPLANS).

        Упавший шаг исправляется планировщиком по тексту ошибки (до
        STEP_FIX_ATTEMPTS раз, о каждой попытке — событие progress через
        emit). Не помогло — сохраняет pending_recovery в SessionMemory и
        выбрасывает ClarificationNeeded, чтобы вызывающий код мог выдать
        clarification_needed и прервать генератор.

        Шаг, чья таблица уже есть в step_tables, не выполняется: так
        продолжается остановленный ход. После каждого шага (и достройки плана)
        пишется чекпоинт хода, если checkpoint (плитки дашборда пишут свой).
        """
        source = source_table or input_table
        tables = dict(step_tables or {})
        inputs: dict[int, str] = {}
        current = input_table
        steps = list(plan.steps)
        replans = 0
        index = 0
        while index < len(steps):
            step = steps[index]
            if step.step_id in tables:
                # Выполнен до остановки хода: результат уже лежит в своей таблице.
                inputs[step.step_id] = resolve_input(step, current, tables, source)
                details = self._step_details(inputs[step.step_id], tables[step.step_id], tables)
                emit(_step_event(step, "finished", **details))
                if tool_kind(step) == "transform":
                    current = tables[step.step_id]
                index += 1
                continue
            logger.info(
                ">>> _execute_plan шаг %d [%s]: tool=%s input=%s args=%s columns=%s $prev=%s",
                step.step_id,
                tool_kind(step),
                step.tool,
                step.input,
                step.arguments,
                step.columns,
                current,
            )
            emit(_step_event(step, "started"))
            started_at = time.perf_counter()
            try:
                step, table = await self._run_step(step, current, tables, source, emit, memory)
            except _StepFailed as failed:
                emit(
                    _step_event(
                        failed.step,
                        "failed",
                        elapsed_ms=_elapsed_ms(started_at),
                        error=str(failed.error),
                    )
                )
                steps[index] = failed.step
                exc = failed.error
                suggestions = (
                    UNSUPPORTED_SUGGESTIONS
                    if isinstance(exc, SandboxError)
                    else CLARIFICATION_SUGGESTIONS
                )
                # Сохраняем контекст неудачи для последующего уточнения.
                memory.set_pending_recovery(
                    {
                        "turn_id": memory.turn_id,
                        "question": question,
                        "failed_step_id": failed.step.step_id,
                        "plan_steps": [s.model_dump() for s in steps],
                        "input_table": current,
                        "source_table": source,
                        "step_tables": {str(key): value for key, value in tables.items()},
                        "error": str(exc),
                        "attempts_made": failed.executions,
                        "suggestions": suggestions,
                        "final_viz": plan.final_viz,
                        "dashboard_requested": plan.dashboard_requested,
                        "result_step": plan.result_step,
                        "dashboard_title": plan.dashboard_title,
                        "template_id": plan.template_id,
                        "dashboard_tiles": [tile.model_dump() for tile in plan.dashboard_tiles],
                    }
                )
                logger.error(
                    "шаг %d упал (выполнений %d): %r", failed.step.step_id, failed.executions, exc
                )
                raise ClarificationNeeded(
                    step_id=failed.step.step_id, error=str(exc), suggestions=suggestions
                ) from exc
            elapsed_ms = _elapsed_ms(started_at)
            inputs[step.step_id] = resolve_input(step, current, tables, source)
            details = self._step_details(inputs[step.step_id], table, tables)
            emit(_step_event(step, "finished", elapsed_ms=elapsed_ms, **details))
            steps[index] = step
            kind = tool_kind(step)
            logger.info("шаг %d готов за %d мс: %s", step.step_id, elapsed_ms, table)
            tables[step.step_id] = table
            memory.record_step(step, table)
            if kind == "transform":
                current = table
            index += 1

            if kind == "lookup" and replans < MAX_REPLANS:
                replans += 1
                executed = steps[:index]
                replanned = await self._replan_after_lookup(
                    memory, question, executed, tables, observation=table
                )
                steps = executed + replanned.steps
                emit(_plan_event(steps))
                plan = ResearchPlan(
                    steps=steps,
                    final_viz=replanned.final_viz,
                    dashboard_requested=plan.dashboard_requested or replanned.dashboard_requested,
                    result_step=replanned.result_step,
                    dashboard_title=replanned.dashboard_title or plan.dashboard_title,
                    template_id=replanned.template_id or plan.template_id,
                    dashboard_tiles=replanned.dashboard_tiles or plan.dashboard_tiles,
                )
            if checkpoint:
                memory.save_checkpoint(
                    {
                        "kind": RESEARCH_CHECKPOINT,
                        "question": question,
                        "plan": plan.model_copy(update={"steps": steps}).model_dump(mode="json"),
                        "input_table": input_table,
                        "source_table": source,
                        "step_tables": {str(key): value for key, value in tables.items()},
                    }
                )

        logger.debug("<<< _execute_plan все шаги завершены $prev=%s", current)
        return self._finish(plan, steps, tables, fallback=current, question=question, inputs=inputs)

    async def _run_step(
        self,
        step: ResearchStep,
        current: str,
        tables: dict[int, str],
        source: str,
        emit: Emit,
        memory: SessionMemory | None = None,
    ) -> tuple[ResearchStep, str]:
        """Выполнить шаг, исправляя его по тексту ошибки; вернуть шаг и его таблицу.

        Повтор с теми же аргументами не помог бы: неверная колонка или тип
        упадут так же. Поэтому каждая попытка — шаг, исправленный
        планировщиком по ошибке и колонкам входной таблицы.

        Raises:
            _StepFailed: исправить не удалось за STEP_FIX_ATTEMPTS попыток,
                ошибка не лечится исправлением (recovery.fixable) или
                планировщик недоступен.
        """

        async def execute(candidate: ResearchStep) -> str:
            return await self.executor.execute(
                candidate, current, step_tables=tables, source_table=source, history=memory
            )

        try:
            return step, await execute(step)
        except Exception as exc:  # noqa: BLE001 — решает recovery.fixable
            error = exc
        executions = 1
        rerouted = self._from_source(step, error, current, tables, source)
        if rerouted is not None:
            # Исправление через LLM видит только колонки $prev и чинить вход
            # не догадывается: две попытки уходили впустую.
            logger.warning(
                "    шаг %d: колонки нет в $prev, есть в $source — беру $source", step.step_id
            )
            step = rerouted
            executions += 1
            try:
                return step, await execute(step)
            except Exception as exc:  # noqa: BLE001
                error = exc
        # error — последняя ошибка выполнения: её видят фронт и пользователь.
        # feedback — что получает планировщик: та же ошибка и, если его
        # исправление отклонила валидация, этот шаг с причиной отказа.
        feedback = str(error)
        for attempt in range(1, STEP_FIX_ATTEMPTS + 1):
            if not fixable(error):
                break
            logger.warning(
                "    шаг %d: исправление %d/%d после ошибки %r",
                step.step_id,
                attempt,
                STEP_FIX_ATTEMPTS,
                feedback,
            )
            emit(_fixing_event(step, attempt, error))
            columns = self._input_columns(step, current, tables, source)
            try:
                fixed = await self.planner.fix_step(step, feedback, columns, set(tables))
            except ValueError as exc:
                feedback = f"{error}\n{exc}"
                continue
            except Exception:  # LLM недоступна: исправлять нечем
                logger.exception("    шаг %d: планировщик не исправил шаг", step.step_id)
                break
            step = fixed
            executions += 1
            try:
                return step, await execute(step)
            except Exception as exc:  # noqa: BLE001
                error = exc
                feedback = str(exc)
        raise _StepFailed(step, error, executions)

    def _from_source(
        self,
        step: ResearchStep,
        error: Exception,
        current: str,
        tables: dict[int, str],
        source: str,
    ) -> ResearchStep | None:
        """Тот же шаг на $source, если колонки из ошибки нет в $prev, но есть в источнике.

        Модель ставит второй независимый расчёт после первого без
        "input": "$source", и он ищет исходную колонку в таблице результата
        (группировка по «Статус» после средней суммы).
        """
        missing = MISSING_COLUMN.search(str(error))
        if missing is None or step.input != PREVIOUS or current == source:
            return None
        rerouted = step.model_copy(update={"input": SOURCE})
        if missing.group(1) not in self._input_columns(rerouted, current, tables, source):
            return None
        return rerouted

    def _input_columns(
        self, step: ResearchStep, current: str, tables: dict[int, str], source: str
    ) -> dict[str, str]:
        """Колонки входной таблицы шага (имя: тип) — для исправления шага."""
        try:
            table = resolve_input(step, current, tables, source)
        except ValueError:
            # Сломана сама ссылка входа — показываем $prev.
            table = current
        try:
            rows = self.db.query(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = $tbl ORDER BY ordinal_position",
                {"tbl": table},
            )
        except duckdb.Error:
            # Без колонок исправление всё равно возможно — по тексту ошибки.
            logger.exception("    шаг %d: колонки таблицы %s не прочитаны", step.step_id, table)
            return {}
        return {row["column_name"]: row["data_type"] for row in rows}

    def _step_details(self, input_table: str, table: str, tables: dict[int, str]) -> dict:
        """Что шаг взял на вход и что получил: для раскрытия шага на фронте (#239).

        Результат не прочитался — только ссылки на таблицы: ход идёт дальше.
        """
        details: dict = {
            "input_table": input_table,
            "input_step_id": next(
                (step_id for step_id, name in tables.items() if name == input_table), None
            ),
            "result_table": table,
        }
        code = self.db.step_code(table)
        if code is not None:
            details["code_language"], details["code"] = code
        try:
            columns, rows = self.db.result_shape(table)
            preview = self.db.to_records(table, limit=STEP_PREVIEW_ROWS)
        except duckdb.Error:
            logger.exception("    результат шага %s не прочитан", table)
            return details
        details.update(result_columns=columns, rows=rows, preview=preview)
        return details

    def _result_columns(self, table: str) -> list[str]:
        """Колонки результата хода для памяти; каталог недоступен — без них."""
        try:
            return self.db.columns(table)
        except duckdb.Error:
            logger.exception("<<< run() колонки результата %s не прочитаны", table)
            return []

    def _finish(
        self,
        plan: ResearchPlan,
        steps: list[ResearchStep],
        tables: dict[int, str],
        fallback: str,
        question: str = "",
        inputs: dict[int, str] | None = None,
    ) -> Execution:
        """Выбрать таблицу ответа и сопутствующие результаты."""
        executed = [step for step in steps if step.step_id in tables]
        if plan.result_step is not None and plan.result_step in tables:
            result_table = tables[plan.result_step]
        elif executed:
            result_table = tables[executed[-1].step_id]
        else:
            result_table = fallback
        shown = next((step for step in executed if tables[step.step_id] == result_table), None)
        supporting = [
            (step.purpose or step.tool or f"шаг {step.step_id}", tables[step.step_id])
            for step in executed
            if tool_kind(step) == "analysis" and tables[step.step_id] != result_table
        ]
        execution = Execution(
            result_table=result_table,
            supporting=supporting,
            final_viz=plan.final_viz,
            dashboard_requested=plan.dashboard_requested,
            tables=dict(tables),
            plan=plan,
            steps=executed,
            inputs=dict(inputs or {}),
            result_purpose=shown.purpose if shown else "",
            result_kind=tool_kind(shown) if shown else "transform",
            result_tool=shown.tool if shown else "",
        )
        try:
            values = self._distribution_values(question, shown, inputs or {})
        except Exception:
            # Гистограмма — дополнение: без неё показываем статистики шага.
            logger.exception("<<< _finish выборка для гистограммы не собрана")
            values = None
        if values is None:
            return execution
        return Execution(
            result_table=values,
            supporting=[(shown.purpose or shown.tool, result_table), *supporting],
            final_viz="histogram",
            dashboard_requested=plan.dashboard_requested,
            tables=dict(tables),
            plan=plan,
            steps=executed,
            inputs=dict(inputs or {}),
            result_purpose=shown.purpose,
        )

    def _distribution_values(
        self, question: str, shown: ResearchStep | None, inputs: dict[int, str]
    ) -> str | None:
        if shown is None or shown.tool != DISTRIBUTION_TOOL or not asks_distribution(question):
            return None
        source = inputs.get(shown.step_id)
        if source is None:
            return None
        column = identifier(
            tool_registry.get(shown.tool).parse_args(shown.columns, shown.arguments).column
        )
        present = self.db.query(f"SELECT COUNT({column}) AS present FROM {identifier(source)}")
        if present[0]["present"] < MIN_HISTOGRAM_VALUES:
            return None
        return self.db.save_step_result(
            source,
            f"SELECT * FROM (SELECT {column} FROM {identifier(source)} WHERE {column} IS NOT NULL)"
            f" USING SAMPLE reservoir({SPEC_ROW_LIMIT} ROWS) REPEATABLE (0)",
        )

    def _histogram_values(
        self, question: str, result_table: str, records: list[dict], titles: dict[str, str]
    ) -> tuple[str, list[dict]]:
        """Оставить для гистограммы одну колонку — ту, о которой спросили."""
        column = histogram_column(question, infer_field_types(records), titles)
        if column is None or list(records[0]) == [column]:
            return result_table, records
        table = self.db.save_step_result(
            result_table, f"SELECT {identifier(column)} FROM {identifier(result_table)}"
        )
        return table, self.db.to_records(table, limit=SPEC_ROW_LIMIT)

    async def _replan_after_lookup(
        self,
        memory: SessionMemory,
        question: str,
        executed: list[ResearchStep],
        tables: dict[int, str],
        observation: str,
    ) -> ResearchPlan:
        """Достроить план с учётом результата lookup-шага."""
        observed = self.executor.db.to_records(observation, limit=OBSERVATION_ROWS)
        context = memory.build_context(question)
        context["executed"] = _describe_executed(executed, tables, observed)
        context["next_step_id"] = _next_step_id(tables)
        logger.info(">>> _replan_after_lookup после шага %d", executed[-1].step_id)
        plan = await self._plan(question, context)
        return _renumber(plan, context["next_step_id"])

    async def _build_dashboard(
        self,
        session_id: str,
        question: str,
        execution: Execution,
        titles: dict[str, str],
        fallback: DashboardTile,
    ) -> dict:
        plan = execution.plan
        tiles = []
        for planned in plan.dashboard_tiles:
            table = execution.tables.get(planned.step_id)
            if table is None:
                logger.warning("    плитка %r: шаг %d не выполнен", planned.title, planned.step_id)
                continue
            try:
                tiles.append(await self._dashboard_tile(planned, table, titles))
            except Exception:
                logger.exception("    плитка %r не собрана", planned.title)
        template_id = plan.template_id if tiles else None
        logger.info(">>> run() дашборд: плиток=%d шаблон=%s", len(tiles), template_id)
        dashboard = await DashboardGenerator(db=self.db).generate(
            session_id,
            plan.dashboard_title or DASHBOARD_TITLE,
            _arrange(tiles) if tiles else [fallback],
            template_id=template_id,
        )
        # Плитки уходят сразу в событии: фронт рисует превью без GET.
        # exclude_none убирает snapshot_data (в payload он None) и другие
        # пустые поля — снимок данных в SSE не передаётся.
        payload = DashboardPayload.from_dashboard(dashboard)
        return {"type": "dashboard", "data": payload.model_dump(exclude_none=True)}

    async def _dashboard_tile(
        self, planned: DashboardTilePlan, table: str, titles: dict[str, str]
    ) -> DashboardTile:
        requested = "kpi" if planned.is_kpi and planned.chart_type == "auto" else planned.chart_type
        records = self.db.to_records(table, limit=SPEC_ROW_LIMIT)
        if requested == "kpi" and not is_single_number(records):
            logger.warning("    плитка %r: KPI не по одному числу", planned.title)
            requested = "auto"
        chart_type = select_chart(planned.title, table, requested, records)
        spec = build_vega_spec(chart_type, table, db=self.db, titles=titles, title=planned.title)
        built = spec["usermeta"]["chart_type"]
        insight = None
        if planned.insight_needed:
            try:
                insight = await self.insight_gen.generate_tile_insight(planned.title, table, built)
            except Exception:
                logger.exception("    плитка %r: вывод не построен", planned.title)
        return DashboardTile(
            title=planned.title,
            spec=spec,
            source_step_id=planned.step_id,
            result_table=table,
            insight=insight or None,
            is_kpi=built == "kpi",
        )

    async def _apply_recovery(
        self,
        memory: SessionMemory,
        pending: dict,
        request: ChatRequest,
        emit: Emit = _ignore,
    ) -> Execution:
        """Применить ответ пользователя на уточнение и продолжить выполнение.

        Поддерживаемые варианты recovery_response:
          - JSON с полем ``action``: ``"skip_step"`` — пропустить неудачный шаг.
          - Свободный текст (любая другая строка) — перепланирование с учётом
            текста уточнения и контекста предыдущих шагов.
        """
        recovery_text = request.recovery_response or ""
        action = None
        free_text = recovery_text

        # Пытаемся распарсить JSON с действием (кнопки фронта).
        try:
            parsed = json.loads(recovery_text)
            if isinstance(parsed, dict) and "action" in parsed:
                action = parsed["action"]
                free_text = parsed.get("text", "")
        except (json.JSONDecodeError, TypeError):
            action = None
            free_text = recovery_text

        # Вопрос берём из сохранённого контекста: фронт может прислать в question
        # сам текст уточнения, а инсайт и перепланирование нужны по исходному.
        question = pending.get("question") or request.question
        failed_step_id = pending["failed_step_id"]
        plan_steps = [ResearchStep.model_validate(s) for s in pending["plan_steps"]]
        input_table = pending["input_table"]
        source_table = pending.get("source_table") or input_table
        tables = {int(key): value for key, value in (pending.get("step_tables") or {}).items()}

        if action == "skip_step":
            # Пропускаем неудачный шаг, выполняем остальные из сохранённого плана.
            result_step = pending.get("result_step")
            plan = ResearchPlan(
                steps=[s for s in plan_steps if s.step_id > failed_step_id],
                final_viz=pending.get("final_viz", "auto"),
                dashboard_requested=pending.get("dashboard_requested", False),
                result_step=None if result_step == failed_step_id else result_step,
                dashboard_title=pending.get("dashboard_title"),
                template_id=pending.get("template_id"),
                dashboard_tiles=pending.get("dashboard_tiles") or [],
            )
            emit(_plan_event(plan.steps))
            return await self._execute_plan(
                memory,
                plan,
                input_table,
                question,
                step_tables=tables,
                source_table=source_table,
                emit=emit,
            )

        # Перепланирование: формируем уточнённый вопрос с учётом ответа
        # пользователя и контекста предыдущих шагов.
        clarification_note = ""
        if free_text:
            clarification_note = f"\nУточнение пользователя: {free_text}"

        modified_question = f"{question}{clarification_note}" if question else free_text

        # Контекст включает шаги, выполненные до сбоя, и ошибку, чтобы LLM могла
        # спланировать обход или исправление неудачного шага.
        executed = [s for s in plan_steps if s.step_id in tables]
        context = memory.build_context(modified_question)
        context["executed"] = (
            f"{_describe_executed(executed, tables)}\n"
            f"Ошибка на шаге {failed_step_id}: {pending['error']}"
        )
        context["next_step_id"] = _next_step_id(tables)

        plan = _renumber(await self._plan(modified_question, context), context["next_step_id"])
        _require_steps(plan)
        emit(_plan_event(plan.steps))
        return await self._execute_plan(
            memory,
            plan,
            input_table,
            modified_question,
            step_tables=tables,
            source_table=source_table,
            emit=emit,
        )

    async def _overview(self, memory: SessionMemory, table: str) -> AsyncIterator[dict]:
        """Ответ на «что передо мной?»: объём, период и описание простыми словами.

        Таблица связана ключами с другими — обзор всех связанных таблиц
        (_schema_overview). Описание не построилось (LLM недоступна) — объём
        и период всё равно отдаются. Технической таблицы колонок (типы,
        заполненность) в ответе нет: пользователю она не нужна.
        """
        group = relations.component(relations.load(self.db), table)
        if len(group) > 1:
            async for event in self._schema_overview(memory, sorted(group)):
                yield event
            return
        stage = "insight"
        try:
            yield _progress_event("insight", "started")
            profile = overview.passport(self.db, table)
            try:
                summary = await overview.describe_dataset(
                    self.db, table, getattr(self.planner, "llm", None)
                )
            except Exception:
                logger.exception("<<< run() описание таблицы %s не построено", table)
                summary = ""
            # Название LLM пишет вместе с описанием (#211): читаем после него.
            headline = overview.headline(profile, relations.table_title(self.db, table))
            details = overview.paragraphs(summary) or [SUMMARY_UNAVAILABLE]
            yield {"type": "text", "data": {"headline": headline, "details": details}}
        except Exception as exc:
            logger.exception("<<< run() обзор таблицы: этап %s упал", stage)
            memory.finish_turn("failed")
            yield _progress_event(stage, "failed", error=str(exc))
            yield _failure_event()
            yield _done(memory.turn_id)
            return
        memory.finish_turn("ok", headline=headline, details=details)
        yield _done(memory.turn_id)

    async def _schema_overview(
        self, memory: SessionMemory, tables: list[str]
    ) -> AsyncIterator[dict]:
        """«Что передо мной?» по таблицам, связанным ключами (#165).

        Таблицы — по важности данных (relations.priority_order: сначала факты,
        затем справочники): название, роль, объём и период, первый абзац
        описания; в конце — связи. График — сводка таблиц вместо колонок одной.
        """
        stage = "insight"
        try:
            yield _progress_event("insight", "started")
            links = relations.load(self.db)
            order = relations.priority_order(self.db, tables, links)
            labels, details, rows = [], [], []
            for number, name in enumerate(order, 1):
                profile = overview.passport(self.db, name)
                try:
                    summary = await overview.describe_dataset(
                        self.db, name, getattr(self.planner, "llm", None)
                    )
                except Exception:
                    logger.exception("<<< run() описание таблицы %s не построено", name)
                    summary = ""
                # Название LLM пишет вместе с описанием (#211): читаем после него.
                title = relations.table_title(self.db, name)
                role = relations.role(links, name)
                labels.append(f"«{title}» ({name})" if title else name)
                facts = overview.facts(profile).removeprefix(f"{name}: ")
                first = (overview.paragraphs(summary) or [SUMMARY_UNAVAILABLE])[0]
                details.append(f"{number}. {labels[-1]} — {role}: {facts}. {first}")
                rows.append(
                    {
                        "Таблица": name,
                        "Название": title or "",
                        "Роль": role,
                        "Строк": profile.row_count,
                        "Колонок": profile.column_count,
                        "Ссылается на": ", ".join(relations.references(links, name)),
                    }
                )
            inside = [r for r in links if r.table in tables and r.ref_table in tables]
            details.append("Связи: " + "; ".join(r.describe() for r in inside))
            headline = (
                f"Перед вами {overview.plural(len(order), TABLE_WORDS)}, связанные ключами; "
                f"главные данные — {labels[0]}"
            )
            yield {"type": "text", "data": {"headline": headline, "details": details}}

            stage = "chart"
            yield _progress_event("chart", "started")
            spec = build_vega_spec("table", data=rows, title=SCHEMA_CAPTION)
            yield {"type": "chart", "data": {"spec": spec, "caption": SCHEMA_CAPTION}}
        except Exception as exc:
            logger.exception("<<< run() обзор связанных таблиц: этап %s упал", stage)
            memory.finish_turn("failed")
            yield _progress_event(stage, "failed", error=str(exc))
            yield _failure_event()
            yield _done(memory.turn_id)
            return
        memory.finish_turn("ok", headline=headline)
        yield _done(memory.turn_id)

    def _morph(self, memory: SessionMemory, question: str, titles: dict[str, str]) -> dict | None:
        """Сменить вид прошлого графика из кеша (None — нужен новый план).

        Срабатывает, если вопрос — просьба сменить вид (detect_morph), в памяти
        есть прошлый график и его temp_result_vN ещё лежит в кеше. LLM и
        исходный SQL не вызываются.
        """
        target = detect_morph(question)
        last = memory.last_chart()
        if target is None or last is None:
            return None
        try:
            records = self.cache.get(last["result_table"], limit=SPEC_ROW_LIMIT)
        except duckdb.Error:
            return None
        spec = morph(last["type"], target, records, titles, last["result_table"])
        chart_type = spec["usermeta"]["chart_type"]
        memory.record_chart(chart_type, last["result_table"], last["title"])
        memory.finish_turn(
            "ok", headline=f"График перестроен: {chart_type}", result_table=last["result_table"]
        )
        return _chart_event(spec, last["title"], last["result_table"])


def _requested_view(execution: Execution, records: list[dict]) -> str:
    if execution.final_viz == NO_CHART and not execution.dashboard_requested:
        return NO_CHART
    requested = "auto" if execution.final_viz == NO_CHART else execution.final_viz
    if execution.result_kind == "transform":
        return requested
    # Стат-тест и разведка — строки чисел: таблица. Тренд и сезонность —
    # ряд по времени: его рисуем линией (или областью, если её просили).
    if not fits("line", records, infer_field_types(records)):
        return "table"
    return requested if requested in ("line", "area") else "line"


def _table_answer(pending: dict, request: ChatRequest) -> tuple[str | None, ChatRequest]:
    """Таблица из ответа на уточнение (название или имя таблицы) и запрос по исходному вопросу.

    Ответ не совпал ни с одним вариантом — None, а ответ становится
    уточнением к вопросу.
    """
    answer = (request.recovery_response or "").strip()
    question = pending["question"]
    chosen = next(
        (
            table
            for title, table in pending["options"].items()
            if answer.casefold() in (title.casefold(), table.casefold())
        ),
        None,
    )
    if chosen is None:
        question = f"{question}\nУточнение пользователя: {answer}"
    return chosen, request.model_copy(update={"question": question, "recovery_response": None})


def _confirmation(remembered: list[str]) -> str:
    """Подтверждение записи в воспоминания; ничего не записано — пусто."""
    return (
        REMEMBERED.format(notes="; ".join(f"«{text}»" for text in remembered)) if remembered else ""
    )


def _table_event(db, table: str, previous: str | None) -> dict:
    """Агент сам взял таблицу: фронт показывает её и переносит на неё ленту."""
    return {
        "type": "table",
        "data": {"table": table, "previous": previous, "title": relations.table_title(db, table)},
    }


def existing_results(db: DuckDBManager, tables: dict[int, str]) -> dict[int, str]:
    """Таблицы шагов из чекпоинта, которые ещё лежат в DuckDB; удалённые очисткой — пересчитать."""
    kept = {key: table for key, table in tables.items() if db.columns(table)}
    if len(kept) < len(tables):
        logger.warning("чекпоинт: нет таблиц %s, шаги выполнятся заново", set(tables) - set(kept))
    return kept


def _next_step_id(tables: dict[int, str]) -> int:
    return max(tables) + 1 if tables else 0


def _renumber(plan: ResearchPlan, start: int) -> ResearchPlan:
    """Пронумеровать новые шаги подряд, начиная со start."""
    for offset, step in enumerate(plan.steps):
        step.step_id = start + offset
    return plan


def _require_steps(plan: ResearchPlan) -> None:
    """Пустой план — подходящих инструментов нет; просим переформулировать."""
    if not plan.steps:
        raise ClarificationNeeded(
            step_id=-1,
            error="Не удалось подобрать инструменты под этот вопрос.",
            suggestions=["Переформулировать вопрос"],
        )


def _describe_executed(
    steps: list[ResearchStep], tables: dict[int, str], observed: list[dict] | None = None
) -> str:
    """Выполненные шаги (и результат последнего lookup) для планировщика."""
    lines = [
        f"$step_{step.step_id}: {step.tool} args={step.arguments or step.columns} — {step.purpose}"
        for step in steps
        if step.step_id in tables
    ]
    if observed is not None:
        lines.append(f"Результат $step_{steps[-1].step_id}: {observed}")
    return "\n".join(lines) or "нет"


def _arrange(tiles: list[DashboardTile]) -> list[DashboardTile]:
    kpis = [tile for tile in tiles if tile.is_kpi]
    charts = [tile for tile in tiles if not tile.is_kpi]
    kpis_per_row = GRID_COLUMNS // KPI_WIDTH
    charts_per_row = GRID_COLUMNS // CHART_WIDTH
    kpi_rows = -(-len(kpis) // kpis_per_row)
    placed = [
        tile.model_copy(
            update={
                "layout": {
                    "x": position % kpis_per_row * KPI_WIDTH,
                    "y": position // kpis_per_row * KPI_HEIGHT,
                    "w": KPI_WIDTH,
                    "h": KPI_HEIGHT,
                }
            }
        )
        for position, tile in enumerate(kpis)
    ]
    placed.extend(
        tile.model_copy(
            update={
                "layout": {
                    "x": position % charts_per_row * CHART_WIDTH,
                    "y": kpi_rows * KPI_HEIGHT + position // charts_per_row * CHART_HEIGHT,
                    "w": CHART_WIDTH,
                    "h": CHART_HEIGHT,
                }
            }
        )
        for position, tile in enumerate(charts)
    )
    return placed
