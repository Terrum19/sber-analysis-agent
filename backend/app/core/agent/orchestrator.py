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
import inspect
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field

import duckdb

from app.api.schemas.chat import (
    ChatRequest,
    DashboardPayload,
    PlannedStep,
    PlanStepOut,
    ProgressPayload,
)
from app.config import settings
from app.core.agent.budget import (
    BudgetExceeded,
    budget_from_settings,
    current_budget,
    set_budget,
)
from app.core.agent.chart_pick import ChartPicker
from app.core.agent.explainer import Explainer
from app.core.agent.insight import InsightGenerator
from app.core.agent.metrics import TurnMetrics, current_metrics, set_metrics
from app.core.agent.planner import PlanInvalid, StepPlanner, is_dashboard_question
from app.core.agent.recovery import fixable
from app.core.agent.router import Route, simple_plan
from app.core.agent.step_executor import StepExecutor, resolve_input, tool_kind
from app.core.agent.triage import small_talk_reply
from app.core.agent.turn_log import TurnLog, describe_plan
from app.core.cache.plan_cache import PlanCache
from app.core.dashboards.generator import DashboardGenerator
from app.core.dashboards.models import DashboardTile
from app.core.data import overview, relations
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.table_vectors import TableVectors
from app.core.llm.budget import track_truncations
from app.core.llm.client import LLMClient
from app.core.logging_setup import bound_turn, set_turn_id
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.sandbox.wasm_runtime import SandboxError
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.core.tools.toolgen.factory import CreatedTool, ToolCreationError, ToolFactory
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

logger = logging.getLogger(__name__)

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
OVERVIEW_CAPTION = "Колонки таблицы"
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
# Осмотр таблицы — разведка: его паспорт не ответ пользователю.
INSPECT_TOOL = "inspect_temp_table"

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


class NoSuitableTool(ClarificationNeeded):
    """Подходящего инструмента нет; оркестратор может создать его (toolgen).

    Наследник ClarificationNeeded: если создание не удалось, внешний код
    по-прежнему получает обычное уточнение. ``planning_finished`` — событие
    planning/finished уже отправлено (пустой план), failed слать не нужно.
    """

    def __init__(
        self,
        error: str = "Не удалось подобрать инструменты под этот вопрос.",
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(
            step_id=-1,
            error=error,
            suggestions=suggestions or ["Переформулировать вопрос"],
        )
        self.planning_finished = False


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


def _planned_steps(steps: list[ResearchStep]) -> list[PlannedStep]:
    """Шаги плана для прогресса: purpose или tool, без выдуманных «Шаг N»."""
    return [
        PlannedStep(step_id=step.step_id, purpose=step.purpose or step.tool, tool=step.tool)
        for step in steps
    ]


def _reports_substages(planner: object) -> bool:
    """Планировщик умеет сообщать реальные фазы планирования."""
    plan = getattr(planner, "plan", None)
    if plan is None:
        return False
    try:
        return "on_substage" in inspect.signature(plan).parameters
    except (TypeError, ValueError):
        return False


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


def _plan_event(steps: list[ResearchStep], **fields: object) -> dict:
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
        **fields,
    )


def _fixing_event(step: ResearchStep, attempt: int, error: Exception) -> dict:
    return _step_event(step, "fixing", attempt=attempt, error=str(error))


def _text_event(
    headline: str,
    details: list[str],
    limits: str | list[str] | None = None,
) -> dict:
    """Вывод целиком: когда стрим выключен или сработал structured-фолбэк."""
    data: dict = {"headline": headline, "details": details}
    if limits:
        data["limits"] = limits
    return {"type": "text", "data": data}


def _text_chunk_event(chunk: str) -> dict:
    """Дельта текста инсайта при INSIGHT_STREAM=true."""
    return {"type": "text", "data": {"chunk": chunk}}


def _chart_event(spec: dict, caption: str, result_table: str) -> dict:
    """График с таблицей результата: из неё фронт пересчитывает плитку и выгружает Excel.

    Ответ без шагов строится по исходной таблице — её не отдаём.
    """
    data = {"spec": spec, "caption": caption}
    if RESULT_TABLE.fullmatch(result_table):
        data["result_table"] = result_table
    return {"type": "chart", "data": data}


def _failure_event(message: str | None = None) -> dict:
    return {"type": "error", "data": {"message": message or FAILURE_MESSAGE}}


def _done(turn_id: str, *, message: str | None = None) -> dict:
    """Событие конца хода; метрики аддитивны к прежнему контракту ``turn_id``."""
    data: dict[str, object] = {"turn_id": turn_id}
    if message:
        data["message"] = message
    metrics = current_metrics()
    if metrics is not None:
        data.update(
            route=metrics.route,
            llm_calls=metrics.llm_calls,
            prompt_tokens=metrics.prompt_tokens,
            total_ms=metrics.total_ms(),
        )
    return {"type": "done", "data": data}


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
        tool_factory: ToolFactory | None = None,
        explainer: Explainer | None = None,
        chart_llm: LLMClient | None = None,
    ) -> None:
        # Один DuckDB на весь ход: temp_result_vN, созданные исполнителем,
        # должны читаться графиком, инсайтом и кешем из той же базы.
        shared = db or (cache.db if cache is not None else None)
        self.executor = executor or StepExecutor(shared)
        self.db = shared or getattr(self.executor, "db", None) or DuckDBManager()
        self.plan_cache = PlanCache() if settings.agent_plan_cache else None
        self.planner = planner or StepPlanner(cache=self.plan_cache)
        self.insight_gen = insight_gen or InsightGenerator(db=self.db)
        # Пояснение — тем же клиентом LLM, что и вывод (у моков вывода клиента нет).
        llm = getattr(self.insight_gen, "llm", None)
        self.explainer = explainer or Explainer(llm=llm, db=self.db)
        self.cache = cache or StateCache(self.db)
        self.profiles = ProfileMemory(self.db)
        self.vectors = TableVectors(self.db)
        # toolgen: инъекция фабрики для тестов, иначе создаётся лениво из planner.llm.
        self.tool_factory = tool_factory
        self._created_tools_count = 0
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

        ``AGENT_TURN_TIMEOUT_S`` — необязательный жёсткий дедлайн всего хода:
        при положительном значении по истечении фронт получает error + done,
        задача плана отменяется. По умолчанию 0 — общего дедлайна нет, ход
        идёт столько, сколько нужно; от зависаний защищают лимиты этапов,
        таймауты операций и счётчики циклов.
        """
        truncations = track_truncations()
        budget = budget_from_settings(settings)
        metrics = TurnMetrics(
            session_id=request.session_id,
            turn_id="",
            question=request.question,
            model=getattr(getattr(self.planner, "llm", None), "model", settings.llm_model),
        )
        turn_log = TurnLog(request.session_id)
        try:
            # total_s=None (или 0) — asyncio.timeout не ограничивает ход.
            async with asyncio.timeout(budget.total_s):
                # aclosing: клиент отключился — ход закрывается сразу, с отменой задачи плана.
                async with aclosing(self._turn(request)) as events:
                    while True:
                        # Контекст может смениться между anext (тест/FastAPI):
                        # выставляем бюджет и метрики перед каждым возобновлением.
                        set_budget(budget)
                        set_metrics(metrics)
                        event = await turn_log.next_event(events)
                        if event is None:
                            break
                        if event["type"] in TURN_END_EVENTS and truncations:
                            event["data"]["prompt_truncated"] = len(truncations)
                        turn_log.observe(event)
                        yield event
        except TimeoutError:
            # Жёсткий дедлайн (если он включён): не даём ходу висеть и
            # держать SSE-соединение. При AGENT_TURN_TIMEOUT_S=0 его нет.
            set_budget(budget)
            set_metrics(metrics)
            metrics.budget_exceeded = "turn"
            total = budget.total_s or 0
            minutes = max(1, math.ceil(total / 60)) if total else 0
            message = (
                f"Не удалось завершить ход за {minutes} минут"
                if minutes
                else "Не удалось завершить ход"
            )
            event = _failure_event(message)
            turn_log.status = turn_log.status or "failed"
            turn_log.reason = message
            turn_log.observe(event)
            finish = _done(metrics.turn_id, message="timeout")
            turn_log.observe(finish)
            yield event
            yield finish
        except Exception as error:
            turn_log.crashed(error)
            raise
        finally:
            turn_log.close()
            set_budget(budget)
            set_metrics(metrics)
            with bound_turn(turn_log.context):
                logger.info(
                    "метрики хода: %s",
                    json.dumps(metrics.as_log(), ensure_ascii=False, default=str),
                )
            if settings.agent_metrics_store and metrics.turn_id:
                from app.core.agent.metrics import MetricsStore

                await asyncio.to_thread(MetricsStore().record, metrics)
            # В контексте этого генератора снимаем значения; токены из
            # start-контекста здесь невалидны (anext может идти в новом Task).
            set_metrics(None)
            set_budget(None)
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
        metrics = current_metrics()
        if metrics is not None:
            metrics.turn_id = turn_id
            metrics.question = request.question
        # Лимит TOOLGEN_MAX_PER_TURN считается на ход, а оркестратор может
        # переиспользоваться между запросами.
        self._created_tools_count = 0
        budget = current_budget()
        self._mark_running(turn_id)
        logger.info("ход начат: вопрос=%r", request.question)
        if budget is not None:
            logger.info(
                "бюджет хода: дедлайн=%s, вызовов LLM=%d, шагов=%d",
                f"{budget.total_s:.0f} с" if budget.total_s else "нет",
                budget.max_llm_calls,
                budget.max_steps,
            )

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
            if metrics is not None:
                metrics.route = "small_talk"
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
            if metrics is not None:
                metrics.route = "no_table"
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
            if metrics is not None:
                metrics.route = "overview"
            async for event in self._overview(memory, table):
                yield event
            return

        if pending is None:
            # Смена вида прошлого графика — из кеша, без планировщика и SQL.
            morphed = self._morph(memory, request.question, titles)
            if morphed is not None:
                logger.info("маршрут: смена вида графика")
                if metrics is not None:
                    metrics.route = "chart_morph"
                yield morphed
                yield _done(turn_id)
                return

        route: Route | None = None
        if pending is None and settings.agent_simple_router:
            # Быстрый путь: шаблонный план без вызова планировщика.
            route = simple_plan(request.question, self.profiles.load_profile(table), titles)
            if route is not None:
                logger.info("маршрут: простой (%s) — %s", route.kind, route.reason)
                if metrics is not None:
                    metrics.route = route.kind

        logger.info("маршрут: %s", "план" if pending is None else "ответ на уточнение")
        async for event in self._answer(
            memory, request, titles, pending=pending, catalog=catalog, route=route
        ):
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
        route: Route | None = None,
        resume: dict | None = None,
    ) -> AsyncIterator[dict]:
        """План и шаги (или их продолжение), затем инсайт, график и дашборд — до done."""
        turn_id = memory.turn_id
        # План и шаги идут задачей: события прогресса, которые она шлёт
        # через emit, отдаются фронту сразу, не дожидаясь конца плана.
        events: asyncio.Queue[dict | None] = asyncio.Queue()
        task = asyncio.create_task(
            self._research(
                memory, pending, request, events.put_nowait, catalog, route=route, resume=resume
            )
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
        stage_started = time.perf_counter()
        try:
            logger.info(">>> run() генерация инсайта")
            yield _progress_event("insight", "started")
            fast = getattr(self.insight_gen, "fast_insight", None)
            fast_res = (
                fast(question, result_table, execution.steps)
                if settings.agent_fast_insight and callable(fast)
                else None
            )
            if fast_res is not None:
                logger.info("<<< run() быстрый инсайт без LLM")
                if confirmation:
                    fast_res.details.append(confirmation)
                insight = fast_res
                yield _progress_event("insight", "finished", elapsed_ms=_elapsed_ms(stage_started))
                yield _text_event(insight.headline, insight.details, insight.limits)
            elif settings.insight_stream and callable(getattr(self.insight_gen, "stream", None)):
                budget = current_budget()
                if budget is not None:
                    budget.enter_stage("insight")
                queue: asyncio.Queue[dict | None] = asyncio.Queue()
                streamed = False

                def emit_chunk(delta: str) -> None:
                    nonlocal streamed
                    if not delta:
                        return
                    if delta.strip():
                        streamed = True
                    queue.put_nowait(_text_chunk_event(delta))

                insight_task = asyncio.create_task(
                    self.insight_gen.stream(
                        question,
                        result_table,
                        supporting=execution.supporting,
                        steps=execution.steps,
                        memories=memory.context_memories(),
                        result_tool=execution.result_tool,
                        source=memory.table,
                        inputs=execution.inputs,
                        emit=emit_chunk,
                    )
                )
                insight_task.add_done_callback(lambda _: queue.put_nowait(None))
                try:
                    try:
                        while (event := await queue.get()) is not None:
                            yield event
                        insight = await insight_task
                    except (BudgetExceeded, TimeoutError, ValueError) as exc:
                        # Бюджет или нечитаемый ответ: результат уже посчитан,
                        # поэтому отдаём шаблонный вывод, как в _insight().
                        # Если текст уже показан, второй ответ дал бы дубль.
                        if streamed:
                            raise
                        metrics = current_metrics()
                        if metrics is not None and isinstance(exc, (BudgetExceeded, TimeoutError)):
                            metrics.budget_exceeded = f"insight: {exc}"
                        logger.warning("инсайт (стрим) не удался, шаблонный вывод: %s", exc)
                        insight = self._fallback_insight(
                            question, result_table, execution.steps, str(exc)
                        )
                finally:
                    # Клиент отключился посреди стрима — закрываем поток LLM.
                    if not insight_task.done():
                        insight_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await insight_task
                    if budget is not None:
                        budget.leave_stage("insight")
                if confirmation:
                    insight.details.append(confirmation)
                yield _progress_event("insight", "finished", elapsed_ms=_elapsed_ms(stage_started))
                if not streamed:
                    # Фолбэк или пустой стрим: без этого фронт не увидит вывод.
                    yield _text_event(insight.headline, insight.details, insight.limits)
            else:
                insight = await self._insight(
                    question,
                    execution,
                    memories=memory.context_memories(),
                    source=memory.table,
                    confirmation=confirmation,
                )
                yield _progress_event("insight", "finished", elapsed_ms=_elapsed_ms(stage_started))
                yield _text_event(insight.headline, insight.details, insight.limits)
            logger.info(
                "<<< run() инсайт: headline=%r details=%r", insight.headline, insight.details
            )

            stage = "chart"
            stage_started = time.perf_counter()
            picked = await picking if picking is not None else None
            if picked is not None:
                chart_type, chart_table, records = picked.chart_type, picked.table, picked.records
                columns = {"x": picked.x, "y": picked.y, "color": picked.color}
                purpose = picked.purpose
                yield _progress_event("chart", "started")
            else:
                records = await self.db.to_records_async(result_table, limit=SPEC_ROW_LIMIT)
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
                yield _progress_event("chart", "started")
                if chart_type == "histogram":
                    result_table, records = await asyncio.to_thread(
                        self._histogram_values, question, result_table, records, titles
                    )
                chart_table, columns, purpose = result_table, {}, execution.result_purpose
            logger.debug(">>> run() построение графика chart_type=%s", chart_type)
            spec = build_vega_spec(
                chart_type,
                chart_table,
                data=records,
                titles=titles,
                title=purpose or chart_title(records, titles),
                **columns,
            )
            yield _progress_event("chart", "finished", elapsed_ms=_elapsed_ms(stage_started))
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
            yield _progress_event(
                stage, "failed", error=str(exc), elapsed_ms=_elapsed_ms(stage_started)
            )
            yield _failure_event()
            yield _done(turn_id)
            return
        finally:
            # Ход упал или клиент ушёл раньше графика — выбор больше не нужен.
            if picking is not None:
                picking.cancel()

        if execution.dashboard_requested:
            dashboard_started = time.perf_counter()
            yield _progress_event("dashboard", "started")
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
            except BudgetExceeded as exc:
                # Бюджет дашборда исчерпан: у пользователя уже есть текст и
                # график, дашборд молча пропускаем (без error и failed).
                logger.warning("<<< run() дашборд пропущен: %s", exc)
            except Exception as exc:
                # Ответ и график уже у пользователя: ход не провален, нет
                # только дашборда — без error, дальше обычный done.
                logger.exception("<<< run() дашборд не собран")
                yield _progress_event(
                    "dashboard",
                    "failed",
                    error=str(exc),
                    elapsed_ms=_elapsed_ms(dashboard_started),
                )
            else:
                yield _progress_event(
                    "dashboard", "finished", elapsed_ms=_elapsed_ms(dashboard_started)
                )
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

    async def _insight(
        self,
        question: str,
        execution: Execution,
        *,
        memories: list[dict] | None = None,
        source: str = "",
        confirmation: str | None = None,
    ) -> Insight:
        """Инсайт с бюджетом: шаблон для одной ячейки, fallback при таймауте."""
        budget = current_budget()
        if budget is not None:
            budget.enter_stage("insight")
        timeout = (
            budget.timeout_for("insight", settings.agent_insight_timeout_s)
            if budget is not None
            else settings.agent_insight_timeout_s
        )
        result_table = execution.result_table
        try:
            async with asyncio.timeout(timeout):
                fast = getattr(self.insight_gen, "fast_insight", None)
                if settings.agent_fast_insight and callable(fast):
                    insight = fast(question, result_table, execution.steps)
                    if insight is not None:
                        logger.info("<<< run() быстрый инсайт без LLM")
                        if confirmation:
                            insight.details.append(confirmation)
                        return insight
                insight = await self.insight_gen.generate(
                    question,
                    result_table,
                    supporting=execution.supporting,
                    steps=execution.steps,
                    memories=memories,
                    result_tool=execution.result_tool,
                    source=source,
                    inputs=execution.inputs,
                )
                if confirmation:
                    insight.details.append(confirmation)
                return insight
        except (BudgetExceeded, TimeoutError) as exc:
            metrics = current_metrics()
            if metrics is not None:
                metrics.budget_exceeded = f"insight: {exc}"
            logger.warning("инсайт не уложился в бюджет: %s", exc)
            fallback = self._fallback_insight(question, result_table, execution.steps, str(exc))
            if confirmation:
                fallback.details.append(confirmation)
            return fallback
        except ValueError as exc:
            # Модель вернула пустой или нечитаемый JSON (например, reasoning-
            # модель потратила весь max_tokens на рассуждения). Результат уже
            # посчитан: шаблонный вывод лучше провала хода без ответа.
            logger.warning("инсайт: LLM вернула нечитаемый ответ: %s", exc)
            fallback = self._fallback_insight(question, result_table, execution.steps, str(exc))
            if confirmation:
                fallback.details.append(confirmation)
            return fallback
        finally:
            if budget is not None:
                budget.leave_stage("insight")

    def _fallback_insight(
        self,
        question: str,
        result_table: str,
        steps: list[ResearchStep],
        reason: str,
    ) -> Insight:
        """Шаблонный вывод, когда LLM не дала инсайт: результат уже посчитан."""
        fallback = getattr(self.insight_gen, "fallback_insight", None)
        if callable(fallback):
            return fallback(question, result_table, steps, reason)
        try:
            rows = len(self.db.to_records(result_table, limit=5))
        except Exception:
            logger.warning("запасной инсайт: таблица %s не прочитана", result_table, exc_info=True)
            rows = 0
        return Insight(
            headline="Результат готов",
            details=[f"Строк в результате: {rows}"],
            limits=reason,
        )

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
        route: Route | None = None,
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
        planning_started = time.perf_counter()
        if route is not None:
            plan = route.plan
            logger.info("план (router %s): %s", route.kind, describe_plan(plan))
            emit(_progress_event("planning", "started", tables=tables, turn_id=memory.turn_id))
            emit(
                _plan_event(
                    plan.steps,
                    planned_steps=_planned_steps(plan.steps),
                    elapsed_ms=_elapsed_ms(planning_started),
                    turn_id=memory.turn_id,
                )
            )
        else:
            logger.debug(">>> run() планируем шаги (planner.plan)")
            plan = await self._plan_or_create(
                request.question,
                context,
                memory.table,
                emit,
                tables=tables,
                allow_empty_remember=True,
                turn_id=memory.turn_id,
            )
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

    async def _plan(
        self,
        question: str,
        context: dict,
        *,
        stage: str = "planning",
        emit: Emit = _ignore,
    ) -> ResearchPlan:
        """План от планировщика; невалидный и после переспроса — уточнение у пользователя.

        ``stage`` — куда относить реальные фазы планирования: ``planning`` для
        нового вопроса и ``replanning`` для достройки после lookup-шага.
        """
        budget = current_budget()
        if budget is not None:
            budget.enter_stage("planning")
        started_at = time.perf_counter()

        def on_substage(name: str) -> None:
            emit(_progress_event(stage, "substage", substage=name))

        try:
            if _reports_substages(self.planner):
                plan = await self.planner.plan(question, context, on_substage=on_substage)
            else:
                plan = await self.planner.plan(question, context)
        except PlanInvalid as exc:
            message = "Не удалось составить корректный план: " + "; ".join(exc.errors)
            if any(
                marker in error
                for error in exc.errors
                for marker in ("неизвестный инструмент", "не указан инструмент")
            ):
                # Модель придумала тул: возможно, его стоит создать, а не просить
                # пользователя переформулировать.
                raise NoSuitableTool(message) from exc
            raise ClarificationNeeded(
                step_id=-1,
                error=message,
                suggestions=["Переформулировать вопрос"],
            ) from exc
        finally:
            if budget is not None:
                budget.leave_stage("planning")
        logger.info("план за %.1f с: %s", time.perf_counter() - started_at, describe_plan(plan))
        return plan

    async def _plan_or_create(
        self,
        question: str,
        context: dict,
        input_table: str,
        emit: Emit = _ignore,
        *,
        tables: list[str] | None = None,
        allow_empty_remember: bool = True,
        tile_id: int | None = None,
        turn_id: str | None = None,
    ) -> ResearchPlan:
        """План; если инструментов не хватает — создать тул и перепланировать.

        Пустой план с remember — это просьба запомнить, toolgen не нужен.
        Создание ограничено TOOLGEN_MAX_PER_TURN на ход.
        """
        while True:
            planning_started = time.perf_counter()
            emit(
                _progress_event(
                    "planning", "started", tables=tables, tile_id=tile_id, turn_id=turn_id
                )
            )
            try:
                plan = await self._plan(question, context, emit=emit)
            except BudgetExceeded as exc:
                emit(
                    _progress_event(
                        "planning",
                        "failed",
                        error=str(exc),
                        tile_id=tile_id,
                        elapsed_ms=_elapsed_ms(planning_started),
                        turn_id=turn_id,
                    )
                )
                raise ClarificationNeeded(
                    step_id=-1,
                    error=(
                        "Не удалось уложиться в бюджет планирования; уточните вопрос или сузьте его"
                    ),
                    suggestions=["Переформулировать вопрос"],
                ) from exc
            except NoSuitableTool as exc:
                if not await self._create_tool(question, input_table, context, emit, exc):
                    emit(
                        _progress_event(
                            "planning",
                            "failed",
                            error=str(exc),
                            tile_id=tile_id,
                            elapsed_ms=_elapsed_ms(planning_started),
                            turn_id=turn_id,
                        )
                    )
                    raise
                continue
            except Exception as exc:
                emit(
                    _progress_event(
                        "planning",
                        "failed",
                        error=str(exc),
                        tile_id=tile_id,
                        elapsed_ms=_elapsed_ms(planning_started),
                        turn_id=turn_id,
                    )
                )
                raise
            if plan.steps or (allow_empty_remember and plan.remember):
                emit(
                    _plan_event(
                        plan.steps,
                        planned_steps=_planned_steps(plan.steps),
                        tile_id=tile_id,
                        elapsed_ms=_elapsed_ms(planning_started),
                        turn_id=turn_id,
                    )
                )
                return plan
            # Пустой план без remember: подходящего инструмента нет.
            emit(
                _plan_event(
                    [],
                    tile_id=tile_id,
                    elapsed_ms=_elapsed_ms(planning_started),
                    turn_id=turn_id,
                )
            )
            if plan.route in ("answer", "overview"):
                # Вопрос о прошлых ответах или обзор таблицы: шагов нет по смыслу,
                # toolgen не нужен — ответят explainer/обзор.
                return plan
            if self._get_tool_factory() is None:
                if settings.toolgen_enabled:
                    # toolgen включён, но создать инструмент нечем (у планировщика
                    # нет LLM): пустой план — ответ словами (route answer/overview)
                    # или пояснение по журналу, а не уточнение.
                    return plan
                exc = NoSuitableTool()
                exc.planning_finished = True
                raise exc
            exc = NoSuitableTool()
            if not await self._create_tool(question, input_table, context, emit, exc):
                exc.planning_finished = True
                raise exc

    async def _create_tool(
        self,
        question: str,
        input_table: str,
        context: dict,
        emit: Emit,
        reason: Exception | None = None,
    ) -> bool:
        """Создать инструмент через toolgen; False — создание недоступно/упало."""
        if self._created_tools_count >= settings.toolgen_max_per_turn:
            logger.info("toolgen: лимит созданий за ход исчерпан")
            return False
        factory = self._get_tool_factory()
        if factory is None:
            return False
        tile_id = context.get("tile_id")
        purpose = context.get("tile_title") or question
        requested = _requested_tool(reason) if reason is not None else None
        emit(
            _progress_event("toolgen", "started", purpose=purpose, tool=requested, tile_id=tile_id)
        )
        started_at = time.perf_counter()
        try:
            created: CreatedTool = await factory.create(
                question=question,
                input_table=input_table,
                purpose=purpose,
                tile_title=context.get("tile_title", ""),
                tile_intent=context.get("tile_intent", ""),
            )
        except ToolCreationError as exc:
            logger.warning("toolgen: инструмент не создан: %s", exc)
            emit(
                _progress_event(
                    "toolgen",
                    "failed",
                    purpose=purpose,
                    error=str(exc),
                    elapsed_ms=_elapsed_ms(started_at),
                    tile_id=tile_id,
                )
            )
            return False
        except Exception as exc:
            logger.exception("toolgen: неожиданный сбой")
            emit(
                _progress_event(
                    "toolgen",
                    "failed",
                    purpose=purpose,
                    error=str(exc),
                    elapsed_ms=_elapsed_ms(started_at),
                    tile_id=tile_id,
                )
            )
            return False
        self._created_tools_count += 1
        context.setdefault("created_tools", []).append(created)
        emit(
            _progress_event(
                "toolgen",
                "finished",
                purpose=purpose,
                tool=created.tool.name,
                elapsed_ms=_elapsed_ms(started_at),
                tile_id=tile_id,
            )
        )
        return True

    def _get_tool_factory(self) -> ToolFactory | None:
        """Фабрика по умолчанию: LLM планировщика и общий DuckDB."""
        if not settings.toolgen_enabled:
            return None
        if self.tool_factory is not None:
            return self.tool_factory
        llm = getattr(self.planner, "llm", None)
        if llm is None:
            return None
        self.tool_factory = ToolFactory(db=self.db, llm=llm)
        return self.tool_factory

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
        if len(plan.steps) > settings.agent_max_steps:
            raise PlanInvalid(
                [f"в плане {len(plan.steps)} шагов, максимум {settings.agent_max_steps}"]
            )
        if settings.agent_dag_executor:
            from app.core.agent.parallel_executor import ParallelPlanExecutor
            from app.core.agent.plan_graph import PlanGraph

            graph = PlanGraph.from_plan(plan)
            errors = graph.validate()
            if errors:
                raise PlanInvalid(errors)
            execution, executed_tables = await ParallelPlanExecutor(self.executor).execute(
                graph, input_table, source, emit, plan=plan
            )
            for step_id, table in executed_tables.items():
                memory.record_step(graph.nodes[step_id].step, table)
            return execution
        metrics = current_metrics()
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
            if metrics is not None:
                metrics.record_stage(f"step_{step.step_id}", elapsed_ms / 1000)
            steps[index] = step
            kind = tool_kind(step)
            logger.info("шаг %d готов за %d мс: %s", step.step_id, elapsed_ms, table)
            tables[step.step_id] = table
            memory.record_step(step, table)
            if kind == "transform":
                current = table
            index += 1

            if kind == "lookup" and replans < settings.agent_max_replans:
                replans += 1
                executed = steps[:index]
                replanning_started = time.perf_counter()
                emit(_progress_event("replanning", "started"))
                try:
                    replanned = await self._replan_after_lookup(
                        memory,
                        question,
                        executed,
                        tables,
                        observation=table,
                        # Паспорт осмотренной таблицы не хранит связь колонок в
                        # строке: планировщику дополнительно показываем её строки.
                        sample_source=(
                            inputs.get(step.step_id) if step.tool == INSPECT_TOOL else None
                        ),
                        emit=emit,
                    )
                except Exception as exc:
                    emit(
                        _progress_event(
                            "replanning",
                            "failed",
                            error=str(exc),
                            elapsed_ms=_elapsed_ms(replanning_started),
                        )
                    )
                    raise
                emit(
                    _progress_event(
                        "replanning",
                        "finished",
                        total_steps=len(executed) + len(replanned.steps),
                        planned_steps=_planned_steps(replanned.steps),
                        elapsed_ms=_elapsed_ms(replanning_started),
                    )
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
            budget = current_budget()
            timeout = (
                budget.timeout_for("steps", settings.agent_step_timeout_s)
                if budget is not None
                else settings.agent_step_timeout_s
            )
            try:
                async with asyncio.timeout(timeout):
                    return await self.executor.execute(
                        candidate,
                        current,
                        step_tables=tables,
                        source_table=source,
                        history=memory,
                    )
            except TimeoutError as exc:
                remaining = budget.remaining() if budget is not None else 0.0
                raise BudgetExceeded("steps", remaining, "SQL/шаг превысил лимит") from exc

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
        for attempt in range(1, settings.agent_step_fix_attempts + 1):
            if not fixable(error):
                break
            logger.warning(
                "    шаг %d: исправление %d/%d после ошибки %r",
                step.step_id,
                attempt,
                settings.agent_step_fix_attempts,
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
        # Осмотр таблицы — разведка: его паспорт не ответ, ответ — сама таблица.
        answer_steps = [step for step in executed if step.tool != INSPECT_TOOL]
        chosen = next((step for step in executed if step.step_id == plan.result_step), None)
        if chosen is not None and chosen.tool == INSPECT_TOOL:
            # result_step явно указывает на осмотр: ответ — осмотренная таблица,
            # даже если в плане есть обычные шаги (docs/visualization.md).
            result_table = inputs.get(chosen.step_id) or fallback
        elif chosen is not None:
            result_table = tables[chosen.step_id]
        elif answer_steps:
            chosen = answer_steps[-1]
            result_table = tables[chosen.step_id]
        elif executed:
            # План кончился осмотром: показываем осмотренную таблицу, а паспорт
            # уходит в инсайт — иначе «сколько строк» отвечало бы числом колонок.
            chosen = executed[-1]
            result_table = inputs.get(chosen.step_id) or fallback
        else:
            result_table = fallback
        passports = [
            (step.purpose or step.tool or f"шаг {step.step_id}", tables[step.step_id])
            for step in executed
            if step.tool == INSPECT_TOOL
        ]
        supporting = [
            (step.purpose or step.tool or f"шаг {step.step_id}", tables[step.step_id])
            for step in executed
            if tool_kind(step) == "analysis" and tables[step.step_id] != result_table
        ]
        supporting += [(purpose, table) for purpose, table in passports if table != result_table]
        execution = Execution(
            result_table=result_table,
            supporting=supporting,
            final_viz=plan.final_viz,
            dashboard_requested=plan.dashboard_requested,
            tables=dict(tables),
            plan=plan,
            steps=executed,
            inputs=dict(inputs or {}),
            result_purpose=chosen.purpose if chosen else "",
            result_kind=tool_kind(chosen) if chosen else "transform",
            result_tool=chosen.tool if chosen else "",
        )
        try:
            values = self._distribution_values(question, chosen, inputs or {})
        except Exception:
            # Гистограмма — дополнение: без неё показываем статистики шага.
            logger.exception("<<< _finish выборка для гистограммы не собрана")
            values = None
        if values is None or chosen is None:
            return execution
        return Execution(
            result_table=values,
            supporting=[(chosen.purpose or chosen.tool, result_table), *supporting],
            final_viz="histogram",
            dashboard_requested=plan.dashboard_requested,
            tables=dict(tables),
            plan=plan,
            steps=executed,
            inputs=dict(inputs or {}),
            result_purpose=chosen.purpose,
            result_kind=tool_kind(chosen),
            result_tool=chosen.tool,
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
        sample_source: str | None = None,
        emit: Emit = _ignore,
    ) -> ResearchPlan:
        """Достроить план с учётом результата lookup-шага.

        ``sample_source`` — таблица, строки которой показываются планировщику
        дополнительно: паспорт ``inspect_temp_table`` описывает колонки по
        отдельности и теряет связь «регион ↔ категория ↔ сумма» в строке.
        """
        observed = self.executor.db.to_records(observation, limit=OBSERVATION_ROWS)
        context = memory.build_context(question)
        lines = [_describe_executed(executed, tables, observed)]
        if sample_source is not None:
            sample_rows = self.executor.db.to_records(sample_source, limit=OBSERVATION_ROWS)
            if sample_rows:
                lines.append(f"Строки {sample_source}: {sample_rows}")
        context["executed"] = "\n".join(lines)
        context["next_step_id"] = _next_step_id(tables)
        logger.info(">>> _replan_after_lookup после шага %d", executed[-1].step_id)
        plan = await self._plan(question, context, stage="replanning", emit=emit)
        return _renumber(plan, context["next_step_id"])

    async def _build_dashboard(
        self,
        session_id: str,
        question: str,
        execution: Execution,
        titles: dict[str, str],
        fallback: DashboardTile,
    ) -> dict:
        budget = current_budget()
        if budget is not None:
            budget.enter_stage("dashboard")
            if budget.stage_remaining("dashboard") <= 0:
                raise BudgetExceeded("dashboard", budget.remaining(), "нет бюджета на дашборд")
        plan = execution.plan
        specs: list[tuple[DashboardTilePlan, DashboardTile]] = []
        try:
            for planned in plan.dashboard_tiles:
                table = execution.tables.get(planned.step_id)
                if table is None:
                    logger.warning(
                        "    плитка %r: шаг %d не выполнен", planned.title, planned.step_id
                    )
                    continue
                try:
                    specs.append((planned, await self._tile_spec(planned, table, titles)))
                except Exception:
                    logger.exception("    плитка %r не собрана", planned.title)
            semaphore = asyncio.Semaphore(settings.agent_max_parallel_llm)
            results = await asyncio.gather(
                *(self._tile_insight(planned, tile, titles, semaphore) for planned, tile in specs),
                return_exceptions=True,
            )
        finally:
            if budget is not None:
                budget.leave_stage("dashboard")
        tiles: list[DashboardTile] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error("    плитка не собрана: %s", result)
                continue
            tiles.append(result)
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

    async def _tile_spec(
        self, planned: DashboardTilePlan, table: str, titles: dict[str, str]
    ) -> DashboardTile:
        """Собрать спеку плитки без LLM: данные, тип графика и layout."""
        requested = "kpi" if planned.is_kpi and planned.chart_type == "auto" else planned.chart_type
        records = await self.db.to_records_async(table, limit=SPEC_ROW_LIMIT)
        if requested == "kpi" and not is_single_number(records):
            logger.warning("    плитка %r: KPI не по одному числу", planned.title)
            requested = "auto"
        chart_type = select_chart(planned.title, table, requested, records)
        spec = build_vega_spec(chart_type, table, data=records, titles=titles, title=planned.title)
        built = spec["usermeta"]["chart_type"]
        return DashboardTile(
            title=planned.title,
            spec=spec,
            source_step_id=planned.step_id,
            result_table=table,
            is_kpi=built == "kpi",
        )

    async def _tile_insight(
        self,
        planned: DashboardTilePlan,
        tile: DashboardTile,
        titles: dict[str, str],
        semaphore: asyncio.Semaphore,
    ) -> DashboardTile:
        """LLM-вывод плитки под семафором; ошибка/таймаут не роняет дашборд."""
        if not planned.insight_needed or tile.result_table is None:
            return tile
        budget = current_budget()
        timeout = (
            budget.timeout_for("dashboard", settings.agent_dashboard_timeout_s)
            if budget is not None
            else settings.agent_dashboard_timeout_s
        )
        async with semaphore:
            try:
                async with asyncio.timeout(timeout):
                    insight = await self.insight_gen.generate_tile_insight(
                        planned.title, tile.result_table, tile.spec["usermeta"]["chart_type"]
                    )
                return tile.model_copy(update={"insight": insight})
            except (BudgetExceeded, TimeoutError) as exc:
                metrics = current_metrics()
                if metrics is not None:
                    metrics.budget_exceeded = f"dashboard: {exc}"
                logger.warning("    плитка %r: вывод не построен в бюджет: %s", planned.title, exc)
                return tile
            except Exception:
                logger.exception("    плитка %r: вывод не построен", planned.title)
                return tile

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
        """Ответ на «что передо мной?»: объём, период, описание и таблица колонок.

        Размер и период идут отдельной строкой из паспорта, описание — пунктами
        «что это / ключевые поля / на что обратить внимание». Заголовок —
        название от LLM и объём данных, без служебного имени таблицы. Таблица
        связана ключами с другими — обзор всех связанных таблиц
        (_schema_overview). Описание не построилось (LLM недоступна) — факты и
        таблица всё равно отдаются. Таблица колонок не становится last_chart:
        «покажи в другом виде» относится к прошлому графику исследования.
        """
        group = relations.component(relations.load(self.db), table)
        if len(group) > 1:
            async for event in self._schema_overview(memory, sorted(group)):
                yield event
            return
        stage = "insight"
        stage_started = time.perf_counter()
        try:
            yield _progress_event("insight", "started")
            profile = overview.passport(self.db, table)
            enrichment = self.profiles.load_enrichment(table)
            try:
                summary = await overview.describe_dataset(
                    self.db, table, getattr(self.planner, "llm", None)
                )
            except Exception:
                logger.exception("<<< run() описание таблицы %s не построено", table)
                summary = ""
            # Название LLM пишет вместе с описанием (#211): читаем после него.
            headline = overview.headline(profile, relations.table_title(self.db, table))
            points = overview.summary_lines(summary)
            details = [overview.facts_line(profile), *(points or [SUMMARY_UNAVAILABLE])]
            yield _progress_event("insight", "finished", elapsed_ms=_elapsed_ms(stage_started))
            yield {"type": "text", "data": {"headline": headline, "details": details}}

            stage = "chart"
            stage_started = time.perf_counter()
            yield _progress_event("chart", "started")
            spec = build_vega_spec(
                "table", data=overview.profile_rows(profile, enrichment), title=OVERVIEW_CAPTION
            )
            yield _progress_event("chart", "finished", elapsed_ms=_elapsed_ms(stage_started))
            yield {"type": "chart", "data": {"spec": spec, "caption": OVERVIEW_CAPTION}}
        except Exception as exc:
            logger.exception("<<< run() обзор таблицы: этап %s упал", stage)
            memory.finish_turn("failed")
            yield _progress_event(
                stage, "failed", error=str(exc), elapsed_ms=_elapsed_ms(stage_started)
            )
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
                first = overview.essence(summary) or SUMMARY_UNAVAILABLE
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
    if requested == "histogram":
        return "histogram"
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
    """Пустой план — подходящих инструментов нет; можно создать новый."""
    if not plan.steps:
        raise NoSuitableTool()


def _requested_tool(error: Exception | None) -> str | None:
    """Имя инструмента из ошибки «неизвестный инструмент 'x'» (для прогресса)."""
    if error is None:
        return None
    match = re.search(r"инструмент\s+'([^']+)'", str(error))
    return match.group(1) if match else None


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
