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

Просьба сменить вид прошлого графика («покажи в другом виде», «сделай
таблицей») обслуживается без планировщика и без повторного SQL: спека
перестраивается chart_morph из кешированной temp_result_vN.

При сбое шага после 2-х попыток оркестратор выдаёт
SSE-событие ``clarification_needed`` и сохраняет в SessionMemory
состояние ожидания уточнения (``pending_recovery``). При следующем
запросе от того же ``session_id`` с полем ``recovery_response``
оркестратор загружает сохранённый контекст, применяет ответ
пользователя и повторяет только неудачный шаг (или перепланирует).
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

import duckdb

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import StepPlanner
from app.core.agent.recovery import with_retry
from app.core.agent.step_executor import StepExecutor, tool_kind
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.viz.chart_morph import detect_morph, morph
from app.core.viz.chart_selector import select_chart
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec, column_aliases
from app.models.research_step import ResearchPlan, ResearchStep

SOURCE_TABLE = "raw"

# Количество попыток вызова инструмента перед уточнением у пользователя.
STEP_RETRY_ATTEMPTS = 2

# Готовые варианты ответа, которые фронт отображает кнопками.
CLARIFICATION_SUGGESTIONS = [
    "Переформулировать вопрос",
    "Уточнить колонки",
    "Пропустить этот шаг и продолжить",
]
# Шаг невыполним в принципе (нет нужной возможности) — колонки не помогут.
UNSUPPORTED_SUGGESTIONS = [
    "Переформулировать вопрос",
    "Пропустить этот шаг и продолжить",
]

# Сколько раз план можно достроить после lookup-шагов (защита от циклов).
MAX_REPLANS = 2
# Сколько строк результата lookup-шага показать планировщику.
OBSERVATION_ROWS = 20


@dataclass
class Execution:
    """Итог выполнения плана."""

    result_table: str
    # (purpose, temp-таблица) — результаты analysis-шагов для инсайта.
    supporting: list[tuple[str, str]] = field(default_factory=list)
    final_viz: str = "auto"
    dashboard_requested: bool = False


class ClarificationNeeded(Exception):
    """Исключение: шаг не прошёл после N попыток, нужен уточняющий ответ пользователя."""

    def __init__(self, step_id: int, error: str, suggestions: list[str]) -> None:
        self.step_id = step_id
        self.error = error
        self.suggestions = suggestions
        super().__init__(error)


class AgentOrchestrator:
    """Оркестратор одного хода диалога."""

    def __init__(
        self,
        planner: StepPlanner | None = None,
        executor: StepExecutor | None = None,
        insight_gen: InsightGenerator | None = None,
        cache: StateCache | None = None,
        db: DuckDBManager | None = None,
    ) -> None:
        # Один DuckDB на весь ход: temp_result_vN, созданные исполнителем,
        # должны читаться графиком, инсайтом и кешем из той же базы.
        shared = db or (cache.db if cache is not None else None)
        self.executor = executor or StepExecutor(shared)
        self.db = shared or getattr(self.executor, "db", None) or DuckDBManager()
        self.planner = planner or StepPlanner()
        self.insight_gen = insight_gen or InsightGenerator(db=self.db)
        self.cache = cache or StateCache(self.db)
        self.profiles = ProfileMemory(self.db)

    async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Выполнить исследование и вернуть поток событий для фронта."""
        logger.info(">>> run() session_id=%s question=%r", request.session_id, request.question)
        memory = SessionMemory(request.session_id, db=self.db)
        titles = column_aliases(
            {
                "glossary": self.profiles.load_glossary(SOURCE_TABLE),
                "aliases": {
                    row.column: row.aliases for row in self.profiles.load_enrichment(SOURCE_TABLE)
                },
            }
        )

        pending = memory.get_pending_recovery()
        if pending is not None and not request.recovery_response:
            # Новый вопрос вместо ответа на уточнение — прошлый сбой забываем,
            # иначе сессия навсегда застрянет в ожидании recovery_response.
            memory.clear_pending_recovery()
            pending = None

        if pending is None:
            # Смена вида прошлого графика — из кеша, без планировщика и SQL.
            morphed = self._morph(memory, request.question, titles)
            if morphed is not None:
                yield {"type": "chart", "data": morphed}
                yield {"type": "done", "data": {}}
                return

        # --- Возобновление после уточнения (pending_recovery) ---
        try:
            if pending is not None:
                question = pending.get("question") or request.question
                execution = await self._apply_recovery(memory, pending, request)
                memory.clear_pending_recovery()
            else:
                # Обычный путь: новый вопрос -> план -> выполнение шагов.
                question = request.question
                context = memory.build_context(request.question)
                logger.info(">>> run() планируем шаги (planner.plan)")
                plan = await self.planner.plan(request.question, context)
                logger.info(
                    "<<< run() план получен: шагов=%d final_viz=%s dashboard=%s",
                    len(plan.steps),
                    plan.final_viz,
                    plan.dashboard_requested,
                )
                for i, s in enumerate(plan.steps):
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
                datasource = memory._state.get("datasource") or "raw"
                execution = await self._execute_plan(memory, plan, datasource, request.question)
        except ClarificationNeeded as exc:
            yield {
                "type": "clarification_needed",
                "data": {
                    "step_id": exc.step_id,
                    "error": exc.error,
                    "suggestions": exc.suggestions,
                },
            }
            return
        result_table = execution.result_table

        # --- Финальный блок: инсайт -> чарт -> дашборд -> done ---
        logger.info(">>> run() генерация инсайта")
        insight = await self.insight_gen.generate(
            question, result_table, supporting=execution.supporting
        )
        logger.info("<<< run() инсайт: headline=%r details=%r", insight.headline, insight.details)
        yield {"type": "text", "data": {"headline": insight.headline, "details": insight.details}}

        chart_type = select_chart(question, result_table, execution.final_viz)
        logger.info(">>> run() построение графика chart_type=%s", chart_type)
        spec = build_vega_spec(chart_type, result_table, db=self.db, titles=titles)
        logger.info(
            "<<< run() график построен: mark=%s строк=%d",
            spec.get("mark", "?"),
            len(spec.get("data", {}).get("values", [])),
        )
        memory.record_chart(spec["usermeta"]["chart_type"], result_table, insight.headline)
        yield {"type": "chart", "data": {"spec": spec, "caption": insight.headline}}

        if execution.dashboard_requested:
            from app.core.dashboards.generator import DashboardGenerator
            from app.core.dashboards.models import DashboardTile

            tile = DashboardTile(
                title=spec.get("title") or insight.headline,
                spec=spec,
                result_table=result_table,
                insight=insight.headline,
            )
            dashboard = await DashboardGenerator(db=self.db).generate(
                request.session_id, "Дашборд", [tile]
            )
            yield {"type": "dashboard", "data": {"dashboard_id": dashboard.id}}

        memory.record_answer(insight.headline)
        yield {"type": "done", "data": {}}

    # --- Вспомогательные методы ---

    async def _execute_plan(
        self,
        memory: SessionMemory,
        plan: ResearchPlan,
        input_table: str,
        question: str,
        *,
        step_tables: dict[int, str] | None = None,
        source_table: str | None = None,
    ) -> Execution:
        """Последовательно выполнить шаги плана.

        transform-шаги продолжают конвейер ($prev); analysis-шаги не меняют
        $prev и попадают в инсайт как сопутствующие результаты; после
        lookup-шага план достраивается с учётом увиденного (до MAX_REPLANS).

        При сбое шага после STEP_RETRY_ATTEMPTS попыток сохраняет
        pending_recovery в SessionMemory и выбрасывает ClarificationNeeded,
        чтобы вызывающий код мог выдать clarification_needed и прервать генератор.
        """
        source = source_table or input_table
        tables = dict(step_tables or {})
        current = input_table
        steps = list(plan.steps)
        replans = 0
        index = 0
        while index < len(steps):
            step = steps[index]
            kind = tool_kind(step)
            logger.info(
                ">>> _execute_plan шаг %d [%s]: tool=%s input=%s args=%s columns=%s $prev=%s",
                step.step_id,
                kind,
                step.tool,
                step.input,
                step.arguments,
                step.columns,
                current,
            )
            try:
                table = await with_retry(
                    self.executor.execute,
                    step,
                    current,
                    step_tables=tables,
                    source_table=source,
                    attempts=STEP_RETRY_ATTEMPTS,
                )
            except Exception as exc:  # noqa: BLE001
                suggestions = (
                    UNSUPPORTED_SUGGESTIONS
                    if isinstance(exc, NotImplementedError)
                    else CLARIFICATION_SUGGESTIONS
                )
                # Сохраняем контекст неудачи для последующего уточнения.
                memory.set_pending_recovery(
                    {
                        "question": question,
                        "failed_step_id": step.step_id,
                        "plan_steps": [s.model_dump() for s in steps],
                        "input_table": current,
                        "source_table": source,
                        "step_tables": {str(key): value for key, value in tables.items()},
                        "error": str(exc),
                        "attempts_made": STEP_RETRY_ATTEMPTS,
                        "suggestions": suggestions,
                        "final_viz": plan.final_viz,
                        "dashboard_requested": plan.dashboard_requested,
                        "result_step": plan.result_step,
                    }
                )
                logger.error("<<< _execute_plan шаг %d ПАЛ: %r", step.step_id, exc)
                raise ClarificationNeeded(
                    step_id=step.step_id, error=str(exc), suggestions=suggestions
                )
            logger.info("<<< _execute_plan шаг %d УСПЕШЕН result_table=%s", step.step_id, table)
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
                plan = ResearchPlan(
                    steps=steps,
                    final_viz=replanned.final_viz,
                    dashboard_requested=plan.dashboard_requested or replanned.dashboard_requested,
                    result_step=replanned.result_step,
                )

        logger.info("<<< _execute_plan все шаги завершены $prev=%s", current)
        return self._finish(plan, steps, tables, fallback=current)

    def _finish(
        self,
        plan: ResearchPlan,
        steps: list[ResearchStep],
        tables: dict[int, str],
        fallback: str,
    ) -> Execution:
        """Выбрать таблицу ответа и сопутствующие результаты."""
        executed = [step for step in steps if step.step_id in tables]
        if plan.result_step is not None and plan.result_step in tables:
            result_table = tables[plan.result_step]
        elif executed:
            result_table = tables[executed[-1].step_id]
        else:
            result_table = fallback
        supporting = [
            (step.purpose or step.tool or f"шаг {step.step_id}", tables[step.step_id])
            for step in executed
            if tool_kind(step) == "analysis" and tables[step.step_id] != result_table
        ]
        return Execution(
            result_table=result_table,
            supporting=supporting,
            final_viz=plan.final_viz,
            dashboard_requested=plan.dashboard_requested,
        )

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
        plan = await self.planner.plan(question, context)
        return _renumber(plan, context["next_step_id"])

    async def _apply_recovery(
        self,
        memory: SessionMemory,
        pending: dict,
        request: ChatRequest,
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
            )
            return await self._execute_plan(
                memory,
                plan,
                input_table,
                question,
                step_tables=tables,
                source_table=source_table,
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

        plan = _renumber(
            await self.planner.plan(modified_question, context), context["next_step_id"]
        )
        _require_steps(plan)
        return await self._execute_plan(
            memory,
            plan,
            input_table,
            modified_question,
            step_tables=tables,
            source_table=source_table,
        )

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
        spec = morph(last["chart_type"], target, records, titles, last["result_table"])
        chart_type = spec["usermeta"]["chart_type"]
        memory.record_chart(chart_type, last["result_table"], last["caption"])
        memory.record_answer(f"График перестроен: {chart_type}")
        return {"spec": spec, "caption": last["caption"]}


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
